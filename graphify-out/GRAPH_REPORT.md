# Graph Report - .  (2026-05-02)

## Corpus Check
- Corpus is ~25,976 words - fits in a single context window. You may not need a graph.

## Summary
- 503 nodes · 708 edges · 20 communities detected
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 43 edges (avg confidence: 0.67)
- Token cost: 0 input · 0 output

## God Nodes (most connected - your core abstractions)
1. `get_conn()` - 52 edges
2. `HX711` - 33 edges
3. `start_sensor_loop()` - 16 edges
4. `_init()` - 11 edges
5. `server.py â€” Flask API Server` - 10 edges
6. `Historian` - 9 edges
7. `_record()` - 8 edges
8. `evaluate_fan_state()` - 8 edges
9. `Diagnostics Commands (CONTROLS)` - 8 edges
10. `_read_json_cached()` - 8 edges

## Surprising Connections (you probably didn't know these)
- `SmartSake` --references--> `VoidSake Logo`  [INFERRED]
  README.md → images/VoidSakeLogo.jpg
- `HX711` --uses--> `Return {zone: [(elapsed_min, temp_target)]} — one shared curve for all zones.`  [INFERRED]
  C:\Users\benja\Assistant\SmartSake\load_cell_hx711.py → C:\Users\benja\Assistant\SmartSake\WriteSensors.py
- `HX711` --uses--> `Linearly interpolate target temp at elapsed_min. Clamps at edges.`  [INFERRED]
  C:\Users\benja\Assistant\SmartSake\load_cell_hx711.py → C:\Users\benja\Assistant\SmartSake\WriteSensors.py
- `HX711` --uses--> `Load zone_config.json, reloading automatically when the file changes.`  [INFERRED]
  C:\Users\benja\Assistant\SmartSake\load_cell_hx711.py → C:\Users\benja\Assistant\SmartSake\WriteSensors.py
- `HX711` --uses--> `Return {zone: 'on'|'off'|None} based on overrides, rules, then limit-switch.`  [INFERRED]
  C:\Users\benja\Assistant\SmartSake\load_cell_hx711.py → C:\Users\benja\Assistant\SmartSake\WriteSensors.py

## Communities

### Community 0 - "Database Layer"
Cohesion: 0.03
Nodes (103): _bucket_average_one_source(), clear_fan_override(), close_conn(), close_deviation_event(), _combine_buckets(), compute_pearson_r(), create_deviation_event(), create_fan_rule() (+95 more)

### Community 1 - "Flask API Routes"
Cohesion: 0.03
Nodes (48): api_calibrate_tc_from_reference(), api_calibrate_tc_two_point(), api_emergency_stop(), api_fan_state(), api_generate_curve_from_csv(), api_get_scale_config(), api_get_zone_config(), api_health() (+40 more)

### Community 2 - "HX711 Scale Driver"
Cohesion: 0.05
Nodes (55): Exception, calibrate(), HX711, load_scale_config(), log_weight(), Read weight. Returns (weight_value, raw_avg) — one read batch, two outputs., Load scale_config.json and return {scale_id: HX711_instance} for configured scal, Write current weight to JSON for main system integration. (+47 more)

### Community 3 - "Server Endpoints"
Cohesion: 0.03
Nodes (16): api_fan_state(), api_latest(), _build_sensor_payload(), _fake_readings(), _fake_temp(), _koji_curve(), _now(), _oscillating_temp() (+8 more)

### Community 4 - "Project Config & Rules"
Cohesion: 0.06
Nodes (41): Active-LOW Relay Convention (CLAUDE.md), Branch Rules (ClaudeAgents Active), Config Hot-Reload by Mtime Rationale, README Maintenance Rule, GET /api/health Endpoint, calibrate_sht30.py â€” SHT30 Calibration Script, calibrate_sht30.py Script, Calibration Procedures (CONTROLS) (+33 more)

### Community 5 - "Backup & Code Standards"
Cohesion: 0.08
Nodes (31): Coding Standards, Code Generation Delegation Rationale, Graceful Hardware Degradation Rationale, backup_db.py â€” Database Backup Script, backup_db.py Manual Backup Script, Database Backup Procedure, Database Backup (Daily 2AM + Manual), SmartSake GitHub Repo (ClaudeAgents branch) (+23 more)

### Community 6 - "TC Probe Identification"
Cohesion: 0.1
Nodes (21): cmd_assign(), cmd_check(), cmd_monitor(), main(), Return {device_id: temp_c or None} for every device on the bus., _read_all(), Diagnostic tool for MAX31850K thermocouples + SHT30. Imports shared sensor help, Append a row of thermocouple readings to the CSV log. (+13 more)

### Community 7 - "Dashboard Phase 2"
Cohesion: 0.15
Nodes (13): _buildBandPlugin(), _buildToolbar(), _init(), _loadMarkers(), _loadTargets(), _poll(), _populateSelector(), _registerPlugin() (+5 more)

### Community 8 - "Historian Archive"
Cohesion: 0.24
Nodes (2): Historian, Historian module for Raspberry Pi sake fermentation monitoring. Provides SQLite

### Community 9 - "Theme Customization"
Cohesion: 0.42
Nodes (9): applyTheme(), buildCustomVars(), buildModal(), buildSwatchHTML(), closeModal(), init(), openModal(), shadeColor() (+1 more)

### Community 10 - "Hardware Test Suite"
Cohesion: 0.4
Nodes (8): _record(), test_all_scales(), test_all_zones(), test_database(), test_scale(), test_sht30(), test_thermocouples(), test_zone()

### Community 11 - "Fan GPIO Control"
Cohesion: 0.25
Nodes (8): cleanup(), init_fans(), fan_gpio.py — GPIO fan control abstraction for SmartSake.  GPIO pin numbers ar, Set up GPIO output pins for all configured fan zones., Drive the GPIO pin for a zone HIGH (on) or LOW (off).      Args:         zone, Release GPIO resources on shutdown., set_fan(), _try_import()

### Community 12 - "DB Backup Operations"
Cohesion: 0.5
Nodes (2): Auto-prune oldest unlocked runs when disk space is low., run_prune()

### Community 13 - "SHT30 Calibration"
Cohesion: 0.83
Nodes (3): main(), _read_direct(), _read_from_file()

### Community 14 - "Fan Control Evaluation"
Cohesion: 1.0
Nodes (1): PID Fan Control

### Community 15 - "Smoke Test"
Cohesion: 1.0
Nodes (2): Hardware Smoke Test, test_hardware.py â€” Hardware Test Script

### Community 16 - "SmartSake System"
Cohesion: 1.0
Nodes (1): SmartSake Graph Report

### Community 17 - "Sensor JSON Output"
Cohesion: 1.0
Nodes (1): sensor_latest.json

### Community 18 - "Run History Page"
Cohesion: 1.0
Nodes (1): history.html â€” Run Detail Table

### Community 19 - "Run Summary Page"
Cohesion: 1.0
Nodes (1): summary.html â€” Hourly Stats

## Knowledge Gaps
- **122 isolated node(s):** `Insert built-in koji reference curves if none exist yet.`, `data keys: tc1-tc6, sht_temp, humidity, fan1-fan6, weight_lbs, weight_lbs_1..4 (`, `Return up to n evenly-strided readings for run_id.`, `rows: list of dicts with elapsed_min, temp_target`, `Return the active override for a zone, or None if absent/expired.` (+117 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Fan Control Evaluation`** (2 nodes): `evaluate_fan_state()`, `PID Fan Control`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Smoke Test`** (2 nodes): `Hardware Smoke Test`, `test_hardware.py â€” Hardware Test Script`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `SmartSake System`** (1 nodes): `SmartSake Graph Report`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Sensor JSON Output`** (1 nodes): `sensor_latest.json`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Run History Page`** (1 nodes): `history.html â€” Run Detail Table`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Run Summary Page`** (1 nodes): `summary.html â€” Hourly Stats`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Are the 20 inferred relationships involving `HX711` (e.g. with `Return {zone: [(elapsed_min, temp_target)]} — one shared curve for all zones.` and `Linearly interpolate target temp at elapsed_min. Clamps at edges.`) actually correct?**
  _`HX711` has 20 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `server.py â€” Flask API Server` (e.g. with `flask>=3.0 Dependency` and `db.py â€” SQLite Database Layer`) actually correct?**
  _`server.py â€” Flask API Server` has 5 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Insert built-in koji reference curves if none exist yet.`, `data keys: tc1-tc6, sht_temp, humidity, fan1-fan6, weight_lbs, weight_lbs_1..4 (`, `Return up to n evenly-strided readings for run_id.` to the rest of the system?**
  _122 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Database Layer` be split into smaller, more focused modules?**
  _Cohesion score 0.03 - nodes in this community are weakly interconnected._
- **Should `Flask API Routes` be split into smaller, more focused modules?**
  _Cohesion score 0.03 - nodes in this community are weakly interconnected._
- **Should `HX711 Scale Driver` be split into smaller, more focused modules?**
  _Cohesion score 0.05 - nodes in this community are weakly interconnected._
- **Should `Server Endpoints` be split into smaller, more focused modules?**
  _Cohesion score 0.03 - nodes in this community are weakly interconnected._