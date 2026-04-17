import { Component } from "react";

/**
 * Catches runtime errors inside tab content so a crashing tab doesn't blank
 * the whole dashboard. Shows error + reset button instead of white screen.
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

    return (
      <div
        style={{
          padding: "24px",
          background: "#18181F",
          border: "1px solid #FF453A44",
          borderLeft: "3px solid #FF453A",
          borderRadius: 8,
          color: "#FFFFFF",
          fontFamily: "'Inter', -apple-system, sans-serif",
          margin: "24px 0",
        }}
      >
        <div
          style={{
            color: "#FF453A",
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
            color: "#888",
            fontSize: 11,
            marginBottom: 16,
          }}
        >
          Something in this tab threw an error. Try reset, or switch to another tab and come back.
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={this.reset}
            style={{
              background: "#0A84FF",
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
            style={{
              background: "transparent",
              color: "#888",
              border: "1px solid #2A2A3A",
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
                color: "#555",
                fontSize: 10,
                cursor: "pointer",
                userSelect: "none",
              }}
            >
              Stack trace
            </summary>
            <pre
              style={{
                color: "#666",
                fontSize: 10,
                marginTop: 8,
                padding: 12,
                background: "#0A0A0F",
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
