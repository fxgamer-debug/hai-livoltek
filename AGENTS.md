# AGENTS.md — hai-livoltek

Context for AI coding assistants working on this repository in future sessions.

The repo name `hai-livoltek` reads as **"Home Assistant + AI"**: the entire codebase, the translations, and the user-facing README were authored by AI tooling under human direction. That's not a disclaimer for you, future agent — it's a status update. Treat this file as your source of truth and you'll be productive without re-deriving the API map every session.

This project is **not** a fork of [`adamlonsdale/hass-livoltek`](https://github.com/adamlonsdale/hass-livoltek) — it's an independent rewrite that targets the same hardware. Both register the HA domain `livoltek`, so only one can be installed at a time. Differences are summarised in the user-facing `README.md`; the short version for contributors is: we use the **private** Livoltek API for telemetry (much richer data, no port-8081 dependency for ongoing polling) instead of the OpenAPI-generated public client that the upstream project ships.

---

## 1. Authentication (v2, May 2026)

One token (Bearer JWT), obtained from the v2 login endpoint.

| Item | Lifetime | How obtained | How sent |
|---|---|---|---|
| Access token (`access_token`) | ~30 days | `POST /nbp/login/customer` with `{login_account, password:<md5>}` | `Authorization: Bearer <token>` |

### Regional servers

Users must select a regional server during setup; the selected URL is stored in `entry.data["base_url"]` and used for all API calls:

- EU & MEA: `https://evs.livoltek-portal.com` (tested)
- International: `https://www.livoltek-portal.com` (untested)
- Asia: `https://aa.livoltek-portal.com` (untested)

### Session registration

After login, call `POST /ctrller-manager/login/login` with the same Bearer token. This is required for alarm endpoints; it is safe to call every time we refresh tokens.

### Token refresh strategy

Implemented in `LivoltekApiClient.ensure_token`:

1. If no token cached → log in.
2. If `session_expiry_time - now < TOKEN_REFRESH_BUFFER` (24h) → log in.
3. On 401 from any call → force `_token_expiry = 0`, refresh, retry once.

---

## 2. API endpoints — confirmed working (v2)

All bases live in `const.py`.

### Single API surface, multiple base URLs

The endpoint paths are identical across regions; only the base URL differs (see Authentication section).

| Endpoint | Method | Purpose | Coordinator |
|---|---|---|---|
| `/ctrller-manager/energystorage/energyStorageInfo?id={device}&isUseChangeUnit=true` | POST `{}` | Live PV / grid / battery / load values + per-period energy totals | fast (60s) |
| `/ctrller-manager/energystorage/signalDeviceStatus?id={device}&isUseChangeUnit=true` | POST `{}` | PCS state, work state, lifetime totals, CO2 | medium (5min) |
| `/ctrller-manager/powerstation/queryPowerFlow/{site_id}` | POST `{}` | Smart-meter values, generator state | medium (5min) |
| `/ctrller-manager/alarm/findAllFilter` | POST | Alarm list | medium (5min) |
| `/hess-ota/device/operation/point/info` | POST | Inverter registers | weekly |

### Endpoints intentionally **not** used

| Endpoint | Why excluded |
|---|---|
| Write endpoints | Intentionally read-only (see README) |

---

## 3. Architecture — three coordinators

Why three? Different data has very different cadence requirements and rate-limit characteristics.

| Coordinator | Interval | Why this cadence |
|---|---|---|
| `LivoltekFastCoordinator` | 60 s | Live power values change minute-to-minute; needed for energy dashboard |
| `LivoltekMediumCoordinator` | 5 min | Status only meaningfully changes on this scale; cheap to poll |
| `LivoltekWeeklyCoordinator` | 1 week | Inverter settings (work mode, SOC limits) change manually only; cheap to refresh on-demand via the button entity |

A small random startup jitter (`STARTUP_JITTER_MAX = 30s`) is added to each coordinator's first interval so the three don't issue requests at the exact same instants. The fast coordinator also keeps a reference to the medium coordinator (`fast_coordinator.medium_coordinator`) so it can run a PV-delta sanity check. When `energyStorageInfo` omits `pcsStatus`, the fast coordinator copies `pcsStatus` from the last successful medium `signalDeviceStatus` payload so `binary_sensor.online` (which reads the fast coordinator) still reflects PCS state.

### Backoff

`BACKOFF_INTERVALS = [60, 120, 300, 600]`. After each failure the coordinator's `update_interval` is bumped to the next value. A single success resets the counter and restores the base interval.

### Fallback (fast coordinator only)

When `energyStorageInfo` fails, the fast coordinator tries `queryPowerFlow`. The shape is normalised in `_normalise_fallback` so the live-power sensors still produce values. A `_fallback: True` flag is added to the returned dict for diagnostics.

---

## 4. Field mapping reference

### `energyStorageInfo` → fast sensors

| Sensor key | JSON field | Notes |
|---|---|---|
| `pv_power` | `pvPower` | kW |
| `pv_string_1_power` | `p1Power` | kW |
| `pv_string_2_power` | `p2Power` | kW |
| `pv_string_1_voltage` | `p1Voltage` | V |
| `pv_string_2_voltage` | `p2Voltage` | V |
| `grid_power` | `girdPower` | kW, **negative = export** |
| `grid_voltage` | `girdVoltage` | V |
| `grid_frequency` | `girdFrequency` | Hz |
| `load_power` | `loadActivePower` | kW (NOT `loadPower` — that field exists in other endpoints but is null here) |
| `load_voltage` | `loadVoltage` | V |
| `battery_power` | `batteryActivePower` | kW, **negative = discharging** (NOT `batteryPower` — that's null) |
| `battery_voltage` | `batteryVoltage` | V |
| `battery_current` | `batteryCurrent` | A |
| `battery_soc` | `batteryRestSoc` | % (NOT `batterySOC` — that's null) |
| `battery_max_temp` | `batteryMaxTemperature` | °C |
| `battery_min_temp` | `batteryMinTemperature` | °C |
| `battery_cell_voltage_max` | `vCellMax` | V |
| `battery_cell_voltage_min` | `vCellMin` | V |
| `inverter_temperature` | `temperature` | °C |
| `eps_power` | `epsPower` | kW |
| `eps_voltage` | `epsVoltage` | V |
| `ac_power` | `activePower` | kW |
| `pv_energy_today/month/total` | `pvFieldToday/Month/Total` | kWh, TOTAL_INCREASING |
| `grid_export_today/month/total` | `girdExportedToday/Month/Total` | kWh |
| `grid_import_today/month/total` | `girdImportedToday/Month/Total` | kWh |
| `battery_charged_today/month/total` | `batteryCDToday/Month/Total` | kWh |
| `battery_discharged_today/month/total` | `batteryFDToday/Month/Total` | kWh |
| `load_consumption_today/month/total` | `loadConsumptionToday/Month/Total` | kWh |
| `eps_energy_total` | `epsConsumptionTotal` | kWh |
| `battery_sn` | `battery1Sn` | string, disabled by default |
| `battery_capacity_kwh` | `batteryCapacityKwh` | kWh, disabled by default |
| `arm_firmware` | `armVersion` | disabled |
| `dsp_firmware` | `masterDSPVersion` | disabled |
| `bms_firmware` | `bMSVersion` | disabled |

**Weekly `point/info` discovery:** `deviceId` in the API is the Wi‑Fi logger serial from `energyStorageInfo.collectorSn`, or `wifiSn` when `collectorSn` is null. `productType` comes from `energyStorageInfo.template`, defaulting to **44** when null (see `DEFAULT_PRODUCT_TYPE` in `const.py`).

### `signalDeviceStatus` + `queryPowerFlow` → medium sensors

Returned wrapped as `{"signal": …, "power_flow": …}` so sensor `value_fn` receives the combined dict.

| Sensor key | Source | Field |
|---|---|---|
| `pcs_status` | signal | `pcsStatus` (0 normal / 1 standby / 2 generating / 3 offline / 4 self-check / 5 upgrading) |
| `work_status` | signal | `workStatus` |
| `smart_meter_power` | power_flow | `smActivePower` (kW) |
| `co2_saved` | signal | `carbonReduction` (kg, disabled) |
| `generator_state` | power_flow | `generatorState` (disabled) |

### `point/info` → weekly sensors

Each register is `{"value": "10", "address": "40010", ...}` — extract `.value`.

| Sensor key | Field | Notes |
|---|---|---|
| `work_mode` | `workModel` | 0 self_use / 1 back_up / 2 feed_in_first |
| `discharge_end_soc` | `dischargeEndSOC` | % |
| `discharge_end_soc_eps` | `dischargeEndSOCEps` | % |
| `max_charge_current` | `chargingCurrent` | A |
| `max_discharge_current` | `dischargingCurrent` | A |
| `battery_soh` | `BMSSOH` | % |
| `warning_soc` | `WarningSoc` | % |
| `grid_feed_power_limit` | `gridFeedPowerLimit` | W |

---

## 5. Known quirks (do not "fix")

1. **`girdPower`, `girdVoltage`, `girdFrequency`, `girdImported*`, `girdExported*`** — typos baked into the API. These are the real field names. Renaming them in the integration breaks data extraction.
2. **String numbers everywhere.** `"pvPower": "0.31"`. Always cast with `float()` and tolerate `None`.
3. **`batterySOC` vs `batteryRestSoc`** — the former is null in the energyStorageInfo response. Use `batteryRestSoc`.
4. **`batteryPower` vs `batteryActivePower`** — same thing: `batteryPower` is null, `batteryActivePower` is the populated kW value.
5. **`loadPower` vs `loadActivePower`** — `loadActivePower` is the one populated by `energyStorageInfo`. `loadPower` only appears (with a value) on `queryPowerFlow`.
6. **Grid sign convention:** `girdPower < 0` ⇒ exporting to grid; `> 0` ⇒ importing.
7. **Port 8081 is sometimes blocked** from non-residential IPs. We assume HA is running on the user's home LAN. Don't paper over a port-8081 failure with retries — surface it as `cannot_connect` so the user knows.
8. **No alarm endpoint.** `/ctrller-manager/alarm/findAllFilter` requires a portal-session JWT; the public-API access token is rejected with `msgCode='token.expiried'` regardless of body shape, header format, or token freshness. Don't re-add a `get_alarms` call without a new auth strategy.

---

## 6. How to test (PowerShell-friendly recipe used during discovery)

```powershell
# 1. Login
$body = '{"login_account":"<username>","password":"<md5(password)>"}'
$login = Invoke-RestMethod `
  -Method POST `
  -Uri "https://evs.livoltek-portal.com/nbp/login/customer" `
  -ContentType "application/json" `
  -Body $body
$token = $login.data.access_token

# 2. Register session (needed for alarms)
Invoke-RestMethod `
  -Method POST `
  -Uri "https://evs.livoltek-portal.com/ctrller-manager/login/login" `
  -Headers @{ Authorization = "Bearer $token"; "Content-Type" = "application/json" } `
  -Body "{}"

# 3. Energy storage info
Invoke-RestMethod `
  -Method POST `
  -Uri "https://evs.livoltek-portal.com/ctrller-manager/energystorage/energyStorageInfo?id=<deviceId>&isUseChangeUnit=true" `
  -Headers @{ Authorization = "Bearer $token"; "Content-Type" = "application/json"; language = "en" } `
  -Body "{}"
```

If a request returns `msgCode != "operate.success"`, the `msg` field contains a human description.

---

## 7. Adding a new sensor

1. Identify the source field by inspecting a coordinator response (use the diagnostics download).
2. Pick the right coordinator: live data → `FAST_SENSORS`; status → `MEDIUM_SENSORS`; settings → `WEEKLY_SENSORS`.
3. Append a new `LivoltekSensorEntityDescription` to that tuple. Provide:
   - `key` — used as the unique-id suffix and translation key.
   - `translation_key` — usually equal to `key`.
   - `device_class` / `native_unit_of_measurement` / `state_class` as appropriate.
   - `value_fn` — a lambda that takes the coordinator dict and returns the value (use `_to_float` / `_to_int` / `_point` helpers from `sensor.py`).
4. Add a matching `entity.sensor.<key>` block to both `strings.json` and `translations/en.json`.

That's it — no other plumbing needed.

---

## 8. Single-backend decision (v2)

As of May 2026, all required endpoints live on the v2 backends (regional base URLs) and the old public API (port 8081) is gone. This integration uses the selected regional backend for both discovery (stations/devices) and telemetry.

---

## 9. Write capability (intentionally excluded)

The v2 API appears to support write controls, but this integration remains **read-only by design**. Do not add `number`, `select`, `switch`, etc. until write operations are explicitly specced, tested, and considered safe.

---

## 10. File-by-file responsibility map

| File | Responsibility |
|---|---|
| `manifest.json` | HA integration metadata. `single_config_entry: true`. |
| `const.py` | All constants — endpoints, intervals, config keys, backoff. |
| `api.py` | Pure aiohttp client. No HA imports beyond the shared logger. |
| `coordinator.py` | Three `DataUpdateCoordinator` subclasses. |
| `__init__.py` | Setup/teardown, glues coordinators together, persists refreshed token. |
| `config_flow.py` | User + select-site + reauth steps. |
| `entity.py` | `LivoltekEntity` base — single device per site, attribution, unique-id pattern. |
| `sensor.py` | All sensor descriptions across the three coordinators. |
| `binary_sensor.py` | `online`. |
| `button.py` | `refresh_status` and `refresh_settings`. |
| `diagnostics.py` | Redacted config dump + raw coordinator payloads. |
| `strings.json` / `translations/en.json` | UI strings, kept in sync. |
