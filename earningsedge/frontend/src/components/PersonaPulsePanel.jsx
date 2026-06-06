import React, { useEffect, useRef, useState } from 'react';
import { getApiBase } from '../apiConfig';

/**
 * PersonaPulsePanel — five named-investor agents reacting to the live
 * transcript in real time. Fires `/api/personas/pulse` every ~25 s while
 * a transcript buffer exists, showing each persona's current sentiment,
 * confidence, and one-line reaction.
 *
 * Designed to feel ALIVE during live audio: as new lines come in, the
 * panel ticks. When sentiment changes sharply between polls, the card
 * flashes to draw the eye. This is what makes the named-investor story
 * visually demonstrable in a 30-second video clip.
 */
export default function PersonaPulsePanel({ ticker, transcript }) {
  const API_BASE = getApiBase();
  const [personas, setPersonas] = useState([]);
  const [updating, setUpdating] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [elapsed, setElapsed] = useState(null);
  const lastPollAtRef = useRef(0);
  const lastBufferLenRef = useRef(0);

  // Build the transcript buffer string for the pulse. Latest ~30 lines.
  const buffer = React.useMemo(() => {
    if (!Array.isArray(transcript) || transcript.length === 0) return '';
    const tail = transcript.slice(-30);
    return tail
      .map((line) => {
        const speaker = line.speaker_role || line.speaker_raw || '';
        const text = (line.text || '').trim();
        return speaker ? `${speaker}: ${text}` : text;
      })
      .filter(Boolean)
      .join('\n');
  }, [transcript]);

  async function runPulse() {
    if (!ticker || !buffer.trim()) return;
    setUpdating(true);
    try {
      const r = await fetch(`${API_BASE}/api/personas/pulse`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker, transcript: buffer }),
      });
      if (!r.ok) {
        return;
      }
      const data = await r.json();
      if (Array.isArray(data.personas)) {
        setPersonas(data.personas);
        setLastUpdated(Date.now());
        setElapsed(data.elapsed_ms);
      }
    } catch (_) {
      // soft-fail
    } finally {
      setUpdating(false);
    }
  }

  // Auto-poll when the transcript buffer grows. Cool-down 25 s so we
  // don't melt Gemini quota.
  useEffect(() => {
    if (!ticker || !buffer.trim()) return;
    const now = Date.now();
    if (now - lastPollAtRef.current < 25000) return;
    if (buffer.length === lastBufferLenRef.current) return;
    lastBufferLenRef.current = buffer.length;
    lastPollAtRef.current = now;
    runPulse();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buffer, ticker]);

  // Don't render until we have at least one transcript line OR the user
  // has already kicked off a manual pulse via the live-audio path.
  if (!ticker || (personas.length === 0 && !buffer.trim())) {
    return null;
  }

  return (
    <section className="persona-pulse">
      <header className="persona-pulse__head">
        <span className="persona-pulse__badge">LIVE</span>
        <h3>Persona pulse — five investors react</h3>
        {updating && (
          <span className="persona-pulse__status">
            <span className="persona-pulse__spinner" /> updating…
          </span>
        )}
        {!updating && lastUpdated && (
          <span className="persona-pulse__status persona-pulse__status--idle">
            {ago(lastUpdated)} ago · {elapsed != null ? `${(elapsed / 1000).toFixed(1)}s` : ''}
            {' '}
            <button className="persona-pulse__refresh" onClick={runPulse} title="Run pulse now">
              ↻
            </button>
          </span>
        )}
      </header>

      {personas.length === 0 && (
        <div className="persona-pulse__empty">
          Waiting for the first transcript line — the five lenses will fire as soon as the audio buffer has content.
        </div>
      )}

      {personas.length > 0 && (
        <ul className="persona-pulse__grid">
          {personas.map((p) => {
            const direction = sentimentBucket(p.sentiment);
            return (
              <li
                key={p.key}
                className={`persona-pulse__card persona-pulse__card--${direction}`}
              >
                <div className="persona-pulse__top">
                  <span className="persona-pulse__name">{p.display}</span>
                  <span className={`persona-pulse__chip persona-pulse__chip--${direction}`}>
                    {labelFromBucket(direction)}
                  </span>
                </div>
                <div className="persona-pulse__meta">
                  <span className="persona-pulse__lens">{p.lens}</span>
                  <span className="persona-pulse__conf">{p.confidence}</span>
                </div>
                <div className="persona-pulse__line">{p.one_line}</div>
                {p.flag && (
                  <div className={`persona-pulse__flag persona-pulse__flag--${p.flag}`}>
                    ⚑ {flagLabel(p.flag)}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function sentimentBucket(s) {
  const v = Number(s);
  if (!Number.isFinite(v)) return 'neutral';
  if (v >= 0.35) return 'bull';
  if (v <= -0.35) return 'bear';
  return 'neutral';
}

function labelFromBucket(b) {
  return b === 'bull' ? 'BULLISH' : b === 'bear' ? 'BEARISH' : 'NEUTRAL';
}

function flagLabel(f) {
  switch (f) {
    case 'pattern_match': return 'pattern match';
    case 'accounting_concern': return 'accounting concern';
    case 'guidance_signal': return 'guidance signal';
    case 'tone_shift': return 'tone shift';
    default: return f;
  }
}

function ago(ts) {
  const diff = (Date.now() - ts) / 1000;
  if (diff < 60) return `${Math.round(diff)}s`;
  if (diff < 3600) return `${Math.round(diff / 60)}m`;
  return `${Math.round(diff / 3600)}h`;
}
