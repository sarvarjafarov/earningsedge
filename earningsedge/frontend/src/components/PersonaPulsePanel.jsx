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
  // CRITICAL — must be declared with the other hooks, BEFORE any
  // conditional early return below. Otherwise hook order differs
  // between renders (empty-transcript render skips this hook; populated
  // render calls it), which triggers React error #310 mid-session and
  // crashes the whole React tree. That was the actual "page goes blank
  // on Listen Live" bug the user was reporting.
  const [expanded, setExpanded] = useState(null);
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

  // Auto-poll when the transcript buffer grows. Cool-down 60 s — was 25 s
  // but with 5 personas × Gemini calls per pulse this was contributing to
  // memory pressure on the Heroku Basic dyno during live audio.
  useEffect(() => {
    if (!ticker || !buffer.trim()) return;
    const now = Date.now();
    if (now - lastPollAtRef.current < 60000) return;
    // Require a meaningful change in buffer size before re-polling.
    if (buffer.length - lastBufferLenRef.current < 200) return;
    lastBufferLenRef.current = buffer.length;
    lastPollAtRef.current = now;
    runPulse();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buffer, ticker]);

  // Don't render until we have at least one transcript line OR the user
  // has already kicked off a manual pulse via the live-audio path.
  // (Early return MUST stay below every hook above — see note on
  // setExpanded.)
  if (!ticker || (personas.length === 0 && !buffer.trim())) {
    return null;
  }

  return (
    <section className="persona-pulse">
      <header className="persona-pulse__head">
        <span className="persona-pulse__badge">LIVE</span>
        <h3>Five investors react</h3>
        {updating && <span className="persona-pulse__status"><span className="persona-pulse__spinner" /></span>}
        {!updating && lastUpdated && (
          <span className="persona-pulse__status persona-pulse__status--idle">
            {ago(lastUpdated)} ago · {elapsed != null ? `${(elapsed / 1000).toFixed(1)}s` : ''}
            <button className="persona-pulse__refresh" onClick={runPulse} title="Run pulse now">↻</button>
          </span>
        )}
      </header>

      {personas.length === 0 && (
        <div className="persona-pulse__empty">
          Waiting for the first transcript line — the five lenses react as soon as the buffer has content.
        </div>
      )}

      {personas.length > 0 && (
        <>
          <ul className="persona-pulse__chips">
            {personas.map((p) => {
              const direction = sentimentBucket(p.sentiment);
              const isOpen = expanded === p.key;
              return (
                <li key={p.key}>
                  <button
                    type="button"
                    className={`persona-pulse__chip-btn persona-pulse__chip-btn--${direction} ${isOpen ? 'is-open' : ''}`}
                    onClick={() => setExpanded(isOpen ? null : p.key)}
                  >
                    <span className="persona-pulse__chip-name">{p.display}</span>
                    <span className={`persona-pulse__chip-score persona-pulse__chip-score--${direction}`}>
                      {p.sentiment >= 0 ? '+' : ''}{p.sentiment.toFixed(2)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          {expanded && (() => {
            const p = personas.find((x) => x.key === expanded);
            if (!p) return null;
            const direction = sentimentBucket(p.sentiment);
            return (
              <div className={`persona-pulse__detail persona-pulse__detail--${direction}`}>
                <div className="persona-pulse__detail-top">
                  <strong>{p.display}</strong>
                  <span className="persona-pulse__detail-lens">{p.lens}</span>
                  <span className="persona-pulse__detail-conf">{p.confidence}</span>
                </div>
                <p className="persona-pulse__detail-line">"{p.one_line}"</p>
                {p.flag && (
                  <div className="persona-pulse__detail-flag">⚑ {flagLabel(p.flag)}</div>
                )}
              </div>
            );
          })()}
        </>
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
