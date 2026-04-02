/**
 * useMarketData — Custom hook for ALL real-time market data.
 * Fetches: live data, intraday technicals, next day levels, weekly outlook, unusual activity.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { fetchLive, fetchUnusual, fetchIntraday, fetchNextDay, fetchWeekly, fetchSignals, fetchOISummary } from "./api";

export function useMarketData() {
  const [live, setLive] = useState(null);
  const [unusual, setUnusual] = useState([]);
  const [intraday, setIntraday] = useState(null);
  const [nextday, setNextday] = useState(null);
  const [weekly, setWeekly] = useState(null);
  const [signals, setSignals] = useState([]);
  const [oiSummary, setOiSummary] = useState(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const pollRef = useRef(null);
  const reconnectRef = useRef(null);
  const slowPollRef = useRef(null);

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
      if (intradayData) setIntraday(intradayData);
      if (nextdayData) setNextday(nextdayData);
      if (weeklyData) setWeekly(weeklyData);
      if (Array.isArray(unusualData) && unusualData.length > 0) setUnusual(unusualData);
      if (Array.isArray(signalsData) && signalsData.length > 0) setSignals(signalsData);
      if (oiData) setOiSummary(oiData);
    } catch (err) {
      // Backend not available
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
            setLive(msg.data);
          }
          if (msg.unusual && msg.unusual.length > 0) {
            setUnusual(msg.unusual);
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
        if (liveData && !liveData.error) setLive(liveData);
        const unusualData = await fetchUnusual();
        if (Array.isArray(unusualData)) setUnusual(unusualData);
      } catch (err) {}
    };
    poll();
    pollRef.current = setInterval(poll, 3000);
  }, []);

  // ── Lifecycle ─────────────────────────────────────────────────────────

  useEffect(() => {
    // Connect WebSocket for live data
    connectWS();

    // Fetch all tab data immediately + every 30 seconds
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
