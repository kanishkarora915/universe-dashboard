/**
 * useSWRPoll
 * ──────────
 * Thin SWR wrapper for periodic JSON polling. The big perf win:
 * when N components mount with the same `key`, SWR de-duplicates
 * to a single in-flight fetch and caches the response. With the
 * backend already caching hot endpoints (5–30s TTL), the chain is:
 *
 *   N components → 1 fetch per `refreshInterval` → 1 cached
 *   computation on the server.
 *
 * Drop-in replacement for `setInterval(fetch)` patterns.
 *
 * Usage:
 *   const { data, error, isLoading, mutate } = useSWRPoll(
 *     "/api/positions/watcher-status",
 *     { refreshInterval: 10000 }
 *   );
 *
 * Pass `null` as the key to disable fetching (conditional polling).
 */

import useSWR from "swr";

const API = import.meta.env.VITE_API_URL || "";

const fetcher = async (url) => {
  const res = await fetch(url);
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
};

export default function useSWRPoll(path, options = {}) {
  const {
    refreshInterval = 10000,
    revalidateOnFocus = false,
    dedupingInterval,
    fallbackData,
    ...rest
  } = options;

  // Default dedup window = half the refresh interval, capped at 5s,
  // so two near-simultaneous mounts share one fetch.
  const dedupWindow = dedupingInterval ?? Math.min(refreshInterval / 2, 5000);

  const key = path ? `${API}${path}` : null;

  return useSWR(key, fetcher, {
    refreshInterval,
    revalidateOnFocus,
    dedupingInterval: dedupWindow,
    fallbackData,
    keepPreviousData: true,
    shouldRetryOnError: false,
    ...rest,
  });
}
