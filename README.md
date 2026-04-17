# hai-livoltek

A modern, read-only Home Assistant integration for **Livoltek hybrid solar inverters** (developed and tested against the **Hyper 5000** with battery storage). Built from scratch using current Home Assistant patterns: config flow with re-authentication, multiple `DataUpdateCoordinator`s, typed entity descriptions, diagnostics, and translations.

> **Built end-to-end with AI.** Every line of Python, every translation, this README, and the API reverse-engineering notes in `AGENTS.md` were produced by AI coding assistants under human direction. The `hai` in the name stands for **"Home Assistant + AI"** — both a description of the toolchain and a small disclaimer. See [AI authorship and what that means for you](#ai-authorship-and-what-that-means-for-you) below before deploying it.

- **Domain:** `livoltek`
- **Tested with:** Home Assistant 2024.1+ / HAOS
- **IoT class:** `cloud_polling`
- **HACS compatible:** yes
- **Read-only:** yes — the Livoltek API key cannot issue write commands (only the portal browser session can)

> **Not affiliated with [`adamlonsdale/hass-livoltek`](https://github.com/adamlonsdale/hass-livoltek).** Only one Livoltek integration can be installed at a time because both register the same `livoltek` domain — see [Comparison](#comparison-with-adamlonsdalehass-livoltek) below to choose the right one for your setup.

---

## AI authorship and what that means for you

This integration was authored by AI tools (large language models running inside the Cursor IDE) working from a human-written specification. A human reviewed the output and is responsible for shipping it, but no part of the code was hand-written from scratch.

What that means in practice:

- **Treat the first installation as a beta.** Run it on a non-production HA instance first if you can. Watch for sensors that report `unknown` for more than a poll cycle — that usually means the AI guessed a JSON field name that doesn't actually exist in your firmware's response.
- **Diagnostics are your friend.** Every coordinator's raw payload is included in the diagnostics download. If something looks wrong, please attach a redacted diagnostics dump to the issue rather than describing the symptom — that gives the maintainer (human *or* AI) the actual API shape to compare against.
- **Energy dashboard cumulative sensors** are the most safety-critical. Cross-check the `*_total` values against what the Livoltek portal shows for the first few days before relying on them in cost calculations.
- **No write entities.** The integration is intentionally read-only, so the worst it can do is misreport a value. It cannot change inverter settings, dispatch the battery, or talk to the grid.

If you find a bug or a hallucinated field name, file an issue with diagnostics and the fix is usually a one-line edit in `sensor.py` — see the "Adding a new sensor" section in `AGENTS.md`.

---

## Comparison with `adamlonsdale/hass-livoltek`

The original [`adamlonsdale/hass-livoltek`](https://github.com/adamlonsdale/hass-livoltek) was the inspiration for this project and is the reason the Livoltek API surface was first mapped out for the Home Assistant community. Huge credit to Adam and contributors for that pioneering work.

`hai-livoltek` is an **independent rewrite** with different architectural goals:

| | adamlonsdale/hass-livoltek | hai-livoltek |
|---|---|---|
| Authorship | Human-maintained | AI-generated under human direction |
| API surface | Auto-generated OpenAPI client (`openapi.yaml` + `cli-gen.sh`) | Hand-written `aiohttp` client targeting only the endpoints we actually need |
| Endpoints | Public API only (`api-eu.livoltek-portal.com:8081`) | Public API for setup, **private API** (`evs.livoltek-portal.com`) for telemetry — many more fields, no port-8081 dependency for ongoing polling |
| Coordinators | Single coordinator | Three coordinators (60 s / 5 min / weekly) tuned to each data class's update cadence |
| Sensors | Core power & energy values | 60+ sensors including per-string PV, per-cell battery voltages, EPS, alarms, inverter settings (work mode / SOC limits / current limits / SOH) |
| Alarm tracking | Not exposed | Active-alarm count by severity, last-alarm code/description, 30-day rolling alarm log in diagnostics |
| Reliability | Standard coordinator behaviour | Exponential backoff `[60s, 2m, 5m, 10m]`, public-API fallback when private API is unreachable, automatic JWT refresh with 30-min buffer, per-coordinator startup jitter |
| Diagnostics | — | Redacted config + raw coordinator payloads + 30-day alarm log |
| Translations | English | English + Romanian |
| Write capability | — | None (intentional — API key is read-only on the backend) |

**Use `adamlonsdale/hass-livoltek` if** you prefer a human-maintained codebase with a smaller surface area, want to track the upstream OpenAPI-generated client, or are happy with the public API only.

**Use `hai-livoltek` if** you want the full per-string / per-cell / alarms / settings detail, want graceful degradation when the cloud is flaky, or run the integration on a network where you'd rather not depend on TCP port 8081 staying reachable for 24/7 polling — and you're comfortable with an AI-generated codebase that's documented for AI-assisted maintenance.

---

## Installation

### HACS (custom repository)

1. Open HACS in Home Assistant.
2. Go to **Integrations → ⋮ → Custom repositories**.
3. Add `https://github.com/your-repo/hai-livoltek` as type **Integration**.
4. Search for **Livoltek (hai)** and install.
5. Restart Home Assistant.

> Important: HACS will block installation if `adamlonsdale/hass-livoltek` is already installed (same `livoltek` domain). Remove the other integration first.

### Manual

1. Download the `custom_components/livoltek` folder from this repository.
2. Copy it to `<config>/custom_components/livoltek` on your Home Assistant instance, replacing any existing folder.
3. Restart Home Assistant.

---

## Getting your credentials

You will need three values from the Livoltek portal at <https://evs.livoltek-portal.com>:

### Security ID and Security Key

1. Log into the portal in a browser.
2. Click your account name in the top-right corner → **My Profile**.
3. Open the **Security ID** tab.
4. Copy the **Security ID** — this is your `secuid`.
5. Copy the **Security Key** — copy it *exactly*, including any trailing characters. The key sometimes includes invisible newline characters that the API requires; pasting from the portal preserves them.

### User Token

1. Stay in **My Profile**.
2. Open the **Generate Token** tab.
3. If no token is shown, click **Generate**.
4. Copy the full token string (it's a long JWT, valid until ~2027).

---

## Setup in Home Assistant

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Livoltek (hai)**.
3. Enter your Security ID, Security Key, and User Token.
4. The integration will auto-discover your site and inverter (or, if you have multiple sites, prompt you to choose one).
5. Click **Submit**.

A single device is created with all sensors grouped underneath.

---

## Entities

### Live values (updated every 60s)

PV: power, per-string power & voltage. Grid: power (negative = export, positive = import), voltage, frequency. Load: power, voltage. Battery: power (negative = discharging, positive = charging), voltage, current, SOC, max/min temperature, max/min cell voltage. Inverter: temperature. EPS: power, voltage. AC output power.

### Energy totals (updated every 60s, `total_increasing` for the Energy dashboard)

Per-period (today / month / lifetime) totals for: PV generation, grid export, grid import, battery charged, battery discharged, load consumption. Lifetime EPS energy.

### Status & alarms (updated every 5 minutes, refreshable via button)

PCS status, work status, smart-meter power, active alarm count by severity, last alarm code & description.

### Inverter settings (refreshed weekly, refreshable via button)

Work mode (self-use / back-up / feed-in first), discharge end SOC (grid + EPS), max charge/discharge current, battery SOH, warning SOC, grid feed power limit.

### Binary sensors

- **Online** (`connectivity` device class) — false when the inverter reports `pcsStatus = 3`.
- **Active alarm** (`problem` device class) — true when any **important** or **urgent** alarm is currently active. Attributes expose the per-severity counts.

### Buttons

- **Refresh inverter settings** — re-fetches all settings registers immediately (otherwise polled weekly).
- **Refresh status** — re-fetches alarms and status sensors immediately (otherwise polled every 5 min).

### Disabled by default

These are useful but rarely needed; enable individually from the entity list:

- CO₂ saved, generator state, firmware versions (ARM / DSP / BMS), battery serial number, battery capacity (kWh).

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
- A 30-day alarm log grouped by severity (Tips / Secondary / Important / Urgent), each entry showing time, code, description and active/cleared status.
- Coordinator health: last-update success, current backoff state, whether the public-API fallback is in use.

---

## Troubleshooting

**"Invalid auth"** — double-check the Security Key. The portal copies it with trailing whitespace/newlines that the API actually requires; if you typed it manually you probably truncated it.

**"Cannot connect"** — the public discovery endpoint runs on TCP port 8081 (`api-eu.livoltek-portal.com:8081`). This is open from a normal home network but blocked from many cloud/VPS firewalls. The integration must run on the same LAN as your inverter (or at least on a network with outbound port 8081 open).

**Sensors stuck at the same value** — check the **Active alarm** binary sensor first. If the inverter is offline (`Online` is `false`), data won't update. Otherwise, click the **Refresh status** button and download diagnostics to see the underlying coordinator state.

**"Token expired"** — handled automatically. Login tokens expire every ~2 hours and are refreshed pre-emptively. The user token is valid for years; if it's invalidated (e.g. you regenerated it on the portal), Home Assistant will surface a re-auth notification.

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

MIT.
