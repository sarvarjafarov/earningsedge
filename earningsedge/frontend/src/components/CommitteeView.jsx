import React from 'react';

/**
 * CommitteeView — displays the weighted committee breakdown.
 *
 * Props:
 *   tradeSignal: the full trade_signal WebSocket data dict. Expected fields:
 *     - committee_score (int 0-100)
 *     - signal (BUY / HOLD / SELL)
 *     - confidence (LOW / MEDIUM / HIGH)
 *     - mode (coverage / live)
 *     - votes[]: list of { name, score, label, confidence, reason,
 *                           base_weight, effective_weight, effective_weight_pct }
 *     - missing_specialists[]: list of names that did not report
 *     - disagreement_spread (int)
 *   variant: "compact" (for trade hero) or "full" (mid-dashboard)
 */

const SPECIALIST_LABELS = {
  analyst: 'Analyst Consensus',
  news: 'News Flow',
  macro: 'Macro Backdrop',
  peer: 'Peer Valuation',
  technical: 'Technicals',
  metrics: 'Live Metrics',
  sentiment: 'Call Sentiment',
};

function labelClass(label) {
  if (label === 'bullish') return 'vote-bullish';
  if (label === 'bearish') return 'vote-bearish';
  return 'vote-neutral';
}

function confidenceClass(conf) {
  if (conf === 'HIGH') return 'conf-high';
  if (conf === 'LOW') return 'conf-low';
  return 'conf-med';
}

function SpreadBadge({ spread }) {
  if (spread == null) return null;
  if (spread >= 30) {
    return (
      <span className="committee-spread committee-spread-wide">
        Committee split · {spread}-pt spread
      </span>
    );
  }
  if (spread <= 10) {
    return (
      <span className="committee-spread committee-spread-tight">
        Committee agrees
      </span>
    );
  }
  return (
    <span className="committee-spread committee-spread-mixed">
      {spread}-pt spread
    </span>
  );
}

function CompactVoteBar({ vote }) {
  const pct = vote.effective_weight_pct || 0;
  return (
    <div className="committee-compact-row" title={vote.reason}>
      <span className="committee-compact-name">
        {SPECIALIST_LABELS[vote.name] || vote.name}
      </span>
      <div className="committee-compact-bar-track">
        <div
          className={`committee-compact-bar ${labelClass(vote.label)}`}
          style={{ width: `${vote.score}%` }}
        />
        <span className="committee-compact-score">{vote.score}</span>
      </div>
      <span className="committee-compact-weight">{pct.toFixed(0)}%</span>
    </div>
  );
}

function FullVoteRow({ vote }) {
  const pct = vote.effective_weight_pct || 0;
  const basePct = ((vote.base_weight || 0) * 100).toFixed(0);
  const weightChanged = Math.abs(pct - parseFloat(basePct)) > 0.5;

  return (
    <div className="committee-full-row">
      <div className="committee-full-header">
        <span className="committee-full-name">
          {SPECIALIST_LABELS[vote.name] || vote.name}
        </span>
        <span className={`committee-conf ${confidenceClass(vote.confidence)}`}>
          {vote.confidence}
        </span>
        <span className={`committee-full-label ${labelClass(vote.label)}`}>
          {vote.label}
        </span>
        <span className="committee-full-weight">
          {pct.toFixed(0)}% wt
          {weightChanged && (
            <span className="committee-full-weight-base"> (base {basePct}%)</span>
          )}
        </span>
      </div>
      <div className="committee-full-bar-track">
        <div
          className={`committee-full-bar ${labelClass(vote.label)}`}
          style={{ width: `${vote.score}%` }}
        />
        <span className="committee-full-score">{vote.score}/100</span>
      </div>
      <div className="committee-full-reason">{vote.reason}</div>
    </div>
  );
}

export default function CommitteeView({ tradeSignal, variant = 'full' }) {
  if (!tradeSignal || !Array.isArray(tradeSignal.votes)) {
    return (
      <div className={`committee-view committee-${variant} committee-empty`}>
        <span className="committee-empty-text">
          Committee view awaiting data…
        </span>
      </div>
    );
  }

  const votes = tradeSignal.votes || [];
  const missing = tradeSignal.missing_specialists || [];
  const spread = tradeSignal.disagreement_spread;
  const modeLabel = tradeSignal.mode === 'live' ? 'Live' : 'Pre-call';
  const cScore = tradeSignal.committee_score ?? '—';

  // Sort votes by effective weight descending so the most influential
  // specialists render first.
  const sortedVotes = [...votes].sort(
    (a, b) => (b.effective_weight_pct || 0) - (a.effective_weight_pct || 0)
  );

  if (variant === 'compact') {
    return (
      <div className="committee-view committee-compact">
        <div className="committee-compact-header">
          <span className="committee-compact-title">Committee ({modeLabel})</span>
          <span className="committee-compact-score-large">{cScore}/100</span>
          <SpreadBadge spread={spread} />
        </div>
        <div className="committee-compact-rows">
          {sortedVotes.map((v) => (
            <CompactVoteBar key={v.name} vote={v} />
          ))}
        </div>
      </div>
    );
  }

  // variant === 'full'
  return (
    <div className="committee-view committee-full card analysis-card">
      <h3 className="card-title">
        <span>Committee · {modeLabel}</span>
        <span className="committee-full-header-score">{cScore}/100</span>
        <SpreadBadge spread={spread} />
      </h3>
      <div className="committee-full-rows">
        {sortedVotes.map((v) => (
          <FullVoteRow key={v.name} vote={v} />
        ))}
      </div>
      {missing.length > 0 && (
        <div className="committee-missing">
          Not reporting:{' '}
          {missing.map((n) => SPECIALIST_LABELS[n] || n).join(', ')}
        </div>
      )}
    </div>
  );
}

