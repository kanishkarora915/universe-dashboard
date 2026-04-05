import { useState, useEffect } from "react";
import Universe from "./Universe";
import Login from "./Login";
import { fetchStatus, logout } from "./api";

function App() {
  const [authenticated, setAuthenticated] = useState(false);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const authParam = params.get("auth");

    if (authParam === "success") {
      window.history.replaceState({}, "", "/");
      setAuthenticated(true);
      setChecking(false);
      return;
    }

    if (authParam === "failed") {
      window.history.replaceState({}, "", "/");
      setAuthenticated(false);
      setChecking(false);
      return;
    }

    fetchStatus()
      .then((data) => {
        // Show dashboard if engine running OR cached data exists
        setAuthenticated(data.authenticated || data.has_cached_data || data.engine_running);
        setChecking(false);
      })
      .catch(() => {
        setAuthenticated(false);
        setChecking(false);
      });
  }, []);

  const handleLogout = async () => {
    try {
      await logout();
    } catch (e) {
      // ignore
    }
    setAuthenticated(false);
  };

  if (checking) {
    return (
      <div style={{
        background: "#0A0A0F", minHeight: "100vh",
        display: "flex", alignItems: "center", justifyContent: "center",
        color: "#555", fontSize: 14,
        fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif",
      }}>
        Connecting to UNIVERSE...
      </div>
    );
  }

  return authenticated ? <Universe onLogout={handleLogout} /> : <Login />;
}

export default App;
