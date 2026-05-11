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
                  Sentry           ↳ in-process auto-login daemon
                                     (08:50 AM IST, weekdays)
```

- Frontend: React 19 + Vite 7 (vanilla JS, mobile responsive)
- Backend: FastAPI on Render Standard ($25/mo)
- Database: 12 SQLite files on /data persistent disk (5 GB)
- Cache: in-memory dict (api_cache.py) populated every 3s
- Auth: in-process daemon thread spawned from main.py lifespan. Wakes
  at 08:50-09:00 AM IST weekdays, runs full Kite login (creds + TOTP
  via pyotp), refreshes /data/access_token.json, restarts engine with
  fresh token. Requires env vars: KITE_USER_ID, KITE_PASSWORD,
  KITE_TOTP_SECRET, KITE_API_KEY, KITE_API_SECRET on Render.
- Errors: Sentry.io (org `kanishk-ck`) — frontend + backend projects active

**Note (2026-05-12):** Old AWS EC2 daemon (Universe-Bot,
i-0f2a83b79ca5b92a0 @ 3.109.54.133) terminated. All auto-login is now
in-process on Render. No external dependency.

---

## State as of 2026-05-10 (Option B Phase 2 partially done)

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

### 🟡 Phase 2 — engine.py modularization (in progress, 2026-05-09 / 2026-05-10)

Strategy: ADDITIVE extraction first. Each task creates a new module in
`backend/engine_modules/` (note: `_modules` suffix, not `engine/`, to
avoid collision with the existing `engine.py` during gradual migration).
engine.py stays UNCHANGED through 2.1–2.7 — production still runs the
old inline methods. Wiring (2.8) and slimming (2.9) come last.

**Done so far** (101 tests passing, was 78):
- ✅ 2.1 Module skeleton (`backend/engine_modules/__init__.py`)
- ✅ 2.2 `WSWatchdog` class — `engine_modules/watchdog.py`
- ✅ 2.3 `CachePopulator` class — `engine_modules/cache_populator.py`
- ✅ 2.4 `PulseScheduler` class — `engine_modules/pulse_scheduler.py`
- ✅ 2.7 price_action helpers — `engine_modules/price_action.py`

**Pending**:
- 🟡 2.5 ticker subscription extract — HIGH RISK (real-time critical), defer
- 🟡 2.6 trade flow extract — HIGH RISK (touches money), defer
- 🟡 2.8 wire engine.py to use new modules (replace inline impls with class instances)
- 🟡 2.9 slim engine.py final (remove old methods, target < 500 LOC)

See `ROADMAP.md` for full task list + success criteria.

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
- **08:50-09:00 AM IST weekdays** — in-process daemon thread in
  `backend/main.py::_autologin_daemon`. Spawned from FastAPI lifespan.
- Window deliberately picked: fresh token ~15 min before 09:15 market
  open, so engine warms up with full chain data in pre-market window.
- Retries every 60s within window on failure, sleeps 30 min after success.
- Requires 5 env vars on Render: KITE_USER_ID, KITE_PASSWORD,
  KITE_TOTP_SECRET, KITE_API_KEY, KITE_API_SECRET. If any missing,
  daemon logs DISABLED and exits cleanly (manual login still works).
- Token cache: `/data/access_token.json` on Render persistent disk.
- Old AWS EC2 daemon: TERMINATED 2026-05-12. Do NOT add back —
  redundant + caused token race when both fired same morning.

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
| "Continue Phase 2" or "Phase 2 start kar" | Read ROADMAP.md → next is Task 2.8 (wire engine.py) |
| "Continue Phase 3" or "Postgres migration" | Read ROADMAP.md → start Postgres migration |
| "Continue Option B" | Where we left off in Option B plan (currently Phase 2 mid-flight) |
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

Date: 2026-05-10
What user asked: "Continue Phase 2 from Task 2.4 only"
What got done:
  - Task 2.4 — extracted `PulseScheduler` into `engine_modules/pulse_scheduler.py`
  - 7 new tests added (101 total now passing)
  - Commit `c3ed0bb` pushed to main
  - Doc updates (this file + ROADMAP.md) — separate commit

User explicitly held off on Tasks 2.5/2.6 as HIGH RISK.
2.8 (wiring) deferred — user will decide when to do it (needs Sentry monitoring
window after deploy).

What's next when user returns:
- Decide whether to do Task 2.8 (wire engine.py to use new modules).
  This is the smallest-diff "switch the lights on" commit, but it DOES
  modify engine.py so it needs the user's attention post-deploy.
- After 2.8 stable in prod → consider 2.5/2.6 (still high risk).
- Postgres migration (Phase 3) is the bigger fish after Phase 2 done.

User's open S3 todo: Add 4 env vars to Render to activate backup daemon

If user starts new chat saying "let's continue", point them to:
1. This file (CLAUDE.md)
2. ROADMAP.md (specific Phase 2 tasks)
3. Their own memory file at `~/.claude/projects/.../MEMORY.md`
