"""DataUpdateCoordinators for the Livoltek integration.

Three coordinators run with different cadences:

* :class:`LivoltekFastCoordinator`   – live telemetry (60s)
* :class:`LivoltekMediumCoordinator` – signal status + power flow (5min)
* :class:`LivoltekWeeklyCoordinator` – inverter settings (weekly, on-demand)
"""
from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    LivoltekApiClient,
    LivoltekApiError,
    LivoltekAuthError,
    LivoltekConnectionError,
)
from .const import (
    ALARM_LOG_DAYS,
    BACKOFF_INTERVALS,
    CONF_COLLECTOR_SN,
    CONF_DEVICE_ID,
    CONF_INVERTER_SN,
    CONF_LOGIN_ACCOUNT,
    CONF_PASSWORD_HASH,
    CONF_PRODUCT_TYPE,
    CONF_SITE_ID,
    DOMAIN,
    LOGGER,
    POINT_INFO_KEYS,
    PV_DELTA_WARNING_THRESHOLD,
    SCAN_INTERVAL_FAST,
    SCAN_INTERVAL_MEDIUM,
    SCAN_INTERVAL_WEEKLY,
    STARTUP_JITTER_MAX,
)


_PV_NOTIFICATION_ID = "livoltek_pv_mismatch"


class _LivoltekBaseCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Shared helpers for all Livoltek coordinators."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api_client: LivoltekApiClient,
        *,
        name: str,
        scan_interval: timedelta,
    ) -> None:
        # A tiny random jitter prevents three coordinators from hammering the
        # API in lock-step right after startup.
        jitter = timedelta(seconds=random.uniform(0, STARTUP_JITTER_MAX))
        super().__init__(
            hass,
            LOGGER,
            name=name,
            update_interval=scan_interval + jitter,
            always_update=False,
        )
        self.api = api_client
        self.entry = entry
        self._base_interval = scan_interval
        self._consecutive_failures = 0

    @property
    def login_account(self) -> str:
        return self.entry.data[CONF_LOGIN_ACCOUNT]

    @property
    def password_hash(self) -> str:
        return self.entry.data[CONF_PASSWORD_HASH]

    @property
    def site_id(self) -> int:
        return int(self.entry.data[CONF_SITE_ID])

    @property
    def device_id(self) -> int:
        return int(self.entry.data[CONF_DEVICE_ID])

    @property
    def collector_sn(self) -> str:
        return self.entry.data[CONF_COLLECTOR_SN]

    @property
    def product_type(self) -> int:
        return int(self.entry.data[CONF_PRODUCT_TYPE])

    def _backoff_seconds(self) -> int:
        idx = min(self._consecutive_failures - 1, len(BACKOFF_INTERVALS) - 1)
        return BACKOFF_INTERVALS[max(idx, 0)]

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        backoff = self._backoff_seconds()
        self.update_interval = timedelta(seconds=backoff)
        LOGGER.warning(
            "Livoltek %s update failed (%s in a row), backing off to %ss",
            self.name,
            self._consecutive_failures,
            backoff,
        )

    def _record_success(self) -> None:
        if self._consecutive_failures:
            LOGGER.info(
                "Livoltek %s recovered after %s failure(s)",
                self.name,
                self._consecutive_failures,
            )
        self._consecutive_failures = 0
        self.update_interval = self._base_interval

    async def _ensure_token(self) -> None:
        try:
            await self.api.ensure_token(self.login_account, self.password_hash)
        except LivoltekAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err


class LivoltekFastCoordinator(_LivoltekBaseCoordinator):
    """Polls live energy storage info every 60 seconds."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api_client: LivoltekApiClient) -> None:
        super().__init__(
            hass,
            entry,
            api_client,
            name=f"{DOMAIN}_fast",
            scan_interval=SCAN_INTERVAL_FAST,
        )
        self._using_fallback = False
        self._last_delta_warning: datetime | None = None
        self.medium_coordinator: LivoltekMediumCoordinator | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        await self._ensure_token()

        try:
            data = await self.api.get_energy_storage_info(
                self.device_id,
                login_account=self.login_account,
                password_hash=self.password_hash,
            )
        except LivoltekAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (LivoltekConnectionError, LivoltekApiError) as err:
            self._record_failure()
            # Try a secondary endpoint so the user still sees *something*.
            try:
                fallback = await self.api.get_power_flow_fallback(
                    self.site_id,
                    login_account=self.login_account,
                    password_hash=self.password_hash,
                )
            except Exception as fb_err:  # noqa: BLE001
                raise UpdateFailed(
                    f"Primary fetch failed ({err}); fallback also failed ({fb_err})"
                ) from err
            self._using_fallback = True
            LOGGER.debug("Livoltek fast coordinator using fallback: %s", fallback)
            return self._normalise_fallback(fallback)

        self._using_fallback = False
        self._record_success()
        data = self._merge_pcs_status_from_medium(data)
        self._maybe_warn_pv_delta(data)
        return data

    # ------------------------------------------------------------------

    def _merge_pcs_status_from_medium(self, data: dict[str, Any]) -> dict[str, Any]:
        """Expose pcsStatus on fast data for binary_sensor.online (spec: fast coordinator).

        energyStorageInfo may omit pcsStatus; signalDeviceStatus always has it.
        """
        if data.get("pcsStatus") not in (None, ""):
            return data
        if not self.medium_coordinator or not self.medium_coordinator.data:
            return data
        pcs = (self.medium_coordinator.data.get("signal") or {}).get("pcsStatus")
        if pcs is None:
            return data
        merged = dict(data)
        merged["pcsStatus"] = pcs
        return merged

    def _normalise_fallback(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Map fallback response into the same shape the sensors expect."""
        return {
            "pvPower": payload.get("pvPower") or payload.get("pvActivePower"),
            "girdPower": payload.get("gridActivePower") or payload.get("girdPower"),
            "loadActivePower": payload.get("loadPower"),
            "batteryActivePower": payload.get("batteryPower") or payload.get("batteryActivePower"),
            "batteryRestSoc": payload.get("batterySOC") or payload.get("batteryRestSoc"),
            "_fallback": True,
        }

    def _maybe_warn_pv_delta(self, fast_data: dict[str, Any]) -> None:
        """Compare PV today value against medium coordinator total.

        Fires a persistent notification at most once per 24 hours if the live
        total diverges from the daily-aggregate total by more than the
        configured threshold — usually a sign one of the two endpoints has
        stale data. Uses notification_id ``livoltek_pv_mismatch``.
        """
        if not self.medium_coordinator or not self.medium_coordinator.data:
            return
        try:
            live = float(fast_data.get("pvFieldToday") or 0)
        except (TypeError, ValueError):
            return
        signal = self.medium_coordinator.data.get("signal") or {}
        try:
            agg = float(signal.get("todayPowerGeneration") or 0)
        except (TypeError, ValueError):
            return
        if live <= 0 or agg <= 0:
            return
        delta = abs(live - agg) / max(live, agg)
        if delta < PV_DELTA_WARNING_THRESHOLD:
            return
        now = datetime.now(timezone.utc)
        if self._last_delta_warning is not None:
            if (now - self._last_delta_warning) < timedelta(hours=24):
                return
        self._last_delta_warning = now
        try:
            from homeassistant.components import persistent_notification

            persistent_notification.async_create(
                self.hass,
                (
                    "Livoltek: PV generation mismatch detected. "
                    f"energyStorageInfo reports {live}kWh, "
                    f"signalDeviceStatus reports {agg}kWh today ({delta * 100:.1f}% difference). "
                    "This may indicate a sensor calibration issue."
                ),
                title="Livoltek PV Mismatch",
                notification_id=_PV_NOTIFICATION_ID,
            )
        except Exception as err:  # noqa: BLE001
            LOGGER.debug("Could not raise PV-delta notification: %s", err)

    @property
    def using_fallback(self) -> bool:
        return self._using_fallback


class LivoltekMediumCoordinator(_LivoltekBaseCoordinator):
    """Polls signal status and power-flow every 5 minutes.

    Alarm fetches share the same :data:`~const.BACKOFF_INTERVALS` table as the
    coordinator-wide backoff, but apply **only** to the alarm HTTP call: when
    ``get_alarms`` repeatedly fails, we skip that request until the next
    backoff window elapses while still polling signal and power flow on every
    cycle. Auth/session registration is unchanged (once per login refresh).
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api_client: LivoltekApiClient) -> None:
        super().__init__(
            hass,
            entry,
            api_client,
            name=f"{DOMAIN}_medium",
            scan_interval=SCAN_INTERVAL_MEDIUM,
        )
        # Tracks the last logged error signature per endpoint label so we
        # can deduplicate persistent failures (see _value_or_log).
        self._last_endpoint_error: dict[str, str] = {}
        self.alarm_log: dict[str, list[dict[str, Any]]] = {
            "Tips": [],
            "Secondary": [],
            "Important": [],
            "Urgent": [],
        }
        # Alarm-only backoff (same seconds ladder as BACKOFF_INTERVALS).
        self._alarm_consecutive_failures = 0
        self._alarm_backoff_until_monotonic = 0.0

    def _alarm_backoff_seconds(self) -> int:
        idx = min(self._alarm_consecutive_failures - 1, len(BACKOFF_INTERVALS) - 1)
        return BACKOFF_INTERVALS[max(idx, 0)]

    def _schedule_alarm_backoff(self) -> None:
        self._alarm_consecutive_failures += 1
        sec = self._alarm_backoff_seconds()
        self._alarm_backoff_until_monotonic = time.monotonic() + sec
        LOGGER.info(
            "Livoltek alarms: backing off alarm endpoint for %ss after failure #%s",
            sec,
            self._alarm_consecutive_failures,
        )

    def _clear_alarm_backoff(self) -> None:
        if self._alarm_consecutive_failures:
            LOGGER.info(
                "Livoltek alarms: alarm endpoint recovered after %s failed fetch(es)",
                self._alarm_consecutive_failures,
            )
        self._alarm_consecutive_failures = 0
        self._alarm_backoff_until_monotonic = 0.0

    async def _async_update_data(self) -> dict[str, Any]:
        await self._ensure_token()

        skip_alarms = time.monotonic() < self._alarm_backoff_until_monotonic

        sig_coro = self.api.get_signal_device_status(
            self.device_id,
            login_account=self.login_account,
            password_hash=self.password_hash,
        )
        flow_coro = self.api.get_query_power_flow(
            self.site_id,
            login_account=self.login_account,
            password_hash=self.password_hash,
        )
        alarm_coro = self.api.get_alarms(
            site_id=self.site_id,
            inverter_sn=str(self.entry.data.get(CONF_INVERTER_SN) or ""),
            login_account=self.login_account,
            password_hash=self.password_hash,
            days=7,
            page_size=50,
        )

        # Signal and flow always run. Alarms are skipped while alarm-specific
        # backoff is active so a flaky alarm endpoint is not hit every poll.
        if skip_alarms:
            signal_res, flow_res = await asyncio.gather(
                sig_coro,
                flow_coro,
                return_exceptions=True,
            )
            alarm_res = None
        else:
            signal_res, flow_res, alarm_res = await asyncio.gather(
                sig_coro,
                flow_coro,
                alarm_coro,
                return_exceptions=True,
            )

        # Auth errors are terminal — let HA flip the entry into reauth
        # mode rather than silently spinning on stale tokens.
        for res in (signal_res, flow_res):
            if isinstance(res, LivoltekAuthError):
                raise ConfigEntryAuthFailed(str(res))
        if alarm_res is not None and isinstance(alarm_res, LivoltekAuthError):
            raise ConfigEntryAuthFailed(str(alarm_res))

        def _value_or_log(result: Any, label: str, default: Any) -> Any:
            """Return ``result`` or log + return ``default`` on exception.

            Logs WARNING on first failure (or when the error message
            changes), DEBUG on consecutive identical failures, and INFO
            once on recovery. This prevents the same persistent backend
            error from spamming the log every poll cycle while still
            surfacing transient issues at WARNING level.
            """
            last = self._last_endpoint_error.get(label)
            if isinstance(result, Exception):
                signature = f"{type(result).__name__}: {result}"
                if signature != last:
                    LOGGER.warning("Livoltek %s fetch failed: %s", label, signature)
                else:
                    LOGGER.debug(
                        "Livoltek %s fetch still failing: %s", label, signature
                    )
                self._last_endpoint_error[label] = signature
                return default
            if last is not None:
                LOGGER.info("Livoltek %s fetch recovered", label)
                self._last_endpoint_error.pop(label, None)
            return result

        signal = _value_or_log(signal_res, "signal status", None)
        power_flow = _value_or_log(flow_res, "power flow", None)

        if alarm_res is None:
            prev = (self.data or {}).get("alarms") if self.data else None
            alarms = list(prev) if isinstance(prev, list) else []
        elif isinstance(alarm_res, Exception):
            alarms = _value_or_log(alarm_res, "alarms", [])
            self._schedule_alarm_backoff()
        else:
            alarms = _value_or_log(alarm_res, "alarms", [])
            self._clear_alarm_backoff()

        # If both endpoints failed, raise UpdateFailed so HA backs off
        # the polling cadence; partial success keeps the at-least-some-
        # data sensors alive.
        if signal is None and power_flow is None and not alarms:
            self._record_failure()
            raise UpdateFailed(
                "All medium-coordinator endpoints failed; see warnings above"
            )

        self._record_success()

        self._update_alarm_log(alarms or [])

        return {
            "signal": signal or {},
            "power_flow": power_flow or {},
            "alarms": alarms or [],
        }

    def _update_alarm_log(self, alarms: list[dict[str, Any]]) -> None:
        """Maintain a rolling 30-day log of alarm IDs grouped by level."""
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - (ALARM_LOG_DAYS * 24 * 60 * 60 * 1000)

        for level, items in self.alarm_log.items():
            self.alarm_log[level] = [
                a
                for a in items
                if int(a.get("actingTime") or 0) >= cutoff_ms
            ]

        seen_ids = {a.get("id") for bucket in self.alarm_log.values() for a in bucket}
        for alarm in alarms:
            if not isinstance(alarm, dict):
                continue
            aid = alarm.get("id")
            if aid in (None, "", 0) or aid in seen_ids:
                continue
            level = alarm.get("level")
            if not isinstance(level, str) or level not in self.alarm_log:
                continue
            self.alarm_log[level].append(alarm)
            seen_ids.add(aid)

        for level in self.alarm_log:
            self.alarm_log[level].sort(
                key=lambda a: int(a.get("actingTime") or 0),
                reverse=True,
            )


class LivoltekWeeklyCoordinator(_LivoltekBaseCoordinator):
    """Polls inverter settings (point info) once per week."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api_client: LivoltekApiClient) -> None:
        super().__init__(
            hass,
            entry,
            api_client,
            name=f"{DOMAIN}_weekly",
            scan_interval=SCAN_INTERVAL_WEEKLY,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        await self._ensure_token()
        try:
            data = await self.api.get_point_info(
                self.device_id,
                self.collector_sn,
                self.product_type,
                login_account=self.login_account,
                password_hash=self.password_hash,
            )
        except LivoltekAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (LivoltekConnectionError, LivoltekApiError) as err:
            self._record_failure()
            raise UpdateFailed(str(err)) from err

        self._record_success()
        return {k: data.get(k) for k in POINT_INFO_KEYS if k in data}
