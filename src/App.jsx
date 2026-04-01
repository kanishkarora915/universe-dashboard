import { useState, useEffect } from "react";
import Universe from "./Universe";
import Login from "./Login";
import { fetchStatus } from "./api";

function App() {
  const [authenticated, setAuthenticated] = useState(false);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    // Check URL params for auth callback
    const params = new URLSearchParams(window.location.search);
    const authParam = params.get("auth");

    if (authParam === "success") {
      // Clean URL
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

    // Check backend status
    fetchStatus()
      .then((data) => {
        setAuthenticated(data.authenticated && data.engine_running);
        setChecking(false);
      })
      .catch(() => {
        setAuthenticated(false);
        setChecking(false);
      });
  }, []);

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

  return authenticated ? <Universe /> : <Login />;
}

export default App;
