"""Config flow for Livoltek."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow

# ``ConfigFlowResult`` was moved into ``homeassistant.config_entries`` in
# HA 2024.4. On older versions it lived in ``homeassistant.data_entry_flow``
# as ``FlowResult``. A plain ``from ... import ConfigFlowResult`` therefore
# breaks module loading on older HA, which the frontend surfaces as a
# "config flow could not be loaded: 500 internal server error" dialog.
try:
    from homeassistant.config_entries import ConfigFlowResult  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover — exercised on HA < 2024.4
    from homeassistant.data_entry_flow import FlowResult as ConfigFlowResult  # type: ignore[assignment]

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    LivoltekApiClient,
    LivoltekApiError,
    LivoltekAuthError,
    LivoltekConnectionError,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_API_KEY,
    CONF_DEVICE_ID,
    CONF_INVERTER_SN,
    CONF_REGION,
    CONF_SECUID,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    CONF_TOKEN_EXPIRY,
    CONF_USER_TOKEN,
    DEFAULT_REGION,
    DOMAIN,
    LOGGER,
    REGION_EU,
    REGION_GLOBAL,
)


# Order matters here — the dict's first item is what the dropdown defaults
# to when the user opens the form for the first time.
_REGION_CHOICES: dict[str, str] = {
    REGION_EU: "Europe (api-eu.livoltek-portal.com)",
    REGION_GLOBAL: "Global (api.livoltek-portal.com)",
}


_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_REGION, default=DEFAULT_REGION): vol.In(_REGION_CHOICES),
        vol.Required(CONF_SECUID): str,
        vol.Required(CONF_API_KEY): str,
        vol.Required(CONF_USER_TOKEN): str,
    }
)


def _extract_list(response: Any, *, label: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Coerce a raw API response into a list of items, or return an error code.

    The Livoltek public API is not strictly documented and we've observed the
    ``data`` field arrive in three shapes for both ``userSites/list`` and
    ``device/{id}/list`` endpoints:

    * a dict ``{"list": [...], "total": N, ...}`` — the documented shape;
    * a bare list ``[...]`` — sometimes returned when there are 0 or 1 items;
    * ``None`` / missing / a non-list ``list`` field — observed when the
      backend has nothing to return but still answers ``msgCode=operate.success``.

    Returns ``(items, None)`` on success or ``(None, error_code)`` where
    ``error_code`` is one of the keys defined in ``strings.json`` under
    ``config.error``.
    """
    if response is None:
        LOGGER.warning("Livoltek %s: response is None", label)
        return None, "cannot_connect"

    if isinstance(response, list):
        items = response
    elif isinstance(response, dict):
        raw_list = response.get("list")
        if raw_list is None:
            LOGGER.warning(
                "Livoltek %s: response dict has no 'list' key (keys=%s)",
                label,
                sorted(response.keys()),
            )
            return None, "cannot_connect"
        if not isinstance(raw_list, list):
            LOGGER.warning(
                "Livoltek %s: 'list' field is not a list (got %s)",
                label,
                type(raw_list).__name__,
            )
            return None, "cannot_connect"
        items = raw_list
    else:
        LOGGER.warning(
            "Livoltek %s: unexpected response type %s (%r)",
            label,
            type(response).__name__,
            response,
        )
        return None, "cannot_connect"

    if not items:
        LOGGER.warning("Livoltek %s: list is empty", label)
        return None, "no_sites_found"

    cleaned = [item for item in items if isinstance(item, dict)]
    if not cleaned:
        LOGGER.warning(
            "Livoltek %s: list contains no dict items (got types=%s)",
            label,
            [type(i).__name__ for i in items],
        )
        return None, "cannot_connect"

    return cleaned, None


# The Livoltek API uses inconsistent field names: sites are returned with
# ``powerStationID`` / ``powerStationName`` (note: trailing ``ID``, not
# ``Id``), while in older docs we've seen ``id`` / ``siteName``. Devices
# appear to use yet another scheme. _first_value tries a list of candidates
# in order and returns the first non-empty value, so we degrade gracefully
# whenever the backend renames a field.
def _first_value(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first non-empty value from ``d`` for any of the given keys."""
    for key in keys:
        value = d.get(key)
        if value not in (None, "", 0):
            return value
    return default


# Field-name candidates observed (or plausibly named) for site dicts.
_SITE_ID_KEYS: tuple[str, ...] = (
    "powerStationID", "powerStationId", "siteId", "id",
)
_SITE_NAME_KEYS: tuple[str, ...] = (
    "powerStationName", "siteName", "name",
)
# Candidates for device dicts — extend as we learn more from real responses.
_DEVICE_ID_KEYS: tuple[str, ...] = (
    "id", "deviceId", "deviceID", "inverterId", "inverterID",
)
_DEVICE_SN_KEYS: tuple[str, ...] = (
    "sn", "deviceSn", "deviceSN", "serialNumber", "serialNo", "inverterSn", "inverterSN",
)


class LivoltekConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Livoltek."""

    VERSION = 1

    def __init__(self) -> None:
        self._region: str = DEFAULT_REGION
        self._secuid: str | None = None
        self._api_key: str | None = None
        self._user_token: str | None = None
        self._access_token: str | None = None
        self._token_expiry: int | None = None
        self._sites: list[dict[str, Any]] = []
        # NB: HA's ConfigFlow defines ``_reauth_entry_id`` as a read-only
        # property (computed from ``self.context``). We keep our own copy
        # under a different name so we don't collide with it.
        self._pending_reauth_entry_id: str | None = None

    # ------------------------------------------------------------------
    # Step: user (initial entry of credentials)
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial credentials step."""
        if self._pending_reauth_entry_id is None:
            self._async_abort_entries_match({})  # single_config_entry safety

        errors: dict[str, str] = {}
        if user_input is not None:
            self._region = user_input.get(CONF_REGION, DEFAULT_REGION)
            self._secuid = user_input[CONF_SECUID].strip()
            self._api_key = user_input[CONF_API_KEY]
            self._user_token = user_input[CONF_USER_TOKEN].strip()

            session = async_get_clientsession(self.hass)
            api = LivoltekApiClient(
                session=session,
                region=self._region,
                user_token=self._user_token,
                secuid=self._secuid,
                api_key=self._api_key,
            )

            try:
                await api.login(self._secuid, self._api_key)
            except LivoltekAuthError as err:
                # Auth-failure logs include the inner ``msgCode`` / message
                # so users can tell wrong-key from wrong-region at a glance.
                LOGGER.warning(
                    "Livoltek login rejected by server (region=%s): %s",
                    self._region, err,
                )
                errors["base"] = "invalid_auth"
            except LivoltekConnectionError as err:
                # Surface at WARNING (not DEBUG) so the underlying transport
                # exception — TLS error, DNS failure, container DNS, proxy,
                # IPv6 routing, etc. — is visible in the default HA log
                # without the user having to enable debug logging.
                LOGGER.warning(
                    "Livoltek login transport failed (region=%s): %s: %s",
                    self._region, type(err.__cause__).__name__ if err.__cause__ else "-", err,
                )
                errors["base"] = "cannot_connect"
            except Exception as err:  # noqa: BLE001
                LOGGER.exception("Unexpected error during login: %s", err)
                errors["base"] = "unknown"
            else:
                self._access_token = api.access_token
                self._token_expiry = api.token_expiry

                try:
                    sites_response = await api.get_sites()
                except (LivoltekConnectionError, LivoltekApiError) as err:
                    LOGGER.warning(
                        "Livoltek get_sites failed (region=%s): %s: %s",
                        self._region, type(err.__cause__).__name__ if err.__cause__ else "-", err,
                    )
                    errors["base"] = "cannot_connect"
                except LivoltekAuthError as err:
                    LOGGER.warning(
                        "Livoltek get_sites auth failed (region=%s): %s",
                        self._region, err,
                    )
                    errors["base"] = "invalid_auth"
                except Exception as err:  # noqa: BLE001
                    LOGGER.exception("Unexpected error during get_sites: %s", err)
                    errors["base"] = "unknown"
                else:
                    sites, err_code = _extract_list(sites_response, label="get_sites")
                    if err_code is not None:
                        errors["base"] = err_code
                    else:
                        assert sites is not None  # for type checkers
                        self._sites = sites
                        if len(sites) == 1:
                            return await self._finalise(api, sites[0])
                        return await self.async_step_select_site()

        return self.async_show_form(
            step_id="user",
            data_schema=_USER_SCHEMA,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step: select_site (only when multiple sites exist)
    # ------------------------------------------------------------------

    async def async_step_select_site(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose which site to add."""
        def _site_id(site: dict[str, Any]) -> str:
            return str(_first_value(site, *_SITE_ID_KEYS, default=""))

        site_choices = {
            _site_id(site): (
                _first_value(site, *_SITE_NAME_KEYS, default=_site_id(site))
            )
            for site in self._sites
        }

        if user_input is not None:
            chosen_id = user_input[CONF_SITE_ID]
            site = next(
                (s for s in self._sites if _site_id(s) == chosen_id),
                None,
            )
            if site is None:
                return self.async_abort(reason="unknown")

            session = async_get_clientsession(self.hass)
            api = LivoltekApiClient(
                session=session,
                region=self._region,
                access_token=self._access_token,
                token_expiry=self._token_expiry,
                user_token=self._user_token,
                secuid=self._secuid,
                api_key=self._api_key,
            )
            return await self._finalise(api, site)

        return self.async_show_form(
            step_id="select_site",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SITE_ID): vol.In(site_choices),
                }
            ),
        )

    # ------------------------------------------------------------------
    # Re-auth flow
    # ------------------------------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Handle a re-authentication request from HA core."""
        self._pending_reauth_entry_id = self.context.get("entry_id")
        # Pre-populate values so the user only needs to update what changed.
        # Existing entries created before the region selector existed default
        # to EU, which matches the integration's prior hardcoded behaviour.
        self._region = entry_data.get(CONF_REGION) or DEFAULT_REGION
        self._secuid = entry_data.get(CONF_SECUID)
        self._user_token = entry_data.get(CONF_USER_TOKEN)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the re-auth form (same fields as initial setup)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            region = user_input.get(CONF_REGION, self._region)
            secuid = user_input[CONF_SECUID].strip()
            api_key = user_input[CONF_API_KEY]
            user_token = user_input[CONF_USER_TOKEN].strip()

            session = async_get_clientsession(self.hass)
            api = LivoltekApiClient(
                session=session,
                region=region,
                user_token=user_token,
                secuid=secuid,
                api_key=api_key,
            )
            try:
                await api.login(secuid, api_key)
            except LivoltekAuthError:
                errors["base"] = "invalid_auth"
            except LivoltekConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                entry = self.hass.config_entries.async_get_entry(
                    self._pending_reauth_entry_id or ""
                )
                if entry is None:
                    return self.async_abort(reason="unknown")
                new_data = dict(entry.data)
                new_data.update(
                    {
                        CONF_REGION: region,
                        CONF_SECUID: secuid,
                        CONF_API_KEY: api_key,
                        CONF_USER_TOKEN: user_token,
                        CONF_ACCESS_TOKEN: api.access_token,
                        CONF_TOKEN_EXPIRY: api.token_expiry,
                    }
                )
                self.hass.config_entries.async_update_entry(entry, data=new_data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REGION, default=self._region): vol.In(_REGION_CHOICES),
                    vol.Required(CONF_SECUID, default=self._secuid or ""): str,
                    vol.Required(CONF_API_KEY): str,
                    vol.Required(CONF_USER_TOKEN, default=self._user_token or ""): str,
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _finalise(
        self, api: LivoltekApiClient, site: dict[str, Any]
    ) -> ConfigFlowResult:
        """Resolve the device for the chosen site and create the entry."""
        site_id_raw = _first_value(site, *_SITE_ID_KEYS)
        if site_id_raw in (None, ""):
            # Log only the keys (not values) — site dicts contain personal
            # information like address, owner name, GPS coords, etc. that
            # should not end up in the HA log even at WARNING level.
            LOGGER.warning(
                "Selected site has no recognised id field. Tried %s. Got keys: %s",
                list(_SITE_ID_KEYS), sorted(site.keys()),
            )
            return self.async_abort(reason="cannot_connect")
        site_id = str(site_id_raw)
        site_name = _first_value(site, *_SITE_NAME_KEYS, default=f"Livoltek {site_id}")

        try:
            devices_response = await api.get_devices(site_id)
        except LivoltekAuthError as err:
            LOGGER.warning("Livoltek get_devices auth failed: %s", err)
            return self.async_show_form(
                step_id="user",
                data_schema=_USER_SCHEMA,
                errors={"base": "invalid_auth"},
            )
        except (LivoltekConnectionError, LivoltekApiError) as err:
            LOGGER.warning(
                "Livoltek get_devices failed: %s: %s",
                type(err.__cause__).__name__ if err.__cause__ else "-", err,
            )
            return self.async_show_form(
                step_id="user",
                data_schema=_USER_SCHEMA,
                errors={"base": "cannot_connect"},
            )
        except Exception as err:  # noqa: BLE001
            LOGGER.exception("Unexpected error during get_devices: %s", err)
            return self.async_show_form(
                step_id="user",
                data_schema=_USER_SCHEMA,
                errors={"base": "unknown"},
            )

        devices, err_code = _extract_list(devices_response, label="get_devices")
        if err_code is not None:
            # "no_sites_found" here really means "no devices for this site" —
            # use a dedicated reason if it's available, otherwise fall back to
            # the generic key. Either way, abort the flow because the user
            # can't fix this from the credentials form.
            reason = "no_devices_found" if err_code == "no_sites_found" else err_code
            return self.async_abort(reason=reason)

        assert devices is not None  # for type checkers
        device = devices[0]
        device_id_raw = _first_value(device, *_DEVICE_ID_KEYS)
        if device_id_raw in (None, "", 0):
            # As with sites, log only the keys to avoid exposing serial
            # numbers / firmware versions in the log.
            LOGGER.warning(
                "First device has no recognised id field. Tried %s. Got keys: %s",
                list(_DEVICE_ID_KEYS), sorted(device.keys()),
            )
            return self.async_abort(reason="cannot_connect")
        try:
            device_id = int(device_id_raw)
        except (TypeError, ValueError):
            LOGGER.warning("Device id is not numeric: %r", device_id_raw)
            return self.async_abort(reason="cannot_connect")
        inverter_sn = _first_value(device, *_DEVICE_SN_KEYS)

        await self.async_set_unique_id(f"{site_id}:{device_id}")
        self._abort_if_unique_id_configured()

        data = {
            CONF_REGION: self._region,
            CONF_SECUID: self._secuid,
            CONF_API_KEY: self._api_key,
            CONF_USER_TOKEN: self._user_token,
            CONF_SITE_ID: site_id,
            CONF_DEVICE_ID: device_id,
            CONF_SITE_NAME: site_name,
            CONF_ACCESS_TOKEN: api.access_token,
            CONF_TOKEN_EXPIRY: api.token_expiry,
            CONF_INVERTER_SN: inverter_sn,
        }
        return self.async_create_entry(title=site_name, data=data)
