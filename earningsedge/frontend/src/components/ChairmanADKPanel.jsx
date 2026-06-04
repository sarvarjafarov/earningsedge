import React, { useState, useEffect, useRef } from 'react';
import { getApiBase, sessionHeaders } from '../apiConfig';

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
          <span className="adk-panel__badge">ADK</span>
          <h3>
            Analyst Chairman{' '}
            <span className="adk-panel__sub">
              via Google Cloud Agent Builder · Gemini 3.5 Flash
            </span>
          </h3>
          {running && (
            <span className="adk-panel__status">
              <span className="adk-panel__spinner" /> running on Gemini 3 …
            </span>
          )}
        </div>
        <p className="adk-panel__lede">
          A root <code>LlmAgent</code> with <strong>13 tools</strong> and{' '}
          <strong>5 sub-agents</strong>{' '}
          (<em>Bull</em>, <em>Bear</em>, <em>Quant</em>, <em>News</em>,{' '}
          <em>Macro</em>). Memory backed by MongoDB Atlas Vector Search;
          persistence by the partner MongoDB MCP server.
        </p>
      </header>

      <textarea
        rows={3}
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        placeholder="Ask the chairman …"
      />

      <div className="adk-panel__actions">
        <button onClick={() => runChairman()} disabled={running}>
          {running ? 'Running …' : ticker ? `Run for ${ticker}` : 'Run'}
        </button>
        {ticker && <span className="adk-panel__ctx">ticker context: {ticker}</span>}
      </div>

      {error && (
        <div className="adk-panel__error">
          <strong>Error</strong>
          <div>{error}</div>
        </div>
      )}

      {result && (
        <div className="adk-panel__result">
          <div className="adk-panel__meta">
            <span><strong>Agent:</strong> {result.agent}</span>
            <span><strong>Model:</strong> {result.model}</span>
            {Array.isArray(result.tool_calls) && (
              <span><strong>Tool calls:</strong> {result.tool_calls.length}</span>
            )}
          </div>
          <div className="adk-panel__response">{result.response}</div>
          {Array.isArray(result.tool_calls) && result.tool_calls.length > 0 && (
            <details className="adk-panel__trace" open={running === false && result.tool_calls.length <= 5}>
              <summary>Tool-call trace ({result.tool_calls.length})</summary>
              <ol>
                {result.tool_calls.map((tc, i) => (
                  <li key={i}>
                    <code>{tc.name}</code>(
                    <code>{JSON.stringify(tc.args || {})}</code>)
                  </li>
                ))}
              </ol>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
