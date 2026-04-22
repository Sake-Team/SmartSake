# Graph Report - SmartSake  (2026-04-22)

## Corpus Check
- Corpus is ~19,526 words - fits in a single context window. You may not need a graph.

## Summary
- 343 nodes · 453 edges · 16 communities detected
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 15 edges (avg confidence: 0.58)
- Token cost: 0 input · 0 output

## God Nodes (most connected - your core abstractions)
1. `get_conn()` - 47 edges
2. `HX711` - 24 edges
3. `_init()` - 11 edges
4. `Historian` - 9 edges
5. `_record()` - 8 edges
6. `_build_sensor_payload()` - 7 edges
7. `buildModal()` - 5 edges
8. `init()` - 5 edges
9. `evaluate_fan_state()` - 5 edges
10. `SmartSake Raspberry Pi Controls Reference` - 5 edges

## Surprising Connections (you probably didn't know these)
- `SmartSake` --references--> `VoidSake Logo`  [INFERRED]
  README.md → images/VoidSakeLogo.jpg
- `Return {zone: [(elapsed_min, temp_target)]} — one shared curve for all zones.` --uses--> `HX711`  [INFERRED]
  C:\Users\benja\Assistant\SmartSake\WriteSensors.py → load_cell_hx711.py
- `Linearly interpolate target temp at elapsed_min. Clamps at edges.` --uses--> `HX711`  [INFERRED]
  C:\Users\benja\Assistant\SmartSake\WriteSensors.py → load_cell_hx711.py
- `Load zone_config.json, reloading automatically when the file changes.` --uses--> `HX711`  [INFERRED]
  C:\Users\benja\Assistant\SmartSake\WriteSensors.py → load_cell_hx711.py
- `Return {zone: 'on'|'off'|None} based on overrides, rules, then limit-switch.` --uses--> `HX711`  [INFERRED]
  C:\Users\benja\Assistant\SmartSake\WriteSensors.py → load_cell_hx711.py

## Hyperedges (group relationships)
- **SmartSake Deployment Stack (systemd + Flask + Gunicorn)** — controls_service_server, requirements_flask, requirements_gunicorn [INFERRED 0.88]
- **SmartSake Sensor Hardware (HX711, SHT30, Thermocouple)** — graph_hx711, graph_community_thermocouple, controls_calibration [INFERRED 0.82]
- **SmartSake Project Core Files** — readme_smartsake, requirements_smartsake_deps, requirements_flask, image_voidsakelogo [INFERRED 0.85]

## Communities

### Community 0 - "Database Layer"
Cohesion: 0.05
Nodes (68): clear_fan_override(), close_deviation_event(), compute_pearson_r(), create_deviation_event(), create_fan_rule(), create_reference_curve(), create_run(), create_run_event() (+60 more)

### Community 1 - "Mock API Server"
Cohesion: 0.03
Nodes (16): api_fan_state(), api_latest(), _build_sensor_payload(), _fake_readings(), _fake_temp(), _koji_curve(), _now(), _oscillating_temp() (+8 more)

### Community 2 - "Production API Server"
Cohesion: 0.04
Nodes (9): api_fan_state(), api_get_zone_config(), api_latest(), api_save_zone_config(), SmartSake Flask server. Replaces the SimpleHTTPServer thread in WriteSensors.py, Returns the latest sensor readings in a flat format matching DB column names:, Return the latest fan_state.json written by the limit-switch fan-control loop., Return current zone tolerances from zone_config.json. (+1 more)

### Community 3 - "Load Cell & Weight Sensing"
Cohesion: 0.06
Nodes (31): calibrate(), HX711, load_scale_config(), log_weight(), Read weight. Returns (weight_value, raw_avg) — one read batch, two outputs., Load scale_config.json and return {scale_id: HX711_instance} for configured scal, Write current weight to JSON for main system integration., Bit-bang driver for the HX711 24-bit ADC (SparkFun SEN-13879). (+23 more)

### Community 4 - "Ops, Backup & Calibration"
Cohesion: 0.09
Nodes (24): GET /api/health Endpoint, backup_db.py Manual Backup Script, calibrate_sht30.py Script, Calibration Procedures (Load Cell + SHT30), Database Backup (Daily 2AM + Manual), Diagnostics Procedures, SmartSake GitHub Repo (ClaudeAgents branch), Initial Setup Procedure (+16 more)

### Community 5 - "Dashboard Phase 2 Viz"
Cohesion: 0.15
Nodes (13): _buildBandPlugin(), _buildToolbar(), _init(), _loadMarkers(), _loadTargets(), _poll(), _populateSelector(), _registerPlugin() (+5 more)

### Community 6 - "Thermocouple Reader"
Cohesion: 0.13
Nodes (12): Diagnostic tool for MAX31850K thermocouples + SHT30. Imports shared sensor helpe, Append a row of thermocouple readings to the CSV log., write_csv_row(), discover_devices(), init_sht30(), Shared sensor helpers for SmartSake Pi hardware., Initialize the SHT30 sensor over I2C., Read temperature and humidity from SHT30. (+4 more)

### Community 7 - "Run Historian"
Cohesion: 0.24
Nodes (2): Historian, Historian module for Raspberry Pi sake fermentation monitoring. Provides SQLite

### Community 8 - "Theme & UI Customization"
Cohesion: 0.42
Nodes (9): applyTheme(), buildCustomVars(), buildModal(), buildSwatchHTML(), closeModal(), init(), openModal(), shadeColor() (+1 more)

### Community 9 - "Hardware Test Suite"
Cohesion: 0.4
Nodes (8): _record(), test_all_scales(), test_all_zones(), test_database(), test_scale(), test_sht30(), test_thermocouples(), test_zone()

### Community 10 - "Fan GPIO Control"
Cohesion: 0.25
Nodes (8): cleanup(), init_fans(), fan_gpio.py — GPIO fan control abstraction for SmartSake.  GPIO pin numbers ar, Set up GPIO output pins for all configured fan zones., Drive the GPIO pin for a zone HIGH (on) or LOW (off).      Args:         zone, Release GPIO resources on shutdown., set_fan(), _try_import()

### Community 11 - "SHT30 Humidity Calibration"
Cohesion: 0.83
Nodes (3): main(), _read_direct(), _read_from_file()

### Community 12 - "Database Backup"
Cohesion: 1.0
Nodes (0): 

### Community 13 - "Limit Switch Fan Control"
Cohesion: 1.0
Nodes (1): PID Fan Control

### Community 14 - "Graph Report"
Cohesion: 1.0
Nodes (1): SmartSake Graph Report

### Community 15 - "Sensor Data Feed"
Cohesion: 1.0
Nodes (1): sensor_latest.json

## Knowledge Gaps
- **61 isolated node(s):** `Insert built-in koji reference curves if none exist yet.`, `data keys: tc1-tc6, sht_temp, humidity, fan1-fan6, weight_lbs, weight_lbs_1..4 (`, `Return up to max_points sensor readings from the last `hours` hours, across all`, `Return up to n evenly-strided readings for run_id.`, `rows: list of dicts with elapsed_min, temp_target` (+56 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Database Backup`** (2 nodes): `backup_db.py`, `run_backup()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Limit Switch Fan Control`** (2 nodes): `evaluate_fan_state()`, `PID Fan Control`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Graph Report`** (1 nodes): `SmartSake Graph Report`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Sensor Data Feed`** (1 nodes): `sensor_latest.json`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Are the 11 inferred relationships involving `HX711` (e.g. with `Return {zone: [(elapsed_min, temp_target)]} — one shared curve for all zones.` and `Linearly interpolate target temp at elapsed_min. Clamps at edges.`) actually correct?**
  _`HX711` has 11 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Insert built-in koji reference curves if none exist yet.`, `data keys: tc1-tc6, sht_temp, humidity, fan1-fan6, weight_lbs, weight_lbs_1..4 (`, `Return up to max_points sensor readings from the last `hours` hours, across all` to the rest of the system?**
  _61 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Database Layer` be split into smaller, more focused modules?**
  _Cohesion score 0.05 - nodes in this community are weakly interconnected._
- **Should `Mock API Server` be split into smaller, more focused modules?**
  _Cohesion score 0.03 - nodes in this community are weakly interconnected._
- **Should `Production API Server` be split into smaller, more focused modules?**
  _Cohesion score 0.04 - nodes in this community are weakly interconnected._
- **Should `Load Cell & Weight Sensing` be split into smaller, more focused modules?**
  _Cohesion score 0.06 - nodes in this community are weakly interconnected._
- **Should `Ops, Backup & Calibration` be split into smaller, more focused modules?**
  _Cohesion score 0.09 - nodes in this community are weakly interconnected._