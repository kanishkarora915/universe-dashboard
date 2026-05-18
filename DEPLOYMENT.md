# Universe Dashboard — Deployment Guide

Production setup uses **separated frontend/backend** for reliability + speed.
Total monthly cost: **$25/month** (Render Standard only — Vercel/Sentry/Cloudflare all on free tiers).

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ User browser                                             │
└────────────────┬────────────────────────────────────────┘
                 │
                 ↓
        ┌────────────────────┐
        │ Cloudflare DNS/CDN │  (free)
        └────────┬───────────┘
                 │
        ┌────────┴────────┐
        ↓                 ↓
┌──────────────┐  ┌──────────────────────┐
│ Vercel       │  │ Render               │
│ (frontend)   │  │ (backend)            │
│              │  │                       │
│ React SPA    │  │ FastAPI              │
│ Edge cached  │  │ WebSocket → Kite     │
│              │  │ 12 SQLite DBs        │
│ FREE         │  │ $25/month            │
└──────────────┘  └──────────────────────┘
        │                 ↑
        │  /api/* proxy   │
        └─────────────────┘
```

**Why separated:**
- Static assets served from Vercel's global edge (faster everywhere)
- Backend bug doesn't affect frontend availability
- Independent deploys (frontend changes ship in 30s, no backend restart)
- Service worker bugs (manifest.json type) eliminated permanently
- Frontend preview branches per PR for safe testing

---

## Prerequisites

- GitHub account (you have)
- Render account (you have — universe-dashboard.onrender.com)
- Vercel account (free) — sign up at vercel.com with GitHub
- Cloudflare account (free, optional) — for custom domain
- Sentry account (free, optional) — error tracking

---

## Phase 1A: Frontend → Vercel (FIRST)

### Step 1: Sign up Vercel
1. Go to **vercel.com**
2. Click **Sign Up** → **Continue with GitHub**
3. Authorize Vercel to access your repos

### Step 2: Import the project
1. Vercel dashboard → **Add New** → **Project**
2. Find `kanishkarora915/universe-dashboard`
3. Click **Import**
4. Vercel auto-detects Vite framework

### Step 3: Configure build
The repo's `vercel.json` already has correct config. Just verify:
- **Framework Preset:** Vite (auto-detected)
- **Build Command:** `npm run build`
- **Output Directory:** `dist`
- **Install Command:** `npm install`

### Step 4: Environment variables
Add these in Vercel project → Settings → Environment Variables:

```
VITE_API_URL          (leave EMPTY — vercel.json proxies /api/* to Render)
VITE_SENTRY_DSN       (optional — fill if Sentry set up)
VITE_GIT_COMMIT       (auto-set by Vercel from git SHA)
```

### Step 5: Deploy
1. Click **Deploy**
2. Wait ~2 min for first build
3. Vercel gives you URL: `universe-dashboard-xxx.vercel.app`
4. Open URL — should see your dashboard

### Step 6: Test
- Open the Vercel URL on laptop + mobile
- Check live data flows (NIFTY/BANKNIFTY tickers update)
- Check Console (F12) — no CORS errors
- Tabs work: PnL, Scalper, Reversal
- API calls work (Network tab → /api/* → 200 OK)

### Step 7: Set production domain (optional)
If you have custom domain (e.g., `dashboard.yourname.com`):
1. Vercel project → Settings → Domains
2. Add domain → follow DNS instructions
3. Update Render `ALLOWED_ORIGINS` env var to include the new domain

---

## Phase 1B: Cloudflare (optional, do after Vercel works)

Cloudflare adds: DDoS protection + free SSL + edge caching beyond Vercel.

### Step 1: Sign up
1. **cloudflare.com** → Sign Up (free)
2. Add a site (your domain if you have one)

### Step 2: DNS records
Point your domain to Vercel:
- **CNAME** record: `www` → `cname.vercel-dns.com`
- **A** record: `@` → Vercel's IP (Vercel guides you)

### Step 3: Cloudflare settings
- **SSL/TLS** → Full (strict)
- **Caching** → Standard
- **Speed → Auto Minify** → JS, CSS, HTML
- **Speed → Brotli** → On

### Step 4: Verify
- Open `https://yourdomain.com` → should load via Cloudflare → Vercel
- Check DevTools → Response headers → should see `cf-cache-status`

---

## Phase 2: Sentry (5 min, free)

### Step 1: Sign up
1. **sentry.io** → Sign Up
2. Free tier: 5K errors/month + 10K performance events

### Step 2: Create projects
Need TWO Sentry projects (one for frontend, one for backend):

**Frontend project:**
1. Create project → choose **React**
2. Copy DSN (looks like `https://abc...@sentry.io/123`)
3. Add to Vercel env: `VITE_SENTRY_DSN=https://...`
4. Redeploy on Vercel

**Backend project:**
1. Create project → choose **Python (FastAPI)**
2. Copy DSN
3. Add to Render env: `SENTRY_DSN=https://...`
4. Render auto-restarts on env change

### Step 3: Verify
- Trigger an error somewhere (e.g., visit a 404 page)
- Sentry dashboard → should see error within 30s

---

## Phase 3: Uptime monitoring (optional, 5 min)

### Uptime Robot (free)
1. **uptimerobot.com** → Sign Up
2. Add Monitor → HTTP(S)
3. URL: `https://universe-dashboard.onrender.com/api/scalper/status`
4. Interval: 5 minutes (free tier)
5. Alert: email when down

### Add backend pulse health check
Add monitor for: `https://universe-dashboard.onrender.com/api/positions/watcher-status`
Alert if: `live: false` for >5 minutes

---

## Render-side cleanup (after Vercel works)

Once Vercel is serving frontend successfully, clean up Render:

### Remove static file serving from FastAPI
The catch-all SPA route in `backend/main.py` is no longer needed since Vercel serves the frontend. You can keep it as fallback (in case Vercel ever goes down and you want the URL to still work via Render).

### Update render.yaml
No changes needed — render.yaml only affects backend deploy.

---

## Environment Variables Reference

### Vercel (frontend)
| Variable | Purpose | Required |
|---|---|---|
| `VITE_API_URL` | Backend URL (leave empty if using vercel.json proxy) | No |
| `VITE_SENTRY_DSN` | Sentry frontend DSN | No |
| `VITE_GIT_COMMIT` | Git SHA (Vercel auto-sets) | Auto |

### Render (backend)
| Variable | Purpose | Required |
|---|---|---|
| `KITE_API_KEY` | Zerodha Kite Connect API key | Yes |
| `KITE_API_SECRET` | Kite Connect secret | Yes |
| `KITE_REQUEST_TOKEN` | Kite session token | Yes |
| `ANTHROPIC_API_KEY` | Claude AI (narrative gen) | No |
| `SENTRY_DSN` | Sentry backend DSN | No |
| `ALLOWED_ORIGINS` | Extra CORS origins (comma-separated) | No |
| `ALLOW_PNL_ON_EXPIRY` | Override expiry-day PnL block (default off) | No |

---

## Rollback procedure

### If Vercel deploy breaks:
1. Vercel dashboard → Deployments
2. Find last working deploy
3. Click **⋯** → **Promote to Production**
4. Done in 30s

### If Render deploy breaks:
1. Render dashboard → Service → Events
2. Find last working deploy
3. Click **Rollback to this deploy**
4. Done in 1-2 min

### If both broken simultaneously:
1. Find last good git commit: `git log --oneline -10`
2. `git revert HEAD` and push
3. Both Vercel + Render auto-deploy the revert

---

## Common issues + fixes

### CORS error in browser console
**Cause:** Backend doesn't allow Vercel domain.
**Fix:** Add Vercel URL to Render env var `ALLOWED_ORIGINS`:
```
ALLOWED_ORIGINS=https://universe-dashboard.vercel.app,https://yourdomain.com
```

### "API not reachable" on Vercel deploy
**Cause:** vercel.json rewrites not working.
**Fix:** Verify `vercel.json` is in repo root + has correct rewrite rules.

### Service worker still serving old assets after deploy
**Fix:** Hard refresh (Cmd+Shift+R) or unregister SW:
- F12 → Application → Service Workers → Unregister

### WebSocket connection fails
**Cause:** Browser blocks ws:// over https://.
**Fix:** Use `wss://` (secure WebSocket) for production.
Backend already serves over HTTPS so this should auto-work.

### Render-side "Engine not running"
**Cause:** Backend crashed during startup.
**Fix:** Render dashboard → Logs → check Python traceback.
Common: missing env var (KITE_API_KEY etc.)

---

## Cost summary

| Service | Plan | Cost/month |
|---|---|---|
| Render | Standard (1 CPU, 2GB RAM) | $25 |
| Vercel | Hobby (100GB bandwidth) | $0 |
| Cloudflare | Free | $0 |
| Sentry | Developer (5K errors) | $0 |
| Uptime Robot | Free (50 monitors) | $0 |
| **TOTAL** | | **$25/month** |

Same as before, but with proper distribution + monitoring.

---

## Performance expectations

| Metric | Before (monolith) | After (Vercel + Render) |
|---|---|---|
| First page load (Mumbai) | 1.5-2s | 400-700ms |
| Frontend deploy time | 3-4 min | 30s |
| Backend CPU during peak | 60-80% | 40-50% |
| API response p50 | 100-200ms | 80-150ms |
| Static asset latency | 300ms (Singapore) | 50ms (edge) |
| Service worker bugs | Frequent | Eliminated |

---

## Maintenance

### Weekly
- Check Sentry dashboard → review errors → fix top 3
- Check Uptime Robot → ensure 99%+ uptime
- Review Render metrics → CPU/memory trends

### Monthly
- Run backtest validator → check filter accuracy
- Review SQLite DB sizes → VACUUM if needed
- Update dependencies (`npm outdated`, `pip list -o`)

### Quarterly
- Database backup test (restore from backup → verify)
- Disaster recovery dry run
- Review architecture: time to migrate to Postgres?

---

## When to consider further upgrades

| Signal | Action |
|---|---|
| 10+ concurrent users | Move SQLite → Postgres |
| Sentry errors >100/day | Fix top errors before adding features |
| API latency p95 >500ms | Add Redis caching |
| WebSocket reconnects >5/hour | Investigate Kite connection stability |
| Disk usage >70% on /data | Set up nightly archive to S3/B2 |

---

## Support

- Render docs: https://render.com/docs
- Vercel docs: https://vercel.com/docs
- Sentry docs: https://docs.sentry.io
- This repo's CLAUDE.md (architecture overview)
