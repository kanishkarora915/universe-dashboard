import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { useTheme } from "../ThemeContext";
import { FONT, TEXT_SIZE, TEXT_WEIGHT, SPACE, RADIUS, TRANSITION, Z } from "../theme";

/**
 * STRIKE SEARCH — Options Chain Browser
 *
 * Layout (TradingView-inspired):
 * ┌──────────────────────────────────────────────┐
 * │ 🔍 Type strike (e.g., 24400 or nifty 25000) │
 * ├──────────────────────────────────────────────┤
 * │ [NIFTY] [BN] [BOTH]                          │ Index toggle
 * │ Apr 24  May 1  May 8  May 29  Jun 26 ...     │ Expiry pills (horizontal)
 * ├──────────────────────────────────────────────┤
 * │ NIFTY · 24 Apr                               │
 * │  CE LTP    Strike    PE LTP    CE OI   PE OI │
 * │  ₹195      24200     ₹58       1.2L    2.1L  │
 * │  ₹158      24400 ATM ₹92       4.2L    5.1L  │
 * │  ₹12       25000     ₹650      0.3L    0.1L  │
 * │                                              │
 * │ BANKNIFTY · 24 Apr                           │
 * │  CE LTP    Strike    PE LTP    CE OI   PE OI │
 * │  ...                                         │
 * └──────────────────────────────────────────────┘
 *
 * Users can pin any strike with ☆ for Battle Station comparison.
 */

async function fetchJSON(url) {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

function Row({ ceLTP, peLTP, strike, ceOI, peOI, index, isATM, pinned, onPin, onSelect, theme }) {
  const rowBg = isATM ? theme.ACCENT + "08" : "transparent";
  const rowBorder = isATM ? theme.ACCENT + "44" : theme.BORDER + "22";
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "60px 70px 1fr 70px 60px 60px 28px 28px 22px",
        gap: 4,
        alignItems: "center",
        padding: `6px ${SPACE.SM}px`,
        borderBottom: `1px solid ${rowBorder}`,
        background: rowBg,
        fontSize: TEXT_SIZE.MICRO,
        fontFamily: FONT.MONO,
      }}
    >
      {/* CE LTP */}
      <span
        style={{
          color: theme.GREEN,
          fontWeight: TEXT_WEIGHT.BOLD,
          textAlign: "right",
          cursor: "pointer",
        }}
        onClick={() => onSelect && onSelect({ index, strike, type: "CE", ltp: ceLTP })}
        title="Click to open CE details"
      >
        {ceLTP > 0 ? `₹${ceLTP}` : "—"}
      </span>
      {/* CE OI */}
      <span style={{ color: theme.TEXT_MUTED, textAlign: "right" }}>
        {ceOI > 0 ? `${(ceOI / 100000).toFixed(1)}L` : "—"}
      </span>
      {/* Strike */}
      <span
        style={{
          color: isATM ? theme.ACCENT : theme.TEXT,
          fontWeight: TEXT_WEIGHT.BOLD,
          textAlign: "center",
          fontSize: TEXT_SIZE.BODY,
        }}
      >
        {strike}
        {isATM && (
          <span
            style={{
              marginLeft: 4,
              color: theme.ACCENT,
              fontSize: 8,
              fontFamily: FONT.UI,
              fontWeight: TEXT_WEIGHT.BOLD,
              letterSpacing: 1,
              padding: "1px 4px",
              background: theme.ACCENT_DIM,
              borderRadius: 2,
            }}
          >
            ATM
          </span>
        )}
      </span>
      {/* PE LTP */}
      <span
        style={{
          color: theme.RED,
          fontWeight: TEXT_WEIGHT.BOLD,
          cursor: "pointer",
        }}
        onClick={() => onSelect && onSelect({ index, strike, type: "PE", ltp: peLTP })}
        title="Click to open PE details"
      >
        {peLTP > 0 ? `₹${peLTP}` : "—"}
      </span>
      {/* PE OI */}
      <span style={{ color: theme.TEXT_MUTED, textAlign: "right" }}>
        {peOI > 0 ? `${(peOI / 100000).toFixed(1)}L` : "—"}
      </span>
      <span />
      {/* Pin CE */}
      <button
        onClick={(e) => {
          e.stopPropagation();
          onPin && onPin({ index, strike, type: "CE", ltp: ceLTP });
        }}
        aria-label={`Pin ${index} ${strike} CE to watchlist`}
        title="Pin CE"
        style={{
          background: "transparent",
          border: "none",
          color: pinned?.ce ? theme.GREEN : theme.TEXT_DIM,
          cursor: "pointer",
          fontSize: 11,
          padding: 0,
          lineHeight: 1,
        }}
      >
        {pinned?.ce ? "★" : "☆"}
      </button>
      {/* Pin PE */}
      <button
        onClick={(e) => {
          e.stopPropagation();
          onPin && onPin({ index, strike, type: "PE", ltp: peLTP });
        }}
        aria-label={`Pin ${index} ${strike} PE to watchlist`}
        title="Pin PE"
        style={{
          background: "transparent",
          border: "none",
          color: pinned?.pe ? theme.RED : theme.TEXT_DIM,
          cursor: "pointer",
          fontSize: 11,
          padding: 0,
          lineHeight: 1,
        }}
      >
        {pinned?.pe ? "★" : "☆"}
      </button>
      <span />
    </div>
  );
}

function HeaderRow({ theme }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "60px 70px 1fr 70px 60px 60px 28px 28px 22px",
        gap: 4,
        padding: `4px ${SPACE.SM}px`,
        borderBottom: `1px solid ${theme.BORDER_HI}`,
        background: theme.SURFACE_HI,
        fontSize: 9,
        fontWeight: TEXT_WEIGHT.BOLD,
        color: theme.TEXT_DIM,
        letterSpacing: 1,
        textTransform: "uppercase",
        fontFamily: FONT.UI,
        position: "sticky",
        top: 0,
        zIndex: 2,
      }}
    >
      <span style={{ textAlign: "right", color: theme.GREEN }}>CE LTP</span>
      <span style={{ textAlign: "right" }}>CE OI</span>
      <span style={{ textAlign: "center" }}>STRIKE</span>
      <span style={{ color: theme.RED }}>PE LTP</span>
      <span style={{ textAlign: "right" }}>PE OI</span>
      <span />
      <span style={{ textAlign: "center" }}>CE</span>
      <span style={{ textAlign: "center" }}>PE</span>
      <span />
    </div>
  );
}

function ExpiryPill({ label, active, onClick, theme }) {
  return (
    <button
      onClick={onClick}
      style={{
        background: active ? theme.ACCENT : "transparent",
        color: active ? "#fff" : theme.TEXT_MUTED,
        border: `1px solid ${active ? theme.ACCENT : theme.BORDER}`,
        borderRadius: RADIUS.SM,
        padding: "4px 10px",
        fontSize: TEXT_SIZE.MICRO,
        fontWeight: TEXT_WEIGHT.BOLD,
        cursor: "pointer",
        whiteSpace: "nowrap",
        transition: TRANSITION.FAST,
        fontFamily: FONT.UI,
        letterSpacing: 0.5,
      }}
    >
      {label}
    </button>
  );
}

function ChainSection({ index, expiry, strikes, query, isPinned, togglePin, onSelect, theme }) {
  // Filter by query if numeric
  const filtered = useMemo(() => {
    const q = (query || "").trim();
    const num = parseInt(q.replace(/\D/g, ""), 10);
    if (!isNaN(num) && num > 0) {
      return strikes.filter((s) => String(s.strike).includes(String(num)));
    }
    return strikes;
  }, [strikes, query]);

  if (!filtered.length) {
    return (
      <div
        style={{
          padding: SPACE.MD,
          color: theme.TEXT_DIM,
          fontSize: TEXT_SIZE.MICRO,
          textAlign: "center",
        }}
      >
        No strikes match "{query}" for {index}
      </div>
    );
  }

  return (
    <div style={{ marginBottom: SPACE.MD }}>
      {/* Section header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: `${SPACE.SM}px ${SPACE.MD}px`,
          background: theme.BG,
          borderTop: `1px solid ${theme.BORDER}`,
          position: "sticky",
          top: 0,
          zIndex: 1,
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: SPACE.SM }}>
          <span
            style={{
              color: index === "NIFTY" ? theme.ACCENT : theme.PURPLE,
              fontSize: TEXT_SIZE.BODY,
              fontWeight: TEXT_WEIGHT.BLACK,
              letterSpacing: 1.5,
              fontFamily: FONT.UI,
            }}
          >
            {index}
          </span>
          <span
            style={{
              color: theme.TEXT_MUTED,
              fontSize: TEXT_SIZE.MICRO,
              fontFamily: FONT.MONO,
            }}
          >
            · {expiry}
          </span>
        </div>
        <span
          style={{
            color: theme.TEXT_DIM,
            fontSize: TEXT_SIZE.MICRO,
            fontFamily: FONT.MONO,
          }}
        >
          {filtered.length} strikes
        </span>
      </div>

      <HeaderRow theme={theme} />

      {filtered.map((s) => {
        const pinState = {
          ce: isPinned && isPinned({ index, strike: s.strike, type: "CE" }),
          pe: isPinned && isPinned({ index, strike: s.strike, type: "PE" }),
        };
        return (
          <Row
            key={`${index}-${s.strike}`}
            index={index}
            strike={s.strike}
            ceLTP={s.ceLTP || s.ce_ltp || 0}
            peLTP={s.peLTP || s.pe_ltp || 0}
            ceOI={s.ceOI || s.ce_oi || 0}
            peOI={s.peOI || s.pe_oi || 0}
            isATM={s.isATM || s.is_atm}
            pinned={pinState}
            onPin={togglePin}
            onSelect={onSelect}
            theme={theme}
          />
        );
      })}
    </div>
  );
}

// Format expiry date: "2026-04-24" → "24 Apr"
function formatExpiry(dateStr) {
  if (!dateStr) return "";
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
  } catch {
    return dateStr;
  }
}

export default function StrikeSearch({ isOpen, onClose, onSelect, watchlist, onCompare }) {
  const { theme } = useTheme();
  const [query, setQuery] = useState("");
  const [indexFilter, setIndexFilter] = useState("BOTH"); // NIFTY | BANKNIFTY | BOTH
  const [niftyExpiries, setNiftyExpiries] = useState([]);
  const [bnExpiries, setBnExpiries] = useState([]);
  const [selectedNiftyExp, setSelectedNiftyExp] = useState("");
  const [selectedBnExp, setSelectedBnExp] = useState("");
  const [niftyChain, setNiftyChain] = useState([]);
  const [bnChain, setBnChain] = useState([]);
  const [loadingChain, setLoadingChain] = useState(false);
  const inputRef = useRef(null);

  const { pinned = [], isPinned, togglePin } = watchlist || {};

  // Load expiries on open
  useEffect(() => {
    if (!isOpen) return;
    setQuery("");
    setTimeout(() => inputRef.current?.focus(), 10);

    fetchJSON("/api/expiries/NIFTY").then((d) => {
      const list = Array.isArray(d) ? d : d?.expiries || [];
      setNiftyExpiries(list);
      if (list.length && !selectedNiftyExp) {
        const cur = list.find((e) => e.isCurrent) || list[0];
        setSelectedNiftyExp(cur.date || cur);
      }
    });
    fetchJSON("/api/expiries/BANKNIFTY").then((d) => {
      const list = Array.isArray(d) ? d : d?.expiries || [];
      setBnExpiries(list);
      if (list.length && !selectedBnExp) {
        const cur = list.find((e) => e.isCurrent) || list[0];
        setSelectedBnExp(cur.date || cur);
      }
    });
  }, [isOpen]);

  // Fetch chains when expiry changes
  useEffect(() => {
    if (!isOpen || !selectedNiftyExp || indexFilter === "BANKNIFTY") return;
    setLoadingChain(true);
    fetchJSON(`/api/expiry-chain/NIFTY/${encodeURIComponent(selectedNiftyExp)}`).then((d) => {
      setNiftyChain(d?.strikes || []);
      setLoadingChain(false);
    });
  }, [isOpen, selectedNiftyExp, indexFilter]);

  useEffect(() => {
    if (!isOpen || !selectedBnExp || indexFilter === "NIFTY") return;
    setLoadingChain(true);
    fetchJSON(`/api/expiry-chain/BANKNIFTY/${encodeURIComponent(selectedBnExp)}`).then((d) => {
      setBnChain(d?.strikes || []);
      setLoadingChain(false);
    });
  }, [isOpen, selectedBnExp, indexFilter]);

  const handleSelect = useCallback(
    (strike) => {
      if (!strike) return;
      const withExpiry = {
        ...strike,
        expiry: strike.index === "NIFTY" ? selectedNiftyExp : selectedBnExp,
      };
      if (watchlist?.addRecent) watchlist.addRecent(withExpiry);
      if (onSelect) onSelect(withExpiry);
      onClose();
    },
    [onSelect, onClose, selectedNiftyExp, selectedBnExp, watchlist]
  );

  const handleTogglePin = useCallback(
    (strike) => {
      const withExpiry = {
        ...strike,
        expiry: strike.index === "NIFTY" ? selectedNiftyExp : selectedBnExp,
      };
      if (togglePin) togglePin(withExpiry);
    },
    [togglePin, selectedNiftyExp, selectedBnExp]
  );

  // ESC to close
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const showNifty = indexFilter === "BOTH" || indexFilter === "NIFTY";
  const showBN = indexFilter === "BOTH" || indexFilter === "BANKNIFTY";

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
        paddingTop: "6vh",
        paddingBottom: "4vh",
        backdropFilter: "blur(4px)",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(780px, 94vw)",
          maxHeight: "88vh",
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
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by strike (24400) — or browse the full chain below"
            style={{
              flex: 1,
              background: "transparent",
              border: "none",
              outline: "none",
              color: theme.TEXT,
              fontSize: 14,
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

        {/* Index filter + Expiry pills */}
        <div
          style={{
            padding: `${SPACE.SM}px ${SPACE.MD}px`,
            borderBottom: `1px solid ${theme.BORDER}`,
            display: "flex",
            flexDirection: "column",
            gap: SPACE.SM,
          }}
        >
          <div style={{ display: "flex", gap: SPACE.XS, alignItems: "center" }}>
            <span
              style={{
                color: theme.TEXT_DIM,
                fontSize: 9,
                fontWeight: TEXT_WEIGHT.BOLD,
                letterSpacing: 1.5,
                textTransform: "uppercase",
                marginRight: SPACE.XS,
              }}
            >
              Index
            </span>
            {["BOTH", "NIFTY", "BANKNIFTY"].map((f) => (
              <button
                key={f}
                onClick={() => setIndexFilter(f)}
                style={{
                  background: indexFilter === f ? theme.ACCENT : "transparent",
                  color: indexFilter === f ? "#fff" : theme.TEXT_MUTED,
                  border: `1px solid ${indexFilter === f ? theme.ACCENT : theme.BORDER}`,
                  borderRadius: RADIUS.SM,
                  padding: "3px 10px",
                  fontSize: TEXT_SIZE.MICRO,
                  fontWeight: TEXT_WEIGHT.BOLD,
                  cursor: "pointer",
                  fontFamily: FONT.UI,
                }}
              >
                {f}
              </button>
            ))}
          </div>

          {showNifty && niftyExpiries.length > 0 && (
            <div style={{ display: "flex", gap: SPACE.XS, alignItems: "center", overflowX: "auto" }}>
              <span
                style={{
                  color: theme.ACCENT,
                  fontSize: 9,
                  fontWeight: TEXT_WEIGHT.BOLD,
                  letterSpacing: 1.5,
                  textTransform: "uppercase",
                  marginRight: SPACE.XS,
                  whiteSpace: "nowrap",
                }}
              >
                NIFTY
              </span>
              {niftyExpiries.slice(0, 8).map((e) => {
                const date = e.date || e;
                return (
                  <ExpiryPill
                    key={date}
                    label={formatExpiry(date) + (e.isCurrent ? " •" : "")}
                    active={selectedNiftyExp === date}
                    onClick={() => setSelectedNiftyExp(date)}
                    theme={theme}
                  />
                );
              })}
            </div>
          )}

          {showBN && bnExpiries.length > 0 && (
            <div style={{ display: "flex", gap: SPACE.XS, alignItems: "center", overflowX: "auto" }}>
              <span
                style={{
                  color: theme.PURPLE,
                  fontSize: 9,
                  fontWeight: TEXT_WEIGHT.BOLD,
                  letterSpacing: 1.5,
                  textTransform: "uppercase",
                  marginRight: SPACE.XS,
                  whiteSpace: "nowrap",
                }}
              >
                BN
              </span>
              {bnExpiries.slice(0, 8).map((e) => {
                const date = e.date || e;
                return (
                  <ExpiryPill
                    key={date}
                    label={formatExpiry(date) + (e.isCurrent ? " •" : "")}
                    active={selectedBnExp === date}
                    onClick={() => setSelectedBnExp(date)}
                    theme={theme}
                  />
                );
              })}
            </div>
          )}
        </div>

        {/* Chain display */}
        <div style={{ flex: 1, overflowY: "auto", minHeight: 200 }}>
          {loadingChain && (
            <div
              style={{
                padding: SPACE.XL,
                textAlign: "center",
                color: theme.TEXT_DIM,
                fontSize: TEXT_SIZE.BODY,
              }}
            >
              Loading chain from Kite...
            </div>
          )}

          {!loadingChain && showNifty && niftyChain.length > 0 && (
            <ChainSection
              index="NIFTY"
              expiry={formatExpiry(selectedNiftyExp)}
              strikes={niftyChain}
              query={query}
              isPinned={isPinned}
              togglePin={handleTogglePin}
              onSelect={handleSelect}
              theme={theme}
            />
          )}

          {!loadingChain && showBN && bnChain.length > 0 && (
            <ChainSection
              index="BANKNIFTY"
              expiry={formatExpiry(selectedBnExp)}
              strikes={bnChain}
              query={query}
              isPinned={isPinned}
              togglePin={handleTogglePin}
              onSelect={handleSelect}
              theme={theme}
            />
          )}

          {!loadingChain &&
            ((showNifty && !niftyChain.length) || (showBN && !bnChain.length)) && (
              <div
                style={{
                  padding: SPACE.XL,
                  textAlign: "center",
                  color: theme.TEXT_DIM,
                  fontSize: TEXT_SIZE.BODY,
                }}
              >
                {!niftyExpiries.length && !bnExpiries.length
                  ? "No expiries available. Backend engine may not be running."
                  : "Select an expiry to load its chain."}
              </div>
            )}
        </div>

        {/* Footer */}
        <div
          style={{
            display: "flex",
            gap: SPACE.LG,
            padding: `${SPACE.SM}px ${SPACE.MD}px`,
            borderTop: `1px solid ${theme.BORDER}`,
            color: theme.TEXT_DIM,
            fontSize: TEXT_SIZE.MICRO,
            fontFamily: FONT.UI,
            alignItems: "center",
          }}
        >
          <span>
            Click <strong style={{ color: theme.GREEN }}>CE LTP</strong> or{" "}
            <strong style={{ color: theme.RED }}>PE LTP</strong> to open details
          </span>
          <span>
            <kbd
              style={{
                fontFamily: FONT.MONO,
                padding: "1px 4px",
                background: theme.BG,
                borderRadius: 2,
              }}
            >
              ☆
            </kbd>{" "}
            pin
          </span>
          <span style={{ color: theme.AMBER }}>
            Pinned: {pinned?.length || 0} / 4
          </span>
          {pinned && pinned.length >= 2 && onCompare && (
            <button
              onClick={() => {
                onCompare();
                onClose();
              }}
              style={{
                marginLeft: "auto",
                background: theme.PURPLE,
                color: "#fff",
                border: "none",
                borderRadius: RADIUS.SM,
                padding: "4px 12px",
                cursor: "pointer",
                fontSize: TEXT_SIZE.MICRO,
                fontWeight: TEXT_WEIGHT.BOLD,
                letterSpacing: 1,
                textTransform: "uppercase",
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              ⚔ Battle {pinned.length} strikes
            </button>
          )}
          {(!pinned || pinned.length < 2) && <span style={{ marginLeft: "auto" }}>Universe Pro</span>}
        </div>
      </div>
    </div>
  );
}
