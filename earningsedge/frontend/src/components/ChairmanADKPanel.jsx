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
    'Give me a structured verdict on this ticker — action, confidence, ' +
    'key driver, named dissent. First call find_similar_past_verdict so the ' +
    'verdict cites any prior committee decisions on this name. Then call ' +
    'remember_verdict at the end so this decision is searchable next time.';

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
    try {
      const r = await fetch(`${API_BASE}/api/adk/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...sessionHeaders() },
        body: JSON.stringify({ prompt: usePrompt, ticker: ticker || undefined }),
      });
      const body = await r.json();
      if (!body.ok) {
        setError(body.error || 'agent failed');
      } else {
        setResult(body);
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
