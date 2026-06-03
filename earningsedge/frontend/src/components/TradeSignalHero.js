import React, { useMemo, useState } from 'react';
import OrderModal from './OrderModal';

/** Max chars per digest bullet — enough to hold full agent reasoning
 *  (e.g. "Macro: Easing monetary policy and moderating inflation generally
 *  support equity valuations, despite a slight uptick in unemployment...").
 *  CSS allows wrapping so the full text shows; we just trim outliers. */
const MAX_DIGEST_LINE = 360;

function truncateDigestLine(line) {
  if (!line) return '';
  const s = String(line).trim();
  if (s.length <= MAX_DIGEST_LINE) return s;
  // Cut at nearest word boundary before MAX_DIGEST_LINE
  const cutAt = s.lastIndexOf(' ', MAX_DIGEST_LINE);
  const safe = cutAt > 200 ? cutAt : MAX_DIGEST_LINE;
  return s.slice(0, safe) + '…';
}

function digestItems(signal) {
  const s = signal || {};
  const lines = s.digest_lines;
  if (Array.isArray(lines) && lines.length) {
    return lines
      .map((x) => truncateDigestLine(x))
      .filter(Boolean);
  }
  const raw = (s.synthesis_digest && String(s.synthesis_digest).trim()) || '';
  if (!raw) return [];
  return raw
    .split('\n')
    .map((x) => truncateDigestLine(x.trim()))
    .filter(Boolean)
    .slice(0, 8);
}

export default function TradeSignalHero({
  signal,
  fresh,
  elapsedSeconds,
  livePrice,
  ticker,
  onOrderSuccess,
}) {
  const s = signal || {};
  const action = (s.signal || 'WAIT').toUpperCase();
  const confidence = (s.confidence || '').toUpperCase();
  const confLabel = confidence || 'PENDING';
  const hasSignal = signal != null;
  const digestList = digestItems(signal);
  const price = Number.isFinite(Number(livePrice)) ? Number(livePrice) : null;

  const tickerUpper = useMemo(() => (ticker ? String(ticker).toUpperCase() : ''), [ticker]);
  /* Paper preview: 1 share at the last polled quote (Alpha Vantage / price_tick). User confirms in modal. */
  const entryPrice = price;
  const stopLoss = null;
  const takeProfit = null;
  const orderQty = action !== 'WAIT' && tickerUpper && price != null && price > 0 ? 1 : 0;

  const canOrder = action !== 'WAIT' && !!tickerUpper && orderQty > 0;

  const [orderOpen, setOrderOpen] = useState(false);
  const [orderSide, setOrderSide] = useState('BUY');
  const [orderVerb, setOrderVerb] = useState('BUY');

  const openOrder = (sideUpper, verbLabel) => {
    setOrderSide(sideUpper);
    setOrderVerb(verbLabel || (sideUpper === 'SELL' ? 'SHORT' : 'BUY'));
    setOrderOpen(true);
  };

  return (
    <div className={`trade-hero trade-hero-${action}${fresh ? ' trade-hero-fresh' : ''}`}>
      <div className="trade-hero-left">
        <div className="trade-hero-eyebrow">
          <span className="trade-hero-eyebrow-text">
            Final synthesis · trade signal
            {s.synthesis_mode === 'precall' ? (
              <span className="trade-hero-mode-chip" title="Refines automatically when you stream a live call">
                {' '}
                · pre-call
              </span>
            ) : null}
          </span>
          <span
            className={`conf-pill conf-${confLabel}`}
            aria-label={`Confidence ${confLabel}`}
            title={`Committee confidence: ${confLabel}`}
          >
            {confLabel}
          </span>
        </div>
        <div className="trade-hero-action-row">
          <div className="trade-hero-action">{action}</div>
          {price != null && (
            <div className="trade-hero-live-price" title="Alpha Vantage GLOBAL_QUOTE, polled every 60s">
              <span className="trade-hero-live-dot" />
              ${price.toFixed(2)}
              <span className="trade-hero-live-label">●60s</span>
            </div>
          )}
        </div>

        {action !== 'WAIT' && (
          <div className="trade-hero-order-controls">
            <button
              type="button"
              className="btn btn-order btn-order-buy"
              onClick={() => openOrder('BUY', 'BUY')}
              disabled={!canOrder}
              title={
                canOrder
                  ? 'Submit a PAPER order via Alpaca (confirmation required; default 1 share at last quote)'
                  : 'Need a ticker and a live quote — load company coverage and wait for the price tick'
              }
            >
              BUY {tickerUpper}
            </button>
            <button
              type="button"
              className="btn btn-order btn-order-short"
              onClick={() => openOrder('SELL', 'SHORT')}
              disabled={!canOrder}
              title={
                canOrder
                  ? 'Paper sell / short: submits SELL to Alpaca (margin rules apply in your paper account)'
                  : 'Need a ticker and a live quote — load company coverage and wait for the price tick'
              }
            >
              SHORT {tickerUpper}
            </button>
          </div>
        )}

        {action !== 'WAIT' && (
          <div
            className="trade-hero-paper-note"
            role="note"
            title="Orders submit to your Alpaca paper account — no real money"
          >
            <span className="trade-hero-paper-dot" aria-hidden="true" />
            Paper trading · no real money
          </div>
        )}
      </div>
      <div className="trade-hero-mid">
        <div className="trade-hero-mid-label">How we got here</div>
        {digestList.length ? (
          <ul className="trade-hero-digest-list">
            {digestList.map((line, i) => (
              <li key={i} className="trade-hero-digest-li">
                {line}
              </li>
            ))}
          </ul>
        ) : (
          <div className="trade-hero-digest trade-hero-placeholder">
            {hasSignal
              ? 'Digest will populate on the next refresh from agents.'
              : 'Synthesizing macro, peers, and technicals — usually under a minute after coverage loads.'}
          </div>
        )}
      </div>
      <div className="trade-hero-right">
        <div className="trade-hero-right-label">
          Thesis
          <span className="trade-hero-scale-hint" title="Score scale: 0–30 bearish · 30–70 mixed · 70–100 bullish">
            score 0–100
          </span>
        </div>
        {hasSignal && s.thesis ? (
          <div className="trade-hero-thesis">{s.thesis}</div>
        ) : (
          <div className="trade-hero-thesis trade-hero-placeholder">
            Pre-call view uses your loaded dashboards; optional live stream adds transcript-level sentiment and fact-checks.
          </div>
        )}
        {s.key_risk && (
          <div className="trade-hero-risk">
            <span className="trade-hero-risk-label">Key risk</span>
            <div className="trade-hero-risk-text">{s.key_risk}</div>
            <div className="trade-hero-risk-hint">
              The "weakest supporter" tells you which agent had the lowest conviction in this trade.
              A score below 50 there means that input is leaning against the verdict.
            </div>
          </div>
        )}
        <div className="trade-hero-disclaimer">
          Not financial advice. For informational purposes only.
        </div>
      </div>

      <OrderModal
        open={orderOpen}
        onClose={() => setOrderOpen(false)}
        ticker={tickerUpper}
        side={orderSide}
        displayVerb={orderVerb}
        qty={orderQty}
        entryPrice={entryPrice}
        stopLoss={stopLoss}
        takeProfit={takeProfit}
        onOrderSuccess={onOrderSuccess}
      />
    </div>
  );
}
