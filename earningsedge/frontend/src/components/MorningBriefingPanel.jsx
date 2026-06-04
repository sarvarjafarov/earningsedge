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
export default function MorningBriefingPanel({ onPickTicker }) {
  const API_BASE = getApiBase();
  const [watchlist, setWatchlist] = useState([]);
  const [briefing, setBriefing] = useState(null);
  const [calendar, setCalendar] = useState([]);
  const [adding, setAdding] = useState('');
  const [running, setRunning] = useState(false);
  const [loaded, setLoaded] = useState(false);

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

  return (
    <section className="briefing">
      <header className="briefing__head">
        <div className="briefing__title">
          <span className="briefing__badge">OVERNIGHT</span>
          <h3>Morning briefing</h3>
        </div>
        <button onClick={runNow} disabled={running} className="briefing__run">
          {running ? 'Running pipeline…' : '↻ Run pipeline now'}
        </button>
      </header>

      <div className="briefing__grid">
        {/* Watchlist editor */}
        <div className="briefing__card">
          <h4>Watchlist</h4>
          <ul className="briefing__tickers">
            {watchlist.map((t) => (
              <li key={t}>
                <button className="briefing__chip" onClick={() => onPickTicker && onPickTicker(t)}>{t}</button>
                <button className="briefing__remove" onClick={() => removeTicker(t)} title="Remove">×</button>
              </li>
            ))}
          </ul>
          <div className="briefing__add">
            <input
              value={adding}
              onChange={(e) => setAdding(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') addTicker(); }}
              placeholder="Add ticker (e.g. PLTR)"
              maxLength={6}
            />
            <button onClick={addTicker} disabled={!adding.trim()}>Add</button>
          </div>
        </div>

        {/* Today's verdicts */}
        <div className="briefing__card briefing__card--wide">
          <h4>Today's verdicts</h4>
          {!loaded && <div className="briefing__empty">Loading…</div>}
          {loaded && !briefing && (
            <div className="briefing__empty">
              No briefing yet for today — the overnight pipeline runs at ~6 AM ET.
              Click <em>Run pipeline now</em> above to trigger it immediately.
            </div>
          )}
          {briefing && Array.isArray(briefing.verdicts) && briefing.verdicts.length === 0 && (
            <div className="briefing__empty">
              No watchlist earnings calls overnight. The pipeline ran ({briefing.date}) and found nothing new.
            </div>
          )}
          {briefing && Array.isArray(briefing.verdicts) && briefing.verdicts.length > 0 && (
            <ol className="briefing__verdicts">
              {briefing.verdicts.map((v, i) => (
                <li key={i} className={`briefing__verdict ${v.ok ? '' : 'briefing__verdict--err'}`}>
                  <div className="briefing__vhead">
                    <button className="briefing__chip" onClick={() => onPickTicker && onPickTicker(v.ticker)}>
                      {v.ticker}
                    </button>
                  </div>
                  <div className="briefing__vtext">
                    {v.ok ? (v.response || '').slice(0, 380) : (v.error || 'pipeline error')}
                    {v.ok && (v.response || '').length > 380 ? '…' : ''}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>

        {/* Upcoming calendar */}
        <div className="briefing__card">
          <h4>Next 7 days</h4>
          {!calendar.length && (
            <div className="briefing__empty">No upcoming earnings calls in your watchlist.</div>
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
      </div>
    </section>
  );
}
