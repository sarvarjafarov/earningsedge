import React, { useEffect, useState } from 'react';
import { getApiBase, sessionHeaders } from '../apiConfig';

/**
 * MorningBriefingPanel — the "you slept through earnings, here's what
 * you missed" surface.
 *
 * Loads today's morning briefing from the overnight pipeline (if it
 * ran), shows the watchlist with an inline editor, and lists upcoming
 * earnings calls in the next 7 days. A "Run pipeline now" button
 * triggers the pipeline on demand — useful for the demo so a judge
 * can see the verdicts populate in real time.
 */
// Same default the backend seeds; we hydrate the UI immediately with this
// so users don't see an empty card while the API call is in flight on a
// cold Heroku dyno.
const DEFAULT_WATCHLIST = ['NVDA', 'AAPL', 'MSFT', 'GOOGL', 'TSLA', 'AMZN'];

export default function MorningBriefingPanel({ onPickTicker }) {
  const API_BASE = getApiBase();
  const [watchlist, setWatchlist] = useState(DEFAULT_WATCHLIST);
  const [briefing, setBriefing] = useState(null);
  const [calendar, setCalendar] = useState([]);
  const [adding, setAdding] = useState('');
  const [running, setRunning] = useState(false);
  const [loaded, setLoaded] = useState(false);
  // Track which ticker is being loaded so we can show a clear loading
  // state on the chip the user just clicked (instead of dead silence
  // while the 10-15s coverage call fires).
  const [pickingTicker, setPickingTicker] = useState(null);

  async function loadAll() {
    setLoaded(false);
    try {
      const [wl, br, cal] = await Promise.all([
        fetch(`${API_BASE}/api/watchlist`, { headers: sessionHeaders() }).then((r) => r.json()),
        fetch(`${API_BASE}/api/briefings/today`, { headers: sessionHeaders() }).then((r) => r.json()),
        fetch(`${API_BASE}/api/calendar/upcoming`, { headers: sessionHeaders() }).then((r) => r.json()),
      ]);
      if (Array.isArray(wl.tickers)) setWatchlist(wl.tickers);
      setBriefing(br?.briefing || null);
      setCalendar(Array.isArray(cal?.events) ? cal.events : []);
    } catch (e) {
      // non-fatal — keep whatever we had
      // eslint-disable-next-line no-console
      console.warn('briefing panel load failed:', e);
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function addTicker() {
    const t = adding.trim().toUpperCase();
    if (!t) return;
    try {
      const r = await fetch(`${API_BASE}/api/watchlist/add`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...sessionHeaders() },
        body: JSON.stringify({ ticker: t }),
      });
      const body = await r.json();
      if (body.ok && Array.isArray(body.tickers)) setWatchlist(body.tickers);
      setAdding('');
    } catch (_) { /* ignore */ }
  }

  async function removeTicker(t) {
    try {
      const r = await fetch(`${API_BASE}/api/watchlist/remove`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...sessionHeaders() },
        body: JSON.stringify({ ticker: t }),
      });
      const body = await r.json();
      if (body.ok && Array.isArray(body.tickers)) setWatchlist(body.tickers);
    } catch (_) { /* ignore */ }
  }

  async function runNow() {
    setRunning(true);
    try {
      const r = await fetch(`${API_BASE}/api/briefings/run_now`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...sessionHeaders() },
        body: JSON.stringify({ force: true }),
      });
      await r.json();
      await loadAll();
    } catch (_) { /* ignore */ }
    setRunning(false);
  }

  // Per-ticker meta from today's briefing (so the chip can show "ADD", "HOLD", etc.)
  const verdictByTicker = {};
  if (briefing && Array.isArray(briefing.verdicts)) {
    for (const v of briefing.verdicts) {
      if (v.ticker && v.ok) verdictByTicker[v.ticker] = v;
    }
  }

  return (
    <section className="briefing">
      {/* === Primary CTA strip — tells the user EXACTLY what to do === */}
      <div className="briefing__cta">
        <div className="briefing__cta-text">
          <strong>Step 1 — pick a company.</strong>{' '}
          <span>Click any ticker below to load the agent verdict, memory, and news.</span>
        </div>
        <button onClick={runNow} disabled={running} className="briefing__run">
          {running ? 'Running…' : '↻ Refresh overnight verdicts'}
        </button>
      </div>

      {/* === Big clickable ticker cards (the primary entry point) === */}
      <div className="briefing__tickers-grid">
        {watchlist.map((t) => {
          const v = verdictByTicker[t];
          const isPicking = pickingTicker === t;
          return (
            <button
              key={t}
              className={`ticker-card ${v ? 'ticker-card--has-verdict' : ''} ${isPicking ? 'ticker-card--loading' : ''}`}
              onClick={() => {
                if (pickingTicker) return;  // already loading something
                setPickingTicker(t);
                onPickTicker && onPickTicker(t);
                // Reset after the coverage call should be done so user can re-click
                setTimeout(() => setPickingTicker(null), 18000);
              }}
              disabled={!!pickingTicker}
            >
              <div className="ticker-card__symbol">{t}</div>
              {isPicking ? (
                <div className="ticker-card__loading-state">
                  <span className="ticker-card__spinner" />
                  Loading coverage…
                </div>
              ) : v ? (
                <div className="ticker-card__verdict-mini">
                  {(v.response || '').slice(0, 90)}…
                </div>
              ) : null}
              <div className="ticker-card__cta">
                {isPicking ? 'Resolving company + loading data…' : <>Open cockpit <span className="ticker-card__arrow">→</span></>}
              </div>
              {!isPicking && (
                <button
                  className="ticker-card__remove"
                  onClick={(e) => { e.stopPropagation(); removeTicker(t); }}
                  title="Remove from watchlist"
                >×</button>
              )}
            </button>
          );
        })}
        {/* Add-ticker card */}
        <div className="ticker-card ticker-card--add">
          <input
            value={adding}
            onChange={(e) => setAdding(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') addTicker(); }}
            placeholder="Add (PLTR)"
            maxLength={6}
          />
          <button onClick={addTicker} disabled={!adding.trim()} className="ticker-card__add-btn">
            + Add
          </button>
        </div>
      </div>

      {/* === Secondary info row: calendar + briefing summary === */}
      <div className="briefing__secondary">
        <div className="briefing__card">
          <h4>📅 Upcoming earnings calls in your watchlist</h4>
          {!calendar.length && (
            <div className="briefing__empty">
              No earnings calls in your watchlist over the next 7 days. Pick any ticker above to analyze it now.
            </div>
          )}
          {calendar.length > 0 && (
            <ul className="briefing__cal">
              {calendar.slice(0, 8).map((e, i) => (
                <li key={i}>
                  <button className="briefing__chip" onClick={() => onPickTicker && onPickTicker(e.ticker)}>
                    {e.ticker}
                  </button>
                  <span className="briefing__caldate">{e.date}</span>
                  <span className="briefing__caltime">{(e.time || '').toUpperCase()}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="briefing__card">
          <h4>🌅 Overnight verdicts</h4>
          {!loaded && <div className="briefing__empty">Loading the overnight briefing…</div>}
          {loaded && !briefing && (
            <div className="briefing__empty">
              The overnight pipeline runs at <strong>7 AM ET</strong>. Click <em>Refresh overnight verdicts</em> above to run it now.
            </div>
          )}
          {briefing && Array.isArray(briefing.verdicts) && briefing.verdicts.length === 0 && (
            <div className="briefing__empty">
              The overnight pipeline ran ({briefing.date}) but found no earnings calls in your watchlist last night.
            </div>
          )}
          {briefing && Array.isArray(briefing.verdicts) && briefing.verdicts.length > 0 && (
            <ol className="briefing__verdicts">
              {briefing.verdicts.slice(0, 4).map((v, i) => (
                <li key={i} className={`briefing__verdict ${v.ok ? '' : 'briefing__verdict--err'}`}>
                  <button className="briefing__chip" onClick={() => onPickTicker && onPickTicker(v.ticker)}>
                    {v.ticker} →
                  </button>
                  <div className="briefing__vtext">
                    {v.ok ? (v.response || '').slice(0, 180) : (v.error || 'pipeline error')}
                    {v.ok && (v.response || '').length > 180 ? '…' : ''}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </section>
  );
}
