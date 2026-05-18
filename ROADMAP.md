# Roadmap — Option B (build into product)

**Plan agreed: 2026-05-09. ~50 hours of Tier-1 work over 4 weeks.**

Read `CLAUDE.md` for project context first.

---

## ✅ Phase 1 — DONE (week 1, 2026-05-09)

### 1.1 Daily backup system ✅
- `backend/backup_manager.py` — daemon at 3 AM IST
- Tar.gz `/data/*.db` → S3 with date-stamped key
- Endpoints: `GET /api/backup/status`, `POST /api/backup/run-now`
- **User action pending:** add 4 env vars to Render
  ```
  BACKUP_S3_BUCKET     = universe-dashboard-backups
  BACKUP_S3_REGION     = ap-south-1
  AWS_ACCESS_KEY_ID    = <from IAM user with PutObject on bucket>
  AWS_SECRET_ACCESS_KEY = <secret>
  ```

### 1.2 Critical-path automated tests ✅
- 78 tests, 100% pass, 0.24s execution
- Coverage: atr_targets, buyer_mode, entry_filters, api_cache, db_migrations
- Run: `cd backend && python -m pytest tests/ -v`

### 1.3 GitHub Actions CI ✅
- `.github/workflows/ci.yml`
- Runs on every push + PR
- Backend: pytest + py_compile + coverage
- Frontend: Vite build + bundle size check + lint
- Required-checks gate

---

## 🟡 Phase 2 — engine.py modularization (week 2-3, ~16 hrs)

**Why:** `backend/engine.py` is 5,500+ LOC monolith. Hard to test, refactor, debug.

**Strategy adjustment (2026-05-09):** Originally planned `backend/engine/`
package, but that name collides with the existing `engine.py` file during
gradual migration. Going with `backend/engine_modules/` instead. Once
migration complete, can rename to `engine/`.

**Approach: ADDITIVE EXTRACTION FIRST.** New modules live alongside engine.py.
engine.py stays UNCHANGED through extraction commits — production keeps
running the original inline methods. The wiring change (2.8) is done in
its own small, easy-to-revert commit. This sequencing keeps each task
zero-risk on its own.

### Goal: Split into focused modules under `backend/engine_modules/`

```
backend/
├── engine.py                            # Slim orchestrator (after 2.9)
└── engine_modules/
    ├── __init__.py                      # Re-exports
    ├── price_action.py        ✅ DONE   # Spot history tracking helpers
    ├── watchdog.py            ✅ DONE   # WSWatchdog class
    ├── cache_populator.py     ✅ DONE   # CachePopulator class
    ├── pulse_scheduler.py     ✅ DONE   # PulseScheduler class
    ├── ticker.py              🟡 DEFER  # WS subscription + handlers (2.5)
    └── trade_flow.py          🟡 DEFER  # Verdict + reversal entry paths (2.6)
```

### Specific tasks (in order):

**Task 2.1 — Setup module skeleton** ✅ DONE (commit 76bf0e3)
- Created `backend/engine_modules/__init__.py` with re-exports

**Task 2.2 — Extract WS watchdog** ✅ DONE (commit 76bf0e3)
- `WSWatchdog` class in `engine_modules/watchdog.py` (~130 LOC)
- 4 tests in `test_engine_modules.py::TestWSWatchdog`

**Task 2.3 — Extract cache populator** ✅ DONE (commit 76bf0e3)
- `CachePopulator` class in `engine_modules/cache_populator.py` (~175 LOC)
- 4 tests in `TestCachePopulator`

**Task 2.4 — Extract pulse scheduler** ✅ DONE (commit c3ed0bb)
- `PulseScheduler` class in `engine_modules/pulse_scheduler.py` (~95 LOC)
- 7 tests in `TestPulseScheduler`
- Mirrors WSWatchdog/CachePopulator pattern (threading.Event stop, idempotent start)

**Task 2.7 — Extract price action** ✅ DONE (commit 76bf0e3)
- Helpers in `engine_modules/price_action.py` (~75 LOC)
- 7 tests in `TestPriceAction`
- Done out of order (low-risk pure functions, batched with 2.1–2.3)

**Task 2.5 — Extract ticker subscription** 🟡 DEFERRED (HIGH RISK)
- Would move `_connect_ticker` + `on_ticks` + `on_message` → `engine_modules/ticker.py`
- Trickiest extraction — many handlers and state.
- Defer until 2.8 stable in prod for ≥1 week.

**Task 2.6 — Extract trade flow** 🟡 DEFERRED (HIGH RISK — touches money)
- Would move verdict-momentum entry path → `engine_modules/trade_flow.py`
- Plus reversal-zone entry path
- Defer until 2.8 stable in prod for ≥1 week.

**Task 2.8 — Wire engine.py to use new modules** 🟡 NEXT
- Replace inline `_start_ws_watchdog` body with `WSWatchdog(self).start()`
- Replace inline `_start_cache_populator` body with `CachePopulator(self).start()`
- Replace inline `_start_pulse_scheduler` body with `PulseScheduler(self).start()`
- Replace inline `_record_price_action` calls with `record_spot_tick(...)`
- Hold reference on the engine for `.stop()` (so `engine.stop()` can clean up).
- Smallest possible diff — one method body at a time, ideally one commit each.
- DEPLOY DURING WEEKEND so Sentry can be monitored ≥30 min post-deploy.

**Task 2.9 — Slim engine.py final** 🟡 PENDING
- After 2.8 stable, delete the now-unused inline methods.
- Target: engine.py < 500 LOC.
- Confirm: all 101+ tests still pass, production untouched.

### Success criteria:
- All 101+ tests still pass (was 78 → 94 → 101)
- engine.py < 500 LOC
- Each engine_modules/ submodule < 700 LOC
- No regression in production (verify in Sentry post-deploy)

### Risk mitigation:
- Make small commits (one task per commit, one method per sub-commit in 2.8)
- After each task, run tests + monitor Sentry for ≥30 min
- If anything breaks, revert that one commit

---

## 🟡 Phase 3 — Postgres migration (week 4, ~12 hrs)

**Why:** 12 SQLite files have write contention. Postgres allows concurrent writes,
better backups, real foreign keys.

### Goal: Single Render Postgres ($7/mo) with proper schemas

### Specific tasks:

**Task 3.1 — Provision Postgres** (1 hr)
- Render → Create Postgres (Mumbai region if available)
- Save DATABASE_URL env var
- Add to Render web service env vars

**Task 3.2 — Schema migration scripts** (3 hrs)
- For each SQLite DB, create equivalent Postgres schema
- Tables to migrate:
  - trades.db → trades, trade_alerts
  - scalper_trades.db → scalper_trades, scalper_ticks
  - capital.db, autopsy.db, etc.
- Use existing `db_migrations.py` infrastructure (extend for Postgres dialect)

**Task 3.3 — Data migration script** (3 hrs)
- One-shot script: read SQLite, write Postgres
- Run during off-hours (weekend morning before market open)
- Verify row counts match

**Task 3.4 — Switch app to Postgres** (3 hrs)
- Replace `sqlite3.connect()` calls with `psycopg2.connect(DATABASE_URL)`
- OR use SQLAlchemy ORM (cleaner abstraction)
- Update tests
- Deploy + verify

**Task 3.5 — Keep SQLite as backup for 1 week** (1 hr)
- Don't delete SQLite files immediately
- If Postgres has issues, fallback path
- After 1 week stable, delete

**Task 3.6 — Update backup_manager** (1 hr)
- Switch from tar.gz of /data/*.db to `pg_dump` to S3
- Same nightly schedule

### Success criteria:
- All trade history accessible via Postgres
- No data loss during migration
- All API endpoints continue working
- Tests still pass

---

## 🎯 What "done" looks like (end of Phase 3)

```
✅ 78+ tests passing in CI
✅ engine.py < 500 LOC, modular submodules
✅ Postgres-backed (no SQLite in /data)
✅ Daily S3 backup running
✅ Sentry catches all errors
✅ Bundle size < 600 KB main
✅ Backend response < 50ms p99 (after cache warm)
✅ Tab open < 500ms (with /api/dashboard/snapshot)
✅ Mobile responsive
✅ Auto-login at 6:05 AM IST
✅ Self-healing WS watchdog
```

**Grade target:** A- → A+

---

## How to resume in a fresh Claude session

1. User opens new chat
2. User says: "Read CLAUDE.md and ROADMAP.md, let's continue Phase 2"
3. Claude reads both files
4. Claude knows:
   - Project context (CLAUDE.md)
   - Where we left off (this file)
   - Specific next task (Phase 2.1 if Phase 1 done)
5. Claude proceeds with Task 2.1

Or even simpler:

User says: **"continue Phase 2"**

Claude reads CLAUDE.md → sees "next is Phase 2" → reads ROADMAP.md → starts Task 2.1.

---

## Hours tracking

| Phase | Planned | Actual |
|---|---|---|
| 1.1 Backup | 4 hrs | 1 hr |
| 1.2 Tests | 16 hrs | 2 hrs |
| 1.3 CI | 6 hrs | 1 hr |
| **Phase 1 total** | 26 hrs | **4 hrs** |
| 2.1 Skeleton | 1 hr | 0.5 hr ✅ |
| 2.2 Watchdog | 2 hrs | 1 hr ✅ |
| 2.3 Cache populator | 2 hrs | 1 hr ✅ |
| 2.4 Pulse scheduler | 2 hrs | 0.5 hr ✅ |
| 2.7 Price action | 1 hr | 0.5 hr ✅ |
| 2.5 Ticker | 3 hrs | DEFERRED |
| 2.6 Trade flow | 3 hrs | DEFERRED |
| 2.8 Wire engine.py | 1 hr | NEXT |
| 2.9 Slim engine.py | 1 hr | TBD |
| **Phase 2 partial** | 16 hrs | **3.5 hrs done, ~2 hrs left (excl. 2.5/2.6)** |
| 3.x Postgres | 12 hrs | TBD |
| **Total Option B** | 54 hrs | **7.5 hrs done, ~24 hrs left** |

---

## Notes for future Claude

- User trades NIFTY/BANKNIFTY options daily during 9:15-15:30 IST.
- Don't break things during market hours. Deploy big changes on weekends.
- User communicates in Hinglish — match their style.
- User cares about: reliability, speed, no surprises. NOT bells and whistles.
- When in doubt about a refactor, write a test first, then refactor.
- After deploy, monitor Sentry for 30 min. If new errors → revert.
