"""
Token Pusher — multi-app Kite token distributor (ISOLATED, additive).

Purpose:
  universe-dashboard already runs its OWN auto-login (auto_login.py) and
  boots its own engine. This module is SEPARATE: it logs into Kite once
  using the SAME account credentials (already on Render) and mints +
  pushes access_tokens for OTHER projects (KHABAR, Stock Audition) to
  their set-token endpoints — so those projects go live daily without
  the user's laptop being on.

Contract:
  - Own daemon thread, wrapped in try/except at every layer.
  - NEVER touches universe-dashboard's engine / trade logic / DBs.
  - Failure here can NOT affect the trading system — fully fail-safe.
  - Reads credentials from the SAME env vars already set on Render.

Env vars:
  KITE_USER_ID, KITE_PASSWORD, KITE_TOTP_SECRET   (already on Render)

  EXTRA_KITE_APPS — semicolon-separated app entries, each:
      api_key:api_secret:push_url
    Example (one line):
      EXTRA_KITE_APPS=khabkey:khabsec:https://khabar.onrender.com/api/auth/set-token;stockkey:stocksec:https://stock-audition.onrender.com/api/auth/set-token

  TOKEN_PUSHER_ENABLED   — "on" (default) | "off"
  TOKEN_PUSHER_HOUR      — daily login hour IST (default 6)

Diagnostics: GET /api/admin/token-pusher  (added in main.py)
"""

from __future__ import annotations

import os
import re
import json
import time
import threading
from datetime import datetime
from urllib.parse import urlparse, parse_qs

import pytz

_IST = pytz.timezone("Asia/Kolkata")

_state = {
    "started": False,
    "last_run_iso": "",
    "last_results": {},   # {api_key_prefix: "ok 200" | "fail: ..."}
    "runs": 0,
}
_lock = threading.Lock()


def _enabled() -> bool:
    return os.environ.get("TOKEN_PUSHER_ENABLED", "on").lower() == "on"


def _parse_apps():
    """Return [(api_key, api_secret, push_url), ...] from EXTRA_KITE_APPS."""
    raw = os.environ.get("EXTRA_KITE_APPS", "").strip()
    apps = []
    if not raw:
        return apps
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        # split into exactly 3 parts: key, secret, url (url may contain ':')
        parts = entry.split(":", 2)
        if len(parts) == 3:
            key, secret, url = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if key and secret and url:
                apps.append((key, secret, url))
    return apps


def _account_login():
    """Steps 1-2: credentials + TOTP. Returns requests.Session or raises."""
    import requests
    import pyotp

    user = os.environ.get("KITE_USER_ID", "")
    pw = os.environ.get("KITE_PASSWORD", "")
    totp_secret = os.environ.get("KITE_TOTP_SECRET", "")
    if not all([user, pw, totp_secret]):
        raise ValueError("KITE_USER_ID / KITE_PASSWORD / KITE_TOTP_SECRET missing")

    s = requests.Session()
    r = s.post("https://kite.zerodha.com/api/login",
               data={"user_id": user, "password": pw}, timeout=30)
    if r.status_code != 200 or r.json().get("status") != "success":
        raise Exception(f"login failed: {r.status_code} {r.text[:150]}")
    request_id = r.json()["data"]["request_id"]

    r = s.post("https://kite.zerodha.com/api/twofa", data={
        "user_id": user, "request_id": request_id,
        "twofa_value": pyotp.TOTP(totp_secret).now(), "twofa_type": "totp",
    }, timeout=30)
    if r.status_code != 200 or r.json().get("status") != "success":
        raise Exception(f"2FA failed: {r.status_code} {r.text[:150]}")
    return s


def _request_token(session, api_key: str) -> str:
    """Step 3: request_token for this app via redirect chain."""
    url = f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
    redirect = ""
    resp = session.get(url, allow_redirects=False, timeout=30)
    for _ in range(10):
        if resp.status_code in (301, 302, 303, 307):
            redirect = resp.headers.get("Location", "")
            if "request_token=" in redirect:
                break
            resp = session.get(redirect, allow_redirects=False, timeout=30)
        else:
            break
    if "request_token=" not in redirect:
        full = session.get(url, allow_redirects=True, timeout=30)
        if "request_token=" in full.url:
            redirect = full.url
        else:
            m = re.search(r"request_token=([a-zA-Z0-9]+)", full.text)
            if m:
                redirect = f"?request_token={m.group(1)}"
            else:
                fm = re.search(r'action="([^"]*)"', full.text)
                if fm:
                    form_url = fm.group(1)
                    if not form_url.startswith("http"):
                        form_url = f"https://kite.zerodha.com{form_url}"
                    ar = session.post(form_url, allow_redirects=False, timeout=30)
                    if ar.status_code in (301, 302, 303, 307):
                        redirect = ar.headers.get("Location", "")
    if "request_token=" not in redirect:
        raise Exception(f"request_token not found for {api_key[:8]}")
    rt = parse_qs(urlparse(redirect).query).get("request_token", [None])[0]
    if not rt:
        raise Exception(f"request_token parse failed {api_key[:8]}")
    return rt


def _make_and_push(session, api_key, api_secret, push_url) -> str:
    """Step 4: access_token + POST to project's set-token endpoint."""
    import requests
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=api_key)
    rt = _request_token(session, api_key)
    data = kite.generate_session(rt, api_secret=api_secret)
    token = data["access_token"]

    r = requests.post(push_url, json={
        "api_key": api_key, "access_token": token,
    }, timeout=30)
    return f"ok {r.status_code}" if r.ok else f"push {r.status_code}: {r.text[:80]}"


def _run_once():
    apps = _parse_apps()
    if not apps:
        print("[TOKEN-PUSHER] no EXTRA_KITE_APPS configured — nothing to do")
        return
    print(f"[TOKEN-PUSHER] {datetime.now(_IST):%Y-%m-%d %H:%M IST} — {len(apps)} app(s)")
    results = {}
    try:
        session = _account_login()
    except Exception as e:
        print(f"[TOKEN-PUSHER] account login FAILED: {e}")
        with _lock:
            _state["last_results"] = {a[0][:8]: f"login fail: {e}" for a in apps}
            _state["last_run_iso"] = datetime.now(_IST).isoformat()
            _state["runs"] += 1
        return
    for api_key, api_secret, push_url in apps:
        try:
            status = _make_and_push(session, api_key, api_secret, push_url)
            print(f"[TOKEN-PUSHER] {api_key[:8]}... -> {push_url[:45]} : {status}")
            results[api_key[:8]] = status
        except Exception as e:
            print(f"[TOKEN-PUSHER] {api_key[:8]}... FAILED: {e}")
            results[api_key[:8]] = f"fail: {e}"
        time.sleep(1)
    with _lock:
        _state["last_results"] = results
        _state["last_run_iso"] = datetime.now(_IST).isoformat()
        _state["runs"] += 1


def _loop():
    login_hour = int(os.environ.get("TOKEN_PUSHER_HOUR", "6"))
    print(f"[TOKEN-PUSHER] daemon started — daily {login_hour:02d}:05 IST")
    time.sleep(90)  # let main app boot
    try:
        _run_once()  # once at startup so tokens are fresh immediately
    except Exception as e:
        print(f"[TOKEN-PUSHER] startup run error: {e}")
    last_date = None
    while True:
        try:
            now = datetime.now(_IST)
            if now.weekday() < 5 and now.hour == login_hour and now.minute < 12:
                today = now.strftime("%Y-%m-%d")
                if last_date != today:
                    _run_once()
                    last_date = today
        except Exception as e:
            print(f"[TOKEN-PUSHER] loop error: {e}")
        time.sleep(60)


def start():
    """Spawn the pusher daemon. Safe to call once at startup."""
    if not _enabled():
        print("[TOKEN-PUSHER] disabled via TOKEN_PUSHER_ENABLED=off")
        return
    if not _parse_apps():
        print("[TOKEN-PUSHER] no EXTRA_KITE_APPS — pusher idle")
        return
    with _lock:
        if _state["started"]:
            return
        _state["started"] = True
    threading.Thread(target=_loop, daemon=True, name="token-pusher").start()


def diagnostics() -> dict:
    with _lock:
        apps = _parse_apps()
        return {
            "enabled": _enabled(),
            "started": _state["started"],
            "app_count": len(apps),
            "app_keys": [a[0][:8] + "..." for a in apps],
            "push_urls": [a[2] for a in apps],
            "last_run_iso": _state["last_run_iso"],
            "last_results": _state["last_results"],
            "runs": _state["runs"],
            "login_hour": int(os.environ.get("TOKEN_PUSHER_HOUR", "6")),
        }
