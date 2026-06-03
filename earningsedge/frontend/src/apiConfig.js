/**
 * API / WebSocket base URLs + per-tab session id.
 * - Local dev: CRA talks to backend on :8000 (see REACT_APP_API_BASE).
 *   Default base is 127.0.0.1 (not "localhost") so Windows resolves IPv4 first;
 *   ::1:8000 is often Docker/WSL, not uvicorn.
 * - Production (e.g. Docker): same origin — relative HTTP and wss to current host.
 */

export function getApiBase() {
  const env = process.env.REACT_APP_API_BASE;
  if (env !== undefined && env !== '') {
    return env.replace(/\/$/, '');
  }
  if (process.env.NODE_ENV === 'development') {
    return 'http://127.0.0.1:8000';
  }
  return '';
}

/**
 * Per-tab session id. Stored in sessionStorage (not localStorage) so each tab
 * gets its own. Backend stamps every WS broadcast with the session id of the
 * tab that triggered the action; the frontend ignores events whose session_id
 * doesn't match. This stops cross-tab and cross-user state leaks: User A
 * loading NVDA no longer flips User B's loaded ticker mid-session.
 */
function _generateUuid() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  // Fallback for older browsers.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

const SESSION_KEY = 'earningsedge.session_id';
let _cachedSessionId = null;

export function getSessionId() {
  if (_cachedSessionId) return _cachedSessionId;
  if (typeof window === 'undefined' || !window.sessionStorage) {
    _cachedSessionId = _generateUuid();
    return _cachedSessionId;
  }
  let id = window.sessionStorage.getItem(SESSION_KEY);
  if (!id) {
    id = _generateUuid();
    try { window.sessionStorage.setItem(SESSION_KEY, id); } catch (_) {}
  }
  _cachedSessionId = id;
  return id;
}

/** Headers that every /api/* request should include so the server can
 *  route resulting WS broadcasts back to this tab only. */
export function sessionHeaders(extra = {}) {
  return { 'X-Session-Id': getSessionId(), ...extra };
}

/** @param {string} path e.g. '/ws' or '/ws/audio' */
export function getWsUrl(path) {
  const base = getApiBase();
  const sid = getSessionId();
  const sep = path.includes('?') ? '&' : '?';
  const tagged = `${path}${sep}session_id=${encodeURIComponent(sid)}`;
  if (base) {
    const wsRoot = base.replace(/^http/, 'ws');
    return `${wsRoot}${tagged.startsWith('/') ? tagged : `/${tagged}`}`;
  }
  const proto = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = typeof window !== 'undefined' ? window.location.host : 'localhost:8000';
  return `${proto}//${host}${tagged.startsWith('/') ? tagged : `/${tagged}`}`;
}
