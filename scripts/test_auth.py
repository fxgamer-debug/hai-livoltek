"""Standalone probe for the Livoltek v2 login endpoint (May 2026 API).

Reads credentials from environment variables and performs:

- Login: ``POST /nbp/login/customer`` with MD5(password)
- Session register: ``POST /ctrller-manager/login/login`` with Bearer token

Usage
-----
    cd ~/Applications/11labs/livoltek

    export LIVOLTEK_LOGIN_ACCOUNT='your-username'
    export LIVOLTEK_PASSWORD='your-password'

    python3 scripts/test_auth.py

The script has no third-party dependencies — it uses urllib from the
standard library so you don't need to set up a venv.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

BASE_URL = os.environ.get("LIVOLTEK_BASE_URL", "https://evs.livoltek-portal.com").rstrip("/")
LOGIN_PATH = "/nbp/login/customer"
SESSION_REGISTER_PATH = "/ctrller-manager/login/login"

TIMEOUT_SECONDS = 15


def _post_json(url: str, body: dict[str, Any]) -> tuple[int, Any, str]:
    """POST a JSON body and return (status, parsed_json_or_text, raw_text)."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        status = exc.code
    except urllib.error.URLError as exc:
        return -1, None, f"URLError: {exc.reason}"

    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = None
    return status, parsed, raw


def _redact(value: str, keep: int = 4) -> str:
    if not value:
        return "<empty>"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}…{value[-keep:]} (len={len(value)})"


def main() -> int:
    login_account = os.environ.get("LIVOLTEK_LOGIN_ACCOUNT")
    password = os.environ.get("LIVOLTEK_PASSWORD")

    if not login_account or not password:
        print("ERROR: please export LIVOLTEK_LOGIN_ACCOUNT and LIVOLTEK_PASSWORD first.")
        return 2

    password_hash = hashlib.md5(password.encode()).hexdigest()

    print("=== Livoltek v2 auth probe ===")
    print(f"login_account: {_redact(login_account)}")
    print(f"password_hash: {_redact(password_hash)}")

    login_url = f"{BASE_URL}{LOGIN_PATH}"
    status, parsed, raw = _post_json(
        login_url,
        {"login_account": login_account, "password": password_hash},
    )
    print(f"\nPOST {login_url}")
    print(f"HTTP {status}")
    if parsed is not None:
        pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
        if len(pretty) > 1200:
            pretty = pretty[:1200] + "\n  ... (truncated)"
        print(pretty)
    else:
        print(raw[:600])

    if not isinstance(parsed, dict) or parsed.get("msgCode") != "operate.success":
        print("\nFAIL: login rejected.")
        return 1

    data = parsed.get("data") if isinstance(parsed.get("data"), dict) else None
    token = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        print("\nFAIL: login returned no access_token.")
        return 1

    register_url = f"{BASE_URL}{SESSION_REGISTER_PATH}"
    req = urllib.request.Request(
        register_url,
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            reg_raw = resp.read().decode("utf-8", errors="replace")
            print(f"\nPOST {register_url}")
            print(f"HTTP {resp.status}")
            print(reg_raw[:600])
    except Exception as exc:  # noqa: BLE001
        print(f"\nWARN: session register failed: {exc}")

    print("\nSUCCESS: token acquired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
