import React, { useEffect, useRef, useState } from 'react';
import { getApiBase } from '../apiConfig';

/**
 * PatternMatchesPanel — surfaces lines from the live transcript that
 * vector-match prior committee verdicts at similarity ≥ 0.78.
 *
 * As the audio streams, the memory engine fires inline alerts:
 *   "CFO just used the same compute-capacity language as our
 *    NVDA Q1 2024 trim verdict (sim 0.93)."
 *
 * This is the memory loop running on top of the live audio path, not
 * just at the end-of-call synthesis.
 */
export default function PatternMatchesPanel({ ticker, transcript }) {
  const API_BASE = getApiBase();
  const [matches, setMatches] = useState([]);
  const lastProcessedIdxRef = useRef(0);
  const lastFiredAtRef = useRef(0);

  useEffect(() => {
    if (!ticker || !Array.isArray(transcript) || transcript.length === 0) return;
    // Process newly arrived lines only.
    const newLines = transcript.slice(lastProcessedIdxRef.current);
    // Only fire when we have at least 3 new lines AND it's been ≥20s since
    // the last call. Prevents flooding /api/transcript/highlights during
    // dense transcription bursts (the endpoint then queues, fills memory,
    // and crashes the dyno on Heroku Basic).
    if (newLines.length < 3) return;
    const now = Date.now();
    if (now - lastFiredAtRef.current < 20000) return;
    lastProcessedIdxRef.current = transcript.length;
    lastFiredAtRef.current = now;

    let cancelled = false;
    (async () => {
      try {
        const payload = newLines
          .map((l) => (l && l.text ? l.text : ''))
          .filter((s) => s && s.length >= 20)
          .slice(-5);  // cap batch size client-side too
        if (payload.length === 0) return;
        const r = await fetch(`${API_BASE}/api/transcript/highlights`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ticker, lines: payload }),
        });
        if (!r.ok) return;
        const data = await r.json();
        const hits = (data.lines || [])
          .filter((row) => row.match)
          .map((row) => ({ ...row, ts: Date.now() }));
        if (!cancelled && hits.length > 0) {
          setMatches((prev) => [...hits, ...prev].slice(0, 12));
        }
      } catch (_) {
        // soft-fail
      }
    })();
    return () => { cancelled = true; };
  }, [transcript, ticker, API_BASE]);

  if (!ticker || matches.length === 0) return null;

  return (
    <section className="pattern-matches">
      <header className="pattern-matches__head">
        <span className="pattern-matches__badge">PATTERN ⚑</span>
        <h3>Live memory matches — phrases that rhyme with past verdicts</h3>
      </header>

      <ul className="pattern-matches__list">
        {matches.slice(0, 6).map((m, i) => (
          <li key={i} className="pattern-matches__item">
            <div className="pattern-matches__quote">"{m.text}"</div>
            <div className="pattern-matches__line">
              ↳ <strong>{m.match.ticker}</strong>{' '}
              <span className={`pattern-matches__action pattern-matches__action--${String(m.match.action || '').toLowerCase()}`}>
                {m.match.action || 'Hold'}
              </span>{' '}
              <span className="pattern-matches__sim" title="cosine similarity">
                sim {Number(m.match.similarity || 0).toFixed(3)}
              </span>
            </div>
            <div className="pattern-matches__snippet">{m.match.snippet}…</div>
          </li>
        ))}
      </ul>
    </section>
  );
}
