import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import TranscriptPanel from './components/TranscriptPanel';
import MetricsPanel from './components/MetricsPanel';
import SentimentGauge from './components/SentimentGauge';
import NewsPanel from './components/NewsPanel';
import CompetitorPanel from './components/CompetitorPanel';
import TradeSignalHero from './components/TradeSignalHero';
import SummaryPanel from './components/SummaryPanel';
import MacroPanel from './components/MacroPanel';
import TechnicalPanel from './components/TechnicalPanel';
import AnalystPanel from './components/AnalystPanel';
import ChairmanADKPanel from './components/ChairmanADKPanel';
import MorningBriefingPanel from './components/MorningBriefingPanel';
import PatternAlertsPanel from './components/PatternAlertsPanel';
import NewsDigestPanel from './components/NewsDigestPanel';
import PersonaPulsePanel from './components/PersonaPulsePanel';
import PatternMatchesPanel from './components/PatternMatchesPanel';
import TradingPanel from './components/TradingPanel';
import CommitteeView from './components/CommitteeView';
import OnboardingTour from './components/OnboardingTour';
import AudioStream, { getMicStream, getTabAudioStream } from './lib/audioStream';
import AudioPlayer from './lib/audioPlayer';
import { getApiBase, getWsUrl, getSessionId, sessionHeaders } from './apiConfig';

const API_BASE = getApiBase();
const SIGNAL_PULSE_MS = 3300;

const LS_LAST = 'earningsedge.last_company';
const LS_RECENT = 'earningsedge.recent_tickers';
const RECENT_MAX = 5;

function readJsonLS(key) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}

function writeJsonLS(key, value) {
  try {
    if (value == null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, JSON.stringify(value));
  } catch (_) {}
}

// App modes:
//   'idle'      — dashboard home; set "today's company" to preload tiles, or use voice briefing.
//   'briefing'  — mic open (optional path), user is talking to identify the company.
//   'ready'     — company loaded; awaiting "Earnings call" (tab capture).
//   'listening' — tab audio streaming the actual call.

function App({ onBackToLanding }) {
  const [transcript, setTranscript] = useState([]);
  /** In-progress live caption from `transcript_partial` events. Renders as an
   *  italic "currently being said" line below the finalized transcript.
   *  Cleared when a final `transcript` event arrives or the session resets. */
  const [transcriptPartial, setTranscriptPartial] = useState(null);
  const [metrics, setMetrics] = useState({});
  const [sentiment, setSentiment] = useState({});
  const [news, setNews] = useState([]);
  const [newsOverall, setNewsOverall] = useState('neutral');
  const [newsOverallRationale, setNewsOverallRationale] = useState('');
  const [competitors, setCompetitors] = useState([]);
  const [tradeSignal, setTradeSignal] = useState(null);
  const [signalFresh, setSignalFresh] = useState(false);
  const [livePrice, setLivePrice] = useState(null);
  const [sessionStatus, setSessionStatus] = useState('idle');
  const [mode, setMode] = useState('idle');
  const [identified, setIdentified] = useState(() => {
    // Seed from last session so a reload doesn't drop the user back at the empty hero.
    const cached = readJsonLS(LS_LAST);
    if (cached && cached.ticker) {
      return {
        ticker: cached.ticker,
        company_name: cached.company_name || null,
        sector: cached.sector || null,
        quarter: cached.quarter || null,
        fiscal_year: cached.fiscal_year || null,
      };
    }
    return { ticker: null, company_name: null, sector: null, quarter: null, fiscal_year: null };
  });
  const [recentTickers, setRecentTickers] = useState(() => {
    const raw = readJsonLS(LS_RECENT);
    return Array.isArray(raw) ? raw.slice(0, RECENT_MAX) : [];
  });
  const [agentSpeaking, setAgentSpeaking] = useState(false);
  const [errorMsg, setErrorMsg] = useState(null);
  const [summary, setSummary] = useState(null);
  const [summarizing, setSummarizing] = useState(false);
  const [activeSource, setActiveSource] = useState(null);
  const [paused, setPaused] = useState(false);
  const [chatLog, setChatLog] = useState([]);
  const [chatInput, setChatInput] = useState('');
  const [chatSending, setChatSending] = useState(false);
  const [audioMeter, setAudioMeter] = useState({ source: null, level: 0, bytesSent: 0 });
  /** Analyst trigger phrases from HighlightLexiconAgent (merged on server). */
  const [highlightLexicon, setHighlightLexicon] = useState([]);
  const [voiceListening, setVoiceListening] = useState(false);
  const [voiceInterim, setVoiceInterim] = useState('');
  const [chatCollapsed, setChatCollapsed] = useState(false);
  /** Gemini Live (live-audio path) health — probed once on mount.
   *  Drives the disabled state + tooltip on the Listen-live button so
   *  judges don't click into a silent failure when quota or billing
   *  blocks the bidiGenerateContent socket. */
  const [geminiLive, setGeminiLive] = useState({ available: true, error: null });
  const [coverageForm, setCoverageForm] = useState({
    ticker: '',
    company_name: '',
    quarter: '',
    year: '',
  });
  const [coverageLoading, setCoverageLoading] = useState(false);
  const [macroData, setMacroData] = useState(null);
  const [technicalData, setTechnicalData] = useState(null);
  /** Full analyst recommendation payload from `analyst_opinion` WS (briefing preload). */
  /** `null` = not hydrated yet; `{}` after coverage/WS means “no analyst payload”. */
  const [analystOpinion, setAnalystOpinion] = useState(null);
  /** Server-side Finnhub / recommendation failure detail after Load company. */
  const [analystOpinionError, setAnalystOpinionError] = useState(null);
  const [peerValuation, setPeerValuation] = useState(null);
  /** `company` — analysis + trade hero; `trading` — Alpaca paper + chart */
  const [appTab, setAppTab] = useState('company');
  const [tradingRefreshKey, setTradingRefreshKey] = useState(0);
  /** Sub-view inside the Company tab. Reduces simultaneous panels from 11 → ~3.
   *  `overview` — transcript + metrics + sentiment (the "what's happening" view)
   *  `peers`    — competitors + analyst + news (the "what does the market think" view)
   *  `macro`    — macro + technical (the "broader context" view)
   *  `committee`— full agent vote breakdown (the "why" view)
   *  --- v5 tabs ---
   *  `verdict`   — agent verdict card + persona pulse (default)
   *  `live`      — transcript + persona pulse + pattern matches during audio
   *  `memory`    — Atlas Vector Search past verdicts
   *  `sentiment` — news sentiment gauge + ranked headlines */
  const [companyView, setCompanyView] = useState('verdict');
  /** When true, show the "Change company" form even after a ticker is loaded. */
  const [showChangeCompany, setShowChangeCompany] = useState(false);
  /** When non-null, the Reset button is awaiting confirmation. */
  const [confirmReset, setConfirmReset] = useState(false);
  /** Tracks which preload data slices have arrived since the last Load click.
   *  Used to render a "Loading: peers · news…" progress strip during the
   *  ~5–30s after coverage is requested. Each WebSocket data event marks its
   *  slice as loaded. */
  const [dataLoaded, setDataLoaded] = useState({
    metrics: false,
    competitors: false,
    news: false,
    macro: false,
    technical: false,
    analyst: false,
  });
  /** When true, the pre-flight modal explaining the tab-share dialog is showing.
   *  Suppressed once the user opts out via "Don't show this again". */
  const [showPreflight, setShowPreflight] = useState(false);
  const SKIP_PREFLIGHT_KEY = 'earningsedge.skip_preflight';
  const TOUR_DONE_KEY = 'earningsedge.tour_completed';
  const [skipPreflight, setSkipPreflight] = useState(() => {
    try { return window.localStorage.getItem(SKIP_PREFLIGHT_KEY) === '1'; } catch (_) { return false; }
  });
  const [tourOpen, setTourOpen] = useState(false);
  /** ISO timestamp captured at App mount. Used to scope the Trading tab's
   *  orders list to the current session by default — without this, the
   *  Alpaca paper history accumulates across reloads and resets and a long-
   *  time user sees a wall of stale orders. */
  const [sessionStartIso] = useState(() => new Date().toISOString());
  /** Ref-tracked timers/controllers so consecutive actions don't leak or
   *  fire stale callbacks (see bug audit notes inline at each call site). */
  const confirmResetTimerRef = useRef(null);
  const summarizeTimerRef = useRef(null);
  const loadingStripTimerRef = useRef(null);
  const coverageAbortRef = useRef(null);
  const identifiedRef = useRef(identified);
  const recognitionRef = useRef(null);

  const dashboardWsRef = useRef(null);
  const audioStreamRef = useRef(null);
  const audioPlayerRef = useRef(null);
  const freshTimerRef = useRef(null);
  const agentSpeakingTimerRef = useRef(null);
  const voiceDoneFallbackRef = useRef(null);

  useEffect(() => {
    identifiedRef.current = identified;
  }, [identified]);

  /** Poll the price endpoint every 30s while a ticker is loaded.
   *  The legacy PriceStream uses yfinance which is missing on the
   *  Heroku slim build, so we use the Finnhub-backed /api/price
   *  endpoint as the source of truth for livePrice. Without this, the
   *  Committee tab's BUY/SHORT buttons stay disabled because the
   *  legacy `price > 0` check never satisfies. */
  useEffect(() => {
    const tk = identified?.ticker;
    if (!tk) return;
    let cancelled = false;
    async function fetchPrice() {
      try {
        const r = await fetch(`${API_BASE}/api/price?ticker=${encodeURIComponent(tk)}`);
        if (cancelled || !r.ok) return;
        const body = await r.json();
        const p = Number(body.price);
        if (Number.isFinite(p) && p > 0) setLivePrice(p);
      } catch (_) { /* ignore */ }
    }
    fetchPrice();
    const interval = setInterval(fetchPrice, 30000);
    return () => { cancelled = true; clearInterval(interval); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identified?.ticker]);

  /** Probe Gemini Live (bidiGenerateContent) once on mount. The result
   *  drives a non-intrusive disabled state on the Listen-live button so
   *  the user gets a clear "live audio unavailable" message instead of
   *  watching a session that will never produce a transcript. */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/gemini/health`);
        if (cancelled) return;
        const body = await r.json();
        setGeminiLive({ available: !!body.available, error: body.error || null });
      } catch (_) {
        if (!cancelled) setGeminiLive({ available: false, error: 'health probe failed' });
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const teardownCapture = useCallback(async () => {
    const stream = audioStreamRef.current;
    audioStreamRef.current = null;
    if (stream) {
      try { await stream.disconnect(); } catch (_) {}
    }
    const player = audioPlayerRef.current;
    audioPlayerRef.current = null;
    if (player) {
      try { await player.dispose(); } catch (_) {}
    }
  }, []);

  const handleMessage = useCallback((msg) => {
    const { type, data } = msg || {};
    if (!type) return;
    // Per-tab session filter: backend stamps every broadcast with the
    // session_id of the tab that triggered the action. Drop events tagged
    // with someone else's session so concurrent users / tabs don't trample
    // each other (NVDA flipping to GOOGL mid-session, fresh tabs auto-
    // loading another user's ticker, etc.). Untagged events (no session_id)
    // are global and always processed.
    if (msg.session_id && msg.session_id !== getSessionId()) return;
    switch (type) {
      case 'company_identified':
        setIdentified({
          ticker: data.ticker || null,
          company_name: data.company_name || null,
          sector: data.sector || null,
          quarter: data.quarter || null,
          fiscal_year: data.fiscal_year || null,
        });
        // Only advance past the briefing screen if the user has actually
        // initiated a session. Without this guard, a stale company_identified
        // message replayed on a cold-open WebSocket would skip the briefing
        // screen entirely.
        // While voice briefing is active, stay in `briefing` so "Done speaking"
        // stays available; switching to `ready` here hid that control while the
        // mic was still open.
        setMode((prev) => {
          if (prev === 'listening') return prev;
          if (prev === 'briefing') return 'briefing';
          return 'ready';
        });
        break;
      case 'transcript': {
        setTranscript((prev) => [...prev, { ...data, _id: prev.length, _ts: Date.now() }]);
        // Final line replaces the in-progress caption — clear the partial so
        // the same text doesn't briefly appear twice (final + partial echo).
        setTranscriptPartial(null);
        break;
      }
      case 'transcript_partial': {
        // Live caption update. We replace (not append) so the in-progress
        // line refreshes in place as more words arrive.
        setTranscriptPartial({
          speaker: data?.speaker || 'CALL',
          text: String(data?.text || ''),
        });
        break;
      }
      case 'metrics':
        setMetrics((prev) => ({ ...prev, ...data }));
        setDataLoaded((prev) => prev.metrics ? prev : { ...prev, metrics: true });
        break;
      case 'sentiment':
        setSentiment(data);
        break;
      case 'analyst_opinion':
        setAnalystOpinion(data && typeof data === 'object' ? data : {});
        setAnalystOpinionError(null);
        setDataLoaded((prev) => prev.analyst ? prev : { ...prev, analyst: true });
        break; // `{}` = server had no usable analyst block (still hydrated)
      case 'news': {
        const articles = (data.articles || []).map((a) => ({
          headline: a.headline || '',
          url: a.url || '#',
          source: a.source || '',
          published_at: a.published_at || '',
          sentiment: a.sentiment || a.sentiment_label || 'neutral',
          sentiment_reason: a.sentiment_reason || '',
          sentiment_confidence:
            a.sentiment_confidence != null ? a.sentiment_confidence : undefined,
        }));
        setNews(articles);
        setNewsOverall(data.overall_sentiment || data.overall_sentiment_label || 'neutral');
        setNewsOverallRationale(data.overall_rationale || '');
        setDataLoaded((prev) => prev.news ? prev : { ...prev, news: true });
        break;
      }
      case 'competitors':
        setCompetitors(data.peers || []);
        setDataLoaded((prev) => prev.competitors ? prev : { ...prev, competitors: true });
        break;
      case 'peer_valuation':
        setPeerValuation(data || null);
        break;
      case 'trade_signal':
        setTradeSignal(data);
        setSignalFresh(true);
        if (freshTimerRef.current) clearTimeout(freshTimerRef.current);
        freshTimerRef.current = setTimeout(() => setSignalFresh(false), SIGNAL_PULSE_MS);
        break;
      case 'price_tick': {
        const p = Number(data?.price);
        if (Number.isFinite(p)) setLivePrice(p);
        break;
      }
      case 'phase':
        // Same guard as company_identified — never advance past idle from
        // a replayed historical phase message.
        if (data.phase === 'listening') {
          setMode((prev) => (prev === 'idle' ? prev : 'listening'));
        } else if (data.phase === 'briefing') {
          // Ignore — `briefing` mode is set only by the client when starting
          // voice. Applying server phase here caused stale messages to override
          // `ready` and hide "Done speaking" / block tab share.
        }
        break;
      case 'agent_audio': {
        const player = audioPlayerRef.current;
        if (player && data.pcm_b64) {
          player.enqueueBase64(data.pcm_b64, data.sample_rate || 24000);
          setAgentSpeaking(true);
          if (agentSpeakingTimerRef.current) clearTimeout(agentSpeakingTimerRef.current);
          agentSpeakingTimerRef.current = setTimeout(() => setAgentSpeaking(false), 800);
        }
        break;
      }
      case 'agent_speech':
        // Legacy event type — backend now emits agent speech as a normal
        // transcript entry with speaker=AGENT. Kept here only as a
        // fallback for any in-flight messages from older sessions.
        if (data.text) {
          setTranscript((prev) => [
            ...prev,
            { speaker: 'AGENT', text: data.text, timestamp_s: 0, _id: prev.length },
          ]);
        }
        break;
      case 'spoken_briefing':
        // Handled server-side (bridged into Live session). Nothing to do here.
        break;
      case 'chat':
        setChatLog((prev) => [...prev, { role: data.role, text: data.text, _id: prev.length }]);
        break;
      case 'summary':
        setSummary(data);
        setSummarizing(false);
        if (summarizeTimerRef.current) {
          window.clearTimeout(summarizeTimerRef.current);
          summarizeTimerRef.current = null;
        }
        break;
      case 'status':
        setSessionStatus(data.state || 'idle');
        if (data.state === 'error' && data.message) {
          setErrorMsg(data.message);
        }
        break;
      case 'highlight_lexicon':
        if (Array.isArray(data.triggers)) {
          setHighlightLexicon(data.triggers);
        }
        break;
      case 'voice_briefing_complete':
        if (voiceDoneFallbackRef.current) {
          clearTimeout(voiceDoneFallbackRef.current);
          voiceDoneFallbackRef.current = null;
        }
        (async () => {
          await teardownCapture();
          setSessionStatus('idle');
          setMode(identifiedRef.current?.ticker ? 'ready' : 'idle');
        })();
        break;
      case 'macro':
        setMacroData(data);
        setDataLoaded((prev) => prev.macro ? prev : { ...prev, macro: true });
        break;
      case 'technical':
        setTechnicalData(data);
        setDataLoaded((prev) => prev.technical ? prev : { ...prev, technical: true });
        break;
      default:
        break;
    }
  }, [teardownCapture]);

  // Dashboard WebSocket — outbound updates from the server.
  useEffect(() => {
    let closed = false;
    let retryTimer = null;
    const connect = () => {
      const ws = new WebSocket(getWsUrl('/ws'));
      dashboardWsRef.current = ws;
      ws.onopen = () => {};
      ws.onclose = () => {
        if (!closed) retryTimer = setTimeout(connect, 1500);
      };
      ws.onerror = () => {
        try { ws.close(); } catch (_) {}
      };
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          handleMessage(msg);
        } catch (_) {}
      };
    };
    connect();
    return () => {
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (freshTimerRef.current) clearTimeout(freshTimerRef.current);
      if (agentSpeakingTimerRef.current) clearTimeout(agentSpeakingTimerRef.current);
      if (voiceDoneFallbackRef.current) clearTimeout(voiceDoneFallbackRef.current);
      if (dashboardWsRef.current) dashboardWsRef.current.close();
    };
  }, [handleMessage]);

  const resetState = () => {
    setTranscript([]);
    setTranscriptPartial(null);
    setHighlightLexicon([]);
    setMetrics({});
    setSentiment({});
    setNews([]);
    setNewsOverall('neutral');
    setNewsOverallRationale('');
    setCompetitors([]);
    setTradeSignal(null);
    setSignalFresh(false);
    setLivePrice(null);
    setChatLog([]);
    setChatInput('');
    setPaused(false);
    setIdentified({ ticker: null, company_name: null, sector: null, quarter: null, fiscal_year: null });
    setErrorMsg(null);
    setSummary(null);
    setSummarizing(false);
    setChatCollapsed(false);
    setCoverageForm({ ticker: '', company_name: '', quarter: '', year: '' });
    setMacroData(null);
    setTechnicalData(null);
    setAnalystOpinion(null);
    setAnalystOpinionError(null);
    setCompanyView('verdict');
    setShowChangeCompany(false);
    setConfirmReset(false);
    setShowPreflight(false);
    setTourOpen(false);
    setDataLoaded({
      metrics: false, competitors: false, news: false,
      macro: false, technical: false, analyst: false,
    });
    // Stop any active speech recognition so a delayed onend doesn't post a
    // chat against the freshly-cleared session.
    try { recognitionRef.current?.stop(); } catch (_) {}
    recognitionRef.current = null;
    setVoiceListening(false);
    setVoiceInterim('');
    // Cancel pending timers so they don't fire after reset.
    if (confirmResetTimerRef.current) {
      window.clearTimeout(confirmResetTimerRef.current);
      confirmResetTimerRef.current = null;
    }
    if (summarizeTimerRef.current) {
      window.clearTimeout(summarizeTimerRef.current);
      summarizeTimerRef.current = null;
    }
    if (loadingStripTimerRef.current) {
      window.clearTimeout(loadingStripTimerRef.current);
      loadingStripTimerRef.current = null;
    }
    if (voiceDoneFallbackRef.current) {
      window.clearTimeout(voiceDoneFallbackRef.current);
      voiceDoneFallbackRef.current = null;
    }
    // Abort any in-flight /api/coverage so its result doesn't undo the reset.
    if (coverageAbortRef.current) {
      try { coverageAbortRef.current.abort(); } catch (_) {}
      coverageAbortRef.current = null;
    }
  };

  /** Clear all panel-bound data so changing companies doesn't leave the
   *  previous company's transcript / metrics / news visible while the new
   *  company's data streams in. */
  const clearPanelDataForLoad = () => {
    setTranscript([]);
    setTranscriptPartial(null);
    setMetrics({});
    setSentiment({});
    setNews([]);
    setNewsOverall('neutral');
    setNewsOverallRationale('');
    setCompetitors([]);
    setTradeSignal(null);
    setSignalFresh(false);
    setMacroData(null);
    setTechnicalData(null);
    setPeerValuation(null);
    setHighlightLexicon([]);
    setDataLoaded({
      metrics: false, competitors: false, news: false,
      macro: false, technical: false, analyst: false,
    });
    // Backend may never send some slices (e.g. no FRED key → no macro). After
    // 30s, mark all unresolved slices as "loaded" so the loading strip clears
    // instead of saying "Loading Macro" forever.
    if (loadingStripTimerRef.current) window.clearTimeout(loadingStripTimerRef.current);
    loadingStripTimerRef.current = window.setTimeout(() => {
      loadingStripTimerRef.current = null;
      setDataLoaded({
        metrics: true, competitors: true, news: true,
        macro: true, technical: true, analyst: true,
      });
    }, 30000);
  };

  const submitCoverage = async (e, override) => {
    if (e && typeof e.preventDefault === 'function') e.preventDefault();
    // Accept an override so the watchlist chip click can pass the ticker
    // synchronously rather than relying on React's batched setState.
    const tickerRaw = (override?.ticker || coverageForm.ticker || '').trim();
    const companyRaw = (override?.company_name || coverageForm.company_name || '').trim();
    if (!tickerRaw && !companyRaw) {
      setErrorMsg('Enter a ticker (e.g. NVDA) or a company name — one is enough.');
      return;
    }
    // Cancel any prior in-flight coverage fetch — only the latest submit wins.
    if (coverageAbortRef.current) {
      try { coverageAbortRef.current.abort(); } catch (_) {}
    }
    const ac = new AbortController();
    coverageAbortRef.current = ac;
    setCoverageLoading(true);
    setErrorMsg(null);
    clearPanelDataForLoad();
    try {
      const r = await fetch(`${API_BASE}/api/coverage`, {
        method: 'POST',
        headers: sessionHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          ticker: tickerRaw ? tickerRaw.toUpperCase() : null,
          company_name: companyRaw || null,
          quarter: coverageForm.quarter.trim() || null,
          year: coverageForm.year.trim() || null,
        }),
        signal: ac.signal,
      });
      const j = await r.json();
      if (ac.signal.aborted) return;
      if (!j.ok) throw new Error(j.error || 'Could not load company coverage');
      // Do not rely only on WebSocket `company_identified` — the dashboard WS
      // may be down or messages may be missed; the HTTP response is authoritative.
      if (j.company) {
        setIdentified({
          ticker: j.company.ticker || null,
          company_name: j.company.company_name || null,
          sector: j.company.sector || null,
          quarter: j.company.quarter || null,
          fiscal_year: j.company.fiscal_year ?? null,
        });
      }
      setAnalystOpinion(
        j.analyst_opinion != null && typeof j.analyst_opinion === 'object' ? j.analyst_opinion : {},
      );
      setAnalystOpinionError(
        j.analyst_opinion_error != null && String(j.analyst_opinion_error).trim()
          ? String(j.analyst_opinion_error).trim()
          : null,
      );
      setMode('ready');
    } catch (err) {
      if (err?.name === 'AbortError') return;
      setErrorMsg(String(err?.message || err));
    } finally {
      if (coverageAbortRef.current === ac) coverageAbortRef.current = null;
      setCoverageLoading(false);
    }
  };

  /** Push-to-talk: open mic, speak one command, then tap "Done speaking". */
  const startVoiceBriefing = async () => {
    if (mode !== 'idle' && mode !== 'ready') return;
    setErrorMsg(null);
    if (!identified.ticker) {
      resetState();
      setCoverageForm({ ticker: '', company_name: '', quarter: '', year: '' });
    } else {
      setTranscript([]);
      setTranscriptPartial(null);
    }
    setSessionStatus('connecting');
    setMode('briefing');
    setChatCollapsed(false);

    const player = new AudioPlayer();
    audioPlayerRef.current = player;

    const stream = new AudioStream();
    audioStreamRef.current = stream;
    try {
      await stream.connect({
        wsUrl: getWsUrl('/ws/audio'),
        onClose: () => {
          if (audioStreamRef.current === stream) {
            teardownCapture();
            setMode(identifiedRef.current?.ticker ? 'ready' : 'idle');
            setSessionStatus('idle');
          }
        },
        onError: (err) => setErrorMsg(String(err?.message || err)),
        onSourceChange: (src) => setActiveSource(src),
        onLevel: (stats) => setAudioMeter(stats),
      });
      stream.sendControl({ control: 'phase', phase: 'briefing' });
      const mic = await getMicStream();
      await stream.attachMicOnly(mic);
    } catch (err) {
      setErrorMsg(String(err?.message || err));
      setSessionStatus('error');
      setMode(identified.ticker ? 'ready' : 'idle');
      await teardownCapture();
    }
  };

  const finishVoiceBriefing = () => {
    const stream = audioStreamRef.current;
    if (!stream) return;
    setErrorMsg(null);
    stream.sendControl({ control: 'briefing_done' });
    if (voiceDoneFallbackRef.current) clearTimeout(voiceDoneFallbackRef.current);
    voiceDoneFallbackRef.current = setTimeout(async () => {
      voiceDoneFallbackRef.current = null;
      // Bail out if the user has since started a different audio session
      // (live earnings call). Otherwise this fallback would tear down their
      // active capture.
      if (audioStreamRef.current !== stream) return;
      await teardownCapture();
      setSessionStatus('idle');
      setMode(identifiedRef.current?.ticker ? 'ready' : 'idle');
    }, 12000);
  };

  /** UI entry point for the call — shows the pre-flight modal first unless the
   *  user has opted out. The pre-flight reduces the most common failure mode
   *  (forgetting to tick "Share tab audio" in the browser's tab-share dialog). */
  const requestStartEarningsCall = () => {
    if (!identified.ticker) {
      setErrorMsg('First load a company: use the form above, or click "Speak the company name".');
      return;
    }
    if (mode === 'listening' || mode === 'briefing') return;
    if (sessionStatus === 'connecting') return;
    if (skipPreflight) {
      startEarningsCall();
    } else {
      setShowPreflight(true);
    }
  };

  const confirmPreflightStart = () => {
    setShowPreflight(false);
    startEarningsCall();
  };

  const setSkipPreflightAndStore = (next) => {
    setSkipPreflight(next);
    try {
      window.localStorage.setItem(SKIP_PREFLIGHT_KEY, next ? '1' : '0');
    } catch (_) {}
  };

  /** Tab capture + live agents — requires company coverage (or prior voice identify). */
  const startEarningsCall = async () => {
    if (!identified.ticker) {
      setErrorMsg('First load a company: use the form above, or click “Say the company name” and speak it.');
      return;
    }
    if (mode === 'listening' || mode === 'briefing') return;
    if (sessionStatus === 'connecting') return;
    setErrorMsg(null);
    setSessionStatus('connecting');
    // Stay on `ready` until tab audio is attached — do not use `briefing` here;
    // that mode is only for voice identify and would hide the tab-share flow.

    if (audioStreamRef.current || audioPlayerRef.current) {
      await teardownCapture();
    }

    const player = new AudioPlayer();
    audioPlayerRef.current = player;
    const stream = new AudioStream();
    audioStreamRef.current = stream;
    try {
      await stream.connect({
        wsUrl: getWsUrl('/ws/audio'),
        onClose: () => {
          if (audioStreamRef.current === stream) {
            // CRITICAL UX FIX: if we have transcript content, the session was
            // real and the user expects to still see the End-call button.
            // Previously this silently reset mode to 'ready', which made the
            // Listen-live button reappear — users thought the site died.
            // Now we keep the session visible and surface a "Audio
            // disconnected" banner with Reconnect + End-call options.
            const hasContent = (identifiedRef.current?.ticker) && Array.isArray(transcript) && transcript.length > 0;
            if (hasContent) {
              setSessionStatus('disconnected');
              setErrorMsg('Audio connection dropped. The transcript so far is preserved — click Reconnect to continue listening, or End call & summarize to wrap up.');
              // Keep mode='listening' so the live-control bar stays visible
              // and the End-call button remains clickable.
            } else {
              teardownCapture();
              setMode(identifiedRef.current?.ticker ? 'ready' : 'idle');
              setSessionStatus('idle');
            }
          }
        },
        onError: (err) => setErrorMsg(String(err?.message || err)),
        onSourceChange: (src) => setActiveSource(src),
        onLevel: (stats) => setAudioMeter(stats),
      });
      const tab = await getTabAudioStream();
      await stream.attachTabAndMic(tab, null);
      stream.sendControl({ control: 'phase', phase: 'listening' });
      setTranscript([]);
      setTranscriptPartial(null);
      setMode('listening');
      setSessionStatus('running');
      // Live transcript belongs in Overview — auto-jump there so the user
      // doesn't miss the speech they just started streaming.
      setCompanyView('live');
    } catch (err) {
      const msg = String(err?.message || err);
      const name = err?.name || '';
      let detail = msg;
      if (name === 'NotAllowedError' || /abort|cancel|dismissed/i.test(msg)) {
        detail =
          'Tab share was cancelled or blocked. Click Listen live again, choose the Chrome tab that is playing the audio, and enable “Share tab audio”.';
      } else if (!msg.includes('tab audio')) {
        detail = `${msg}. In the share dialog, pick the tab that’s playing the audio and check “Share tab audio”.`;
      }
      setErrorMsg(detail);
      setSessionStatus('error');
      setMode('ready');
      await teardownCapture();
    }
  };

  const togglePause = async () => {
    const next = !paused;
    // Optimistic flip — feels snappy. Revert if the server disagrees so the
    // UI doesn't show "Paused" while the backend keeps streaming.
    setPaused(next);
    try {
      const r = await fetch(`${API_BASE}/api/${next ? 'pause' : 'resume'}`, { method: 'POST', headers: sessionHeaders() });
      if (!r.ok) {
        setPaused(!next);
        setErrorMsg(`Could not ${next ? 'pause' : 'resume'} the call (server returned ${r.status})`);
      }
    } catch (err) {
      setPaused(!next);
      setErrorMsg(`Could not ${next ? 'pause' : 'resume'} the call: ${err?.message || err}`);
    }
  };

  const sendChatQuestion = async (overrideText) => {
    const question = (overrideText ?? chatInput).trim();
    if (!question || chatSending) return;
    setChatSending(true);
    setChatInput('');
    setVoiceInterim('');
    try {
      await fetch(`${API_BASE}/api/ask`, {
        method: 'POST',
        headers: sessionHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ question }),
      });
    } catch (err) {
      setErrorMsg(`Chat error: ${err?.message || err}`);
    } finally {
      setChatSending(false);
    }
  };

  const startVoiceQuestion = async () => {
    if (voiceListening) {
      // Second click stops listening and submits whatever we have.
      try { recognitionRef.current?.stop(); } catch (_) {}
      return;
    }
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      setErrorMsg('Voice input requires Chrome or Edge (Web Speech API).');
      return;
    }
    // Cancel any currently-speaking TTS so the mic doesn't hear it.
    try { window.speechSynthesis?.cancel(); } catch (_) {}
    // Make sure we're paused so the call audio isn't competing.
    if (!paused) {
      try { await fetch(`${API_BASE}/api/pause`, { method: 'POST', headers: sessionHeaders() }); } catch (_) {}
      setPaused(true);
    }

    const recognition = new SR();
    recognition.lang = 'en-US';
    recognition.interimResults = true;
    recognition.continuous = false;
    recognition.maxAlternatives = 1;

    let finalText = '';
    recognition.onstart = () => {
      setVoiceListening(true);
      setVoiceInterim('');
    };
    recognition.onresult = (event) => {
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const res = event.results[i];
        if (res.isFinal) {
          finalText += res[0].transcript;
        } else {
          interim += res[0].transcript;
        }
      }
      setVoiceInterim((finalText + ' ' + interim).trim());
    };
    recognition.onerror = (event) => {
      setVoiceListening(false);
      if (event.error && event.error !== 'no-speech') {
        setErrorMsg(`Voice error: ${event.error}`);
      }
    };
    recognition.onend = () => {
      setVoiceListening(false);
      const q = finalText.trim();
      if (q) {
        sendChatQuestion(q);
      }
    };

    recognitionRef.current = recognition;
    try {
      recognition.start();
    } catch (err) {
      setErrorMsg(`Could not start voice input: ${err?.message || err}`);
      setVoiceListening(false);
    }
  };

  const endCallAndSummarize = async () => {
    if (summarizing) return;
    setSummarizing(true);
    // Stop streaming call audio so the agent's voice has the floor.
    if (audioStreamRef.current) {
      try { audioStreamRef.current.detachSource(); } catch (_) {}
    }
    // Safety net: if the WS `summary` event never arrives (network blip,
    // backend silently fails after returning ok), unstick the button after
    // 90 seconds so the user can retry instead of being locked out.
    if (summarizeTimerRef.current) window.clearTimeout(summarizeTimerRef.current);
    summarizeTimerRef.current = window.setTimeout(() => {
      summarizeTimerRef.current = null;
      setSummarizing((current) => {
        if (!current) return false;
        setErrorMsg('Summary is taking unusually long — try again.');
        return false;
      });
    }, 90000);
    try {
      const r = await fetch(`${API_BASE}/api/summarize`, { method: 'POST', headers: sessionHeaders() });
      const j = await r.json();
      if (!j.ok) {
        setErrorMsg(j.error || 'Summary failed');
        setSummarizing(false);
        if (summarizeTimerRef.current) {
          window.clearTimeout(summarizeTimerRef.current);
          summarizeTimerRef.current = null;
        }
      }
    } catch (err) {
      setErrorMsg(String(err?.message || err));
      setSummarizing(false);
      if (summarizeTimerRef.current) {
        window.clearTimeout(summarizeTimerRef.current);
        summarizeTimerRef.current = null;
      }
    }
  };

  const closeSummary = () => setSummary(null);

  const stopSession = useCallback(async () => {
    await teardownCapture();
    try { await fetch(`${API_BASE}/api/stop`, { method: 'POST', headers: sessionHeaders() }); } catch (_) {}
    setMode('idle');
    setSessionStatus('idle');
    setChatCollapsed(false);
  }, [teardownCapture]);

  const resetSession = async () => {
    await stopSession();
    resetState();
    // Clear the persisted "last company" so a reload returns to the empty hero
    // (recent tickers are kept — explicit "Clear recents" link removes those).
    writeJsonLS(LS_LAST, null);
  };

  /** True once we have a company in context. */
  const hasLoaded = !!identified.ticker;

  /** Tour steps. Targets reference data-tour="..." attributes in the JSX.
   *  Steps whose target isn't present are skipped silently — so the tour
   *  adapts naturally to whether the user is on the empty hero or has a
   *  company loaded. `before` callbacks set up app state (e.g. switching
   *  to the Trading tab) before a step is shown. */
  /** Stage-aware tour. Empty-hero and loaded states have completely
   *  different DOM. Returning a different array per stage means every step
   *  always has a real target — no silent skipping, no broken Back button. */
  const tourSteps = useMemo(() => {
    const welcomeStep = {
      target: null,
      placement: 'center',
      title: 'Welcome to EarningsEdge',
      body: (
        <>
          A real-time cockpit for any company audio — earnings calls, news segments,
          conference live streams, fireside chats. Pre-call coverage, live transcript,
          a committee-driven trade signal, and a paper-trading desk. About 60 seconds
          to walk through it.
        </>
      ),
    };
    const navStep = {
      target: '[data-tour="nav-bar"]',
      placement: 'bottom',
      title: 'Two workspaces, top of every page',
      body: (
        <>
          <strong>Company</strong> on the left holds research, transcript, and the trade
          signal. <strong>Trading</strong> on the right is your paper account, positions,
          orders, and chart. The active tab shows your loaded ticker; a yellow
          <em> PAPER</em> chip on Trading is a constant reminder nothing is real money.
        </>
      ),
    };
    const helpStep = {
      target: '[data-tour="help"]',
      placement: 'bottom-end',
      title: 'Re-run this tour anytime',
      body: (
        <>
          Click the <strong>?</strong> in the nav to bring this tour back. After
          a live call, hit <strong>End call &amp; summarize</strong> to generate
          a downloadable analyst PDF report.
        </>
      ),
    };

    if (!hasLoaded) {
      // Stage 1 — empty hero. Five focused steps, every target exists.
      return [
        welcomeStep,
        navStep,
        {
          target: '[data-tour="empty-form"]',
          placement: 'bottom',
          title: 'Pick a company',
          body: (
            <>
              Type a ticker like <strong>NVDA</strong> or a company name, then click
              <strong> Load company</strong>. Backend agents fan out to fundamentals,
              peers, news, macro and technicals — usually under a minute.
            </>
          ),
        },
        {
          target: '[data-tour="empty-voice"]',
          placement: 'top-end',
          title: 'Or speak the company name',
          body: (
            <>
              Click the mic, say something like <em>"NVIDIA Q4 fiscal 2024"</em>, then
              tap <strong>Done speaking</strong>. The server resolves the ticker and
              loads coverage automatically.
            </>
          ),
        },
        helpStep,
      ];
    }

    // Stage 2 — loaded. The sticky trade hero, context bar, sub-nav, and
    // panels are all visible; trading-tab steps switch tabs via `before`.
    return [
      welcomeStep,
      navStep,
      {
        target: '[data-tour="context-bar"]',
        placement: 'bottom',
        title: 'Your stage and context',
        body: (
          <>
            The cyan stage label tells you where you are —
            <em> pre-call coverage</em>, <em>live audio</em>, or briefing.
            The ticker, name and quarter sit beside it. <strong>Change company</strong>
            opens a small form; <strong>Reset</strong> takes two taps.
          </>
        ),
      },
      {
        target: '[data-tour="trade-hero"]',
        placement: 'bottom',
        title: 'The committee verdict',
        body: (
          <>
            Seven agents synthesize <strong>BUY / HOLD / SHORT</strong> with a
            confidence pill. The <em>How we got here</em> digest summarizes their
            reasoning. The green BUY and red SHORT buttons send <strong>paper</strong>
            orders to Alpaca — confirmation required, default 1 share at the last quote.
          </>
        ),
      },
      {
        target: '[data-tour="subnav"]',
        placement: 'bottom',
        title: 'Four focused views',
        body: (
          <>
            <strong>Overview</strong> — transcript and live metrics.
            <br /><strong>Peers &amp; News</strong> — competitors, analyst targets, headlines.
            <br /><strong>Macro &amp; Technical</strong> — rates, curves, indicators.
            <br /><strong>Committee</strong> — the agent votes behind the trade signal.
          </>
        ),
      },
      {
        target: '[data-tour="chat"]',
        placement: 'top-end',
        title: 'Ask the analyst',
        body: (
          <>
            Always here in the corner. Type a question or click the mic — we'll
            auto-pause the live call so the agent can hear you, then resume when
            you're done.
          </>
        ),
      },
      // ─── Trading tab — auto-switch then back ────────────────────────
      {
        target: '[data-tour="trading-account"]',
        placement: 'bottom',
        title: 'Trading desk · paper account',
        before: () => setAppTab('trading'),
        body: (
          <>
            Buying power, cash, and portfolio value from your Alpaca paper account.
            Refreshes every 15 seconds. <em>Paper money — no real funds at risk.</em>
          </>
        ),
      },
      {
        target: '[data-tour="trading-chart"]',
        placement: 'top',
        title: 'Chart',
        // Idempotent — keeps us on Trading even if Back jumps in here from
        // the help step (which has setAppTab('company')).
        before: () => setAppTab('trading'),
        body: (
          <>
            Embedded TradingView chart anchored to your loaded ticker. Use the
            timeframe pills above the chart to switch from <em>1m</em> through
            <em> 1D</em> candles.
          </>
        ),
      },
      {
        target: '[data-tour="trading-orders"]',
        placement: 'top',
        title: 'Orders & positions',
        before: () => setAppTab('trading'),
        body: (
          <>
            Recent orders on the left (with status badges) and open positions on the
            right (with unrealized P&amp;L). Orders submit from the Company tab —
            the BUY / SHORT buttons in the trade hero.
          </>
        ),
      },
      {
        ...helpStep,
        before: () => setAppTab('company'),
      },
    ];
  }, [hasLoaded, setAppTab]);

  /** Document-level Esc handler for the preflight modal. Attaching onKeyDown
   *  to a focus-less <div> doesn't reliably fire — focused children swallow
   *  the event. */
  useEffect(() => {
    if (!showPreflight) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        setShowPreflight(false);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [showPreflight]);

  const closeTour = useCallback((reason) => {
    setTourOpen(false);
    // Mark complete on either path — skipping is a deliberate user choice and
    // we don't want to nag on every page load.
    if (reason === 'completed' || reason === 'skipped') {
      try { window.localStorage.setItem(TOUR_DONE_KEY, '1'); } catch (_) {}
    }
  }, [TOUR_DONE_KEY]);

  /** Tour is now strictly opt-in via the in-app help button. The previous
   *  auto-launch on first ticker load blocked the verdict + agent panels
   *  with a modal overlay — bad first impression for hackathon judges. */
  // eslint-disable-next-line no-unused-vars
  const tourDone = true;

  /** Once every loading slice has arrived, the give-up timer is moot. */
  useEffect(() => {
    const allLoaded = Object.values(dataLoaded).every(Boolean);
    if (allLoaded && loadingStripTimerRef.current) {
      window.clearTimeout(loadingStripTimerRef.current);
      loadingStripTimerRef.current = null;
    }
  }, [dataLoaded]);

  /** App-unmount cleanup. Critical for the navigate-back-to-Landing flow:
   *  if the user backs out mid-call, we have to stop the MediaStream tracks
   *  (mic / tab capture) so the browser drops the "is recording" indicator
   *  and the AudioContext stops scheduling agent speech. Also kills any
   *  outstanding speech-recognition session and pending timers. */
  useEffect(() => () => {
    try { audioStreamRef.current?.disconnect(); } catch (_) {}
    try { audioPlayerRef.current?.dispose(); } catch (_) {}
    try { recognitionRef.current?.stop(); } catch (_) {}
    if (confirmResetTimerRef.current) window.clearTimeout(confirmResetTimerRef.current);
    if (summarizeTimerRef.current) window.clearTimeout(summarizeTimerRef.current);
    if (loadingStripTimerRef.current) window.clearTimeout(loadingStripTimerRef.current);
    if (voiceDoneFallbackRef.current) window.clearTimeout(voiceDoneFallbackRef.current);
    if (coverageAbortRef.current) {
      try { coverageAbortRef.current.abort(); } catch (_) {}
    }
  }, []);

  /** Persist current company across page reloads + maintain recent list. */
  useEffect(() => {
    if (!identified.ticker) return;
    writeJsonLS(LS_LAST, identified);
    setRecentTickers((prev) => {
      const next = [identified.ticker, ...prev.filter((t) => t !== identified.ticker)].slice(0, RECENT_MAX);
      writeJsonLS(LS_RECENT, next);
      return next;
    });
  }, [identified]);

  /** On first mount, if we hydrated a company from localStorage, kick off a fresh
   *  coverage fetch so the dashboards don't show stale or empty placeholders.
   *
   *  Race-condition guard: if the user resets (or changes company) WHILE this
   *  async fetch is in flight, applying the result would undo their action and
   *  re-save the old ticker. So we re-read LS_LAST after the fetch and abandon
   *  if it no longer matches the ticker we started with. */
  const didRehydrateRef = useRef(false);
  useEffect(() => {
    if (didRehydrateRef.current) return;
    didRehydrateRef.current = true;
    if (!identified.ticker) return;

    const startedTicker = identified.ticker;
    const startedCompany = identified.company_name;
    const startedSector = identified.sector;
    const startedQuarter = identified.quarter;
    const startedFY = identified.fiscal_year;
    let cancelled = false;

    // Coordinate with the same abort/timeout machinery the explicit Load uses,
    // so a Reset during rehydrate cancels both legitimately.
    if (coverageAbortRef.current) {
      try { coverageAbortRef.current.abort(); } catch (_) {}
    }
    const ac = new AbortController();
    coverageAbortRef.current = ac;
    clearPanelDataForLoad();

    (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/coverage`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ticker: startedTicker,
            company_name: startedCompany || null,
            quarter: startedQuarter || null,
            year: startedFY || null,
          }),
          signal: ac.signal,
        });
        const j = await r.json();
        if (cancelled || ac.signal.aborted) return;

        // The user may have hit Reset (which clears LS_LAST) or loaded a
        // different company while we were waiting on the network. Either way,
        // applying our stale result would clobber their action.
        const stillCached = readJsonLS(LS_LAST);
        if (!stillCached || stillCached.ticker !== startedTicker) return;

        if (j && j.ok) {
          if (j.company) {
            setIdentified({
              ticker: j.company.ticker || startedTicker,
              company_name: j.company.company_name || startedCompany,
              sector: j.company.sector || startedSector,
              quarter: j.company.quarter || startedQuarter,
              fiscal_year: j.company.fiscal_year ?? startedFY,
            });
          }
          setAnalystOpinion(
            j.analyst_opinion != null && typeof j.analyst_opinion === 'object' ? j.analyst_opinion : {},
          );
          setMode('ready');
        }
      } catch (err) {
        if (err?.name === 'AbortError') return;
        // Silent — user will see the cached context bar; if data never arrives
        // they can hit Change company → Load to retry explicitly.
      } finally {
        if (coverageAbortRef.current === ac) coverageAbortRef.current = null;
      }
    })();

    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadRecent = async (ticker) => {
    if (!ticker || coverageLoading) return;
    setCoverageForm({ ticker, company_name: '', quarter: '', year: '' });
    if (coverageAbortRef.current) {
      try { coverageAbortRef.current.abort(); } catch (_) {}
    }
    const ac = new AbortController();
    coverageAbortRef.current = ac;
    setCoverageLoading(true);
    setErrorMsg(null);
    clearPanelDataForLoad();
    try {
      const r = await fetch(`${API_BASE}/api/coverage`, {
        method: 'POST',
        headers: sessionHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ ticker, company_name: null, quarter: null, year: null }),
        signal: ac.signal,
      });
      const j = await r.json();
      if (ac.signal.aborted) return;
      if (!j.ok) throw new Error(j.error || 'Could not load company coverage');
      if (j.company) {
        setIdentified({
          ticker: j.company.ticker || ticker,
          company_name: j.company.company_name || null,
          sector: j.company.sector || null,
          quarter: j.company.quarter || null,
          fiscal_year: j.company.fiscal_year ?? null,
        });
      }
      setAnalystOpinion(
        j.analyst_opinion != null && typeof j.analyst_opinion === 'object' ? j.analyst_opinion : {},
      );
      setAnalystOpinionError(null);
      setMode('ready');
    } catch (err) {
      if (err?.name === 'AbortError') return;
      setErrorMsg(String(err?.message || err));
    } finally {
      if (coverageAbortRef.current === ac) coverageAbortRef.current = null;
      setCoverageLoading(false);
    }
  };

  const clearRecents = () => {
    setRecentTickers([]);
    writeJsonLS(LS_RECENT, []);
  };
  const stageLabel = mode === 'briefing'
    ? 'Listening for company name…'
    : mode === 'listening'
      ? 'Live audio'
      : hasLoaded ? 'Pre-call coverage' : 'Pick a company to begin';

  const handleResetClick = () => {
    // Always clear any pending auto-cancel timer first so previous timers
    // don't fire after the user has re-entered the flow.
    if (confirmResetTimerRef.current) {
      window.clearTimeout(confirmResetTimerRef.current);
      confirmResetTimerRef.current = null;
    }
    if (!confirmReset) {
      setConfirmReset(true);
      confirmResetTimerRef.current = window.setTimeout(() => {
        confirmResetTimerRef.current = null;
        setConfirmReset(false);
      }, 4000);
      return;
    }
    setConfirmReset(false);
    resetSession();
  };

  return (
    <div className={`app ${appTab === 'company' && !hasLoaded ? 'app--empty' : ''}`}>
      <div className="app-sticky-shell">
        <nav className="app-top-nav" aria-label="Primary">
          <div className="app-top-nav-inner">
            <button
              type="button"
              className="app-top-nav-brand app-top-nav-brand-link"
              data-tour="brand"
              onClick={() => onBackToLanding?.()}
              title="Back to landing page"
              aria-label="EarningsEdge — back to landing page"
            >
              <span className="app-top-nav-logo-mark" aria-hidden="true">
                <svg viewBox="0 0 20 20" width="22" height="22" fill="none">
                  <path
                    d="M3 14l4-5 3 3 4-7"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  <circle cx="14" cy="5" r="1.6" fill="currentColor" />
                </svg>
              </span>
              <span className="app-top-nav-logo">
                Earnings<span className="app-top-nav-logo-edge">Edge</span>
              </span>
            </button>
            <div className="app-top-nav-links" role="tablist" data-tour="nav-bar">
              <button
                type="button"
                role="tab"
                aria-selected={appTab === 'company'}
                className={`app-nav-pill ${appTab === 'company' ? 'app-nav-pill-active' : ''}`}
                onClick={() => setAppTab('company')}
              >
                <span className="app-nav-pill-icon" aria-hidden="true">
                  <svg viewBox="0 0 18 18" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
                    <path d="M3 14V9" />
                    <path d="M9 14V5" />
                    <path d="M15 14V11" />
                    <path d="M2 17h14" />
                  </svg>
                </span>
                <span className="app-nav-pill-text">
                  <span className="app-nav-pill-label">Company</span>
                  <span className="app-nav-pill-sub">Research &amp; live call</span>
                </span>
                {appTab === 'company' && hasLoaded && (
                  <span className="app-nav-pill-chip" title={identified.company_name || identified.ticker}>
                    {identified.ticker}
                  </span>
                )}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={appTab === 'trading'}
                className={`app-nav-pill ${appTab === 'trading' ? 'app-nav-pill-active' : ''}`}
                onClick={() => setAppTab('trading')}
              >
                <span className="app-nav-pill-icon" aria-hidden="true">
                  <svg viewBox="0 0 18 18" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 12l4-4 3 3 4-6" />
                    <path d="M11 5h4v4" />
                  </svg>
                </span>
                <span className="app-nav-pill-text">
                  <span className="app-nav-pill-label">Trading</span>
                  <span className="app-nav-pill-sub">Paper account &amp; orders</span>
                </span>
                <span className="app-nav-pill-chip app-nav-pill-chip--paper">PAPER</span>
              </button>
              <button
                type="button"
                className="app-nav-help is-pulsing"
                data-tour="help"
                onClick={() => setTourOpen(true)}
                title="Take the 90-second tour"
                aria-label="Take the 90-second tour"
              >
                <span aria-hidden="true" className="app-nav-help-icon">?</span>
                <span className="app-nav-help-label">Tour</span>
              </button>
            </div>
          </div>
        </nav>
        {/* Legacy TradeSignalHero hidden — the ADK Chairman card below
            is the canonical verdict so the user isn't choosing between
            two competing labels (yellow HOLD vs green STRONG_BUY). */}
      </div>

      {appTab === 'trading' ? (
        <TradingPanel
          ticker={identified.ticker}
          companyName={identified.company_name}
          refreshKey={tradingRefreshKey}
          sessionStartIso={sessionStartIso}
        />
      ) : null}

      {appTab === 'company' && !hasLoaded ? (
        // ============= STAGE 1 — empty / briefing (SIMPLIFIED v3) =============
        <section className="company-empty company-empty--v3" aria-labelledby="empty-heading">
          <h1 id="empty-heading" className="company-empty-title">
            Sleep through earnings calls.<br/>
            <span className="company-empty-title-accent">Wake up with conviction.</span>
          </h1>
          <p className="company-empty-lede">
            Five named-investor agents — Cathie Wood, Michael Burry, Stan Druckenmiller,
            Jim Cramer, Howard Marks — debate every call you miss. Atlas Vector Search
            remembers every prior verdict.
          </p>

          {/* Loading banner — pinned at the top of the page while coverage
              is fetching. Without this the user clicks a ticker and stares
              at unchanged UI for 10-15 seconds. */}
          {coverageLoading && (
            <div className="coverage-loading-banner" role="status" aria-live="polite">
              <span className="coverage-loading-banner__spinner" />
              <div>
                <strong>Loading {coverageForm.ticker || 'company'} coverage…</strong>
                <div className="coverage-loading-banner__hint">
                  Fetching fundamentals, peers, analyst consensus, news sentiment,
                  macro context, and last year's verdicts — usually 10-15 seconds.
                </div>
              </div>
            </div>
          )}

          {/* Watchlist + briefing — the primary entry point. Click a chip to load. */}
          <MorningBriefingPanel
            onPickTicker={(t) => {
              setCoverageForm({ ticker: t, company_name: '', quarter: '', year: '' });
              // Pass the ticker explicitly so we don't depend on the setState
              // having flushed before the submit fires.
              submitCoverage(null, { ticker: t });
            }}
          />

          {/* Custom-ticker form collapsed by default — most users use the watchlist. */}
          <details className="company-empty-custom">
            <summary>…or load a custom ticker</summary>
            <form className="company-empty-form" onSubmit={submitCoverage}>
              <input
                id="empty-coverage-ticker"
                name="ee-ticker-search"
                type="text"
                className="coverage-input company-empty-input-primary"
                placeholder="NVDA"
                value={coverageForm.ticker}
                onChange={(e) => setCoverageForm((f) => ({ ...f, ticker: e.target.value }))}
                maxLength={12}
                autoCapitalize="characters"
                disabled={mode === 'briefing'}
                autoComplete="off"
                autoCorrect="off"
                spellCheck="false"
                data-1p-ignore="true"
                data-lpignore="true"
                data-form-type="other"
              />
              <button
                type="submit"
                className="btn btn-primary"
                disabled={coverageLoading || mode === 'briefing'}
              >
                {coverageLoading ? 'Loading…' : 'Load →'}
              </button>
            </form>
          </details>

          {errorMsg && (
            <div className="error-banner" role="alert">
              <strong>Error:</strong> {errorMsg}
            </div>
          )}
        </section>
      ) : null}

      {appTab === 'company' && hasLoaded ? (
        // ============= STAGE 2 / 3 — loaded coverage or live call =============
        <>
        <section className="company-context-bar" aria-label="Company context" data-tour="context-bar">
          <div className="ccb-left">
            <div className={`ccb-stage ccb-stage-${mode}`}>
              <span className={`status-dot ${sessionStatus}`} />
              <span className="ccb-stage-label">{stageLabel}</span>
            </div>
            <div className="ccb-ticker">
              <span className="ccb-ticker-symbol">{identified.ticker}</span>
              {identified.company_name && (
                <span className="ccb-ticker-name">· {identified.company_name}</span>
              )}
              {(identified.quarter || identified.fiscal_year) && (
                <span className="ccb-ticker-period">
                  · {[identified.quarter, identified.fiscal_year].filter(Boolean).join(' ')}
                </span>
              )}
            </div>
            {mode === 'listening' && activeSource === 'tab' && (
              <span className="live-badge" role="status" aria-live="polite">
                <span className="live-dot" />
                LIVE · Tab Audio
              </span>
            )}
            {mode === 'listening' && activeSource === 'mic' && (
              <span className="live-badge mic-listen" role="status" aria-live="polite">
                <span className="live-dot" />
                LISTENING FOR YOUR QUESTION
              </span>
            )}
            {agentSpeaking && (
              <span className="agent-speaking" role="status" aria-live="polite">
                <span className="speak-dot" /><span className="speak-dot" /><span className="speak-dot" />
                Agent speaking
              </span>
            )}
          </div>
          <div className="ccb-actions">
            {!showChangeCompany && mode !== 'listening' && mode !== 'briefing' && (
              <button
                type="button"
                className="btn btn-ghost ccb-change"
                onClick={() => setShowChangeCompany(true)}
                title="Load a different company"
              >
                Change company
              </button>
            )}
            {/* Top-bar primary CTA — but only when there's NO active session.
                If there's a transcript already, the End-call button in the
                Live audio tab is the canonical action; showing Listen-live
                here too creates two conflicting CTAs. */}
            {mode === 'ready' && transcript.length === 0 && sessionStatus !== 'disconnected' && (
              <button
                type="button"
                className="btn btn-primary ccb-primary"
                data-tour="primary-cta"
                onClick={requestStartEarningsCall}
                disabled={sessionStatus === 'connecting' || !geminiLive.available}
                title={
                  !geminiLive.available
                    ? `Live audio unavailable: ${geminiLive.error || 'Gemini Live not reachable on the current GEMINI_API_KEY'}`
                    : 'Share any browser tab playing audio — earnings webcast, news segment, conference stream, fireside chat'
                }
              >
                {sessionStatus === 'connecting' ? (
                  'Connecting…'
                ) : !geminiLive.available ? (
                  'Live audio unavailable'
                ) : (
                  <><span aria-hidden="true">▶</span> Listen live</>
                )}
              </button>
            )}
            {/* Reconnect button when session was disconnected. Replaces the
                Listen-live position so we never show 2 conflicting CTAs. */}
            {sessionStatus === 'disconnected' && (
              <button
                type="button"
                className="btn btn-primary ccb-primary"
                onClick={requestStartEarningsCall}
                disabled={!geminiLive.available}
                title="Reconnect to keep listening — the transcript so far is preserved"
              >
                <span aria-hidden="true">↻</span> Reconnect
              </button>
            )}
            {mode === 'briefing' && (
              <button
                type="button"
                className="btn btn-primary ccb-primary"
                onClick={finishVoiceBriefing}
              >
                ⏹ Done speaking
              </button>
            )}
            {mode === 'listening' && (
              <>
                <button
                  className="btn btn-ghost"
                  onClick={togglePause}
                  title={paused ? 'Resume listening to the call' : 'Pause listening so you can ask a question'}
                >
                  {paused ? (
                    <><span aria-hidden="true">▶</span> Resume</>
                  ) : (
                    <><span aria-hidden="true">⏸</span> Pause</>
                  )}
                </button>
                <button
                  className="btn btn-primary ccb-primary"
                  onClick={endCallAndSummarize}
                  disabled={summarizing}
                  title="Generate the analyst report"
                >
                  {summarizing ? 'Generating…' : 'End call & summarize'}
                </button>
              </>
            )}
            <button
              type="button"
              className="btn btn-ghost ccb-tour is-pulsing"
              onClick={() => setTourOpen(true)}
              title="Take the 90-second guided tour"
              aria-label="Take the 90-second guided tour"
            >
              <span aria-hidden="true">?</span> Tour
            </button>
            <button
              className={`btn ${confirmReset ? 'btn-danger' : 'btn-ghost'} ccb-reset`}
              onClick={handleResetClick}
              aria-label={confirmReset ? 'Confirm reset session' : 'Reset session'}
              title={confirmReset ? 'Click again within 4 s to clear everything' : 'Reset session'}
            >
              {confirmReset ? 'Click again to reset' : (
                <><span aria-hidden="true">↻</span> Reset</>
              )}
            </button>
          </div>
        </section>

        {showChangeCompany && (
          <section className="coverage-panel" aria-labelledby="change-coverage-heading">
            <div className="coverage-panel-head">
              <h2 id="change-coverage-heading" className="card-title">Change company</h2>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setShowChangeCompany(false)}
                aria-label="Close change-company form"
              >
                <span aria-hidden="true">✕</span> Close
              </button>
            </div>
            <form
              className="coverage-form"
              onSubmit={(e) => {
                submitCoverage(e);
                setShowChangeCompany(false);
              }}
            >
              <label htmlFor="change-coverage-ticker" className="visually-hidden">Ticker symbol</label>
              <input
                id="change-coverage-ticker"
                name="ee-ticker-search-change"
                type="text"
                className="coverage-input"
                placeholder="Ticker (e.g. NVDA) — optional if name below"
                value={coverageForm.ticker}
                onChange={(e) => setCoverageForm((f) => ({ ...f, ticker: e.target.value }))}
                maxLength={12}
                autoCapitalize="characters"
                autoComplete="off"
                autoCorrect="off"
                spellCheck="false"
                data-1p-ignore="true"
                data-lpignore="true"
                data-form-type="other"
              />
              <label htmlFor="change-coverage-company" className="visually-hidden">Company name</label>
              <input
                id="change-coverage-company"
                name="ee-company-search-change"
                type="text"
                className="coverage-input coverage-input-grow"
                placeholder="Or company name"
                value={coverageForm.company_name}
                onChange={(e) => setCoverageForm((f) => ({ ...f, company_name: e.target.value }))}
                autoComplete="off"
                autoCorrect="off"
                spellCheck="false"
                data-1p-ignore="true"
                data-lpignore="true"
                data-form-type="other"
              />
              <label htmlFor="change-coverage-quarter" className="visually-hidden">Fiscal quarter</label>
              <input
                id="change-coverage-quarter"
                name="ee-quarter-search-change"
                type="text"
                className="coverage-input coverage-input-sm"
                placeholder="Q1–Q4"
                value={coverageForm.quarter}
                onChange={(e) => setCoverageForm((f) => ({ ...f, quarter: e.target.value }))}
                autoComplete="off"
                autoCorrect="off"
                spellCheck="false"
                data-1p-ignore="true"
                data-lpignore="true"
              />
              <label htmlFor="change-coverage-year" className="visually-hidden">Fiscal year</label>
              <input
                id="change-coverage-year"
                name="ee-year-search-change"
                type="text"
                className="coverage-input coverage-input-sm"
                placeholder="Year"
                value={coverageForm.year}
                onChange={(e) => setCoverageForm((f) => ({ ...f, year: e.target.value }))}
                autoComplete="off"
                autoCorrect="off"
                spellCheck="false"
                data-1p-ignore="true"
                data-lpignore="true"
              />
              <button type="submit" className="btn btn-primary" disabled={coverageLoading}>
                {coverageLoading ? 'Loading…' : 'Load company'}
              </button>
            </form>
          </section>
        )}

        {(() => {
          const slices = [
            { key: 'metrics', label: 'Fundamentals' },
            { key: 'competitors', label: 'Peers' },
            { key: 'news', label: 'News' },
            { key: 'macro', label: 'Macro' },
            { key: 'technical', label: 'Technicals' },
            { key: 'analyst', label: 'Analyst' },
          ];
          const pending = slices.filter((s) => !dataLoaded[s.key]);
          const loaded = slices.filter((s) => dataLoaded[s.key]);
          if (pending.length === 0) return null;
          return (
            <div className="loading-strip" role="status" aria-live="polite" data-tour="loading-strip">
              <span className="loading-strip-spinner" aria-hidden="true" />
              <span className="loading-strip-label">
                Loading {pending.map((s) => s.label).join(' · ')}
              </span>
              {loaded.length > 0 && (
                <span className="loading-strip-done">
                  · {loaded.length} of {slices.length} ready
                </span>
              )}
            </div>
          );
        })()}

        {mode === 'ready' && (
          <div className="dashboard-welcome-strip" role="status">
            <span>
              Coverage is loaded. Tap <strong>Listen live</strong> above when an earnings webcast,
              news segment, or conference stream starts; we'll transcribe and refresh the
              dashboards live.
            </span>
          </div>
        )}

        {errorMsg && (
          <div className="error-banner" role="alert">
            <strong>Error:</strong> {errorMsg}
          </div>
        )}

        {/* === COCKPIT v5 — proper tab navigation =========================
            Six clearly-named tabs, each with a distinct purpose.
            Verdict is the default landing tab. Everything is one click. */}

        <div className="cockpit-tabs" role="tablist" aria-label="Cockpit view">
          {[
            { id: 'verdict',   label: 'Verdict',     hint: 'Agent synthesis — what to do' },
            { id: 'committee', label: 'Committee',   hint: 'Weighted vote of 8 specialists' },
            { id: 'live',      label: 'Live audio',  hint: 'Transcript · persona pulse · pattern matches' },
            { id: 'memory',    label: 'Memory',      hint: 'Atlas Vector Search · prior verdicts' },
            { id: 'sentiment', label: 'Sentiment',   hint: 'News sentiment · ranked headlines' },
            { id: 'coverage',  label: 'Coverage',    hint: 'Peers · analyst · macro · technicals' },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={companyView === tab.id}
              className={`cockpit-tab ${companyView === tab.id ? 'is-active' : ''}`}
              onClick={() => setCompanyView(tab.id)}
              title={tab.hint}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* ----- VERDICT TAB (default) ----- */}
        {companyView === 'verdict' && (
          <div className="cockpit-pane">
            <ChairmanADKPanel ticker={identified?.ticker} />
            <PersonaPulsePanel ticker={identified?.ticker} transcript={transcript} />
          </div>
        )}

        {/* ----- COMMITTEE TAB ----- */}
        {companyView === 'committee' && (
          <div className="cockpit-pane">
            {tradeSignal ? (
              <>
                <TradeSignalHero
                  signal={tradeSignal}
                  fresh={signalFresh}
                  livePrice={livePrice}
                  ticker={identified.ticker}
                  onOrderSuccess={() => setTradingRefreshKey((k) => k + 1)}
                />
                <CommitteeView tradeSignal={tradeSignal} variant="full" />
              </>
            ) : (
              <div className="cockpit-empty">Committee verdict is generating — give it ~15 seconds after loading a ticker.</div>
            )}
          </div>
        )}

        {/* ----- LIVE AUDIO TAB ----- */}
        {companyView === 'live' && (
          <div className="cockpit-pane">
            {/* Disconnected banner — surfaces a recovered session after the
                WS audio drops (Heroku H15 timeout, network blip, etc.). */}
            {sessionStatus === 'disconnected' && (
              <div className="disconnected-banner" role="alert">
                <div>
                  <strong>⚠ Audio stream disconnected</strong>
                  <div className="disconnected-banner__hint">
                    The transcript above is preserved. Click <strong>Reconnect</strong> to
                    keep listening, or <strong>End call</strong> to wrap up with the agent
                    summary.
                  </div>
                </div>
                <button
                  className="btn btn-primary"
                  onClick={() => requestStartEarningsCall()}
                  disabled={sessionStatus === 'connecting'}
                >
                  ↻ Reconnect
                </button>
              </div>
            )}

            {/* === Always-visible live session control bar ===
                Shows End call & summarize whenever there's an active session
                OR transcript content to summarize, not just when the strict
                mode==='listening' flag is set. That flag depends on a WS phase
                event that doesn't always fire reliably. */}
            <div className="live-control-bar">
              <div className="live-control-bar__left">
                {mode === 'listening' ? (
                  <>
                    <span className="live-control-bar__dot" />
                    <strong>Live</strong>
                    <span className="live-control-bar__hint">
                      Streaming audio · {transcript.length} transcript line{transcript.length === 1 ? '' : 's'} captured
                    </span>
                  </>
                ) : mode === 'paused' ? (
                  <>
                    <span className="live-control-bar__dot live-control-bar__dot--paused" />
                    <strong>Paused</strong>
                    <span className="live-control-bar__hint">
                      Audio stream is paused — agent will resume scoring on Resume
                    </span>
                  </>
                ) : transcript.length > 0 || sessionStatus === 'running' ? (
                  <>
                    <span className="live-control-bar__dot" />
                    <strong>Session active</strong>
                    <span className="live-control-bar__hint">
                      {transcript.length} transcript line{transcript.length === 1 ? '' : 's'} captured · click <em>End call</em> to generate the analyst summary
                    </span>
                  </>
                ) : (
                  <>
                    <strong>Live audio session</strong>
                    <span className="live-control-bar__hint">
                      Click <em>Listen live</em> in the top bar to start streaming audio
                    </span>
                  </>
                )}
              </div>
              <div className="live-control-bar__actions">
                {/* End call shows whenever there's transcript content to
                    summarize OR any non-idle session state. */}
                {(mode === 'listening' || mode === 'paused' || transcript.length > 0 || sessionStatus === 'running') && (
                  <>
                    {(mode === 'listening' || mode === 'paused') && (
                      <button
                        className="btn btn-ghost"
                        onClick={togglePause}
                        title={paused ? 'Resume listening to the call' : 'Pause listening so you can ask a question'}
                      >
                        {paused ? '▶ Resume' : '⏸ Pause'}
                      </button>
                    )}
                    <button
                      className="btn btn-primary live-control-bar__end"
                      onClick={endCallAndSummarize}
                      disabled={summarizing}
                      title="End the call and generate the analyst summary report"
                    >
                      {summarizing ? '⏳ Generating summary…' : '⏹ End call & generate summary'}
                    </button>
                  </>
                )}
              </div>
            </div>

            <PersonaPulsePanel ticker={identified?.ticker} transcript={transcript} />
            <PatternMatchesPanel ticker={identified?.ticker} transcript={transcript} />
            <div className="dashboard-grid dashboard-grid--overview">
              <div className="col col-left">
                <TranscriptPanel
                  transcript={transcript}
                  transcriptPartial={transcriptPartial}
                  mode={mode}
                  audioMeter={audioMeter}
                  highlightLexicon={highlightLexicon}
                />
              </div>
              <div className="col col-center">
                <MetricsPanel metrics={metrics} />
              </div>
            </div>
          </div>
        )}

        {/* ----- MEMORY TAB ----- */}
        {companyView === 'memory' && (
          <div className="cockpit-pane">
            <PatternAlertsPanel ticker={identified?.ticker} />
            <PatternMatchesPanel ticker={identified?.ticker} transcript={transcript} />
          </div>
        )}

        {/* ----- SENTIMENT TAB ----- */}
        {companyView === 'sentiment' && (
          <div className="cockpit-pane">
            {(sentiment && (sentiment.material_count || 0) > 0) ? (
              <SentimentGauge sentiment={sentiment} analystOpinion={analystOpinion} />
            ) : (
              <div className="cockpit-empty">Sentiment gauge populates once the news pipeline returns rated articles.</div>
            )}
            <NewsDigestPanel ticker={identified?.ticker} />
            <NewsPanel
              news={news}
              overall={newsOverall}
              overallRationale={newsOverallRationale}
            />
          </div>
        )}

        {/* ----- COVERAGE TAB (peers, analyst, macro, technicals) ----- */}
        {companyView === 'coverage' && (
          <div className="cockpit-pane">
            <CompetitorPanel
              competitors={competitors}
              target={identified.ticker}
              peerValuation={peerValuation}
            />
            <AnalystPanel opinion={analystOpinion} opinionError={analystOpinionError} />
            <MacroPanel data={macroData} />
            <TechnicalPanel data={technicalData} />
          </div>
        )}
        </>
      ) : null}

      <SummaryPanel summary={summary} onClose={closeSummary} />

      <OnboardingTour open={tourOpen} steps={tourSteps} onClose={closeTour} />

      {showPreflight && (
        <div
          className="modal-overlay"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setShowPreflight(false);
          }}
          onKeyDown={(e) => { if (e.key === 'Escape') setShowPreflight(false); }}
        >
          <div
            className="preflight-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="preflight-title"
            tabIndex={-1}
          >
            <div className="preflight-eyebrow">Before you start the call</div>
            <h2 id="preflight-title" className="preflight-title">
              Share the browser tab playing the webcast
            </h2>
            <p className="preflight-lede">
              You'll see a browser dialog next. Pick the tab that's playing the earnings webcast,
              and <strong>tick "Share tab audio"</strong> — without that, we'll receive bytes but no
              audio and the transcript will stay empty.
            </p>

            <ol className="preflight-steps">
              <li>
                <span className="preflight-step-num">1</span>
                <div className="preflight-step-body">
                  <strong>Choose the right tab</strong>
                  <span className="preflight-step-hint">
                    The Chrome / Edge tab that already has the webcast loaded and audible.
                  </span>
                </div>
              </li>
              <li>
                <span className="preflight-step-num">2</span>
                <div className="preflight-step-body">
                  <strong>Tick "Share tab audio"</strong>
                  <span className="preflight-step-hint">
                    Lower-left corner of the share dialog. This is the easiest step to miss.
                  </span>
                </div>
              </li>
              <li>
                <span className="preflight-step-num">3</span>
                <div className="preflight-step-body">
                  <strong>Click "Share"</strong>
                  <span className="preflight-step-hint">
                    The transcript will start scrolling within a few seconds.
                  </span>
                </div>
              </li>
            </ol>

            <label className="preflight-skip">
              <input
                type="checkbox"
                checked={skipPreflight}
                onChange={(e) => setSkipPreflightAndStore(e.target.checked)}
              />
              <span>Don't show this again</span>
            </label>

            <div className="preflight-actions">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setShowPreflight(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={confirmPreflightStart}
                autoFocus
              >
                I'm ready — start sharing
              </button>
            </div>
          </div>
        </div>
      )}

      {hasLoaded && appTab === 'company' && chatCollapsed && (
        <button
          type="button"
          className="chat-minimized-bar"
          data-tour="chat"
          onClick={() => setChatCollapsed(false)}
          title="Open Ask the analyst"
          aria-label={
            chatLog.length > 0
              ? `Open Ask the analyst (${Math.min(chatLog.length, 99)} messages)`
              : 'Open Ask the analyst'
          }
        >
          <span><span aria-hidden="true">💬</span> Ask the analyst</span>
          {chatLog.length > 0 && (
            <span className="chat-minimized-badge" aria-hidden="true">
              {Math.min(chatLog.length, 99)}
            </span>
          )}
        </button>
      )}
      {hasLoaded && appTab === 'company' && !chatCollapsed && (
        <div className={`chat-widget ${chatLog.length > 0 ? 'has-messages' : ''}`} data-tour="chat">
          <div className="chat-header">
            <span>💬 Ask the analyst</span>
            <div className="chat-header-right">
              {paused && <span className="chat-paused-pill">CALL PAUSED</span>}
              {voiceListening && <span className="chat-listening-pill">🎤 LISTENING</span>}
              <button
                type="button"
                className="btn chat-minimize"
                onClick={() => setChatCollapsed(true)}
                title="Minimize chat"
                aria-label="Minimize chat"
              >
                <span aria-hidden="true">−</span>
              </button>
            </div>
          </div>
          {chatLog.length > 0 && (
            <div className="chat-log">
              {chatLog.slice(-6).map((m) => (
                <div key={m._id} className={`chat-msg chat-${m.role}`}>
                  <span className="chat-role">{m.role === 'user' ? 'You' : 'Agent'}</span>
                  <div className="chat-text">{m.text}</div>
                </div>
              ))}
            </div>
          )}
          {voiceListening && voiceInterim && (
            <div className="chat-interim">{voiceInterim}<span className="typing-indicator">▋</span></div>
          )}
          <form
            className="chat-input-row"
            onSubmit={(e) => {
              e.preventDefault();
              sendChatQuestion();
            }}
          >
            <button
              type="button"
              className={`btn chat-mic ${voiceListening ? 'chat-mic-active' : ''}`}
              onClick={startVoiceQuestion}
              title={
                voiceListening
                  ? 'Click to stop recording and send'
                  : 'Click to speak your question (pauses the call)'
              }
              aria-label={
                voiceListening
                  ? 'Stop recording and send your question'
                  : 'Speak your question'
              }
              aria-pressed={voiceListening}
            >
              <span aria-hidden="true">{voiceListening ? '⏹' : '🎤'}</span>
            </button>
            <input
              type="text"
              className="chat-input"
              placeholder={
                voiceListening
                  ? 'Listening…'
                  : paused
                    ? 'Ask anything about the call (or click the mic)'
                    : 'Ask a question (click the mic to talk or Pause to focus)'
              }
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              disabled={chatSending || voiceListening}
            />
            <button
              type="submit"
              className="btn btn-primary chat-send"
              disabled={chatSending || voiceListening || !chatInput.trim()}
            >
              {chatSending ? '…' : 'Ask'}
            </button>
          </form>
        </div>
      )}
    </div>
  );
}

export default App;
