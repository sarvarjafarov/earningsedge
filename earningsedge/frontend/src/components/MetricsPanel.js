import React, { useMemo } from 'react';

function fmt(value) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null;
    if (Math.abs(value) >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
    if (Math.abs(value) >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
    return `$${value.toLocaleString()}`;
  }
  return String(value);
}

function fmtEps(value) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value.toFixed(2);
  }
  const s = String(value).trim().replace(/^\$+/, '').replace(/,/g, '');
  const n = parseFloat(s);
  if (Number.isFinite(n)) return n.toFixed(2);
  return s || null;
}

function deltaClass(reported, estimate) {
  if (reported == null || estimate == null) return 'none';
  const r = Number(reported);
  let e = Number(estimate);
  if (Number.isNaN(r)) return 'none';
  if (Number.isNaN(e)) {
    const es = String(estimate).replace(/^\$+/, '').replace(/,/g, '');
    e = parseFloat(es);
  }
  if (Number.isNaN(e)) return 'none';
  if (r > e) return 'beat';
  if (r < e) return 'miss';
  return 'none';
}

function deltaLabel(cls) {
  if (cls === 'beat') return 'Beat';
  if (cls === 'miss') return 'Miss';
  return '—';
}

function compactPeriod(consensusLabel, consensusSource) {
  if (!consensusLabel || typeof consensusLabel !== 'string') {
    return consensusSource ? String(consensusSource).split(/[·|]/)[0].trim() : '';
  }
  const m = consensusLabel.match(/fiscal\s+(Q[1-4])\s+FY(\d{2,4})/i);
  if (m) {
    const y = m[2].length === 4 ? m[2].slice(2) : m[2];
    return `${m[1]} FY${y}`;
  }
  const short = consensusLabel.replace(/^Analyst consensus\s*\([^)]*\)\s*·\s*/i, '').trim();
  return short.length > 48 ? `${short.slice(0, 46)}…` : short;
}

function formatSurprisePct(pct) {
  if (pct == null || !Number.isFinite(Number(pct))) return null;
  const n = Number(pct);
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(1)}%`;
}

export default function MetricsPanel({ metrics }) {
  const m = metrics || {};
  const revDelta = deltaClass(m.revenue_reported, m.revenue_estimate);
  const epsDelta = deltaClass(m.eps_reported, m.eps_estimate);

  const guidanceCls = m.guidance_raised === true ? 'raised' : m.guidance_raised === false ? 'lowered' : null;

  const consRev = m.revenue_estimate != null ? fmt(m.revenue_estimate) : null;
  const consEps = m.eps_estimate != null ? fmtEps(m.eps_estimate) : null;

  const repRev = m.revenue_reported != null ? fmt(m.revenue_reported) : null;
  const repEps = m.eps_reported != null ? fmtEps(m.eps_reported) : null;

  const subhead = useMemo(
    () => compactPeriod(m.consensus_period_label, m.consensus_source),
    [m.consensus_period_label, m.consensus_source],
  );

  const priorAct = m.prior_eps_actual;
  const priorEst = m.prior_eps_estimate;
  const priorSurp = m.prior_eps_surprise_pct;
  const priorPeriod = m.prior_eps_period;
  const hasPriorEps =
    priorAct != null &&
    Number.isFinite(Number(priorAct)) &&
    (priorEst != null || (priorSurp != null && Number.isFinite(Number(priorSurp))));

  return (
    <div className="card metrics-panel-clean">
      <div className="metrics-panel-head">
        <h3 className="card-title">Operational metrics</h3>
        {subhead ? <p className="metrics-panel-sub">{subhead}</p> : null}
      </div>

      <div className="metrics-grid metrics-grid-two">
        <div className="metric-card metric-card-compact">
          <div className="metric-row">
            <span className="metric-label">Revenue</span>
            {repRev != null && consRev != null ? (
              <span className={`delta ${revDelta}`}>{deltaLabel(revDelta)}</span>
            ) : (
              <span className="delta none"> </span>
            )}
          </div>
          {repRev != null ? (
            <>
              <div className="metric-value metric-kpi">{repRev}</div>
              {consRev != null ? (
                <div className="metric-kpi-meta">vs est. {consRev}</div>
              ) : null}
            </>
          ) : consRev != null ? (
            <>
              <div className="metric-value metric-kpi metric-kpi-est">{consRev}</div>
              <div className="metric-kpi-hint">Next print · from transcript when live</div>
            </>
          ) : (
            <>
              <div className="metric-value metric-empty">—</div>
              <div className="metric-kpi-hint">No consensus loaded</div>
            </>
          )}
        </div>

        <div className="metric-card metric-card-compact">
          <div className="metric-row">
            <span className="metric-label">Diluted EPS</span>
            {repEps != null && consEps != null ? (
              <span className={`delta ${epsDelta}`}>{deltaLabel(epsDelta)}</span>
            ) : (
              <span className="delta none"> </span>
            )}
          </div>
          {repEps != null ? (
            <>
              <div className="metric-value metric-kpi">${repEps}</div>
              {consEps != null ? (
                <div className="metric-kpi-meta">vs est. ${consEps}</div>
              ) : null}
            </>
          ) : consEps != null ? (
            <>
              <div className="metric-value metric-kpi metric-kpi-est">${consEps}</div>
              <div className="metric-kpi-hint">Next print · from transcript when live</div>
            </>
          ) : (
            <>
              <div className="metric-value metric-empty">—</div>
              <div className="metric-kpi-hint">No consensus loaded</div>
            </>
          )}

          {hasPriorEps ? (
            <div className="metrics-eps-prior">
              <div className="metrics-eps-prior-title">
                {priorPeriod ? `${priorPeriod} · ` : ''}
                last quarter
              </div>
              <div className="metrics-eps-prior-row">
                <span className="metrics-eps-prior-nums">
                  ${fmtEps(priorAct)} actual
                  {priorEst != null && Number.isFinite(Number(priorEst)) ? (
                    <> · ${fmtEps(priorEst)} est</>
                  ) : null}
                </span>
                {priorSurp != null && Number.isFinite(Number(priorSurp)) ? (
                  <span
                    className={`metrics-eps-surprise ${
                      Number(priorSurp) >= 0 ? 'metrics-eps-surprise-pos' : 'metrics-eps-surprise-neg'
                    }`}
                  >
                    {formatSurprisePct(priorSurp)}
                  </span>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>
      </div>

      {guidanceCls ? (
        <div className={`guidance-banner ${guidanceCls}`}>
          {guidanceCls === 'raised' ? '▲ Guidance raised' : '▼ Guidance lowered'}
          {m.guidance_note ? <span className="note">{m.guidance_note}</span> : null}
        </div>
      ) : null}
    </div>
  );
}
