"""Pure API client for the Livoltek portal.

This module performs all HTTP communication with the Livoltek cloud APIs.
It deliberately has no Home Assistant imports (other than the shared
package logger via ``const``) so it can be unit-tested independently.

Authentication overview
-----------------------
The Livoltek backend requires *two* tokens for every data request:

1. A **login token** (short-lived JWT, ~2h) returned by ``POST /hess/api/login``
   using ``secuid`` + ``key``. It is sent as the literal value of the
   ``Authorization`` header (with no ``Bearer`` prefix).
2. A **user token** (long-lived JWT) generated manually by the user from the
   Livoltek portal. It is sent as a ``userToken`` query parameter on public
   endpoints and as the ``Authorization`` header on private endpoints if the
   login token is unavailable.

API key formatting quirk
~~~~~~~~~~~~~~~~~~~~~~~~
The Livoltek portal displays the API key with a visible ``\\r\\n`` suffix
(four ASCII characters: backslash-r-backslash-n). Users typically copy that
suffix verbatim into the config form. The backend, however, only accepts the
key when those four characters are converted to **real** CR (0x0D) and LF
(0x0A) bytes before being JSON-encoded. The original
``adamlonsdale/hass-livoltek`` integration did the same conversion, and it is
the only form the upstream API accepts. See :func:`_normalise_api_key`.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from .const import (
    ALARM_FILTER_ENDPOINT,
    CURRENT_POWER_FLOW_ENDPOINT,
    DEVICES_ENDPOINT,
    ENERGY_STORAGE_INFO_ENDPOINT,
    LOGGER,
    LOGIN_ENDPOINT,
    POINT_INFO_ENDPOINT,
    PRIVATE_API_BASE,
    PUBLIC_API_BASE,
    QUERY_POWER_FLOW_ENDPOINT,
    REQUEST_TIMEOUT,
    SIGNAL_DEVICE_STATUS_ENDPOINT,
    SITES_ENDPOINT,
    TOKEN_REFRESH_BUFFER,
)


class LivoltekAuthError(Exception):
    """Raised when authentication with the Livoltek API fails."""


class LivoltekConnectionError(Exception):
    """Raised when the Livoltek API is unreachable or times out."""


class LivoltekApiError(Exception):
    """Raised when the Livoltek API returns a non-success message code."""


_SUCCESS_MSG_CODE = "operate.success"


def _normalise_api_key(api_key: str) -> str:
    """Convert visible ``\\r``/``\\n`` escape sequences in the API key.

    The Livoltek portal hands users a key that ends with the four visible
    characters ``\\r\\n`` (backslash, r, backslash, n). The HA config form
    preserves that text verbatim, but the backend only accepts the key when
    those characters are turned into real carriage-return / line-feed bytes
    before JSON-encoding. Sending the literal text instead of the control
    characters yields a generic "invalid credentials" error from the API.

    This mirrors the behaviour of the original ``adamlonsdale/hass-livoltek``
    integration, which is the only documented working reference.
    """
    return api_key.replace("\\r", "\r").replace("\\n", "\n")


def _decode_token_expiry(token: str) -> int:
    """Decode a JWT's ``exp`` claim without verifying its signature.

    PyJWT is intentionally **not** used here so the integration has zero
    external Python dependencies beyond what Home Assistant core already
    ships. We only need the ``exp`` claim (a unix timestamp) so a plain
    base64-decode of the middle segment is sufficient.

    The padding step (``'=' * (4 - len(payload_part) % 4)``) is critical:
    JWT base64 segments are typically un-padded, and :func:`base64.b64decode`
    raises :class:`binascii.Error` ("Incorrect padding") when the input
    length isn't a multiple of 4. Do not remove or "simplify" it.
    """
    try:
        payload_part = token.split(".")[1]
        padded = payload_part + "=" * (4 - len(payload_part) % 4)
        payload = json.loads(base64.b64decode(padded))
        return int(payload["exp"])
    except (IndexError, KeyError, ValueError, binascii.Error) as err:
        raise LivoltekAuthError(f"Could not decode token expiry: {err}") from err


class LivoltekApiClient:
    """Async client for the Livoltek public + private APIs."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        access_token: str | None = None,
        token_expiry: int | None = None,
        user_token: str | None = None,
        secuid: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """Initialise the client.

        ``access_token`` / ``token_expiry`` may be passed in to seed the cache
        from a previously persisted config entry, avoiding an immediate login.
        """
        self._session = session
        self._access_token: str | None = access_token
        self._token_expiry: int | None = token_expiry
        self._user_token: str | None = user_token
        self._secuid: str | None = secuid
        self._api_key: str | None = api_key
        self._token_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    @property
    def access_token(self) -> str | None:
        """Return the cached login token (may be expired)."""
        return self._access_token

    @property
    def token_expiry(self) -> int | None:
        """Return the cached login token expiry as a unix timestamp."""
        return self._token_expiry

    async def login(self, secuid: str, api_key: str) -> str:
        """Log in and cache a fresh access token.

        Returns the new token. Raises :class:`LivoltekAuthError` on failure.
        """
        url = f"{PUBLIC_API_BASE}{LOGIN_ENDPOINT}"
        # The portal-issued key contains a visible "\r\n" suffix that must be
        # transmitted as real CR/LF bytes (see _normalise_api_key for details).
        normalised_key = _normalise_api_key(api_key)
        payload = {"secuid": secuid, "key": normalised_key}
        try:
            async with self._session.post(
                url,
                json=payload,
                timeout=REQUEST_TIMEOUT,
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status >= 500:
                    raise LivoltekConnectionError(
                        f"Login HTTP {resp.status}"
                    )
                data = await resp.json(content_type=None)
        except asyncio.TimeoutError as err:
            raise LivoltekConnectionError("Login request timed out") from err
        except aiohttp.ClientError as err:
            raise LivoltekConnectionError(f"Login transport error: {err}") from err

        if not isinstance(data, dict):
            raise LivoltekAuthError(f"Unexpected login response: {data!r}")

        msg_code = data.get("msgCode")
        token = data.get("data")
        if msg_code != _SUCCESS_MSG_CODE or not token or not isinstance(token, str):
            raise LivoltekAuthError(
                f"Login rejected (msgCode={msg_code!r}, msg={data.get('msg')!r})"
            )

        expiry = _decode_token_expiry(token)

        self._access_token = token
        self._token_expiry = expiry
        self._secuid = secuid
        self._api_key = api_key
        LOGGER.debug(
            "Livoltek login successful, token expires at %s",
            datetime.fromtimestamp(expiry, tz=timezone.utc).isoformat(),
        )
        return token

    async def ensure_token(self, secuid: str | None = None, api_key: str | None = None) -> None:
        """Refresh the login token if it is missing or about to expire."""
        secuid = secuid or self._secuid
        api_key = api_key or self._api_key
        if not secuid or not api_key:
            raise LivoltekAuthError("Missing secuid/api_key for token refresh")

        async with self._token_lock:
            now = int(time.time())
            buffer = int(TOKEN_REFRESH_BUFFER.total_seconds())
            if (
                self._access_token
                and self._token_expiry
                and self._token_expiry > now + buffer
            ):
                return
            await self.login(secuid, api_key)

    def _get_headers(self) -> dict[str, str]:
        """Return the standard headers for a private API request.

        Note: the Authorization header value is the raw token — *no* Bearer prefix.
        """
        if not self._access_token:
            raise LivoltekAuthError("Cannot build headers without a login token")
        return {
            "Authorization": self._access_token,
            "Content-Type": "application/json",
            "language": "en",
            "timeZone": "Europe/Bucharest",
        }

    # ------------------------------------------------------------------
    # Low-level request helpers
    # ------------------------------------------------------------------

    async def _request_full(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        retry_on_401: bool = True,
    ) -> dict[str, Any]:
        """Send a request and return the **full** parsed JSON payload.

        Unlike :meth:`_request`, this preserves the raw ``data`` field shape
        (including ``None`` / missing) so callers can distinguish between
        "API returned a successful empty result" and "API returned nothing
        useful at all". Used by setup-time helpers in ``config_flow``.
        """
        headers = self._get_headers()
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401 and retry_on_401:
                    LOGGER.debug("Got 401 from %s, refreshing token and retrying", url)
                    self._token_expiry = 0
                    await self.ensure_token()
                    return await self._request_full(
                        method,
                        url,
                        params=params,
                        json_body=json_body,
                        retry_on_401=False,
                    )
                if resp.status >= 400:
                    raise LivoltekApiError(
                        f"{method} {url} -> HTTP {resp.status}"
                    )
                payload = await resp.json(content_type=None)
        except asyncio.TimeoutError as err:
            raise LivoltekConnectionError(f"Timeout on {method} {url}") from err
        except aiohttp.ClientError as err:
            raise LivoltekConnectionError(f"Transport error on {method} {url}: {err}") from err

        if payload is None:
            raise LivoltekApiError(f"Empty response from {url}")
        if not isinstance(payload, dict):
            raise LivoltekApiError(f"Unexpected response from {url}: {payload!r}")

        msg_code = payload.get("msgCode")
        if msg_code != _SUCCESS_MSG_CODE:
            msg = payload.get("msg")
            if msg_code in ("login.invalid", "token.invalid", "user.token.invalid"):
                raise LivoltekAuthError(f"{url}: {msg_code} ({msg})")
            raise LivoltekApiError(f"{url}: {msg_code} ({msg})")

        return payload

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        retry_on_401: bool = True,
    ) -> dict[str, Any]:
        """Send a request and return the parsed JSON ``data`` payload.

        Convenience wrapper around :meth:`_request_full` that collapses
        ``None`` / missing ``data`` to an empty dict — appropriate for
        coordinator polling where a missing-but-successful response is
        equivalent to "nothing changed".
        """
        payload = await self._request_full(
            method,
            url,
            params=params,
            json_body=json_body,
            retry_on_401=retry_on_401,
        )
        return payload.get("data", {}) or {}

    async def _post_private(self, endpoint: str, body: dict[str, Any] | None = None,
                            params: dict[str, Any] | None = None) -> Any:
        """POST to the private API."""
        url = f"{PRIVATE_API_BASE}{endpoint}"
        return await self._request("POST", url, params=params, json_body=body or {})

    async def _get_public(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """GET from the public API.

        ``userToken`` is added automatically.
        """
        if not self._user_token:
            raise LivoltekAuthError("user_token is required for public API calls")
        url = f"{PUBLIC_API_BASE}{endpoint}"
        merged = dict(params or {})
        merged.setdefault("userToken", self._user_token)
        return await self._request("GET", url, params=merged)

    # ------------------------------------------------------------------
    # Public API — site / device discovery (used by config_flow only)
    # ------------------------------------------------------------------

    async def get_sites(self) -> Any:
        """Return the *raw* ``data`` field from ``/userSites/list``.

        The shape returned by the upstream API is not strictly guaranteed —
        it may be ``None``, a dict containing a ``list`` key, a bare list,
        or in pathological cases something else entirely. The caller
        (currently only :mod:`config_flow`) is responsible for inspecting
        the result and raising the appropriate user-facing error.
        """
        if not self._user_token:
            raise LivoltekAuthError("user_token is required for public API calls")
        url = f"{PUBLIC_API_BASE}{SITES_ENDPOINT}"
        params = {"page": 1, "size": 10, "userToken": self._user_token}
        payload = await self._request_full("GET", url, params=params)
        return payload.get("data")

    async def get_devices(self, site_id: str) -> Any:
        """Return the *raw* ``data`` field from ``/device/{site_id}/list``.

        Same caveats as :meth:`get_sites` regarding response shape.
        """
        if not self._user_token:
            raise LivoltekAuthError("user_token is required for public API calls")
        endpoint = DEVICES_ENDPOINT.format(site_id=site_id)
        url = f"{PUBLIC_API_BASE}{endpoint}"
        params = {"userToken": self._user_token}
        payload = await self._request_full("GET", url, params=params)
        return payload.get("data")

    # ------------------------------------------------------------------
    # Private API — telemetry endpoints used by the coordinators
    # ------------------------------------------------------------------

    async def get_energy_storage_info(self, device_id: int) -> dict[str, Any]:
        """Fetch the live energy storage info for ``device_id``."""
        return await self._post_private(
            ENERGY_STORAGE_INFO_ENDPOINT,
            params={"id": device_id, "isUseChangeUnit": "true"},
            body={},
        )

    async def get_signal_device_status(self, device_id: int) -> dict[str, Any]:
        """Fetch high-level device status (PCS state, work state, totals)."""
        return await self._post_private(
            SIGNAL_DEVICE_STATUS_ENDPOINT,
            params={"id": device_id, "isUseChangeUnit": "true"},
            body={},
        )

    async def get_query_power_flow(self, site_id: str) -> dict[str, Any]:
        """Fetch the current power-flow snapshot for ``site_id``."""
        endpoint = QUERY_POWER_FLOW_ENDPOINT.format(site_id=site_id)
        return await self._post_private(endpoint, body={})

    async def get_alarms(self, site_id: str, *, days: int = 30, page_size: int = 100) -> list[dict[str, Any]]:
        """Fetch alarms for ``site_id`` over the last ``days`` days."""
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)
        body = {
            "powerStationFilter": [int(site_id)],
            "filterTime": [
                start.strftime("%Y-%m-%d %H:%M:%S"),
                now.strftime("%Y-%m-%d %H:%M:%S"),
            ],
            "pageSize": page_size,
            "start": 1,
            "fuzzyQueryId": True,
            "showDescribe": True,
        }
        data = await self._post_private(ALARM_FILTER_ENDPOINT, body=body)
        if isinstance(data, dict):
            return list(data.get("list") or data.get("rows") or [])
        if isinstance(data, list):
            return data
        return []

    async def get_point_info(self, device_id: int) -> dict[str, Any]:
        """Fetch the full register snapshot for ``device_id`` (~148 fields)."""
        data = await self._post_private(POINT_INFO_ENDPOINT, body={"id": device_id})
        return data if isinstance(data, dict) else {}

    async def get_current_power_flow_fallback(
        self, site_id: str, user_token: str | None = None
    ) -> dict[str, Any]:
        """Public-API fallback for live power-flow values.

        Used by the fast coordinator when the private API is unreachable.
        """
        token = user_token or self._user_token
        if not token:
            raise LivoltekAuthError("user_token required for fallback call")
        endpoint = CURRENT_POWER_FLOW_ENDPOINT.format(site_id=site_id)
        return await self._get_public(endpoint, {"userToken": token})
