"""
=============================================================================
Dhan API v2 — Auto Token Manager
Balfund Trading Private Limited
=============================================================================
Automatically generates and renews your Dhan Access Token daily.

TWO METHODS SUPPORTED:
  Method 1 (RECOMMENDED — Fully Automatic):
      Uses TOTP (Time-based OTP) + PIN to generate token via API.
      Endpoint: POST https://auth.dhan.co/app/generateAccessToken

  Method 2 (Fallback — if token is still active):
      Renews an existing valid token for another 24 hours.
      Endpoint: GET https://api.dhan.co/v2/RenewToken

Shared token: reads from C:/balfund_shared/dhan_token.json (Windows)
              or ~/balfund_shared/dhan_token.json (Mac/Linux)
=============================================================================
"""

import os
import sys
import json
import time
import logging
import argparse
import platform
import schedule
from datetime import datetime, timezone
from pathlib import Path

import requests
import pyotp
from dotenv import load_dotenv, set_key

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("DhanTokenManager")

# ── PyInstaller-safe base path ────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent

ENV_FILE = _BASE / ".env"

# ── Shared token file ─────────────────────────────────────────────────────────
if platform.system() == "Windows":
    SHARED_TOKEN_FILE = Path("C:/balfund_shared/dhan_token.json")
else:
    SHARED_TOKEN_FILE = Path.home() / "balfund_shared" / "dhan_token.json"


def read_shared_token() -> dict:
    """Read token from the shared JSON file written by dhan-token-generator."""
    if not SHARED_TOKEN_FILE.exists():
        return {}
    try:
        data = json.loads(SHARED_TOKEN_FILE.read_text(encoding="utf-8"))
        client_id    = str(data.get("client_id", "")).strip()
        access_token = str(data.get("access_token", "")).strip()
        if client_id and access_token:
            return {"client_id": client_id, "access_token": access_token}
    except Exception as e:
        log.warning("Could not read shared token file: %s", e)
    return {}


def load_config() -> dict:
    """Re-reads .env. Shared token from dhan-token-generator takes priority."""
    load_dotenv(ENV_FILE, override=True)
    config = {
        "client_id":    os.getenv("DHAN_CLIENT_ID", "").strip(),
        "pin":          os.getenv("DHAN_PIN", "").strip(),
        "totp_secret":  os.getenv("DHAN_TOTP_SECRET", "").strip(),
        "access_token": os.getenv("DHAN_ACCESS_TOKEN", "").strip(),
    }
    if not config["client_id"]:
        raise ValueError("DHAN_CLIENT_ID is missing in .env file.")
    shared = read_shared_token()
    if shared.get("access_token"):
        config["access_token"] = shared["access_token"]
    return config


def save_token_to_env(access_token: str, expiry: str = ""):
    set_key(str(ENV_FILE), "DHAN_ACCESS_TOKEN", access_token)
    if expiry:
        set_key(str(ENV_FILE), "DHAN_TOKEN_EXPIRY", expiry)
    log.info("Token saved to %s", ENV_FILE)


# ── Method 1 — TOTP-based Token Generation ───────────────────────────────────

def generate_totp(totp_secret: str) -> str:
    totp = pyotp.TOTP(totp_secret)
    code = totp.now()
    log.info("Generated TOTP: %s (valid for ~%ds)", code, 30 - (int(time.time()) % 30))
    return code


def generate_token_via_totp(client_id: str, pin: str, totp_secret: str) -> dict:
    totp_code = generate_totp(totp_secret)
    url = (
        f"https://auth.dhan.co/app/generateAccessToken"
        f"?dhanClientId={client_id}&pin={pin}&totp={totp_code}"
    )
    log.info("Requesting new token via TOTP for client %s...", client_id)
    try:
        resp = requests.post(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if "accessToken" in data:
            log.info("✅ Token generated! Expires: %s", data.get("expiryTime", "N/A"))
            return {
                "success":      True,
                "access_token": data["accessToken"],
                "expiry":       data.get("expiryTime", ""),
                "client_name":  data.get("dhanClientName", ""),
                "method":       "TOTP",
            }
        log.error("❌ Token generation failed: %s", data)
        return {"success": False, "error": str(data)}
    except requests.exceptions.HTTPError as e:
        log.error("❌ HTTP error: %s — %s", e.response.status_code, e.response.text)
        return {"success": False, "error": str(e)}
    except Exception as e:
        log.error("❌ Request failed: %s", e)
        return {"success": False, "error": str(e)}


# ── Method 2 — Renew Existing Token ──────────────────────────────────────────

def renew_token(client_id: str, access_token: str) -> dict:
    url = "https://api.dhan.co/v2/RenewToken"
    headers = {
        "access-token": access_token,
        "dhanClientId": client_id,
        "Content-Type": "application/json",
    }
    log.info("Attempting to renew existing token...")
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if "accessToken" in data:
            log.info("✅ Token renewed! Expires: %s", data.get("expiryTime", "N/A"))
            return {
                "success":      True,
                "access_token": data["accessToken"],
                "expiry":       data.get("expiryTime", ""),
                "method":       "RENEW",
            }
        log.warning("⚠️ Renew returned unexpected response: %s", data)
        return {"success": False, "error": str(data)}
    except requests.exceptions.HTTPError as e:
        log.warning("⚠️ Token renew failed: %s", e.response.status_code)
        return {"success": False, "error": str(e)}
    except Exception as e:
        log.error("❌ Renew request failed: %s", e)
        return {"success": False, "error": str(e)}


# ── Verify Token ──────────────────────────────────────────────────────────────

def verify_token(client_id: str, access_token: str) -> bool:
    if not access_token:
        return False
    url = "https://api.dhan.co/v2/profile"
    headers = {"access-token": access_token, "client-id": client_id}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            log.info("✅ Current token is valid.")
            return True
        log.warning("⚠️ Token validation failed: %s", resp.status_code)
        return False
    except Exception as e:
        log.warning("⚠️ Token check error: %s", e)
        return False


# ── Master Function ───────────────────────────────────────────────────────────

def get_fresh_token(config: dict, force_new: bool = False) -> str:
    client_id    = config["client_id"]
    pin          = config["pin"]
    totp_secret  = config["totp_secret"]
    access_token = config["access_token"]
    result = None

    if access_token and not force_new:
        if verify_token(client_id, access_token):
            result = renew_token(client_id, access_token)
            if result["success"]:
                save_token_to_env(result["access_token"], result.get("expiry", ""))
                return result["access_token"]

    if totp_secret and pin:
        result = generate_token_via_totp(client_id, pin, totp_secret)
        if result["success"]:
            save_token_to_env(result["access_token"], result.get("expiry", ""))
            return result["access_token"]
    else:
        log.error("❌ Cannot generate token: DHAN_PIN or DHAN_TOTP_SECRET missing in .env")

    raise RuntimeError(f"Token generation failed: {result.get('error', 'Unknown') if result else 'No result'}")


# ── Scheduler ─────────────────────────────────────────────────────────────────

def scheduled_refresh():
    log.info("=" * 60)
    log.info("⏰ Scheduled token refresh starting...")
    try:
        config = load_config()
        token  = get_fresh_token(config, force_new=True)
        log.info("✅ Scheduled refresh complete. New token: %s...", token[:20])
    except Exception as e:
        log.error("❌ Scheduled refresh failed: %s", e)
    log.info("=" * 60)


def run_daemon(refresh_time: str = "08:00"):
    log.info("🚀 DhanTokenManager daemon started.")
    log.info("   Token will auto-refresh daily at %s", refresh_time)
    scheduled_refresh()
    schedule.every().day.at(refresh_time).do(scheduled_refresh)
    log.info("⏳ Next refresh scheduled at %s. Running...", refresh_time)
    while True:
        schedule.run_pending()
        time.sleep(30)


# ── Setup Helper ──────────────────────────────────────────────────────────────

def create_env_template():
    if ENV_FILE.exists():
        log.info(".env already exists at %s", ENV_FILE.absolute())
        return
    template = """\
# ──────────────────────────────────────────────
# Dhan API Credentials — OI Option Seller
# ──────────────────────────────────────────────
DHAN_CLIENT_ID=
DHAN_PIN=
DHAN_TOTP_SECRET=
DHAN_ACCESS_TOKEN=
DHAN_TOKEN_EXPIRY=
"""
    with open(ENV_FILE, "w") as f:
        f.write(template)
    log.info("✅ Created .env template at %s", ENV_FILE.absolute())


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dhan API v2 Auto Token Manager")
    parser.add_argument("--daemon",       action="store_true")
    parser.add_argument("--refresh-time", default="08:00")
    parser.add_argument("--setup",        action="store_true")
    parser.add_argument("--force",        action="store_true")
    parser.add_argument("--verify",       action="store_true")
    args = parser.parse_args()

    if args.setup:
        create_env_template()
    elif args.verify:
        cfg = load_config()
        print(f"Token valid: {verify_token(cfg['client_id'], cfg['access_token'])}")
    elif args.daemon:
        run_daemon(refresh_time=args.refresh_time)
    else:
        try:
            cfg   = load_config()
            token = get_fresh_token(cfg, force_new=args.force)
            print(f"\n{'='*60}\n✅ ACCESS TOKEN:\n   {token}\n{'='*60}\n")
        except Exception as e:
            log.error("Failed: %s", e)
            exit(1)
