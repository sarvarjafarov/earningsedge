import React, { useCallback, useEffect, useState } from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import Landing from './Landing';

/**
 * Lightweight history-based router. Cockpit-first:
 *   /            → App (the cockpit) — what hackathon judges + users see
 *   /marketing   → Landing (the legacy marketing page, kept for reference)
 *
 * Previously `/` was the marketing wall and the cockpit was behind a
 * click — this hid the new named-investor sub-agents, the morning
 * briefing, and Atlas Vector Search memory engine. The cockpit is the
 * product; the landing is supplementary.
 */
function Router() {
  const [path, setPath] = useState(() => window.location.pathname || '/');

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname || '/');
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = useCallback((to) => {
    if (!to || to === window.location.pathname) return;
    window.history.pushState({}, '', to);
    setPath(to);
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
  }, []);

  if (path.startsWith('/marketing')) {
    return <Landing onOpenApp={() => navigate('/')} />;
  }
  return <App onBackToLanding={() => navigate('/marketing')} />;
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <React.StrictMode>
    <Router />
  </React.StrictMode>
);
