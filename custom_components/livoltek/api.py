"""Pure API client for the Livoltek portal (May 2026 v2 API).

This module performs all HTTP communication with the Livoltek cloud APIs.
It deliberately has no Home Assistant imports (other than the shared
package logger via ``const``) so it can be unit-tested independently.

Auth overview (v2)
------------------
1. Login: ``POST /nbp/login/customer`` with ``{login_account, password(md5)}``
   returns ``data.access_token`` (JWT) + ``data.session_expiry_time`` (unix ms).
2. Session register: ``POST /ctrller-manager/login/login`` with Bearer token.
   Required for alarm endpoints; called after every login for consistency.

All subsequent calls use ``Authorization: Bearer <token>``.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from .const import (
    ALARM_FILTER_ENDPOINT,
    DEFAULT_PRODUCT_TYPE,
    ENERGY_STORAGE_INFO_ENDPOINT,
    GET_DEVICES_ENDPOINT,
    GET_STATIONS_ENDPOINT,
    LOGGER,
    LOGIN_ENDPOINT,
    POINT_INFO_ENDPOINT,
    POWER_FLOW_FALLBACK_ENDPOINT,
    QUERY_POWER_FLOW_ENDPOINT,
    REQUEST_TIMEOUT,
    SESSION_REGISTER_ENDPOINT,
    SIGNAL_DEVICE_STATUS_ENDPOINT,
    TOKEN_REFRESH_BUFFER,
)


class LivoltekAuthError(Exception):
    """Raised when authentication with the Livoltek API fails."""


class LivoltekConnectionError(Exception):
    """Raised when the Livoltek API is unreachable or times out."""


class LivoltekApiError(Exception):
    """Raised when the Livoltek API returns a non-success message code."""


_SUCCESS_CODE = "operate.success"


def _msg_text(payload: dict[str, Any]) -> str | None:
    """Return human message field from a Livoltek payload."""
    for key in ("message", "msg", "msg_text"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _msg_code(payload: dict[str, Any]) -> str | None:
    """Return msgCode (or msg_code) from a Livoltek payload."""
    code = payload.get("msgCode")
    if isinstance(code, str):
        return code
    code = payload.get("msg_code")
    if isinstance(code, str):
        return code
    return None


def _data_field(payload: dict[str, Any]) -> Any:
    """Return data field from a Livoltek payload."""
    return payload.get("data")


def _now_ms() -> int:
    return int(time.time() * 1000)


class LivoltekApiClient:
    """Async client for the Livoltek v2 API (single backend)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        base_url: str,
        access_token: str | None = None,
        token_expiry: int | None = None,
    ) -> None:
        """Initialise the client.

        ``access_token`` / ``token_expiry`` may be passed in to seed the cache
        from a previously persisted config entry, avoiding an immediate login.
        """
        self._session = session
        self._base = base_url.rstrip("/")
        self._access_token: str | None = access_token
        self._token_expiry: int | None = token_expiry
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

    async def login(self, login_account: str, password_hash: str) -> tuple[str, int]:
        """Log in and cache a fresh access token + expiry (unix ms).

        Returns (token, expiry_ms). Raises :class:`LivoltekAuthError` on failure.
        """
        url = f"{self._base}{LOGIN_ENDPOINT}"
        payload = {"login_account": login_account, "password": password_hash}
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
                if resp.status == 401:
                    raise LivoltekAuthError("Login rejected (HTTP 401)")
                raw_response = await resp.json(content_type=None)
        except asyncio.TimeoutError as err:
            raise LivoltekConnectionError("Login request timed out") from err
        except aiohttp.ClientError as err:
            raise LivoltekConnectionError(f"Login transport error: {err}") from err

        if not isinstance(raw_response, dict):
            raise LivoltekAuthError(f"Unexpected login response: {raw_response!r}")

        code = _msg_code(raw_response)
        if code != _SUCCESS_CODE:
            raise LivoltekAuthError(
                f"Login rejected: msgCode={code!r} message={_msg_text(raw_response)!r}"
            )

        data = _data_field(raw_response)
        if not isinstance(data, dict):
            raise LivoltekAuthError(f"Login response missing data object: {raw_response!r}")

        token = data.get("access_token")
        expiry = data.get("session_expiry_time")
        if not isinstance(token, str) or not token:
            raise LivoltekAuthError(f"Login response missing access_token: {raw_response!r}")
        if not isinstance(expiry, (int, float)) or int(expiry) <= 0:
            raise LivoltekAuthError(
                f"Login response missing session_expiry_time: {raw_response!r}"
            )

        self._access_token = token
        self._token_expiry = int(expiry)
        LOGGER.debug(
            "Livoltek login successful, token expires at %s",
            datetime.fromtimestamp(self._token_expiry / 1000, tz=timezone.utc).isoformat(),
        )
        await self._register_session()
        return token, self._token_expiry

    async def _register_session(self) -> None:
        """Register session after login (required for alarms).

        Non-critical for core telemetry endpoints; errors are swallowed.
        """
        if not self._access_token:
            return
        url = f"{self._base}{SESSION_REGISTER_ENDPOINT}"
        async with self._token_lock:
            headers = {
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            }
        try:
            async with self._session.post(
                url,
                json={},
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status >= 400:
                    return
                await resp.json(content_type=None)
        except Exception:  # noqa: BLE001
            return

    async def ensure_token(self, login_account: str, password_hash: str) -> None:
        """Refresh token if missing or expiring soon."""
        async with self._token_lock:
            now_ms = _now_ms()
            buffer_ms = int(TOKEN_REFRESH_BUFFER.total_seconds() * 1000)
            if (
                self._access_token
                and self._token_expiry
                and int(self._token_expiry) > now_ms + buffer_ms
            ):
                return
            await self.login(login_account, password_hash)

    def _get_headers(self) -> dict[str, str]:
        """Return standard headers for a v2 API request."""
        if not self._access_token:
            raise LivoltekAuthError("Cannot build headers without a login token")
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "language": "en",
        }

    # ------------------------------------------------------------------
    # Low-level request helpers
    # ------------------------------------------------------------------

    async def _request_json(
        self, method: str, endpoint: str, *, params: dict[str, Any] | None = None, json_body: Any | None = None
    ) -> dict[str, Any]:
        """Send a request and return the parsed JSON payload (dict)."""
        url = f"{self._base}{endpoint}"
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
                if resp.status == 401:
                    raise LivoltekAuthError(f"Unauthorized on {endpoint}")
                if resp.status >= 500:
                    raise LivoltekConnectionError(f"{method} {endpoint} -> HTTP {resp.status}")
                if resp.status >= 400:
                    text = (await resp.text())[:500]
                    raise LivoltekApiError(f"{method} {endpoint} -> HTTP {resp.status}: {text!r}")
                payload = await resp.json(content_type=None)
        except asyncio.TimeoutError as err:
            raise LivoltekConnectionError(f"Timeout on {method} {endpoint}") from err
        except aiohttp.ClientError as err:
            raise LivoltekConnectionError(f"Transport error on {method} {endpoint}: {err}") from err

        if payload is None:
            raise LivoltekApiError(f"Empty response from {endpoint}")
        if not isinstance(payload, dict):
            raise LivoltekApiError(f"Unexpected response from {endpoint}: {payload!r}")
        return payload

    async def _post(self, endpoint: str, body: dict[str, Any] | None = None, *, params: dict[str, Any] | None = None) -> Any:
        """POST and return the response `data`."""
        payload = await self._request_json("POST", endpoint, params=params, json_body=body or {})
        code = _msg_code(payload)
        if code != _SUCCESS_CODE:
            raise LivoltekApiError(f"{endpoint}: msgCode={code!r} message={_msg_text(payload)!r}")
        return _data_field(payload)

    async def _post_with_retry(
        self,
        endpoint: str,
        body: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
        login_account: str | None = None,
        password_hash: str | None = None,
    ) -> Any:
        """POST with a single token-refresh retry on 401."""
        try:
            return await self._post(endpoint, body, params=params)
        except LivoltekAuthError:
            if not login_account or not password_hash:
                raise
            # Force refresh and retry once
            self._token_expiry = 0
            await self.ensure_token(login_account, password_hash)
            return await self._post(endpoint, body, params=params)

    # ------------------------------------------------------------------
    # Discovery methods (used by config_flow only)
    # ------------------------------------------------------------------

    async def get_stations(self, *, login_account: str, password_hash: str) -> list[dict[str, Any]]:
        data = await self._post_with_retry(
            GET_STATIONS_ENDPOINT,
            {},
            login_account=login_account,
            password_hash=password_hash,
        )
        return data if isinstance(data, list) else []

    async def get_devices(self, site_id: int, *, login_account: str, password_hash: str) -> list[dict[str, Any]]:
        data = await self._post_with_retry(
            GET_DEVICES_ENDPOINT,
            {"id": site_id, "seriesGroup": None},
            login_account=login_account,
            password_hash=password_hash,
        )
        return data if isinstance(data, list) else []

    async def get_collector_sn_and_product_type(
        self, device_id: int, *, login_account: str, password_hash: str
    ) -> tuple[str, int]:
        data = await self.get_energy_storage_info(
            device_id, login_account=login_account, password_hash=password_hash
        )
        if not isinstance(data, dict):
            raise LivoltekApiError("energyStorageInfo returned no data dict")
        # collectorSn may be null — wifiSn is the documented fallback for point/info deviceId.
        raw_sn = data.get("collectorSn") or data.get("wifiSn")
        if raw_sn in (None, "", "null"):
            raise LivoltekApiError(
                "energyStorageInfo missing collectorSn and wifiSn for point/info"
            )
        collector_sn = str(raw_sn).strip()
        if not collector_sn:
            raise LivoltekApiError(
                "energyStorageInfo missing collectorSn and wifiSn for point/info"
            )
        template = data.get("template")
        if template in (None, "", "null"):
            product_type = DEFAULT_PRODUCT_TYPE
        else:
            try:
                product_type = int(template)
            except (TypeError, ValueError) as err:
                raise LivoltekApiError("energyStorageInfo template is not an int") from err
        return collector_sn, product_type

    # ------------------------------------------------------------------
    # Data methods
    # ------------------------------------------------------------------

    async def get_energy_storage_info(
        self, device_id: int, *, login_account: str, password_hash: str
    ) -> dict[str, Any]:
        data = await self._post_with_retry(
            ENERGY_STORAGE_INFO_ENDPOINT,
            {},
            params={"id": device_id, "isUseChangeUnit": "true"},
            login_account=login_account,
            password_hash=password_hash,
        )
        return data if isinstance(data, dict) else {}

    async def get_signal_device_status(
        self, device_id: int, *, login_account: str, password_hash: str
    ) -> dict[str, Any]:
        data = await self._post_with_retry(
            SIGNAL_DEVICE_STATUS_ENDPOINT,
            {},
            params={"id": device_id, "isUseChangeUnit": "true"},
            login_account=login_account,
            password_hash=password_hash,
        )
        return data if isinstance(data, dict) else {}

    async def get_query_power_flow(
        self, site_id: int, *, login_account: str, password_hash: str
    ) -> dict[str, Any]:
        endpoint = QUERY_POWER_FLOW_ENDPOINT.format(site_id=site_id)
        data = await self._post_with_retry(
            endpoint, {}, login_account=login_account, password_hash=password_hash
        )
        return data if isinstance(data, dict) else {}

    async def get_power_flow_fallback(
        self, site_id: int, *, login_account: str, password_hash: str
    ) -> dict[str, Any]:
        endpoint = POWER_FLOW_FALLBACK_ENDPOINT.format(site_id=site_id)
        data = await self._post_with_retry(
            endpoint, {}, login_account=login_account, password_hash=password_hash
        )
        return data if isinstance(data, dict) else {}

    async def get_alarms(
        self,
        site_id: int,
        *,
        login_account: str,
        password_hash: str,
        days: int = 1,
        page_size: int = 5,
    ) -> list[dict[str, Any]]:
        # station filtering is not required (empty = all stations for account),
        # but we keep site_id in the signature for future improvements.
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)

        def _iso(dt: datetime) -> str:
            return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        body = {
            "powerStationFilter": [],
            "filterTime": [_iso(start), _iso(end)],
            "pageSize": page_size,
            "start": 1,
            "fuzzyQueryId": True,
            "showDescribe": True,
        }
        data = await self._post_with_retry(
            ALARM_FILTER_ENDPOINT, body, login_account=login_account, password_hash=password_hash
        )
        return data if isinstance(data, list) else []

    async def get_alarms_full_log(
        self, site_id: int, *, login_account: str, password_hash: str
    ) -> list[dict[str, Any]]:
        return await self.get_alarms(
            site_id,
            login_account=login_account,
            password_hash=password_hash,
            days=30,
            page_size=100,
        )

    async def get_point_info(
        self,
        device_id: int,
        collector_sn: str,
        product_type: int,
        *,
        login_account: str,
        password_hash: str,
    ) -> dict[str, Any]:
        body = {"deviceId": collector_sn, "id": device_id, "productType": product_type}
        data = await self._post_with_retry(
            POINT_INFO_ENDPOINT, body, login_account=login_account, password_hash=password_hash
        )
        return data if isinstance(data, dict) else {}
