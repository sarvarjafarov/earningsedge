import React, { useMemo, useState } from 'react';

/**
 * TradingView embed wrapped in our own header. The chart pixels are still
 * rendered by TradingView's iframe (we have no control over candle/axis
 * styling), but the surrounding chrome — symbol pill, timeframe pills,
 * empty state — is fully ours.
 *
 * The iframe URL is rebuilt whenever `chartInterval` changes; the iframe
 * reloads to show the new interval (~1s).
 *
 * URL params we set:
 *   hide_top_toolbar=1   — kills TV's symbol+timeframe bar (we provide ours)
 *   hidesidetoolbar=1    — kills the left drawing-tools toolbar
 *   allow_symbol_change=0 — locks the symbol to whatever the app loaded
 *   saveimage=0          — hides the screenshot button
 *   theme=dark           — match the cockpit
 *   toolbarbg=0a1620     — even though toolbar is hidden, this colors the
 *                          legend strip background
 */

const INTERVALS = [
  { id: '1', label: '1m' },
  { id: '5', label: '5m' },
  { id: '15', label: '15m' },
  { id: '60', label: '1h' },
  { id: 'D', label: '1D' },
];

function buildSrc(symbol, intervalId) {
  const params = new URLSearchParams({
    symbol,
    interval: intervalId,
    hide_top_toolbar: '1',
    hidesidetoolbar: '1',
    allow_symbol_change: '0',
    saveimage: '0',
    theme: 'dark',
    style: '1',
    toolbarbg: '0a1620',
    timezone: 'exchange',
    hidelegend: '0',
  });
  return `https://www.tradingview.com/widgetembed/?${params.toString()}`;
}

export default function TradingViewChart({ symbol, companyName }) {
  const [chartInterval, setChartInterval] = useState('5');

  const displaySymbol = useMemo(() => {
    if (!symbol) return '';
    return symbol.split(':').pop().toUpperCase();
  }, [symbol]);

  if (!symbol) {
    return (
      <div className="tv-chart tv-chart-empty">
        <div className="tv-chart-empty-text">
          Load a company on the Company tab to anchor the chart to that ticker.
        </div>
      </div>
    );
  }

  const src = buildSrc(symbol, chartInterval);

  return (
    <div className="tv-chart">
      <div className="tv-chart-head">
        <div className="tv-chart-head-left">
          <span className="tv-chart-eyebrow">Chart</span>
          <span className="tv-chart-symbol">{displaySymbol}</span>
          {companyName ? (
            <span className="tv-chart-name" title={companyName}>{companyName}</span>
          ) : null}
        </div>
        <div className="tv-chart-intervals" role="tablist" aria-label="Chart interval">
          {INTERVALS.map((iv) => (
            <button
              key={iv.id}
              type="button"
              role="tab"
              aria-selected={chartInterval === iv.id}
              className={`tv-chart-interval ${chartInterval === iv.id ? 'is-active' : ''}`}
              onClick={() => setChartInterval(iv.id)}
              title={`Switch to ${iv.label} candles`}
            >
              {iv.label}
            </button>
          ))}
        </div>
      </div>
      <iframe
        // Force re-render when interval changes so the iframe actually reloads.
        key={chartInterval}
        title={`Chart ${displaySymbol} — ${chartInterval}`}
        src={src}
        className="tv-chart-iframe"
        sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox"
      />
    </div>
  );
}
