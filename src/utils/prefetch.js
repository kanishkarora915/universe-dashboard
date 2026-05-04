/**
 * prefetch.js
 * ───────────
 * N5: Hover-intent prefetch for SWR cache.
 *
 * When user hovers a tab button or trade card, fire a background fetch
 * and warm SWR's cache. By the time they actually click, data is already
 * loaded. Linear / Vercel / Notion all use this pattern.
 *
 * Usage:
 *   import { prefetch } from "../utils/prefetch";
 *   <button onMouseEnter={() => prefetch("/api/scalper/trades/open")}>...</button>
 *
 * Internals:
 *   - Uses SWR's `mutate` to populate cache without rendering anything
 *   - Debounces same-key prefetches within 2s window (no spam)
 *   - Silently catches errors (prefetch is best-effort)
 */

import { mutate } from "swr";

const API = import.meta.env.VITE_API_URL || "";

// Debounce: track last-prefetch ts per key
const _lastPrefetch = new Map();
const DEBOUNCE_MS = 2000;

export function prefetch(path) {
  if (!path) return;

  const key = `${API}${path}`;
  const now = Date.now();
  const last = _lastPrefetch.get(key) || 0;
  if (now - last < DEBOUNCE_MS) return;  // recently prefetched, skip
  _lastPrefetch.set(key, now);

  // Fire async, populate SWR cache, fail silently
  fetch(key, { credentials: "same-origin" })
    .then(r => r.ok ? r.json() : null)
    .then(data => {
      if (data != null) {
        // Populate SWR cache without revalidation
        mutate(key, data, { revalidate: false });
      }
    })
    .catch(() => {});
}

/**
 * prefetchMany — hover handler that warms multiple endpoints at once.
 * Useful for tabs that need several feeds (e.g. cockpit needs reversal +
 * oi-context for both indices).
 */
export function prefetchMany(paths) {
  if (!Array.isArray(paths)) return;
  paths.forEach(p => prefetch(p));
}
