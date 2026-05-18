import { useTheme } from "../ThemeContext";
import { FONT, SPACE } from "../theme";
import Sidebar from "./Sidebar";
import TopBar from "./TopBar";

export default function NewLayout({
  activeTab,
  onTabChange,
  nifty,
  banknifty,
  vix,
  pcr,
  liveStatus,
  onSearchClick,
  onAlertsClick,
  alertCount,
  tabBadges,
  flashingTab,
  onThemeToggle,
  onSettingsClick,
  onHelpClick,
  watchlist,
  onWatchlistClick,
  children,
}) {
  const { theme } = useTheme();

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        width: "100vw",
        background: theme.BG,
        color: theme.TEXT,
        fontFamily: FONT.UI,
        overflow: "hidden",
      }}
    >
      <TopBar
        nifty={nifty}
        banknifty={banknifty}
        vix={vix}
        pcr={pcr}
        liveStatus={liveStatus}
        onSearchClick={onSearchClick}
        onAlertsClick={onAlertsClick}
        alertCount={alertCount}
        onThemeToggle={onThemeToggle}
        onSettingsClick={onSettingsClick}
        onHelpClick={onHelpClick}
      />

      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        <Sidebar
          activeTab={activeTab}
          onTabChange={onTabChange}
          tabBadges={tabBadges}
          flashingTab={flashingTab}
          watchlist={watchlist}
          onWatchlistClick={onWatchlistClick}
        />

        <main
          style={{
            flex: 1,
            overflow: "auto",
            padding: SPACE.LG,
            background: theme.BG,
          }}
        >
          {children}
        </main>
      </div>
    </div>
  );
}
