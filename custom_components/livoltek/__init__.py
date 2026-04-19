"""The Livoltek integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LivoltekApiClient, LivoltekAuthError, LivoltekConnectionError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_API_KEY,
    CONF_REGION,
    CONF_SECUID,
    CONF_TOKEN_EXPIRY,
    CONF_USER_TOKEN,
    COORDINATOR_FAST,
    COORDINATOR_MEDIUM,
    COORDINATOR_WEEKLY,
    DEFAULT_REGION,
    DOMAIN,
    LOGGER,
    PLATFORMS,
)
from .coordinator import (
    LivoltekFastCoordinator,
    LivoltekMediumCoordinator,
    LivoltekWeeklyCoordinator,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Livoltek from a config entry."""
    session = async_get_clientsession(hass)

    api = LivoltekApiClient(
        session=session,
        # Existing entries created before the region selector default to EU,
        # which matches the integration's prior hardcoded behaviour.
        region=entry.data.get(CONF_REGION) or DEFAULT_REGION,
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        token_expiry=entry.data.get(CONF_TOKEN_EXPIRY),
        user_token=entry.data[CONF_USER_TOKEN],
        secuid=entry.data[CONF_SECUID],
        api_key=entry.data[CONF_API_KEY],
    )

    fast_coordinator = LivoltekFastCoordinator(hass, entry, api)
    medium_coordinator = LivoltekMediumCoordinator(hass, entry, api)
    weekly_coordinator = LivoltekWeeklyCoordinator(hass, entry, api)

    # Cross-link so the fast coordinator can access today's aggregate for the
    # PV-delta sanity check.
    fast_coordinator.medium_coordinator = medium_coordinator

    # The fast coordinator is the integration's must-have data source. If
    # it cannot get a single sample on first refresh — even via its public-
    # API fallback — there's nothing useful to surface, so we let HA back
    # off and retry. Auth failures are propagated so HA can flip the entry
    # into reauth mode.
    try:
        await fast_coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise
    except LivoltekAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except (LivoltekConnectionError, Exception) as err:  # noqa: BLE001
        raise ConfigEntryNotReady(str(err)) from err

    # The medium and weekly coordinators back individual sensor groups
    # (signal/flow and inverter settings). If their first refresh fails
    # we still want the entry to come up — the working sensors populate,
    # the failed ones show as ``unavailable``, and the user can see
    # exactly which endpoint is broken from the WARNING-level log lines
    # emitted by the coordinator. Reauth is still propagated.
    for coord, label in (
        (medium_coordinator, "medium"),
        (weekly_coordinator, "weekly"),
    ):
        try:
            await coord.async_config_entry_first_refresh()
        except ConfigEntryAuthFailed:
            raise
        except LivoltekAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001
            LOGGER.warning(
                "Livoltek %s coordinator's first refresh failed; related "
                "sensors will be unavailable until the next successful "
                "update: %s",
                label, err,
            )

    # If the access token has been refreshed during setup, persist it so we
    # don't need to log in again on the next HA restart.
    if (
        api.access_token
        and api.access_token != entry.data.get(CONF_ACCESS_TOKEN)
    ):
        new_data = dict(entry.data)
        new_data[CONF_ACCESS_TOKEN] = api.access_token
        new_data[CONF_TOKEN_EXPIRY] = api.token_expiry
        hass.config_entries.async_update_entry(entry, data=new_data)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        COORDINATOR_FAST: fast_coordinator,
        COORDINATOR_MEDIUM: medium_coordinator,
        COORDINATOR_WEEKLY: weekly_coordinator,
        "api": api,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    LOGGER.debug("Livoltek integration setup complete for entry %s", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Livoltek config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
