/**
 * useMarketData — Custom hook for ALL real-time market data.
 * Caches ALL data in date-stamped localStorage for session restore.
 * Auto-cleans old dates (keeps last 7 days).
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { fetchLive, fetchUnusual, fetchIntraday, fetchNextDay, fetchWeekly, fetchSignals, fetchOISummary, fetchSellerSummary, fetchTradeAnalysis, fetchHiddenShift } from "./api";

// ── Date-stamped cache helpers ──────────────────────────────────────────

function getTodayIST() {
  return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" }); // YYYY-MM-DD
}

function getCacheKey() {
  return `universe_data_${getTodayIST()}`;
}

function loadCache() {
  try {
    const raw = localStorage.getItem(getCacheKey());
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function saveCache(data) {
  try {
    localStorage.setItem(getCacheKey(), JSON.stringify(data));
  } catch { /* storage full — try cleanup */
    cleanupOldDates(3);
    try { localStorage.setItem(getCacheKey(), JSON.stringify(data)); } catch {}
  }
}

function getCached(key) {
  // Try today's cache first
  const today = loadCache()[key];
  if (today) return today;

  // Fallback: check last 3 days for cached data (market closed / holidays)
  const now = new Date();
  for (let i = 1; i <= 3; i++) {
    const prev = new Date(now);
    prev.setDate(prev.getDate() - i);
    const prevKey = `universe_data_${prev.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" })}`;
    try {
      const raw = localStorage.getItem(prevKey);
      if (raw) {
        const data = JSON.parse(raw);
        if (data[key]) return data[key];
      }
    } catch {}
  }
  return null;
}

function setCached(key, value) {
  const cache = loadCache();
  cache[key] = value;
  cache._lastUpdated = new Date().toISOString();
  saveCache(cache);
}

function cleanupOldDates(keepDays = 7) {
  const today = new Date();
  const keysToRemove = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.startsWith("universe_data_") && k !== getCacheKey()) {
      const dateStr = k.replace("universe_data_", "");
      const d = new Date(dateStr);
      const diffDays = (today - d) / (1000 * 60 * 60 * 24);
      if (diffDays > keepDays) keysToRemove.push(k);
    }
  }
  keysToRemove.forEach(k => localStorage.removeItem(k));
  // Also remove legacy non-dated cache
  localStorage.removeItem("universe_data_cache");
}

export function useMarketData() {
  // Cleanup old dates on mount
  useEffect(() => { cleanupOldDates(7); }, []);

  // Load from today's localStorage on first render
  const [live, setLive] = useState(() => getCached("live"));
  const [unusual, setUnusual] = useState(() => getCached("unusual") || []);
  const [intraday, setIntraday] = useState(() => getCached("intraday"));
  const [nextday, setNextday] = useState(() => getCached("nextday"));
  const [weekly, setWeekly] = useState(() => getCached("weekly"));
  const [signals, setSignals] = useState(() => getCached("signals") || []);
  const [oiSummary, setOiSummary] = useState(() => getCached("oiSummary"));
  const [sellerData, setSellerData] = useState(() => getCached("sellerData"));
  const [tradeAnalysis, setTradeAnalysis] = useState(() => getCached("tradeAnalysis"));
  const [hiddenShift, setHiddenShift] = useState(() => getCached("hiddenShift"));
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
  const setSellerDataCached = useCallback(setAndCache("sellerData", setSellerData), []);
  const setTradeAnalysisCached = useCallback(setAndCache("tradeAnalysis", setTradeAnalysis), []);
  const setHiddenShiftCached = useCallback(setAndCache("hiddenShift", setHiddenShift), []);

  // ── Fetch all tab data (called once + every 30s) ──────────────────────

  const fetchAllTabData = useCallback(async () => {
    try {
      const [intradayData, nextdayData, weeklyData, unusualData, signalsData, oiData, sellerRes, tradeRes, hiddenRes] = await Promise.all([
        fetchIntraday().catch(() => null),
        fetchNextDay().catch(() => null),
        fetchWeekly().catch(() => null),
        fetchUnusual().catch(() => []),
        fetchSignals().catch(() => []),
        fetchOISummary().catch(() => null),
        fetchSellerSummary().catch(() => null),
        fetchTradeAnalysis().catch(() => null),
        fetchHiddenShift().catch(() => null),
      ]);
      if (intradayData && !intradayData.error) setIntradayCached(intradayData);
      if (nextdayData && !nextdayData.error) setNextdayCached(nextdayData);
      if (weeklyData && !weeklyData.error) setWeeklyCached(weeklyData);
      if (Array.isArray(unusualData) && unusualData.length > 0) setUnusualCached(unusualData);
      if (Array.isArray(signalsData) && signalsData.length > 0) setSignalsCached(signalsData);
      if (oiData && !oiData.error) setOiSummaryCached(oiData);
      if (sellerRes && !sellerRes.error) setSellerDataCached(sellerRes);
      if (tradeRes && !tradeRes.error) setTradeAnalysisCached(tradeRes);
      if (hiddenRes && !hiddenRes.error) setHiddenShiftCached(hiddenRes);
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

  return { live, unusual, intraday, nextday, weekly, signals, oiSummary, sellerData, tradeAnalysis, hiddenShift, connected };
}
