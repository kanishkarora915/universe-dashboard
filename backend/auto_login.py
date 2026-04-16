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

    # Step 3: Hit Kite Connect login URL to get request_token
    print(f"[AUTO-LOGIN] Step 3: Getting request_token from Kite Connect...")
    kite_login_url = f"https://kite.trade/connect/login?v=3&api_key={API_KEY}"
    redirect_resp = session.get(kite_login_url, allow_redirects=False)

    # Follow redirects manually to extract request_token
    if redirect_resp.status_code in (301, 302, 303, 307):
        redirect_url = redirect_resp.headers.get("Location", "")
    else:
        # Try following full redirect chain
        redirect_resp = session.get(kite_login_url, allow_redirects=True)
        redirect_url = redirect_resp.url

    if "request_token=" not in redirect_url:
        # Try alternate approach
        redirect_resp = session.get(
            f"https://kite.zerodha.com/connect/login?v=3&api_key={API_KEY}",
            allow_redirects=True
        )
        redirect_url = redirect_resp.url

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
    """Tell the backend to use the new access_token."""
    if not TOKEN_CACHE.exists():
        print("[AUTO-LOGIN] No cached token found")
        return False

    token_data = json.loads(TOKEN_CACHE.read_text())

    # Option 1: POST to backend login API
    try:
        # First login with API key/secret
        resp = requests.post(f"{BACKEND_URL}/api/login", json={
            "api_key": token_data["api_key"],
            "api_secret": token_data["api_secret"],
        })
        if resp.ok:
            login_url = resp.json().get("login_url", "")
            print(f"[AUTO-LOGIN] Backend login initiated")

        # Now simulate the callback with access_token
        resp2 = requests.get(f"{BACKEND_URL}/api/callback", params={
            "request_token": "auto_" + token_data["access_token"][:20],
            "status": "success",
        }, allow_redirects=False)
        print(f"[AUTO-LOGIN] Backend callback triggered: {resp2.status_code}")
        return True
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
    """Run as daemon — auto-login at 8:55 AM IST daily."""
    print("[AUTO-LOGIN] Daemon mode started. Will login at 8:55 AM IST daily.")

    while True:
        now = ist_now()

        # Check: is it 8:55 AM and not yet logged in today?
        if now.hour == 8 and 55 <= now.minute <= 59:
            if TOKEN_CACHE.exists():
                try:
                    data = json.loads(TOKEN_CACHE.read_text())
                    if data.get("date") == now.strftime("%Y-%m-%d"):
                        time.sleep(60)  # Already done today
                        continue
                except Exception:
                    pass

            # Weekend/holiday check
            if now.weekday() >= 5:
                print(f"[AUTO-LOGIN] Skipping — weekend ({now.strftime('%A')})")
                time.sleep(3600)
                continue

            run_auto_login()
            time.sleep(300)  # Wait 5 min before next check
        else:
            # Sleep until next check (check every 30 seconds)
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
