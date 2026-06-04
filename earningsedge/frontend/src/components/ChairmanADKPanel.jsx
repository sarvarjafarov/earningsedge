import React, { useState } from 'react';
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
 * (`earningsedge_chairman`) and three sub-agents
 * (`bull_analyst`, `bear_analyst`, `quant_analyst`).
 */
export default function ChairmanADKPanel({ ticker }) {
  const API_BASE = getApiBase();
  const [prompt, setPrompt] = useState(
    'Give me a structured verdict on this ticker — action, confidence, ' +
    'key driver, named dissent, and any paper trade to draft.',
  );
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function runChairman() {
    if (!prompt.trim()) return;
    setRunning(true);
    setResult(null);
    setError(null);
    try {
      const r = await fetch(`${API_BASE}/api/adk/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...sessionHeaders() },
        body: JSON.stringify({ prompt, ticker: ticker || undefined }),
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

  return (
    <section className="adk-panel">
      <header>
        <div className="adk-panel__title">
          <span className="adk-panel__badge">ADK</span>
          <h3>Analyst Chairman <span className="adk-panel__sub">via Google Cloud Agent Builder</span></h3>
        </div>
        <p className="adk-panel__lede">
          Same Gemini 3 brain that powers the cockpit, exposed as an{' '}
          <code>LlmAgent</code> with 11 tools and 3 sub-agents
          (<em>Bull</em>, <em>Bear</em>, <em>Quant</em>). Persistence backed by
          MongoDB MCP.
        </p>
      </header>

      <textarea
        rows={3}
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        placeholder="Ask the chairman …"
      />

      <div className="adk-panel__actions">
        <button onClick={runChairman} disabled={running}>
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
          </div>
          <div className="adk-panel__response">{result.response}</div>
          {Array.isArray(result.tool_calls) && result.tool_calls.length > 0 && (
            <details className="adk-panel__trace">
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
