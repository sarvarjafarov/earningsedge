import React from 'react';

const CONFIDENCE_LEVELS = { LOW: 1, MEDIUM: 2, HIGH: 3 };

export default function TradeSignal({ signal, fresh }) {
  const s = signal || {};
  const action = (s.signal || 'WAIT').toUpperCase();
  const confidence = (s.confidence || '').toUpperCase();
  const dots = CONFIDENCE_LEVELS[confidence] || 0;
  const hasSignal = signal != null;

  return (
    <div className="card">
      <h3 className="card-title">Trade Signal</h3>
      <div className="signal-wrap">
        <div className={`signal-badge ${action} ${fresh ? 'fresh' : ''}`}>{action}</div>
        <div className="confidence-row">
          <span>Confidence</span>
          <span className={`conf-dot ${dots >= 1 ? 'on' : ''}`} />
          <span className={`conf-dot ${dots >= 2 ? 'on' : ''}`} />
          <span className={`conf-dot ${dots >= 3 ? 'on' : ''}`} />
          <span>{confidence || 'PENDING'}</span>
        </div>
        {hasSignal && s.thesis ? (
          <div className="thesis">{s.thesis}</div>
        ) : !hasSignal ? (
          <>
            <div className="skeleton skel-line" style={{ width: 280, height: 12 }} />
            <div className="skeleton skel-line" style={{ width: 220, height: 12 }} />
          </>
        ) : null}
        {s.key_risk && <div className="risk">Key risk: {s.key_risk}</div>}
        <div className="disclaimer">Not financial advice. For informational purposes only.</div>
      </div>
    </div>
  );
}
