"""
Auto Login — Automated Kite Connect login with TOTP.
Runs at 8:55 AM IST daily. Generates access_token without browser.

Required environment variables:
  KITE_USER_ID      — Zerodha login ID (e.g., AB1234)
  KITE_PASSWORD     — Zerodha password
  KITE_TOTP_SECRET  — TOTP secret key from Zerodha 2FA setup
  KITE_API_KEY      — Kite Connect API key
  KITE_API_SECRET   — Kite Connect API secret

Usage:
  python auto_login.py              # Run once
  python auto_login.py --daemon     # Run as daemon (auto-login daily at 8:55 AM)
"""

import os
import sys
import time
import json
import requests
import pyotp
from datetime import datetime
from pathlib import Path
import pytz

IST = pytz.timezone("Asia/Kolkata")

# Load from environment
USER_ID = os.getenv("KITE_USER_ID", "")
PASSWORD = os.getenv("KITE_PASSWORD", "")
TOTP_SECRET = os.getenv("KITE_TOTP_SECRET", "")
API_KEY = os.getenv("KITE_API_KEY", "")
API_SECRET = os.getenv("KITE_API_SECRET", "")

# Backend URL (local or Render)
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# Token cache file
_data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).parent
TOKEN_CACHE = _data_dir / "access_token.json"


def ist_now():
    return datetime.now(IST)


def generate_totp():
    """Generate current TOTP code from secret."""
    if not TOTP_SECRET:
        raise ValueError("KITE_TOTP_SECRET not set")
    totp = pyotp.TOTP(TOTP_SECRET)
    return totp.now()


def kite_login():
    """Complete Kite login flow: credentials → TOTP → request_token → access_token."""
    if not all([USER_ID, PASSWORD, TOTP_SECRET, API_KEY, API_SECRET]):
        missing = []
        if not USER_ID: missing.append("KITE_USER_ID")
        if not PASSWORD: missing.append("KITE_PASSWORD")
        if not TOTP_SECRET: missing.append("KITE_TOTP_SECRET")
        if not API_KEY: missing.append("KITE_API_KEY")
        if not API_SECRET: missing.append("KITE_API_SECRET")
        raise ValueError(f"Missing env vars: {', '.join(missing)}")

    session = requests.Session()

    # Browser-like headers — Kite's anti-bot detection rejects naked
    # requests.Session() calls. Mimicking a real Chrome request avoids
    # silent CAPTCHA challenges and "Invalid request" rejections that
    # caused the daily 8:50 AM login failures.
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })

    # Step 1: POST credentials
    print(f"[AUTO-LOGIN] Step 1: Logging in as {USER_ID}...")
    login_resp = session.post("https://kite.zerodha.com/api/login", data={
        "user_id": USER_ID,
        "password": PASSWORD,
    })

    if login_resp.status_code != 200:
        raise Exception(f"Login failed: {login_resp.status_code} — {login_resp.text[:200]}")

    login_data = login_resp.json()
    if login_data.get("status") != "success":
        raise Exception(f"Login failed: {login_data}")

    request_id = login_data["data"]["request_id"]
    print(f"[AUTO-LOGIN] Step 1 OK — request_id: {request_id[:10]}...")

    # Step 2: POST TOTP
    totp_code = generate_totp()
    print(f"[AUTO-LOGIN] Step 2: Submitting TOTP...")
    twofa_resp = session.post("https://kite.zerodha.com/api/twofa", data={
        "user_id": USER_ID,
        "request_id": request_id,
        "twofa_value": totp_code,
        "twofa_type": "totp",
    })

    if twofa_resp.status_code != 200:
        raise Exception(f"2FA failed: {twofa_resp.status_code} — {twofa_resp.text[:200]}")

    twofa_data = twofa_resp.json()
    if twofa_data.get("status") != "success":
        raise Exception(f"2FA failed: {twofa_data}")

    print(f"[AUTO-LOGIN] Step 2 OK — 2FA complete")

    # Step 3: Hit Kite Connect login URL → get request_token
    print(f"[AUTO-LOGIN] Step 3: Getting request_token from Kite Connect...")
    import re

    redirect_url = ""

    # Approach 1: GET connect/login with allow_redirects=False to catch each redirect
    connect_url = f"https://kite.zerodha.com/connect/login?v=3&api_key={API_KEY}"
    resp = session.get(connect_url, allow_redirects=False)
    print(f"[AUTO-LOGIN] Step 3a: Status {resp.status_code}")

    # Follow redirect chain manually
    for _ in range(10):
        if resp.status_code in (301, 302, 303, 307):
            redirect_url = resp.headers.get("Location", "")
            print(f"[AUTO-LOGIN] Step 3 redirect: {redirect_url[:120]}")
            if "request_token=" in redirect_url:
                break
            resp = session.get(redirect_url, allow_redirects=False)
        else:
            break

    # Approach 2: Check response body for request_token or authorization form
    if "request_token=" not in redirect_url:
        resp_full = session.get(connect_url, allow_redirects=True)
        body = resp_full.text
        print(f"[AUTO-LOGIN] Step 3b: Final URL={resp_full.url[:120]}, body_len={len(body)}")

        # Check for request_token in final URL
        if "request_token=" in resp_full.url:
            redirect_url = resp_full.url
        else:
            # Search in HTML body
            match = re.search(r'request_token=([a-zA-Z0-9]+)', body)
            if match:
                redirect_url = f"?request_token={match.group(1)}"
            else:
                # Look for form action
                form_match = re.search(r'action="([^"]*)"', body)
                if form_match:
                    form_url = form_match.group(1)
                    print(f"[AUTO-LOGIN] Step 3c: Found form action: {form_url[:120]}")
                    # Submit the form (authorize)
                    if not form_url.startswith("http"):
                        form_url = f"https://kite.zerodha.com{form_url}"
                    auth_resp = session.post(form_url, allow_redirects=False)
                    if auth_resp.status_code in (301, 302, 303, 307):
                        redirect_url = auth_resp.headers.get("Location", "")

    if "request_token=" not in redirect_url:
        raise Exception(f"Could not extract request_token. Redirect URL: {redirect_url[:200]}")

    # Extract request_token from URL
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(redirect_url)
    params = parse_qs(parsed.query)
    request_token = params.get("request_token", [None])[0]

    if not request_token:
        raise Exception(f"request_token not found in redirect URL")

    print(f"[AUTO-LOGIN] Step 3 OK — request_token: {request_token[:10]}...")

    # Step 4: Generate access_token via Kite Connect API
    print(f"[AUTO-LOGIN] Step 4: Generating access_token...")
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=API_KEY)
    data = kite.generate_session(request_token, api_secret=API_SECRET)
    access_token = data["access_token"]

    print(f"[AUTO-LOGIN] Step 4 OK — access_token: {access_token[:10]}...")

    # Save token
    token_data = {
        "access_token": access_token,
        "api_key": API_KEY,
        "api_secret": API_SECRET,
        "user_id": USER_ID,
        "login_time": ist_now().isoformat(),
        "date": ist_now().strftime("%Y-%m-%d"),
    }
    TOKEN_CACHE.write_text(json.dumps(token_data, indent=2))
    print(f"[AUTO-LOGIN] Token saved to {TOKEN_CACHE}")

    return access_token


def trigger_backend_login():
    """POST real access_token to backend — starts engine without fake callback flow."""
    if not TOKEN_CACHE.exists():
        print("[AUTO-LOGIN] No cached token found")
        return False

    token_data = json.loads(TOKEN_CACHE.read_text())

    try:
        resp = requests.post(
            f"{BACKEND_URL}/api/auto-login",
            json={
                "api_key": token_data["api_key"],
                "access_token": token_data["access_token"],
                "api_secret": token_data.get("api_secret", ""),
            },
            timeout=30,
        )
        if resp.ok:
            print(f"[AUTO-LOGIN] Backend engine started: {resp.json()}")
            return True
        else:
            print(f"[AUTO-LOGIN] Backend rejected: {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"[AUTO-LOGIN] Backend trigger failed: {e}")
        return False


def run_auto_login():
    """Run the full auto-login flow."""
    now = ist_now()
    print(f"\n{'='*50}")
    print(f"[AUTO-LOGIN] Starting at {now.strftime('%Y-%m-%d %H:%M:%S IST')}")
    print(f"{'='*50}")

    try:
        # Check if already logged in today
        if TOKEN_CACHE.exists():
            data = json.loads(TOKEN_CACHE.read_text())
            if data.get("date") == now.strftime("%Y-%m-%d"):
                print(f"[AUTO-LOGIN] Already logged in today at {data.get('login_time', 'unknown')}")
                return data.get("access_token")

        access_token = kite_login()
        print(f"[AUTO-LOGIN] Login successful!")
        print(f"[AUTO-LOGIN] Access token: {access_token[:15]}...")

        # Trigger backend
        trigger_backend_login()

        return access_token

    except Exception as e:
        print(f"[AUTO-LOGIN] FAILED: {e}")
        return None


def daemon_mode():
    """Run as daemon — auto-login at 6:05 AM IST daily.

    Window: 06:05 - 06:59 IST. Kite access_tokens expire at 6 AM IST,
    so we refresh as soon as that's done. This means whenever you wake
    up (any time after ~6:10 AM), dashboard is already authenticated
    with a fresh token valid for the full trading day.

    Retry: if 6:05 attempt fails (e.g., transient Kite outage), tries
    every 2 minutes until 6:59. Past that, gives up till next day.
    """
    print("[AUTO-LOGIN] Daemon mode started. Will login at 6:05 AM IST daily.")
    print("[AUTO-LOGIN] (Token expires 6 AM, refresh window 06:05-06:59 IST.)")

    LOGIN_HOUR = 6
    LOGIN_MIN_START = 5
    LOGIN_MIN_END = 59  # Try until 06:59 if first attempt fails

    while True:
        now = ist_now()

        # Weekend skip — no markets, no trade, no token needed
        if now.weekday() >= 5:
            print(f"[AUTO-LOGIN] Skipping — weekend ({now.strftime('%A')})")
            time.sleep(3600)
            continue

        # In login window?
        if now.hour == LOGIN_HOUR and LOGIN_MIN_START <= now.minute <= LOGIN_MIN_END:
            # Already logged in today?
            if TOKEN_CACHE.exists():
                try:
                    data = json.loads(TOKEN_CACHE.read_text())
                    if data.get("date") == now.strftime("%Y-%m-%d"):
                        time.sleep(60)  # Already done — sleep past window
                        continue
                except Exception:
                    pass

            # Try login. Returns access_token on success, None on failure.
            result = run_auto_login()
            if result:
                # Success — sleep 5 min to skip rest of window
                time.sleep(300)
            else:
                # Failed — retry in 2 minutes (transient Kite issue?)
                print("[AUTO-LOGIN] Will retry in 120s...")
                time.sleep(120)
        else:
            # Outside window — check every 30 seconds
            time.sleep(30)


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        daemon_mode()
    else:
        token = run_auto_login()
        if token:
            print(f"\nAccess Token: {token}")
            print("Use this in your dashboard or set KITE_ACCESS_TOKEN env var")
        else:
            print("\nLogin failed. Check credentials and try again.")
            sys.exit(1)
