# CLAUDE.md — Project context for any Claude session

**This file is automatically read by Claude when working in this repo.**
**Update it when project state changes meaningfully.**

---

## Project: Universe Dashboard

NSE options trading dashboard for option BUYERS on NIFTY/BANKNIFTY/SENSEX.
Solo trader (Kanishk Arora). Real-time analytics + automated trade execution
with realistic risk caps.

### Live URLs
- **Frontend:** https://universe-dashboard-chi.vercel.app
- **Backend:** https://universe-dashboard.onrender.com
- **GitHub:** https://github.com/kanishkarora915/universe-dashboard

---

## Architecture (current)

```
USER (Mumbai) → Vercel (edge) → Render (Singapore) → Kite Connect WebSocket
                  ↓                ↓
                  Sentry      AWS EC2 (auto-login daemon, 6:05 AM IST)
```

- Frontend: React 19 + Vite 7 (vanilla JS, mobile responsive)
- Backend: FastAPI on Render Standard ($25/mo)
- Database: 12 SQLite files on /data persistent disk
- Cache: in-memory dict (api_cache.py) populated every 3s
- Auth: AWS EC2 daemon (`kite-autologin.service`, IP `3.109.54.133`) — refreshes
  Kite token at 6:05 AM IST and POSTs to /api/auto-login
- Errors: Sentry.io (org `kanishk-ck`) — frontend + backend projects active

---

## State as of 2026-05-09 (Option B Phase 1 complete)

User chose **Option B: build into product / show to investors** (~50 hrs Tier-1
work over 4 weeks).

### ✅ Completed (Phase 1, week 1)
1. **Backup system** (`backend/backup_manager.py`) — daily 3 AM IST S3 upload.
   User must add S3 env vars (BACKUP_S3_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, BACKUP_S3_REGION) to Render to activate.
2. **78 automated tests** in `backend/tests/` covering:
   - atr_targets (T1/T2/SL bounds)
   - buyer_mode (max-loss caps)
   - entry_filters (trend/greeks/regime)
   - api_cache (get/set/invalidate)
   - db_migrations (apply/version)
3. **GitHub Actions CI** (`.github/workflows/ci.yml`) — runs on every push:
   pytest + Vite build + bundle size check.

### 🟡 Pending (Phase 2, week 2-3)
**`engine.py` modularization** — currently 5,514 LOC monolith. Split into:
- `engine/core.py` (MarketEngine class)
- `engine/ticker.py` (WebSocket + watchdog)
- `engine/cache_populator.py`
- `engine/scheduler.py` (pulse scheduler)
- `engine/trade_flow.py` (verdict-based + reversal-zone entry paths)

See `ROADMAP.md` for specific tasks.

### 🟡 Pending (Phase 3, week 4)
**Postgres migration** — replace 12 SQLite files with single Render Postgres
($7/mo). See `ROADMAP.md`.

---

## Key files to know

| File | Purpose | LOC |
|---|---|---|
| `backend/main.py` | FastAPI app + 60+ endpoints | 4,400 |
| `backend/engine.py` | MarketEngine + 6 background threads | 5,500 |
| `backend/trade_logger.py` | Trade entry/exit + SL stack | 1,750 |
| `backend/scalper_mode.py` | Scalper mode (independent) | 1,440 |
| `backend/api_cache.py` | In-memory cache helpers | 122 |
| `backend/structured_logger.py` | JSON logging | 155 |
| `backend/db_migrations.py` | Schema versioning | 204 |
| `backend/backup_manager.py` | S3 backup daemon | 210 |
| `backend/buyer_mode.py` | BUYER/HEDGER thresholds | — |
| `backend/atr_targets.py` | T1/T2/SL calculation | — |
| `backend/entry_filters.py` | Pre-trade quality gates | 313 |
| `backend/forecast_engine.py` | Predictive narrative | ~350 |
| `backend/reversal_zone_tracker.py` | Double-bottom detector | 192 |
| `src/Universe.jsx` | Main dashboard | 3,176 |
| `src/components/BuyerCockpit.jsx` | Verdict cards | 628 |
| `src/components/ForecastCard.jsx` | Predictive cards | 259 |
| `src/hooks/useViewport.js` | Mobile breakpoints | — |
| `src/hooks/useSWRPoll.js` | API polling hook | — |

---

## Critical user-spec rules (DON'T break these)

These are user's explicit business rules — locked in by tests:

### Trade exits (BUYER mode)
- **MAX LOSS = -5%** (entry × 0.95). NO overrides. Hard cap at REVERSAL_EXIT.
- **T1 = +5%** (entry × 1.05). Realistic, locks profit.
- **T2 = +12%** (entry × 1.12). Mid 10-15% range.
- **BREAKEVEN at +3%** profit.
- **POST-T2 trail**: lock T2 + ratchet up forever.
- **Reversal-zone trades** (source='reversal_zone'):
  - SL = -5%
  - Trail activates ONLY at +25% profit
  - Trail SL = peak × 0.95

### Entry filters (Gate 11 / A12)
- Block CE entries on bearish 5-min spot trend (>0.3% down).
- Block PE entries on bullish 5-min spot trend (>0.3% up).
- Block deep OTM (delta < 0.30) — lottery tickets.
- Block deep ITM (delta > 0.70) — no leverage.
- Block CHOP regime entries (unless winProbability ≥ 75%).
- BREAKOUT regime allowed unconditionally.

### Auto-login schedule
- **6:05 AM IST daily** (NOT 8:55 AM — old config).
- AWS EC2 daemon `kite-autologin.service` on instance `i-0f2a83b79ca5b92a0`.
- Token cache: `/data/access_token.json` on Render.

---

## How to run tests

```bash
# All tests
cd backend && python -m pytest tests/ -v

# Specific file
python -m pytest tests/test_atr_targets.py -v

# With coverage
python -m pytest tests/ --cov=. --cov-report=term-missing
```

CI runs automatically on every push (`.github/workflows/ci.yml`).

---

## How to deploy

```bash
git push origin main   # Vercel + Render auto-deploy in 2-3 min
```

Both Vercel and Render watch `main` branch. CI must pass first (GitHub Actions).

---

## Recent critical fixes (2026-05-08 to 2026-05-09)

Today's commits to know about:
- `8c57e38` — 78 tests + GitHub Actions CI
- `07fccfa` — Backup system (Phase 1.1)
- `aea162f` — Universal fast cache + bundled `/api/dashboard/snapshot`
- `889d7d4` — Structured JSON logging + DB migrations system
- `18f80bd` — Background-populated cache (50-100x faster hot endpoints)
- `c3698cc` — **CRITICAL** `_bm` NameError fix (was breaking ALL trade updates silently)
- `5d359bd` — WebSocket watchdog (auto-heal frozen ticks)
- `dd8cd7d` — Reversal zone tracker (double-bottom entry, custom -5% SL + +25% trail)
- `26cbd00` — Entry quality filters (5-min trend + greeks + breakout)
- `058e3c4` — ALL SL systems unified (no max-loss override possible)
- `716cfa5` — Realistic T1/T2 + max-loss cap (-5% hard)

---

## Trigger phrases for new Claude sessions

When user says these in a fresh chat, you should know what they mean:

| Phrase | Action |
|---|---|
| "Continue Phase 2" or "Phase 2 start kar" | Read ROADMAP.md → start engine.py split |
| "Continue Phase 3" or "Postgres migration" | Read ROADMAP.md → start Postgres migration |
| "Continue Option B" | Where we left off in Option B plan (currently Phase 2 pending) |
| "How is the system doing" | Check Sentry + Render logs + run /api/cache/stats |
| "Run tests" | `cd backend && python -m pytest tests/ -v` |
| "Verify backup is working" | `curl -X POST .../api/backup/run-now` then check S3 |

---

## Things NOT to do

- ❌ Don't relax max-loss cap below -5% in BUYER mode (locked by tests)
- ❌ Don't add fantasy T1/T2 targets (>15%) (locked by tests)
- ❌ Don't break the auto-login schedule (6:05 AM IST is intentional)
- ❌ Don't disable WS watchdog (prevents silent freezes)
- ❌ Don't push without CI passing (gated)
- ❌ Don't add new features when user says "no new features"

---

## Last session summary

Date: 2026-05-09
What user asked: "Tier 1 from Option B" (build into product)
What got done: Phase 1.1 + 1.2 + 1.3 (backup + tests + CI)
What's next: Phase 2 (engine.py modularization, ~16 hrs, week 2-3)
User's open S3 todo: Add 4 env vars to Render to activate backup daemon

If user starts new chat saying "let's continue", point them to:
1. This file (CLAUDE.md)
2. ROADMAP.md (specific Phase 2 tasks)
3. Their own memory file at `~/.claude/projects/.../MEMORY.md`
