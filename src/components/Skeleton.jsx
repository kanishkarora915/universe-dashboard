/**
 * Skeleton loaders — pulsing placeholders while data loads.
 */

const pulse = `
@keyframes skeletonPulse {
  0% { opacity: 0.15; }
  50% { opacity: 0.3; }
  100% { opacity: 0.15; }
}
`;

export function SkeletonBox({ width = "100%", height = 16, radius = 6, style = {} }) {
  return (
    <>
      <style>{pulse}</style>
      <div style={{
        width, height, borderRadius: radius,
        background: "#1E1E2E",
        animation: "skeletonPulse 1.5s ease-in-out infinite",
        ...style,
      }} />
    </>
  );
}

export function SkeletonCard({ lines = 3, style = {} }) {
  return (
    <div style={{
      background: "#111118", border: "1px solid #1E1E2E",
      borderRadius: 12, padding: "16px 20px", ...style,
    }}>
      <SkeletonBox width={120} height={10} style={{ marginBottom: 12 }} />
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        {[1, 2, 3].map(i => (
          <SkeletonBox key={i} height={50} style={{ flex: 1 }} />
        ))}
      </div>
      {Array.from({ length: lines }).map((_, i) => (
        <SkeletonBox key={i} height={12} style={{ marginBottom: 6, width: `${90 - i * 15}%` }} />
      ))}
    </div>
  );
}

export function SkeletonStat() {
  return (
    <div style={{ background: "#0A0A0F", borderRadius: 8, padding: "10px 14px", flex: 1, minWidth: 80 }}>
      <SkeletonBox width={50} height={8} style={{ marginBottom: 6 }} />
      <SkeletonBox width={70} height={16} />
    </div>
  );
}

export function EmptyState({ icon = "📊", title, message }) {
  return (
    <div style={{
      textAlign: "center", padding: "40px 20px",
      background: "#111118", border: "1px solid #1E1E2E",
      borderRadius: 12,
    }}>
      <div style={{ fontSize: 32, marginBottom: 8 }}>{icon}</div>
      <div style={{ color: "#555", fontSize: 14, fontWeight: 700, marginBottom: 4 }}>{title}</div>
      <div style={{ color: "#333", fontSize: 11, maxWidth: 300, margin: "0 auto" }}>{message}</div>
    </div>
  );
}
