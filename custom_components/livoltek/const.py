"""Constants for the Livoltek integration."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp
from homeassistant.const import Platform

LOGGER = logging.getLogger(__package__)

DOMAIN = "livoltek"
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]
ATTRIBUTION = "Data provided by Livoltek"

# Regions — the Livoltek backend is sharded by region and each account lives
# on exactly one shard. Hitting the wrong shard returns ``data == "user not
# exit"`` (sic) inside an otherwise-successful envelope, so users must pick
# the right one at setup time.
REGION_EU = "eu"
REGION_GLOBAL = "global"
DEFAULT_REGION = REGION_EU

PUBLIC_API_BASES: dict[str, str] = {
    REGION_EU:     "https://api-eu.livoltek-portal.com:8081",
    REGION_GLOBAL: "https://api.livoltek-portal.com:8081",
}

# Private telemetry API. No region split is documented for it yet; both
# regions are mapped to the same host. If a Global user reports telemetry
# failures we'll need to split this map.
PRIVATE_API_BASES: dict[str, str] = {
    REGION_EU:     "https://evs.livoltek-portal.com",
    REGION_GLOBAL: "https://evs.livoltek-portal.com",
}

LOGIN_ENDPOINT = "/hess/api/login"
SITES_ENDPOINT = "/hess/api/userSites/list"
DEVICES_ENDPOINT = "/hess/api/device/{site_id}/list"

ENERGY_STORAGE_INFO_ENDPOINT = "/ctrller-manager/energystorage/energyStorageInfo"
SIGNAL_DEVICE_STATUS_ENDPOINT = "/ctrller-manager/energystorage/signalDeviceStatus"
QUERY_POWER_FLOW_ENDPOINT = "/ctrller-manager/powerstation/queryPowerFlow/{site_id}"
POINT_INFO_ENDPOINT = "/hess-ota/device/operation/point/info"

# NOTE: ``/ctrller-manager/alarm/findAllFilter`` is intentionally not
# wired up. It requires a portal-session JWT (the kind obtained by
# logging into the portal in a browser) and rejects the public-API
# access token with msgCode ``token.expiried`` regardless of freshness.
# See README "No alarm sensors" callout and AGENTS.md §2 for the full
# investigation. Don't re-add a get_alarms() call without a new auth
# strategy.

# Fallback public endpoint (used when fast coordinator fails)
CURRENT_POWER_FLOW_ENDPOINT = "/hess/api/site/{site_id}/curPowerflow"

# Config entry keys
CONF_SECUID = "secuid"
CONF_API_KEY = "api_key"
CONF_USER_TOKEN = "user_token"
CONF_SITE_ID = "site_id"
CONF_DEVICE_ID = "device_id"
CONF_SITE_NAME = "site_name"
CONF_ACCESS_TOKEN = "access_token"
CONF_TOKEN_EXPIRY = "token_expiry"
CONF_INVERTER_SN = "inverter_sn"
CONF_REGION = "region"

# Coordinator names
COORDINATOR_FAST = "fast"
COORDINATOR_MEDIUM = "medium"
COORDINATOR_WEEKLY = "weekly"

# Poll intervals
SCAN_INTERVAL_FAST = timedelta(seconds=60)
SCAN_INTERVAL_MEDIUM = timedelta(minutes=5)
SCAN_INTERVAL_WEEKLY = timedelta(weeks=1)
STARTUP_JITTER_MAX = 30  # seconds

# Reliability
BACKOFF_INTERVALS = [60, 120, 300, 600]  # seconds: 1min, 2min, 5min, 10min
TOKEN_REFRESH_BUFFER = timedelta(minutes=30)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)

# Delta check threshold
PV_DELTA_WARNING_THRESHOLD = 0.10  # 10%
