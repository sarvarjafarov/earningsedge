import React, { useCallback, useEffect, useState } from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import Landing from './Landing';

/**
 * Lightweight history-based router. Two routes only:
 *   /     → Landing (marketing page)
 *   /app  → App (the cockpit)
 *
 * No external dependency; uses pushState + popstate. Children receive a
 * `navigate(path)` callback so the Landing CTAs and the App back-to-home
 * link can switch routes without a full page reload.
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

  if (path.startsWith('/app')) {
    return <App onBackToLanding={() => navigate('/')} />;
  }
  return <Landing onOpenApp={() => navigate('/app')} />;
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <React.StrictMode>
    <Router />
  </React.StrictMode>
);
