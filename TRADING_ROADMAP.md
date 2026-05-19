# TRADING ROADMAP — Win More, Lose Less

**Created: 2026-05-19** (supersedes engine-modularization priority)

Source of truth for trading-quality improvements. Read alongside `CLAUDE.md`
(project context) and `ROADMAP.md` (deferred Phase 2 modularization).

---

## 📊 Audit baseline (60 days actual data)

| | Value |
|---|---:|
| Total trades | 371 (160 main + 211 scalper) |
| Combined WR | 48% |
| Total P&L | +₹428,616 |
| Avg WIN | +₹13,867 |
| Avg LOSS | -₹23,732 |
| R:R ratio | 0.58 (losses 1.7× wins) |
| Worst single day | -₹81,078 (May 14) |

**Preventable losses identified:** ₹819k out of ₹1.5M total = 54% of losses are SYSTEM FLAWS, not bad signals.

---

## ✅ ALREADY DONE (don't redo)

| # | Item | Commit | Status |
|---|---|---|:---:|
| 0.1 | Auto-login (6-layer bulletproof) | cae0d84, ed87f8d | ✅ |
| 0.2 | Telegram alerts wired | (multiple) | ✅ |
| 0.3 | WS watchdog 5-stage escalation | eb07887 | ✅ |
| 0.4 | Trend-aware smartBias gate | 2035e71 | ✅ |
| 0.5 | Scalper kill switch | 33057e1 | ✅ |
| 0.6 | Reversal Zone disabled | 0ef01a3 | ✅ |
| 0.7 | Trinity DB prune fix | b7a566a | ✅ |
| 0.8 | Council vote direction fix | f0f45a6 | ✅ |
| 0.9 | Perf monitor (5-min samples) | (multiple) | ✅ |
| 0.10 | Dead engine cleanup (vwap, predictive) | cf71fd3 | ✅ |
| 0.11 | Calibration measurement layer | ce1fc07 | ✅ READ-ONLY |
| 0.12 | Engine bias analyzer | 58963f5 | ✅ READ-ONLY |

---

## 🎯 PHASE 1 — STOP THE BLEED (Week 1-2, 4 commits)

**Goal:** Eliminate ₹819k/60d of preventable losses. Each commit independent + env-gated.

### 1.1 — Smart SL placement (BOTH tabs)

**Problem solved:**
- STOP_HUNT: 37 main trades, 0 wins, -₹351,707
- SL at obvious round levels (5-pt grid) → institutional sweep target

**What it does:**
- Replace fixed-% SL with: `last_swing_low - 0.3 × ATR(14)` (CE) or `last_swing_high + 0.3 × ATR(14)` (PE)
- Min SL distance: -10% premium (don't go too tight)
- Max SL distance: -25% premium (don't go too wide)

**Files touched:** `backend/engine.py` (SL calculation block ~3448), `backend/scalper_mode.py`

**Env flag:** `SMART_SL_ENABLED` (default: `off` for first deploy, then `on` after 5-day shadow)

**₹ impact:** +₹350k/year savings (65% stop-hunt elimination)

**Risk:** LOW — same R:R, just better placement. Reversible via env var.

**Effort:** 4-6 hrs (incl. tests)

**Acceptance:**
- 5 unit tests for SL calculation edge cases
- Shadow mode logs old_sl vs new_sl for 5 days before going live
- 0 regression in TRAIL_EXIT behavior

---

### 1.2 — Theta-decay pre-check (SCALPER primarily, MAIN optional)

**Problem solved:**
- VELOCITY_EXIT: 19 scalper trades, 0 wins, -₹212,386
- Bought option in flat market, theta ate premium

**What it does:**
- Before firing: compute `expected_move_30min = ATR(30m) / 13`
- Compute `theta_loss_30min = option_theta × 0.5`
- If `expected_move < theta_loss × 2` → SKIP entry, log "THETA_GATE" reason

**Files touched:** `backend/scalper_mode.py` (log_scalp_trade), optionally `backend/engine.py`

**Env flag:** `THETA_GATE_ENABLED` (default: `off` → `on` after shadow)

**₹ impact:** +₹212k/year savings

**Risk:** LOW — only blocks trades, never adds. Reversible.

**Effort:** 3-4 hrs

**Acceptance:**
- Tests cover: ATM theta calc, ATR computation, threshold edge cases
- Shadow log: would-have-skipped trades + actual outcomes (verify theta gate matches losing trades)

---

### 1.3 — Daily loss circuit breaker (BOTH tabs)

**Problem solved:**
- May 14: -₹81k single session (1W/7L disaster)
- No hard floor, system kept firing

**What it does:**
- Cumulative daily P&L tracker (per tab)
- When tab cumulative P&L ≤ -₹15,000: refuse new entries for rest of day
- Telegram alert when breaker fires
- Resets at 00:01 IST next trading day

**Files touched:** New `backend/circuit_breaker.py`, hooks in `engine.py` (place_trade) + `scalper_mode.py` (log_scalp_trade)

**Env flags:** `DAILY_LOSS_LIMIT_MAIN=15000`, `DAILY_LOSS_LIMIT_SCALPER=15000`

**₹ impact:** +₹240-360k/year (caps 2-3 disaster days/quarter)

**Risk:** LOW — only blocks new entries, doesn't close open trades.

**Effort:** 4-5 hrs

**Acceptance:**
- Test: simulated -₹15k breaks new entries
- Test: breaker resets next day
- Test: breaker independent per tab (scalper breach doesn't block main)
- API: `GET /api/circuit-breaker/status` shows current day P&L vs limit

---

### 1.4 — Consecutive loss pause (BOTH tabs)

**Problem solved:**
- May 14 had 7 losses in a row
- After 3 losses, system on stuck-pattern, keeps firing

**What it does:**
- Track last N trade outcomes per tab
- 3 losses in a row → 30-min cool-off (no new entries)
- Telegram alert at trigger + at resume

**Files touched:** `backend/circuit_breaker.py` (same module as 1.3)

**Env flags:** `CONSECUTIVE_LOSS_LIMIT=3`, `COOL_OFF_MINUTES=30`

**₹ impact:** +₹200-320k/year

**Risk:** LOW

**Effort:** 2-3 hrs (builds on 1.3 infra)

**Acceptance:**
- Tests for streak detection, cool-off timer, auto-resume
- Streak counter visible via `GET /api/circuit-breaker/status`

---

## 🎯 PHASE 2 — WIN QUALITY (Week 3-4, 4 commits)

### 2.1 — Direction lock with trend (BOTH tabs)

**Problem solved:**
- Scalper 4-session audit: 82% PE bias in rally market
- ~70% of PE entries against established uptrend
- Estimated -₹80k loss / 60 days from counter-trend

**What it does:**
- When `smartBias.trend = TREND_UP` AND signal is bearish → SKIP entry
- When `smartBias.trend = TREND_DOWN` AND signal is bullish → SKIP entry
- Allow counter-trend only if `calibrated_wr ≥ 65%` (high-conviction reversal)

**Files touched:** `backend/engine.py` (trade-firing block), `backend/scalper_mode.py`

**Env flag:** `DIRECTION_LOCK_ENABLED`

**₹ impact:** +₹600k/year

**Risk:** MEDIUM — reduces trade count ~30%. Some valid counter-trend trades blocked.

**Effort:** 3-4 hrs

**Acceptance:**
- Tests for trend classification thresholds
- Shadow log: would-have-skipped vs outcomes
- Backtest: confirm post-lock WR improves

---

### 2.2 — Partial profit booking (BOTH tabs)

**Problem solved:**
- T1_HIT scalper: 30 trades all wins, but FULL EXIT misses runners
- Current TRAIL_EXIT works great but doesn't combine well with T1

**What it does:**
- T1 hit → book 50% qty, trail remaining 50% with smart trail
- If trail back to entry → exit remaining 50% at entry (no loss on second half)
- If hits T2 → full exit

**Files touched:** `backend/engine.py` (exit ladder logic), `backend/scalper_mode.py`

**Env flag:** `PARTIAL_BOOKING_ENABLED`

**₹ impact:** +₹250-400k/year (12-15% bigger T1 wins)

**Risk:** MEDIUM — changes exit behavior on winners (the breadwinners).

**Effort:** 5-6 hrs (most complex change in Phase 2)

**Acceptance:**
- 8+ tests covering: T1 hit with partial book, T2 hit, trail back to entry, trail to SL, manual close
- Backtest on last 30 trades to verify net improvement

---

### 2.3 — Time-based exit (idle exit, BOTH tabs)

**Problem solved:**
- Trades that go idle bleed theta + tie up capital
- No "if not moving, exit" rule

**What it does:**
- Scalper: idle 15 min with < 2% P&L move → close at market
- Main: idle 45 min with < 3% P&L move → close at market

**Env flag:** `IDLE_EXIT_ENABLED`

**₹ impact:** +₹80-120k/year (frees capital + cuts dead weight)

**Risk:** LOW

**Effort:** 3 hrs

---

### 2.4 — Wire calibrated probability into gating (BOTH tabs)

**Problem solved:**
- 90%+ raw prob bucket = 29% actual WR, -₹179k
- System fires on broken metric

**What it does:**
- Before firing: lookup `calibration.calibrated_wr(raw_prob, engine_type, action)`
- If `calibrated_wr < 55` AND `is_inverted = True` → SKIP entry
- Logs "CALIBRATION_GATE" reason

**PREREQUISITE:** Wait 2-3 more weeks of accumulated trade data (160 → 250+ trades). Current 160-trade base is thin for 5pp buckets.

**Files touched:** `backend/engine.py`, `backend/scalper_mode.py` (entry guards)

**Env flag:** `CALIBRATION_GATE_ENABLED`

**₹ impact:** +₹1.23M/year

**Risk:** MEDIUM-HIGH — biggest behavior change. Reduces trade count -25%.

**Effort:** 4 hrs (foundation already shipped 2026-05-19)

**Acceptance:**
- 7-day shadow log: would-have-skipped trades + outcomes
- Skip rate must match audit predictions (~10% of trades)
- Net P&L improvement in shadow window

---

## 🎯 PHASE 3 — FRONTEND VISIBILITY (Week 5, 5 commits)

### 3.1 — Calibrated WR per pending trade

**What:** Next to every "would fire" signal on dashboard, show:
```
Raw 85% → Cal 41% ⚠️ INVERTED
```
**Effort:** 3 hrs
**Risk:** LOW

### 3.2 — Bias warning chip on Council tab

**What:** When `CORRELATED_BULL_CLUSTER` is unanimous bull, show warning:
> "All 3 correlated engines bullish — treat as 1 signal, not 3"
**Effort:** 2 hrs
**Risk:** LOW

### 3.3 — Daily P&L pace bar

**What:** Top-of-dashboard bar showing:
```
Main:    ₹+8,200 / ₹15k limit  [██████░░░░] 55%
Scalper: ₹-2,100 / ₹15k limit  [██░░░░░░░░] 14%
```
**Effort:** 3 hrs
**Risk:** LOW

### 3.4 — Engine accuracy column in council tab

**What:** For each engine, show rolling 30-day accuracy + bias %
**Effort:** 4 hrs
**Risk:** LOW

### 3.5 — Skip-reason transparency

**What:** When circuit breaker/theta gate/calibration gate skips a trade, show reason in UI + Telegram
**Effort:** 2 hrs (plumbing already exists)
**Risk:** LOW

---

## 🎯 PHASE 4 — INFRASTRUCTURE HYGIENE (Week 6-8)

### 4.1 — trap_data.db audit (1.98 GB)

**Why:** Biggest disk hog. 5GB Render disk → 40% used by ONE file.

**What:**
- Inspect schema (likely unbounded fingerprint history)
- Add row count + size monitoring
- Build prune logic (keep 7 days, drop older)
- Verify with `vacuum` reclaim

**Risk:** MEDIUM (production DB, errors could corrupt)
**Effort:** 6-8 hrs
**Impact:** Disk pressure relief, faster queries

### 4.2 — DB consolidation (30 → 5 logical groups)

**Why:** 30 SQLite files = file descriptors, prune complexity, backup complexity

**Proposed groupings:**
1. `trades.db` — main + scalper trades + autopsies
2. `analytics.db` — council, trap, oi history
3. `live.db` — capital, positions, watcher
4. `ops.db` — perf, health, autologin, backups
5. `backtest.db` — backtest + shadow data

**Risk:** HIGH (data migration, schema changes)
**Effort:** 15-20 hrs (over multiple weekends)
**Impact:** Operational simplicity, future-proofing

### 4.3 — Postgres migration (Phase 3 from old roadmap)

**Why:** SQLite hits limits at higher concurrency. Postgres = proper queries, indexing, hot backups.

**Risk:** HIGH
**Effort:** 12-15 hrs
**Impact:** Production-grade DB. Required if scaling beyond 10 users.

**Defer until Phases 1-3 stable.**

---

## 🎯 PHASE 5 — LONG-TERM (3-6 months)

### 5.1 — Tick-data archive

Store raw tick stream for backtesting + signal research. Currently lost after process restart.

**Why needed:** Required prerequisite for any backtesting or micro-scalper redesign.

**Effort:** 8-10 hrs

### 5.2 — Backtest framework

Replay historical ticks through current engine/scalper logic. Measure delta vs production.

**Effort:** 15-20 hrs

### 5.3 — Micro-scalper redesign (OPTIONAL)

Only after backtest framework proves edge exists at micro-timeframe.

**Currently:** Scalper holds 15 min, looks for verdict-momentum + multi-engine alignment.
**Proposed:** Scalper 2-4 min hold, tick-level signals (premium velocity, OI flip).

**Why deferred:** Need backtest proof + tick archive first. Without those, this is gambling.

**Effort:** 40-60 hrs IF the data supports it.

---

## 📋 EXECUTION ORDER (recommended)

```
Week 1:  Phase 1.1 (Smart SL)              ← +₹350k/yr, LOW risk
Week 1:  Phase 1.2 (Theta pre-check)        ← +₹212k/yr, LOW risk
Week 2:  Phase 1.3 (Daily loss breaker)     ← +₹300k/yr, LOW risk
Week 2:  Phase 1.4 (Consec loss pause)      ← +₹250k/yr, LOW risk

  └─ MILESTONE: ~₹1.1M/yr savings, all reversible via env vars

Week 3:  Phase 2.1 (Direction lock)         ← +₹600k/yr, MEDIUM risk
Week 3:  Phase 2.3 (Idle exit)              ← +₹100k/yr, LOW risk
Week 4:  Phase 2.2 (Partial booking)        ← +₹300k/yr, MEDIUM risk

  └─ MILESTONE: ~₹2.1M/yr savings, accumulated 30 more days of data

Week 5:  Phase 2.4 (Wire calibration)       ← +₹1.23M/yr, MEDIUM-HIGH risk
                                              (with 250+ trades data)

  └─ MILESTONE: ~₹3.3M/yr improvement vs current

Week 5:  Phase 3.1-3.5 (Frontend visibility) ← clarity for live trading

Week 6-8: Phase 4 (Infrastructure)           ← hygiene + scaling prep

Months 4-6: Phase 5 (Long-term)              ← if/when needed
```

---

## 🛑 GUARDRAILS (apply to every commit)

1. **Env-flag everything** — default OFF, enable after shadow validation
2. **Telegram alert on activation** — so you know it's live
3. **Shadow mode first** — log "would have skipped X trades" before real gate
4. **Test coverage** — minimum 5 unit tests per behavioral change
5. **One commit, one fix** — never bundle 2 trading-logic changes
6. **Strict git workflow** — `git status` before every `git add`, never `-A`
7. **Backup before risk** — for any DB schema change, snapshot first
8. **Document expected vs actual** — every commit should predict ₹ impact, verify after 7 days

---

## ❌ EXPLICITLY NOT DOING (don't be tempted)

- **DON'T add new engines** (already 11, too many correlated)
- **DON'T modify engine.py vote computation** without backtest proof
- **DON'T re-enable Reversal Zone** until trend gate + circuit breaker proven
- **DON'T touch trap_fingerprints engine** (rare but 61.9% WR historical)
- **DON'T spin up AWS EC2** (in-process autologin works, AWS = duplicate race)
- **DON'T refactor for refactor's sake** (Phase 2 modularization deferred)

---

## ✅ DEFINITION OF DONE (per fix)

A fix is "shipped" only when ALL true:
- [ ] Tests passing (full suite, not just new)
- [ ] Env flag works (verified off → on → off cycle)
- [ ] Shadow mode validated 3-5 days (logs match prediction)
- [ ] Telegram alert sent when flag flipped
- [ ] CLAUDE.md updated with what changed
- [ ] One week post-deploy: ₹ impact measured vs prediction

---

## 📈 SUCCESS METRICS (review every Sunday)

Track in `council.db` perf_samples + new `metrics.db`:

| Metric | Baseline | 30-day target | 90-day target |
|---|---:|---:|---:|
| Combined WR | 48% | 53% | 58% |
| Avg WIN | ₹13,867 | ₹16,000 | ₹18,000 |
| Avg LOSS | -₹23,732 | -₹18,000 | -₹14,000 |
| R:R | 0.58 | 0.89 | 1.29 |
| Stop-hunt count | 37 / 60d | 15 / 30d | 10 / 30d |
| Velocity exits | 19 / 60d | 5 / 30d | 2 / 30d |
| Worst day | -₹81k | -₹20k | -₹15k |
| Daily P&L variance | high | medium | low |

If any metric REGRESSES → halt next phase, diagnose first.

---

## 🆘 ROLLBACK PLAN (per phase)

Every commit has a rollback path:

| Phase | Rollback |
|---|---|
| 1.1 Smart SL | `SMART_SL_ENABLED=off` env var |
| 1.2 Theta gate | `THETA_GATE_ENABLED=off` |
| 1.3 Daily breaker | `DAILY_LOSS_LIMIT_MAIN=999999` |
| 1.4 Consec pause | `CONSECUTIVE_LOSS_LIMIT=999` |
| 2.1 Direction lock | `DIRECTION_LOCK_ENABLED=off` |
| 2.2 Partial booking | `PARTIAL_BOOKING_ENABLED=off` |
| 2.3 Idle exit | `IDLE_EXIT_ENABLED=off` |
| 2.4 Calibration gate | `CALIBRATION_GATE_ENABLED=off` |

ALL fixes are env-flag controlled. Render dashboard → flip → container restart → live.
No code redeploy needed for rollback.

---

**End of TRADING_ROADMAP.md**
