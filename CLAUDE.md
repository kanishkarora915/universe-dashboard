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

## Project North Star (2026-05-13)

**The "smart mind" rebuild**: After the 2026-05-12 reversal_zone disaster
(10 CE entries on a -1.83% bearish day, ₹-48k loss), we're building a
Council-based decision layer that replaces "weighted-sum voting" with
"multi-engine debate and agreement."

**See `ARCHITECTURE.md`** for the full 6-component vision (Pre-Market
Briefing, Council Aggregator, Pullback Detector, Scenario Tree, Learning
Loop, Structure Reader) and phased rollout plan.

**Current state (Phase 1 scaffold)**:
- `backend/council/` package exists with Vote schema + Council class
- 26 unit tests pass (test_council.py)
- OBSERVE-ONLY — does not influence trade decisions yet
- All 7 phase flags in `council/__init__.py` set to gradual enable

**Verdict engine (the production trading path) remains unchanged.**
54% winrate, +₹18,869 in 14 days. Council watches alongside, learns.

---

## Last session summary

Date: 2026-05-19 (most recent — supersedes all earlier session notes)

### What was wrong + what got fixed

**Scalper losses (4 sessions, -₹119,046):**
- 38% winrate, 82% PE bias (counter-trend in trending markets)
- 10/45 trades killed by theta decay
- Probability scoring uncalibrated (70-79% prob bucket = -₹103k)
- Root cause: counter-trend philosophy in trending market

**Main engine — 0 trades for 10+ days (since May 8):**
- smartBias flipping bullish → bearish on every rally day
- RANGE_PENALTY_BULL + MOVE_EXHAUSTED_BULL halved + reduced bull scores
- Result: BUY PE on rallying days → either lost or refused to fire
- FIXED 2026-05-19: trend-aware gate added (`commit 2035e71`)

**WebSocket stale (last_tick_age_sec hitting 200+s):**
- Old watchdog: 30s checks + 60s threshold + 2 strikes = 90-120s detection
- 5-min cooldown between attempts
- Only ONE recovery strategy
- FIXED 2026-05-19: 5-stage escalating watchdog (`commit eb07887`)
  - Stage 1 (15s): restart ticker
  - Stage 2 (45s): restart + force re-subscribe
  - Stage 3 (90s): reload token from cache
  - Stage 4 (150s): fresh Kite login
  - Stage 5 (210s): 🚨 Telegram CRITICAL

**Reversal Zone disaster (2026-05-12, -₹48k):**
- Disabled via REVERSAL_ZONE_ENABLED=False flag (`commit 0ef01a3`)
- Still disabled. Re-enable only after P0 fixes (trend gate, regime gate, etc).

### Current state (as of 2026-05-19)

Working systems:
- ✅ 6-layer auto-login (daemon, GitHub cron, self-heal, etc.)
- ✅ Telegram alerts (working — verified)
- ✅ Trinity cadence at 1s (was 500ms)
- ✅ Trinity DB prune (was broken, now correct — deletes from real tables)
- ✅ Page visibility polling pause (8 components)
- ✅ React.memo HeatmapRow
- ✅ Perf monitor (5-min sampling to council.db perf_samples table)
- ✅ Trend-aware smartBias gate (no false bull→bear flips on trend days)
- ✅ 5-stage WS watchdog (15s-210s escalation)
- ✅ Scalper auto-trade re-enabled (user's choice) with kill switch
- ✅ Council aggregator OBSERVE-ONLY (collecting data, not influencing trades)

Pending decisions:
- 🟡 Scalper trading logic fixes (directional gate, theta protection)
- 🟡 Engine.py modules wiring (Phase 2.8) — scaffold exists, unused
- 🟡 DB consolidation (30 → 5 logical groups)
- 🟡 Trap_data.db audit (1.98 GB, biggest disk hog)
- 🟡 Postgres migration (Phase 3)

### Critical Discoveries (Don't Repeat)

1. **Engine died at 3:55 AM (2026-05-14) — cause unknown**
   - No instrumentation existed to diagnose
   - NOW: perf_monitor samples every 5 min to council.db perf_samples
   - Future crash: query `/api/perf-history?hours=2` to see what was happening

2. **trinity.db prune was deleting wrong tables**
   - Old code tried to DELETE FROM verdict_history, stream_history etc.
   - Those tables DON'T EXIST. Real tables are trinity_ticks, trinity_signals, trinity_strike_data
   - Silently failed → DB grew unbounded to 111MB
   - FIXED 2026-05-17 (`commit b7a566a`) + verified working

3. **Council observe mode revealed engine vote direction inversion**
   - Initial implementation read magnitude only (always positive)
   - 82% bullish votes, even when reasons said BEARISH
   - FIXED 2026-05-13 (`commit f0f45a6`) — now reads bull_reasons/bear_reasons lists

4. **Auto-trade kill switch precedent**
   - 2026-05-18: paused scalper via SCALPER_AUTO_TRADE env var
   - 2026-05-18 same day: user chose to re-enable, accept risk
   - Pattern: env-var gated flags are good for emergency pauses

### Today's Open Questions

User keeps asking variations of "why does my dashboard keep failing":
- Honest answer (2026-05-19 session): It's not a failure, it's wrong tool for goal.
- Custom dashboard for solo trader = engineering luxury
- Commercial tools (Sensibull, Opstra, Streak) cheaper + more reliable
- Hybrid recommended: keep dashboard for monitoring, use commercial for analysis

User decision PENDING:
- Path B (Surgical refactor, 15-20 hr) vs Trading Logic Fixes (20-25 hr)
- 5 trading fixes (gap classifier, OI memory, OI flow, scalper directional gate,
  engine accuracy audit) — would improve P&L
- Path B (modular engines, DB consolidation) — would improve maintainability

### Resume Instructions for New Session

If user starts new chat saying any of these:
- "continue from last session"
- "where did we leave off"
- "what's the status"
- "let's resume"

→ Steps for new Claude:

1. Read this file (CLAUDE.md) FIRST
2. Read ARCHITECTURE.md for component overview
3. Read ROADMAP.md for Phase 2/3 task list
4. Check memory file: `~/.claude/projects/-Users-kanishkarora-Desktop-oi-live-bot-dhan/memory/project_universe_dashboard.md`
5. Run `git log --oneline -20` to see recent commits
6. Hit `/api/ws/health` to verify engine state
7. Hit `/api/auto-login/status` to verify daemon working
8. DO NOT make code changes until user confirms direction
9. Engineering style: terse Hinglish, no preambles, honest pushback when wrong

### CRITICAL — Things New Session Must NOT Do

- ❌ Don't add new engines (already too many)
- ❌ Don't build new abstractions without user OK
- ❌ Don't run mass `git add -A` (caused 216-file deletion 2026-05-18)
- ❌ Don't re-enable Reversal Zone without P0 fixes
- ❌ Don't refactor without explicit user request
- ❌ Don't be defensive when user is frustrated — be brutally honest
- ❌ Don't suggest building if user is losing money — suggest commercial tools

### Strict Git Workflow (after 2026-05-18 disaster)

Before every commit:
1. `git status` — verify expected files only
2. `git diff --cached --stat` — verify diff size sensible
3. NEVER `git add -A` unless user explicitly asks
4. Stage files by exact path: `git add backend/scalper_mode.py` etc.
5. Use heredoc for commit message: `git commit -F /tmp/commit_msg.txt`
6. Push immediately, verify CI green

### User Profile

- **Name:** Kanishk Arora
- **Communication:** Hinglish (Hindi + English mix), terse, direct
- **Background:** Solo trader, 10 invited users on dashboard
- **Goal:** Reliable trading bot, not a startup product
- **Patience:** Low when system breaks during market hours
- **Honesty:** Wants brutal truth, not engineering polish
- **Phone:** Telegram bot `UNIVERSE_DASHBOARD_BOT` (chat_id: 7970601517)

### Most Recent Commits (newest first, 2026-05-19)

```
eb07887  fix(ws): aggressive multi-stage auto-recovery — 'fix this forever'
2035e71  feat(smartbias): trend-aware gate — fix 10+ days of zero main trades
33057e1  feat(scalper): re-enable auto-trade by default — user choice
56c797a  fix: restore 216 files accidentally deleted in acde378
acde378  fix(scalper): PAUSE auto-trade — 4-session audit, -₹119k losses
32ed4e5  feat(perf): system metrics sampling — diagnose future crashes
b7a566a  fix(trinity): proper DB prune — fixes silent disk fill (was 111MB)
7ef76e9  perf: React.memo HeatmapRow with custom comparator
0eba8b2  perf: pause polling when browser tab is hidden (8 setInterval sites)
c1c4865  perf: Trinity cadence 500ms → 1s
```
