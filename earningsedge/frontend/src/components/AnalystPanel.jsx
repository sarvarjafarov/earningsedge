import React from 'react';

function fmtMoney(x) {
  if (x == null || x === '') return null;
  const n = Number(x);
  if (!Number.isFinite(n)) return null;
  const frac = Math.abs(n % 1) > 1e-9;
  return `$${n.toLocaleString(undefined, {
    minimumFractionDigits: frac ? 2 : 0,
    maximumFractionDigits: 2,
  })}`;
}

function fmtTargetDate(raw) {
  if (raw == null || raw === '') return null;
  const s = String(raw).trim();
  const iso = /^(\d{4}-\d{2}-\d{2})/.exec(s);
  if (iso) return iso[1];
  const d = new Date(s);
  if (!Number.isNaN(d.getTime())) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }
  return s.slice(0, 10);
}

function consensusScoreTone(label) {
  const u = String(label || '').toLowerCase();
  if (u === 'bullish') return 'bull';
  if (u === 'bearish') return 'bear';
  return 'neu';
}

function upsideTone(pct) {
  if (pct == null || Number.isNaN(Number(pct))) return 'muted';
  const n = Number(pct);
  if (Math.abs(n) < 3) return 'flat';
  if (n > 0) return 'up';
  return 'down';
}

function upsideBadgeText(pct) {
  if (pct == null || Number.isNaN(Number(pct))) return null;
  const n = Number(pct);
  if (Math.abs(n) < 3) return 'Near target';
  const abs = Math.abs(n).toFixed(1);
  if (n > 0) return `+${abs}% upside`;
  return `-${abs}% downside`;
}

/** Generate a one-sentence rationale for why analysts disagree. Heuristic-
 *  driven so it always says SOMETHING useful even when the backend doesn't
 *  ship a structured reason. Driven by spread + range + analyst count. */
function disagreementRationale({ low, high, mean, spreadPct, analysts }) {
  const lowN = Number(low);
  const highN = Number(high);
  const meanN = Number(mean);
  const reasons = [];

  if (Number.isFinite(lowN) && Number.isFinite(highN) && lowN > 0) {
    const ratio = highN / lowN;
    if (ratio >= 2.5) {
      reasons.push(`bull case is ${ratio.toFixed(1)}× the bear case`);
    } else if (ratio >= 1.6) {
      reasons.push(`high target is ${(ratio - 1).toFixed(1)}× larger than the low`);
    }
  }

  if (Number.isFinite(meanN) && Number.isFinite(lowN) && meanN > 0) {
    const downsidePct = ((meanN - lowN) / meanN) * 100;
    if (downsidePct > 40) {
      reasons.push(`bears see ${downsidePct.toFixed(0)}% downside vs the mean`);
    }
  }

  if (Number.isFinite(Number(spreadPct)) && Number(spreadPct) > 50) {
    reasons.push('dispersion is roughly half the mean target');
  }

  if (Number.isFinite(Number(analysts)) && Number(analysts) >= 30) {
    reasons.push(`${analysts} analysts cover the name, with split conviction`);
  }

  if (reasons.length === 0) {
    return 'Range of published targets is wide — analysts disagree on the next 12 months.';
  }
  // Pick the first 1–2 most informative reasons; capitalize.
  const picked = reasons.slice(0, 2).join(', ');
  return `${picked.charAt(0).toUpperCase()}${picked.slice(1)}.`;
}

export default function AnalystPanel({ opinion = null, opinionError = null }) {
  if (opinion == null) {
    return (
      <div className="card analysis-card analyst-panel analyst-panel-empty">
        <h3 className="card-title">
          <span>ANALYST CONSENSUS</span>
          <span className="analysis-dot analysis-dot-muted" aria-hidden />
        </h3>
        <p className="analysis-muted-sm analyst-panel-loading-msg">Analyst consensus: loading…</p>
      </div>
    );
  }

  const o = opinion && typeof opinion === 'object' ? opinion : {};

  const sb = Number(o.strong_buy);
  const buy = Number(o.buy);
  const hold = Number(o.hold);
  const sell = Number(o.sell);
  const ss = Number(o.strong_sell);
  const buyTotal = (Number.isFinite(sb) ? sb : 0) + (Number.isFinite(buy) ? buy : 0);
  const sellTotal = (Number.isFinite(sell) ? sell : 0) + (Number.isFinite(ss) ? ss : 0);
  const holdN = Number.isFinite(hold) ? hold : 0;
  const analysts = o.total_analysts;
  const analystsN = analysts != null && Number.isFinite(Number(analysts)) ? Number(analysts) : null;

  const hasRatingCounts =
    buyTotal > 0 || holdN > 0 || sellTotal > 0 || (analystsN != null && analystsN > 0);
  const hasBaseline = o.baseline_score != null && Number.isFinite(Number(o.baseline_score));
  const hasPriceOnly =
    o.target_mean != null && o.target_mean !== '' && Number.isFinite(Number(o.target_mean));
  const showConsensus = hasBaseline || hasRatingCounts || hasPriceOnly;

  const labelRaw = String(o.label || '').trim();
  const labelDisp = labelRaw ? labelRaw.toUpperCase() : 'CONSENSUS';
  const scoreNum = Number(o.baseline_score);
  const headline =
    hasBaseline && Number.isFinite(scoreNum)
      ? `${labelDisp} ${Math.round(scoreNum)}/100`
      : labelDisp;
  const scoreTone = hasBaseline ? consensusScoreTone(o.label) : 'neu';

  const countsParts = [];
  if (buyTotal > 0) countsParts.push(`${buyTotal} buy`);
  if (holdN > 0) countsParts.push(`${holdN} hold`);
  if (sellTotal > 0) countsParts.push(`${sellTotal} sell`);
  if (analystsN != null && analystsN > 0) countsParts.push(`${analystsN} analysts`);

  const mean = o.target_mean;
  const showPriceBlock =
    mean != null && mean !== '' && Number.isFinite(Number(mean));

  const lowStr = fmtMoney(o.target_low);
  const highStr = fmtMoney(o.target_high);
  const showRange = Boolean(lowStr && highStr);

  const upside = o.target_upside_pct;
  const spread = o.target_spread_pct;

  // "Wide analyst disagreement" should require BOTH a wide price-target
  // spread AND split ratings. A 22 buy / 9 hold / 1 sell distribution is
  // ~69% bullish — a clear majority — even if price targets span 33%
  // of the mean. Flagging that as "disagreement" misled the reviewer:
  // the firms agree on direction (BUY), they just disagree on magnitude.
  // We now ALSO require rating concentration < 60% (no clear majority
  // direction) before showing the warning.
  const totalRatings = buyTotal + holdN + sellTotal;
  const ratingConcentration =
    totalRatings > 0
      ? Math.max(buyTotal, holdN, sellTotal) / totalRatings
      : null;
  const ratingsAreSplit =
    ratingConcentration == null || ratingConcentration < 0.6;
  const wideSpread =
    spread != null
    && Number.isFinite(Number(spread))
    && Number(spread) > 30
    && ratingsAreSplit;

  const updated = fmtTargetDate(o.target_last_updated);

  return (
    <div
      className={`card analysis-card analyst-panel ${showConsensus ? 'card--loaded' : ''} ${!showConsensus ? 'analyst-panel-empty' : ''}`}
    >
      <h3 className="card-title">
        <span>ANALYST CONSENSUS</span>
        <span className="analysis-dot analysis-dot-muted" aria-hidden />
      </h3>

      {!showConsensus ? (
        <div className="analysis-muted-sm analyst-panel-empty analyst-panel-empty-hint">
          {opinionError ? (
            <p className="analyst-panel-empty-lead analyst-panel-error-detail">{opinionError}</p>
          ) : (
            <p className="analyst-panel-empty-lead">
              No analyst coverage available for this ticker. Small-cap and non-US tickers often lack
              published analyst data.
            </p>
          )}
        </div>
      ) : (
        <>
          <div className="analyst-consensus">
            <span className="analysis-row-label">Consensus rating</span>
            <div data-score-tone={scoreTone} title={o.period ? String(o.period) : undefined}>
              {headline}
            </div>
            {countsParts.length > 0 ? (
              <p className="analysis-muted-sm">{countsParts.join(' · ')}</p>
            ) : null}
          </div>

          {showPriceBlock ? (
            <div className="analyst-target">
              <div className="macro-yield-title">Price target</div>
              <div>
                <div className="analyst-price-main">
                  <div className="analysis-muted-sm">
                    Mean target:{' '}
                    <span className="num-mono analyst-target-mean-val">{fmtMoney(mean)}</span>
                  </div>
                  {showRange ? (
                    <div className="analysis-muted-sm analyst-target-range">Range: {lowStr} – {highStr}</div>
                  ) : null}
                </div>
                {upside != null && Number.isFinite(Number(upside)) ? (
                  <span
                    className="analyst-target-upside"
                    data-tone={upsideTone(upside)}
                    title={spread != null ? `Dispersion ±${Number(spread).toFixed(1)}% of mean` : undefined}
                  >
                    {upsideBadgeText(upside)}
                  </span>
                ) : null}
              </div>
              {wideSpread ? (
                <div className="analyst-target-spread-warning">
                  <div className="analyst-target-spread-label">Wide analyst disagreement</div>
                  <div className="analyst-target-spread-rationale">
                    {disagreementRationale({
                      low: o.target_low,
                      high: o.target_high,
                      mean: o.target_mean,
                      spreadPct: spread,
                      analysts: analystsN,
                    })}
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}

          {updated ? (
            <p className="analysis-muted-sm analyst-panel-footer">Price target: {updated}</p>
          ) : null}
          {o.source === 'yfinance' && (
            <div className="analyst-panel-source">via Yahoo Finance</div>
          )}
          {o.source === 'hybrid' && (
            <div className="analyst-panel-source">via Finnhub + Yahoo Finance</div>
          )}
        </>
      )}
    </div>
  );
}
