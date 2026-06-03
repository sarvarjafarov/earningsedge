import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getApiBase } from '../apiConfig';

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function fmtNum(x, digits = 2) {
  const n = Number(x);
  if (!Number.isFinite(n)) return '—';
  return n.toFixed(digits);
}

/** Strip Python enum prefix ("OrderStatus.ACCEPTED" → "ACCEPTED") and uppercase. */
function fmtStatus(raw) {
  if (!raw) return '—';
  return String(raw).split('.').pop().toUpperCase();
}

/** UUID → "2533bfcf…1a2ef" (first 8 + last 4) for legibility. */
function fmtOrderId(raw) {
  const s = String(raw || '').trim();
  if (s.length <= 14) return s;
  return `${s.slice(0, 8)}…${s.slice(-4)}`;
}

export default function OrderModal({
  open,
  onClose,
  ticker,
  side,
  qty,
  entryPrice,
  stopLoss,
  takeProfit,
  /** 'BUY' | 'SHORT' for header — SHORT maps to sell side in API */
  displayVerb,
  onOrderSuccess,
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(null);
  const dialogRef = useRef(null);
  const cancelBtnRef = useRef(null);
  const previouslyFocusedRef = useRef(null);
  /** Tracks the in-flight POST so we can abort if the user closes mid-submit. */
  const abortRef = useRef(null);
  /** Tracks the post-success auto-close timer so reopens don't clobber each other. */
  const autoCloseTimerRef = useRef(null);

  const apiBase = getApiBase();
  const sideUpper = (side || '').toUpperCase();
  const verb =
    displayVerb ||
    (sideUpper === 'SELL' ? 'SELL' : sideUpper === 'BUY' ? 'BUY' : sideUpper);

  const estimatedCost = useMemo(() => {
    const q = Number(qty);
    const p = Number(entryPrice);
    if (!Number.isFinite(q) || !Number.isFinite(p)) return null;
    return q * p;
  }, [qty, entryPrice]);

  useEffect(() => {
    if (!open) {
      // Modal is closing — cancel any in-flight POST so the unmounted modal
      // doesn't stuff state into a vanished component, and clear any pending
      // auto-close timer from a previous success.
      if (abortRef.current) {
        try { abortRef.current.abort(); } catch (_) {}
        abortRef.current = null;
      }
      if (autoCloseTimerRef.current) {
        window.clearTimeout(autoCloseTimerRef.current);
        autoCloseTimerRef.current = null;
      }
      return;
    }
    setBusy(false);
    setError('');
    setSuccess(null);
  }, [open]);

  // Belt-and-suspenders: also cancel on unmount.
  useEffect(() => () => {
    if (abortRef.current) {
      try { abortRef.current.abort(); } catch (_) {}
      abortRef.current = null;
    }
    if (autoCloseTimerRef.current) {
      window.clearTimeout(autoCloseTimerRef.current);
      autoCloseTimerRef.current = null;
    }
  }, []);

  const handleClose = useCallback(() => {
    onClose?.();
  }, [onClose]);

  // Save/restore focus + Esc + Tab focus trap whenever the modal is open.
  useEffect(() => {
    if (!open) return undefined;

    previouslyFocusedRef.current = document.activeElement;
    // Defer autofocus until the dialog has actually mounted in the DOM.
    const focusTimer = window.setTimeout(() => {
      cancelBtnRef.current?.focus();
    }, 0);

    const onKeyDown = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        handleClose();
        return;
      }
      if (e.key !== 'Tab' || !dialogRef.current) return;

      const nodes = Array.from(
        dialogRef.current.querySelectorAll(FOCUSABLE_SELECTOR),
      ).filter((n) => !n.hasAttribute('disabled') && n.offsetParent !== null);
      if (nodes.length === 0) {
        e.preventDefault();
        return;
      }
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener('keydown', onKeyDown);
      const prev = previouslyFocusedRef.current;
      if (prev && typeof prev.focus === 'function') {
        try { prev.focus(); } catch (_) {}
      }
    };
  }, [open, handleClose]);

  const confirm = async () => {
    if (!ticker) {
      setError('Missing ticker');
      return;
    }
    const q = Number(qty);
    if (!Number.isFinite(q) || q <= 0) {
      setError('Invalid share quantity');
      return;
    }

    setBusy(true);
    setError('');
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      const limitPrice = Number(entryPrice);
      const payload = {
        ticker,
        side: sideUpper === 'BUY' ? 'buy' : 'sell',
        qty: Math.floor(q),
      };
      if (Number.isFinite(limitPrice) && limitPrice > 0) {
        payload.limit_price = limitPrice;
      }

      const resp = await fetch(`${apiBase}/api/order`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: ac.signal,
      });
      const data = await resp.json().catch(() => ({}));
      if (ac.signal.aborted) return;
      if (!resp.ok || data.error) {
        setError(data.error || 'Order submission failed');
        setBusy(false);
        return;
      }
      setSuccess(data);
      onOrderSuccess?.();

      autoCloseTimerRef.current = window.setTimeout(() => {
        autoCloseTimerRef.current = null;
        onClose?.();
      }, 3000);
    } catch (exc) {
      if (exc?.name === 'AbortError') return;
      setError(String(exc?.message || exc || 'Order submission failed'));
    } finally {
      if (abortRef.current === ac) abortRef.current = null;
      setBusy(false);
    }
  };

  if (!open) return null;

  const sideClass =
    sideUpper === 'BUY' ? 'order-modal--buy'
      : sideUpper === 'SELL' ? 'order-modal--sell'
        : 'order-modal--neutral';

  return (
    <div
      className="modal-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
    >
      <div
        className={`order-modal ${sideClass}`}
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="order-modal-title"
        tabIndex={-1}
      >
        <div className="order-modal-head">
          <div className="order-modal-head-text">
            <div className="order-modal-eyebrow">Paper order confirmation</div>
            <div id="order-modal-title" className="order-modal-title">
              <span className="order-modal-verb">{verb}</span>{' '}
              {ticker ? String(ticker).toUpperCase() : ''}
            </div>
          </div>
          <button
            className="btn btn-ghost order-modal-close"
            type="button"
            onClick={handleClose}
            disabled={busy}
            aria-label="Close order confirmation"
          >
            <span aria-hidden="true">✕</span> Close
          </button>
        </div>

        <div className="order-modal-body">
          <div className="order-modal-grid">
            <div className="order-modal-field">
              <div className="order-modal-field-label">Shares</div>
              <div className="order-modal-field-value">
                {Number.isFinite(Number(qty)) ? Math.floor(Number(qty)) : '—'}
              </div>
            </div>

            <div className="order-modal-field">
              <div className="order-modal-field-label">Entry price</div>
              <div className="order-modal-field-value">
                ${fmtNum(entryPrice, 2)}
              </div>
            </div>

            {Number.isFinite(Number(stopLoss)) && Number(stopLoss) > 0 && (
              <div className="order-modal-field">
                <div className="order-modal-field-label">Stop loss</div>
                <div className="order-modal-field-value order-modal-field-value--secondary">
                  ${fmtNum(stopLoss, 2)}
                </div>
              </div>
            )}

            {Number.isFinite(Number(takeProfit)) && Number(takeProfit) > 0 && (
              <div className="order-modal-field">
                <div className="order-modal-field-label">Take profit</div>
                <div className="order-modal-field-value order-modal-field-value--secondary">
                  ${fmtNum(takeProfit, 2)}
                </div>
              </div>
            )}

            <div className="order-modal-field order-modal-field--span">
              <div className="order-modal-field-label">Estimated cost</div>
              <div className="order-modal-field-value">
                {estimatedCost == null ? '—' : `$${fmtNum(estimatedCost, 2)}`}
              </div>
            </div>
          </div>
        </div>

        {error ? (
          <div className="order-modal-msg order-modal-msg--error" role="alert">
            {error}
          </div>
        ) : null}

        {success && !error ? (
          <div className="order-modal-msg order-modal-msg--success" role="status" aria-live="polite">
            <span className="order-modal-msg-icon" aria-hidden="true">✓</span>{' '}
            Order <span className="order-modal-msg-emph">{fmtStatus(success.status)}</span>
            <span className="order-modal-msg-meta">
              · ID <code title={success.order_id}>{fmtOrderId(success.order_id)}</code>
            </span>
          </div>
        ) : null}

        <div className="order-modal-actions">
          {success ? (
            // Order is in. One clear action: dismiss the success.
            // Auto-close still fires after 3s as a safety net.
            <button
              ref={cancelBtnRef}
              type="button"
              className="btn btn-primary"
              onClick={handleClose}
              autoFocus
            >
              DONE
            </button>
          ) : (
            <>
              <button
                ref={cancelBtnRef}
                type="button"
                className="btn btn-ghost"
                onClick={handleClose}
                disabled={busy}
              >
                CANCEL
              </button>
              <button
                type="button"
                className={sideUpper === 'SELL' ? 'btn btn-danger' : 'btn btn-primary'}
                onClick={confirm}
                disabled={busy}
              >
                {busy ? 'CONFIRMING…' : 'CONFIRM ORDER'}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

