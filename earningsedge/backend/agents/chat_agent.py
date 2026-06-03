"""ChatAgent — answers user questions about the live call via /api/ask.

Runs outside the Gemini Live audio path: a dedicated gemini-2.5-flash call with
session context (ticker, recent CALL transcript lines, latest metrics). Emits
`chat` WebSocket messages for the floating UI, plus `agent_audio` PCM frames
synthesized by the gemini-2.5-flash-preview-tts model so the answer is read
aloud through the existing browser AudioPlayer.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import Any, Awaitable, Callable

from google import genai
from google.genai import types

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = "gemini-2.5-flash"
TTS_MODEL = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Kore"
TTS_SAMPLE_RATE = 24000
# Slice the synthesized PCM into ~200ms chunks so the frontend's audioPlayer
# can start playback while later bytes are still arriving on the wire.
TTS_CHUNK_BYTES = TTS_SAMPLE_RATE * 2 // 5  # 200ms of 16-bit mono = 9600 bytes
_log = logging.getLogger("uvicorn.error")

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


class ChatAgent:
    """Side-channel Q&A over the current earnings session."""

    async def answer(
        self,
        question: str,
        *,
        state: dict[str, Any],
        history: list[dict[str, Any]],
        broadcast: BroadcastFn,
        voice_command: bool = False,
    ) -> str:
        q = (question or "").strip()
        if not q:
            return ""

        await broadcast({
            "type": "chat",
            "data": {"role": "user", "text": q},
        })

        recent_transcript_lines: list[str] = []
        latest_metrics = None
        for msg in history[-120:]:
            t = msg.get("type")
            d = msg.get("data") or {}
            if t == "transcript" and d.get("speaker") == "CALL":
                recent_transcript_lines.append(d.get("text", ""))
            elif t == "metrics":
                latest_metrics = d
        recent_transcript = " ".join(recent_transcript_lines[-20:])

        ctx_lines = []
        if state.get("ticker"):
            ctx_lines.append(
                f"Company: {state.get('company_name')} ({state.get('ticker')}), "
                f"{state.get('quarter', '')} {state.get('year', '')}, "
                f"sector {state.get('sector') or 'unknown'}"
            )
        if latest_metrics:
            ctx_lines.append(f"Latest metrics: {json.dumps(latest_metrics, default=str)}")
        if recent_transcript:
            ctx_lines.append(f"Recent call transcript:\n{recent_transcript[:2000]}")
        # Prior-call recap was removed (Octagon dependency). Keep the slot for
        # backwards compatibility in prompts, but it's always empty now.
        prior_txt = (state.get("prior_call_summary_text") or "").strip()
        if prior_txt:
            ctx_lines.append(
                "Summary of the company's last reported earnings call (for continuity):\n"
                f"{prior_txt[:3500]}"
            )
        context = "\n\n".join(ctx_lines) or "No session context yet."

        if voice_command and not state.get("ticker"):
            prompt = (
                "You are the EarningsEdge dashboard copilot (Jarvis-style): concise, "
                "professional, proactive. The user spoke a hands-free voice command. "
                "They may ask for company research, market context, or how to use the app. "
                "If they asked to analyze a specific ticker, say you'll need it loaded "
                "via the form or a clear company name next time. Answer in 2-5 sentences. "
                "Do NOT invent stock prices or private figures.\n\n"
                f"CONTEXT:\n{context}\n\n"
                f"VOICE REQUEST: {q}\n\n"
                "ANSWER:"
            )
        else:
            prompt = (
                "You are EarningsEdge, a sell-side equity analyst assistant. "
                "The user is listening to live audio about the loaded company — "
                "this may be an earnings call, news interview, conference talk, "
                "fireside chat, or any other source. Answer in 1-3 concise "
                "sentences, referencing specific numbers from the context when "
                "possible. Do NOT invent figures.\n\n"
                f"CONTEXT:\n{context}\n\n"
                f"USER QUESTION: {q}\n\n"
                "ANSWER:"
            )

        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            response = await client.aio.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.3),
            )
            answer = (getattr(response, "text", None) or "").strip()
        except Exception as exc:
            answer = f"(agent error: {exc})"

        await broadcast({
            "type": "chat",
            "data": {"role": "agent", "text": answer},
        })

        # Speak the answer back. Run in background so the text response isn't
        # blocked by TTS latency — the frontend's audioPlayer queues frames
        # back-to-back regardless of arrival order.
        if answer and not answer.startswith("(agent error:"):
            asyncio.create_task(self._speak(answer, broadcast))

        return answer

    async def _speak(self, text: str, broadcast: BroadcastFn) -> None:
        """Synthesize `text` to PCM via Gemini TTS and stream as agent_audio."""
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            resp = await client.aio.models.generate_content(
                model=TTS_MODEL,
                contents=[text],
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=TTS_VOICE,
                            ),
                        ),
                    ),
                ),
            )
        except Exception as exc:
            _log.warning("ChatAgent TTS failed: %s", exc)
            return

        pcm = b""
        for cand in resp.candidates or []:
            for part in (cand.content.parts if cand.content else []) or []:
                ib = getattr(part, "inline_data", None)
                if ib is None or not ib.data:
                    continue
                data = ib.data
                if isinstance(data, str):
                    try:
                        data = base64.b64decode(data)
                    except Exception:
                        continue
                pcm += data
        if not pcm:
            return

        # Stream in ~200ms slices so playback can start while later bytes
        # are still coming over the wire. Frame is 16-bit so cut on even bytes.
        for i in range(0, len(pcm), TTS_CHUNK_BYTES):
            chunk = pcm[i:i + TTS_CHUNK_BYTES]
            if len(chunk) % 2:
                chunk = chunk[:-1]
            if not chunk:
                continue
            await broadcast({
                "type": "agent_audio",
                "data": {
                    "pcm_b64": base64.b64encode(chunk).decode("ascii"),
                    "sample_rate": TTS_SAMPLE_RATE,
                },
            })
