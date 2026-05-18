"""
telegram_alerts — push notifications to user when system events happen.

Used by:
  • auto-login daemon (success / failure / retry)
  • engine self-heal (recovery / down alerts)
  • critical errors that need human attention

Setup (env vars on Render):
  TELEGRAM_BOT_TOKEN  — from BotFather (@BotFather → /newbot)
  TELEGRAM_CHAT_ID    — your chat id (from @userinfobot)

If either env var missing, all alert calls become silent no-ops.
System keeps running, just no notifications.

Design principles:
  • Never block — every send runs on a daemon thread.
  • Never crash — caller is shielded from Telegram API errors.
  • Throttled — won't spam more than 1 alert per (key, minute).
"""

import os
import time
import threading
from typing import Optional, Dict
from collections import defaultdict


# ── Config ────────────────────────────────────────────────────────────

_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Throttle: don't send same-key alert more than once per N seconds
_THROTTLE_SEC = 60

# Per-key last-send timestamp for throttling
_last_sent: Dict[str, float] = defaultdict(float)
_throttle_lock = threading.Lock()


def is_enabled() -> bool:
    """True if both env vars are set."""
    return bool(_BOT_TOKEN and _CHAT_ID)


def _send_sync(text: str, parse_mode: str = "Markdown") -> bool:
    """Synchronous send. Returns True on success."""
    if not is_enabled():
        return False

    try:
        # Lazy import — keep telegram_alerts importable even if requests
        # isn't installed (it is, but defense in depth).
        import requests

        url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            json={
                "chat_id": _CHAT_ID,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        # Don't raise — just log & return False
        print(f"[TELEGRAM] send failed: HTTP {resp.status_code} {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"[TELEGRAM] send error: {e}")
        return False


def send(text: str, key: str = "default", parse_mode: str = "Markdown") -> None:
    """Send a message asynchronously. Non-blocking, non-throwing.

    Args:
        text: Message body (Markdown supported by default).
        key:  Throttle bucket. Same key won't fire more than
              once per _THROTTLE_SEC. Use stable keys like
              "engine_down", "autologin_success", etc.
    """
    if not is_enabled():
        return

    # Throttle check
    now = time.time()
    with _throttle_lock:
        last = _last_sent.get(key, 0.0)
        if now - last < _THROTTLE_SEC:
            return  # silently suppress to avoid spam
        _last_sent[key] = now

    # Fire-and-forget
    threading.Thread(
        target=_send_sync,
        args=(text, parse_mode),
        daemon=True,
        name=f"telegram-{key}",
    ).start()


# ── Convenience helpers for common events ────────────────────────────

def alert_engine_started(source: str, token_preview: str = "") -> None:
    """Called when engine starts up (auto-login success)."""
    msg = (
        f"✅ *Engine Started*\n"
        f"Source: `{source}`\n"
    )
    if token_preview:
        msg += f"Token: `{token_preview}...`\n"
    msg += f"_{time.strftime('%H:%M:%S IST')}_"
    send(msg, key="engine_started")


def alert_engine_down(reason: str) -> None:
    """Called when engine is detected down during market hours."""
    msg = (
        f"🚨 *Engine Down*\n"
        f"Reason: {reason}\n"
        f"_{time.strftime('%H:%M:%S IST')}_\n\n"
        f"Manual login may be needed."
    )
    send(msg, key="engine_down")


def alert_autologin_failed(error: str, attempt: int = 1) -> None:
    """Called when auto-login daemon attempt fails."""
    msg = (
        f"⚠️ *Auto-Login Failed* (attempt {attempt})\n"
        f"Error: `{error[:200]}`\n"
        f"_{time.strftime('%H:%M:%S IST')}_"
    )
    send(msg, key="autologin_failed")


def alert_autologin_critical() -> None:
    """Final-stage alert — daemon gave up, manual login needed."""
    msg = (
        f"🆘 *AUTO-LOGIN CRITICAL*\n"
        f"Daemon exhausted all retries.\n"
        f"Engine is OFF. Market opens at 09:15.\n"
        f"_{time.strftime('%H:%M:%S IST')}_\n\n"
        f"👉 *Manual login required NOW*"
    )
    send(msg, key="autologin_critical")


def alert_engine_recovered() -> None:
    """Called when engine self-heal succeeds after being down."""
    msg = (
        f"💚 *Engine Recovered*\n"
        f"Self-heal succeeded — back online.\n"
        f"_{time.strftime('%H:%M:%S IST')}_"
    )
    send(msg, key="engine_recovered")


def test_alert() -> bool:
    """Synchronous test — useful for debugging setup.
    Returns True if the message was accepted by Telegram.
    """
    return _send_sync(
        "🧪 *Test Alert*\n"
        "Universe Dashboard alerts are wired and working.\n"
        f"_{time.strftime('%H:%M:%S IST %d %b %Y')}_"
    )
