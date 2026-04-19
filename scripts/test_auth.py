"""Standalone probe for the Livoltek /hess/api/login endpoint.

Reads credentials from environment variables (so they never end up in
shell history or get baked into the file) and tries every plausible
combination of:

  - server region: EU + Global
  - key encoding : as-pasted, \\r\\n -> real CR/LF, stripped of all
                   trailing whitespace
  - userToken    : optional; only validated if you also export it

For each attempt it prints the HTTP status, the parsed JSON, and a tiny
verdict line. The point is to see exactly which combination the live
backend accepts so we can lock the integration to that one form.

Usage
-----
    cd ~/Applications/11labs/livoltek

    export LIVOLTEK_SECUID='your-security-id'
    export LIVOLTEK_KEY='paste-key-exactly-as-shown'   # keep the trailing \\r\\n
    export LIVOLTEK_USER_TOKEN='optional-user-token'   # optional

    python3 scripts/test_auth.py

The script has no third-party dependencies — it uses urllib from the
standard library so you don't need to set up a venv.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

SERVERS: dict[str, str] = {
    "EU":     "https://api-eu.livoltek-portal.com:8081",
    "Global": "https://api.livoltek-portal.com:8081",
}

LOGIN_PATH = "/hess/api/login"
SITES_PATH = "/hess/api/userSites/list"

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


def _get_json(url: str) -> tuple[int, Any, str]:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json"},
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


def _msg(p: dict[str, Any]) -> str | None:
    """Return the human-readable message field (login uses ``message``)."""
    text = p.get("message")
    if text is None:
        text = p.get("msg")
    return text if isinstance(text, str) else None


def _unwrap(parsed: Any) -> Any:
    """Collapse all three Livoltek response shapes into one canonical form.

    The Livoltek backend uses three different shapes that vary by endpoint
    and auth state. See ``api.py:_normalise_response`` for the catalogue;
    this helper mirrors that logic so the probe's verdict matches what
    the integration sees at runtime.
    """
    if not isinstance(parsed, dict):
        return parsed
    if "msgCode" in parsed:
        return parsed
    code = str(parsed.get("code") or "")
    msg = (_msg(parsed) or "").upper()
    if code not in {"200", "SUCCESS"} and msg not in {"200", "SUCCESS"}:
        return parsed
    inner = parsed.get("data")
    if isinstance(inner, dict) and "msgCode" in inner:
        return inner
    return {"msgCode": "operate.success", "message": _msg(parsed), "data": inner}


def _verdict(parsed: Any) -> str:
    if not isinstance(parsed, dict):
        return "no JSON / unexpected shape"
    body = _unwrap(parsed)
    if not isinstance(body, dict):
        return "no application body inside transport envelope"
    code = body.get("msgCode")
    msg = _msg(body)
    if code != "operate.success":
        return f"FAIL — msgCode={code!r} message={msg!r}"

    data = body.get("data")

    # The wrong-region sentinel: msgCode is "operate.success" but the
    # ``data`` payload is the literal string "user not exit" (sic),
    # meaning the account does not live on this shard.
    if isinstance(data, str) and data.lower().startswith("user not exi"):
        return f"FAIL — wrong region (server returned {data!r})"

    if isinstance(data, str):
        return f"SUCCESS — JWT len={len(data)}"
    return f"SUCCESS — data type={type(data).__name__}"


def _key_variants(raw_key: str) -> list[tuple[str, str]]:
    """Return labelled variants of the key to try."""
    normalised = raw_key.replace("\\r", "\r").replace("\\n", "\n")
    stripped = raw_key.rstrip("\\r\\n").rstrip("\r\n").rstrip()
    variants = [
        ("as-pasted (literal text)", raw_key),
        ("normalised (\\r\\n -> CR/LF)", normalised),
        ("stripped (no trailing whitespace)", stripped),
    ]
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for label, value in variants:
        if value in seen:
            continue
        seen.add(value)
        deduped.append((label, value))
    return deduped


def probe_login(secuid: str, raw_key: str) -> list[tuple[str, str, dict[str, Any] | None, str]]:
    """Run the full login matrix. Return a list of (server, variant, parsed, raw)."""
    results = []
    for server_label, base in SERVERS.items():
        url = f"{base}{LOGIN_PATH}"
        for variant_label, key_value in _key_variants(raw_key):
            body = {"secuid": secuid, "key": key_value}
            status, parsed, raw = _post_json(url, body)
            print(f"\n[{server_label}] {variant_label}")
            print(f"  POST {url}")
            print(f"  body.key (repr) = {key_value!r}  (len={len(key_value)})")
            print(f"  HTTP {status}")
            if parsed is not None:
                pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
                if len(pretty) > 600:
                    pretty = pretty[:600] + "\n  ... (truncated)"
                print(f"  body: {pretty}")
            else:
                snippet = raw[:300] + ("..." if len(raw) > 300 else "")
                print(f"  raw : {snippet!r}")
            print(f"  >> {_verdict(parsed)}")
            results.append((server_label, variant_label, parsed, raw))
    return results


def probe_user_token(user_token: str, results: list[tuple[str, str, dict[str, Any] | None, str]]) -> None:
    print("\n=== userToken sanity check (no login required) ===")
    for server_label, base in SERVERS.items():
        url = f"{base}{SITES_PATH}?page=1&size=10&userToken={user_token}"
        status, parsed, raw = _get_json(url)
        print(f"\n[{server_label}] GET {SITES_PATH}?...&userToken=…")
        print(f"  HTTP {status}")
        if parsed is not None:
            verdict = _verdict(parsed)
            body = _unwrap(parsed)
            if isinstance(body, dict):
                print(f"  msgCode={body.get('msgCode')!r} message={_msg(body)!r}")
                data = body.get("data")
                if isinstance(data, dict):
                    lst = data.get("list") or []
                    print(f"  sites returned: {len(lst)}")
            print(f"  >> {verdict}")
        else:
            snippet = raw[:300] + ("..." if len(raw) > 300 else "")
            print(f"  raw : {snippet!r}")


def main() -> int:
    secuid = os.environ.get("LIVOLTEK_SECUID")
    raw_key = os.environ.get("LIVOLTEK_KEY")
    user_token = os.environ.get("LIVOLTEK_USER_TOKEN")

    if not secuid or not raw_key:
        print("ERROR: please export LIVOLTEK_SECUID and LIVOLTEK_KEY first.")
        print("Optional: LIVOLTEK_USER_TOKEN to also exercise the userSites endpoint.")
        return 2

    print("=== Livoltek auth probe ===")
    print(f"secuid : {_redact(secuid)}")
    print(f"raw key: {_redact(raw_key)}  (raw repr ends with {raw_key[-6:]!r})")
    if user_token:
        print(f"token  : {_redact(user_token, keep=8)}")
    print(f"trying {len(SERVERS)} servers x {len(_key_variants(raw_key))} key variants")

    results = probe_login(secuid, raw_key)

    if user_token:
        probe_user_token(user_token, results)

    print("\n=== summary ===")
    any_success = False
    for server_label, variant_label, parsed, _raw in results:
        verdict = _verdict(parsed)
        marker = "OK " if verdict.startswith("SUCCESS") else "   "
        if verdict.startswith("SUCCESS"):
            any_success = True
        print(f"  {marker}[{server_label}] {variant_label}: {verdict}")

    if not any_success:
        print("\nNo combination authenticated. Likely causes:")
        print("  - secuid + key are valid but for a region not listed above")
        print("  - the key was copied with a missing/extra character")
        print("  - the account has been disabled or rate-limited")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
