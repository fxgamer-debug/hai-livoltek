"""DataUpdateCoordinators for the Livoltek integration.

Three coordinators run with different cadences:

* :class:`LivoltekFastCoordinator`   – live telemetry (60s)
* :class:`LivoltekMediumCoordinator` – status + alarms (5min)
* :class:`LivoltekWeeklyCoordinator` – inverter settings (weekly, on-demand)
"""
from __future__ import annotations

import asyncio
import random
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
    CONF_API_KEY,
    CONF_DEVICE_ID,
    CONF_INVERTER_SN,
    CONF_SECUID,
    CONF_SITE_ID,
    CONF_USER_TOKEN,
    DOMAIN,
    LOGGER,
    PV_DELTA_WARNING_THRESHOLD,
    SCAN_INTERVAL_FAST,
    SCAN_INTERVAL_MEDIUM,
    SCAN_INTERVAL_WEEKLY,
    STARTUP_JITTER_MAX,
)


_PV_NOTIFICATION_KEY = f"{DOMAIN}_pv_delta_warning"


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
    def secuid(self) -> str:
        return self.entry.data[CONF_SECUID]

    @property
    def api_key(self) -> str:
        return self.entry.data[CONF_API_KEY]

    @property
    def site_id(self) -> str:
        return self.entry.data[CONF_SITE_ID]

    @property
    def device_id(self) -> int:
        return int(self.entry.data[CONF_DEVICE_ID])

    @property
    def user_token(self) -> str:
        return self.entry.data[CONF_USER_TOKEN]

    @property
    def inverter_sn(self) -> str | None:
        """Inverter serial number resolved during config flow.

        May be ``None`` for very old config entries created before the SN
        was extracted from get_devices; callers should tolerate that.
        """
        return self.entry.data.get(CONF_INVERTER_SN)

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
            await self.api.ensure_token(self.secuid, self.api_key)
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
        self._last_pv_warning_date: str | None = None
        self.medium_coordinator: LivoltekMediumCoordinator | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        await self._ensure_token()

        try:
            data = await self.api.get_energy_storage_info(self.device_id)
        except LivoltekAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (LivoltekConnectionError, LivoltekApiError) as err:
            self._record_failure()
            # Try public-API fallback so the user still sees *something*.
            try:
                fallback = await self.api.get_current_power_flow_fallback(
                    self.site_id, self.user_token
                )
            except Exception as fb_err:  # noqa: BLE001
                raise UpdateFailed(
                    f"Primary fetch failed ({err}); fallback also failed ({fb_err})"
                ) from err
            self._using_fallback = True
            LOGGER.debug("Livoltek fast coordinator using public fallback: %s", fallback)
            return self._normalise_fallback(fallback)

        self._using_fallback = False
        self._record_success()
        self._maybe_warn_pv_delta(data)
        return data

    # ------------------------------------------------------------------

    def _normalise_fallback(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Map the public fallback response into the same shape the sensors expect."""
        return {
            "pvPower": payload.get("pvPower"),
            "girdPower": payload.get("powerGridPower"),
            "loadActivePower": payload.get("loadPower"),
            "batteryActivePower": payload.get("energyPower"),
            "batteryRestSoc": payload.get("energySoc"),
            "_fallback": True,
        }

    def _maybe_warn_pv_delta(self, fast_data: dict[str, Any]) -> None:
        """Compare PV today value against medium coordinator total.

        Fires a persistent notification at most once per day if the live total
        diverges from the daily-aggregate total by more than the configured
        threshold — usually a sign one of the two endpoints has stale data.
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
        today = datetime.now(timezone.utc).date().isoformat()
        if self._last_pv_warning_date == today:
            return
        self._last_pv_warning_date = today
        try:
            from homeassistant.components import persistent_notification

            persistent_notification.async_create(
                self.hass,
                (
                    f"Livoltek PV totals diverge by {delta:.0%}: "
                    f"live={live:.2f} kWh vs aggregate={agg:.2f} kWh. "
                    "One of the endpoints may be stale."
                ),
                title="Livoltek PV mismatch",
                notification_id=_PV_NOTIFICATION_KEY,
            )
        except Exception as err:  # noqa: BLE001
            LOGGER.debug("Could not raise PV-delta notification: %s", err)

    @property
    def using_fallback(self) -> bool:
        return self._using_fallback


class LivoltekMediumCoordinator(_LivoltekBaseCoordinator):
    """Polls signal status, power-flow and alarms every 5 minutes."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api_client: LivoltekApiClient) -> None:
        super().__init__(
            hass,
            entry,
            api_client,
            name=f"{DOMAIN}_medium",
            scan_interval=SCAN_INTERVAL_MEDIUM,
        )
        # 30-day rolling alarm log, keyed by alarm level (1..4).
        self.alarm_log: dict[int, list[dict[str, Any]]] = {1: [], 2: [], 3: [], 4: []}

    async def _async_update_data(self) -> dict[str, Any]:
        await self._ensure_token()

        # The three private-API endpoints are independent. Run them with
        # ``return_exceptions=True`` so a single broken endpoint (which the
        # Livoltek backend produces somewhat liberally — see the alarm
        # endpoint returning HTTP 500 ``service_error``) does not blank
        # out the other two. Sensors backed by failing endpoints will
        # naturally surface as ``unavailable``.
        results = await asyncio.gather(
            self.api.get_signal_device_status(self.device_id),
            self.api.get_query_power_flow(self.site_id),
            self.api.get_alarms(
                self.site_id, days=7, page_size=5, sn=self.inverter_sn
            ),
            return_exceptions=True,
        )
        signal_res, flow_res, alarms_res = results

        # Auth errors are still terminal — let HA flip the entry into
        # reauth mode rather than silently spinning on stale tokens.
        for res in results:
            if isinstance(res, LivoltekAuthError):
                raise ConfigEntryAuthFailed(str(res))

        def _value_or_log(result: Any, label: str, default: Any) -> Any:
            if isinstance(result, Exception):
                LOGGER.warning(
                    "Livoltek %s fetch failed: %s: %s",
                    label, type(result).__name__, result,
                )
                return default
            return result

        signal = _value_or_log(signal_res, "signal status", None)
        power_flow = _value_or_log(flow_res, "power flow", None)
        alarms = _value_or_log(alarms_res, "alarms", None)

        # Treat an all-failure refresh as an UpdateFailed so HA backs
        # off the polling cadence; partial success is recorded as success
        # so the at-least-some-data sensors stay live.
        if signal is None and power_flow is None and alarms is None:
            self._record_failure()
            raise UpdateFailed(
                "All medium-coordinator endpoints failed; see warnings above"
            )

        self._record_success()
        self._merge_alarms(alarms or [])

        return {
            "signal": signal or {},
            "power_flow": power_flow or {},
            "alarms": alarms or [],
        }

    # ------------------------------------------------------------------

    def _merge_alarms(self, new_alarms: list[dict[str, Any]]) -> None:
        """Merge new alarms into the rolling 30-day log, deduping by code+time."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=ALARM_LOG_DAYS)
        cutoff_ts = cutoff.timestamp() * 1000  # actingTime is ms since epoch

        for alarm in new_alarms:
            level = int(alarm.get("level") or 1)
            bucket = self.alarm_log.setdefault(level, [])
            key = (alarm.get("actingTime"), alarm.get("alarmCode"))
            if any(
                (a.get("actingTime"), a.get("alarmCode")) == key for a in bucket
            ):
                continue
            bucket.append(alarm)

        for level, bucket in self.alarm_log.items():
            pruned = [a for a in bucket if (a.get("actingTime") or 0) >= cutoff_ts]
            pruned.sort(key=lambda a: a.get("actingTime") or 0)
            self.alarm_log[level] = pruned

    async def async_full_alarm_refresh(self) -> list[dict[str, Any]]:
        """Fetch a full 30-day alarm history (used by diagnostics)."""
        await self._ensure_token()
        alarms = await self.api.get_alarms(
            self.site_id,
            days=ALARM_LOG_DAYS,
            page_size=100,
            sn=self.inverter_sn,
        )
        self._merge_alarms(alarms or [])
        return alarms


class LivoltekWeeklyCoordinator(_LivoltekBaseCoordinator):
    """Polls inverter settings (point info) once per week."""

    # Only these keys from the ~148-field point/info response are exposed.
    SETTINGS_KEYS = (
        "workModel",
        "dischargeEndSOC",
        "dischargeEndSOCEps",
        "chargingCurrent",
        "dischargingCurrent",
        "BMSSOH",
        "WarningSoc",
        "gridFeedPowerLimit",
    )

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
            data = await self.api.get_point_info(self.device_id)
        except LivoltekAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (LivoltekConnectionError, LivoltekApiError) as err:
            self._record_failure()
            raise UpdateFailed(str(err)) from err

        self._record_success()
        return {key: data.get(key) for key in self.SETTINGS_KEYS}
