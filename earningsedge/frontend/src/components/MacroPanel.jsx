import React, { useMemo } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

function clip(s, max) {
  const t = String(s || '').trim();
  if (!t) return '';
  if (t.length <= max) return t;
  return `${t.slice(0, max - 1)}…`;
}

function firstSentence(s) {
  const t = String(s || '').trim();
  if (!t) return '';
  const m = t.match(/^[^.!?]+[.!?]?/);
  return (m && m[0]) ? m[0].trim() : t.slice(0, 160);
}

function seriesFmt(block) {
  if (!block || block.value == null || block.value === '') {
    return { value: '—', date: '' };
  }
  return { value: String(block.value), date: block.date ? String(block.date) : '' };
}

function cpiHeadline(block) {
  const t = block?.trend;
  if (t && typeof t === 'object') {
    if (t.yoy_label) return t.yoy_label;
    if (t.yoy_inflation_pct != null) return `YoY ${Number(t.yoy_inflation_pct).toFixed(2)}%`;
  }
  return '';
}

function signalBadgeClass(sig) {
  const s = String(sig || 'NEUTRAL').toUpperCase();
  if (s === 'TAILWIND') return 'macro-signal-badge macro-signal-TAILWIND';
  if (s === 'HEADWIND') return 'macro-signal-badge macro-signal-HEADWIND';
  return 'macro-signal-badge macro-signal-NEUTRAL';
}

function regimePillClass(reg) {
  const r = String(reg || '').toLowerCase();
  if (r === 'loose') return 'analysis-pill analysis-pill-loose';
  if (r === 'tight') return 'analysis-pill analysis-pill-tight';
  return 'analysis-pill analysis-pill-neutral';
}

function regimeLabel(reg) {
  const r = String(reg || '').toLowerCase();
  if (r === 'loose') return 'LOOSE';
  if (r === 'tight') return 'TIGHT';
  return 'NEUTRAL';
}

export default function MacroPanel({ data }) {
  const curveData = useMemo(() => {
    const raw = data?.yield_curve;
    if (!Array.isArray(raw) || raw.length === 0) return [];
    return raw.map((p) => ({
      label: p.label || `${p.tenor_years}Y`,
      yield_pct: typeof p.yield_pct === 'number' ? p.yield_pct : parseFloat(p.yield_pct),
    })).filter((r) => Number.isFinite(r.yield_pct));
  }, [data]);

  if (data == null) {
    return (
      <div className="card analysis-card">
        <h3 className="card-title macro-title-row">
          <span>MACRO</span>
          <span className="macro-signal-badge macro-signal-NEUTRAL" aria-hidden>
            …
          </span>
        </h3>
        <div className="analysis-skeleton">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="skeleton skel-line" style={{ height: 22 }} />
          ))}
        </div>
      </div>
    );
  }

  const summary = clip(data.equity_summary || '', 320);
  const sectorNote = clip(data.sector_note || '', 140);
  const sig = String(data.signal || 'NEUTRAL').toUpperCase();
  const pe = data.policy_expectations;

  const ff = seriesFmt(data.fed_funds_rate);
  const cpi = seriesFmt(data.cpi);
  const un = seriesFmt(data.unemployment_rate);
  const spr = data.yield_spread_10y2y;
  const spreadVal = spr && spr.value != null ? String(spr.value) : '';

  const policyLine = pe && typeof pe === 'object' && pe.effective_fed_funds_pct != null
    ? [
        pe.spread_eff_minus_2y_bps != null ? `${pe.spread_eff_minus_2y_bps} bps (eff. vs 2Y)` : null,
        pe.hikes_priced_25bp_equiv > 0
          ? `~${pe.hikes_priced_25bp_equiv}×25 bp hikes priced vs spot`
          : null,
        pe.cuts_priced_25bp_equiv > 0
          ? `~${pe.cuts_priced_25bp_equiv}×25 bp cuts priced vs spot`
          : null,
      ].filter(Boolean).join(' · ')
    : '';

  const interpShort = clip(firstSentence(pe?.interpretation || ''), 160);

  const yMin = curveData.length
    ? Math.floor(Math.min(...curveData.map((d) => d.yield_pct)) * 10) / 10 - 0.2
    : 0;
  const yMax = curveData.length
    ? Math.ceil(Math.max(...curveData.map((d) => d.yield_pct)) * 10) / 10 + 0.2
    : 6;

  return (
    <div className="card analysis-card card--loaded macro-panel-v2">
      <h3 className="card-title macro-title-row">
        <span>MACRO</span>
        <span className="macro-title-badges">
          <span className={regimePillClass(data.macro_regime)} title="Policy rate regime (rule-of-thumb)">
            {regimeLabel(data.macro_regime)}
          </span>
          <span className={signalBadgeClass(data.signal)} title={sig}>
            {sig}
          </span>
        </span>
      </h3>

      <div className="macro-kpi-strip">
        <div className="macro-kpi-pill" title={ff.date ? `As of ${ff.date}` : ''}>
          <span className="macro-kpi-k">Fed funds</span>
          <span className="macro-kpi-v">{ff.value}%</span>
        </div>
        <div className="macro-kpi-pill" title={cpi.date ? `As of ${cpi.date}` : ''}>
          <span className="macro-kpi-k">Inflation</span>
          <span className="macro-kpi-v">{cpiHeadline(data.cpi) || cpi.value}</span>
        </div>
        <div className="macro-kpi-pill" title={un.date ? `As of ${un.date}` : ''}>
          <span className="macro-kpi-k">Unemployment</span>
          <span className="macro-kpi-v">{un.value}%</span>
        </div>
        {spreadVal ? (
          <div className="macro-kpi-pill" title="10Y − 2Y (FRED T10Y2Y)">
            <span className="macro-kpi-k">10Y−2Y</span>
            <span className="macro-kpi-v">{spreadVal}</span>
          </div>
        ) : null}
      </div>

      <div className="macro-yield-block">
        <div className="macro-yield-title">Treasury curve (spot)</div>
        {curveData.length >= 2 ? (
          <div className="macro-yield-chart">
            <ResponsiveContainer width="100%" height={168}>
              <LineChart data={curveData} margin={{ top: 4, right: 6, left: -18, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(0, 212, 255, 0.08)" vertical={false} />
                <XAxis
                  dataKey="label"
                  tick={{ fill: 'rgba(160, 200, 220, 0.85)', fontSize: 10 }}
                  axisLine={{ stroke: 'rgba(0, 212, 255, 0.2)' }}
                  tickLine={false}
                />
                <YAxis
                  domain={[yMin, yMax]}
                  tick={{ fill: 'rgba(160, 200, 220, 0.75)', fontSize: 10 }}
                  tickFormatter={(v) => `${v}%`}
                  width={40}
                  axisLine={{ stroke: 'rgba(0, 212, 255, 0.2)' }}
                  tickLine={false}
                />
                <Tooltip
                  contentStyle={{
                    background: 'rgba(8, 16, 28, 0.96)',
                    border: '1px solid rgba(0, 212, 255, 0.25)',
                    borderRadius: 6,
                    fontSize: 11,
                  }}
                  formatter={(v) => [`${Number(v).toFixed(2)}%`, 'Yield']}
                  labelFormatter={(l) => l}
                />
                <Line
                  type="monotone"
                  dataKey="yield_pct"
                  stroke="var(--accent-cyan)"
                  strokeWidth={2}
                  dot={{ r: 4, fill: 'var(--accent-cyan)', strokeWidth: 0 }}
                  activeDot={{ r: 5 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className="macro-yield-fallback">Yield curve needs FRED Treasury series (3M, 2Y–30Y).</p>
        )}
      </div>

      {policyLine || interpShort ? (
        <div className="macro-policy-compact">
          {policyLine ? <div className="macro-policy-metrics">{policyLine}</div> : null}
          {interpShort ? <p className="macro-policy-interpret-short">{interpShort}</p> : null}
        </div>
      ) : null}

      {(summary || sectorNote) ? (
        <div className="macro-synth">
          {summary ? <p className="macro-synth-equity">{summary}</p> : null}
          {sectorNote ? <p className="macro-synth-sector">{sectorNote}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
