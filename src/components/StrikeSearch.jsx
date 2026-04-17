import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION, Z } from "../theme";

function fuzzyMatch(query, items) {
  if (!query) return items;
  const q = query.toLowerCase().replace(/\s+/g, " ").trim();
  const tokens = q.split(" ").filter(Boolean);
  return items
    .map((item) => {
      const hay = `${item.index} ${item.strike} ${item.type || ""} ${item.label || ""}`.toLowerCase();
      const allMatch = tokens.every((t) => hay.includes(t));
      if (!allMatch) return null;
      // rank by earliest match position
      const score = tokens.reduce((acc, t) => acc + hay.indexOf(t), 0);
      return { item, score };
    })
    .filter(Boolean)
    .sort((a, b) => a.score - b.score)
    .map((x) => x.item);
}

function Row({ strike, active, onClick, onPin, pinned, theme }) {
  const color = strike.type === "CE" ? theme.GREEN : strike.type === "PE" ? theme.RED : theme.TEXT;
  return (
    <div
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: SPACE.MD,
        padding: `${SPACE.SM}px ${SPACE.MD}px`,
        background: active ? theme.SURFACE_ACTIVE : "transparent",
        borderLeft: active ? `2px solid ${theme.ACCENT}` : "2px solid transparent",
        cursor: "pointer",
        transition: TRANSITION.FAST,
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = theme.SURFACE_HI)}
      onMouseLeave={(e) => (e.currentTarget.style.background = active ? theme.SURFACE_ACTIVE : "transparent")}
    >
      <span
        style={{
          color: theme.TEXT_MUTED,
          fontSize: TEXT_SIZE.MICRO,
          fontWeight: TEXT_WEIGHT.BOLD,
          minWidth: 70,
          fontFamily: FONT.UI,
          letterSpacing: 0.5,
        }}
      >
        {strike.index}
      </span>
      <span
        style={{
          color: theme.TEXT,
          fontSize: 14,
          fontWeight: TEXT_WEIGHT.BOLD,
          fontFamily: FONT.MONO,
          minWidth: 60,
        }}
      >
        {strike.strike}
      </span>
      {strike.type && (
        <span
          style={{
            color,
            fontSize: TEXT_SIZE.MICRO,
            fontWeight: TEXT_WEIGHT.BOLD,
            padding: "2px 6px",
            background: color + "22",
            borderRadius: RADIUS.XS,
            fontFamily: FONT.UI,
            letterSpacing: 0.5,
          }}
        >
          {strike.type}
        </span>
      )}
      {strike.ltp != null && (
        <span
          style={{
            color: theme.TEXT,
            fontSize: TEXT_SIZE.BODY,
            fontWeight: TEXT_WEIGHT.BOLD,
            fontFamily: FONT.MONO,
            marginLeft: "auto",
          }}
        >
          ₹{strike.ltp}
        </span>
      )}
      {strike.badge && (
        <span
          style={{
            color: theme.ACCENT,
            fontSize: TEXT_SIZE.MICRO,
            fontWeight: TEXT_WEIGHT.BOLD,
            padding: "2px 6px",
            background: theme.ACCENT_DIM,
            borderRadius: RADIUS.XS,
            fontFamily: FONT.UI,
          }}
        >
          {strike.badge}
        </span>
      )}
      {onPin && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onPin(strike);
          }}
          title={pinned ? "Unpin" : "Pin to watchlist"}
          style={{
            background: "transparent",
            border: "none",
            color: pinned ? theme.AMBER : theme.TEXT_DIM,
            cursor: "pointer",
            fontSize: 14,
            padding: 4,
          }}
        >
          {pinned ? "★" : "☆"}
        </button>
      )}
    </div>
  );
}

function Section({ title, count, children, action, theme }) {
  return (
    <div style={{ marginBottom: SPACE.MD }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          padding: `${SPACE.XS}px ${SPACE.MD}px`,
        }}
      >
        <span
          style={{
            color: theme.TEXT_DIM,
            fontSize: TEXT_SIZE.MICRO,
            fontWeight: TEXT_WEIGHT.BOLD,
            letterSpacing: 1.5,
            textTransform: "uppercase",
            flex: 1,
          }}
        >
          {title} {count != null && <span style={{ color: theme.TEXT_MUTED }}>({count})</span>}
        </span>
        {action}
      </div>
      {children}
    </div>
  );
}

export default function StrikeSearch({ isOpen, onClose, onSelect, suggestions = [], quickJumps = [], watchlist }) {
  const { theme } = useTheme();
  const [query, setQuery] = useState("");
  const [activeIdx, setActiveIdx] = useState(0);
  const inputRef = useRef(null);

  const { recent = [], pinned = [], addRecent, clearRecent, isPinned, togglePin } = watchlist || {};

  useEffect(() => {
    if (isOpen) {
      setQuery("");
      setActiveIdx(0);
      setTimeout(() => inputRef.current?.focus(), 10);
    }
  }, [isOpen]);

  const matches = useMemo(() => fuzzyMatch(query, suggestions), [query, suggestions]);

  const displayList = query ? matches : [];

  const handleSelect = useCallback(
    (strike) => {
      if (!strike) return;
      if (addRecent) addRecent(strike);
      if (onSelect) onSelect(strike);
      onClose();
    },
    [addRecent, onSelect, onClose]
  );

  useEffect(() => {
    if (!isOpen) return;
    const handler = (e) => {
      if (e.key === "Escape") {
        onClose();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIdx((i) => Math.min(i + 1, displayList.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIdx((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (displayList[activeIdx]) handleSelect(displayList[activeIdx]);
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [isOpen, displayList, activeIdx, handleSelect, onClose]);

  if (!isOpen) return null;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: theme.OVERLAY,
        zIndex: Z.MODAL,
        display: "flex",
        justifyContent: "center",
        paddingTop: "10vh",
        backdropFilter: "blur(4px)",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(640px, 90vw)",
          maxHeight: "70vh",
          background: theme.SURFACE,
          border: `1px solid ${theme.BORDER_HI}`,
          borderRadius: RADIUS.LG,
          boxShadow: theme.SHADOW_HI,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {/* Input */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: SPACE.SM,
            padding: SPACE.MD,
            borderBottom: `1px solid ${theme.BORDER}`,
          }}
        >
          <span style={{ color: theme.TEXT_DIM, fontSize: 16 }}>⌕</span>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActiveIdx(0);
            }}
            placeholder="Search strike (22900, nifty 22900 ce, bn 48500...)"
            style={{
              flex: 1,
              background: "transparent",
              border: "none",
              outline: "none",
              color: theme.TEXT,
              fontSize: 15,
              fontFamily: FONT.UI,
              fontWeight: TEXT_WEIGHT.MED,
            }}
          />
          <span
            style={{
              color: theme.TEXT_DIM,
              fontSize: 10,
              padding: "2px 6px",
              background: theme.BG,
              borderRadius: RADIUS.XS,
              fontFamily: FONT.MONO,
            }}
          >
            ESC
          </span>
        </div>

        {/* Results / suggestions */}
        <div style={{ flex: 1, overflowY: "auto", padding: `${SPACE.SM}px 0` }}>
          {query && displayList.length === 0 && (
            <div
              style={{
                padding: SPACE.XL,
                textAlign: "center",
                color: theme.TEXT_DIM,
                fontSize: TEXT_SIZE.BODY,
              }}
            >
              No matches for "{query}"
            </div>
          )}

          {query && displayList.length > 0 && (
            <Section title={`Matches`} count={displayList.length} theme={theme}>
              {displayList.slice(0, 20).map((s, i) => (
                <Row
                  key={`m-${i}`}
                  strike={s}
                  active={i === activeIdx}
                  onClick={() => handleSelect(s)}
                  onPin={togglePin}
                  pinned={isPinned && isPinned(s)}
                  theme={theme}
                />
              ))}
            </Section>
          )}

          {!query && pinned && pinned.length > 0 && (
            <Section title="Pinned" count={pinned.length} theme={theme}>
              {pinned.map((s, i) => (
                <Row
                  key={`p-${i}`}
                  strike={{ ...s, badge: "pinned" }}
                  onClick={() => handleSelect(s)}
                  onPin={togglePin}
                  pinned={true}
                  theme={theme}
                />
              ))}
            </Section>
          )}

          {!query && recent && recent.length > 0 && (
            <Section
              title="Recent"
              count={recent.length}
              action={
                <button
                  onClick={clearRecent}
                  style={{
                    background: "transparent",
                    border: "none",
                    color: theme.TEXT_DIM,
                    fontSize: TEXT_SIZE.MICRO,
                    cursor: "pointer",
                    fontFamily: FONT.UI,
                  }}
                >
                  Clear
                </button>
              }
              theme={theme}
            >
              {recent.map((s, i) => (
                <Row
                  key={`r-${i}`}
                  strike={s}
                  onClick={() => handleSelect(s)}
                  onPin={togglePin}
                  pinned={isPinned && isPinned(s)}
                  theme={theme}
                />
              ))}
            </Section>
          )}

          {!query && quickJumps && quickJumps.length > 0 && (
            <Section title="Quick Jumps" theme={theme}>
              {quickJumps.map((s, i) => (
                <Row key={`q-${i}`} strike={s} onClick={() => handleSelect(s)} theme={theme} />
              ))}
            </Section>
          )}

          {!query && (!recent || recent.length === 0) && (!pinned || pinned.length === 0) && (!quickJumps || quickJumps.length === 0) && (
            <div
              style={{
                padding: SPACE.XL,
                textAlign: "center",
                color: theme.TEXT_DIM,
                fontSize: TEXT_SIZE.BODY,
              }}
            >
              Start typing to search strikes
            </div>
          )}
        </div>

        {/* Footer hints */}
        <div
          style={{
            display: "flex",
            gap: SPACE.LG,
            padding: `${SPACE.SM}px ${SPACE.MD}px`,
            borderTop: `1px solid ${theme.BORDER}`,
            color: theme.TEXT_DIM,
            fontSize: TEXT_SIZE.MICRO,
            fontFamily: FONT.UI,
          }}
        >
          <span>
            <kbd style={{ fontFamily: FONT.MONO, padding: "1px 4px", background: theme.BG, borderRadius: 2 }}>↑↓</kbd>{" "}
            navigate
          </span>
          <span>
            <kbd style={{ fontFamily: FONT.MONO, padding: "1px 4px", background: theme.BG, borderRadius: 2 }}>↵</kbd>{" "}
            open
          </span>
          <span>
            <kbd style={{ fontFamily: FONT.MONO, padding: "1px 4px", background: theme.BG, borderRadius: 2 }}>☆</kbd>{" "}
            pin
          </span>
          <span style={{ marginLeft: "auto" }}>Universe Pro</span>
        </div>
      </div>
    </div>
  );
}
