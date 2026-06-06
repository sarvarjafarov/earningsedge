import React, { useState, useEffect, useRef } from 'react';
import { getApiBase, sessionHeaders } from '../apiConfig';

/** Pull the first JSON object that looks like a score_block out of the
 *  agent's prose response. The agents are instructed to emit one
 *  inside ```json fences```; sometimes they emit it inline. */
function extractScoreBlock(text) {
  if (!text) return null;
  // Try fenced JSON block first
  const fenced = /```json\s*([\s\S]*?)```/i.exec(text) || /```\s*({[\s\S]*?})\s*```/i.exec(text);
  const candidates = [];
  if (fenced && fenced[1]) candidates.push(fenced[1]);
  // Also try to find a bare {...} object that has "score" or "label"
  const bare = /\{[\s\S]{0,2000}?(?:"label"|"score"|"confidence")[\s\S]{0,2000}?\}/.exec(text);
  if (bare && bare[0]) candidates.push(bare[0]);
  for (const c of candidates) {
    try {
      const obj = JSON.parse(c);
      if (obj && (obj.label || obj.score || obj.confidence)) return obj;
    } catch (_) { /* try next */ }
  }
  return null;
}

/** Remove fenced ```json``` blocks from the prose so we don't render
 *  them twice (once as a card, once as raw text). */
function stripJsonBlocks(text) {
  return (text || '')
    .replace(/```json\s*[\s\S]*?```/gi, '')
    .replace(/```\s*\{[\s\S]*?\}\s*```/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/**
 * ChairmanADKPanel — runs the EarningsEdge Analyst Chairman LlmAgent
 * (Google Cloud Agent Builder / ADK) on a user prompt and renders the
 * synthesized response plus the tool-call trace.
 *
 * This is the hackathon-visible Agent Builder surface: the legacy
 * `/api/coverage` path uses direct `genai` calls, while this panel
 * exercises `POST /api/adk/run` which routes through ADK's
 * `InMemoryRunner` over a Gemini 3 brain composed of a root agent
 * (`earningsedge_chairman`), three sub-agents
 * (`bull_analyst`, `bear_analyst`, `quant_analyst`), and 13 tools
 * including Atlas Vector Search for prior-verdict memory.
 *
 * Auto-fires when `ticker` changes so judges loading a company see the
 * Agent Builder path execute without clicking anything.
 */
export default function ChairmanADKPanel({ ticker, autoRun = true }) {
  const API_BASE = getApiBase();
  const DEFAULT_PROMPT =
    'Quick verdict on this ticker. Pick the SINGLE most relevant ' +
    'named-investor sub-agent (Cathie Wood / Michael Burry / Druckenmiller / ' +
    'Cramer / Marks) and transfer to them once. First call ' +
    'find_similar_past_verdict to cite memory if relevant. Synthesize in ' +
    '4 sentences max. Skip remember_verdict — write that asynchronously.';

  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const lastFiredTickerRef = useRef(null);

  async function runChairman(overridePrompt) {
    const usePrompt = (overridePrompt ?? prompt).trim();
    if (!usePrompt) return;
    setRunning(true);
    setResult(null);
    setError(null);

    // Streaming: read SSE chunks so a >30s agent run survives Heroku's
    // router timeout. Each event is one JSON payload after "data: ".
    const acc = { ok: true, agent: null, model: null, response: '', tool_calls: [] };

    try {
      const r = await fetch(`${API_BASE}/api/adk/run`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...sessionHeaders(),
        },
        body: JSON.stringify({ prompt: usePrompt, ticker: ticker || undefined }),
      });
      if (!r.ok) {
        const txt = await r.text();
        try {
          const body = JSON.parse(txt);
          setError(body.error || 'agent failed');
        } catch (_) {
          setError(`HTTP ${r.status}: ${txt.slice(0, 200)}`);
        }
        return;
      }
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl;
        // SSE messages are delimited by blank lines.
        while ((nl = buf.indexOf('\n\n')) !== -1) {
          const chunk = buf.slice(0, nl);
          buf = buf.slice(nl + 2);
          if (!chunk.startsWith('data:')) continue;
          const payload = chunk.slice(5).trim();
          if (!payload) continue;
          try {
            const ev = JSON.parse(payload);
            if (ev.type === 'start') {
              acc.agent = ev.agent;
              acc.model = ev.model;
            } else if (ev.type === 'tool_call') {
              acc.tool_calls = [...acc.tool_calls, { name: ev.name, args: ev.args || {} }];
              setResult({ ...acc });  // intermediate render
            } else if (ev.type === 'final') {
              acc.response = ev.response || '';
              setResult({ ...acc });
            } else if (ev.type === 'error') {
              setError(ev.error || 'agent failed');
            }
          } catch (_) {
            // ignore parse failures (heartbeats etc.)
          }
        }
      }
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setRunning(false);
    }
  }

  // Auto-run when ticker first appears or changes. We only auto-run once per
  // ticker change so the user can refine and re-run manually after that.
  useEffect(() => {
    if (!autoRun || !ticker) return;
    if (lastFiredTickerRef.current === ticker) return;
    lastFiredTickerRef.current = ticker;
    runChairman(DEFAULT_PROMPT);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, autoRun]);

  // Don't render the panel at all when no ticker is loaded — the empty
  // textarea-and-button block was visually noisy on the hero screen.
  if (!ticker && !result && !running && !error) return null;

  return (
    <section className={`adk-panel ${running ? 'adk-panel--running' : ''}`}>
      <header>
        <div className="adk-panel__title">
          <span className="adk-panel__badge">AI AGENT</span>
          <h3>
            Analyst Chairman
            {running && (
              <span className="adk-panel__status">
                <span className="adk-panel__spinner" /> Thinking…
              </span>
            )}
          </h3>
        </div>
        <p className="adk-panel__lede">
          <strong>What this is:</strong> An AI senior analyst built on Google Cloud Agent
          Builder. It runs five named investor personas in parallel (Cathie Wood,
          Michael Burry, Druckenmiller, Cramer, Marks), consults Atlas Vector
          Search for prior verdicts, and synthesizes them into ONE call.
        </p>
      </header>

      {/* === Step 1: The question === */}
      <div className="adk-step">
        <div className="adk-step__label">
          <span className="adk-step__num">1</span> Your question
        </div>
        <textarea
          rows={2}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Ask the chairman …"
        />
        <div className="adk-panel__actions">
          <button onClick={() => runChairman()} disabled={running}>
            {running ? 'Running…' : ticker ? `Run analysis for ${ticker}` : 'Run analysis'}
          </button>
          {ticker && <span className="adk-panel__ctx">ticker: <strong>{ticker}</strong></span>}
        </div>
      </div>

      {error && (
        <div className="adk-panel__error">
          <strong>Error</strong>
          <div>{error}</div>
        </div>
      )}

      {/* === Step 2: Agent reasoning (tool calls trace) === */}
      {(running || (result && Array.isArray(result.tool_calls) && result.tool_calls.length > 0)) && (
        <div className="adk-step">
          <div className="adk-step__label">
            <span className="adk-step__num">2</span> Agent reasoning
            <span className="adk-step__hint">— what tools the agent is calling</span>
          </div>
          {result && Array.isArray(result.tool_calls) && result.tool_calls.length > 0 && (
            <ul className="adk-trace">
              {result.tool_calls.map((tc, i) => (
                <li key={i} className="adk-trace__item">
                  <span className="adk-trace__icon">⚙</span>
                  <code>{tc.name}</code>
                  {tc.args && Object.keys(tc.args).length > 0 && (
                    <span className="adk-trace__args">
                      ({Object.entries(tc.args).map(([k, v]) => `${k}=${JSON.stringify(v).slice(0, 40)}`).join(', ')})
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
          {running && (!result || !result.tool_calls || result.tool_calls.length === 0) && (
            <div className="adk-trace__empty">Agent is composing the first tool call…</div>
          )}
        </div>
      )}

      {/* === Step 3: Verdict === */}
      {result && (
        <div className="adk-step">
          <div className="adk-step__label">
            <span className="adk-step__num">3</span> Verdict
          </div>
        <div className="adk-panel__result">
          {/* Extract any structured score_block JSON from the response and
              render it as a verdict card. Falls back to plain prose if no
              JSON block is found. */}
          {(() => {
            const parsed = extractScoreBlock(result.response || '');
            if (!parsed) return null;
            const label = (parsed.label || 'Hold').toLowerCase();
            const accent =
              label.includes('buy') || label.includes('add') || label.includes('strong')
                ? 'add'
                : label.includes('sell') || label.includes('avoid') || label.includes('trim')
                ? 'avoid'
                : 'hold';
            return (
              <div className={`adk-card adk-card--${accent}`}>
                <div className="adk-card__action">{parsed.label || 'Hold'}</div>
                <div className="adk-card__metrics">
                  {typeof parsed.score === 'number' && (
                    <div className="adk-card__metric">
                      <span className="adk-card__metric-key">Score</span>
                      <span className="adk-card__metric-val">{parsed.score}</span>
                    </div>
                  )}
                  {parsed.confidence && (
                    <div className="adk-card__metric">
                      <span className="adk-card__metric-key">Confidence</span>
                      <span className="adk-card__metric-val">{String(parsed.confidence).toUpperCase()}</span>
                    </div>
                  )}
                  {Array.isArray(parsed.drivers) && (
                    <div className="adk-card__metric">
                      <span className="adk-card__metric-key">Drivers</span>
                      <span className="adk-card__metric-val">{parsed.drivers.length}</span>
                    </div>
                  )}
                </div>
                {Array.isArray(parsed.drivers) && parsed.drivers.length > 0 && (
                  <ul className="adk-card__drivers">
                    {parsed.drivers.slice(0, 4).map((d, i) => (
                      <li key={i}>
                        {typeof d === 'string'
                          ? d
                          : d.evidence || d.driver || d.description || JSON.stringify(d)}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })()}

          <div className="adk-panel__meta">
            <span><strong>Agent:</strong> {result.agent}</span>
            <span><strong>Model:</strong> {result.model}</span>
            {Array.isArray(result.tool_calls) && (
              <span><strong>Tool calls:</strong> {result.tool_calls.length}</span>
            )}
          </div>
          <div className="adk-panel__response">{stripJsonBlocks(result.response || '')}</div>
        </div>
        </div>
      )}
    </section>
  );
}
