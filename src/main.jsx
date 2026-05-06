import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

// ── Sentry — frontend error tracking (free tier, only active if DSN set) ──
// Set VITE_SENTRY_DSN in Vercel env vars. Catches crashes, network errors,
// performance issues. Privacy-conscious: no PII by default.
// Dynamically imported so Sentry SDK is only bundled when DSN configured.
const SENTRY_DSN = import.meta.env.VITE_SENTRY_DSN
if (SENTRY_DSN) {
  import('@sentry/react').then(Sentry => {
    Sentry.init({
      dsn: SENTRY_DSN,
      integrations: [
        Sentry.browserTracingIntegration(),
        Sentry.replayIntegration({ maskAllText: false, blockAllMedia: false }),
      ],
      tracesSampleRate: 0.1,            // 10% performance traces
      replaysSessionSampleRate: 0.05,   // 5% session replays (free tier limit)
      replaysOnErrorSampleRate: 1.0,    // 100% on errors (debugging gold)
      release: import.meta.env.VITE_GIT_COMMIT || 'dev',
      environment: import.meta.env.MODE,
      beforeSend(event) {
        // Strip query strings to remove possible PII
        if (event.request?.url) {
          event.request.url = event.request.url.split('?')[0]
        }
        return event
      },
    })
    console.log('[Sentry] Initialized')
  }).catch(err => console.warn('[Sentry] Init failed:', err))
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
