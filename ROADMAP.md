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

**Why:** `backend/engine.py` is 5,514 LOC monolith. Hard to test, refactor, debug.

### Goal: Split into focused modules under `backend/engine/`

```
backend/
├── engine.py                    # Slim orchestrator (re-exports for backcompat)
└── engine/
    ├── __init__.py              # Re-exports MarketEngine, etc.
    ├── core.py                  # MarketEngine class (state + lifecycle)
    ├── ticker.py                # WebSocket subscription + tick handlers
    ├── watchdog.py              # WS watchdog (auto-reconnect logic)
    ├── cache_populator.py       # Background cache pre-compute
    ├── pulse_scheduler.py       # 1Hz pulse scheduler (drives engines)
    ├── trade_flow.py            # Verdict-based + reversal-zone entry logic
    ├── verdict_cycle.py         # 60s verdict computation cycle
    └── price_action.py          # _record_price_action + _spot_history
```

### Specific tasks (in order):

**Task 2.1 — Setup module skeleton** (1 hr)
- Create `backend/engine/__init__.py`
- Move imports from `engine.py` to `engine/__init__.py` for backcompat

**Task 2.2 — Extract WS watchdog** (2 hrs)
- Move `_start_ws_watchdog` + `_restart_ticker` → `engine/watchdog.py`
- Tests: ensure `engine.start()` still calls watchdog
- Run pytest after to verify nothing broke

**Task 2.3 — Extract cache populator** (2 hrs)
- Move `_start_cache_populator` + `_populator_loop` → `engine/cache_populator.py`
- Tests: hit `/api/cache/stats` after deploy, verify keys populating

**Task 2.4 — Extract pulse scheduler** (2 hrs)
- Move `_start_pulse_scheduler` + `_scheduler_loop` → `engine/pulse_scheduler.py`
- Tests: verify watcher pulse, capit pulse still firing

**Task 2.5 — Extract ticker subscription** (3 hrs)
- Move `_connect_ticker` + `on_ticks` + `on_message` → `engine/ticker.py`
- This is the trickiest — many handlers and state.
- Run all tests + manual deploy verification.

**Task 2.6 — Extract trade flow** (3 hrs)
- Move verdict-momentum entry path (line ~4380-4640) → `engine/trade_flow.py`
- Move reversal-zone entry path (recent commit dd8cd7d) → same
- Update entry_filters integration

**Task 2.7 — Extract price action** (1 hr)
- Move `_record_price_action` + `_spot_history` → `engine/price_action.py`

**Task 2.8 — Slim engine.py** (1 hr)
- engine.py becomes orchestrator: imports submodules, ties them together
- Should be < 500 LOC

**Task 2.9 — Add per-module tests** (1 hr)
- Each new module gets a basic smoke test

### Success criteria:
- All 78 existing tests still pass
- engine.py < 500 LOC
- Each engine/ submodule < 700 LOC
- No regression in production (verify in Sentry post-deploy)

### Risk mitigation:
- Make small commits (one task per commit)
- After each task, run tests + deploy to staging URL if possible
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
| 2.x Engine split | 16 hrs | TBD |
| 3.x Postgres | 12 hrs | TBD |
| **Total Option B** | 54 hrs | **4 hrs done, ~28 hrs left** |

---

## Notes for future Claude

- User trades NIFTY/BANKNIFTY options daily during 9:15-15:30 IST.
- Don't break things during market hours. Deploy big changes on weekends.
- User communicates in Hinglish — match their style.
- User cares about: reliability, speed, no surprises. NOT bells and whistles.
- When in doubt about a refactor, write a test first, then refactor.
- After deploy, monitor Sentry for 30 min. If new errors → revert.
