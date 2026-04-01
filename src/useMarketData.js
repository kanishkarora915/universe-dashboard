/**
 * useMarketData — Custom hook for real-time market data.
 * Connects to WebSocket for live ticks, falls back to REST polling.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { fetchLive, fetchUnusual } from "./api";

export function useMarketData() {
  const [live, setLive] = useState(null);
  const [unusual, setUnusual] = useState([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const pollRef = useRef(null);
  const reconnectRef = useRef(null);

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
        // Clear polling if WS connects
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
        } catch (err) {
          // ignore parse errors
        }
      };

      ws.onclose = () => {
        console.log("[WS] Disconnected, falling back to polling");
        setConnected(false);
        wsRef.current = null;
        startPolling();
        // Reconnect after 3 seconds
        reconnectRef.current = setTimeout(connectWS, 3000);
      };

      ws.onerror = () => {
        ws.close();
      };

    } catch (err) {
      console.log("[WS] Connection failed, using polling");
      startPolling();
    }
  }, []);

  // ── REST polling fallback ─────────────────────────────────────────────

  const startPolling = useCallback(() => {
    if (pollRef.current) return;
    console.log("[POLL] Starting REST polling (3s interval)");

    const poll = async () => {
      try {
        const liveData = await fetchLive();
        if (liveData && !liveData.error) setLive(liveData);

        const unusualData = await fetchUnusual();
        if (Array.isArray(unusualData)) setUnusual(unusualData);
      } catch (err) {
        // Backend not available
      }
    };

    poll(); // Initial fetch
    pollRef.current = setInterval(poll, 3000);
  }, []);

  // ── Lifecycle ─────────────────────────────────────────────────────────

  useEffect(() => {
    // Try WebSocket first, fall back to polling
    connectWS();

    return () => {
      if (wsRef.current) wsRef.current.close();
      if (pollRef.current) clearInterval(pollRef.current);
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
    };
  }, [connectWS]);

  return { live, unusual, connected };
}
