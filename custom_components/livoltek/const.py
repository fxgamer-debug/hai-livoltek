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

# API — regional servers (user-selected)
SERVERS: dict[str, dict[str, str]] = {
    "eu_mea": {
        "label": "EU & MEA (Europe, Middle East & Africa)",
        "url": "https://evs.livoltek-portal.com",
    },
    "international": {
        "label": "International (Latin America, Australia & others)",
        "url": "https://www.livoltek-portal.com",
    },
    "asia": {
        "label": "Asia",
        "url": "https://aa.livoltek-portal.com",
    },
}

CONF_SERVER = "server"
CONF_BASE_URL = "base_url"

# Auth endpoints (May 2026 v2 API)
LOGIN_ENDPOINT = "/nbp/login/customer"
SESSION_REGISTER_ENDPOINT = "/ctrller-manager/login/login"

# Setup/discovery endpoints
GET_STATIONS_ENDPOINT = "/ctrller-manager/powerstation/getAllStationInfoToC"
GET_DEVICES_ENDPOINT = "/ctrller-manager/powerstation/inverterSelect"

# Data endpoints — fast coordinator (60s)
ENERGY_STORAGE_INFO_ENDPOINT = "/ctrller-manager/energystorage/energyStorageInfo"

# Data endpoints — medium coordinator (5min)
SIGNAL_DEVICE_STATUS_ENDPOINT = "/ctrller-manager/energystorage/signalDeviceStatus"
QUERY_POWER_FLOW_ENDPOINT = "/ctrller-manager/powerstation/queryPowerFlow/{site_id}"
ALARM_FILTER_ENDPOINT = "/ctrller-manager/alarm/findAllFilter"

# Data endpoints — weekly coordinator
POINT_INFO_ENDPOINT = "/hess-ota/device/operation/point/info"

# Fallback endpoint (if fast coordinator fails)
POWER_FLOW_FALLBACK_ENDPOINT = "/ctrller-manager/powerstation/queryPowerFlow/{site_id}"

# Config entry keys
CONF_LOGIN_ACCOUNT = "login_account"  # user's portal username
CONF_PASSWORD_HASH = "password_hash"  # MD5 hash of user's password, never plaintext
CONF_SITE_ID = "site_id"  # int — discovered via getAllStationInfoToC
CONF_DEVICE_ID = "device_id"  # int — discovered via inverterSelect
CONF_COLLECTOR_SN = "collector_sn"  # str — discovered via energyStorageInfo.collectorSn
CONF_PRODUCT_TYPE = "product_type"  # int — discovered via energyStorageInfo.template
CONF_SITE_NAME = "site_name"  # str — discovered via getAllStationInfoToC
CONF_INVERTER_SN = "inverter_sn"  # str — discovered via inverterSelect
CONF_ACCESS_TOKEN = "access_token"  # JWT — obtained at login, refreshed automatically
CONF_TOKEN_EXPIRY = "token_expiry"  # int — Unix ms timestamp from login response

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
TOKEN_REFRESH_BUFFER = timedelta(hours=24)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)

# Alarm levels — strings in v2 API response
ALARM_LEVEL_TIPS = "Tips"
ALARM_LEVEL_SECONDARY = "Secondary"
ALARM_LEVEL_IMPORTANT = "Important"
ALARM_LEVEL_URGENT = "Urgent"
ALARM_ACTIVE_LEVELS = {ALARM_LEVEL_IMPORTANT, ALARM_LEVEL_URGENT}

# Alarm log
ALARM_LOG_DAYS = 30

# Delta check threshold
PV_DELTA_WARNING_THRESHOLD = 0.10  # 10%

# point/info keys to extract
POINT_INFO_KEYS = [
    "workModel",
    "dischargeEndSOC",
    "dischargeEndSOCEps",
    "chargingCurrent",
    "dischargingCurrent",
    "BMSSOH",
    "WarningSoc",
    "gridFeedPowerLimit",
]
