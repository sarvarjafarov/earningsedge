import React, { useEffect, useRef, useState } from 'react';
import HeroDemo from './HeroDemo';

/* useReveal — adds `.is-revealed` to the ref'd element the first time it
 * enters the viewport. Drives the fade-up-on-scroll polish on every section. */
function useReveal(threshold = 0.18) {
  const ref = useRef(null);
  const [revealed, setRevealed] = useState(false);
  useEffect(() => {
    if (!ref.current || revealed) return undefined;
    if (typeof IntersectionObserver === 'undefined') {
      setRevealed(true);
      return undefined;
    }
    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setRevealed(true);
          obs.disconnect();
        }
      },
      { threshold },
    );
    obs.observe(ref.current);
    return () => obs.disconnect();
  }, [revealed, threshold]);
  return [ref, revealed];
}

/* useCountUp — animate an integer or float from 0 to target once, when the
 * element enters the viewport. Returns [ref, displayedValue].
 *
 * RAF + observer cleanup both happen in the OUTER useEffect cleanup so they
 * actually fire when the component unmounts mid-animation. (Previously the
 * `cancelAnimationFrame` was returned from the IntersectionObserver callback,
 * which discards the return value.) */
function useCountUp(target, { duration = 1100, decimals = 0 } = {}) {
  const ref = useRef(null);
  const [val, setVal] = useState(0);
  const startedRef = useRef(false);
  useEffect(() => {
    if (!ref.current || startedRef.current) return undefined;
    if (typeof IntersectionObserver === 'undefined') {
      setVal(target);
      return undefined;
    }
    let raf = 0;
    let cancelled = false;
    const obs = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting || startedRef.current) return;
      startedRef.current = true;
      const start = performance.now();
      const tick = (now) => {
        if (cancelled) return;
        const t = Math.min((now - start) / duration, 1);
        // ease-out cubic
        const eased = 1 - Math.pow(1 - t, 3);
        setVal(target * eased);
        if (t < 1) raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    }, { threshold: 0.4 });
    obs.observe(ref.current);
    return () => {
      cancelled = true;
      if (raf) cancelAnimationFrame(raf);
      obs.disconnect();
    };
  }, [target, duration]);
  const formatted = decimals === 0 ? Math.round(val).toString() : val.toFixed(decimals);
  return [ref, formatted];
}

/* SVG icon set — outlined, currentColor, consistent stroke. */
const Icon = {
  Mic: (props) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0" />
      <path d="M12 18v3" />
    </svg>
  ),
  Chart: (props) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M3 17V7" />
      <path d="M9 17V11" />
      <path d="M15 17V4" />
      <path d="M21 17v-7" />
      <path d="M3 21h18" />
    </svg>
  ),
  Users: (props) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20a6.5 6.5 0 0 1 13 0" />
      <circle cx="17" cy="9" r="2.5" />
      <path d="M21 19a4 4 0 0 0-5-3.87" />
    </svg>
  ),
  Money: (props) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M3 12l5-5 4 4 5-7" />
      <path d="M14 4h4v4" />
      <path d="M3 20h18" />
    </svg>
  ),
  Globe: (props) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18" />
      <path d="M12 3a14 14 0 0 1 0 18" />
      <path d="M12 3a14 14 0 0 0 0 18" />
    </svg>
  ),
  Pulse: (props) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M3 12h4l2-7 4 14 2-7h6" />
    </svg>
  ),
  News: (props) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M7 8h10" />
      <path d="M7 12h10" />
      <path d="M7 16h6" />
    </svg>
  ),
  Sentiment: (props) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M8.5 14a4 4 0 0 0 7 0" />
      <circle cx="9" cy="10" r="0.6" fill="currentColor" />
      <circle cx="15" cy="10" r="0.6" fill="currentColor" />
    </svg>
  ),
  Verdict: (props) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M12 3l8 4v6c0 4.5-3.5 7.5-8 8-4.5-.5-8-3.5-8-8V7l8-4z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  ),
  Arrow: (props) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M5 12h14" />
      <path d="M13 6l6 6-6 6" />
    </svg>
  ),
  GitHub: (props) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
      <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.56v-2.18c-3.2.7-3.87-1.37-3.87-1.37-.52-1.32-1.27-1.67-1.27-1.67-1.04-.71.08-.7.08-.7 1.15.08 1.76 1.18 1.76 1.18 1.02 1.75 2.69 1.24 3.34.95.1-.74.4-1.24.72-1.53-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.29 1.18-3.1-.12-.29-.51-1.46.11-3.04 0 0 .96-.31 3.15 1.18.91-.25 1.89-.38 2.86-.39.97.01 1.95.14 2.87.39 2.18-1.49 3.14-1.18 3.14-1.18.62 1.58.23 2.75.12 3.04.74.81 1.18 1.84 1.18 3.1 0 4.42-2.69 5.39-5.25 5.68.41.36.78 1.06.78 2.14v3.17c0 .31.21.68.8.56 4.57-1.52 7.86-5.83 7.86-10.91C23.5 5.65 18.35.5 12 .5z" />
    </svg>
  ),
};

const FEATURES = [
  {
    icon: Icon.Mic,
    title: 'Live transcription',
    body: 'Share the browser tab playing the webcast — Gemini Live transcribes the call as it streams. Speaker-tagged, instantly searchable.',
  },
  {
    icon: Icon.Verdict,
    title: 'Committee verdict',
    body: 'Seven specialist agents synthesize a single BUY · HOLD · SHORT call with confidence and a written thesis. No black box.',
  },
  {
    icon: Icon.Chart,
    title: 'Pre-call coverage',
    body: 'Fundamentals, peer comps, analyst targets, macro & technicals — all loaded in seconds before the first word of the call.',
  },
  {
    icon: Icon.Money,
    title: 'Paper trading',
    body: 'Confirm-then-execute orders via Alpaca paper. Real workflow, no real money. The PAPER badge is always visible.',
  },
];

const STEPS = [
  {
    n: 1,
    title: 'Pick a company',
    body: 'Type a ticker like NVDA, or just speak the company name. Coverage starts loading immediately.',
  },
  {
    n: 2,
    title: 'Listen live',
    body: 'Open any browser tab playing audio — earnings webcast, news segment, conference live stream — then click Listen live and share that tab with audio.',
  },
  {
    n: 3,
    title: 'Decide',
    body: 'Read the live transcript, watch sentiment shift, ask the analyst questions, and place paper trades from the verdict.',
  },
];

const COMMITTEE = [
  { icon: Icon.Globe, name: 'Macro Agent', body: 'Yield curve, FRED data, policy stance.' },
  { icon: Icon.Pulse, name: 'Technical Agent', body: 'SMA crossovers, MACD, RSI, momentum.' },
  { icon: Icon.Users, name: 'Peer Agent', body: 'Multiples vs. competitors. Where the company sits in the cohort.' },
  { icon: Icon.Verdict, name: 'Analyst Agent', body: 'Sell-side consensus, target upside, recent revisions.' },
  { icon: Icon.News, name: 'News Agent', body: 'Headlines classified by sentiment, deduped, weighted.' },
  { icon: Icon.Sentiment, name: 'Sentiment Agent', body: 'Live tone of the call: bullish drivers, bearish drivers, risks.' },
  { icon: Icon.Chart, name: 'Synthesis Committee', body: 'Weights every voice into one verdict + a one-paragraph thesis.' },
];

export default function Landing({ onOpenApp }) {
  const goApp = (e) => {
    if (e) e.preventDefault();
    onOpenApp?.();
  };

  // Scroll-reveal refs for each major section.
  const [featuresRef, featuresIn] = useReveal();
  const [stepsRef, stepsIn] = useReveal();
  const [committeeRef, committeeIn] = useReveal();
  const [trustRef, trustIn] = useReveal();
  const [finalRef, finalIn] = useReveal();

  // Animated counters for hero stats (kick in when the hero is visible).
  const [statSecRef, statSec] = useCountUp(60);
  const [statAgentsRef, statAgents] = useCountUp(7);
  const [statVerdictRef, statVerdict] = useCountUp(1);

  return (
    <div className="lp-root">
      {/* ───── Top bar ───── */}
      <header className="lp-topbar">
        <div className="lp-topbar-inner">
          <a className="lp-brand" href="/" onClick={(e) => e.preventDefault()}>
            <span className="lp-brand-mark" aria-hidden="true">
              <svg viewBox="0 0 20 20" width="22" height="22" fill="none">
                <path d="M3 14l4-5 3 3 4-7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                <circle cx="14" cy="5" r="1.6" fill="currentColor" />
              </svg>
            </span>
            <span className="lp-brand-text">
              Earnings<span className="lp-brand-edge">Edge</span>
            </span>
          </a>

          <nav className="lp-topbar-links" aria-label="Sections">
            <a href="#features">Features</a>
            <a href="#how-it-works">How it works</a>
            <a href="#committee">Committee</a>
            <a
              href="https://github.com/anthropics/claude-code"
              className="lp-topbar-github"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Source on GitHub"
              onClick={(e) => e.preventDefault()}
            >
              <Icon.GitHub width="16" height="16" /> Source
            </a>
            <a href="/app" className="lp-topbar-cta" onClick={goApp}>
              Open the cockpit <Icon.Arrow width="14" height="14" />
            </a>
          </nav>
        </div>
      </header>

      {/* ───── Hero ───── */}
      <section className="lp-hero">
        <div className="lp-hero-bg" aria-hidden="true">
          <div className="lp-hero-grid" />
          <div className="lp-hero-glow" />
        </div>

        <div className="lp-hero-inner">
          <div className="lp-hero-eyebrow">
            <span className="lp-hero-dot" />
            Real-time company audio cockpit · paper trading
          </div>

          <h1 className="lp-hero-title">
            Walk into every call, every interview <br />
            <span className="lp-hero-title-accent">with the cockpit.</span>
          </h1>

          <p className="lp-hero-sub">
            Pre-call coverage, live transcript, peer benchmarks, macro context,
            and a committee-driven trade signal — synced as audio unfolds.
            Stream an earnings webcast, news segment, or conference talk from any
            browser tab. Paper-trade in one click.
          </p>

          <div className="lp-hero-ctas">
            <a href="/app" className="lp-cta lp-cta-primary" onClick={goApp}>
              Open the cockpit <Icon.Arrow width="16" height="16" />
            </a>
            <a href="#how-it-works" className="lp-cta lp-cta-ghost">
              See how it works
            </a>
          </div>

          <div className="lp-hero-stats">
            <div className="lp-hero-stat" ref={statSecRef}>
              <span className="lp-hero-stat-value">&lt; {statSec}s</span>
              <span className="lp-hero-stat-label">to load coverage</span>
            </div>
            <div className="lp-hero-stat-div" />
            <div className="lp-hero-stat" ref={statAgentsRef}>
              <span className="lp-hero-stat-value">{statAgents}</span>
              <span className="lp-hero-stat-label">specialist agents</span>
            </div>
            <div className="lp-hero-stat-div" />
            <div className="lp-hero-stat" ref={statVerdictRef}>
              <span className="lp-hero-stat-value">{statVerdict}</span>
              <span className="lp-hero-stat-label">committee verdict</span>
            </div>
          </div>
        </div>

        {/* Live, auto-playing demo of the cockpit in motion */}
        <HeroDemo />
      </section>

      {/* ───── Features grid ───── */}
      <section className={`lp-section lp-reveal ${featuresIn ? 'is-revealed' : ''}`} id="features" ref={featuresRef}>
        <div className="lp-section-inner">
          <div className="lp-section-eyebrow">What you get</div>
          <h2 className="lp-section-title">A workspace, not a feed.</h2>
          <p className="lp-section-lede">
            Most earnings tools throw a wall of data at you. EarningsEdge stages
            the right view at the right moment — pre-call, live, post-call.
          </p>

          <div className="lp-features">
            {FEATURES.map((f) => (
              <div className="lp-feature" key={f.title}>
                <div className="lp-feature-icon"><f.icon width="20" height="20" /></div>
                <h3 className="lp-feature-title">{f.title}</h3>
                <p className="lp-feature-body">{f.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ───── How it works ───── */}
      <section className={`lp-section lp-section-alt lp-reveal ${stepsIn ? 'is-revealed' : ''}`} id="how-it-works" ref={stepsRef}>
        <div className="lp-section-inner">
          <div className="lp-section-eyebrow">How it works</div>
          <h2 className="lp-section-title">Three steps. About 90 seconds end-to-end.</h2>

          <div className="lp-steps">
            {STEPS.map((s) => (
              <div className="lp-step" key={s.n}>
                <div className="lp-step-num">{s.n}</div>
                <h3 className="lp-step-title">{s.title}</h3>
                <p className="lp-step-body">{s.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ───── Committee ───── */}
      <section className={`lp-section lp-reveal ${committeeIn ? 'is-revealed' : ''}`} id="committee" ref={committeeRef}>
        <div className="lp-section-inner">
          <div className="lp-section-eyebrow">The committee</div>
          <h2 className="lp-section-title">Seven specialists. One verdict.</h2>
          <p className="lp-section-lede">
            Each agent owns one slice of the picture. The synthesis committee weights
            their votes and writes the thesis. You see the votes, not just the answer.
          </p>

          <div className="lp-committee">
            {COMMITTEE.map((c) => (
              <div className="lp-committee-card" key={c.name}>
                <div className="lp-committee-icon"><c.icon width="20" height="20" /></div>
                <div className="lp-committee-text">
                  <div className="lp-committee-name">{c.name}</div>
                  <div className="lp-committee-body">{c.body}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ───── Trust + honest copy ───── */}
      <section className={`lp-section lp-section-alt lp-reveal ${trustIn ? 'is-revealed' : ''}`} ref={trustRef}>
        <div className="lp-section-inner lp-trust">
          <div className="lp-trust-block">
            <div className="lp-section-eyebrow">Built on</div>
            <h2 className="lp-trust-title">Real data, not vibes.</h2>
            <p className="lp-trust-sub">
              Every panel is wired to a public source you can verify. No inferred
              numbers, no synthesized data — when something says <em>$268.61</em>,
              that's the analyst mean target Finnhub returned this morning.
            </p>
            <div className="lp-stack">
              <div className="lp-stack-row">
                <span className="lp-stack-tag lp-stack-tag-ai">AI</span>
                <div className="lp-stack-chips">
                  <span className="lp-stack-chip">
                    <span className="lp-stack-chip-name">Gemini Live</span>
                    <span className="lp-stack-chip-role">Live transcription · agents</span>
                  </span>
                </div>
              </div>
              <div className="lp-stack-row">
                <span className="lp-stack-tag lp-stack-tag-data">MARKET</span>
                <div className="lp-stack-chips">
                  <span className="lp-stack-chip">
                    <span className="lp-stack-chip-name">Alpha Vantage</span>
                    <span className="lp-stack-chip-role">Quotes · OHLC</span>
                  </span>
                  <span className="lp-stack-chip">
                    <span className="lp-stack-chip-name">Financial Modeling Prep</span>
                    <span className="lp-stack-chip-role">Fundamentals · peers</span>
                  </span>
                  <span className="lp-stack-chip">
                    <span className="lp-stack-chip-name">Finnhub</span>
                    <span className="lp-stack-chip-role">Analyst consensus</span>
                  </span>
                  <span className="lp-stack-chip">
                    <span className="lp-stack-chip-name">yfinance</span>
                    <span className="lp-stack-chip-role">Backstop history</span>
                  </span>
                </div>
              </div>
              <div className="lp-stack-row">
                <span className="lp-stack-tag lp-stack-tag-macro">MACRO</span>
                <div className="lp-stack-chips">
                  <span className="lp-stack-chip">
                    <span className="lp-stack-chip-name">FRED</span>
                    <span className="lp-stack-chip-role">Treasury curve · econ</span>
                  </span>
                </div>
              </div>
              <div className="lp-stack-row">
                <span className="lp-stack-tag lp-stack-tag-trade">TRADE</span>
                <div className="lp-stack-chips">
                  <span className="lp-stack-chip">
                    <span className="lp-stack-chip-name">Alpaca paper</span>
                    <span className="lp-stack-chip-role">Paper account · orders</span>
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div className="lp-trust-block">
            <div className="lp-section-eyebrow">Be honest about it</div>
            <h2 className="lp-trust-title">No upsell, no hidden risk.</h2>
            <p className="lp-trust-sub">
              The product makes three explicit promises. Read them before you
              load a ticker.
            </p>
            <div className="lp-promise-grid">
              <div className="lp-promise lp-promise-money">
                <div className="lp-promise-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="6" width="18" height="12" rx="2" />
                    <circle cx="12" cy="12" r="3" />
                    <path d="M5 9h.01M19 15h.01" />
                    <path d="M3 6l18 12" stroke="currentColor" strokeWidth="2" />
                  </svg>
                </div>
                <div className="lp-promise-body">
                  <div className="lp-promise-title">No real money</div>
                  <div className="lp-promise-detail">
                    Every BUY / SHORT routes to your Alpaca <strong>paper</strong> account.
                    A red <em>PAPER</em> badge sits next to the order buttons at all times.
                  </div>
                </div>
              </div>

              <div className="lp-promise lp-promise-keys">
                <div className="lp-promise-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="8" cy="14" r="4" />
                    <path d="M11 11l9-9" />
                    <path d="M16 6l3 3" />
                    <path d="M18 4l3 3" />
                  </svg>
                </div>
                <div className="lp-promise-body">
                  <div className="lp-promise-title">Bring your own keys</div>
                  <div className="lp-promise-detail">
                    No SaaS pricing. Clone the repo, paste keys into <code>.env</code>,
                    and run locally. We never see your data or your trades.
                  </div>
                </div>
              </div>

              <div className="lp-promise lp-promise-advice">
                <div className="lp-promise-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 3l9 18H3z" />
                    <path d="M12 10v5" />
                    <circle cx="12" cy="18" r="0.6" fill="currentColor" />
                  </svg>
                </div>
                <div className="lp-promise-body">
                  <div className="lp-promise-title">Not financial advice</div>
                  <div className="lp-promise-detail">
                    Signals are agent synthesis from public data. They're a starting
                    point, not a recommendation. Decisions — and consequences — are
                    yours.
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ───── Final CTA ───── */}
      <section className={`lp-final lp-reveal ${finalIn ? 'is-revealed' : ''}`} ref={finalRef}>
        <div className="lp-final-inner">
          <h2 className="lp-final-title">Ready when the next call drops?</h2>
          <p className="lp-final-sub">
            Load a ticker now — coverage in under a minute. Stream a live call when
            you're ready.
          </p>
          <a href="/app" className="lp-cta lp-cta-primary lp-cta-large" onClick={goApp}>
            Open the cockpit <Icon.Arrow width="18" height="18" />
          </a>
          <div className="lp-final-disclaimer">
            EarningsEdge is informational only. Not financial advice.
          </div>
        </div>
      </section>

      {/* ───── Footer ───── */}
      <footer className="lp-footer">
        <div className="lp-footer-inner">
          <span className="lp-footer-brand">EarningsEdge</span>
          <span className="lp-footer-sep">·</span>
          <a href="/app" onClick={goApp}>Open the app</a>
          <span className="lp-footer-sep">·</span>
          <a href="#features" onClick={(e) => { e.preventDefault(); document.getElementById('features')?.scrollIntoView({ behavior: 'smooth' }); }}>Features</a>
          <span className="lp-footer-sep">·</span>
          <a href="#committee" onClick={(e) => { e.preventDefault(); document.getElementById('committee')?.scrollIntoView({ behavior: 'smooth' }); }}>Committee</a>
        </div>
      </footer>
    </div>
  );
}
