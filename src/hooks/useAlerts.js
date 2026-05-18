// Alerts hook — fetches from backend DB + WS stream + tab flash + sound
import { useState, useEffect, useRef, useCallback } from "react";
import { useSound } from "./useSound";

const ORIGINAL_TITLE = typeof document !== "undefined" ? document.title : "Universe Pro";
const ORIGINAL_FAVICON = (() => {
  if (typeof document === "undefined") return null;
  const link = document.querySelector("link[rel~='icon']");
  return link ? link.href : null;
})();

function makeRedDotFavicon() {
  // Generate data URL for favicon with red dot
  const canvas = document.createElement("canvas");
  canvas.width = 32;
  canvas.height = 32;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#0A84FF";
  ctx.fillRect(0, 0, 32, 32);
  // red dot
  ctx.beginPath();
  ctx.arc(24, 8, 7, 0, Math.PI * 2);
  ctx.fillStyle = "#FF453A";
  ctx.fill();
  return canvas.toDataURL("image/png");
}

function setFavicon(href) {
  let link = document.querySelector("link[rel~='icon']");
  if (!link) {
    link = document.createElement("link");
    link.rel = "icon";
    document.head.appendChild(link);
  }
  link.href = href;
}

export function useAlerts({ pollInterval = 8000, activeTab } = {}) {
  const [alerts, setAlerts] = useState([]);
  const [counts, setCounts] = useState({ total: 0, byTab: {} });
  const [toasts, setToasts] = useState([]);
  const [flashingTab, setFlashingTab] = useState(null);
  const lastSeenId = useRef(0);
  const flashTimeout = useRef(null);
  const { playAlert } = useSound();

  const fetchAlerts = useCallback(async () => {
    try {
      const res = await fetch("/api/alerts?limit=50");
      if (!res.ok) return;
      const data = await res.json();
      setAlerts(data.alerts || []);

      const cRes = await fetch("/api/alerts/counts");
      if (cRes.ok) setCounts(await cRes.json());

      // detect new alerts for toasts + sound
      const newest = (data.alerts || [])[0];
      if (newest && newest.id > lastSeenId.current) {
        // Only trigger if not first load
        if (lastSeenId.current > 0) {
          const freshOnes = (data.alerts || []).filter((a) => a.id > lastSeenId.current && !a.read);
          freshOnes.slice(0, 3).forEach((a) => {
            // toast
            setToasts((t) => [...t, { ...a, toastId: a.id }]);
            setTimeout(() => {
              setToasts((t) => t.filter((x) => x.toastId !== a.id));
            }, 6000);

            // play sound
            playAlert(a.alert_type);

            // flash tab icon
            setFlashingTab(a.tab);
            if (flashTimeout.current) clearTimeout(flashTimeout.current);
            flashTimeout.current = setTimeout(() => setFlashingTab(null), 3000);
          });
        }
        lastSeenId.current = newest.id;
      }
    } catch (e) {
      // silent
    }
  }, [playAlert]);

  // Poll
  useEffect(() => {
    fetchAlerts();
    const t = setInterval(fetchAlerts, pollInterval);
    return () => clearInterval(t);
  }, [fetchAlerts, pollInterval]);

  // Tab title + favicon update
  useEffect(() => {
    const unread = counts.total || 0;
    if (unread > 0 && document.visibilityState === "hidden") {
      document.title = `(${unread}) ${ORIGINAL_TITLE}`;
      try {
        setFavicon(makeRedDotFavicon());
      } catch {
        // ignore
      }
    } else {
      document.title = ORIGINAL_TITLE;
      if (ORIGINAL_FAVICON) setFavicon(ORIGINAL_FAVICON);
    }
  }, [counts.total]);

  // When user returns to tab, mark read for active tab section
  useEffect(() => {
    const handler = () => {
      if (document.visibilityState === "visible") {
        document.title = ORIGINAL_TITLE;
        if (ORIGINAL_FAVICON) setFavicon(ORIGINAL_FAVICON);
      }
    };
    document.addEventListener("visibilitychange", handler);
    return () => document.removeEventListener("visibilitychange", handler);
  }, []);

  // Mark tab alerts as read when switching to that tab
  const prevTab = useRef(activeTab);
  useEffect(() => {
    if (activeTab && activeTab !== prevTab.current) {
      prevTab.current = activeTab;
      (async () => {
        try {
          await fetch("/api/alerts/mark-read", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tab: activeTab }),
          });
          fetchAlerts();
        } catch {
          // ignore
        }
      })();
    }
  }, [activeTab, fetchAlerts]);

  const dismissToast = useCallback((id) => {
    setToasts((t) => t.filter((x) => x.toastId !== id));
  }, []);

  const markAllRead = useCallback(async () => {
    try {
      await fetch("/api/alerts/mark-read", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ all: true }),
      });
      fetchAlerts();
    } catch {
      // ignore
    }
  }, [fetchAlerts]);

  const dismissAlert = useCallback(
    async (id) => {
      try {
        await fetch(`/api/alerts/${id}/dismiss`, { method: "POST" });
        fetchAlerts();
      } catch {
        // ignore
      }
    },
    [fetchAlerts]
  );

  const pinAlert = useCallback(
    async (id, pinned) => {
      try {
        await fetch(`/api/alerts/${id}/pin`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pinned }),
        });
        fetchAlerts();
      } catch {
        // ignore
      }
    },
    [fetchAlerts]
  );

  return {
    alerts,
    counts,
    toasts,
    flashingTab,
    dismissToast,
    markAllRead,
    dismissAlert,
    pinAlert,
    refetch: fetchAlerts,
  };
}
