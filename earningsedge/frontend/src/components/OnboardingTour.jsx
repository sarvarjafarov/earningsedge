import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';

/**
 * Lightweight in-app guided tour. Each step targets a CSS selector and renders
 * a tooltip card next to the spotlit element. No external dependencies.
 *
 * `steps` shape:
 *   {
 *     target: string | null,     // CSS selector (null = centered welcome card)
 *     title: string,
 *     body: ReactNode,
 *     placement?: 'bottom' | 'bottom-start' | 'bottom-end'
 *               | 'top' | 'top-start' | 'top-end'
 *               | 'right' | 'left' | 'center',
 *     padding?: number,           // spotlight padding around target rect (px)
 *   }
 *
 * Steps whose target selector matches no element in the DOM are silently skipped.
 */

const SPOTLIGHT_PADDING = 8;
const TOOLTIP_GAP = 16;
const TOOLTIP_WIDTH = 360;
const VIEWPORT_MARGIN = 16;

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

function computeTooltipPos(rect, placement, tooltipH = 220) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  // Center fallback when no target.
  if (!rect || placement === 'center') {
    return {
      left: clamp((vw - TOOLTIP_WIDTH) / 2, VIEWPORT_MARGIN, vw - TOOLTIP_WIDTH - VIEWPORT_MARGIN),
      top: clamp((vh - tooltipH) / 2, VIEWPORT_MARGIN, vh - tooltipH - VIEWPORT_MARGIN),
    };
  }

  let left;
  let top;

  switch (placement) {
    case 'top':
      left = rect.left + rect.width / 2 - TOOLTIP_WIDTH / 2;
      top = rect.top - tooltipH - TOOLTIP_GAP;
      break;
    case 'top-start':
      left = rect.left;
      top = rect.top - tooltipH - TOOLTIP_GAP;
      break;
    case 'top-end':
      left = rect.right - TOOLTIP_WIDTH;
      top = rect.top - tooltipH - TOOLTIP_GAP;
      break;
    case 'bottom-start':
      left = rect.left;
      top = rect.bottom + TOOLTIP_GAP;
      break;
    case 'bottom-end':
      left = rect.right - TOOLTIP_WIDTH;
      top = rect.bottom + TOOLTIP_GAP;
      break;
    case 'right':
      left = rect.right + TOOLTIP_GAP;
      top = rect.top + rect.height / 2 - tooltipH / 2;
      break;
    case 'left':
      left = rect.left - TOOLTIP_WIDTH - TOOLTIP_GAP;
      top = rect.top + rect.height / 2 - tooltipH / 2;
      break;
    case 'bottom':
    default:
      left = rect.left + rect.width / 2 - TOOLTIP_WIDTH / 2;
      top = rect.bottom + TOOLTIP_GAP;
      break;
  }

  // If we'd flow off the bottom, flip above.
  if (top + tooltipH + VIEWPORT_MARGIN > vh) {
    const flipped = rect.top - tooltipH - TOOLTIP_GAP;
    if (flipped >= VIEWPORT_MARGIN) top = flipped;
    else top = vh - tooltipH - VIEWPORT_MARGIN;
  }

  // Clamp to viewport.
  left = clamp(left, VIEWPORT_MARGIN, vw - TOOLTIP_WIDTH - VIEWPORT_MARGIN);
  top = clamp(top, VIEWPORT_MARGIN, vh - tooltipH - VIEWPORT_MARGIN);

  return { left, top };
}

export default function OnboardingTour({ open, steps, onClose }) {
  const [stepIdx, setStepIdx] = useState(0);
  const [rect, setRect] = useState(null);
  const [tipPos, setTipPos] = useState(null);
  const tooltipRef = useRef(null);
  /** Tracks the last navigation direction so missing-target skips advance
   *  in the right direction (Back → keep going Back, Next → keep going Next).
   *  Without this, hitting Back on a step whose previous target is missing
   *  auto-advances Forward, looking like the Back button is broken. */
  const directionRef = useRef(1);

  // Reset to step 0 each time the tour opens, and reset direction.
  useEffect(() => {
    if (open) {
      setStepIdx(0);
      directionRef.current = 1;
    }
  }, [open]);

  /** When `steps` changes mid-tour (e.g. user loaded a ticker and the
   *  stage-aware tour swapped to the loaded variant), reset to step 0 of
   *  the new tour instead of leaving the user pointing at an undefined
   *  index of a different array. */
  const lastStepsRef = useRef(steps);
  useEffect(() => {
    if (!open) {
      lastStepsRef.current = steps;
      return;
    }
    if (lastStepsRef.current !== steps) {
      setStepIdx(0);
      directionRef.current = 1;
      lastStepsRef.current = steps;
    }
  }, [open, steps]);

  /** Reposition spotlight + tooltip from the current target. Pulled out so
   *  the resize listener and the ResizeObserver can both call it. */
  const repositionFromTarget = (el, step) => {
    if (!el || !step) return;
    const r = el.getBoundingClientRect();
    const pad = step.padding ?? SPOTLIGHT_PADDING;
    const padded = {
      top: r.top - pad,
      left: r.left - pad,
      right: r.right + pad,
      bottom: r.bottom + pad,
      width: r.width + pad * 2,
      height: r.height + pad * 2,
    };
    setRect(padded);
    const tooltipH = tooltipRef.current?.offsetHeight ?? 220;
    setTipPos(computeTooltipPos(padded, step.placement || 'bottom', tooltipH));
  };

  // Resolve the current step. Steps may declare a `before` callback (to set
  // up app state, e.g. switch tabs) — we call it, wait a frame for the DOM
  // to settle, then measure.
  useLayoutEffect(() => {
    if (!open) return undefined;

    let cancelled = false;
    let attempt = 0;

    const step = steps[stepIdx];
    if (step?.before) {
      try { step.before(); } catch (_) {}
    }

    const resolveStep = () => {
      if (cancelled) return;
      if (!step) {
        onClose?.('completed');
        return;
      }
      if (!step.target) {
        setRect(null);
        setTipPos(computeTooltipPos(null, step.placement || 'center'));
        return;
      }
      const el = document.querySelector(step.target);
      if (!el) {
        // Target missing — give the DOM a couple of frames in case the user
        // just changed views, then skip in the direction the user was moving.
        attempt += 1;
        if (attempt < 8) {
          window.requestAnimationFrame(resolveStep);
          return;
        }
        const dir = directionRef.current;
        const next = stepIdx + dir;
        if (next < 0) {
          // Going backward past step 0 — clamp to first viable step
          setStepIdx(0);
          directionRef.current = 1;
        } else if (next >= steps.length) {
          onClose?.('completed');
        } else {
          setStepIdx(next);
        }
        return;
      }
      el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' });
      // Wait one frame after scroll + a tiny extra delay so layout settles
      // (auto-focus, focus-rings, etc. can shift coordinates by a few px).
      window.requestAnimationFrame(() => {
        if (cancelled) return;
        window.setTimeout(() => {
          if (cancelled) return;
          const refreshed = document.querySelector(step.target);
          if (refreshed) repositionFromTarget(refreshed, step);
        }, 80);
      });
    };

    // Slight delay so any `before` state change (e.g. setAppTab) flushes
    // before we look up the target.
    const t = window.setTimeout(resolveStep, step?.before ? 120 : 0);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [open, stepIdx, steps, onClose]);

  // Recompute on resize AND on layout shifts (mutation/resize of the target).
  useEffect(() => {
    if (!open) return undefined;
    const step = steps[stepIdx];
    if (!step?.target) return undefined;
    const el = document.querySelector(step.target);
    if (!el) return undefined;

    const onResize = () => repositionFromTarget(el, step);
    window.addEventListener('resize', onResize);

    let ro;
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => repositionFromTarget(el, step));
      ro.observe(el);
      ro.observe(document.body);
    }

    // Tick 200ms for a few seconds in case async data shifts the layout
    // after the step opens (loading-strip arriving, panels populating).
    let ticks = 0;
    const tick = window.setInterval(() => {
      ticks += 1;
      const refreshed = document.querySelector(step.target);
      if (refreshed) repositionFromTarget(refreshed, step);
      if (ticks > 12) window.clearInterval(tick);  // 12 * 200 = 2.4s
    }, 200);

    return () => {
      window.removeEventListener('resize', onResize);
      if (ro) ro.disconnect();
      window.clearInterval(tick);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, stepIdx, steps]);

  // Esc to dismiss; arrow keys to navigate (with direction tracking).
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.('skipped');
      else if (e.key === 'ArrowRight') {
        directionRef.current = 1;
        setStepIdx((i) => Math.min(i + 1, steps.length - 1));
      } else if (e.key === 'ArrowLeft') {
        directionRef.current = -1;
        setStepIdx((i) => Math.max(i - 1, 0));
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, steps.length, onClose]);

  const goNext = () => {
    directionRef.current = 1;
    setStepIdx((i) => Math.min(i + 1, steps.length - 1));
  };
  const goBack = () => {
    directionRef.current = -1;
    setStepIdx((i) => Math.max(i - 1, 0));
  };

  if (!open) return null;
  const step = steps[stepIdx];
  if (!step) return null;

  const isLast = stepIdx === steps.length - 1;
  const isFirst = stepIdx === 0;

  return (
    <div className="tour-root" role="dialog" aria-modal="true" aria-labelledby="tour-title">
      {rect ? (
        <div
          className="tour-spotlight"
          style={{
            top: rect.top,
            left: rect.left,
            width: rect.width,
            height: rect.height,
          }}
        />
      ) : (
        <div className="tour-overlay-fill" />
      )}

      <div
        ref={tooltipRef}
        className="tour-tooltip"
        style={tipPos ? { top: tipPos.top, left: tipPos.left, width: TOOLTIP_WIDTH } : { width: TOOLTIP_WIDTH }}
      >
        <div className="tour-eyebrow">
          Tour · {stepIdx + 1} of {steps.length}
        </div>
        <h3 id="tour-title" className="tour-title">{step.title}</h3>
        <div className="tour-body">{step.body}</div>
        <div className="tour-actions">
          <button
            type="button"
            className="tour-btn tour-btn-skip"
            onClick={() => onClose?.('skipped')}
          >
            Skip tour
          </button>
          <div className="tour-actions-right">
            {!isFirst && (
              <button
                type="button"
                className="tour-btn tour-btn-back"
                onClick={goBack}
                /* Direction-aware autofocus: when the user just pressed Back,
                 * keep focus on Back so Enter continues going Back instead of
                 * silently flipping to Next. */
                autoFocus={directionRef.current === -1}
              >
                ← Back
              </button>
            )}
            {isLast ? (
              <button
                type="button"
                className="tour-btn tour-btn-primary"
                onClick={() => onClose?.('completed')}
                autoFocus={directionRef.current === 1}
              >
                Got it
              </button>
            ) : (
              <button
                type="button"
                className="tour-btn tour-btn-primary"
                onClick={goNext}
                autoFocus={directionRef.current === 1}
              >
                Next →
              </button>
            )}
          </div>
        </div>
        <div className="tour-progress" aria-hidden="true">
          {steps.map((_, i) => (
            <span
              key={i}
              className={`tour-dot ${i === stepIdx ? 'is-current' : ''} ${i < stepIdx ? 'is-done' : ''}`}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
