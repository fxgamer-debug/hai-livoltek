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

# API endpoints
PUBLIC_API_BASE = "https://api-eu.livoltek-portal.com:8081"
PRIVATE_API_BASE = "https://evs.livoltek-portal.com"

LOGIN_ENDPOINT = "/hess/api/login"
SITES_ENDPOINT = "/hess/api/userSites/list"
DEVICES_ENDPOINT = "/hess/api/device/{site_id}/list"

ENERGY_STORAGE_INFO_ENDPOINT = "/ctrller-manager/energystorage/energyStorageInfo"
SIGNAL_DEVICE_STATUS_ENDPOINT = "/ctrller-manager/energystorage/signalDeviceStatus"
QUERY_POWER_FLOW_ENDPOINT = "/ctrller-manager/powerstation/queryPowerFlow/{site_id}"
ALARM_FILTER_ENDPOINT = "/ctrller-manager/alarm/findAllFilter"
POINT_INFO_ENDPOINT = "/hess-ota/device/operation/point/info"

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

# Alarm levels
ALARM_LEVEL_TIPS = 1
ALARM_LEVEL_SECONDARY = 2
ALARM_LEVEL_IMPORTANT = 3
ALARM_LEVEL_URGENT = 4

# Alarm log retention
ALARM_LOG_DAYS = 30

# Delta check threshold
PV_DELTA_WARNING_THRESHOLD = 0.10  # 10%
