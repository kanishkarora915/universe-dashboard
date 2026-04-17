// Universe Pro — new layout wrapper
// Wraps the existing Universe.jsx tab content with the new shell:
// TopBar + Sidebar + Strike Search + Alerts + Hotkeys + Theme

import { useState, useEffect, useMemo, useCallback } from "react";
import { useTheme } from "./ThemeContext";
import NewLayout from "./components/NewLayout";
import StrikeSearch from "./components/StrikeSearch";
import StrikeDetail from "./components/StrikeDetail";
import AlertToastStack from "./components/AlertToast";
import AlertDrawer from "./components/AlertDrawer";
import HotkeyHelp from "./components/HotkeyHelp";
import ReplayMode from "./components/ReplayMode";
import { VerdictHero } from "./components/DashboardHero";
import { useHotkeys } from "./hooks/useHotkeys";
import { useAlerts } from "./hooks/useAlerts";
import { useWatchlist } from "./hooks/useWatchlist";
import { SPACE, RADIUS, FONT, TEXT_SIZE, TEXT_WEIGHT } from "./theme";

function Placeholder({ title, theme }) {
  return (
    <div
      style={{
        padding: SPACE.XXXL,
        textAlign: "center",
        color: theme.TEXT_DIM,
        background: theme.SURFACE,
        border: `1px solid ${theme.BORDER}`,
        borderRadius: RADIUS.LG,
      }}
    >
      <div
        style={{
          color: theme.TEXT_MUTED,
          fontSize: TEXT_SIZE.H1,
          fontWeight: TEXT_WEIGHT.BOLD,
          marginBottom: SPACE.SM,
        }}
      >
        {title}
      </div>
      <div style={{ fontSize: TEXT_SIZE.BODY }}>Wire to existing Universe.jsx tab component</div>
    </div>
  );
}

export default function UniverseShell({
  liveData,           // { nifty: {...}, banknifty: {...}, vix, pcr, ... }
  signalData,         // from signal engine
  chainData,          // option chain for search suggestions
  onLogout,
  renderTabContent,   // fn(tabId) -> ReactNode  (bridges to existing Universe.jsx tabs)
}) {
  const { theme, toggle: toggleTheme } = useTheme();
  const [activeTab, setActiveTab] = useState(() => {
    const p = new URLSearchParams(window.location.search);
    return p.get("tab") || "dashboard";
  });
  const [searchOpen, setSearchOpen] = useState(false);
  const [alertsOpen, setAlertsOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [replayOpen, setReplayOpen] = useState(false);
  const [activeStrike, setActiveStrike] = useState(null);
  const [strikeTabs, setStrikeTabs] = useState([]); // open strike detail tabs
  const [selectedIndex, setSelectedIndex] = useState("NIFTY");

  const watchlist = useWatchlist();
  const {
    alerts, counts, toasts, flashingTab,
    dismissToast, markAllRead, dismissAlert, pinAlert,
  } = useAlerts({ activeTab });

  // Persist active tab in URL
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    p.set("tab", activeTab);
    window.history.replaceState({}, "", `/?${p.toString()}`);
  }, [activeTab]);

  // Build strike suggestions from chain data
  const suggestions = useMemo(() => {
    if (!chainData) return [];
    const out = [];
    ["NIFTY", "BANKNIFTY"].forEach((idx) => {
      const strikes = chainData[idx.toLowerCase()]?.strikes || chainData[idx]?.strikes || [];
      strikes.forEach((s) => {
        out.push({ index: idx, strike: s.strike, type: "CE", ltp: s.ceLTP, isATM: s.isATM });
        out.push({ index: idx, strike: s.strike, type: "PE", ltp: s.peLTP, isATM: s.isATM });
      });
    });
    return out;
  }, [chainData]);

  const quickJumps = useMemo(() => {
    const out = [];
    if (liveData?.nifty?.atm) out.push({ index: "NIFTY", strike: liveData.nifty.atm, label: "ATM Nifty", badge: "ATM" });
    if (liveData?.banknifty?.atm) out.push({ index: "BANKNIFTY", strike: liveData.banknifty.atm, label: "ATM BN", badge: "ATM" });
    if (liveData?.nifty?.maxPain) out.push({ index: "NIFTY", strike: liveData.nifty.maxPain, label: "Max Pain Nifty", badge: "MaxPain" });
    if (liveData?.banknifty?.maxPain) out.push({ index: "BANKNIFTY", strike: liveData.banknifty.maxPain, label: "Max Pain BN", badge: "MaxPain" });
    return out;
  }, [liveData]);

  const openStrike = useCallback((strike) => {
    const key = `${strike.index}-${strike.strike}`;
    if (!strikeTabs.find((s) => `${s.index}-${s.strike}` === key)) {
      setStrikeTabs((tabs) => [...tabs.slice(-4), strike]); // max 5
    }
    setActiveStrike(strike);
    setActiveTab(`strike:${key}`);
  }, [strikeTabs]);

  const closeStrike = useCallback((strike) => {
    const key = `${strike.index}-${strike.strike}`;
    setStrikeTabs((tabs) => tabs.filter((s) => `${s.index}-${s.strike}` !== key));
    if (activeTab === `strike:${key}`) {
      setActiveTab("dashboard");
      setActiveStrike(null);
    }
  }, [activeTab]);

  // Hotkeys
  useHotkeys({
    "cmd+k": () => setSearchOpen(true),
    "ctrl+k": () => setSearchOpen(true),
    "escape": () => {
      if (searchOpen) setSearchOpen(false);
      else if (alertsOpen) setAlertsOpen(false);
      else if (helpOpen) setHelpOpen(false);
      else if (replayOpen) setReplayOpen(false);
    },
    "?": () => setHelpOpen(true),
    "cmd+shift+l": () => toggleTheme(),
    "ctrl+shift+l": () => toggleTheme(),
    "cmd+shift+a": () => setAlertsOpen((o) => !o),
    "ctrl+shift+a": () => setAlertsOpen((o) => !o),
    "n": () => setSelectedIndex((i) => (i === "NIFTY" ? "BANKNIFTY" : "NIFTY")),
    "1": () => setActiveTab("dashboard"),
    "2": () => setActiveTab("oi"),
    "3": () => setActiveTab("pnl"),
    "4": () => setActiveTab("reports"),
    "5": () => setActiveTab("autopsy"),
    "6": () => setActiveTab("times"),
  });

  const isLiveFresh = liveData?.dataQuality === "fresh" || liveData?.lastTick ? true : false;
  const liveStatus = !liveData
    ? "disconnected"
    : liveData.dataQuality === "stale"
    ? "stale"
    : liveData.dataQuality === "lag"
    ? "lag"
    : "live";

  const tabBadges = counts?.byTab || {};

  // Active content
  const activeStrikeTab = activeTab?.startsWith("strike:")
    ? strikeTabs.find((s) => `${s.index}-${s.strike}` === activeTab.slice(7))
    : null;

  return (
    <>
      <NewLayout
        activeTab={activeTab?.startsWith("strike:") ? null : activeTab}
        onTabChange={setActiveTab}
        nifty={liveData?.nifty}
        banknifty={liveData?.banknifty}
        vix={liveData?.vix}
        pcr={liveData?.pcr}
        liveStatus={liveStatus}
        onSearchClick={() => setSearchOpen(true)}
        onAlertsClick={() => setAlertsOpen(true)}
        alertCount={counts?.total || 0}
        tabBadges={tabBadges}
        flashingTab={flashingTab}
        onThemeToggle={toggleTheme}
        onSettingsClick={() => setActiveTab("settings")}
        onHelpClick={() => setHelpOpen(true)}
        watchlist={watchlist.pinned}
        onWatchlistClick={openStrike}
      >
        {/* Strike tabs strip (when any strike tabs open) */}
        {strikeTabs.length > 0 && (
          <div
            style={{
              display: "flex",
              gap: 0,
              marginBottom: SPACE.MD,
              borderBottom: `1px solid ${theme.BORDER}`,
              overflowX: "auto",
            }}
          >
            {strikeTabs.map((s) => {
              const key = `${s.index}-${s.strike}`;
              const isActive = activeTab === `strike:${key}`;
              return (
                <div
                  key={key}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: SPACE.SM,
                    padding: `6px ${SPACE.MD}px`,
                    background: isActive ? theme.SURFACE_ACTIVE : "transparent",
                    borderBottom: isActive ? `2px solid ${theme.ACCENT}` : "2px solid transparent",
                    cursor: "pointer",
                    fontFamily: FONT.MONO,
                    fontSize: TEXT_SIZE.BODY,
                    color: isActive ? theme.TEXT : theme.TEXT_MUTED,
                    fontWeight: TEXT_WEIGHT.BOLD,
                  }}
                  onClick={() => setActiveTab(`strike:${key}`)}
                >
                  <span>{s.index} {s.strike}{s.type ? ` ${s.type}` : ""}</span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      closeStrike(s);
                    }}
                    style={{
                      background: "transparent",
                      border: "none",
                      color: theme.TEXT_DIM,
                      cursor: "pointer",
                      padding: 0,
                      fontSize: 14,
                      lineHeight: 1,
                    }}
                  >
                    \u00D7
                  </button>
                </div>
              );
            })}
          </div>
        )}

        {/* Active content */}
        {activeStrikeTab ? (
          <StrikeDetail
            strike={activeStrikeTab}
            onClose={() => closeStrike(activeStrikeTab)}
            onPin={watchlist.togglePin}
            pinned={watchlist.isPinned(activeStrikeTab)}
            liveData={liveData}
          />
        ) : activeTab === "dashboard" ? (
          <div style={{ display: "flex", flexDirection: "column", gap: SPACE.MD }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: SPACE.MD }}>
              <VerdictHero
                index="NIFTY"
                verdict={signalData?.nifty}
                reasons={signalData?.nifty?.reasons || []}
              />
              <VerdictHero
                index="BANKNIFTY"
                verdict={signalData?.banknifty}
                reasons={signalData?.banknifty?.reasons || []}
              />
            </div>
            {renderTabContent && renderTabContent("dashboard")}
          </div>
        ) : activeTab === "replay" ? (
          <ReplayMode
            index={selectedIndex}
            isOpen={true}
            onClose={() => setActiveTab("dashboard")}
          />
        ) : activeTab === "settings" ? (
          <Placeholder title="Settings (coming)" theme={theme} />
        ) : (
          renderTabContent && renderTabContent(activeTab)
        )}
      </NewLayout>

      {/* Overlays */}
      <StrikeSearch
        isOpen={searchOpen}
        onClose={() => setSearchOpen(false)}
        onSelect={openStrike}
        suggestions={suggestions}
        quickJumps={quickJumps}
        watchlist={watchlist}
      />

      <AlertDrawer
        isOpen={alertsOpen}
        onClose={() => setAlertsOpen(false)}
        alerts={alerts}
        onPin={pinAlert}
        onDismiss={dismissAlert}
        onMarkAllRead={markAllRead}
        onAlertClick={(a) => {
          if (a.tab) setActiveTab(a.tab);
          setAlertsOpen(false);
        }}
      />

      <HotkeyHelp isOpen={helpOpen} onClose={() => setHelpOpen(false)} />

      <AlertToastStack
        toasts={toasts}
        onDismiss={dismissToast}
        onClickAlert={(a) => {
          if (a.tab) setActiveTab(a.tab);
          dismissToast(a.toastId);
        }}
      />
    </>
  );
}
