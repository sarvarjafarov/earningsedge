import React from 'react';

function isMissingValue(value) {
  if (value === null || value === undefined || value === '') return true;
  if (typeof value === 'number') return !Number.isFinite(value);
  return false;
}

function fmtRatio(value) {
  if (isMissingValue(value)) return { text: '—', missing: true, num: null };
  const num = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(num)) return { text: '—', missing: true, num: null };
  return { text: num.toFixed(2), missing: false, num };
}

function fmtPct(value) {
  if (isMissingValue(value)) return { text: '—', missing: true, pctNum: null };
  const num = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(num)) return { text: '—', missing: true, pctNum: null };
  const pctNum = Math.abs(num) < 2 ? num * 100 : num;
  return { text: `${pctNum.toFixed(1)}%`, missing: false, pctNum };
}

/**
 * One <tr> per peer: TICKER (+ YOU) | COMPANY | FWD P/E | REV GROWTH | PEG
 */
export default function CompetitorPanel({ competitors, target, peerValuation = null }) {
  const rows = (() => {
    const raw = Array.isArray(competitors) ? competitors : [];
    const targetUpper = target ? String(target).toUpperCase() : '';

    // Prefer the explicit target row if present.
    const targetRow =
      raw.find((p) => p && p.is_target === true) ||
      raw.find((p) => p && p.ticker && targetUpper && String(p.ticker).toUpperCase() === targetUpper) ||
      null;

    const targetTickerUpper = targetRow?.ticker ? String(targetRow.ticker).toUpperCase() : targetUpper;

    // Deduplicate tickers (case-insensitive), and ensure the target ticker only appears once.
    const seen = new Set();
    const out = [];
    for (const p of raw) {
      if (!p) continue;
      const t = p.ticker ? String(p.ticker) : '';
      const u = t.toUpperCase();
      if (!u) continue;
      if (targetTickerUpper && u === targetTickerUpper && targetRow && p !== targetRow) continue;
      if (seen.has(u)) continue;
      seen.add(u);
      out.push(p);
    }

    if (targetRow && targetTickerUpper) {
      const idx = out.findIndex((p) => p && p.ticker && String(p.ticker).toUpperCase() === targetTickerUpper);
      if (idx > 0) {
        const [picked] = out.splice(idx, 1);
        out.unshift(picked);
      }
    }

    return out;
  })();

  const isLoading = rows.length === 0;

  return (
    <div className="card">
      <h3 className="card-title">Peer Comparison</h3>
      {isLoading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="skeleton skel-line" style={{ height: 28 }} />
          ))}
        </div>
      ) : (
        <div className="peer-comparison-wrap">
          {peerValuation && peerValuation.score_block && (
            <div className={`peer-valuation-chip peer-valuation-${peerValuation.score_block.label}`}>
              <span className="peer-valuation-label">
                vs peers: {peerValuation.score_block.label.toUpperCase()}
              </span>
              <span className="peer-valuation-reason">
                {peerValuation.score_block.reason}
              </span>
            </div>
          )}
          <table className="peer-comparison-table" aria-label="Peer comparison">
            <thead>
              <tr>
                <th scope="col">Ticker</th>
                <th scope="col">Company</th>
                <th scope="col">Fwd P/E</th>
                <th scope="col">Rev Growth</th>
                <th scope="col">PEG</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p, i) => {
                const isTarget =
                  p.is_target === true ||
                  (target &&
                    p.ticker &&
                    p.ticker.toUpperCase() === String(target).toUpperCase());

                const pe = fmtRatio(p.pe_ratio);
                const rev = fmtPct(p.revenue_growth);

                let revClass = 'peer-td peer-td--metric peer-td--rev';
                if (rev.missing || rev.pctNum === null) {
                  revClass += ' peer-td--missing';
                } else if (rev.pctNum > 0) {
                  revClass += ' peer-td--rev-pos';
                } else if (rev.pctNum < 0) {
                  revClass += ' peer-td--rev-neg';
                }

                return (
                  <tr
                    key={p.ticker || `peer-${i}`}
                    className={p.is_target ? 'peer-row peer-row-target' : isTarget ? 'peer-row peer-row--you' : 'peer-row'}
                  >
                    <td className="peer-td peer-td--ticker">
                      <span className="peer-ticker-label">{p.ticker || '—'}</span>
                      {isTarget ? (
                        <span className="peer-you-badge" aria-label="Current company">
                          YOU
                        </span>
                      ) : null}
                    </td>
                    <td className="peer-td peer-td--company" title={p.name ? String(p.name) : undefined}>
                      {p.name || '—'}
                    </td>
                    <td
                      className={
                        pe.missing
                          ? 'peer-td peer-td--metric peer-td--missing'
                          : 'peer-td peer-td--metric'
                      }
                    >
                      {pe.text}
                    </td>
                    <td className={revClass}>{rev.text}</td>
                    <td className="peer-peg">
                      {p.peg != null
                        ? Number(p.peg).toFixed(2)
                        : <span className="peer-dash">—</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
