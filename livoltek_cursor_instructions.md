# Livoltek Home Assistant Integration — Cursor Build Instructions

## Overview

Build a custom Home Assistant integration for Livoltek hybrid solar inverters from scratch. This is a complete rewrite of the abandoned `adamlonsdale/hass-livoltek` integration. The new integration is **read-only**, uses modern HA patterns, and is built for long-term maintainability.

**Target:** Home Assistant 2024.1+ / HAOS  
**Domain:** `livoltek`  
**IoT class:** `cloud_polling`  
**HACS compatible:** Yes

---

## Critical Context — Read Before Writing Any Code

### Authentication (two tokens, not one)

The Livoltek API requires **two separate tokens** for every data request:

1. **Login token** (`access_token`): Short-lived JWT obtained by calling `POST /hess/api/login` with `secuid` + `key`. Used as `Authorization: <token>` header (NO "Bearer" prefix — this is intentional and confirmed). Expires in ~2 hours. Must be refreshed automatically.

2. **User token** (`userToken`): Long-lived JWT obtained from the Livoltek portal (My Profile → Generate Token). Valid until ~2027. Passed as a **query parameter** `?userToken=<token>` on public API calls. Also used as `Authorization: <token>` header on private API calls.

**Login payload quirk:** The `key` field must contain literal `\r\n` characters as JSON escape sequences (`\\r\\n` in the JSON string). Example:
```json
{"secuid": "abc123", "key": "somekey\\r\\n"}
```
This is confirmed working and must not be changed.

**Token refresh logic:**
- Cache the login token with its expiry timestamp
- Pre-emptively refresh when <30 minutes to expiry
- Also refresh on any 401 response
- Store `userToken` in config entry — never refresh it programmatically

### Two API backends

| Backend | Base URL | Auth | Use |
|---|---|---|---|
| Public | `https://api-eu.livoltek-portal.com:8081` | login token (header) + userToken (query param) | Setup only — site/device discovery |
| Private | `https://evs.livoltek-portal.com` | login token (header) only | All data polling |

**Important:** Port 8081 may be blocked from some server IPs. The integration runs inside HAOS on the user's home LAN where it is accessible. Do not add fallback logic for port 8081 being unreachable — if it fails during setup, show a clear error.

### HTTP client requirements

- Use `aiohttp` exclusively — no `requests`, no `urllib`
- One persistent `aiohttp.ClientSession` per config entry, created in `__init__.py` and closed on unload
- Every request must use `aiohttp.ClientTimeout(total=30, connect=10)`
- Never use `timeout=None`

---

## File Structure

```
custom_components/livoltek/
├── __init__.py
├── manifest.json
├── const.py
├── api.py
├── coordinator.py
├── config_flow.py
├── entity.py
├── sensor.py
├── binary_sensor.py
├── button.py
├── diagnostics.py
├── strings.json
└── translations/
    └── en.json
```

Root level:
```
hacs.json
AGENTS.md
README.md
```

---

## manifest.json

```json
{
  "domain": "livoltek",
  "name": "Livoltek",
  "codeowners": ["@your-github-handle"],
  "config_flow": true,
  "documentation": "https://github.com/your-repo/hass-livoltek",
  "integration_type": "device",
  "iot_class": "cloud_polling",
  "issue_tracker": "https://github.com/your-repo/hass-livoltek/issues",
  "requirements": [],
  "single_config_entry": true,
  "version": "1.0.0"
}
```

No external Python dependencies — use only HA built-ins (`aiohttp` is included in HA).

---

## const.py

Define all constants here. Nothing should be hardcoded elsewhere.

```python
DOMAIN = "livoltek"
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]
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
```

---

## api.py

This is the pure API layer. No HA imports except for logging. All methods are `async`.

### Class: `LivoltekApiClient`

Constructor takes `session: aiohttp.ClientSession`. Stores session, login token, token expiry, user token.

#### Methods to implement:

**`async def login(self, secuid: str, api_key: str) -> str`**
- POST to `PUBLIC_API_BASE + LOGIN_ENDPOINT`
- Payload: `{"secuid": secuid, "key": api_key}` — the key is stored with literal `\r\n` and must be sent as-is
- Parse response: `data.data` is the JWT token, `data.msgCode` should be `operate.success`
- Decode JWT to get expiry: `import jwt; payload = jwt.decode(token, options={"verify_signature": False}); expiry = payload["exp"]`
- Store token and expiry on self
- Raise `LivoltekAuthError` if login fails
- Return the token

**`async def ensure_token(self, secuid: str, api_key: str) -> None`**
- Check if token exists and expiry > now + 30 minutes
- If not, call `login()`

**`async def _get_headers(self) -> dict`**
- Return `{"Authorization": self._access_token, "Content-Type": "application/json", "language": "en", "timeZone": "Europe/Bucharest"}`
- Note: NO "Bearer" prefix on Authorization

**`async def _post_private(self, endpoint: str, body: dict) -> dict`**
- POST to `PRIVATE_API_BASE + endpoint`
- Uses `_get_headers()`
- On 401: refresh token, retry once
- On timeout: raise `LivoltekConnectionError`
- On `msgCode != "operate.success"`: raise `LivoltekApiError`
- Return `response["data"]`

**`async def _get_public(self, endpoint: str, params: dict = None) -> dict`**
- GET to `PUBLIC_API_BASE + endpoint`
- Adds `userToken` to params automatically
- Uses login token as Authorization header (same format, no Bearer)
- Same error handling as `_post_private`
- Return `response["data"]`

**`async def get_sites(self) -> list`**
- GET `SITES_ENDPOINT?page=1&size=10&userToken=...`
- Returns list of sites from `data.list`

**`async def get_devices(self, site_id: str) -> list`**
- GET `DEVICES_ENDPOINT` with site_id
- Returns list from `data.list`

**`async def get_energy_storage_info(self, device_id: int) -> dict`**
- POST `ENERGY_STORAGE_INFO_ENDPOINT?id={device_id}&isUseChangeUnit=true` with empty body `{}`
- Returns full data dict

**`async def get_signal_device_status(self, device_id: int) -> dict`**
- POST `SIGNAL_DEVICE_STATUS_ENDPOINT?id={device_id}&isUseChangeUnit=true` with `{}`
- Returns data dict

**`async def get_query_power_flow(self, site_id: str) -> dict`**
- POST `QUERY_POWER_FLOW_ENDPOINT` (with site_id interpolated) with `{}`
- Returns data dict

**`async def get_alarms(self, site_id: str, days: int = 30) -> list`**
- POST `ALARM_FILTER_ENDPOINT`
- Body: `{"powerStationFilter": [int(site_id)], "filterTime": [<30 days ago ISO>, <now ISO>], "pageSize": 100, "start": 1, "fuzzyQueryId": true, "showDescribe": true}`
- For medium coordinator polling: use `pageSize: 5, days: 1` (last 24h only)
- For diagnostics full log: use `pageSize: 100, days: 30`
- Returns list of alarm objects

**`async def get_point_info(self, device_id: int) -> dict`**
- POST `POINT_INFO_ENDPOINT` with `{"id": device_id}`
- Returns data dict of 148 register points

**`async def get_current_power_flow_fallback(self, site_id: str, user_token: str) -> dict`**
- Fallback method using public API
- GET `CURRENT_POWER_FLOW_ENDPOINT?userToken={user_token}`
- Returns data dict with pvPower, powerGridPower, loadPower, energyPower, energySoc

### Exception classes (define at top of api.py):
```python
class LivoltekAuthError(Exception): pass
class LivoltekConnectionError(Exception): pass
class LivoltekApiError(Exception): pass
```

---

## coordinator.py

Three coordinator classes. All inherit from `DataUpdateCoordinator`.

### Base pattern for all coordinators:

```python
class LivoltekFastCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, api_client):
        # Add random jitter to first update
        jitter = timedelta(seconds=random.uniform(0, STARTUP_JITTER_MAX))
        super().__init__(
            hass,
            LOGGER,
            name=f"{DOMAIN}_fast",
            update_interval=SCAN_INTERVAL_FAST + jitter,
            always_update=False,
        )
        self.api = api_client
        self.entry = entry
        self._consecutive_failures = 0
        self._using_fallback = False
```

### `LivoltekFastCoordinator`

**`_async_update_data()`:**
1. Call `api.ensure_token(secuid, api_key)`
2. Try `api.get_energy_storage_info(device_id)`
3. On success: reset `_consecutive_failures = 0`, set `_using_fallback = False`
4. On `LivoltekConnectionError` or `LivoltekApiError`:
   - Increment `_consecutive_failures`
   - Calculate backoff: `BACKOFF_INTERVALS[min(_consecutive_failures-1, len(BACKOFF_INTERVALS)-1)]`
   - Update `update_interval` to backoff value
   - Try fallback: `api.get_current_power_flow_fallback(site_id, user_token)`
   - If fallback succeeds: set `_using_fallback = True`, return minimal data dict
   - If fallback fails: raise `UpdateFailed`
5. On success after failures: restore `update_interval` to `SCAN_INTERVAL_FAST`
6. **PV delta check** (run here after successful fetch):
   - Get `pvFieldToday` from energyStorageInfo
   - Get `todayPowerGeneration` from last medium coordinator data (if available)
   - If both available and delta > `PV_DELTA_WARNING_THRESHOLD`:
     - Fire HA persistent notification once per day maximum
     - `hass.components.persistent_notification.async_create(...)`

**Data returned:** Full `energyStorageInfo` response dict. Entities read directly from it.

### `LivoltekMediumCoordinator`

**`_async_update_data()`:**
1. `await api.ensure_token(...)`
2. Fetch all three in parallel using `asyncio.gather()`:
   - `api.get_signal_device_status(device_id)`
   - `api.get_query_power_flow(site_id)`
   - `api.get_alarms(site_id, days=1)` with `pageSize=5`
3. Same backoff logic as fast coordinator
4. **Alarm log maintenance:**
   - For each new alarm returned, check if it's already in the 30-day log (by `actingTime` + `alarmCode`)
   - Add new ones, keep log sorted chronologically per level
   - Prune entries older than 30 days
   - Store log on `self.alarm_log` dict keyed by level: `{1: [...], 2: [...], 3: [...], 4: [...]}`
5. Return combined dict: `{"signal": signal_data, "power_flow": power_flow_data, "alarms": alarm_list}`

**Manual refresh:** Exposed via `button.livoltek_refresh_status` which calls `await coordinator.async_request_refresh()`

### `LivoltekWeeklyCoordinator`

**`_async_update_data()`:**
1. `await api.ensure_token(...)`
2. `api.get_point_info(device_id)`
3. Extract only these keys from the 148-field response:
   - `workModel`, `dischargeEndSOC`, `dischargeEndSOCEps`, `chargingCurrent`, `dischargingCurrent`, `BMSSOH`, `WarningSoc`, `gridFeedPowerLimit`
4. Return filtered dict

**Manual refresh:** Exposed via `button.livoltek_refresh_settings` which calls `await coordinator.async_request_refresh()`

---

## __init__.py

```python
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

async def async_setup_entry(hass, entry):
    # Create shared aiohttp session
    session = async_get_clientsession(hass)
    
    # Create API client
    api = LivoltekApiClient(
        session=session,
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        token_expiry=entry.data.get(CONF_TOKEN_EXPIRY),
        user_token=entry.data[CONF_USER_TOKEN],
        secuid=entry.data[CONF_SECUID],
        api_key=entry.data[CONF_API_KEY],
    )
    
    # Create coordinators
    fast_coordinator = LivoltekFastCoordinator(hass, entry, api)
    medium_coordinator = LivoltekMediumCoordinator(hass, entry, api)
    weekly_coordinator = LivoltekWeeklyCoordinator(hass, entry, api)
    
    # Initial fetch — raise ConfigEntryNotReady on failure
    await fast_coordinator.async_config_entry_first_refresh()
    await medium_coordinator.async_config_entry_first_refresh()
    await weekly_coordinator.async_config_entry_first_refresh()
    
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        COORDINATOR_FAST: fast_coordinator,
        COORDINATOR_MEDIUM: medium_coordinator,
        COORDINATOR_WEEKLY: weekly_coordinator,
        "api": api,
    }
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass, entry):
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
```

---

## config_flow.py

### Step 1: `async_step_user`

Show form with three fields:
- `secuid` (string, required) — label: "Security ID"
- `api_key` (string, required) — label: "Security Key"
- `user_token` (string, required) — label: "User Token"

On submit:
1. Create a temporary `aiohttp.ClientSession`
2. Create `LivoltekApiClient` with the provided credentials
3. Call `api.login(secuid, api_key)` — if `LivoltekAuthError`, show `invalid_auth` error
4. Call `api.get_sites()` — if fails, show `cannot_connect` error
5. If only one site, auto-select it
6. If multiple sites, proceed to `async_step_select_site`
7. Call `api.get_devices(site_id)` to get `device_id`
8. Create entry with all data

Config entry `data` dict:
```python
{
    CONF_SECUID: secuid,
    CONF_API_KEY: api_key,         # stored with \r\n intact
    CONF_USER_TOKEN: user_token,
    CONF_SITE_ID: site_id,         # string e.g. "29849"
    CONF_DEVICE_ID: device_id,     # int e.g. 26599
    CONF_SITE_NAME: site_name,
    CONF_ACCESS_TOKEN: token,      # cached, refreshed automatically
    CONF_TOKEN_EXPIRY: expiry,     # unix timestamp int
}
```

### Step 2: `async_step_select_site` (only if multiple sites)

Show dropdown of site names. On selection, get devices for chosen site and create entry.

### Re-auth flow: `async_step_reauth`

Triggered when `ConfigEntryAuthFailed` is raised. Show same form, update credentials.

---

## entity.py

Base class for all Livoltek entities:

```python
class LivoltekEntity(CoordinatorEntity):
    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, description):
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_SITE_ID]}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_SITE_ID])},
            manufacturer="Livoltek",
            name=entry.data[CONF_SITE_NAME],
            model=coordinator.data.get("productTypeName") if coordinator.data else None,
            serial_number=entry.data.get("inverterSn"),
            sw_version=coordinator.data.get("armVersion") if coordinator.data else None,
        )
```

---

## sensor.py

Use `@dataclasses.dataclass(frozen=True, kw_only=True)` for `LivoltekSensorEntityDescription` extending `SensorEntityDescription` with:
- `value_fn: Callable[[dict], Any]` — extracts value from coordinator data
- `entity_registry_enabled_default: bool = True`

Define `FAST_SENSORS`, `MEDIUM_SENSORS`, `WEEKLY_SENSORS` lists.

### FAST_SENSORS (from `energyStorageInfo` data dict):

**Live power:**
```python
# PV
key="pv_power", name="PV power", device_class=POWER, unit=kW, state_class=MEASUREMENT,
    value_fn=lambda d: float(d["pvPower"]) if d.get("pvPower") else None

key="pv_string_1_power", name="PV string 1 power", device_class=POWER, unit=kW,
    value_fn=lambda d: float(d["p1Power"]) if d.get("p1Power") else None

key="pv_string_2_power", name="PV string 2 power", device_class=POWER, unit=kW,
    value_fn=lambda d: float(d["p2Power"]) if d.get("p2Power") else None

key="pv_string_1_voltage", name="PV string 1 voltage", device_class=VOLTAGE, unit=V,
    value_fn=lambda d: float(d["p1Voltage"]) if d.get("p1Voltage") else None

key="pv_string_2_voltage", name="PV string 2 voltage", device_class=VOLTAGE, unit=V,
    value_fn=lambda d: float(d["p2Voltage"]) if d.get("p2Voltage") else None

# Grid
key="grid_power", name="Grid power", device_class=POWER, unit=kW,
    value_fn=lambda d: float(d["girdPower"]) if d.get("girdPower") else None
    # Note: negative = exporting, positive = importing

key="grid_voltage", name="Grid voltage", device_class=VOLTAGE, unit=V,
    value_fn=lambda d: float(d["girdVoltage"]) if d.get("girdVoltage") else None

key="grid_frequency", name="Grid frequency", device_class=FREQUENCY, unit=Hz,
    value_fn=lambda d: float(d["girdFrequency"]) if d.get("girdFrequency") else None

# Load
key="load_power", name="Load power", device_class=POWER, unit=kW,
    value_fn=lambda d: float(d["loadActivePower"]) if d.get("loadActivePower") else None

key="load_voltage", name="Load voltage", device_class=VOLTAGE, unit=V,
    value_fn=lambda d: float(d["loadVoltage"]) if d.get("loadVoltage") else None

# Battery
key="battery_power", name="Battery power", device_class=POWER, unit=kW,
    value_fn=lambda d: float(d["batteryActivePower"]) if d.get("batteryActivePower") else None
    # Note: negative = discharging, positive = charging

key="battery_voltage", name="Battery voltage", device_class=VOLTAGE, unit=V,
    value_fn=lambda d: float(d["batteryVoltage"]) if d.get("batteryVoltage") else None

key="battery_current", name="Battery current", device_class=CURRENT, unit=A,
    value_fn=lambda d: float(d["batteryCurrent"]) if d.get("batteryCurrent") else None

key="battery_soc", name="Battery SOC", device_class=BATTERY, unit=%,
    value_fn=lambda d: float(d["batteryRestSoc"]) if d.get("batteryRestSoc") else None

key="battery_max_temp", name="Battery max temperature", device_class=TEMPERATURE, unit=°C,
    value_fn=lambda d: float(d["batteryMaxTemperature"]) if d.get("batteryMaxTemperature") else None

key="battery_min_temp", name="Battery min temperature", device_class=TEMPERATURE, unit=°C,
    value_fn=lambda d: float(d["batteryMinTemperature"]) if d.get("batteryMinTemperature") else None

key="battery_cell_voltage_max", name="Battery cell voltage max", device_class=VOLTAGE, unit=V,
    value_fn=lambda d: float(d["vCellMax"]) if d.get("vCellMax") else None

key="battery_cell_voltage_min", name="Battery cell voltage min", device_class=VOLTAGE, unit=V,
    value_fn=lambda d: float(d["vCellMin"]) if d.get("vCellMin") else None

# Temperatures
key="inverter_temperature", name="Inverter temperature", device_class=TEMPERATURE, unit=°C,
    value_fn=lambda d: float(d["temperature"]) if d.get("temperature") else None

# EPS / AC
key="eps_power", name="EPS power", device_class=POWER, unit=kW,
    value_fn=lambda d: float(d["epsPower"]) if d.get("epsPower") else None

key="eps_voltage", name="EPS voltage", device_class=VOLTAGE, unit=V,
    value_fn=lambda d: float(d["epsVoltage"]) if d.get("epsVoltage") else None

key="ac_power", name="AC output power", device_class=POWER, unit=kW,
    value_fn=lambda d: float(d["activePower"]) if d.get("activePower") else None
```

**Energy totals (state_class=TOTAL_INCREASING):**
```python
key="pv_energy_today", name="PV energy today", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["pvFieldToday"]) if d.get("pvFieldToday") else None

key="pv_energy_month", name="PV energy this month", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["pvFieldMonth"]) if d.get("pvFieldMonth") else None

key="pv_energy_total", name="PV energy total", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["pvFieldTotal"]) if d.get("pvFieldTotal") else None

key="grid_export_today", name="Grid export today", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["girdExportedToday"]) if d.get("girdExportedToday") else None

key="grid_export_month", name="Grid export this month", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["girdExportedMonth"]) if d.get("girdExportedMonth") else None

key="grid_export_total", name="Grid export total", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["girdExportedTotal"]) if d.get("girdExportedTotal") else None

key="grid_import_today", name="Grid import today", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["girdImportedToday"]) if d.get("girdImportedToday") else None

key="grid_import_month", name="Grid import this month", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["girdImportedMonth"]) if d.get("girdImportedMonth") else None

key="grid_import_total", name="Grid import total", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["girdImportedTotal"]) if d.get("girdImportedTotal") else None

key="battery_charged_today", name="Battery charged today", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["batteryCDToday"]) if d.get("batteryCDToday") else None

key="battery_charged_month", name="Battery charged this month", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["batteryCDMonth"]) if d.get("batteryCDMonth") else None

key="battery_charged_total", name="Battery charged total", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["batteryCDTotal"]) if d.get("batteryCDTotal") else None

key="battery_discharged_today", name="Battery discharged today", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["batteryFDToday"]) if d.get("batteryFDToday") else None

key="battery_discharged_month", name="Battery discharged this month", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["batteryFDMonth"]) if d.get("batteryFDMonth") else None

key="battery_discharged_total", name="Battery discharged total", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["batteryFDTotal"]) if d.get("batteryFDTotal") else None

key="load_consumption_today", name="Load consumption today", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["loadConsumptionToday"]) if d.get("loadConsumptionToday") else None

key="load_consumption_month", name="Load consumption this month", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["loadConsumptionMonth"]) if d.get("loadConsumptionMonth") else None

key="load_consumption_total", name="Load consumption total", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["loadConsumptionTotal"]) if d.get("loadConsumptionTotal") else None

key="eps_energy_total", name="EPS energy total", device_class=ENERGY, unit=kWh,
    value_fn=lambda d: float(d["epsConsumptionTotal"]) if d.get("epsConsumptionTotal") else None
```

**Static/diagnostic (entity_registry_enabled_default=False — these are device attributes, not useful as sensors for most users):**
```python
key="battery_sn", name="Battery serial number", device_class=None, unit=None,
    state_class=None,
    value_fn=lambda d: d.get("battery1Sn"),
    entity_registry_enabled_default=False

key="battery_capacity_kwh", name="Battery capacity", device_class=ENERGY_STORAGE, unit=kWh,
    value_fn=lambda d: float(d["batteryCapacityKwh"]) if d.get("batteryCapacityKwh") else None,
    entity_registry_enabled_default=False

key="arm_firmware", name="ARM firmware version", unit=None,
    value_fn=lambda d: d.get("armVersion"),
    entity_registry_enabled_default=False

key="dsp_firmware", name="DSP firmware version", unit=None,
    value_fn=lambda d: d.get("masterDSPVersion"),
    entity_registry_enabled_default=False

key="bms_firmware", name="BMS firmware version", unit=None,
    value_fn=lambda d: d.get("bMSVersion"),
    entity_registry_enabled_default=False
```

### MEDIUM_SENSORS (from `coordinator.data["signal"]` and `coordinator.data["power_flow"]`):

```python
# From signalDeviceStatus — enabled by default
key="pcs_status", name="PCS status", unit=None,
    value_fn=lambda d: d["signal"].get("pcsStatus")
    # 0=normal, 2=generating — document these in strings.json

key="work_status", name="Work status", unit=None,
    value_fn=lambda d: d["signal"].get("workStatus")

key="smart_meter_power", name="Smart meter power", device_class=POWER, unit=kW,
    value_fn=lambda d: float(d["power_flow"]["smActivePower"]) if d["power_flow"].get("smActivePower") else None

# From signalDeviceStatus — disabled by default
key="co2_saved", name="CO₂ saved", unit="kg",
    state_class=TOTAL_INCREASING,
    value_fn=lambda d: float(d["signal"]["carbonReduction"]) if d["signal"].get("carbonReduction") else None,
    entity_registry_enabled_default=False

# From queryPowerFlow — disabled by default
key="generator_state", name="Generator state", unit=None,
    value_fn=lambda d: d["power_flow"].get("generatorState"),
    entity_registry_enabled_default=False
```

**Alarm sensors (from `coordinator.data["alarms"]`):**
```python
key="alarm_count_active", name="Active alarm count", state_class=MEASUREMENT,
    value_fn=lambda d: sum(1 for a in d["alarms"] if a.get("actionId") == 0 and a.get("level", 1) >= 2)
    # actionId=0 means active/unresolved

key="alarm_count_secondary", name="Secondary alarm count", state_class=MEASUREMENT,
    value_fn=lambda d: sum(1 for a in d["alarms"] if a.get("level") == 2 and a.get("actionId") == 0)

key="alarm_count_important", name="Important alarm count", state_class=MEASUREMENT,
    value_fn=lambda d: sum(1 for a in d["alarms"] if a.get("level") == 3 and a.get("actionId") == 0)

key="alarm_count_urgent", name="Urgent alarm count", state_class=MEASUREMENT,
    value_fn=lambda d: sum(1 for a in d["alarms"] if a.get("level") == 4 and a.get("actionId") == 0)

key="last_alarm_code", name="Last alarm code", unit=None,
    value_fn=lambda d: d["alarms"][0].get("alarmCode") if d["alarms"] else None

key="last_alarm_description", name="Last alarm description", unit=None,
    value_fn=lambda d: d["alarms"][0].get("content") if d["alarms"] else None
```

### WEEKLY_SENSORS (from `coordinator.data` — the filtered point/info dict):

```python
key="work_mode", name="Work mode", unit=None,
    value_fn=lambda d: d.get("workModel", {}).get("value")
    # Map values: 0=Self Use, 1=Back Up, 2=Feed-in First — document in strings.json

key="discharge_end_soc", name="Discharge end SOC (grid)", device_class=BATTERY, unit=%,
    value_fn=lambda d: int(d.get("dischargeEndSOC", {}).get("value", 0)) if d.get("dischargeEndSOC") else None

key="discharge_end_soc_eps", name="Discharge end SOC (EPS)", device_class=BATTERY, unit=%,
    value_fn=lambda d: int(d.get("dischargeEndSOCEps", {}).get("value", 0)) if d.get("dischargeEndSOCEps") else None

key="max_charge_current", name="Max charge current", device_class=CURRENT, unit=A,
    value_fn=lambda d: float(d.get("chargingCurrent", {}).get("value", 0)) if d.get("chargingCurrent") else None

key="max_discharge_current", name="Max discharge current", device_class=CURRENT, unit=A,
    value_fn=lambda d: float(d.get("dischargingCurrent", {}).get("value", 0)) if d.get("dischargingCurrent") else None

key="battery_soh", name="Battery SOH", device_class=BATTERY, unit=%,
    value_fn=lambda d: float(d.get("BMSSOH", {}).get("value", 0)) if d.get("BMSSOH") else None

key="warning_soc", name="Warning SOC", device_class=BATTERY, unit=%,
    value_fn=lambda d: int(d.get("WarningSoc", {}).get("value", 0)) if d.get("WarningSoc") else None

key="grid_feed_power_limit", name="Grid feed power limit", device_class=POWER, unit=W,
    value_fn=lambda d: float(d.get("gridFeedPowerLimit", {}).get("value", 0)) if d.get("gridFeedPowerLimit") else None
```

### `async_setup_entry` in sensor.py:

```python
async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    entities = []
    
    fast_coord = data[COORDINATOR_FAST]
    medium_coord = data[COORDINATOR_MEDIUM]
    weekly_coord = data[COORDINATOR_WEEKLY]
    
    entities += [LivoltekSensor(fast_coord, entry, desc) for desc in FAST_SENSORS]
    entities += [LivoltekSensor(medium_coord, entry, desc) for desc in MEDIUM_SENSORS]
    entities += [LivoltekSensor(weekly_coord, entry, desc) for desc in WEEKLY_SENSORS]
    
    async_add_entities(entities)
```

---

## binary_sensor.py

Two binary sensors:

**`binary_sensor.livoltek_online`:**
- Coordinator: fast
- `is_on`: `coordinator.data.get("pcsStatus") not in (None, 3)` — pcsStatus 3 = offline
- `device_class`: CONNECTIVITY
- Name: "Online"

**`binary_sensor.livoltek_active_alarm`:**
- Coordinator: medium
- `is_on`: `any(a.get("level", 1) >= 3 and a.get("actionId") == 0 for a in coordinator.data.get("alarms", []))`
- `device_class`: PROBLEM
- Name: "Active alarm"
- `extra_state_attributes`: include count of level 3 and 4 active alarms

---

## button.py

Two button entities, both `ButtonDeviceClass.RESTART` class:

**`button.livoltek_refresh_settings`:**
- Name: "Refresh inverter settings"
- Coordinator: weekly
- `async_press()`: `await self.coordinator.async_request_refresh()`

**`button.livoltek_refresh_status`:**
- Name: "Refresh status"  
- Coordinator: medium
- `async_press()`: `await self.coordinator.async_request_refresh()`

---

## diagnostics.py

```python
async def async_get_config_entry_diagnostics(hass, entry):
    data = hass.data[DOMAIN][entry.entry_id]
    fast_data = data[COORDINATOR_FAST].data or {}
    medium_data = data[COORDINATOR_MEDIUM].data or {}
    weekly_data = data[COORDINATOR_WEEKLY].data or {}
    medium_coord = data[COORDINATOR_MEDIUM]
    
    # Build alarm log grouped by level, each section chronological
    alarm_log = {}
    level_names = {1: "Tips", 2: "Secondary", 3: "Important", 4: "Urgent"}
    for level, name in level_names.items():
        level_alarms = sorted(
            medium_coord.alarm_log.get(level, []),
            key=lambda a: a.get("actingTime", 0)
        )
        alarm_log[name] = [
            {
                "time": a.get("actingTimeString"),
                "code": a.get("alarmCode"),
                "description": a.get("content"),
                "status": "active" if a.get("actionId") == 0 else "cleared",
            }
            for a in level_alarms
        ]
    
    return {
        "config_entry": async_redact_data(dict(entry.data), [CONF_API_KEY, CONF_USER_TOKEN, CONF_ACCESS_TOKEN]),
        "fast_coordinator_data": async_redact_data(fast_data, []),  # no sensitive data in telemetry
        "medium_coordinator_data": async_redact_data(medium_data, []),
        "weekly_coordinator_data": weekly_data,
        "alarm_log_30_days": alarm_log,
        "coordinator_status": {
            "fast_last_update": str(data[COORDINATOR_FAST].last_update_success),
            "medium_last_update": str(data[COORDINATOR_MEDIUM].last_update_success),
            "weekly_last_update": str(data[COORDINATOR_WEEKLY].last_update_success),
            "using_fallback": data[COORDINATOR_FAST]._using_fallback,
            "consecutive_failures": data[COORDINATOR_FAST]._consecutive_failures,
        }
    }
```

---

## strings.json / translations/en.json

Must include translations for:
- Config flow steps: `user` (with field descriptions for secuid, api_key, user_token), `select_site`
- All sensor names (use `translation_key` matching `key` field on each sensor description)
- Error messages: `invalid_auth`, `cannot_connect`, `no_sites_found`, `unknown`
- `pcsStatus` values: 0=Normal, 1=Standby, 2=Generating, 3=Offline, 4=Self-check, 5=Upgrading
- `workModel` values: 0=Self use, 1=Back up, 2=Feed-in first

---

## hacs.json

```json
{
  "name": "Livoltek",
  "render_readme": true,
  "homeassistant": "2024.1.0"
}
```

---

## AGENTS.md

This file provides context for AI coding assistants working on this integration in future sessions. Include:

1. **Full authentication flow** — both tokens, no Bearer prefix, key encoding with `\r\n`, token refresh logic
2. **All confirmed API endpoints** — URL, method, payload, response structure, which fields are populated vs null
3. **Why certain endpoints were excluded** — device/details (too large), ESS (batterySn null), site/overview (redundant), etc.
4. **The three coordinator architecture** — what each polls, why
5. **Field mapping reference** — JSON field name → sensor key for every sensor
6. **Known quirks** — `girdPower` typo in API (not `gridPower`), `batteryRestSoc` not `batterySOC`, `loadActivePower` not `loadPower`
7. **Test credentials pattern** — how to test (PowerShell examples from discovery)
8. **Future extension pattern** — how to add a new sensor (add entry to FAST/MEDIUM/WEEKLY_SENSORS list with value_fn)
9. **Private vs public API decision** — why private API is used for data despite being undocumented
10. **Write capability status** — confirmed blocked for API credentials, only works via browser session

---

## README.md

### Installation

**Via HACS (recommended):**
1. Open HACS in Home Assistant
2. Go to Integrations → Custom Repositories
3. Add `https://github.com/your-repo/hass-livoltek` as Integration type
4. Search for "Livoltek" and install
5. Restart Home Assistant

**Manual:**
1. Download the `custom_components/livoltek` folder
2. Copy to your HA `config/custom_components/livoltek` folder
3. Restart Home Assistant

### Getting your credentials

You need three values from the Livoltek portal at `https://evs.livoltek-portal.com`:

**Security ID and Security Key:**
1. Log into the portal
2. Click your account name (top right) → My Profile
3. Click **Security ID** tab
4. Copy the `Security ID` (this is your `secuid`)
5. Copy the `Security Key` (this is your `key`) — copy it exactly including any trailing characters

**User Token:**
1. Still in My Profile
2. Click **Generate Token** tab
3. If no token exists, click Generate
4. Copy the full token string

### Setup in Home Assistant
1. Go to Settings → Devices & Services → Add Integration
2. Search for "Livoltek"
3. Enter your Security ID, Security Key, and User Token
4. The integration will auto-discover your site and inverter
5. Click Submit

### Entities

After setup the following device will appear with all sensors grouped under it:
- All live power sensors (updated every 60 seconds)
- All energy totals (updated every 60 seconds)
- Status sensors — PCS status, work status, smart meter power (every 5 minutes)
- Alarm sensors (every 5 minutes)
- Inverter settings — work mode, SOC thresholds, etc. (weekly, or on-demand via button)

Some sensors are **disabled by default** — CO₂ saved, generator state, firmware versions, battery SN. Enable them individually in the entity list if needed.

### Buttons
- **Refresh inverter settings** — immediately re-fetches all `point/info` register values
- **Refresh status** — immediately re-fetches alarms and status sensors

### Diagnostics
To download a diagnostics report (useful for bug reports):
Settings → Devices & Services → Livoltek → 3-dot menu → Download diagnostics

The report includes a 30-day alarm log grouped by severity level.

---

## Important implementation notes

1. **Field name typos in the API are real** — `girdPower` (not `gridPower`), `girdVoltage`, `girdFrequency` etc. These are the actual JSON field names. Do not "fix" them.

2. **Many fields return string numbers** — e.g. `"pvPower": "0.31"`. Always cast with `float()` and handle `None` gracefully.

3. **`batteryRestSoc`** is the correct SOC field. `batterySOC` in the same response is null. Use `batteryRestSoc`.

4. **`batteryActivePower`** is the correct battery power field (in kW). `batteryPower` in the same response is null.

5. **`loadActivePower`** is the correct load power field. `loadPower` appears in other endpoints but `loadActivePower` is what `energyStorageInfo` provides.

6. **Grid power sign convention** — negative `girdPower` = exporting to grid, positive = importing. Document this clearly in sensor names or attributes.

7. **The `point/info` response** — each register is an object like `{"value": "10", "address": "40010", ...}`. Extract `.value` from each and cast appropriately.

8. **Alarm `actionId`** — `0` = active/unresolved, `1` = cleared/resolved. Use this to filter active vs historical alarms.

9. **Session reuse** — use `async_get_clientsession(hass)` from `homeassistant.helpers.aiohttp_client`. Do not create a new session per request.

10. **`ConfigEntryAuthFailed`** — raise this (not `UpdateFailed`) when getting auth errors, so HA surfaces the re-auth UI to the user.
