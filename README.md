# hai-livoltek (v2 API)

A read-only Home Assistant integration for **Livoltek hybrid solar inverters** (being developed against the **Hyper 5000** with battery storage). Built from scratch using current Home Assistant patterns: config flow with re-authentication, multiple `DataUpdateCoordinator`s, typed entity descriptions, diagnostics, and translations.

> **Status: updated to the May 2026 API.** The Livoltek backend changed in May 2026: the old public API is gone, authentication is now username/password (hashed locally), and alarms are available again.

> **Built end-to-end with AI.** Every line of Python, every translation, this README, and the API reverse-engineering notes in `AGENTS.md` were produced by AI coding assistants under human direction. The `hai` in the name stands for **"Home Assistant + AI"** — both a description of the toolchain and a small disclaimer. See [AI authorship and what that means for you](#ai-authorship-and-what-that-means-for-you) below before deploying it.

- **Domain:** `livoltek`
- **Targets:** Home Assistant 2024.1+ / HAOS (verified on **2026.4**)
- **IoT class:** `cloud_polling`
- **HACS compatible:** yes
- **Read-only:** yes — write capability exists but is intentionally excluded

> **Not affiliated with [`adamlonsdale/hass-livoltek`](https://github.com/adamlonsdale/hass-livoltek).** That project was the inspiration and reference for this work — see [Acknowledgements](#acknowledgements) below. Only one Livoltek integration can be installed at a time, since both register the same `livoltek` domain.

---

## AI authorship and what that means for you

This integration was authored by AI tools (large language models running inside the Cursor IDE) working from a human-written specification. A human reviewed the output and is responsible for shipping it, but no part of the code was hand-written from scratch.

What that means in practice:

- **Treat your first installation as a beta if you're not on a Hyper 5000.** The integration has been verified end-to-end on a Hyper 5000 with battery on the EU portal under HA 2026.4, but the wider Livoltek family ships several inverter variants whose JSON field names occasionally drift. Watch for sensors that report `unknown` for more than a poll cycle — that usually means the AI guessed a field name that doesn't match your firmware's response.
- **Diagnostics are your friend.** Every coordinator's raw payload is included in the diagnostics download. If something looks wrong, please attach a redacted diagnostics dump to the issue rather than describing the symptom — that gives the maintainer (human *or* AI) the actual API shape to compare against.
- **Energy dashboard cumulative sensors** are the most safety-critical. Cross-check the `*_total` values against what the Livoltek portal shows for the first few days before relying on them in cost calculations.
- **No write entities.** The integration is intentionally read-only, so the worst it can do is misreport a value. It cannot change inverter settings, dispatch the battery, or talk to the grid.

If you find a bug or a hallucinated field name, file an issue with diagnostics and the fix is usually a one-line edit in `sensor.py` — see the "Adding a new sensor" section in `AGENTS.md`.

---

## Installation

### HACS (custom repository)

1. Open HACS in Home Assistant.
2. Go to **Integrations → ⋮ → Custom repositories**.
3. Add `https://github.com/fxgamer-debug/hai-livoltek` as type **Integration**.
4. Search for **Livoltek (hai)** and install.
5. Restart Home Assistant.

> Important: HACS will block installation if another integration registered as `livoltek` is already installed. Remove any prior Livoltek integration first.

### Manual

1. Download the `custom_components/livoltek` folder from this repository.
2. Copy it to `<config>/custom_components/livoltek` on your Home Assistant instance, replacing any existing folder.
3. Restart Home Assistant.

---

## Getting your credentials

Log into the Livoltek portal (regional server) and use your portal username/password.

Your credentials are:

- **Username** — your portal login username
- **Password** — your portal login password

The integration hashes the password locally (MD5) and stores only the hash in the config entry.

---

## Setup in Home Assistant

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Livoltek (hai)**.
3. Select your server region (EU & MEA / International / Asia).
4. Enter your portal username and password.
5. The integration auto-discovers your site and inverter (or, if you have multiple sites, prompts you to choose one).
5. Click **Submit**.

A single device is created with all sensors grouped underneath.

---

## Entities

### Live values (updated every 60s)

PV: power, per-string power & voltage. Grid: power (negative = export, positive = import), voltage, frequency. Load: power, voltage. Battery: power (negative = discharging, positive = charging), voltage, current, SOC, max/min temperature, max/min cell voltage. Inverter: temperature. EPS: power, voltage. AC output power.

### Energy totals (updated every 60s, `total_increasing` for the Energy dashboard)

Per-period (today / month / lifetime) totals for: PV generation, grid export, grid import, battery charged, battery discharged, load consumption. Lifetime EPS energy.

### Status (updated every 5 minutes, refreshable via button)

PCS status, work status, smart-meter power.

### Inverter settings (refreshed weekly, refreshable via button)

Work mode (self-use / back-up / feed-in first), discharge end SOC (grid + EPS), max charge/discharge current, battery SOH, warning SOC, grid feed power limit.

### Binary sensors

- **Online** (`connectivity` device class) — false when the inverter reports `pcsStatus = 3`.
- **Active alarm** (`problem` device class) — on when any **Important** or **Urgent** alarm is currently active.

### Buttons

- **Refresh inverter settings** — re-fetches all settings registers immediately (otherwise polled weekly).
- **Refresh status** — re-fetches the status sensors immediately (otherwise polled every 5 min).

### Alarms

The integration tracks alarms across four severity levels:

- Tips
- Secondary
- Important
- Urgent

The `binary_sensor.livoltek_active_alarm` turns on when any Important or Urgent alarm is active. A 30‑day alarm history is available in the diagnostics download.

### Note on regional server testing

Only the EU & MEA server (`evs.livoltek-portal.com`) has been tested and verified. The International (`www.livoltek-portal.com`) and Asia (`aa.livoltek-portal.com`) servers are assumed to use identical API endpoints and authentication, but this has not been independently confirmed.

### Disabled by default

These are useful but rarely needed; enable individually from the entity list:

- CO₂ saved, **dashboard metrics** (today yield, revenue, CO₂ reduction, trees — see note below), generator state, firmware versions (ARM / DSP / BMS), battery serial number, battery capacity (kWh).

> **Note on revenue, CO₂ and tree planting sensors**  
> The revenue, CO₂ reduction and equivalent trees planted sensors use the Livoltek dashboard metrics API. These sensors will only return data if the corresponding metrics have been enabled on your account. To enable them, log into the Livoltek portal, go to your account dashboard settings, and activate the metrics you want to track. Revenue sensors will show 0 until you also configure your electricity tariff in the portal.

---

## Energy dashboard

All `today/month/total` energy sensors use `state_class: total_increasing` so they're directly compatible with Home Assistant's Energy dashboard. Suggested mappings:

| Energy dashboard slot | Livoltek sensor |
|---|---|
| Solar production | `sensor.livoltek_pv_energy_total` |
| Grid consumption | `sensor.livoltek_grid_import_total` |
| Return to grid | `sensor.livoltek_grid_export_total` |
| Battery storage – Energy going in | `sensor.livoltek_battery_charged_total` |
| Battery storage – Energy coming out | `sensor.livoltek_battery_discharged_total` |

---

## Translations

Built-in: **English** (`en`) and **Romanian** (`ro`). Home Assistant picks the right one automatically based on the user's profile language. Contributions of additional languages are welcome — copy `custom_components/livoltek/translations/en.json` to `<lang>.json` and translate the values. AI-assisted translations are explicitly fine; just make sure a native speaker has read the result before opening the PR.

---

## Diagnostics

To download a diagnostics report (useful for bug reports):

> Settings → Devices & Services → **Livoltek** → ⋮ → Download diagnostics

The report includes:

- Redacted config entry (no API key, user token, or access token).
- Raw payloads from each coordinator.
- Coordinator health: last-update success, current backoff state, whether the public-API fallback is in use.

---

## Troubleshooting

**"Invalid auth"** — verify the portal username/password are correct.

**"Cannot connect"** — Home Assistant must be able to reach `evs.livoltek-portal.com` over HTTPS.

**Sensors stuck at the same value** — check the **Online** binary sensor first. If the inverter is offline, data won't update. Otherwise, click the **Refresh status** button and download diagnostics to see the underlying coordinator state.

---

## Changelog

### 2.4

- **Dashboard metrics:** Optional sensors for today yield (dashboard), revenue (today/total), CO₂ reduction, and equivalent trees planted via `customerData` — all **disabled by default**; enable in HA and in the Livoltek portal dashboard settings. Legacy **CO₂ saved** (`signalDeviceStatus`) remains disabled by default; prefer **CO₂ emission reduction** from dashboard data when enabled.

### 2.3

- **Alarms:** When the alarm endpoint fails repeatedly, alarm HTTP requests now use the same backoff ladder as other endpoints (`60s → 120s → …`) while signal and power-flow polling continues each cycle—reduces load on a flaky alarm API without slowing core telemetry.

### 2.2

- **Alarms:** Session registration now runs on every successful login (required by the portal). Alarm polling sends site filter, inverter serial (`inverterSn` from discovery), `showDescribe`, and `fuzzyQueryId`, and uses a **7-day** window for routine polls (30 days remains for diagnostics full log).

---

## Contributing

Issues and PRs welcome. Because this codebase is designed for AI-assisted maintenance, two files matter most:

- `AGENTS.md` is the canonical reference for the API shape, field name quirks, and the three-coordinator architecture. Read it before changing anything in `api.py` or `coordinator.py`. Update it whenever you change the API surface.
- `custom_components/livoltek/sensor.py` is intentionally a flat list of `LivoltekSensorEntityDescription` records — adding a new sensor is one new entry plus matching strings in `strings.json` / `translations/*.json`.

If you use AI to generate a patch, please call that out in the PR description — not as a disqualifier, just so reviewers know what to focus on (typically: hallucinated JSON field names, fabricated HA constants, missing edge-case handling for `None` values from the API).

---

## Acknowledgements

- **[`adamlonsdale/hass-livoltek`](https://github.com/adamlonsdale/hass-livoltek)** — the original Home Assistant integration for Livoltek inverters and the inspiration for this project. Adam's work was the first to demonstrate the Livoltek public API was usable from HA, and the API surface mapped out by that project provided a starting point for the deeper reverse engineering documented in `AGENTS.md`.
- The Livoltek portal web app, whose network traffic was inspected to map the private telemetry endpoints used here.
- The Cursor IDE and the AI models used to author the code, the translations, and this document.

---

## License

[MIT](LICENSE) — Copyright © 2026 [fxgamer-debug](https://github.com/fxgamer-debug).

You're free to use, copy, modify, fork, and redistribute this integration, including in commercial or closed-source projects, as long as the copyright notice and the MIT permission notice are preserved. No warranty of any kind is provided; see [`LICENSE`](LICENSE) for the full text.

The MIT licence was chosen deliberately to match [`adamlonsdale/hass-livoltek`](https://github.com/adamlonsdale/hass-livoltek) so that improvements can flow in either direction without re-licensing friction, and to keep this integration aligned with the rest of the Home Assistant / HACS ecosystem. If you ship a fix or a new sensor mapping, please consider opening a PR here so other Livoltek owners benefit.
