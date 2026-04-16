const GREEN = "#30D158";
const RED = "#FF453A";
const BORDER = "#1E1E2E";

export default function LiveTicker({ live }) {
  if (!live) return null;

  const nifty = live.nifty || {};
  const bn = live.banknifty || {};

  const items = [
    { label: "NIFTY", value: nifty.ltp, change: nifty.changePct, color: nifty.changePct >= 0 ? GREEN : RED },
    { label: "BANKNIFTY", value: bn.ltp, change: bn.changePct, color: bn.changePct >= 0 ? GREEN : RED },
    { label: "VIX", value: nifty.vix || bn.vix, color: (nifty.vix || 0) > 18 ? RED : GREEN },
    { label: "N-PCR", value: nifty.pcr, color: nifty.pcr > 1.1 ? GREEN : nifty.pcr < 0.9 ? RED : "#888" },
    { label: "BN-PCR", value: bn.pcr, color: bn.pcr > 1.1 ? GREEN : bn.pcr < 0.9 ? RED : "#888" },
  ];

  return (
    <div style={{
      background: "#08080D", borderBottom: `1px solid ${BORDER}`,
      padding: "4px 16px", display: "flex", gap: 20, alignItems: "center",
      overflowX: "auto", fontSize: 11, fontWeight: 600,
    }}>
      {items.map((item, i) => (
        <div key={i} style={{ display: "flex", gap: 6, alignItems: "center", whiteSpace: "nowrap" }}>
          <span style={{ color: "#444" }}>{item.label}</span>
          <span style={{ color: "#ccc", fontWeight: 700 }}>
            {item.value ? (typeof item.value === "number" ? item.value.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : item.value) : "—"}
          </span>
          {item.change !== undefined && item.change !== null && (
            <span style={{ color: item.color, fontSize: 10 }}>
              {item.change >= 0 ? "▲" : "▼"}{Math.abs(item.change)}%
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
