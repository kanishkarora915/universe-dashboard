import { Component } from "react";
import { DARK } from "../theme";

/**
 * Catches runtime errors inside tab content so a crashing tab doesn't blank
 * the whole dashboard. Shows error + reset button instead of white screen.
 *
 * Uses DARK theme constants directly since ErrorBoundary is a class component
 * and can't use useTheme hook. Errors are rare and usually need to be visible,
 * so dark theme as fallback is acceptable.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, info: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", error, info);
    this.setState({ info });
  }

  reset = () => {
    this.setState({ hasError: false, error: null, info: null });
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    const err = this.state.error;
    const msg = (err && (err.message || String(err))) || "Unknown error";
    const stack = (err && err.stack) || "";
    const t = DARK;

    return (
      <div
        role="alert"
        style={{
          padding: "24px",
          background: t.SURFACE_HI,
          border: `1px solid ${t.RED}44`,
          borderLeft: `3px solid ${t.RED}`,
          borderRadius: 8,
          color: t.TEXT,
          fontFamily: "'Inter', -apple-system, sans-serif",
          margin: "24px 0",
        }}
      >
        <div
          style={{
            color: t.RED,
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: 1.5,
            textTransform: "uppercase",
            marginBottom: 6,
          }}
        >
          Tab crashed
        </div>
        <div
          style={{
            fontSize: 16,
            fontWeight: 700,
            marginBottom: 8,
            fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          {msg}
        </div>
        <div
          style={{
            color: t.TEXT_MUTED,
            fontSize: 11,
            marginBottom: 16,
          }}
        >
          Something in this tab threw an error. Try reset, or switch to another tab and come back.
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={this.reset}
            aria-label="Reset this tab"
            style={{
              background: t.ACCENT,
              color: "#fff",
              border: "none",
              borderRadius: 6,
              padding: "6px 14px",
              fontSize: 12,
              fontWeight: 700,
              cursor: "pointer",
            }}
          >
            Reset this tab
          </button>
          <button
            onClick={() => window.location.reload()}
            aria-label="Reload entire page"
            style={{
              background: "transparent",
              color: t.TEXT_MUTED,
              border: `1px solid ${t.BORDER_HI}`,
              borderRadius: 6,
              padding: "6px 14px",
              fontSize: 12,
              fontWeight: 700,
              cursor: "pointer",
            }}
          >
            Reload page
          </button>
        </div>
        {stack && (
          <details style={{ marginTop: 16 }}>
            <summary
              style={{
                color: t.TEXT_DIM,
                fontSize: 10,
                cursor: "pointer",
                userSelect: "none",
              }}
            >
              Stack trace
            </summary>
            <pre
              style={{
                color: t.TEXT_MUTED,
                fontSize: 10,
                marginTop: 8,
                padding: 12,
                background: t.BG,
                borderRadius: 4,
                overflow: "auto",
                maxHeight: 240,
                fontFamily: "'JetBrains Mono', monospace",
              }}
            >
              {stack}
            </pre>
          </details>
        )}
      </div>
    );
  }
}
