import React, { useEffect, useRef, useState } from 'react';

/**
 * Auto-playing hero demo. Cycles through phases that tell the EarningsEdge
 * story in ~9 seconds:
 *   empty   → cursor blinks in ticker field
 *   typing  → "NVDA" appears letter by letter
 *   loading → loading strip animates in, data slices report ready one by one
 *   partial → metrics fade in
 *   full    → trade signal hero materializes + transcript starts scrolling
 *   fade    → quick fade-out
 *   restart
 *
 * Pauses when offscreen (IntersectionObserver) so it doesn't burn CPU when
 * the user scrolls away. Pauses on hover so users can read mid-cycle.
 */

const PHASES = [
  { id: 'empty',   ms: 1100 },
  { id: 'typing',  ms: 900  },
  { id: 'loading', ms: 1500 },
  { id: 'partial', ms: 1300 },
  { id: 'full',    ms: 3400 },
  { id: 'fade',    ms: 500  },
];

const TICKER = 'NVDA';

const TRANSCRIPT_LINES = [
  { speaker: 'CEO',     text: 'Q4 was the strongest quarter in the company\'s history.' },
  { speaker: 'CFO',     text: 'Operating margin expanded 220 bps year-over-year.' },
  { speaker: 'CEO',     text: 'Demand visibility into FY26 remains exceptional.' },
  { speaker: 'ANALYST', text: 'Can you walk through inference vs training mix?' },
];

const DATA_SLICES = ['Fundamentals', 'Peers', 'News', 'Macro', 'Technicals', 'Analyst'];

function useReplayKey() {
  const [k, setK] = useState(0);
  return { k, replay: () => setK((x) => x + 1) };
}

export default function HeroDemo() {
  const { k: replayKey, replay } = useReplayKey();
  const [phaseIdx, setPhaseIdx] = useState(0);
  const [typedCount, setTypedCount] = useState(0);
  const [loadedSlices, setLoadedSlices] = useState(0);
  const [transcriptCount, setTranscriptCount] = useState(0);
  const [paused, setPaused] = useState(false);
  const [inView, setInView] = useState(true);
  const rootRef = useRef(null);
  const timeoutsRef = useRef([]);

  // Pause when offscreen so we don't waste CPU.
  useEffect(() => {
    if (!rootRef.current || typeof IntersectionObserver === 'undefined') return undefined;
    const obs = new IntersectionObserver(
      ([entry]) => setInView(entry.isIntersecting && entry.intersectionRatio > 0.15),
      { threshold: [0, 0.15, 0.5] },
    );
    obs.observe(rootRef.current);
    return () => obs.disconnect();
  }, []);

  // Drive the phase machine.
  useEffect(() => {
    if (paused || !inView) return undefined;
    timeoutsRef.current.forEach(clearTimeout);
    timeoutsRef.current = [];

    const phase = PHASES[phaseIdx];

    // Phase-specific sub-animations
    if (phase.id === 'empty') {
      setTypedCount(0);
      setLoadedSlices(0);
      setTranscriptCount(0);
    } else if (phase.id === 'typing') {
      // Type 4 letters across the phase duration
      const per = phase.ms / TICKER.length;
      for (let i = 0; i < TICKER.length; i++) {
        const t = setTimeout(() => setTypedCount(i + 1), per * (i + 1));
        timeoutsRef.current.push(t);
      }
    } else if (phase.id === 'loading') {
      // Slices "arrive" one at a time
      const per = phase.ms / DATA_SLICES.length;
      for (let i = 0; i < DATA_SLICES.length; i++) {
        const t = setTimeout(() => setLoadedSlices(i + 1), per * (i + 1));
        timeoutsRef.current.push(t);
      }
    } else if (phase.id === 'full') {
      // Transcript lines appear one at a time inside the "full" phase
      const per = (phase.ms - 600) / TRANSCRIPT_LINES.length;
      for (let i = 0; i < TRANSCRIPT_LINES.length; i++) {
        const t = setTimeout(() => setTranscriptCount(i + 1), 600 + per * (i + 1));
        timeoutsRef.current.push(t);
      }
    } else if (phase.id === 'fade') {
      // No sub-anim
    }

    // Advance to next phase after this one's duration
    const advance = setTimeout(() => {
      setPhaseIdx((i) => (i + 1) % PHASES.length);
    }, phase.ms);
    timeoutsRef.current.push(advance);

    return () => timeoutsRef.current.forEach(clearTimeout);
  }, [phaseIdx, paused, inView, replayKey]);

  // When user clicks Replay, jump to phase 0 immediately.
  useEffect(() => {
    setPhaseIdx(0);
    setTypedCount(0);
    setLoadedSlices(0);
    setTranscriptCount(0);
  }, [replayKey]);

  const phase = PHASES[phaseIdx];
  const typed = TICKER.slice(0, typedCount);
  const showLoadingStrip = phase.id === 'loading';
  const showContextBar = ['partial', 'full', 'fade'].includes(phase.id);
  const showMetrics = ['partial', 'full', 'fade'].includes(phase.id);
  const showVerdict = ['full', 'fade'].includes(phase.id);
  const showTranscript = phase.id === 'full' || phase.id === 'fade';
  const fading = phase.id === 'fade';
  const formActive = phase.id === 'empty' || phase.id === 'typing';

  const slicesToShow = DATA_SLICES.slice(loadedSlices);
  const slicesDone = DATA_SLICES.slice(0, loadedSlices);

  return (
    <div className="hero-demo-wrap">
      <div
        ref={rootRef}
        className={`hero-demo ${fading ? 'is-fading' : ''}`}
        onMouseEnter={() => setPaused(true)}
        onMouseLeave={() => setPaused(false)}
        aria-label="Animated demo of the EarningsEdge cockpit"
      >
        {/* Browser chrome */}
        <div className="hd-bar">
          <span className="hd-dot hd-dot-r" />
          <span className="hd-dot hd-dot-y" />
          <span className="hd-dot hd-dot-g" />
          <span className="hd-bar-text">earningsedge.app/app</span>
          <span className="hd-bar-status">
            <span className={`hd-bar-status-dot ${inView && !paused ? 'is-live' : ''}`} />
            {paused ? 'paused' : 'live demo'}
          </span>
        </div>

        {/* App nav (mocked) */}
        <div className="hd-nav">
          <div className="hd-brand">
            <span className="hd-brand-mark" aria-hidden="true">
              <svg viewBox="0 0 20 20" width="16" height="16" fill="none">
                <path d="M3 14l4-5 3 3 4-7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                <circle cx="14" cy="5" r="1.6" fill="currentColor" />
              </svg>
            </span>
            <span className="hd-brand-text">Earnings<span className="hd-brand-edge">Edge</span></span>
          </div>
          <div className="hd-nav-tabs">
            <span className="hd-nav-tab is-active">
              Company
              {showContextBar && <span className="hd-nav-chip">{TICKER}</span>}
            </span>
            <span className="hd-nav-tab">
              Trading
              <span className="hd-nav-chip hd-nav-chip-paper">PAPER</span>
            </span>
          </div>
        </div>

        {/* Body */}
        <div className="hd-body">
          {formActive && (
            <div className="hd-empty">
              <div className="hd-empty-eyebrow">Welcome — pick a company</div>
              <div className="hd-empty-row">
                <div className="hd-input">
                  <span className="hd-input-text">{typed}</span>
                  <span className="hd-input-caret" />
                </div>
                <button className="hd-cta" disabled={typedCount < TICKER.length}>
                  Load company →
                </button>
              </div>
            </div>
          )}

          {showContextBar && (
            <div className="hd-ccb">
              <div className="hd-ccb-left">
                <span className="hd-stage-dot" />
                <span className="hd-stage">PRE-CALL COVERAGE</span>
                <span className="hd-ticker">NVDA</span>
                <span className="hd-name">· NVIDIA Corp</span>
              </div>
              <div className="hd-ccb-right">
                <button className="hd-ghost">Change</button>
                <button className="hd-cta hd-cta-sm">▶ Listen live</button>
              </div>
            </div>
          )}

          {showLoadingStrip && (
            <div className="hd-loading">
              <span className="hd-spinner" />
              <span>
                Loading{' '}
                {slicesToShow.map((s, i) => (
                  <span key={s}>
                    <span className="hd-slice-pending">{s}</span>
                    {i < slicesToShow.length - 1 && <span className="hd-slice-sep"> · </span>}
                  </span>
                ))}
              </span>
              {slicesDone.length > 0 && (
                <span className="hd-loaded">
                  · {slicesDone.length} of {DATA_SLICES.length} ready
                </span>
              )}
            </div>
          )}

          {showVerdict && (
            <div className={`hd-verdict ${showVerdict ? 'is-in' : ''}`}>
              <div className="hd-verdict-eyebrow">FINAL SYNTHESIS · TRADE SIGNAL</div>
              <div className="hd-verdict-row">
                <div className="hd-verdict-action">BUY</div>
                <div className="hd-verdict-pill">CONFIDENCE · HIGH</div>
                <div className="hd-verdict-price">$208.27</div>
              </div>
              <div className="hd-verdict-thesis">
                Bullish on data-center momentum; gross margin holds despite mix shift.
              </div>
            </div>
          )}

          {(showMetrics || showTranscript) && (
            <div className="hd-grid">
              {showTranscript && (
                <div className="hd-card hd-card-tall">
                  <div className="hd-card-title">BRIEFING TRANSCRIPT</div>
                  {TRANSCRIPT_LINES.slice(0, transcriptCount).map((l, i) => (
                    <div
                      key={i}
                      className={`hd-line ${l.speaker === 'CEO' ? 'hd-line-cyan' : ''}`}
                      style={{ animationDelay: `${i * 80}ms` }}
                    >
                      <span className="hd-line-speaker">{l.speaker}</span>
                      <span className="hd-line-text">{l.text}</span>
                    </div>
                  ))}
                  {transcriptCount < TRANSCRIPT_LINES.length && (
                    <div className="hd-line hd-line-typing">
                      <span className="hd-line-cursor" />
                    </div>
                  )}
                </div>
              )}
              {showMetrics && (
                <>
                  <div className="hd-card">
                    <div className="hd-card-title">REVENUE</div>
                    <div className="hd-card-big">$82.98B</div>
                    <div className="hd-card-sub hd-good">+0.1% vs est</div>
                  </div>
                  <div className="hd-card">
                    <div className="hd-card-title">DILUTED EPS</div>
                    <div className="hd-card-big">$4.14</div>
                    <div className="hd-card-sub hd-good">vs $3.22 est</div>
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        {/* Phase indicator + replay */}
        <div className="hd-controls">
          <div className="hd-progress" aria-hidden="true">
            {PHASES.map((p, i) => (
              <span
                key={p.id}
                className={`hd-progress-dot ${i === phaseIdx ? 'is-current' : ''} ${i < phaseIdx ? 'is-done' : ''}`}
              />
            ))}
          </div>
          <button type="button" className="hd-replay" onClick={replay} title="Replay the demo">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 12a9 9 0 1 0 3-6.7" />
              <path d="M3 4v5h5" />
            </svg>
            Replay
          </button>
        </div>
      </div>
    </div>
  );
}
