import React, { useEffect, useRef, useState } from 'react';

// Categorized highlighting — different visual weight for financial figures,
// positive signals, negative signals, and neutral earnings-call jargon.

const POSITIVE_WORDS = [
  'record', 'accelerating', 'strong', 'exceptional', 'beat', 'beats',
  'beating', 'raised', 'growth', 'demand', 'momentum', 'outperform',
  'outperformed', 'robust', 'exceeded', 'tripled', 'doubled', 'unprecedented',
  'all-time', 'unmatched', 'leading', 'leader', 'ramping', 'expanding',
  'expansion', 'surged', 'surge', 'best',
];

const NEGATIVE_WORDS = [
  'uncertain', 'challenging', 'headwind', 'headwinds', 'pressure',
  'constrained', 'constraint', 'excluded', 'miss', 'missed', 'lower',
  'decline', 'declined', 'risk', 'concern', 'cautious', 'weakness',
  'softness', 'delayed', 'deferred', 'shortfall', 'tough', 'lumpy',
  'compress', 'compressing', 'disappointing', 'slower',
];

const FINANCIAL_WORDS = [
  'revenue', 'eps', 'margin', 'margins', 'guidance', 'growth', 'billion',
  'million', 'trillion', 'earnings', 'profit', 'income', 'sales', 'gross',
  'operating', 'net', 'ebitda', 'cash', 'free cash flow', 'capex',
  'dividend', 'buyback', 'backlog', 'bookings', 'pipeline', 'forecast',
  'outlook', 'raised', 'lowered', 'reaffirmed', 'q1', 'q2', 'q3', 'q4',
  'fiscal', 'annual', 'yoy', 'quarter', 'quarterly', 'consensus',
];

function buildRegex(words) {
  const escaped = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  // Sort longer phrases first so "free cash flow" matches before "cash".
  escaped.sort((a, b) => b.length - a.length);
  return new RegExp(`\\b(${escaped.join('|')})\\b`, 'gi');
}

const POSITIVE_RE = buildRegex(POSITIVE_WORDS);
const NEGATIVE_RE = buildRegex(NEGATIVE_WORDS);
const FINANCIAL_RE = buildRegex(FINANCIAL_WORDS);
// Numbers like $22.1B, $194 billion, 75%, $4.93
const NUMBER_RE = /(\$[\d,]+(?:\.\d+)?\s*(?:billion|million|trillion|b|m|t)?|\b\d+(?:\.\d+)?\s*(?:percent|%|bps))/gi;

function escapePhrase(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function overlaps(a, b) {
  return !(a.end <= b.start || a.start >= b.end);
}

/**
 * @param {string} text
 * @param {Array<{ phrase?: string, tone?: string }>} highlightLexicon — from HighlightLexiconAgent
 */
function highlight(text, highlightLexicon = []) {
  const source = String(text || '');
  if (!source) return [];

  const matches = [];

  // Priority 10: LLM-derived analyst triggers (verbatim phrases in transcript)
  for (const t of highlightLexicon || []) {
    const phrase = (t.phrase || '').trim();
    if (phrase.length < 2) continue;
    const tone = (t.tone || 'material').toLowerCase();
    let cls = 'kw kw-trigger';
    if (tone === 'bullish') cls = 'kw kw-pos';
    else if (tone === 'bearish') cls = 'kw kw-neg';
    let re;
    try {
      re = new RegExp(escapePhrase(phrase), 'gi');
    } catch (_) {
      continue;
    }
    let m;
    while ((m = re.exec(source)) !== null) {
      matches.push({
        start: m.index,
        end: m.index + m[0].length,
        cls,
        text: m[0],
        pri: 10,
      });
      if (m[0].length === 0) re.lastIndex += 1;
    }
  }

  const staticRegexes = [
    { re: NUMBER_RE, cls: 'kw kw-num', pri: 9 },
    { re: POSITIVE_RE, cls: 'kw kw-pos', pri: 4 },
    { re: NEGATIVE_RE, cls: 'kw kw-neg', pri: 4 },
    { re: FINANCIAL_RE, cls: 'kw kw-fin', pri: 3 },
  ];
  for (const { re, cls, pri } of staticRegexes) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(source)) !== null) {
      matches.push({
        start: m.index,
        end: m.index + m[0].length,
        cls,
        text: m[0],
        pri,
      });
      if (m[0].length === 0) re.lastIndex += 1;
    }
  }

  // Highest priority first, then earlier in string
  matches.sort((a, b) => b.pri - a.pri || a.start - b.start || a.end - b.end);

  const accepted = [];
  for (const m of matches) {
    if (accepted.some((a) => overlaps(m, a))) continue;
    accepted.push(m);
  }
  accepted.sort((a, b) => a.start - b.start);

  const out = [];
  let cursor = 0;
  accepted.forEach((m, i) => {
    if (m.start > cursor) {
      out.push(<span key={`t${i}`}>{source.slice(cursor, m.start)}</span>);
    }
    out.push(<span key={`k${i}`} className={m.cls}>{m.text}</span>);
    cursor = m.end;
  });
  if (cursor < source.length) {
    out.push(<span key="tail">{source.slice(cursor)}</span>);
  }
  return out;
}

const KNOWN_SPEAKERS = new Set([
  'CEO', 'CFO', 'ANALYST', 'AGENT', 'YOU', 'CALL',
]);

function normalizeSpeaker(s) {
  const v = (s || 'UNKNOWN').toUpperCase();
  return KNOWN_SPEAKERS.has(v) ? v : 'UNKNOWN';
}

const TITLE_BY_MODE = {
  idle: 'Live Transcript',
  briefing: 'Briefing Transcript',
  ready: 'Briefing Transcript',
  listening: 'Live Transcript',
};

function formatBytes(n) {
  if (!n) return '0 B';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function LevelBars({ level }) {
  // Normalise RMS (0..~0.3 typical speech) to 0..1
  const normalized = Math.min(1, Math.max(0, level * 4));
  const barCount = 12;
  const activeBars = Math.round(normalized * barCount);
  return (
    <div className="level-bars">
      {Array.from({ length: barCount }).map((_, i) => (
        <span
          key={i}
          className={`level-bar ${i < activeBars ? 'active' : ''}`}
          style={{ opacity: i < activeBars ? 0.4 + (i / barCount) * 0.6 : 0.15 }}
        />
      ))}
    </div>
  );
}

/** RMS below this is treated as "silent" for the tab-audio warning (with hysteresis below). */
const SILENT_LEVEL_MAX = 0.012;
/** Show the amber warning only after this many ms of sustained silence — avoids flicker when RMS jitters. */
const SILENT_WARN_DEBOUNCE_MS = 2200;

export default function TranscriptPanel({
  transcript,
  transcriptPartial = null,
  mode,
  audioMeter,
  highlightLexicon = [],
}) {
  const feedRef = useRef(null);
  const atBottomRef = useRef(true);
  const [silentWarnShown, setSilentWarnShown] = useState(false);

  const handleScroll = () => {
    const el = feedRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    atBottomRef.current = distance < 40;
  };

  useEffect(() => {
    const el = feedRef.current;
    if (el && atBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [transcript, transcriptPartial, highlightLexicon]);

  const isEmpty = transcript.length === 0;
  const title = TITLE_BY_MODE[mode] || 'Live Transcript';
  const level = audioMeter?.level || 0;
  const bytesSent = audioMeter?.bytesSent || 0;
  const showMeter = mode === 'listening' || mode === 'briefing';

  // Raw condition: lots of bytes but analyser thinks silence (often wrong during quiet speech or VAD gaps).
  // Without debouncing, RMS crosses the threshold every few ms → amber banner / border flashes unbearably.
  // If transcript lines exist, audio is clearly reaching the pipeline — suppress (AnalyserNode RMS often lies for tab capture).
  useEffect(() => {
    if (mode !== 'listening' || bytesSent <= 200000) {
      setSilentWarnShown(false);
      return undefined;
    }
    if (transcript.length > 0) {
      setSilentWarnShown(false);
      return undefined;
    }
    if (level >= SILENT_LEVEL_MAX) {
      setSilentWarnShown(false);
      return undefined;
    }
    const id = window.setTimeout(() => {
      setSilentWarnShown(true);
    }, SILENT_WARN_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [level, bytesSent, mode, transcript.length]);

  const silentWarning = silentWarnShown;

  return (
    <div className="card">
      <h3 className="card-title">
        {title}
        <span className="badge">{transcript.length} lines</span>
      </h3>
      {showMeter && (
        <div className={`audio-meter-row ${silentWarning ? 'silent-warning' : ''}`}>
          <span className="audio-meter-label">
            {audioMeter?.source === 'tab' ? '📺 Tab' : '🎤 Mic'}
          </span>
          <LevelBars level={level} />
          <span className="audio-meter-stats">{formatBytes(bytesSent)}</span>
        </div>
      )}
      {silentWarning && (
        <div className="silent-warning-msg" role="status" aria-live="polite">
          Receiving audio bytes but the signal is silent. Your tab share is probably
          missing audio — click <strong>Reset</strong>, then <strong>Listen live</strong> again,
          and make sure you check <strong>"Share tab audio"</strong> in the dialog.
        </div>
      )}
      <div className="transcript-feed" ref={feedRef} onScroll={handleScroll}>
        {isEmpty ? (
          mode === 'idle' ? (
            <div className="transcript-idle-hint">
              <p className="transcript-idle-hint-title">No active session</p>
              <p className="transcript-idle-hint-body">
                Use <strong>Load company</strong> above, then <strong>Listen live</strong> when the
                webcast, news segment, or conference stream starts.
                Live transcript lines will appear here.
              </p>
            </div>
          ) : (
            <>
              {[0, 1, 2].map((i) => (
                <div key={i} className="transcript-line skeleton-line">
                  <div className="skeleton skel-line" style={{ width: 56, height: 12 }} />
                  <div className="skeleton skel-line" style={{ width: '92%' }} />
                  <div className="skeleton skel-line" style={{ width: '74%' }} />
                </div>
              ))}
              {mode === 'listening' && (
                <div className="transcript-listening-footer">
                  <span className="live-dot-inline" />
                  Listening to tab audio… transcript will appear as the call speaks.
                </div>
              )}
            </>
          )
        ) : (
          transcript.map((line, idx) => {
            const speaker = normalizeSpeaker(line.speaker);
            return (
              <div
                key={line._id ?? idx}
                className={`transcript-line ${speaker} transcript-fresh`}
              >
                <span className={`speaker-badge ${speaker}`}>{speaker}</span>
                <div className="transcript-text">{highlight(line.text, highlightLexicon)}</div>
              </div>
            );
          })
        )}
        {transcriptPartial && transcriptPartial.text ? (() => {
          const speaker = normalizeSpeaker(transcriptPartial.speaker);
          return (
            <div
              className={`transcript-line ${speaker} transcript-line-partial`}
              aria-live="polite"
              aria-label="Live caption in progress"
            >
              <span className={`speaker-badge ${speaker}`}>{speaker}</span>
              <div className="transcript-text">
                {highlight(transcriptPartial.text, highlightLexicon)}
                <span className="transcript-typing-cursor" aria-hidden="true" />
              </div>
            </div>
          );
        })() : null}
      </div>
    </div>
  );
}
