import React, { useCallback, useEffect, useState } from 'react';
import TradingViewChart from './TradingViewChart';
import { getApiBase } from '../apiConfig';

const API_BASE = getApiBase();

function fmtMoney(value, decimals = 2) {
  if (value === null || value === undefined || value === '') return '—';
  const num = Number(value);
  if (!Number.isFinite(num)) return '—';
  return num.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function fmtSignedMoney(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  if (n >= 0) return `+$${Math.abs(n).toFixed(2)}`;
  return `−$${Math.abs(n).toFixed(2)}`;
}

function fmtSignedPct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  if (n >= 0) return `+${n.toFixed(2)}%`;
  return `${n.toFixed(2)}%`; // negative already has its own minus
}

function fmtStatus(raw) {
  if (!raw) return '—';
  // Alpaca status can come as "OrderStatus.FILLED", "FILLED", or "filled".
  // Normalize: strip enum prefix, uppercase.
  const s = String(raw).split('.').pop().toUpperCase();
  return s;
}

function fmtSide(raw) {
  if (!raw) return '—';
  return String(raw).split('.').pop().toUpperCase();
}

function acctMoney(value) {
  const m = fmtMoney(value);
  return m === '—' ? '—' : `$${m}`;
}

/** Best-effort US equity symbol for TradingView (Alpaca paper is US-focused). */
function tvSymbol(ticker) {
  if (!ticker) return null;
  const t = String(ticker).toUpperCase().trim();
  if (!t) return null;
  return `NASDAQ:${t}`;
}

export default function TradingPanel({ ticker, companyName, refreshKey, sessionStartIso }) {
  const [account, setAccount] = useState(null);
  const [plAnalytics, setPlAnalytics] = useState(null);
  const [positions, setPositions] = useState([]);
  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  /** When true, only orders submitted since this session started are shown. */
  const [sessionOnly, setSessionOnly] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [a, pl, p, o] = await Promise.all([
        fetch(`${API_BASE}/api/account`).then((r) => r.json()),
        fetch(`${API_BASE}/api/pl_analytics`).then((r) => r.json()),
        fetch(`${API_BASE}/api/positions`).then((r) => r.json()),
        fetch(`${API_BASE}/api/orders`).then((r) => r.json()),
      ]);
      setAccount(a && !a.error ? a : null);
      if (a && a.error) setErr(a.error);
      setPlAnalytics(pl && !pl.error ? pl : null);
      setPositions(Array.isArray(p) ? p : []);
      setOrders(o && o.orders ? o.orders : []);
      if (o && o.error && !a?.error) setErr(o.error);
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  useEffect(() => {
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load]);

  const chartSym = tvSymbol(ticker);

  return (
    <div className="trading-panel">
      <div className="trading-panel-header" data-tour="trading-account">
        <div>
          <div className="trading-panel-eyebrow">Paper · Alpaca</div>
          <h2 className="trading-panel-title">Trading desk</h2>
          <p className="trading-panel-sub">
            {ticker ? (
              <>
                Watching <strong>{String(ticker).toUpperCase()}</strong>
                {companyName ? ` · ${companyName}` : ''}. Place orders from the Company tab; they execute in your{' '}
                <strong>paper</strong> account.
              </>
            ) : (
              <>Load a company on the Company tab to anchor the chart. Orders still show for your full paper account.</>
            )}
          </p>
        </div>
        {account && (
          <div className="trading-panel-account">
            <div>
              <span className="trading-acct-label">Buying power</span>
              <span className="trading-acct-val">{acctMoney(account.buying_power)}</span>
            </div>
            <div>
              <span className="trading-acct-label">Cash</span>
              <span className="trading-acct-val">{acctMoney(account.cash)}</span>
            </div>
            <div>
              <span className="trading-acct-label">Portfolio</span>
              <span className="trading-acct-val">{acctMoney(account.portfolio_value)}</span>
            </div>
          </div>
        )}
      </div>

      {err && (
        <div className="trading-panel-banner trading-panel-banner-warn" role="status">
          {err.includes('not configured') ? (
            <>
              Alpaca paper keys missing. Set <code>ALPACA_API_KEY</code>, <code>ALPACA_SECRET_KEY</code>, and{' '}
              <code>ALPACA_BASE_URL</code> in <code>.env</code> to link this tab.
            </>
          ) : (
            err
          )}
        </div>
      )}

      {plAnalytics && !plAnalytics.error && (
        <div className="pl-analytics-strip" data-tour="trading-pl">
          <div className="pl-cell">
            <span className="pl-label">Day P&amp;L</span>
            <span className={`pl-value ${plAnalytics.day_pl_abs >= 0 ? 'pl-pos' : 'pl-neg'}`}>
              {fmtSignedMoney(plAnalytics.day_pl_abs)}
              <span className="pl-pct">
                ({fmtSignedPct(plAnalytics.day_pl_pct)})
              </span>
            </span>
          </div>
          <div className="pl-cell">
            <span className="pl-label">Unrealized P&amp;L</span>
            <span className={`pl-value ${plAnalytics.total_unrealized_pl >= 0 ? 'pl-pos' : 'pl-neg'}`}>
              {fmtSignedMoney(plAnalytics.total_unrealized_pl)}
              <span className="pl-pct">
                ({fmtSignedPct(plAnalytics.total_unrealized_pct)})
              </span>
            </span>
          </div>
          <div className="pl-cell">
            <span className="pl-label">Equity</span>
            <span className="pl-value">
              $
              {plAnalytics.equity.toLocaleString('en-US', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}
            </span>
          </div>
          <div className="pl-cell">
            <span className="pl-label">Positions</span>
            <span className="pl-value">{plAnalytics.position_count}</span>
          </div>
        </div>
      )}

      <div className="trading-panel-chart-block" data-tour="trading-chart">
        <TradingViewChart symbol={chartSym} companyName={companyName} />
      </div>

      <div className="trading-panel-split" data-tour="trading-orders">
        <section className="trading-card">
          <div className="trading-card-head">
            <h3 className="trading-card-title">Open &amp; recent orders</h3>
            {sessionStartIso ? (
              <div
                className="trading-scope-toggle"
                role="tablist"
                aria-label="Order scope"
                title="Limit the orders list to this browser session, or show your full Alpaca paper history"
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={sessionOnly}
                  className={`trading-scope-btn ${sessionOnly ? 'is-active' : ''}`}
                  onClick={() => setSessionOnly(true)}
                >
                  This session
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={!sessionOnly}
                  className={`trading-scope-btn ${!sessionOnly ? 'is-active' : ''}`}
                  onClick={() => setSessionOnly(false)}
                >
                  All time
                </button>
              </div>
            ) : null}
          </div>
          {loading && <p className="trading-muted">Loading…</p>}
          {(() => {
            if (loading) return null;
            const sinceMs = sessionOnly && sessionStartIso ? Date.parse(sessionStartIso) : 0;
            const visibleOrders = sessionOnly && sinceMs
              ? orders.filter((row) => {
                  if (!row?.submitted_at) return false;
                  const t = Date.parse(row.submitted_at);
                  return Number.isFinite(t) && t >= sinceMs;
                })
              : orders;
            if (visibleOrders.length === 0) {
              return (
                <p className="trading-muted">
                  {sessionOnly
                    ? 'No orders this session — submit from Company tab.'
                    : 'No orders yet — submit from Company tab.'}
                </p>
              );
            }
            return (
              <div className="trading-table-wrap">
                <table className="trading-table">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Symbol</th>
                      <th>Side</th>
                      <th>Qty</th>
                      <th>Status</th>
                      <th>Avg</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleOrders.map((row) => (
                      <tr key={row.id}>
                        <td className="num-mono">{row.submitted_at ? row.submitted_at.slice(0, 19).replace('T', ' ') : '—'}</td>
                        <td>{row.symbol}</td>
                        <td>
                          <span
                            className={
                              String(row.side).split('.').pop().toLowerCase() === 'buy'
                                ? 'tr-side-buy'
                                : 'tr-side-sell'
                            }
                          >
                            {fmtSide(row.side)}
                          </span>
                        </td>
                        <td className="num-mono">{row.qty}</td>
                        <td>{fmtStatus(row.status)}</td>
                        <td className="num-mono">{fmtMoney(row.filled_avg_price)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })()}
        </section>

        <section className="trading-card">
          <h3 className="trading-card-title">Positions</h3>
          {loading && <p className="trading-muted">Loading…</p>}
          {!loading && positions.length === 0 && <p className="trading-muted">No open positions.</p>}
          {!loading && positions.length > 0 && (
            <div className="trading-table-wrap">
              <table className="trading-table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Qty</th>
                    <th>Entry</th>
                    <th>Value</th>
                    <th>Unrealized</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((row) => (
                    <tr key={row.ticker}>
                      <td>{row.ticker}</td>
                      <td className="num-mono">{row.qty}</td>
                      <td className="num-mono">${fmtMoney(row.avg_entry)}</td>
                      <td className="num-mono">${fmtMoney(row.market_value)}</td>
                      <td className="num-mono">
                        {(() => {
                          const n = Number(row.unrealized_pl);
                          if (!Number.isFinite(n)) return '—';
                          const sign = n >= 0 ? '+' : '−';
                          return (
                            <span className={n >= 0 ? 'pl-pos' : 'pl-neg'}>
                              {sign}${fmtMoney(Math.abs(n))}
                            </span>
                          );
                        })()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
