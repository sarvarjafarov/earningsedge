"""TranscriptAgent — owns the Gemini Live audio session.

Single responsibility: PCM audio in, sentences out. Reads from
`audio_queue`, sends frames to Gemini Live via the realtime API, buffers
the input_transcription text deltas, and pushes complete sentences to
`transcript_queue`. Speaker labeling: BRIEFING mode → YOU. LIVE mode → CALL.

Notes on the model name and modality:
  - The spec calls for `gemini-2.0-flash-live-001` with `response_modalities=TEXT`.
    As of 2026 that combination is no longer accepted by the API: the only
    Live model returned by `client.models.list()` is `gemini-3.1-flash-live-preview`,
    and it requires `response_modalities=["AUDIO"]`. We get text-only output
    by enabling `input_audio_transcription` and consuming ONLY that field.
    The model's audio output is silently dropped.

Why we tune VAD with very long silence tolerance:
  - Default Live VAD ends the user's turn after ~700 ms of silence. With
    response_modalities=AUDIO + a "stay silent" system prompt, the model
    produces an empty turn and the *server closes the websocket cleanly
    with code 1000* (`APIError('1000 None. ')`). The transcript visibly
    stops after 1–3 sentences and reconnects loop forever.
  - We CAN'T just disable automatic VAD: that makes the server BUFFER audio
    until `activity_end` is sent — `input_audio_transcription` deltas never
    stream in real time and the panel stays empty.
  - Instead we keep VAD enabled but set a very long `silence_duration_ms`
    so natural pauses in an earnings call (Q&A handoffs, breath, etc.) are
    never counted as end-of-speech. End-of-speech only fires when the user
    actually stops sharing audio for many seconds — by which time we don't
    care that the session closes. `input_audio_transcription` streams
    continuously the whole time.

Keepalive: a 100ms silent PCM frame is sent every ~250ms when the audio
queue is idle. This serves two purposes — it keeps the Live websocket from
hitting its 1011 keepalive ping timeout, AND it provides continuous silence
into the model's VAD pipeline so end-of-speech detection actually fires.

Session resumption + context-window compression let a single Live session
survive the full ~60+ minute call without hitting the 10-minute audio limit.

Auto-reconnect: any websocket exception in the receive loop triggers a
short-delay reopen. We resume the session via the saved handle so we don't
lose conversation context. The last 3 emitted sentences are also re-injected
as `[RECONNECT CONTEXT] ...` so the model picks up where it left off.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import time
import logging
from typing import Any, Awaitable, Callable

import websockets  # noqa: F401  (kept for ConnectionClosedError/OK exception types)
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

# NOTE: a previous revision monkey-patched `websockets.asyncio.client.connect`
# to enlarge ping timeouts. That patch landed BEFORE the genai SDK imported,
# but on the current SDK rev it interfered with Live's own connection
# parameters and correlated with rapid 1000-closes. We now let the SDK
# manage its own websocket lifetimes.
from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from audio import SILENT_FRAME

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
LIVE_MODEL = "gemini-3.1-flash-live-preview"
_log = logging.getLogger("uvicorn.error")

SYSTEM_PROMPT = (
    "You are a transcription agent. Stay completely silent. Do not respond. "
    "Do not generate any audio output. Your only job is for the system to "
    "read the input_transcription field from each server response."
)

KEEPALIVE_POLL_S = 0.20
KEEPALIVE_IDLE_S = 0.30
SENTENCE_END = (".", "?", "!")
# Flush even incomplete sentences after 1.2s of buffer idle. The previous
# 3.0s was too slow and made the dashboard feel laggy.
FLUSH_TIMEOUT_S = 1.2
MAX_BUFFER_CHARS = 280
# Even if deltas stream continuously without punctuation, force a partial flush
# so the dashboard updates during long monologues.
MAX_BUFFER_AGE_S = 1.6
STALL_POLL_S = 1.2
STALL_DELTA_S = 10.0
RECONNECT_DELAY_S = 2.0
RECONNECT_CONTEXT_KEEP = 3
RECONNECT_MAX_ATTEMPTS = 40
# Low-latency mode: the frontend now emits ~40ms frames. Only tiny coalescing
# is allowed (at most ~80ms) to avoid overwhelming the socket while keeping
# subtitle-like responsiveness.
COALESCE_MAX_FRAMES = 2
COALESCE_MAX_WAIT_S = 0.03

# Live partial transcript: broadcast the in-progress buffer to the frontend
# every PARTIAL_THROTTLE_S so the UI can render captions word-by-word like
# YouTube live captions, instead of dumping a paragraph at sentence end.
# Existing _flush_locked() still emits the final `transcript` event for
# downstream agents — this is purely additive UI plumbing.
PARTIAL_THROTTLE_S = 0.18

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


class TranscriptAgent:
    def __init__(
        self,
        audio_queue: asyncio.Queue,
        transcript_queue: asyncio.Queue,
        broadcast: BroadcastFn,
        get_mode: Callable[[], str],
    ) -> None:
        self.audio_queue = audio_queue
        self.transcript_queue = transcript_queue
        self.broadcast = broadcast
        self.get_mode = get_mode  # returns "BRIEFING" or "LIVE"
        self._client: genai.Client | None = None
        self._stack: contextlib.AsyncExitStack | None = None
        self._live: Any | None = None
        self._buffer = ""
        self._buffer_lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None
        self._last_send_at: float = 0.0
        self._last_delta_at: float = 0.0
        self._last_emit_at: float = 0.0
        self._send_lock = asyncio.Lock()
        self._closed = asyncio.Event()
        self._last_sentences: list[str] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._last_recv_at: float = 0.0
        self._send_failures: int = 0
        self._last_send_error_at: float = 0.0
        self._reconnect_lock = asyncio.Lock()
        self._last_stall_warn_at: float = 0.0
        self._last_reconnect_err: str | None = None
        self._rx_frames: int = 0
        self._rx_deltas: int = 0
        self._last_diag_at: float = 0.0
        self._session_handle: str | None = None
        # Gemini API does not support transparent session resumption (Vertex-only).
        # We keep a small replay buffer and, more importantly, we coalesce frames
        # to reduce websocket message churn (a common trigger for 1000 closes).
        self._audio_replay: list[bytes] = []
        self._audio_replay_max_frames: int = 25  # ~2.5s at 100ms/frame
        # Gemini Live often sends cumulative transcription strings (not deltas).
        # Track the last seen text so we only append the new suffix.
        self._last_cumulative_tx: str = ""
        # Last fully-emitted line to prevent duplicates even across reconnects.
        self._last_emitted_line: str = ""
        # Throttled partial-transcript state — drives the live caption stream
        # to the frontend. Reset on every final flush so each new sentence
        # starts a fresh partial line in the UI.
        self._last_partial_at: float = 0.0
        self._last_partial_text: str = ""
        # Track recent 1000-close timestamps so we can back off when the server
        # is closing us in a tight loop (rate-limit log + reopen interval).
        self._close_history: list[float] = []
        self._last_close_log_at: float = 0.0

    async def run(self) -> None:
        try:
            self._client = genai.Client(api_key=GEMINI_API_KEY)
            await self._open_live_socket()
        except Exception as exc:
            # If Live cannot open (auth / network / SDK mismatch), surface it to the UI.
            await self.broadcast({
                "type": "status",
                "data": {"state": "error", "message": f"transcript live connect failed: {exc}"},
            })
            self._closed.set()
            return
        # Bare-bones tasks. Keepalive + stall-watchdog were correlated with
        # rapid 1000-closes and have been disabled — the browser's continuous
        # tab audio is itself a keepalive, and the recv loop reconnects on
        # any genuine drop.
        self._tasks = [
            asyncio.create_task(self._send_loop()),
            asyncio.create_task(self._recv_loop_with_reconnect()),
        ]
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def force_flush(self) -> None:
        """Flush pending transcription immediately (user tapped 'Done speaking')."""
        await self._flush_buffer()

    async def stop(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        # Cancel any pending flush timer before final flush.
        try:
            await self._flush_buffer()
        except Exception:
            pass
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:
                pass
            self._stack = None
        self._live = None

    def _build_config(self) -> types.LiveConnectConfig:
        # Minimal, robust config:
        # - Keep automatic VAD on (required for streaming input_transcription).
        # - Make it tolerant of silence so natural pauses don't end the turn.
        # - DO NOT pass a "stay silent" system_instruction. Per this file's own
        #   docstring, that combination (AUDIO modality + silence prompt)
        #   makes the model produce an empty turn and the server closes 1000
        #   in a tight loop — exactly what we observed on the
        #   billing-enabled GCP project. We let the model produce audio
        #   output and ignore it; sessions stay open through long calls.
        # - Drop session_resumption (Vertex-only; on AI Studio it's a no-op
        #   that sometimes confuses newer server revs).
        # - Drop context_window_compression — extra moving part we don't need.
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                    silence_duration_ms=30000,
                    prefix_padding_ms=200,
                ),
            ),
        )

    async def _open_live_socket(self) -> None:
        # Guard: reconnect paths can race (recv exception + watchdog).
        async with self._reconnect_lock:
            await self._open_live_socket_unlocked()

    async def _open_live_socket_unlocked(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:
                pass
        self._stack = contextlib.AsyncExitStack()
        config = self._build_config()
        try:
            self._live = await self._stack.enter_async_context(
                self._client.aio.live.connect(model=LIVE_MODEL, config=config)
            )
            self._last_reconnect_err = None
        except Exception as exc:
            self._last_reconnect_err = repr(exc)
            raise
        # Each Live session restarts transcription from scratch — the
        # cumulative-string tracker MUST be reset, otherwise the new
        # session's first line is misclassified as a continuation of the
        # previous session's text and the delta diff suppresses it.
        self._last_cumulative_tx = ""
        # IMPORTANT: do not re-inject text context or replay audio on reconnect.
        # On Gemini API this tends to *increase* duplication (the model echoes
        # old context and/or re-transcribes replayed audio). We prefer slight
        # gaps over repeated lines.

    def _delta_from_cumulative(self, new_text: str) -> str | None:
        """Gemini Live sometimes sends the full cumulative transcript so far.
        Convert it into a delta suffix suitable for streaming UI updates."""
        t = (new_text or "").strip("\n")
        if not t:
            return None
        prev = self._last_cumulative_tx
        if t == prev:
            return None
        if prev and t.startswith(prev):
            delta = t[len(prev):]
        elif prev and prev.startswith(t):
            # Server sent a shorter prefix (rare). Ignore rather than duplicate.
            return None
        else:
            # Heuristic: if the new payload is "small", treat it as a delta,
            # not a reset. If it's large, treat as fresh cumulative text.
            delta = t if len(t) > 140 else t
        self._last_cumulative_tx = t
        return delta

    async def _stall_watchdog_loop(self) -> None:
        """If we are sending audio but stop receiving transcription deltas,
        force a reconnect. This handles the 'one line then nothing' failure mode
        without requiring an exception from the SDK."""
        try:
            while not self._closed.is_set():
                try:
                    await asyncio.wait_for(self._closed.wait(), timeout=STALL_POLL_S)
                    return
                except asyncio.TimeoutError:
                    pass
                now = time.monotonic()
                # Only enforce during LIVE (call) mode.
                if self.get_mode() != "LIVE":
                    continue
                # If we've never seen any deltas yet, give the model more time.
                if self._last_delta_at <= 0:
                    continue
                # If audio has stopped, don't churn reconnect.
                if (now - self._last_send_at) > 3.5:
                    continue
                stalled = (now - self._last_delta_at) >= STALL_DELTA_S
                if not stalled:
                    continue
                # Warn (rate-limited) and reconnect.
                if (now - self._last_stall_warn_at) > 8.0:
                    self._last_stall_warn_at = now
                    await self.broadcast({
                        "type": "status",
                        "data": {"state": "reconnecting", "message": "transcript stalled — reconnecting…"},
                    })
                try:
                    await self._open_live_socket()
                except Exception:
                    # recv loop will also attempt reconnects; suppress here.
                    continue
        except asyncio.CancelledError:
            return

    async def _send_pcm(self, chunk: bytes) -> None:
        # Always keep the most recent frames so we can replay them after a
        # reconnect. This is cheap (a few KB) and prevents word dropouts.
        if len(self._audio_replay) >= self._audio_replay_max_frames:
            self._audio_replay.pop(0)
        self._audio_replay.append(chunk)
        if self._live is None or self._closed.is_set():
            return
        async with self._send_lock:
            try:
                await self._live.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                )
                self._last_send_at = time.monotonic()
                self._send_failures = 0
            except Exception:
                # recv loop owns reconnect, but if sends are failing persistently we
                # should tell the UI (otherwise the transcript just "never starts").
                self._send_failures += 1
                now = time.monotonic()
                if self._send_failures >= 20 and (now - self._last_send_error_at) > 6.0:
                    self._last_send_error_at = now
                    await self.broadcast({
                        "type": "status",
                        "data": {"state": "warning", "message": "audio is streaming but transcript send is failing — will retry"},
                    })

    async def _send_loop(self) -> None:
        # No coalescing: forward each frame to Live as it arrives. Coalescing
        # was an optimization that, on this account, correlated with the
        # rapid 1000-close pattern. Direct forwarding mirrors what worked in
        # standalone diagnostics (29 s session, full transcription).
        while not self._closed.is_set():
            try:
                chunk = await self.audio_queue.get()
            except asyncio.CancelledError:
                return
            if chunk is None:
                return
            await self._send_pcm(chunk)

    async def _keepalive_loop(self) -> None:
        try:
            while not self._closed.is_set():
                try:
                    await asyncio.wait_for(self._closed.wait(), timeout=KEEPALIVE_POLL_S)
                    return
                except asyncio.TimeoutError:
                    pass
                idle = time.monotonic() - self._last_send_at
                # Less aggressive keepalive to avoid unnecessary message churn.
                if idle >= 1.2:
                    await self._send_pcm(SILENT_FRAME)
        except asyncio.CancelledError:
            return

    async def _recv_loop_with_reconnect(self) -> None:
        """Consume Live responses until the session ends.

        AI Studio's Gemini Live API closes sessions intermittently with
        `APIError('1000 None. ')` (clean close, no reason). This is a known
        backend behaviour — each session may only last a few seconds before
        the server closes. We therefore reconnect FAST on a 1000-close
        (~150 ms) instead of using the slow backoff path that's reserved
        for genuine network errors. Without this, only 1–4 transcript lines
        ever reach the UI before the next 2 s backoff swallows the next
        chunk of audio.
        """
        attempts = 0
        while not self._closed.is_set():
            try:
                if self._live is None:
                    await self._open_live_socket()
                saw_any = False
                async for response in self._live.receive():
                    if not saw_any:
                        # First response on this socket → reset failure counter.
                        attempts = 0
                        saw_any = True
                    if self._closed.is_set():
                        return
                    await self._process_response(response)
                if self._closed.is_set():
                    return

                # Clean iterator end (no exception). Reopen quickly.
                try:
                    await asyncio.sleep(0.15 if saw_any else 0.6)
                    if self._closed.is_set():
                        return
                    await self._open_live_socket()
                except Exception as exc:
                    self._last_reconnect_err = repr(exc)
                attempts = 0
            except asyncio.CancelledError:
                return
            except (ConnectionClosedOK, genai_errors.APIError) as exc:
                # `APIError('1000 None. ')` and ConnectionClosedOK both mean
                # the server cleanly ended the session. Reconnect immediately
                # without burning attempts so audio isn't lost between sockets.
                if self._closed.is_set():
                    return
                self._last_reconnect_err = repr(exc)
                code = getattr(exc, "code", None)
                # Anything that isn't a plain 1000 (e.g. 1011 internal error,
                # 1007 invalid argument) is a real failure — fall through to
                # the slow backoff path below.
                if code not in (None, 1000):
                    raise
                # Sessions on this account close very quickly (~1s). Flush
                # whatever partial text the previous session managed to emit
                # so the dashboard sees something instead of fragments
                # stranded in _buffer until the next punctuation hit.
                try:
                    await self._flush_buffer()
                except Exception:
                    pass
                # Track recent close cadence so we don't flood the log or
                # melt the API quota when the server is closing us repeatedly.
                self._close_history.append(time.monotonic())
                # Keep only the last 12 closes for cadence calc.
                if len(self._close_history) > 12:
                    self._close_history.pop(0)
                # If 5+ closes in the last 6s, back off to 1s reopen and
                # log only once per burst. Otherwise fast-reopen as before.
                recent = [t for t in self._close_history if (time.monotonic() - t) < 6.0]
                if len(recent) >= 5:
                    if (time.monotonic() - self._last_close_log_at) > 5.0:
                        self._last_close_log_at = time.monotonic()
                        _log.warning(
                            "Live stream closing rapidly (%d in last 6s); slowing reopen to 1s. last=%s",
                            len(recent), repr(exc),
                        )
                    delay = 1.0
                else:
                    if (time.monotonic() - self._last_close_log_at) > 2.0:
                        self._last_close_log_at = time.monotonic()
                        _log.info("Live stream closed cleanly (%s); fast reopen.", repr(exc))
                    delay = 0.15
                try:
                    await asyncio.sleep(delay)
                    if self._closed.is_set():
                        return
                    await self._open_live_socket()
                except Exception as ie:
                    self._last_reconnect_err = repr(ie)
                continue
            except ConnectionClosedError as exc:
                self._last_reconnect_err = repr(exc)
                raise
            except Exception as exc:
                if self._closed.is_set():
                    return
                attempts += 1
                self._last_reconnect_err = repr(exc)
                _log.warning(
                    "Transcript recv loop error (attempt %s/%s): %s",
                    attempts,
                    RECONNECT_MAX_ATTEMPTS,
                    repr(exc),
                )
                await self.broadcast({
                    "type": "status",
                    "data": {"state": "reconnecting", "message": "transcript reconnecting…"},
                })
                if attempts > RECONNECT_MAX_ATTEMPTS:
                    await self.broadcast({
                        "type": "status",
                        "data": {"state": "error", "message": "transcript reconnect exhausted"},
                    })
                    return
                try:
                    delay = min(RECONNECT_DELAY_S + (attempts - 1) * 0.4, 6.0)
                    await asyncio.sleep(delay)
                    if self._closed.is_set():
                        return
                    await self._open_live_socket_unlocked()
                except Exception:
                    if attempts in (3, 8, 16, 24, 32, 40):
                        detail = (self._last_reconnect_err or "").strip()
                        if detail:
                            await self.broadcast({
                                "type": "status",
                                "data": {"state": "reconnecting", "message": f"transcript reconnect failing: {detail}"},
                            })
                    pass

    async def _process_response(self, response: Any) -> None:
        self._last_recv_at = time.monotonic()
        self._rx_frames += 1
        # Capture session-resumption handle so we can resume after a drop.
        sru = getattr(response, "session_resumption_update", None)
        if sru is not None:
            new_handle = getattr(sru, "new_handle", None)
            if new_handle:
                self._session_handle = new_handle
        # `go_away` means the server is about to terminate the session
        # (typically just before the model's max session length). Reopen
        # immediately and resume from the saved handle.
        go_away = getattr(response, "go_away", None)
        if go_away is not None:
            _log.info("Live API go_away received; reconnecting with session handle.")
            try:
                await self._open_live_socket()
            except Exception:
                pass
            return
        sc = getattr(response, "server_content", None)
        if sc is None:
            return
        input_tx = getattr(sc, "input_transcription", None)
        if input_tx is not None:
            text = getattr(input_tx, "text", None)
            if text:
                # Some SDK builds send deltas, others send cumulative strings.
                # Convert cumulative -> delta when it looks like a prefix-growth.
                delta = self._delta_from_cumulative(text) if self._last_cumulative_tx else text
                if delta and delta.strip():
                    self._rx_deltas += 1
                    await self._handle_text_delta(delta)
        if getattr(sc, "turn_complete", None):
            self._last_cumulative_tx = ""
            await self._flush_buffer()
        # Periodic diagnostics (rate-limited) so we can see if we are receiving
        # Live frames but no transcription.
        now = time.monotonic()
        if (now - self._last_diag_at) >= 6.0:
            self._last_diag_at = now
            await self.broadcast({
                "type": "status",
                "data": {
                    "state": "running",
                    "message": f"live rx frames={self._rx_frames} deltas={self._rx_deltas}",
                },
            })

    async def _handle_text_delta(self, text: str) -> None:
        if self._closed.is_set():
            return
        async with self._buffer_lock:
            now = time.monotonic()
            self._last_delta_at = now
            self._buffer += text
            # Live-caption stream: broadcast in-progress buffer (throttled) so
            # the dashboard renders text as it arrives rather than at sentence
            # end. Existing flush logic below still fires the final
            # `transcript` event that downstream agents consume.
            await self._maybe_broadcast_partial_locked(now)
            self._cancel_flush_timer_locked()
            stripped = self._buffer.rstrip()
            if any(stripped.endswith(c) for c in SENTENCE_END):
                await self._flush_locked()
            else:
                # If the model streams continuous deltas (no punctuation / no turn_complete),
                # force periodic partial flushes so the UI behaves like subtitles.
                # IMPORTANT: we must NOT require punctuation OR long buffers,
                # otherwise continuous speech can sit forever without emitting.
                if (now - self._last_emit_at) >= MAX_BUFFER_AGE_S and len(stripped) >= 60:
                    await self._flush_locked()
                    return
                # Secondary guard: if the buffer grows huge, flush even if we just emitted.
                if len(stripped) >= MAX_BUFFER_CHARS:
                    await self._flush_locked()
                    return
                self._flush_task = asyncio.create_task(self._delayed_flush())

    async def _maybe_broadcast_partial_locked(self, now: float) -> None:
        """Emit a `transcript_partial` event with the current buffer if at
        least PARTIAL_THROTTLE_S has elapsed since the last partial. Caller
        must already hold _buffer_lock.

        Skips the broadcast when the buffer hasn't changed since the last
        partial (avoids spamming identical messages on rapid empty deltas).
        """
        if (now - self._last_partial_at) < PARTIAL_THROTTLE_S:
            return
        text = self._buffer.strip()
        if not text or text == self._last_partial_text:
            return
        self._last_partial_at = now
        self._last_partial_text = text
        mode = self.get_mode()
        speaker = "YOU" if mode == "BRIEFING" else "CALL"
        try:
            await self.broadcast({
                "type": "transcript_partial",
                "data": {"speaker": speaker, "text": text},
            })
        except Exception:
            # Never let UI plumbing break the agent loop.
            pass

    async def _delayed_flush(self) -> None:
        try:
            await asyncio.sleep(FLUSH_TIMEOUT_S)
        except asyncio.CancelledError:
            return
        if self._closed.is_set():
            return
        async with self._buffer_lock:
            await self._flush_locked()

    def _cancel_flush_timer_locked(self) -> None:
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        self._flush_task = None

    async def _flush_buffer(self) -> None:
        async with self._buffer_lock:
            self._cancel_flush_timer_locked()
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        if self._closed.is_set():
            # Session ended; don't emit new transcript lines after stop.
            self._buffer = ""
            self._cancel_flush_timer_locked()
            self._last_partial_text = ""
            return
        text = self._buffer.strip()
        self._buffer = ""
        self._cancel_flush_timer_locked()
        if not text:
            return
        # Hard de-dupe: Gemini can resend the same line (or cumulative buffers
        # that collapse to the same line) during reconnect churn.
        if text == self._last_emitted_line:
            # Even on dedupe, clear partial state so the UI's in-progress line
            # disappears (the duplicate text is already shown as a final line).
            self._last_partial_text = ""
            return
        self._last_emitted_line = text
        self._last_emit_at = time.monotonic()
        # Reset partial state so the next sentence starts a fresh in-progress
        # caption line in the UI instead of looking like a continuation.
        self._last_partial_text = ""
        mode = self.get_mode()
        speaker = "YOU" if mode == "BRIEFING" else "CALL"
        self._last_sentences.append(text)
        if len(self._last_sentences) > 10:
            self._last_sentences.pop(0)
        await self.broadcast({
            "type": "transcript",
            "data": {
                "speaker": speaker,
                "text": text,
                "timestamp_s": 0,
            },
        })
        try:
            self.transcript_queue.put_nowait({"text": text, "speaker": speaker, "mode": mode})
        except asyncio.QueueFull:
            pass
