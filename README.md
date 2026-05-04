# SmartSake

Temperature monitoring and fan control system for koji fermentation, built on a Raspberry Pi 4B.

Reads six MAX31850K thermocouples, an SHT30 humidity sensor, and up to four HX711 load cells. Controls six relay-switched fans to hold temperature curves. Serves a real-time web dashboard over the local network.

<!-- ## Screenshots
Drop PNGs into `images/` and uncomment:
![Dashboard](images/dashboard.png)
![Mobile View](images/mobile.png)
![Curve Builder](images/curves.png)
-->

## About the Project

SmartSake is the controls and instrumentation half of a senior capstone project for the **University of Kentucky Biosystems & Agricultural Engineering** department (BAE 402/403, Fall 2025 – Spring 2026). The mechanical half — the koji table and load cell housings — is documented in `hardware/` and `docs/`.

**Team:** Benjamin Lin, Anastasia Myers, Makenna Hull, Natalie Cupples
**Advisor:** Dr. Alicia Modenbach

For background on the design — problem definition, constraints, alternatives considered, and economic justification — see [`docs/design-report.pdf`](docs/design-report.pdf).

## Safety

This system controls mains-voltage equipment near food-contact surfaces. Before powering anything on:

- **Electrical:** All 120 VAC wiring (relay → fan power) must be enclosed and follow [`docs/schematics/power-schematic.pdf`](docs/schematics/power-schematic.pdf). Do not energize with the relay board exposed. The Pi must be on a separate 5V supply, never tapped from the fan rail.
- **Thermal:** Fans run hot under sustained load. The auto fan-control loop has no upper bound — a misconfigured `tolerance_c` of 0 will keep fans on indefinitely. Always set a sane tolerance (1–2 °C) and check the dashboard during the first hour of every new run.
- **Food contact:** Only the load cell housing surface and the koji table top are food-contact. Use food-safe PETG or food-safe-coated PLA for the [STL parts](hardware/stl/), and sanitize between runs.
- **Watchdog:** The systemd watchdog (60 s) will restart the server if it hangs, but it does **not** force fans off. The fans hold their last commanded state during a server crash. If you need a hard cutoff, kill power at the relay board.
- **Emergency stop:** The dashboard's emergency-stop overrides all six zones to OFF until cleared. Use it before opening the table.

## Quickstart

If your Pi is wired and SSH-able:

```bash
git clone https://github.com/Sake-Team/SmartSake.git
cd SmartSake
git checkout ClaudeAgents
sudo bash scripts/onboarding.sh     # Phase 1: install packages, enable overlays
# --- Pi reboots here ---
sudo bash scripts/onboarding.sh     # Phase 2: map thermocouples, calibrate, start service
```

The setup wizard auto-detects which phase to run. After Phase 2, open `http://<pi-ip>:8080` and start a run.

For sensor setup without the full onboarding (e.g., after replacing a probe):

```bash
./setup.sh              # map thermocouples, verify sensors, start service
./setup.sh --remap      # re-run TC identification only
./setup.sh --verify     # check all sensors without starting
./setup.sh --status     # show sensor + service state
```

For manual setup or troubleshooting individual steps, see the full instructions below.

---

## Table of Contents

1. [Hardware Setup](#1-hardware-setup)
2. [Software Installation](#2-software-installation)
3. [Running SmartSake](#3-running-smartsake)
4. [Using the Dashboard](#4-using-the-dashboard)
5. [Hardware Files](#5-hardware-files)
6. [Documentation](#6-documentation)
7. [Troubleshooting](#7-troubleshooting)
8. [API Reference](#8-api-reference)
9. [Glossary](#9-glossary)
10. [Contributing](#10-contributing)
11. [License](#11-license)
12. [Acknowledgments](#12-acknowledgments)

---

## 1. Hardware Setup

### Parts List

Quick reference — for the full Bill of Materials with vendors, part numbers, and costs see [`BOM.md`](BOM.md).

| Component | Model | Qty |
|---|---|---|
| Single-board computer | Raspberry Pi 4B | 1 |
| Thermocouple amplifier | MAX31850K (1-Wire) | 6 |
| Humidity/temp sensor | SHT30 (I2C) | 1 |
| Relay board | SunFounder TS0012 (active-LOW, 8-ch) | 1 |
| Load cell amplifier | HX711 | 1-4 |
| Fans | 12V DC (one per zone) | up to 6 |

### GPIO Pin Map (BCM numbering)

```
Pin  2 ── SHT30 SDA (I2C)
Pin  3 ── SHT30 SCL (I2C)
Pin  4 ── 1-Wire data bus (all six MAX31850K)
Pin  5 ── HX711 Scale 1 DAT
Pin  6 ── HX711 Scale 1 CLK
Pin 12 ── HX711 Scale 2 DAT
Pin 13 ── HX711 Scale 2 CLK
Pin 16 ── HX711 Scale 3 DAT
Pin 17 ── Relay CH5 → Fan Zone 5
Pin 19 ── HX711 Scale 3 CLK
Pin 20 ── HX711 Scale 4 DAT
Pin 21 ── HX711 Scale 4 CLK
Pin 22 ── Relay CH4 → Fan Zone 4
Pin 23 ── Relay CH3 → Fan Zone 3
Pin 24 ── Relay CH2 → Fan Zone 2
Pin 25 ── Relay CH1 → Fan Zone 1
Pin 27 ── Relay CH6 → Fan Zone 6
```

> **Reserved pins:** 2/3 (I2C), 4 (1-Wire), 5/6/12/13/16/19/20/21 (HX711). Do not use these for relays.

### Relay Wiring (Active-LOW)

The SunFounder TS0012 is active-LOW:

- **GPIO LOW** = relay coil energized = fan **ON**
- **GPIO HIGH** = relay coil de-energized = fan **OFF**

All relay pins initialize HIGH at boot so fans start OFF.

### 1-Wire Bus (Thermocouples)

All six MAX31850K probes share a single 1-Wire bus on GPIO 4. Each probe has a unique device ID starting with `3b-` and appears at:

```
/sys/bus/w1/devices/3b-XXXXXXXXXXXXX/w1_slave
```

Enable the 1-Wire overlay in `/boot/config.txt`:

```
dtoverlay=w1-gpio,gpiopin=4
```

After wiring, run the probe identification script to assign each physical probe to a zone (1-6):

```bash
python3 scripts/identify_tcs.py
```

This creates `tc_zone_map.json` — the sensor loop will not start until all six zones are mapped.

### SHT30 (I2C)

The SHT30/SHT31 connects to the default I2C bus (pins 2/3). Enable I2C in `raspi-config` if not already on. No additional configuration needed.

### HX711 Load Cells

Scale 1 uses GPIO 5 (DAT) and GPIO 6 (CLK). Scales 2-4 are defined in `scale_config.json` but not wired by default. To calibrate:

```bash
python3 load_cell_hx711.py --calibrate --scale 1
```

To re-tare without full calibration:

```bash
python3 load_cell_hx711.py --tare --scale 1
```

---

## 2. Software Installation

### Prerequisites

```bash
# System packages
sudo apt update
sudo apt install python3 python3-pip git

# Python dependencies
pip3 install flask gunicorn

# Pi hardware libraries
pip3 install RPi.GPIO
pip3 install adafruit-blinka adafruit-circuitpython-sht31d
```

### Clone the Repo

```bash
cd ~
git clone https://github.com/Sake-Team/SmartSake.git
cd SmartSake
git checkout ClaudeAgents
```

### Configuration Files

Three JSON files control hardware mapping. They live in the project root:

**`tc_zone_map.json`** — Thermocouple-to-zone assignment (generated by `scripts/identify_tcs.py`):

```json
{
  "3b-000000000001": 1,
  "3b-000000000002": 2,
  "3b-000000000003": 3,
  "3b-000000000004": 4,
  "3b-000000000005": 5,
  "3b-000000000006": 6
}
```

**`zone_config.json`** — Per-zone temperature tolerance and calibration:

```json
{
  "default": { "tolerance_c": 1.0 },
  "zone1":   { "tolerance_c": 1.0, "setpoint_c": 32.0 },
  "zone2":   { "tolerance_c": 1.5 }
}
```

Optional per-zone fields: `setpoint_c`, `offset_c` (legacy single-point cal), `cal_slope` + `cal_intercept` (two-point cal).

**`scale_config.json`** — Load cell calibration and SHT30 offset:

```json
{
  "sensors": {
    "sht30_temp_offset_c": 0.0
  },
  "scales": {
    "1": {
      "dat_pin": 5,
      "clk_pin": 6,
      "tare_offset": 4166,
      "calibration_factor": 8000,
      "units": "kg",
      "label": "Scale 1"
    }
  }
}
```

### Install systemd Services

For production use on the Pi, install the systemd units so SmartSake starts on boot and auto-restarts on failure:

```bash
sudo bash systemd/install.sh
```

This installs and enables:

| Service | Purpose |
|---|---|
| `smartsake.service` | Flask server + sensor loop (port 8080) |
| `smartsake-sensors.service` | Standalone sensor loop (for multi-worker setups, conflicts with above) |
| `smartsake-backup.timer` | Database backup every 6 hours |

The main service runs as user `kojitable` with resource limits:

- **Memory:** 256 MB max
- **CPU:** 80% quota
- **Watchdog:** 60s (kills hung process)
- **tmpfs:** `/run/smartsake/` for volatile JSON files (protects SD card)

### Database

SQLite database (`smartsake.db`) is created automatically on first run. No manual setup needed.

---

## 3. Running SmartSake

### Start / Stop / Restart

The canonical way to manage SmartSake is through `restart.sh`, which detects whether systemd is installed and acts accordingly:

```bash
# Start or restart
./restart.sh

# Check status
./restart.sh --status

# Tail live logs
./restart.sh --logs
```

If systemd is installed, these map to:

```bash
sudo systemctl restart smartsake
sudo systemctl status smartsake
journalctl -u smartsake -f
```

If systemd is not installed (development mode), `restart.sh` runs `server.py` directly with `nohup`, logging to `server.log`.

### First Run

1. Wire all hardware and enable 1-Wire + I2C in `raspi-config`
2. Run `python3 scripts/identify_tcs.py` to create `tc_zone_map.json`
3. Run `python3 load_cell_hx711.py --calibrate --scale 1` (if using a scale)
4. Install services: `sudo bash systemd/install.sh`
5. Open `http://<pi-ip>:8080` in a browser

### Stopping the Server

```bash
# With systemd
sudo systemctl stop smartsake

# Without systemd (dev mode)
pkill -f "python.*server.py"
```

### Updating

Pull the latest code and restart:

```bash
cd ~/SmartSake
git pull
./restart.sh
```

### Development Mode (Windows/Mac)

A mock server is included for UI development without Pi hardware:

```bash
python mock_server.py
# Opens at http://localhost:8080 with simulated sensor data
```

All hardware imports degrade gracefully — `RPi.GPIO`, `adafruit_sht31d`, and HX711 log warnings and run as no-ops when unavailable.

---

## 4. Using the Dashboard

### Accessing SmartSake

Once running, open a browser to:

```
http://<pi-ip-address>:8080
```

Find the Pi's IP with `hostname -I` on the Pi, or check your router's device list.

### Pages

| Page | URL | Purpose |
|---|---|---|
| Home | `/` | Start/resume runs, view history |
| Dashboard | `/dashboard.html?run=N&mode=live` | Real-time temp, fan, and weight monitoring |
| Mobile | `/mobile.html` | Lightweight phone/tablet view |
| Calibration | `/calibration.html` | TC offset and load cell calibration |
| Curve Builder | `/curves.html` | Create/edit temperature reference curves |
| History | `/history.html?run=N` | Detailed run data table |
| Summary | `/summary.html?run=N` | Hourly temperature statistics |
| Room History | `/room-history.html` | Ambient environment over time |

### Home Page

The landing page shows:

- **Active run** — if a batch is in progress, with a "Resume" button to jump to the live dashboard
- **New Run** — name a batch and optionally attach a reference temperature curve
- **Tools** — links to Curve Builder, Calibration, Room History, and Mobile View
- **Previous Runs** — all completed/crashed batches with View, Summary, Details, Lock, and Delete actions

### Dashboard (TV / Desktop)

The main dashboard is designed for a wall-mounted display. It shows:

- **Six zone cards** — current temperature, fan state (ON/OFF badge), and deviation from target
- **Temperature chart** — live line graph updated every 10 seconds
- **Fan state indicators** — mode (limit/manual/rule), setpoint, trigger point, alarm level
- **Weight chart** — load cell readings updated every 30 seconds
- **Humidity/ambient** — SHT30 environment readings
- **Zone controls** — click any zone card to open fan overrides, rules, notes, and calibration

**Fan control modes:**

| Mode | How it works |
|---|---|
| Limit (auto) | Fans turn on when temp exceeds setpoint + tolerance, off when temp drops below setpoint |
| Manual override | Force a zone's fan ON or OFF regardless of temperature |
| Rules | Time-window or threshold-based triggers (configured per-run) |

### Mobile Dashboard (iPhone / iPad)

Navigate to `/mobile.html` or tap "Mobile View" from the home page.

Optimized for phones (375px and up):

- **2-column grid** of zone cards with large temperature numbers
- **Fan ON/OFF badges** per zone (green/gray)
- **Alarm states** — amber border for warnings, pulsing red for critical deviations
- **Environment bar** — humidity and ambient temp
- **Weight summary** — shown when a scale is active
- **Connection-lost banner** — appears after 3 failed fetches
- **Stale-data indicator** — timestamp turns amber if data is older than 60 seconds

The mobile page is read-only (monitoring only). For fan overrides and zone configuration, use the full dashboard.

To add SmartSake to your iPhone home screen as an app:

1. Open `http://<pi-ip>:8080/mobile.html` in Safari
2. Tap the Share button → "Add to Home Screen"
3. It will launch full-screen without Safari's address bar

### Polling Intervals

| Data | Backend interval | Frontend poll |
|---|---|---|
| Temperature (TC) | 10s | 10s |
| Humidity (SHT30) | 10s | 10s |
| Fan relay control | 10s | 10s |
| Weight (HX711) | 30s | 30s |
| Stage markers | on change | 30s |

---

## File Structure

```
SmartSake/
├── server.py              # Flask API server (starts sensor loop as background thread)
├── WriteSensors.py        # Sensor collection loop (standalone or via server.py)
├── db.py                  # SQLite database layer
├── fan_gpio.py            # GPIO relay abstraction
├── sensors.py             # 1-Wire and SHT30 helpers
├── load_cell_hx711.py     # HX711 ADC driver and calibration CLI
├── mock_server.py         # Simulated server for UI development
├── home.html              # Landing page
├── dashboard.html         # Main monitoring dashboard
├── dashboard-phase2.js    # Stage markers + weight analytics overlay
├── mobile.html            # Mobile-optimized dashboard
├── calibration.html       # TC and scale calibration UI
├── curves.html            # Reference curve builder
├── history.html           # Run detail table
├── summary.html           # Hourly stats for completed runs
├── room-history.html      # Ambient environment history
├── styles.css             # Shared theme and component styles
├── customize.js           # Theme customization (light/dark)
├── restart.sh             # Start/restart/status/logs helper
├── requirements.txt       # Python dependencies
├── tc_zone_map.json       # Probe-to-zone mapping (generated)
├── zone_config.json       # Per-zone tolerance and calibration
├── scale_config.json      # Load cell config and SHT30 offset
├── smartsake.db           # SQLite database (auto-created)
├── sensor_data.csv        # Rolling CSV log (~24 hrs)
├── scripts/
│   └── identify_tcs.py    # Interactive probe identification
├── systemd/
│   ├── smartsake.service          # Main service unit
│   ├── smartsake-sensors.service  # Standalone sensor unit
│   ├── smartsake-backup.service   # DB backup (oneshot)
│   ├── smartsake-backup.timer     # Backup schedule (every 6h)
│   └── install.sh                 # Service installer
├── images/
│   └── VoidSakeLogo.jpg   # Logo
├── hardware/
│   ├── bill-of-materials.xlsx
│   ├── cad/
│   │   ├── sake-table-drawing.pdf
│   │   ├── sake-table-exploded.png
│   │   └── load-cell-housing-drawing.pdf
│   └── stl/
│       ├── load-cell-housing.stl
│       └── load-cell-housing-lid.stl
└── docs/
    ├── design-report.pdf
    ├── standards-memo.pdf
    ├── brewing-flow-chart.png
    └── schematics/
        ├── koji-room-layout.png
        ├── power-schematic.pdf
        ├── wiring-schematic.pdf
        ├── signal-schematic.svg
        └── smartsake-wiring.json   # EasyEDA source (LV signal + 120 V power, 2 sheets)
```

---

## 5. Hardware Files

### Bill of Materials

[`hardware/bill-of-materials.xlsx`](hardware/bill-of-materials.xlsx) — full parts list with vendors, part numbers, quantities, and unit costs (Spring 2026 revision).

### CAD Drawings

| File | Description |
|---|---|
| [`hardware/cad/sake-table-drawing.pdf`](hardware/cad/sake-table-drawing.pdf) | Dimensioned drawing of the koji table (v3) |
| [`hardware/cad/sake-table-exploded.png`](hardware/cad/sake-table-exploded.png) | Exploded assembly view |
| [`hardware/cad/load-cell-housing-drawing.pdf`](hardware/cad/load-cell-housing-drawing.pdf) | Load cell housing dimensioned drawing |

### 3D-Printable Parts (STL)

| File | Description | Notes |
|---|---|---|
| [`hardware/stl/load-cell-housing.stl`](hardware/stl/load-cell-housing.stl) | Load cell housing body (v3) | PLA or PETG, 0.2mm layer, 30% infill |
| [`hardware/stl/load-cell-housing-lid.stl`](hardware/stl/load-cell-housing-lid.stl) | Load cell housing lid | Same settings as body |

The native Fusion 360 file (`SakeTableCAD.f3z`) is not included due to size; contact the team if you need the editable source.

---

## 6. Documentation

| File | Description |
|---|---|
| [`docs/design-report.pdf`](docs/design-report.pdf) | Full design report — problem, alternatives, final design, economic justification |
| [`docs/standards-memo.pdf`](docs/standards-memo.pdf) | Applicable engineering standards (food safety, electrical, fabrication) |
| [`docs/brewing-flow-chart.png`](docs/brewing-flow-chart.png) | Sake brewing process flow chart |
| [`docs/schematics/koji-room-layout.png`](docs/schematics/koji-room-layout.png) | Koji room physical layout |
| [`docs/schematics/power-schematic.pdf`](docs/schematics/power-schematic.pdf) | Power distribution schematic |
| [`docs/schematics/wiring-schematic.pdf`](docs/schematics/wiring-schematic.pdf) | Full wiring schematic (Pi → relays → fans → sensors) |
| [`docs/schematics/signal-schematic.svg`](docs/schematics/signal-schematic.svg) | Signal-level schematic (1-Wire, I2C, GPIO) |
| [`docs/schematics/smartsake-wiring.json`](docs/schematics/smartsake-wiring.json) | Editable EasyEDA source — Sheet 1: LV signal wiring (Pi GPIO → MAX31850K × 6, SHT30, HX711 × 4, relay logic). Sheet 2: 120 VAC power wiring (separate Pi-supply outlet, relay-switched fans, always-on dehumidifier). Open in EasyEDA Std Edition via *File → Open → Local*. |

---

## 7. Troubleshooting

### Dashboard won't load / can't reach Pi
- `ping <pi-ip>` — confirm the Pi is on the network
- On the Pi: `sudo ss -tlnp | grep 8080` — confirm something is listening
- `sudo systemctl status smartsake` — check for crash loops
- If the port is taken by a stale process: `pkill -f "python.*server.py"` then `./restart.sh`

### "Sensor loop will not start — tc_zone_map.json incomplete"
All six probes must be mapped before the loop runs. There is no auto-assignment.
```bash
python3 scripts/identify_tcs.py            # interactive — heat each probe in turn
python3 scripts/identify_tcs.py --check    # validate (no dupes, all 6 present)
sudo systemctl restart smartsake
```

### Probe shows N/A or wild jumps
- Check the 1-Wire bus on GPIO 4 — `ls /sys/bus/w1/devices/` should list six `3b-…` entries
- Loose probe wiring is the most common cause; reseat at the screw terminal
- Live monitor without writes: `python3 scripts/identify_tcs.py --monitor`

### Fan stuck ON or OFF
1. Check the dashboard zone card for an active manual override or rule — clear it
2. Verify relay logic is active-LOW (GPIO LOW = fan ON) — see `fan_gpio.py`
3. If a relay is mechanically stuck, hard-cycle the relay board's 5V supply
4. Use the dashboard's **emergency stop** to force all fans OFF, then diagnose

### Scale reads zero or drifts
- Re-tare without recalibrating: `python3 load_cell_hx711.py --tare --scale N`
- Full calibration with a known weight: `python3 load_cell_hx711.py --calibrate --scale N`
- Confirm DAT/CLK pins in `scale_config.json` match the wiring (defaults: 5/6 for scale 1)
- HX711 is sensitive to vibration and temperature swings; mount it rigidly

### "Database is locked"
Another process is writing. Stop everything and restart cleanly:
```bash
sudo systemctl stop smartsake smartsake-sensors
sudo systemctl start smartsake
```
The `-sensors` standalone unit conflicts with the main service — only one of them should be active.

### Hot-reload not picking up config changes
The server uses an mtime check. If you edit `zone_config.json` over SMB or with an editor that writes atomically (replacing the file), mtime updates correctly. If you `cat >` the file, mtime may not change — `touch zone_config.json` to force.

---

## 8. API Reference

All endpoints are JSON unless noted. Base URL is `http://<pi-ip>:8080`.

### Health & status
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Overall system health (sensor loop, DB, GPIO) |
| GET | `/api/sensor-status` | Sensor loop diagnostics, library availability, last-write age |
| GET | `/api/latest` | Latest TC + SHT30 + weight reading |
| GET | `/api/fan-state` | Latest fan state per zone (mode, setpoint, alarm) |

### Runs
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/runs` | List all runs |
| GET | `/api/runs/active` | Currently active run (if any) |
| POST | `/api/runs` | Start a new run — body: `{name, target_curve_id?}` |
| GET | `/api/runs/<id>` | Get run metadata |
| POST | `/api/runs/<id>/end` | Mark run completed |
| DELETE | `/api/runs/<id>` | Delete run (unless locked) |
| POST | `/api/runs/<id>/pin` | Lock/unlock run from deletion |
| POST | `/api/prune` | Free disk by deleting oldest unlocked runs |
| GET | `/api/runs/<id>/readings` | Full reading history |
| GET | `/api/runs/<id>/latest` | Latest reading for this run |
| GET | `/api/runs/<id>/summary` | Hourly temp stats per zone |
| GET | `/api/runs/<id>/export.csv` | Stream all readings as CSV |
| GET | `/api/runs/completed` | List completed runs |
| GET | `/api/room-history?hours=N` | Ambient SHT30 history |

### Targets, fans, zones
| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/api/runs/<id>/target` | Get/set per-run reference temperature curve |
| POST | `/api/runs/<id>/target/from-curve/<curve_id>` | Load a saved curve as the target |
| GET | `/api/runs/<id>/zones` | All per-zone notes |
| GET / PUT | `/api/runs/<id>/zones/<n>` | Get/save zone note |
| GET | `/api/runs/<id>/fan-overrides` | Active manual overrides |
| POST / DELETE | `/api/runs/<id>/zones/<n>/fan` | Set/clear manual override on zone N |
| POST | `/api/runs/<id>/emergency-stop` | Force all six zones OFF |
| GET / POST | `/api/runs/<id>/fan-rules` | List/create rule-based triggers |
| PATCH / DELETE | `/api/runs/<id>/fan-rules/<rule_id>` | Toggle/delete rule |
| GET | `/api/runs/<id>/deviations` | All deviation events |
| GET | `/api/runs/<id>/deviations/active` | Currently-active deviations |

### Reference curves
| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/api/reference-curves` | List/save curves |
| GET / DELETE | `/api/reference-curves/<id>` | Get/delete curve |
| POST | `/api/reference-curves/generate` | Build a curve from setpoints |
| POST | `/api/reference-curves/generate-from-csv` | Build a curve from a CSV upload |

### Calibration & config
| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/api/zone-config` | Read/write `zone_config.json` |
| GET | `/api/scale-config` | Read `scale_config.json` |
| POST | `/api/tc-calibration/<zone>` | Set per-zone TC offset directly |

Most write endpoints validate input (range checks on `tolerance_c`, `setpoint_c`, `offset_c`) and return `400` with an `error` key on failure. See `server.py` for exact validation rules.

---

## 9. Glossary

For readers without a brewing background:

| Term | Meaning |
|---|---|
| **Sake** | Japanese alcoholic beverage made by fermenting rice; requires koji to convert rice starches to sugars before yeast fermentation |
| **Koji** (麹) | Steamed rice inoculated with *Aspergillus oryzae* mold; produces enzymes that convert starch to sugar |
| **Kojikin** (麹菌) | The *A. oryzae* spores themselves, sprinkled onto cooled steamed rice to start a koji batch |
| **Koji-muro** | Traditional koji-making room — warm, humid, temperature-controlled. SmartSake automates this. |
| **Koji table** | The wooden/stainless table inside the koji-muro where rice ferments. Our table has six independently-controlled fan zones. |
| **Moromi** | The main fermentation mash — koji + steamed rice + yeast + water. Happens after koji is finished. |
| **Tane-kōji** | The "seed koji" — a previous batch of koji used to inoculate a new batch (alternative to dried kojikin) |
| **Zone** | One of the six independently-monitored regions of the koji table, each with its own thermocouple and fan |
| **Setpoint** | Target temperature for a zone, in °C |
| **Tolerance** | How far above setpoint the temp must go before the fan kicks on (typically 1–2 °C) |
| **Reference curve** | A planned temperature schedule over a 24–48 hr koji run; the dashboard plots actual vs. reference |

---

## 10. Contributing

This is an active capstone project. If you are on the team:

1. Work on the `ClaudeAgents` branch — `main` and `zany` are protected
2. Update [`README.md`](README.md) whenever you change wiring, GPIO assignments, config schemas, scripts, or systemd units (see [`CLAUDE.md`](CLAUDE.md) for the full rule set)
3. Hardware imports (`RPi.GPIO`, `adafruit_sht31d`, HX711) must degrade gracefully so the code still imports on a dev laptop — wrap in try/except and log a warning
4. Run the mock server locally before pushing changes that touch the dashboard:
   ```bash
   python archive/mock_server.py
   ```

For external contributors: please open an issue describing the change before submitting a PR.

---

## 11. License

This project is released for academic and educational use. Hardware designs (CAD, STL, schematics) and software are © 2025–2026 the SmartSake team. Reuse for non-commercial purposes is permitted with attribution. Contact the team for commercial licensing.

---

## 12. Acknowledgments

- **Dr. Alicia Modenbach** — capstone advisor, BAE Department
- **University of Kentucky Biosystems & Agricultural Engineering** — capstone program, lab space, fabrication resources
- **Open-source projects:** Flask, Adafruit CircuitPython libraries, Chart.js, the HX711 driver community
- **The brewing community** — for centuries of documented technique that made the temperature curves possible
