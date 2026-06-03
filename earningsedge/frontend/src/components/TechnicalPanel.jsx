import React from 'react';

/**
 * Plain-language scorecard for retail users. Replaces the previous gauge +
 * raw-number layout. Each row shows a metric, the value (rounded sensibly),
 * a one-line interpretation, and a color chip (BULLISH / BEARISH / NEUTRAL /
 * CAUTION). A short paragraph at the bottom ties it all together.
 */

function fmtPrice(x) {
  if (x == null || x === '') return '—';
  const n = Number(x);
  if (!Number.isFinite(n)) return '—';
  return `$${n.toFixed(2)}`;
}

function fmtRsi(rsi) {
  const n = Number(rsi);
  if (!Number.isFinite(n)) return '—';
  return n.toFixed(1);
}

function fmtMacd(h) {
  const n = Number(h);
  if (!Number.isFinite(n)) return '—';
  const sign = n > 0 ? '+' : n < 0 ? '−' : '';
  return `${sign}${Math.abs(n).toFixed(2)}`;
}

function rsiVerdict(rsi) {
  const n = Number(rsi);
  if (!Number.isFinite(n)) return { label: 'No data', tone: 'neutral', detail: '' };
  if (n >= 70) {
    return {
      label: 'Overbought',
      tone: 'caution',
      detail: 'Above 70 — momentum is hot, but pullback risk rises.',
    };
  }
  if (n <= 30) {
    return {
      label: 'Oversold',
      tone: 'opportunity',
      detail: 'Below 30 — selling looks exhausted; bounces often follow.',
    };
  }
  if (n >= 55) return { label: 'Bullish lean', tone: 'bullish', detail: 'Healthy momentum without being stretched.' };
  if (n <= 45) return { label: 'Bearish lean', tone: 'bearish', detail: 'Momentum tilts negative; not yet oversold.' };
  return { label: 'Neutral', tone: 'neutral', detail: 'Momentum is balanced (30–70 range).' };
}

function macdVerdict(hist) {
  const n = Number(hist);
  if (!Number.isFinite(n)) return { label: 'No data', tone: 'neutral', detail: '' };
  if (n > 0.5) return { label: 'Strong bullish', tone: 'bullish', detail: 'Histogram clearly positive — uptrend has fuel.' };
  if (n > 0) return { label: 'Bullish', tone: 'bullish', detail: 'Slight positive — short-term momentum favors buyers.' };
  if (n < -0.5) return { label: 'Strong bearish', tone: 'bearish', detail: 'Histogram clearly negative — downtrend has fuel.' };
  if (n < 0) return { label: 'Bearish', tone: 'bearish', detail: 'Slight negative — short-term momentum favors sellers.' };
  return { label: 'Flat', tone: 'neutral', detail: 'Momentum at the crossover point.' };
}

function smaVerdict(s50, s200, price) {
  const a = Number(s50);
  const b = Number(s200);
  const p = Number(price);
  if (!Number.isFinite(a) || !Number.isFinite(b)) {
    return { label: 'No data', tone: 'neutral', detail: '' };
  }
  const hasPrice = Number.isFinite(p);
  const above50 = hasPrice ? p > a : null;
  const above200 = hasPrice ? p > b : null;
  if (a > b) {
    if (!hasPrice) return { label: 'Bullish trend', tone: 'bullish', detail: '50-day MA above 200-day. Long-term trend favors buyers.' };
    if (above50 === true && above200 === true) return { label: 'Strong uptrend', tone: 'bullish', detail: 'Price above both moving averages, with 50-day above 200-day. Classic bull setup.' };
    if (above200 === true) return { label: 'Bullish trend', tone: 'bullish', detail: '50-day MA above 200-day. Long-term trend favors buyers.' };
    return { label: 'Bullish trend (price below)', tone: 'caution', detail: '50-day above 200-day, but price has dropped below. Pullback within an uptrend.' };
  }
  if (a < b) {
    if (!hasPrice) return { label: 'Bearish trend', tone: 'bearish', detail: '50-day MA below 200-day. Long-term trend favors sellers.' };
    if (above50 === false && above200 === false) return { label: 'Strong downtrend', tone: 'bearish', detail: 'Price below both moving averages, with 50-day below 200-day. Classic bear setup.' };
    if (above200 === false) return { label: 'Bearish trend', tone: 'bearish', detail: '50-day MA below 200-day. Long-term trend favors sellers.' };
    return { label: 'Bearish trend (price above)', tone: 'caution', detail: '50-day below 200-day, but price has rallied above. Bounce within a downtrend.' };
  }
  return { label: 'Indecisive', tone: 'neutral', detail: 'Moving averages are flat against each other.' };
}

function trendVerdict(trend) {
  const u = String(trend || '').toUpperCase();
  if (u === 'UPTREND') return { label: 'Uptrend', tone: 'bullish', detail: 'Higher highs and higher lows over the lookback window.' };
  if (u === 'DOWNTREND') return { label: 'Downtrend', tone: 'bearish', detail: 'Lower highs and lower lows over the lookback window.' };
  return { label: 'Mixed', tone: 'neutral', detail: 'No clear direction — sideways price action.' };
}

function overallTone(s) {
  const u = String(s || '').toUpperCase();
  if (u === 'BULLISH') return 'bullish';
  if (u === 'BEARISH') return 'bearish';
  return 'neutral';
}

function ToneChip({ tone, children }) {
  return <span className={`tech-chip tech-chip-${tone}`}>{children}</span>;
}

function ScoreRow({ label, value, verdict }) {
  return (
    <div className="tech-row">
      <div className="tech-row-head">
        <span className="tech-row-label">{label}</span>
        <span className="tech-row-value">{value}</span>
        <ToneChip tone={verdict.tone}>{verdict.label}</ToneChip>
      </div>
      {verdict.detail ? <div className="tech-row-detail">{verdict.detail}</div> : null}
    </div>
  );
}

export default function TechnicalPanel({ data }) {
  if (data == null) {
    return (
      <div className="card analysis-card">
        <h3 className="card-title">
          <span>TECHNICAL</span>
          <span className="analysis-dot analysis-dot-muted" />
        </h3>
        <div className="analysis-skeleton">
          <div className="skeleton skel-line" style={{ height: 120 }} />
        </div>
      </div>
    );
  }

  const overall = (data.overall_signal && String(data.overall_signal).trim()) || 'NEUTRAL';
  const bestPrice =
    (data && Number.isFinite(Number(data.current_price)) ? Number(data.current_price) : null) ??
    (data && Number.isFinite(Number(data.last_close)) ? Number(data.last_close) : null) ??
    (data && Number.isFinite(Number(data.close)) ? Number(data.close) : null);

  const rsiV = rsiVerdict(data.rsi);
  const macdV = macdVerdict(data.macd_hist);
  const smaV = smaVerdict(data.sma_50, data.sma_200, bestPrice);
  const trendV = trendVerdict(data.trend);

  const summary = (data.one_line_summary && String(data.one_line_summary).trim()) || '';

  return (
    <div className="card analysis-card card--loaded">
      <h3 className="card-title">
        <span>TECHNICAL</span>
        <ToneChip tone={overallTone(overall)}>{overall}</ToneChip>
      </h3>

      <div className="tech-rows">
        <ScoreRow label="Trend" value={String(data.trend || 'MIXED').toUpperCase()} verdict={trendV} />
        <ScoreRow label="Momentum (RSI 14)" value={fmtRsi(data.rsi)} verdict={rsiV} />
        <ScoreRow label="Short-term momentum (MACD)" value={fmtMacd(data.macd_hist)} verdict={macdV} />
        <ScoreRow
          label="50-day vs 200-day MA"
          value={`${fmtPrice(data.sma_50)} / ${fmtPrice(data.sma_200)}`}
          verdict={smaV}
        />
      </div>

      <div className="tech-legend">
        <span className="tech-legend-item">
          <span className="tech-chip tech-chip-bullish">Bullish</span> momentum favors buyers
        </span>
        <span className="tech-legend-item">
          <span className="tech-chip tech-chip-bearish">Bearish</span> momentum favors sellers
        </span>
        <span className="tech-legend-item">
          <span className="tech-chip tech-chip-caution">Caution</span> stretched / mixed signal
        </span>
      </div>

      {summary && <p className="tech-summary">{summary}</p>}
    </div>
  );
}
