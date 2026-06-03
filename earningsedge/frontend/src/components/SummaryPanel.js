import React from 'react';
import html2pdf from 'html2pdf.js';
import { getApiBase } from '../apiConfig';

const API_BASE = getApiBase();

function Section({ title, children }) {
  return (
    <div className="summary-section">
      <h3 className="summary-section-title">{title}</h3>
      <div className="summary-section-body">{children}</div>
    </div>
  );
}

export default function SummaryPanel({ summary, onClose }) {
  const contentRef = React.useRef(null);
  const [telegramAvailable, setTelegramAvailable] = React.useState(false);
  const [telegramSending, setTelegramSending] = React.useState(false);
  const [telegramNote, setTelegramNote] = React.useState(null);

  React.useEffect(() => {
    if (!summary || summary.error) {
      setTelegramAvailable(false);
      setTelegramNote(null);
      return undefined;
    }
    let cancelled = false;
    setTelegramNote(null);
    setTelegramAvailable(false);
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/telegram/status`);
        const j = await r.json().catch(() => ({}));
        if (!cancelled && j?.notify_available) setTelegramAvailable(true);
      } catch (_) {
        if (!cancelled) setTelegramAvailable(false);
      }
    })();
    return () => { cancelled = true; };
  }, [summary]);

  const sendTelegramPing = React.useCallback(async () => {
    if (!summary || summary.error) return;
    const co = summary.company || {};
    setTelegramNote(null);
    setTelegramSending(true);
    try {
      const r = await fetch(`${API_BASE}/api/telegram/notify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker: co.ticker || null,
          company_name: co.name || null,
        }),
      });
      const j = await r.json().catch(() => ({}));
      if (j?.ok) {
        setTelegramNote({ type: 'ok', text: 'Notification sent to your Telegram group.' });
      } else {
        setTelegramNote({
          type: 'err',
          text: j?.error || `Could not send (${r.status}).`,
        });
      }
    } catch (e) {
      setTelegramNote({ type: 'err', text: String(e?.message || e) });
    } finally {
      setTelegramSending(false);
    }
  }, [summary]);

  const handleDownloadPdf = React.useCallback(async () => {
    if (!contentRef.current || !summary || summary.error) return;
    const company = summary.company || {};
    const ticker = company.ticker || 'report';
    const dateStr = new Date().toISOString().slice(0, 10);
    const filename = `EarningsEdge_${ticker}_${dateStr}.pdf`;

    const opt = {
      margin: [10, 10, 10, 10],
      filename,
      image: { type: 'jpeg', quality: 0.98 },
      html2canvas: {
        scale: 2,
        backgroundColor: '#ffffff',
        logging: false,
      },
      jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' },
      pagebreak: { mode: ['avoid-all', 'css', 'legacy'] },
    };

    contentRef.current.classList.add('pdf-capture-mode');
    try {
      await html2pdf().set(opt).from(contentRef.current).save();
    } finally {
      contentRef.current.classList.remove('pdf-capture-mode');
    }
  }, [summary]);

  if (!summary) {
    return <aside className="summary-panel" aria-hidden="true" />;
  }

  if (summary.error) {
    return (
      <aside className="summary-panel open" role="dialog" aria-modal="false" aria-labelledby="summary-error-title">
        <div className="summary-actions no-print">
          <button
            onClick={onClose}
            className="btn btn-ghost"
            aria-label="Close summary"
          >
            <span aria-hidden="true">✕</span> Close
          </button>
        </div>
        <div className="summary-content">
          <h2 id="summary-error-title" style={{ color: 'var(--red)' }}>Summary failed</h2>
          <p className="muted">{summary.error}</p>
          {summary.raw && (
            <pre style={{ fontSize: 11, color: 'var(--muted)', overflow: 'auto' }}>
              {summary.raw}
            </pre>
          )}
        </div>
      </aside>
    );
  }

  const company = summary.company || {};
  const metrics = Array.isArray(summary.metrics_recap) ? summary.metrics_recap : [];
  const sentiment = Array.isArray(summary.sentiment_arc) ? summary.sentiment_arc : [];
  const qa = Array.isArray(summary.qa_recap) ? summary.qa_recap : [];
  const userQa = Array.isArray(summary.user_qa) ? summary.user_qa : [];
  const trade = summary.trade_signal || null;

  return (
    <aside className="summary-panel open" role="dialog" aria-modal="false" aria-labelledby="summary-ticker-title">
      <div className="summary-actions no-print">
        <div className="summary-actions-cluster">
          <button type="button" onClick={handleDownloadPdf} className="btn btn-primary">
            <span aria-hidden="true">📄</span> Download as PDF
          </button>
          {telegramAvailable ? (
            <button
              type="button"
              onClick={sendTelegramPing}
              className="btn btn-ghost"
              disabled={telegramSending}
              title="Sends a short ping to your configured Telegram group (not the full summary)."
            >
              {telegramSending ? 'Sending…' : 'Notify Telegram'}
            </button>
          ) : null}
        </div>
        <button
          onClick={onClose}
          className="btn btn-ghost"
          aria-label="Close summary"
        >
          <span aria-hidden="true">✕</span> Close
        </button>
      </div>
      {telegramNote ? (
        <p
          className={`no-print summary-telegram-note ${telegramNote.type === 'ok' ? 'muted' : ''}`}
          style={telegramNote.type === 'err' ? { color: 'var(--red)' } : undefined}
          role="status"
        >
          {telegramNote.text}
        </p>
      ) : null}

      <div className="summary-content" ref={contentRef}>
        <div className="summary-header">
          <div className="summary-eyebrow">EarningsEdge · Analyst Report</div>
          <h1 id="summary-ticker-title" className="summary-ticker">{company.ticker || '—'}</h1>
          <h2 className="summary-company">{company.name || ''}</h2>
          <div className="summary-meta">
            {[company.quarter, company.fiscal_year, company.sector, company.call_date]
              .filter(Boolean)
              .join(' · ')}
          </div>
        </div>

        {trade && trade.signal && (() => {
          const finalSignal = trade.signal === 'WAIT' ? 'HOLD' : trade.signal;
          const finalConfidence = trade.signal === 'WAIT'
            ? 'LOW'
            : (trade.confidence || 'MEDIUM');
          return (
            <div className={`summary-trade-hero summary-trade-${finalSignal}`}>
              <div className="summary-trade-eyebrow">Final Trade Recommendation</div>
              <div className="summary-trade-action">{finalSignal}</div>
              <div className="summary-trade-confidence">
                Confidence: <strong>{finalConfidence}</strong>
              </div>
              {trade.thesis && (
                <div className="summary-trade-thesis">{trade.thesis}</div>
              )}
              {trade.key_risk && (
                <div className="summary-trade-risk">
                  <strong>Key risk:</strong> {trade.key_risk}
                </div>
              )}
            </div>
          );
        })()}

        {summary.headline && (
          <div className="summary-headline">{summary.headline}</div>
        )}

        <Section title="Call Analysis">
          {summary.spoken ? (
            <p>{summary.spoken}</p>
          ) : (
            <p className="muted">
              No call analysis available yet. Capture at least 3 transcript lines, then summarize again.
            </p>
          )}
          {metrics.length > 0 ? (
            <p className="muted" style={{ marginTop: 8 }}>
              Note: Specific earnings numbers were detected, but this report now prioritizes narrative analysis over a blank-prone table.
            </p>
          ) : null}
        </Section>

        <Section title="How Management Sounded">
          {sentiment.length > 0 ? (
            <ul className="sentiment-list">
              {sentiment.map((s, i) => (
                <li key={i}>
                  <span className="sent-phase">
                    <strong>{s.phase}</strong>
                    {typeof s.score === 'number' && <span className="sent-score"> · {s.score}/100</span>}
                  </span>
                  <span className="sent-text">{s.summary}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No sentiment data captured.</p>
          )}
        </Section>

        <Section title="How It Stacks Up vs Peers">
          <p>{summary.competitors_recap || 'No competitor comparisons surfaced.'}</p>
        </Section>

        <Section title="News Context">
          <p>{summary.news_recap || 'No news context surfaced.'}</p>
        </Section>

        <Section title="Questions Worth Asking">
          {qa.length > 0 ? (
            <ol className="qa-recap-list">
              {qa.map((q, i) => (
                <li key={i}>
                  <div className="qa-recap-q">{q.question}</div>
                  {q.reason && <div className="muted">Why: {q.reason}</div>}
                </li>
              ))}
            </ol>
          ) : (
            <p className="muted">No questions flagged during the call.</p>
          )}
        </Section>

        {userQa.length > 0 && (
          <Section title="Your Questions">
            <ul className="user-qa-list">
              {userQa.map((q, i) => (
                <li key={i}>
                  <p><strong>You asked:</strong> {q.question}</p>
                  {q.answer && <p><strong>Answer:</strong> {q.answer}</p>}
                </li>
              ))}
            </ul>
          </Section>
        )}

        <div className="summary-footer">
          EarningsEdge Analyst Report · Generated by Gemini ·
          For informational purposes only. Not financial advice.
        </div>
      </div>
    </aside>
  );
}
