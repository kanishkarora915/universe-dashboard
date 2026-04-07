/**
 * UNIVERSE API Service Layer
 * REST endpoints + WebSocket connection to FastAPI backend.
 */

export async function fetchStatus() {
  const res = await fetch("/api/status");
  return res.json();
}

export async function login(apiKey, apiSecret) {
  const res = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey, api_secret: apiSecret }),
  });
  return res.json();
}

export async function logout() {
  const res = await fetch("/api/logout", { method: "POST" });
  return res.json();
}

export async function fetchLive() {
  const res = await fetch("/api/live");
  if (!res.ok) return null;
  return res.json();
}

export async function fetchOptionChain(index) {
  const res = await fetch(`/api/option-chain/${index}`);
  if (!res.ok) return null;
  return res.json();
}

export async function fetchHistorical(token, interval = "5minute", days = 5) {
  const res = await fetch(`/api/historical/${token}/${interval}?days=${days}`);
  if (!res.ok) return null;
  return res.json();
}

export async function fetchUnusual() {
  const res = await fetch("/api/unusual");
  if (!res.ok) return [];
  return res.json();
}

export async function fetchOISummary() {
  const res = await fetch("/api/oi-summary");
  if (!res.ok) return null;
  return res.json();
}

export async function fetchSignals() {
  const res = await fetch("/api/signals");
  if (!res.ok) return [];
  return res.json();
}

export async function fetchIntraday() {
  const res = await fetch("/api/intraday");
  if (!res.ok) return null;
  return res.json();
}

export async function fetchNextDay() {
  const res = await fetch("/api/nextday");
  if (!res.ok) return null;
  return res.json();
}

export async function fetchWeekly() {
  const res = await fetch("/api/weekly");
  if (!res.ok) return null;
  return res.json();
}

export async function fetchSellerSummary() {
  const res = await fetch("/api/seller-summary");
  if (!res.ok) return null;
  return res.json();
}

export async function fetchTradeAnalysis() {
  const res = await fetch("/api/trade-analysis");
  if (!res.ok) return null;
  return res.json();
}

export async function fetchDailyExport() {
  const res = await fetch("/api/export-daily");
  if (!res.ok) return null;
  return res.json();
}

export async function fetchHiddenShift() {
  const res = await fetch("/api/hidden-shift");
  if (!res.ok) return null;
  return res.json();
}

export async function fetchExpiries(index) {
  const res = await fetch(`/api/expiries/${index}`);
  if (!res.ok) return [];
  return res.json();
}

export async function fetchExpiryChain(index, expiry) {
  const res = await fetch(`/api/expiry-chain/${index}/${expiry}`);
  if (!res.ok) return null;
  return res.json();
}

export async function fetchTrapScan() {
  const res = await fetch("/api/trap/scan");
  if (!res.ok) return null;
  return res.json();
}

export async function fetchTrapAlerts() {
  const res = await fetch("/api/trap/alerts");
  if (!res.ok) return [];
  return res.json();
}

export async function fetchAIAnalysis() {
  const res = await fetch("/api/ai-analysis");
  if (!res.ok) return null;
  return res.json();
}

export async function fetchTrapHistory() {
  const res = await fetch("/api/trap/history");
  if (!res.ok) return [];
  return res.json();
}

export async function fetchTrapToday() {
  const res = await fetch("/api/trap/today");
  if (!res.ok) return [];
  return res.json();
}

export async function fetchPriceAction(expiry = null) {
  const url = expiry ? `/api/price-action?expiry=${expiry}` : "/api/price-action";
  const res = await fetch(url);
  if (!res.ok) return null;
  return res.json();
}
