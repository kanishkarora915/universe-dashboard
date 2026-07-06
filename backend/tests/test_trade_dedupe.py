"""
Tests for trade_dedupe — cross-tab same-strike double-fire block.

Contract these tests enforce:
  1. Module is isolated — no trading imports.
  2. No fail-loud — every error path returns (False, "") allow.
  3. Env kill switch works.
  4. Correct side detection from "BUY CE" / "BUY PE".
  5. Recent same-strike in main OR scalper DB → block.
  6. Window boundary respected (outside window → allow).
  7. Different strike / different side / different idx → allow.
  8. Missing DB files → allow (graceful).
"""
from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

_IST = pytz.timezone("Asia/Kolkata")


def _mk_db(path: Path, table: str):
    conn = sqlite3.connect(str(path))
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_time TEXT NOT NULL,
            exit_time TEXT,
            idx TEXT NOT NULL,
            action TEXT NOT NULL,
            strike INTEGER NOT NULL,
            entry_price REAL,
            status TEXT DEFAULT 'OPEN',
            pnl_rupees REAL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def _insert(path: Path, table: str, *, idx="NIFTY", strike=24450,
            action="BUY CE", when=None, status="OPEN", pnl=0.0):
    if when is None:
        when = datetime.now(_IST).replace(tzinfo=None)
    conn = sqlite3.connect(str(path))
    conn.execute(
        f"INSERT INTO {table} (entry_time, idx, action, strike, entry_price, status, pnl_rupees) "
        f"VALUES (?, ?, ?, ?, ?, ?, ?)",
        (when.isoformat(), idx, action, strike, 62.5, status, pnl),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def temp_dbs(tmp_path, monkeypatch):
    """Point the dedupe module at tmp-path DB files."""
    main_db = tmp_path / "trades.db"
    scalper_db = tmp_path / "scalper_trades.db"
    _mk_db(main_db, "trades")
    _mk_db(scalper_db, "scalper_trades")
    monkeypatch.setenv("DEDUPE_MAIN_DB_PATH", str(main_db))
    monkeypatch.setenv("DEDUPE_SCALPER_DB_PATH", str(scalper_db))
    # Reset module cache so env vars are respected
    for k in list(sys.modules):
        if k == "trade_dedupe":
            del sys.modules[k]
    return {"main": main_db, "scalper": scalper_db}


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    for k in [
        "CROSS_ENGINE_DEDUPE",
        "DEDUPE_WINDOW_MIN",
        "DEDUPE_INCLUDE_CLOSED",
    ]:
        monkeypatch.delenv(k, raising=False)


# ── Isolation contract ────────────────────────────────────────────────


class TestIsolation:
    def test_no_trading_imports_leak(self):
        prev = set(sys.modules)
        import trade_dedupe  # noqa: F401
        new = set(sys.modules) - prev
        forbidden = {
            "trade_logger", "scalper_mode", "position_watcher",
            "structure_gate", "day_classifier", "drawdown_guard",
        }
        assert not (forbidden & new), (
            f"trade_dedupe leaked forbidden imports: {forbidden & new}"
        )

    def test_public_api(self):
        import trade_dedupe as td
        assert hasattr(td, "check_dedupe")
        assert hasattr(td, "diagnostics")


# ── Env kill switch + fail-safe ──────────────────────────────────────


class TestEnv:
    def test_default_enabled(self, temp_dbs):
        import trade_dedupe as td
        d = td.diagnostics()
        assert d["enabled"] is True
        assert d["window_min"] == 30.0
        assert d["include_closed"] is True

    def test_disable_env(self, temp_dbs, monkeypatch):
        monkeypatch.setenv("CROSS_ENGINE_DEDUPE", "off")
        # Even with a matching trade, disabled means no block.
        _insert(temp_dbs["main"], "trades", strike=24450)
        import trade_dedupe as td
        blocked, _ = td.check_dedupe("NIFTY", 24450, "BUY CE", "scalper")
        assert blocked is False

    def test_missing_dbs_allow(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEDUPE_MAIN_DB_PATH", str(tmp_path / "nope.db"))
        monkeypatch.setenv("DEDUPE_SCALPER_DB_PATH", str(tmp_path / "nada.db"))
        for k in list(sys.modules):
            if k == "trade_dedupe":
                del sys.modules[k]
        import trade_dedupe as td
        blocked, _ = td.check_dedupe("NIFTY", 24450, "BUY CE", "main")
        assert blocked is False

    def test_unknown_action_allow(self, temp_dbs):
        import trade_dedupe as td
        blocked, _ = td.check_dedupe("NIFTY", 24450, "GARBAGE STRING", "main")
        assert blocked is False


# ── Same-strike block behaviour ──────────────────────────────────────


class TestBlock:
    def test_scalper_blocks_when_main_just_fired(self, temp_dbs):
        _insert(temp_dbs["main"], "trades",
                strike=24450, action="BUY CE")
        import trade_dedupe as td
        blocked, reason = td.check_dedupe("NIFTY", 24450, "BUY CE", "scalper")
        assert blocked is True
        assert "main" in reason
        assert "24450" in reason

    def test_main_blocks_when_scalper_just_fired(self, temp_dbs):
        _insert(temp_dbs["scalper"], "scalper_trades",
                strike=24450, action="BUY CE")
        import trade_dedupe as td
        blocked, reason = td.check_dedupe("NIFTY", 24450, "BUY CE", "main")
        assert blocked is True
        assert "scalper" in reason

    def test_closed_losing_trade_still_blocks(self, temp_dbs):
        """Even after the previous trade has exited (loss), block the
        immediate re-entry within window."""
        _insert(temp_dbs["main"], "trades", strike=24450,
                status="STOP_HUNTED", pnl=-4275)
        import trade_dedupe as td
        blocked, _ = td.check_dedupe("NIFTY", 24450, "BUY CE", "scalper")
        assert blocked is True

    def test_outside_window_allows(self, temp_dbs, monkeypatch):
        monkeypatch.setenv("DEDUPE_WINDOW_MIN", "5")
        for k in list(sys.modules):
            if k == "trade_dedupe":
                del sys.modules[k]
        # Insert a trade 60 minutes ago — well outside 5min window
        old = datetime.now(_IST).replace(tzinfo=None) - timedelta(minutes=60)
        _insert(temp_dbs["main"], "trades", strike=24450, when=old)
        import trade_dedupe as td
        blocked, _ = td.check_dedupe("NIFTY", 24450, "BUY CE", "scalper")
        assert blocked is False


# ── Non-matches don't block ──────────────────────────────────────────


class TestAllow:
    def test_different_strike_allows(self, temp_dbs):
        _insert(temp_dbs["main"], "trades", strike=24450)
        import trade_dedupe as td
        blocked, _ = td.check_dedupe("NIFTY", 24500, "BUY CE", "scalper")
        assert blocked is False

    def test_different_side_allows(self, temp_dbs):
        _insert(temp_dbs["main"], "trades", strike=24450, action="BUY CE")
        import trade_dedupe as td
        blocked, _ = td.check_dedupe("NIFTY", 24450, "BUY PE", "scalper")
        assert blocked is False

    def test_different_index_allows(self, temp_dbs):
        _insert(temp_dbs["main"], "trades", idx="NIFTY", strike=24450)
        import trade_dedupe as td
        blocked, _ = td.check_dedupe("BANKNIFTY", 24450, "BUY CE", "scalper")
        assert blocked is False


# ── Include-closed env behaviour ─────────────────────────────────────


class TestClosedFilter:
    def test_include_closed_off_only_blocks_open(self, temp_dbs, monkeypatch):
        monkeypatch.setenv("DEDUPE_INCLUDE_CLOSED", "off")
        for k in list(sys.modules):
            if k == "trade_dedupe":
                del sys.modules[k]
        # Insert a closed trade
        _insert(temp_dbs["main"], "trades", strike=24450,
                status="STOP_HUNTED", pnl=-4275)
        import trade_dedupe as td
        blocked, _ = td.check_dedupe("NIFTY", 24450, "BUY CE", "scalper")
        assert blocked is False  # closed trades ignored when flag off

    def test_include_closed_off_still_blocks_open(self, temp_dbs, monkeypatch):
        monkeypatch.setenv("DEDUPE_INCLUDE_CLOSED", "off")
        for k in list(sys.modules):
            if k == "trade_dedupe":
                del sys.modules[k]
        _insert(temp_dbs["main"], "trades", strike=24450, status="OPEN")
        import trade_dedupe as td
        blocked, _ = td.check_dedupe("NIFTY", 24450, "BUY CE", "scalper")
        assert blocked is True


# ── Side parsing ─────────────────────────────────────────────────────


class TestSideParsing:
    def test_side_ce_variations(self):
        import trade_dedupe as td
        assert td._side_from_action("BUY CE") == "CE"
        assert td._side_from_action("BUY  CE") == "CE"
        assert td._side_from_action("CE") == "CE"
        assert td._side_from_action("buy ce") == "CE"

    def test_side_pe_variations(self):
        import trade_dedupe as td
        assert td._side_from_action("BUY PE") == "PE"
        assert td._side_from_action("PE") == "PE"
        assert td._side_from_action("SELL PE") == "PE"

    def test_side_none(self):
        import trade_dedupe as td
        assert td._side_from_action("") is None
        assert td._side_from_action("NO TRADE") is None
