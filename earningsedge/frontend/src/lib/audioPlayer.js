/**
 * AudioPlayer — buffered playback of 24 kHz mono Int16 PCM chunks streamed
 * from the Gemini Live agent. Schedules each chunk back-to-back so the
 * agent's spoken response sounds continuous.
 *
 * Usage:
 *   const player = new AudioPlayer();
 *   player.enqueueBase64(b64String, 24000);  // each agent_audio message
 *   player.dispose();                        // when the session ends
 */

export default class AudioPlayer {
  constructor() {
    this.ctx = null;
    this.nextStartTime = 0;
    this.sampleRate = 24000;
  }

  _ensureContext(sampleRate) {
    if (!this.ctx || this.ctx.state === 'closed') {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      this.ctx = new Ctx({ sampleRate });
      this.nextStartTime = 0;
      this.sampleRate = sampleRate;
    }
    if (this.ctx.state === 'suspended') {
      this.ctx.resume().catch(() => {});
    }
  }

  enqueueBase64(b64, sampleRate = 24000) {
    if (!b64) return;
    this._ensureContext(sampleRate);

    // base64 -> Uint8Array
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    // Uint8Array bytes -> Int16Array (little-endian)
    const int16 = new Int16Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 2));
    if (int16.length === 0) return;

    // Int16 -> Float32 [-1, 1]
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / 0x8000;
    }

    const buffer = this.ctx.createBuffer(1, float32.length, this.ctx.sampleRate);
    buffer.copyToChannel(float32, 0);

    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.ctx.destination);

    const now = this.ctx.currentTime;
    const startAt = Math.max(now, this.nextStartTime);
    source.start(startAt);
    this.nextStartTime = startAt + buffer.duration;
  }

  flush() {
    this.nextStartTime = 0;
  }

  async dispose() {
    if (this.ctx) {
      try { await this.ctx.close(); } catch (_) {}
      this.ctx = null;
    }
    this.nextStartTime = 0;
  }
}
