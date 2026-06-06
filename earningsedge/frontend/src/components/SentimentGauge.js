import React, { useEffect, useState } from 'react';

const DIMENSION_LABELS = {
  results_vs_expectations: 'Results vs expectations',
  forward_outlook: 'Forward outlook',
  demand_strength: 'Demand',
  supply_execution: 'Supply / execution',
  margins_profitability: 'Margins',
  customer_mix: 'Customer mix',
  competitive_position: 'Competitive',
  regulatory_geographic_risk: 'Regulatory / geo',
  management_credibility: 'Credibility',
  q_and_a_quality: 'Q&A quality',
};

const DIMENSION_ORDER = [
  'results_vs_expectations',
  'forward_outlook',
  'demand_strength',
  'supply_execution',
  'margins_profitability',
  'management_credibility',
  'customer_mix',
  'competitive_position',
  'regulatory_geographic_risk',
  'q_and_a_quality',
];

const EVIDENCE_LABELS = {
  numeric: 'Numeric',
  explicit_guidance: 'Guidance',
  qualitative_specific: 'Qualitative',
  qualitative_vague: 'Qualitative (vague)',
  inferred: 'Inferred',
};

function colorFor(score) {
  if (score <= 35) return 'var(--red)';
  if (score <= 55) return 'var(--amber)';
  return 'var(--green)';
}

function Arc({ score }) {
  const clamped = Math.max(0, Math.min(100, score));
  const radius = 72;
  const circumference = Math.PI * radius;
  const dash = (clamped / 100) * circumference;
  const color = colorFor(clamped);
  return (
    <svg width="180" height="110" viewBox="0 0 180 110">
      <path
        d="M 18 100 A 72 72 0 0 1 162 100"
        fill="none"
        stroke="var(--border)"
        strokeWidth="12"
        strokeLinecap="round"
      />
      <path
        d="M 18 100 A 72 72 0 0 1 162 100"
        fill="none"
        stroke={color}
        strokeWidth="12"
        strokeLinecap="round"
        strokeDasharray={`${dash} ${circumference}`}
        style={{ transition: 'stroke-dasharray 800ms ease, stroke 800ms ease' }}
      />
    </svg>
  );
}

function DimensionBar({ name, score }) {
  const label = DIMENSION_LABELS[name] || name;
  const clamped = Math.max(0, Math.min(100, score));
  const color = colorFor(clamped);
  return (
    <div className="dim-bar-row">
      <div className="dim-bar-label" title={label}>{label}</div>
      <div className="dim-bar-track">
        <div
          className="dim-bar-fill"
          style={{
            width: `${clamped}%`,
            background: color,
          }}
        />
      </div>
      <div className="dim-bar-score">{clamped}</div>
    </div>
  );
}

function RiskPill({ risk }) {
  if (!risk || !risk.level) return null;
  const cls = `risk-pill risk-${risk.level}`;
  const text =
    risk.level === 'high' ? 'HIGH RISK' : risk.level === 'medium' ? 'MED RISK' : 'LOW RISK';
  return (
    <span className={cls} title={risk.drivers?.join(' · ') || ''}>
      {text}
    </span>
  );
}

function DriverCard({ driver, polarity }) {
  const dim = driver.dimension || '';
  const label = DIMENSION_LABELS[dim] || dim.replace(/_/g, ' ');
  const score = typeof driver.score === 'number' ? driver.score : null;
  const delta = typeof driver.delta_from_neutral === 'number' ? driver.delta_from_neutral : score != null ? score - 50 : null;
  const sway = driver.estimated_sway_pts;
  const et = driver.evidence_type || 'inferred';
  const evLabel = EVIDENCE_LABELS[et] || et.replace(/_/g, ' ');
  const src = driver.source_label || 'Live transcript';

  return (
    <li className={`sentiment-driver-card sentiment-driver-${polarity}`}>
      <div className="sentiment-driver-top">
        <span className="sentiment-driver-dim">{label}</span>
        {score != null && (
          <span className="sentiment-driver-score" style={{ color: colorFor(score) }}>
            {score}
          </span>
        )}
      </div>
      {driver.reason && (
        <div className="sentiment-driver-reason">{driver.reason}</div>
      )}
      <div className="sentiment-driver-meta">
        {delta != null && (
          <span title="Band score vs neutral (50)">Δ vs neutral {delta > 0 ? '+' : ''}{delta}</span>
        )}
        {typeof sway === 'number' && (
          <span title="Approximate pull on the blended composite">
            · est. sway {sway > 0 ? '+' : ''}{sway} pts
          </span>
        )}
        <span className="sentiment-driver-ev"> · {evLabel}</span>
        <span className="sentiment-driver-src"> · {src}</span>
      </div>
      {driver.quote && (
        <blockquote className="sentiment-driver-quote">
          “{driver.quote}”
        </blockquote>
      )}
    </li>
  );
}

function formatMoney(x) {
  if (x == null || x === '') return '—';
  const n = Number(x);
  if (!Number.isFinite(n)) return '—';
  return n >= 100 ? n.toFixed(2) : n.toFixed(2);
}

function AnalystEnrichmentStrip({ data }) {
  if (!data || typeof data !== 'object') return null;
  const upside = data.target_upside_pct;
  const spread = data.target_spread_pct;
  const hasTargets =
    data.target_mean != null ||
    data.target_high != null ||
    data.target_low != null ||
    upside != null;

  return (
    <div className="analyst-enrichment-strip">
      {hasTargets ? (
        <div className="ae-row ae-target-row">
          <span className="ae-label">Consensus price target</span>
          <span className="ae-target-mean">
            Mean {formatMoney(data.target_mean)}
            {data.target_median != null ? ` · Med ${formatMoney(data.target_median)}` : ''}
          </span>
          <span className="ae-upside">
            {upside != null && Number.isFinite(Number(upside))
              ? `Implied vs spot ${Number(upside) >= 0 ? '+' : ''}${Number(upside).toFixed(1)}%`
              : ''}
            {spread != null && Number.isFinite(Number(spread)) ? ` · Dispersion ±${Number(spread).toFixed(1)}% of mean` : ''}
          </span>
          <span className="ae-meta">
            {data.target_count != null ? `${data.target_count} analysts` : ''}
            {data.target_last_updated
              ? `${data.target_count != null ? ' · ' : ''}as of ${data.target_last_updated}`
              : ''}
          </span>
        </div>
      ) : null}
    </div>
  );
}

export default function SentimentGauge({ sentiment, analystOpinion }) {
  const hasData = sentiment && typeof sentiment.score === 'number';
  const target = hasData ? sentiment.score : 50;
  const trend = sentiment?.trend || (hasData ? 'stable' : 'pending');
  const bullish = sentiment?.bullish_phrases || [];
  const bearish = sentiment?.bearish_phrases || [];
  const dimensionScores = sentiment?.dimension_scores || {};
  const risk = sentiment?.risk_overlay;
  const overallLabel = sentiment?.overall_label;
  const mixedCount = sentiment?.mixed_count || 0;
  const materialCount = sentiment?.material_count || 0;
  const evidenceCount = sentiment?.evidence_count ?? 0;
  const posDrivers = sentiment?.top_positive_drivers || [];
  const negDrivers = sentiment?.top_negative_drivers || [];
  const hasCallDrivers = posDrivers.length > 0 || negDrivers.length > 0;
  const opinionHasPayload =
    analystOpinion &&
    typeof analystOpinion === 'object' &&
    Object.keys(analystOpinion).length > 0;
  const analystEnrichment = sentiment?.analyst_enrichment || (opinionHasPayload ? analystOpinion : null);

  const [display, setDisplay] = useState(50);
  useEffect(() => {
    let frame;
    const start = display;
    const delta = target - start;
    if (delta === 0) return;
    const duration = 800;
    const t0 = performance.now();
    const tick = (t) => {
      const p = Math.min(1, (t - t0) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      setDisplay(Math.round(start + delta * eased));
      if (p < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  const orderedDims = DIMENSION_ORDER.filter((d) => d in dimensionScores);

  const showLiveLabel = overallLabel && materialCount > 0;

  return (
    <div className="card">
      <h3 className="card-title">
        Sentiment
        <span className="card-title-right">
          <RiskPill risk={risk} />
          <span className="badge">{trend.toUpperCase()}</span>
        </span>
      </h3>
      <div className="sentiment-wrap">
        <div className="gauge">
          <Arc score={display} />
          <div className="gauge-score" style={{ color: colorFor(display) }}>
            {display}
          </div>
        </div>

        {showLiveLabel ? (
          <div className="sentiment-label">
            {String(overallLabel).replace(/_/g, ' ')}
            {materialCount > 0 && (
              <span className="sentiment-meta">
                {materialCount} material statement{materialCount === 1 ? '' : 's'}
                {mixedCount > 0 ? ` · ${mixedCount} mixed` : ''}
              </span>
            )}
          </div>
        ) : null}

        {analystEnrichment ? <AnalystEnrichmentStrip data={analystEnrichment} /> : null}

        {orderedDims.length > 0 && (
          <div className="dim-bars">
            {orderedDims.map((name) => (
              <DimensionBar
                key={name}
                name={name}
                score={dimensionScores[name]}
              />
            ))}
          </div>
        )}

        <div className="phrases-grid">
          <div className="phrase-col bullish">
            <h4>Bullish drivers</h4>
            {!hasData ? (
              <ul>
                <li className="skeleton skel-line" style={{ height: 18 }} />
                <li className="skeleton skel-line" style={{ height: 18, width: '70%' }} />
              </ul>
            ) : hasCallDrivers ? (
              posDrivers.length > 0 ? (
                <ul className="sentiment-driver-list">
                  {posDrivers.map((d, i) => (
                    <DriverCard key={`${d.dimension}-${i}-${d.quote?.slice(0, 12)}`} driver={d} polarity="bull" />
                  ))}
                </ul>
              ) : (
                <ul>
                  <li className="sentiment-driver-empty">No bullish transcript drivers in the latest window.</li>
                </ul>
              )
            ) : evidenceCount === 0 ? (
              <ul>
                {bullish.slice(0, 4).map((p, i) => (
                  <li key={i} className="sentiment-baseline-note">{p}</li>
                ))}
                {bullish.length === 0 && (
                  <li style={{ color: 'var(--muted)', fontStyle: 'italic' }}>—</li>
                )}
              </ul>
            ) : (
              <ul>
                <li className="sentiment-driver-empty">No bullish drivers yet — scores still near neutral.</li>
              </ul>
            )}
          </div>
          <div className="phrase-col bearish">
            <h4>Bearish drivers</h4>
            {!hasData ? (
              <ul>
                <li className="skeleton skel-line" style={{ height: 18 }} />
                <li className="skeleton skel-line" style={{ height: 18, width: '60%' }} />
              </ul>
            ) : hasCallDrivers ? (
              negDrivers.length > 0 ? (
                <ul className="sentiment-driver-list">
                  {negDrivers.map((d, i) => (
                    <DriverCard key={`${d.dimension}-n-${i}-${d.quote?.slice(0, 12)}`} driver={d} polarity="bear" />
                  ))}
                </ul>
              ) : (
                <ul>
                  <li className="sentiment-driver-empty">No bearish transcript drivers in the latest window.</li>
                </ul>
              )
            ) : evidenceCount === 0 ? (
              <ul>
                {bearish.slice(0, 4).map((p, i) => (
                  <li key={i} className="sentiment-baseline-note">{p}</li>
                ))}
                {bearish.length === 0 && (
                  <li style={{ color: 'var(--muted)', fontStyle: 'italic' }}>—</li>
                )}
              </ul>
            ) : (
              <ul>
                <li className="sentiment-driver-empty">No bearish drivers yet — scores still near neutral.</li>
              </ul>
            )}
          </div>
        </div>

        {risk?.drivers?.length > 0 && (
          <div className="risk-drivers">
            <div className="risk-drivers-title">Risk drivers</div>
            <ul>
              {risk.drivers.slice(0, 3).map((d, i) => (
                <li key={i}>{d}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
