// Register service worker for PWA install + offline shell
export function registerSW() {
  if (typeof window === "undefined") return;
  if (!("serviceWorker" in navigator)) return;
  if (window.location.hostname === "localhost") return; // skip in dev

  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js")
      .then((reg) => {
        console.log("[SW] registered", reg.scope);
      })
      .catch((err) => {
        console.warn("[SW] register failed", err);
      });
  });
}

// Request browser notification permission (called once by user gesture)
export async function requestNotificationPermission() {
  if (!("Notification" in window)) return "unsupported";
  if (Notification.permission === "granted") return "granted";
  if (Notification.permission === "denied") return "denied";
  try {
    const result = await Notification.requestPermission();
    return result;
  } catch {
    return "default";
  }
}
