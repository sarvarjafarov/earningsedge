import React, { useEffect, useState } from 'react';
import { getApiBase } from '../apiConfig';

/**
 * NewsDigestPanel — top-ranked recent news for a single ticker.
 *
 * Surfaces the 6 highest-weighted Finnhub headlines (Reuters / WSJ /
 * Bloomberg / FT / CNBC carry more weight than aggregators) over the
 * last 7 days. Renders next to the Chairman panel so the user sees
 * the same inputs that influenced the verdict.
 */
export default function NewsDigestPanel({ ticker }) {
  const API_BASE = getApiBase();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!ticker) return;
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const r = await fetch(`${API_BASE}/api/news/digest?ticker=${encodeURIComponent(ticker)}&top_n=6`);
        const body = await r.json();
        if (!cancelled && Array.isArray(body.items)) setItems(body.items);
      } catch (_) {
        if (!cancelled) setItems([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [ticker, API_BASE]);

  if (!ticker) return null;
  if (!loading && !items.length) return null;

  return (
    <section className="news-digest">
      <header className="news-digest__head">
        <span className="news-digest__badge">NEWS</span>
        <h3>Recent narrative — top headlines, 7 days</h3>
        {loading && <span className="news-digest__loading">loading…</span>}
      </header>
      <ol className="news-digest__list">
        {items.slice(0, 6).map((n, i) => (
          <li key={i} className="news-digest__item">
            <a href={n.url || '#'} target="_blank" rel="noreferrer" className="news-digest__title">
              {n.headline}
            </a>
            <div className="news-digest__meta">
              <span>{n.source || 'unknown source'}</span>
              <span>·</span>
              <span>{Math.round(n.age_hours || 0)}h ago</span>
              <span>·</span>
              <span title="source-weighted score">score {n.score}</span>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
