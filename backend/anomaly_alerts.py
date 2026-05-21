"""
anomaly_alerts — periodic check + Telegram dispatcher.

WHY THIS MODULE EXISTS

Phase 1 modules (regime_monitor, pattern_shift_detector, daily_diagnostic)
each measure something. This module:

  1. Runs them periodically (every 15 min during market hours)
  2. Decides what alerts to send
  3. Throttles to prevent spam
  4. Tracks alert history (prevent duplicate alerts per day)

DEPLOYMENT

Designed to be called from engine main loop or background scheduler.
Safe to call frequently — internal throttling prevents spam.

ENV FLAGS

  ANOMALY_ALERTS_ENABLED=on       master switch (default on)
  ANOMALY_ALERTS_INTERVAL_MIN=15  how often to check (default 15)
"""

from __future__ import annotations
import os
import time
from datetime import datetime
from typing import Optional

import pytz

IST = pytz.timezone("Asia/Kolkata")


# In-memory throttle: { alert_key: last_sent_timestamp }
_last_alert: dict = {}

# Per-day alert tracker: prevent same alert firing multiple times in a day
_today_alerts: set = set()
_today_date: Optional[str] = None


def is_enabled() -> bool:
    return os.environ.get("ANOMALY_ALERTS_ENABLED", "on").lower() == "on"


def _market_hours() -> bool:
    """Only run during market hours (9:15-15:30 IST weekdays)."""
    now = datetime.now(IST)
    if now.weekday() > 4:  # weekend
        return False
    h, m = now.hour, now.minute
    if h < 9 or (h == 9 and m < 15):
        return False
    if h > 15 or (h == 15 and m > 30):
        return False
    return True


def _reset_daily_if_needed():
    """Reset _today_alerts when date changes."""
    global _today_date, _today_alerts
    today = datetime.now(IST).strftime("%Y-%m-%d")
    if today != _today_date:
        _today_date = today
        _today_alerts = set()


def _can_alert(key: str, cooldown_min: int = 60, once_per_day: bool = False) -> bool:
    """Throttle check. Returns True if we should send this alert."""
    _reset_daily_if_needed()

    if once_per_day:
        if key in _today_alerts:
            return False

    last = _last_alert.get(key, 0)
    now = time.time()
    if now - last < cooldown_min * 60:
        return False
    return True


def _mark_alerted(key: str, once_per_day: bool = False):
    _last_alert[key] = time.time()
    if once_per_day:
        _today_alerts.add(key)


def _send_telegram(msg: str, key: str):
    """Send via telegram_alerts with throttling."""
    try:
        import telegram_alerts as _tg
        if _tg.is_enabled():
            _tg.send(msg, key=key)
            return True
    except Exception as e:
        print(f"[ANOMALY_ALERTS] telegram send failed: {e}")
    return False


def run_periodic_checks() -> dict:
    """Run all detection modules and send alerts if conditions met.

    Returns dict summary of what was checked and what alerts fired.
    """
    if not is_enabled():
        return {"enabled": False, "alerts_fired": []}

    if not _market_hours():
        return {"enabled": True, "in_market_hours": False, "alerts_fired": []}

    alerts_fired = []

    # ── 1. Regime monitor check ──
    try:
        from regime_monitor import assess as regime_assess
        ra = regime_assess(tab="BOTH")
        severity = ra.get("severity", "OK")

        if severity == "CRITICAL":
            key = "regime_critical"
            if _can_alert(key, cooldown_min=120, once_per_day=True):
                msg = (
                    f"🚨 CRITICAL: Regime shift detected\n"
                    f"{ra.get('summary', '')[:200]}\n"
                    f"\n"
                    f"Recommendation: {ra.get('recommendation', '')[:150]}"
                )
                if _send_telegram(msg, key=key):
                    _mark_alerted(key, once_per_day=True)
                    alerts_fired.append({"type": "regime", "severity": severity})

        elif severity == "WARNING":
            key = "regime_warning"
            if _can_alert(key, cooldown_min=180, once_per_day=False):
                msg = (
                    f"⚠️ WARNING: System metrics drifting\n"
                    f"{ra.get('summary', '')[:200]}\n"
                    f"\n"
                    f"{ra.get('recommendation', '')[:150]}"
                )
                if _send_telegram(msg, key=key):
                    _mark_alerted(key)
                    alerts_fired.append({"type": "regime", "severity": severity})

    except Exception as e:
        print(f"[ANOMALY_ALERTS] regime check error: {e}")

    # ── 2. Pattern shift check ──
    try:
        from pattern_shift_detector import detect_shifts
        ps = detect_shifts(tab="BOTH")
        level = ps.get("alert_level", "OK")

        if level == "CRITICAL":
            key = f"pattern_critical_{ps.get('consecutive_losses', 0)}"
            if _can_alert(key, cooldown_min=60):
                consec = ps.get("consecutive_losses", 0)
                consec_w = ps.get("consecutive_watcher_exits", 0)
                msg = (
                    f"🚨 CRITICAL: Pattern shift in current session\n"
                    f"{ps.get('summary', '')[:200]}\n"
                    f"Consecutive losses: {consec}\n"
                    f"Watcher exits in row: {consec_w}\n"
                    f"Today: {ps.get('today_n', 0)} trades"
                )
                if _send_telegram(msg, key=key):
                    _mark_alerted(key)
                    alerts_fired.append({"type": "pattern_shift", "level": level})

        elif level == "WARNING":
            key = "pattern_warning"
            if _can_alert(key, cooldown_min=90):
                msg = (
                    f"⚠️ PATTERN ALERT: {ps.get('summary', '')[:200]}\n"
                    f"Consecutive losses: {ps.get('consecutive_losses', 0)}"
                )
                if _send_telegram(msg, key=key):
                    _mark_alerted(key)
                    alerts_fired.append({"type": "pattern_shift", "level": level})

    except Exception as e:
        print(f"[ANOMALY_ALERTS] pattern check error: {e}")

    return {
        "enabled": True,
        "in_market_hours": True,
        "checked_at": datetime.now(IST).isoformat(),
        "alerts_fired": alerts_fired,
    }


def run_eod_diagnostic() -> dict:
    """Run end-of-day diagnostic + Telegram report.
    Should be called once at 15:35 IST."""
    try:
        from daily_diagnostic import send_eod_telegram
        return send_eod_telegram()
    except Exception as e:
        return {"error": str(e)}


def get_status() -> dict:
    """Current state of alert system."""
    _reset_daily_if_needed()
    return {
        "enabled": is_enabled(),
        "in_market_hours": _market_hours(),
        "today_alerts_fired": list(_today_alerts),
        "throttle_state": {k: int(time.time() - v) for k, v in _last_alert.items()},
    }
