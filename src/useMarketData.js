/**
 * useMarketData — Custom hook for ALL real-time market data.
 * Caches ALL data in localStorage so it persists across refreshes and restarts.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { fetchLive, fetchUnusual, fetchIntraday, fetchNextDay, fetchWeekly, fetchSignals, fetchOISummary } from "./api";

const CACHE_KEY = "universe_data_cache";

function loadCache() {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function saveCache(data) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(data));
  } catch { /* storage full */ }
}

function getCached(key) {
  return loadCache()[key] || null;
}

function setCached(key, value) {
  const cache = loadCache();
  cache[key] = value;
  cache._lastUpdated = new Date().toISOString();
  saveCache(cache);
}

export function useMarketData() {
  // Load from localStorage on first render
  const [live, setLive] = useState(() => getCached("live"));
  const [unusual, setUnusual] = useState(() => getCached("unusual") || []);
  const [intraday, setIntraday] = useState(() => getCached("intraday"));
  const [nextday, setNextday] = useState(() => getCached("nextday"));
  const [weekly, setWeekly] = useState(() => getCached("weekly"));
  const [signals, setSignals] = useState(() => getCached("signals") || []);
  const [oiSummary, setOiSummary] = useState(() => getCached("oiSummary"));
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const pollRef = useRef(null);
  const reconnectRef = useRef(null);
  const slowPollRef = useRef(null);

  // ── Helper: set state + cache ───────────────────────────────────────

  const setAndCache = useCallback((key, setter) => (data) => {
    setter(data);
    setCached(key, data);
  }, []);

  const setLiveCached = useCallback(setAndCache("live", setLive), []);
  const setUnusualCached = useCallback(setAndCache("unusual", setUnusual), []);
  const setIntradayCached = useCallback(setAndCache("intraday", setIntraday), []);
  const setNextdayCached = useCallback(setAndCache("nextday", setNextday), []);
  const setWeeklyCached = useCallback(setAndCache("weekly", setWeekly), []);
  const setSignalsCached = useCallback(setAndCache("signals", setSignals), []);
  const setOiSummaryCached = useCallback(setAndCache("oiSummary", setOiSummary), []);

  // ── Fetch all tab data (called once + every 30s) ──────────────────────

  const fetchAllTabData = useCallback(async () => {
    try {
      const [intradayData, nextdayData, weeklyData, unusualData, signalsData, oiData] = await Promise.all([
        fetchIntraday().catch(() => null),
        fetchNextDay().catch(() => null),
        fetchWeekly().catch(() => null),
        fetchUnusual().catch(() => []),
        fetchSignals().catch(() => []),
        fetchOISummary().catch(() => null),
      ]);
      if (intradayData && !intradayData.error) setIntradayCached(intradayData);
      if (nextdayData && !nextdayData.error) setNextdayCached(nextdayData);
      if (weeklyData && !weeklyData.error) setWeeklyCached(weeklyData);
      if (Array.isArray(unusualData) && unusualData.length > 0) setUnusualCached(unusualData);
      if (Array.isArray(signalsData) && signalsData.length > 0) setSignalsCached(signalsData);
      if (oiData && !oiData.error) setOiSummaryCached(oiData);
    } catch (err) {
      // Backend not available — localStorage cache still active
    }
  }, []);

  // ── WebSocket connection ──────────────────────────────────────────────

  const connectWS = useCallback(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/ticks`;

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log("[WS] Connected");
        setConnected(true);
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.channel === "live" && msg.data) {
            setLiveCached(msg.data);
          }
          if (msg.unusual && msg.unusual.length > 0) {
            setUnusualCached(msg.unusual);
          }
        } catch (err) {}
      };

      ws.onclose = () => {
        console.log("[WS] Disconnected, falling back to polling");
        setConnected(false);
        wsRef.current = null;
        startPolling();
        reconnectRef.current = setTimeout(connectWS, 3000);
      };

      ws.onerror = () => { ws.close(); };
    } catch (err) {
      console.log("[WS] Connection failed, using polling");
      startPolling();
    }
  }, []);

  // ── REST polling fallback ─────────────────────────────────────────────

  const startPolling = useCallback(() => {
    if (pollRef.current) return;
    const poll = async () => {
      try {
        const liveData = await fetchLive();
        if (liveData && !liveData.error) setLiveCached(liveData);
        const unusualData = await fetchUnusual();
        if (Array.isArray(unusualData) && unusualData.length > 0) setUnusualCached(unusualData);
      } catch (err) {}
    };
    poll();
    pollRef.current = setInterval(poll, 3000);
  }, []);

  // ── Lifecycle ─────────────────────────────────────────────────────────

  useEffect(() => {
    connectWS();
    fetchAllTabData();
    slowPollRef.current = setInterval(fetchAllTabData, 30000);

    return () => {
      if (wsRef.current) wsRef.current.close();
      if (pollRef.current) clearInterval(pollRef.current);
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      if (slowPollRef.current) clearInterval(slowPollRef.current);
    };
  }, [connectWS, fetchAllTabData]);

  return { live, unusual, intraday, nextday, weekly, signals, oiSummary, connected };
}
