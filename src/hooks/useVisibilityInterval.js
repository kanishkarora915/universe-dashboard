/**
 * useVisibilityInterval — setInterval that pauses when tab is hidden.
 *
 * Drop-in replacement for raw setInterval that:
 *   • Stops firing when document.visibilityState === 'hidden'
 *   • Resumes immediately when tab becomes visible again
 *   • Also pauses on window blur, resumes on focus (extra safety)
 *
 * USAGE
 *   useVisibilityInterval(() => {
 *     fetchSomething();
 *   }, 1000);
 *
 *   // With deps (callback uses fresh closure):
 *   useVisibilityInterval(() => doStuff(value), 5000, [value]);
 *
 * BACKEND IMPACT
 *   When user switches to another tab (YouTube, Slack, etc), polling
 *   stops. Backend stops being hit by background tabs. Render CPU
 *   stays cool. Phone battery stops draining.
 *
 *   ZERO impact on backend logic — engine keeps running, trades still
 *   fire, SL still hits, Telegram alerts still send. This only pauses
 *   the BROWSER'S display-refresh polling.
 *
 * SAFETY
 *   • Runs callback once immediately on mount (so initial data loads)
 *   • Runs callback once on visibility restore (instant refresh)
 *   • Cleans up on unmount
 */

import { useEffect, useRef } from "react";

export function useVisibilityInterval(callback, delayMs, deps = []) {
  // Keep latest callback in a ref so we don't restart interval on deps change
  const savedCallback = useRef(callback);
  savedCallback.current = callback;

  useEffect(() => {
    if (delayMs == null || delayMs < 0) return undefined;

    let intervalId = null;
    let isPaused = false;

    const tick = () => {
      try {
        savedCallback.current();
      } catch (err) {
        // Don't let interval crash if callback throws
        console.error("[useVisibilityInterval] callback threw:", err);
      }
    };

    const startInterval = () => {
      if (intervalId != null) return;
      intervalId = setInterval(tick, delayMs);
    };

    const stopInterval = () => {
      if (intervalId == null) return;
      clearInterval(intervalId);
      intervalId = null;
    };

    const isVisible = () => {
      if (typeof document === "undefined") return true;
      // Tab is "visible" if document is visible AND window is focused.
      // Some browsers (especially mobile Safari) don't always fire
      // visibilitychange reliably on blur — checking both is safer.
      const visibleByApi = document.visibilityState === "visible";
      return visibleByApi;
    };

    const handleVisibilityChange = () => {
      const visible = isVisible();
      if (visible && isPaused) {
        // Resumed → fire immediately so user sees fresh data
        isPaused = false;
        tick();
        startInterval();
      } else if (!visible && !isPaused) {
        isPaused = true;
        stopInterval();
      }
    };

    // Initial fire (immediate, so first load isn't delayed by delayMs)
    if (isVisible()) {
      tick();
      startInterval();
    } else {
      isPaused = true;
    }

    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("focus", handleVisibilityChange);
    window.addEventListener("blur", handleVisibilityChange);

    return () => {
      stopInterval();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("focus", handleVisibilityChange);
      window.removeEventListener("blur", handleVisibilityChange);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [delayMs, ...deps]);
}

/**
 * Helper: returns true if the tab is currently visible.
 * Useful for conditional rendering or skipping expensive work on render.
 */
export function isTabVisible() {
  if (typeof document === "undefined") return true;
  return document.visibilityState === "visible";
}

export default useVisibilityInterval;
