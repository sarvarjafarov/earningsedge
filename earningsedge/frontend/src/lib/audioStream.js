/**
 * AudioStream — owns the persistent WebSocket to /ws/audio and the
 * AudioWorklet pipeline that converts a MediaStream into 16 kHz mono Int16
 * PCM frames. Supports two modes:
 *
 *   1. Briefing mode      — only the microphone is attached.
 *   2. Listening mode     — BOTH tab audio and microphone are attached.
 *
 * In listening mode, the active source (the one feeding the worklet)
 * automatically switches:
 *   - Tab audio is the default.
 *   - When tab audio drops below the silence threshold for
 *     TAB_SILENT_TO_MIC_MS (the user paused the call), the microphone
 *     becomes active so the user can ask the agent a question.
 *   - When tab audio rises above the activity threshold again (the user
 *     resumed the call), tab audio becomes active again and the mic stops
 *     forwarding.
 *
 * Both source streams stay open while listening — only the routing into
 * the worklet swaps.
 */

const TARGET_SAMPLE_RATE = 16000;
// Low-latency captioning: emit ~40ms PCM frames (Gemini Live best practice is small chunks).
const FRAME_SAMPLES = (TARGET_SAMPLE_RATE * 40) / 1000; // 640 samples = 40 ms

// Level polling interval for the header "LIVE · Tab Audio" indicator only.
// We no longer auto-switch between tab and mic based on silence — that
// feature was fragile (if the user's tab audio RMS didn't cross the
// active threshold we'd get stuck on mic and never hear the call). Tab
// audio is now sticky once attached in LIVE mode.
const LEVEL_POLL_MS = 300;

// Tab audio from getDisplayMedia is MUCH quieter than mic input —
// Gemini's VAD treats the faint signal as silence. We aggressively boost
// the tab source before the worklet so the VAD reliably fires. 10x may
// clip on very loud sources but earnings call audio is almost always
// under full-scale so clipping isn't a concern.
const TAB_GAIN = 10.0;

// Frontend keepalive: when no real audio frame has been forwarded for
// FRONT_KEEPALIVE_IDLE_MS, fire a short silent PCM frame directly via
// the websocket. Mirrors the backend keepalive so the browser-side WS
// also stays warm during gaps.
const FRONT_KEEPALIVE_POLL_MS = 250;
const FRONT_KEEPALIVE_IDLE_MS = 350;
const SILENT_FRAME_BYTES = (() => {
  const buf = new ArrayBuffer(640 * 2); // 40 ms of 16 kHz mono Int16 zeros
  return buf;
})();

const WORKLET_SOURCE = `
class PCMResampler extends AudioWorkletProcessor {
  constructor(opts) {
    super();
    const o = (opts && opts.processorOptions) || {};
    this.targetRate = o.targetRate || 16000;
    this.frameSamples = o.frameSamples || 1600;
    this.buffer = [];
  }
  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || !input[0]) return true;
    const channel = input[0];
    const ratio = sampleRate / this.targetRate;
    let pos = 0;
    while (pos + 1 < channel.length) {
      const i = Math.floor(pos);
      const f = pos - i;
      const sample = channel[i] * (1 - f) + channel[i + 1] * f;
      this.buffer.push(sample);
      pos += ratio;
    }
    while (this.buffer.length >= this.frameSamples) {
      const frame = this.buffer.splice(0, this.frameSamples);
      const int16 = new Int16Array(frame.length);
      for (let i = 0; i < frame.length; i++) {
        const s = Math.max(-1, Math.min(1, frame[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      this.port.postMessage(int16.buffer, [int16.buffer]);
    }
    return true;
  }
}
registerProcessor('pcm-resampler', PCMResampler);
`;

export default class AudioStream {
  constructor() {
    this.ws = null;
    this.ctx = null;
    this.workletNode = null;
    this.workletUrl = null;

    this.tabSource = null;
    this.tabAnalyser = null;
    this.tabGain = null; // gain stage for tab audio (VAD calibration)
    this.tabStream = null;

    this.micSource = null;
    this.micAnalyser = null;
    this.micStream = null;

    this.silentSink = null; // primes the audio graph pull chain
    this.activeSource = null; // 'tab' | 'mic' | null
    this.tabSilentSince = null;
    this.levelTimer = null;
    this.keepaliveTimer = null;
    this.lastSendAt = 0;
    this.bytesSent = 0;

    this.onSourceChange = null;
    this.onLevel = null; // callback(level0to1) for the tab level meter
    this.onError = null;
    this.onClose = null;
    this.connected = false;
  }

  async connect({ wsUrl, onError, onClose, onSourceChange, onLevel } = {}) {
    if (this.connected) return;
    this.onError = onError || (() => {});
    this.onClose = onClose || (() => {});
    this.onSourceChange = onSourceChange || (() => {});
    this.onLevel = onLevel || (() => {});

    this.ws = new WebSocket(wsUrl);
    this.ws.binaryType = 'arraybuffer';
    await new Promise((resolve, reject) => {
      const onOpen = () => {
        this.ws.removeEventListener('open', onOpen);
        this.ws.removeEventListener('error', onErr);
        resolve();
      };
      const onErr = () => {
        this.ws.removeEventListener('open', onOpen);
        this.ws.removeEventListener('error', onErr);
        reject(new Error('WebSocket connection failed'));
      };
      this.ws.addEventListener('open', onOpen);
      this.ws.addEventListener('error', onErr);
    });

    this.ws.addEventListener('close', () => {
      this.connected = false;
      this.onClose && this.onClose();
    });
    this.ws.addEventListener('error', (e) => {
      this.onError && this.onError(e);
    });

    const Ctx = window.AudioContext || window.webkitAudioContext;
    this.ctx = new Ctx();

    // Some Chrome setups intermittently fail to load blob URLs inside
    // AudioWorklet (shows as "Unable to load a worklet's module").
    // Try blob URL first (fast), then fall back to a data: URL.
    try {
      const blob = new Blob([WORKLET_SOURCE], { type: 'text/javascript' });
      this.workletUrl = URL.createObjectURL(blob);
      await this.ctx.audioWorklet.addModule(this.workletUrl);
    } catch (e) {
      try {
        if (this.workletUrl) {
          try { URL.revokeObjectURL(this.workletUrl); } catch (_) {}
          this.workletUrl = null;
        }
        const dataUrl = `data:application/javascript;charset=utf-8,${encodeURIComponent(WORKLET_SOURCE)}`;
        await this.ctx.audioWorklet.addModule(dataUrl);
      } catch (e2) {
        throw new Error(`Unable to load a worklet's module. ${String(e2?.message || e2 || e)}`);
      }
    }

    this.workletNode = new AudioWorkletNode(this.ctx, 'pcm-resampler', {
      processorOptions: {
        targetRate: TARGET_SAMPLE_RATE,
        frameSamples: FRAME_SAMPLES,
      },
    });
    this.workletNode.port.onmessage = (event) => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        try {
          this.ws.send(event.data);
          this.lastSendAt = Date.now();
          this.bytesSent += event.data.byteLength || 0;
        } catch (err) {
          this.onError && this.onError(err);
        }
      }
    };

    // PRIME THE PULL CHAIN: connect worklet -> silent gain -> destination
    // so Chrome keeps actively pulling audio through the graph. Without
    // this, getDisplayMedia tab audio can go dormant in the graph even
    // though the track is "live", and the worklet never receives samples.
    this.silentSink = this.ctx.createGain();
    this.silentSink.gain.value = 0;
    this.workletNode.connect(this.silentSink);
    this.silentSink.connect(this.ctx.destination);

    this.connected = true;
    this._startLevelMonitor();
    this._startKeepalive();
  }

  /** Briefing mode: attach the microphone as the only source. */
  async attachMicOnly(mediaStream) {
    if (!this.connected || !this.ctx || !this.workletNode) {
      throw new Error('AudioStream is not connected');
    }
    this._detachTab();
    this._detachMic();
    this.micStream = mediaStream;
    this.micSource = this.ctx.createMediaStreamSource(mediaStream);
    this.micAnalyser = this.ctx.createAnalyser();
    this.micAnalyser.fftSize = 1024;
    this.micSource.connect(this.micAnalyser);
    this._activateSource('mic');
    if (this.ctx.state === 'suspended') {
      try { await this.ctx.resume(); } catch (_) {}
    }
  }

  /** Listening mode: attach tab audio as the sticky source.
   *
   *  The previous version attached BOTH tab and mic and auto-switched
   *  between them on silence detection. In practice that was too fragile
   *  — if the user's tab audio RMS didn't cross the threshold fast enough
   *  we'd get stuck on mic and miss the entire call. This version is
   *  simpler: tab audio is the only source in LIVE mode, period. Mic is
   *  optional and only attached if the caller explicitly provides it
   *  (for future mid-call Q&A); it's kept around as an analyser-only
   *  monitor and never fed to the worklet. */
  async attachTabAndMic(tabStream, micStream) {
    if (!this.connected || !this.ctx || !this.workletNode) {
      throw new Error('AudioStream is not connected');
    }
    this._detachTab();
    this._detachMic();

    if (!tabStream || tabStream.getAudioTracks().length === 0) {
      throw new Error('Tab stream has no audio track. Check "Share tab audio" in the share dialog.');
    }

    this.tabStream = tabStream;
    this.tabSource = this.ctx.createMediaStreamSource(tabStream);
    this.tabAnalyser = this.ctx.createAnalyser();
    this.tabAnalyser.fftSize = 1024;
    // Gain stage on the tab signal — tab audio is quieter than mic
    // and VAD fires better with a stronger signal. Feed the analyser
    // from the POST-gain source so the level meter reflects what
    // Gemini actually sees.
    this.tabGain = this.ctx.createGain();
    this.tabGain.gain.value = TAB_GAIN;
    this.tabSource.connect(this.tabGain);
    this.tabGain.connect(this.tabAnalyser);

    if (micStream) {
      this.micStream = micStream;
      this.micSource = this.ctx.createMediaStreamSource(micStream);
      this.micAnalyser = this.ctx.createAnalyser();
      this.micAnalyser.fftSize = 1024;
      this.micSource.connect(this.micAnalyser);
      // Mic analyser only — never connected to worklet in LIVE mode.
    }

    this._activateSource('tab');
    if (this.ctx.state === 'suspended') {
      try { await this.ctx.resume(); } catch (_) {}
    }
  }

  /** Detach everything (used by Stop / Reset and by summarize). */
  detachSource() {
    this._detachTab();
    this._detachMic();
    this._activateSource(null);
  }

  _activateSource(which) {
    if (this.activeSource === which) return;
    // Disconnect previous source — for tab we route through tabGain.
    if (this.activeSource === 'tab' && this.tabGain) {
      try { this.tabGain.disconnect(this.workletNode); } catch (_) {}
    }
    if (this.activeSource === 'mic' && this.micSource) {
      try { this.micSource.disconnect(this.workletNode); } catch (_) {}
    }
    if (which === 'tab' && this.tabGain) {
      this.tabGain.connect(this.workletNode);
    } else if (which === 'mic' && this.micSource) {
      this.micSource.connect(this.workletNode);
    }
    this.activeSource = which;
    // Tell the backend which input source is currently feeding the stream
    // so it can label transcript entries CALL vs YOU.
    if (which === 'tab' || which === 'mic') {
      this.sendControl({ control: 'source', source: which });
    }
    this.onSourceChange && this.onSourceChange(which);
  }

  _detachTab() {
    if (this.tabGain) {
      try { this.tabGain.disconnect(); } catch (_) {}
      this.tabGain = null;
    }
    if (this.tabSource) {
      try { this.tabSource.disconnect(); } catch (_) {}
      this.tabSource = null;
    }
    this.tabAnalyser = null;
    if (this.tabStream) {
      this.tabStream.getTracks().forEach((t) => {
        try { t.stop(); } catch (_) {}
      });
      this.tabStream = null;
    }
    this.tabSilentSince = null;
  }

  _detachMic() {
    if (this.micSource) {
      try { this.micSource.disconnect(); } catch (_) {}
      this.micSource = null;
    }
    this.micAnalyser = null;
    if (this.micStream) {
      this.micStream.getTracks().forEach((t) => {
        try { t.stop(); } catch (_) {}
      });
      this.micStream = null;
    }
  }

  _startLevelMonitor() {
    if (this.levelTimer) return;
    this.levelTimer = setInterval(() => {
      if (!this.onLevel) return;
      if (this.activeSource === 'tab' && this.tabAnalyser) {
        const level = this._levelOf(this.tabAnalyser);
        this.onLevel({ source: 'tab', level, bytesSent: this.bytesSent });
      } else if (this.activeSource === 'mic' && this.micAnalyser) {
        const level = this._levelOf(this.micAnalyser);
        this.onLevel({ source: 'mic', level, bytesSent: this.bytesSent });
      }
    }, LEVEL_POLL_MS);
  }

  _levelOf(analyser) {
    if (!analyser) return 0;
    const buf = new Float32Array(analyser.fftSize);
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    return Math.sqrt(sum / buf.length);
  }

  _startKeepalive() {
    if (this.keepaliveTimer) return;
    this.keepaliveTimer = setInterval(() => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      const now = Date.now();
      if (now - this.lastSendAt < FRONT_KEEPALIVE_IDLE_MS) return;
      try {
        this.ws.send(SILENT_FRAME_BYTES);
        this.lastSendAt = now;
      } catch (_) {
        // ignore — onError will handle the underlying ws error
      }
    }, FRONT_KEEPALIVE_POLL_MS);
  }

  sendControl(obj) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    try {
      this.ws.send(JSON.stringify(obj));
    } catch (err) {
      this.onError && this.onError(err);
    }
  }

  async disconnect() {
    if (this.levelTimer) {
      clearInterval(this.levelTimer);
      this.levelTimer = null;
    }
    if (this.keepaliveTimer) {
      clearInterval(this.keepaliveTimer);
      this.keepaliveTimer = null;
    }
    this._detachTab();
    this._detachMic();
    if (this.silentSink) {
      try { this.silentSink.disconnect(); } catch (_) {}
      this.silentSink = null;
    }
    if (this.workletNode) {
      try { this.workletNode.disconnect(); } catch (_) {}
      this.workletNode = null;
    }
    if (this.ctx) {
      try { await this.ctx.close(); } catch (_) {}
      this.ctx = null;
    }
    if (this.workletUrl) {
      try { URL.revokeObjectURL(this.workletUrl); } catch (_) {}
      this.workletUrl = null;
    }
    if (this.ws) {
      try {
        if (this.ws.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ control: 'end' }));
        }
      } catch (_) {}
      try { this.ws.close(); } catch (_) {}
      this.ws = null;
    }
    this.connected = false;
  }
}

export async function getMicStream() {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('getUserMedia is not supported in this browser');
  }
  return navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
}

export async function getTabAudioStream() {
  if (!navigator.mediaDevices?.getDisplayMedia) {
    throw new Error('getDisplayMedia is not supported in this browser');
  }
  const stream = await navigator.mediaDevices.getDisplayMedia({
    video: true,
    audio: true,
  });
  stream.getVideoTracks().forEach((t) => t.stop());
  if (stream.getAudioTracks().length === 0) {
    stream.getTracks().forEach((t) => t.stop());
    throw new Error(
      'No tab audio captured. In the share dialog, pick a Chrome tab and check "Share tab audio".'
    );
  }
  return stream;
}
