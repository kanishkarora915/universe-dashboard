# Universe Dashboard — Next-Gen Mind Architecture

**Drafted:** 2026-05-13 (overnight after the 10-CE-bearish-day incident)
**Author:** Claude + Kanishk (co-design)
**Status:** Specification — implementation begins Phase 1 (Council Aggregator)

---

## 0. The Why

### What happened 2026-05-12

NIFTY closed -1.83%. Pure trend-down day. Reversal Zone tracker fired
**10 BUY CE entries in a single session — 10% win rate, ₹-48,624 net.**

Root cause: pattern detection on option premium with **zero spot/context
awareness**. Filters were either missing or bypassed (parallel entry path
ignored regime gate).

### What this document fixes

Move from **"isolated filters with weighted vote"** to **"council of
engines that think together, predict tomorrow from today, and read
market structure live."**

Less strict. More smart. Engines collaborate.

---

## 1. Mental Model Shift

| Current ("strict mind") | Target ("smart mind") |
|---|---|
| Filter pipeline — yes/no gates | Council of engines — debate + agree |
| Engines vote weighted sum | Engines submit conviction + reasoning |
| One filter fails → catastrophic gap | Council requires multi-agreement → no single failure point |
| Reactive: signal → trade | Predictive: today's close → tomorrow's plan → live tracking |
| Pullback = entry trigger (often false) | Pullback vs reversal classifier (volume + OI + multi-TF) |
| Static rules | Self-tuning weights via post-close learning |

---

## 2. Six Components

### 2.1 Pre-Market Briefing 🌅

**Purpose:** Synthesize today's data into tomorrow's expected story.

**Inputs:**
- Today's OHLC, FII/DII, OI snapshot, VIX trajectory
- Closing OI levels by strike (writers' positions)
- Sectoral leadership (Bank Nifty vs Nifty)
- Global cues overnight (SGX, US futures, Asian open)

**Outputs (structured JSON + AI narrative):**
```json
{
  "date": "2026-05-13",
  "today_close": 23379,
  "today_change_pct": -1.83,
  "tomorrow_bias": "BEARISH",
  "conviction": 7,
  "key_levels": {
    "resistance": [23450, 23500, 23700],
    "support": [23300, 23200]
  },
  "expected_range": [23250, 23450],
  "primary_scenario": {
    "name": "CONTINUATION_DOWN",
    "probability": 0.55,
    "trigger": "open below 23400 with first 15min lower close"
  },
  "alternate_scenarios": [...],
  "narrative": "...(AI Brain generated...).",
  "trade_strategy": {
    "preferred_side": "PE",
    "entry_zones": [{"strike": 23400, "buy_below": 23440}],
    "avoid": ["CE entries unless 23500 breaks with volume"]
  }
}
```

**When it runs:** 15:35 IST (post-close) + 08:30 IST (pre-open update).

**Module:** `backend/council/briefing.py`

**Uses:** Existing `forecast_engine.py` (60% reusable), `ai_brain.py` for narrative.

---

### 2.2 Council Aggregator 👥

**Purpose:** Combine 9+ engines' opinions into single weighted-but-debated verdict.

**Vote schema (per engine, per 1-min pulse):**
```python
@dataclass
class EngineVote:
    engine: str              # "seller_positioning"
    direction: str           # BULLISH / BEARISH / NEUTRAL
    conviction: float        # 0.0 to 10.0
    reasoning: str           # 1-line explanation
    timestamp: datetime
    horizon: str             # INTRADAY / EOD / OVERNIGHT
    raw_score: dict          # original engine output (for audit)
```

**Aggregation algorithm:**
```python
def council_verdict(votes: list[EngineVote]) -> CouncilVerdict:
    bull_strength = sum(v.conviction for v in votes if v.direction == "BULLISH")
    bear_strength = sum(v.conviction for v in votes if v.direction == "BEARISH")
    neutral_count = sum(1 for v in votes if v.direction == "NEUTRAL")

    total_engines = len(votes)
    dissent_pct = neutral_count / total_engines

    if dissent_pct > 0.4:
        return verdict(direction="MIXED", confidence=0, action="NO_TRADE")

    if bull_strength > 2 * bear_strength and bull_strength > THRESHOLD:
        return verdict(direction="STRONG_BULLISH", confidence=bull_strength/MAX)
    if bear_strength > 2 * bull_strength and bear_strength > THRESHOLD:
        return verdict(direction="STRONG_BEARISH", confidence=bear_strength/MAX)
    if abs(bull_strength - bear_strength) < TIE_THRESHOLD:
        return verdict(direction="MIXED", confidence=0, action="NO_TRADE")

    direction = "LEANING_BULL" if bull_strength > bear_strength else "LEANING_BEAR"
    return verdict(direction=direction, confidence=med)
```

**Key principle: 2x rule.** One side must dominate 2:1 to count as conviction.

**Module:** `backend/council/aggregator.py`

**Sources to wire (existing in engine.py around line 2900):**
- seller_positioning
- trap_fingerprints
- price_action
- oi_flow
- market_context
- vwap
- multi_timeframe
- fii_dii
- global_cues
- predictive (bonus engine)
- smart_money (bonus engine)

Already producing per-engine scores in `_eng` dict. Council just needs to translate to vote format.

---

### 2.3 Pullback vs Reversal Detector 🌊

**Purpose:** When market moves opposite to the established trend, classify:
is it a **pullback** (continuation likely) or **reversal** (turning point)?

**Scoring formula:**
```python
def reversal_score(move_against_trend) -> int:
    s = 0
    s += 2 if move.size_pct > 0.7 else 0       # significant size
    s += 2 if move.volume > 1.5 * avg_vol else 0  # high volume
    s += 1 if move.duration_min > 30 else 0     # held for time
    s += 3 if oi_writers_flipped() else 0       # OI confirms (biggest weight)
    s += 2 if vwap_crossed_and_held() else 0    # VWAP regained
    s += 2 if multi_tf_aligned() else 0         # 5m + 15m both agree
    s += 3 if council_flipped_direction() else 0  # council also flipped

    return s  # 0-15

def is_real_reversal(move) -> bool:
    return reversal_score(move) >= 8
```

**Today's 10 trades:** all would have scored 3-5 → classified as pullback → blocked.

**Module:** `backend/council/pullback_detector.py`

---

### 2.4 Live Scenario Tree 🌳

**Purpose:** Pre-market briefing generates 3-4 scenarios. Throughout the day,
live data updates probabilities. Trades only fire when one scenario is
clearly "in play."

**Scenario object:**
```python
@dataclass
class Scenario:
    id: str
    name: str                        # "CONTINUATION_DOWN"
    description: str
    probability: float               # 0-1, updates live
    trigger_conditions: list[Cond]   # all must be true to activate
    invalidation: list[Cond]         # any true → scenario dead
    active: bool                     # currently the operating scenario?
    trade_plan: TradePlan            # what to do if active
```

**Live update loop (every 1 min):**
```python
for scenario in active_scenarios:
    if any(cond.is_met() for cond in scenario.invalidation):
        scenario.probability *= 0.1   # almost dead
    elif all(cond.is_met() for cond in scenario.trigger_conditions):
        scenario.probability = min(0.9, scenario.probability + 0.1)
    else:
        scenario.probability *= 0.95  # slowly decay

# Normalize so probabilities sum to 1
total = sum(s.probability for s in scenarios)
for s in scenarios: s.probability /= total

# Active scenario = highest prob > 0.4
active = max(scenarios, key=lambda s: s.probability) if max_prob > 0.4 else None
```

**Trade decision** only fires if:
- Active scenario exists
- Active scenario's trade_plan aligns with council verdict
- Council pullback detector approves entry

**Module:** `backend/council/scenarios.py`

---

### 2.5 Post-Close Learning Loop 🎓

**Purpose:** Every market close, evaluate each engine's prediction accuracy
and adjust voting weights.

**Daily cycle:**
```python
def daily_learning():
    today_close = get_close_price()
    today_change = today_close - today_open

    # Each engine had a morning prediction in council
    morning_votes = load_morning_council_votes()
    for vote in morning_votes:
        predicted_direction = vote.direction
        actual_direction = "BULLISH" if today_change > 0 else "BEARISH"

        if predicted_direction == actual_direction:
            engine_accuracy[vote.engine].record_correct()
        else:
            engine_accuracy[vote.engine].record_wrong()

    # Update weights based on rolling 20-day accuracy
    for engine, stats in engine_accuracy.items():
        accuracy = stats.rolling_accuracy(window=20)
        new_weight = base_weight * (0.5 + accuracy)  # 0.5x to 1.5x range
        update_engine_weight(engine, new_weight)
```

**Tables (new):**
- `engine_predictions`: every vote saved with outcome
- `engine_accuracy`: rolling stats per engine
- `daily_briefings`: morning predictions vs actual close

**Module:** `backend/council/learning.py` + `backend/council.db`

---

### 2.6 Structure Reader 🏗️

**Purpose:** Identify market structure (Wyckoff-style) and constrain trades.

**Live tracking:**
- Swing highs / swing lows (significant pivots)
- Sequence: HH (higher high), HL (higher low), LL (lower low), LH (lower high)
- Trend label: UP_TREND, DOWN_TREND, RANGE, DISTRIBUTION, ACCUMULATION

**Trade gates from structure:**
| Structure | CE allowed? | PE allowed? |
|---|---|---|
| UP_TREND (HH+HL) | ✅ on HL dip | ❌ except confirmed reversal |
| DOWN_TREND (LL+LH) | ❌ except confirmed reversal | ✅ on LH bounce |
| RANGE | ✅ at range low only | ✅ at range high only |
| DISTRIBUTION | ❌ blocked entirely | ✅ aggressive |
| ACCUMULATION | ✅ aggressive | ❌ blocked entirely |

**Today (2026-05-12):** clear DOWN_TREND from 09:30 onwards.
With structure reader live → all 10 CE entries automatically blocked.

**Module:** `backend/council/structure.py`

---

## 3. Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                  PRE-MARKET (08:30 IST)                          │
│  ┌────────────────────────────────────────────────────────┐      │
│  │  Briefing Generator (Component 2.1)                    │      │
│  │  ↓ produces                                            │      │
│  │  Daily Briefing JSON + AI narrative                    │      │
│  │  ↓ feeds                                               │      │
│  │  Initial Scenario Tree (Component 2.4)                 │      │
│  │  ↓ feeds                                               │      │
│  │  Structure Reader baseline (Component 2.6)             │      │
│  └────────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────────┐
│                  INTRADAY (every 1 min)                          │
│  ┌────────────────────────────────────────────────────────┐      │
│  │  9-11 Engines (existing) → emit signals                │      │
│  │           ↓                                            │      │
│  │  Council Aggregator (2.2)                              │      │
│  │           ↓ verdict                                    │      │
│  │  Structure Reader (2.6) checks alignment               │      │
│  │           ↓                                            │      │
│  │  Pullback Detector (2.3) filters fake moves            │      │
│  │           ↓                                            │      │
│  │  Scenario Matcher (2.4) → "which scenario active?"     │      │
│  │           ↓                                            │      │
│  │  ENTRY DECISION (only if all aligned)                  │      │
│  │           ↓                                            │      │
│  │  Trade execution (existing — unchanged)                │      │
│  └────────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────────────┐
│                  POST-CLOSE (15:35 IST)                          │
│  ┌────────────────────────────────────────────────────────┐      │
│  │  Learning Loop (2.5)                                   │      │
│  │  • Score each engine's morning prediction              │      │
│  │  • Update rolling accuracy                             │      │
│  │  • Adjust weights for tomorrow                         │      │
│  │  • Generate tomorrow's briefing                        │      │
│  └────────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. File Layout (new)

```
backend/
├── council/                      ← NEW package
│   ├── __init__.py              # public exports
│   ├── vote.py                  # EngineVote, CouncilVerdict dataclasses
│   ├── aggregator.py            # Council class — main orchestrator
│   ├── engines_registry.py      # adapters wrapping existing 9-11 engines
│   ├── briefing.py              # pre-market briefing generator
│   ├── scenarios.py             # scenario tree + live updater
│   ├── pullback_detector.py     # pullback vs reversal classifier
│   ├── structure.py             # market structure tracker
│   └── learning.py              # post-close learning loop
├── council.db                    ← NEW (engine predictions, accuracy, briefings)
└── engine.py                     ← UNCHANGED in Phase 1
                                  ← TOUCHED minimally in Phase 2 (council hook)

tests/
└── test_council.py               ← NEW (aggregator + pullback + structure)
```

---

## 5. Rollout Phases

### Phase 1: Council Scaffold + Observe Mode (Week 1)
- Build `backend/council/` package
- Wire 9 engines into vote adapters
- Aggregate verdict — but DO NOT influence trades
- Store decisions in council.db
- Dashboard read-only card showing council vs actual

**Production risk:** ZERO. Council observes, verdict engine still decides.

**Success criteria:** 7 days of data showing council would have caught
80%+ of today's bad trades.

### Phase 2: Council Activated (Week 2-3)
- One flag flip: `COUNCIL_ACTIVE = True`
- Verdict engine now ALSO checks council before firing
- Smaller position size for 2 weeks (10 lots vs 30)
- Compare results vs Phase 1 observe baseline

**Production risk:** LOW. Can revert flag instantly.

### Phase 3: Pre-Market Briefing + Scenarios (Week 4-5)
- Daily AI briefing generation
- Scenario tree live updating
- Entry decisions now require active scenario match

### Phase 4: Pullback Detector + Structure Reader (Week 6-7)
- Plug both into council pipeline
- Additional layer of false-signal rejection

### Phase 5: Learning Loop (Week 8)
- Daily accuracy scoring
- Auto-tune weights

### Phase 6: Full Cutover (Week 9-10)
- Existing weighted-vote verdict logic deprecated
- Council is the sole source of truth
- 30-day live A/B comparison vs old system
- Whichever wins → keep

---

## 6. Rollback Plan

Every component has an explicit kill switch:

```python
# backend/council/__init__.py
COUNCIL_ENABLED = True          # imports + collects data
COUNCIL_ACTIVE = False          # influences trade decisions (Phase 2 flip)
BRIEFING_ENABLED = True         # generates daily briefing
SCENARIOS_ENABLED = False       # live scenario tracking (Phase 3 flip)
PULLBACK_DETECTOR_ENABLED = False  # Phase 4 flip
STRUCTURE_READER_ENABLED = False   # Phase 4 flip
LEARNING_LOOP_ENABLED = False      # Phase 5 flip
```

Any catastrophic behavior → flip the relevant flag to `False` → push → 3 min
Render redeploy → back to last known good state.

**Worst case:** Set all 7 flags to False → system behaves exactly like
2026-05-13 baseline (verdict engine + disabled reversal zone).

---

## 7. Database Schema

### `council.db` (new persistent disk file)

```sql
CREATE TABLE engine_votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP NOT NULL,
    pulse_id TEXT NOT NULL,              -- groups votes per pulse
    engine TEXT NOT NULL,
    direction TEXT NOT NULL,             -- BULLISH/BEARISH/NEUTRAL
    conviction REAL NOT NULL,            -- 0-10
    reasoning TEXT,
    horizon TEXT,                        -- INTRADAY/EOD/OVERNIGHT
    raw_score TEXT,                      -- JSON of original engine output
    INDEX idx_pulse_engine (pulse_id, engine),
    INDEX idx_timestamp (timestamp)
);

CREATE TABLE council_verdicts (
    pulse_id TEXT PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    direction TEXT NOT NULL,             -- STRONG_BULLISH/LEANING_BULL/MIXED/LEANING_BEAR/STRONG_BEARISH
    confidence REAL NOT NULL,            -- 0-1
    bull_strength REAL,
    bear_strength REAL,
    action TEXT NOT NULL,                -- ALLOW_ENTRY/NO_TRADE/EXIT_NOW
    actual_trade_fired INTEGER,          -- 0/1 — did verdict engine actually trade?
    actual_outcome_pnl REAL              -- post-trade fill-in
);

CREATE TABLE daily_briefings (
    date DATE PRIMARY KEY,
    today_close REAL,
    tomorrow_bias TEXT,
    conviction INTEGER,
    expected_range_low REAL,
    expected_range_high REAL,
    primary_scenario TEXT,
    narrative TEXT,
    actual_close_next_day REAL,           -- filled next day
    bias_accuracy TEXT,                   -- HIT/MISS/PARTIAL
    raw_payload TEXT                      -- full JSON
);

CREATE TABLE engine_accuracy (
    engine TEXT PRIMARY KEY,
    total_predictions INTEGER DEFAULT 0,
    correct_predictions INTEGER DEFAULT 0,
    current_weight REAL DEFAULT 1.0,
    rolling_20d_accuracy REAL,
    last_updated TIMESTAMP
);

CREATE TABLE scenarios_live (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL,
    scenario_id TEXT NOT NULL,
    name TEXT,
    description TEXT,
    probability_history TEXT,             -- JSON time series
    became_active_at TIMESTAMP,
    invalidated_at TIMESTAMP,
    invalidation_reason TEXT
);
```

---

## 8. API Endpoints (new)

```
GET  /api/council/current
     → current pulse vote breakdown + verdict + confidence

GET  /api/council/history?limit=100
     → past 100 council verdicts with timestamps

GET  /api/council/accuracy
     → per-engine rolling accuracy stats

GET  /api/council/briefing/{date}
     → daily briefing for given date

GET  /api/council/scenarios/live
     → currently tracked scenarios with probabilities

GET  /api/council/structure
     → current market structure (trend, key levels, recent pivots)

POST /api/council/feedback              [admin only]
     → user marks a verdict as "would have been right/wrong" for tuning
```

---

## 9. Frontend Changes (deferred to Phase 1.5)

New panel on dashboard:
- "Council Verdict" card — live updating, shows direction + confidence
- "Engine Votes" expandable — 9 engines with vote + reasoning
- "Today's Briefing" — morning prediction summary
- "Active Scenarios" — live probability bars
- "Market Structure" — current trend label + recent pivots

All Phase 1.5 work, after Phase 1 backend is solid.

---

## 10. What Does NOT Change

Critical promise: **the verdict engine (54% winrate, +₹18,869 in 14 days)
keeps running unchanged through Phase 1.** Council watches, learns, but
doesn't trade.

Other invariants:
- All SL/T1/T2 thresholds (-5% SL, +5% T1, +12% T2) — UNTOUCHED
- Risk caps, position sizing — UNTOUCHED
- Auto-login daemon — UNTOUCHED (just hardened today)
- WS watchdog, cache populator, pulse scheduler — UNTOUCHED
- Existing 9 engines' internal logic — UNTOUCHED (council reads their output)

Only NEW code, in `backend/council/`. Existing code only gets minimal hook
in Phase 2 when council goes active.

---

## 11. Implementation Order (engineering tasks)

| # | Task | Phase | Hours | Risk |
|---|---|---|---|---|
| 1 | `backend/council/` scaffold + Vote schema | P1 | 1 | Zero |
| 2 | `engines_registry.py` — adapters for 9 engines | P1 | 4 | Zero |
| 3 | `aggregator.py` — Council class + verdict logic | P1 | 4 | Zero |
| 4 | `council.db` migration + write votes/verdicts | P1 | 2 | Zero |
| 5 | API endpoints (read-only) | P1 | 2 | Zero |
| 6 | Unit tests (15+ tests) | P1 | 3 | Zero |
| 7 | Frontend card (Phase 1.5) | P1.5 | 4 | Low |
| 8 | `COUNCIL_ACTIVE` flag + entry hook | P2 | 2 | Medium |
| 9 | `briefing.py` — daily briefing generator | P3 | 8 | Low |
| 10 | `scenarios.py` — scenario tree | P3 | 16 | Medium |
| 11 | `pullback_detector.py` | P4 | 4 | Low |
| 12 | `structure.py` — market structure tracker | P4 | 10 | Medium |
| 13 | `learning.py` — post-close learning | P5 | 6 | Low |
| 14 | Phase 6 cutover + 30-day A/B | P6 | varies | Medium |

**Total: ~66 hours over 8-10 weeks.**

---

## 12. Success Metrics (when to call it done)

| Phase | Metric | Target |
|---|---|---|
| P1 | Council captures bad trades retrospectively | ≥80% of historical bad trades |
| P2 | Live trades aligned with council direction | ≥85% agreement |
| P3 | Daily briefing accuracy (bias direction) | ≥60% within 30 days |
| P4 | False-signal reduction vs P1 baseline | ≥40% fewer wrong-direction entries |
| P5 | Engine weight changes improving accuracy | Rolling 20-day accuracy ↑ |
| P6 | Full system winrate vs old verdict engine | ≥+5% absolute |

---

## 13. Open Questions / Decisions Needed

1. **Engine weight initialization** — start equal, or use historical performance?
2. **Council quorum size** — minimum N engines required to vote before verdict?
3. **Briefing AI cost** — Claude Haiku ~₹0.50 per call × 2/day = ₹30/mo. Acceptable?
4. **Scenario probability bounds** — minimum threshold to "activate" a scenario?
5. **Structure pivot detection** — fixed N-bar lookback, or adaptive?
6. **Learning loop window** — 20 trading days too short / too long?

These will be resolved during Phase 1 implementation with real data.

---

## 14. Resume Instructions for Future Claude

If you (Claude) are picking this up in a fresh session:

1. Read this file (`ARCHITECTURE.md`) first
2. Then `CLAUDE.md` + `ROADMAP.md` for production context
3. Check `backend/council/` for current implementation state
4. Check `council.db` schema for what data has been collected
5. Check phase flag values in `backend/council/__init__.py`
6. Ask user which phase they want to advance; do NOT assume

Critical: **DO NOT** delete or replace existing engine.py voting logic
until Phase 6 cutover. Council is additive through Phases 1-5.

---

## 15. Sign-off

This architecture is the result of:
- 2026-05-12 reversal_zone disaster diagnosis
- 14-day historical analysis (verdict engine works, reversal_zone broken)
- Multi-engine "team" thinking model
- Strict-to-smart transformation
- Realistic solo-dev rollout pacing

**Final principle:** *Don't replace what works. Build smarter machinery
around it. Activate gradually. Always reversible.*
