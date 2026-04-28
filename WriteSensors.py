import glob
import json
import time
import csv
import os
import signal
import threading
import board
import adafruit_sht31d
from datetime import datetime, timedelta
import db as sakedb
import fan_gpio

from sensors import discover_devices, read_temp_c, format_device_id, MAX_THERMOCOUPLES
from load_cell_hx711 import HX711, SAMPLES_PER_READ, load_scale_config, log_weight

CSV_FILE = "sensor_data.csv"
JSON_FILE = "sensor_latest.json"
MAX_CSV_ROWS = 43200  # ~24 hrs at 2-second interval

# SHT30 calibration offset (°C).  Positive = sensor reads too high; adjust until
# readings match a reference thermometer placed at the same location.
SHT30_TEMP_OFFSET_C = 0.0

# Shared state updated by background threads
weight_state = {
    1: {'kg': None, 'raw': None},
    2: {'kg': None, 'raw': None},
    3: {'kg': None, 'raw': None},
    4: {'kg': None, 'raw': None},
}
_active_run_id = None
_last_db_write_time: float = 0.0

# ── Deviation detection state ─────────────────────────────────────────────────
# Keys: (run_id, zone) → {breach_start: datetime, event_id: int|None, max_dev: float}
_deviation_tracking = {}

# Cached target profile per run to avoid a DB query every loop iteration
_cached_profile = {"run_id": None, "points": {}}  # points: {zone: [(elapsed_min, temp)]}

# Threshold rules: track when a breach started per (run_id, zone, rule_id)
_threshold_breach_start = {}

DEVIATION_THRESHOLD_C = 2.0   # °C above/below target to trigger
DEVIATION_HOLD_MIN    = 10.0  # minutes a breach must persist before logging

# ── Limit-switch fan-control constants ───────────────────────────────────────
DEADBAND_HOLD       = 3      # consecutive ticks required to change fan state (anti-chatter)
DEFAULT_TOLERANCE_C = 1.0    # °C above setpoint at which fan turns on

_BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
ZONE_CONFIG_FILE = os.path.join(_BASE_DIR, "zone_config.json")
FAN_STATE_JSON   = os.path.join(_BASE_DIR, "fan_state.json")
TC_ZONE_MAP_FILE = os.path.join(_BASE_DIR, "tc_zone_map.json")

# ── Zone config cache ─────────────────────────────────────────────────────────
_zone_cfg       = {}
_zone_cfg_mtime = 0.0

# ── Limit-switch runtime state (one per zone, reset on import) ────────────────
_fan_hold_counts = {z: 0     for z in range(1, 7)}
_fan_on          = {z: False for z in range(1, 7)}

# Mode/setpoint side-channel populated by evaluate_fan_state → _write_fan_state_json
_last_fan_mode     = {z: "none" for z in range(1, 7)}
_last_fan_setpoint = {z: None   for z in range(1, 7)}
_last_fan_trigger  = {z: None   for z in range(1, 7)}  # setpoint + tolerance


def _load_target_profile(run_id):
    """Return {zone: [(elapsed_min, temp_target)]} — one shared curve for all zones."""
    if _cached_profile["run_id"] == run_id:
        return _cached_profile["points"]
    rows = sakedb.get_target_profile(run_id)
    pts = [(r["elapsed_min"], r["temp_target"]) for r in rows if r["temp_target"] is not None]
    shared = {z: pts for z in range(1, 7)} if pts else {}
    _cached_profile["run_id"] = run_id
    _cached_profile["points"] = shared
    return shared


def _interp_target(profile_pts, elapsed_min):
    """Linearly interpolate target temp at elapsed_min. Clamps at edges."""
    if not profile_pts:
        return None
    if elapsed_min <= profile_pts[0][0]:
        return profile_pts[0][1]
    if elapsed_min >= profile_pts[-1][0]:
        return profile_pts[-1][1]
    for i in range(len(profile_pts) - 1):
        t0, v0 = profile_pts[i]
        t1, v1 = profile_pts[i + 1]
        if t0 <= elapsed_min <= t1:
            frac = (elapsed_min - t0) / (t1 - t0)
            return v0 + frac * (v1 - v0)
    return None


def _load_zone_config():
    """Load zone_config.json, reloading automatically when the file changes."""
    global _zone_cfg, _zone_cfg_mtime
    try:
        mtime = os.path.getmtime(ZONE_CONFIG_FILE)
        if mtime != _zone_cfg_mtime:
            with open(ZONE_CONFIG_FILE) as f:
                _zone_cfg = json.load(f)
            _zone_cfg_mtime = mtime
    except Exception:
        if not _zone_cfg:
            _zone_cfg = {"default": {"tolerance_c": DEFAULT_TOLERANCE_C}}
    return _zone_cfg


def _zone_tolerance(zone):
    cfg = _load_zone_config()
    default = cfg.get("default", {"tolerance_c": DEFAULT_TOLERANCE_C})
    return cfg.get(f"zone{zone}", default).get("tolerance_c", DEFAULT_TOLERANCE_C)


def evaluate_fan_state(run, tc_readings):
    """Return {zone: 'on'|'off'|None} based on overrides, rules, then limit-switch.

    Priority: manual override > enabled rules > limit-switch auto-control > None.
    For conflicting rules on the same zone, 'on' wins over 'off'.
    Side-effect: updates _last_fan_mode/_last_fan_setpoint/_last_fan_trigger.
    """
    global _fan_hold_counts, _fan_on
    global _last_fan_mode, _last_fan_setpoint, _last_fan_trigger

    run_id = run["id"]
    now = datetime.now()
    started_at = datetime.fromisoformat(run["started_at"])
    elapsed_min = (now - started_at).total_seconds() / 60

    result = {z: None for z in range(1, 7)}
    tc_map = {ch: temp for ch, temp in tc_readings}

    # 1. Manual overrides take priority
    overrides = sakedb.get_all_fan_overrides(run_id)
    override_zones = set()
    for zone, ov in overrides.items():
        result[zone] = ov["action"]
        override_zones.add(zone)

    # 2. Evaluate fan rules for non-overridden zones
    rules = sakedb.get_fan_rules(run_id)
    rule_zones = set()
    for rule in rules:
        if not rule["enabled"]:
            continue
        zone = rule["zone"]
        if zone in override_zones:
            continue

        fires = False
        if rule["rule_type"] == "time_window":
            fires = rule["elapsed_min_start"] <= elapsed_min < rule["elapsed_min_end"]

        elif rule["rule_type"] == "threshold":
            tc_val = tc_map.get(zone)
            if tc_val is not None:
                key = (run_id, zone, rule["id"])
                exceeds = (rule["threshold_dir"] == "above" and tc_val > rule["threshold_temp_c"]) or \
                          (rule["threshold_dir"] == "below" and tc_val < rule["threshold_temp_c"])
                if exceeds:
                    if key not in _threshold_breach_start:
                        _threshold_breach_start[key] = now
                    elapsed_breach = (now - _threshold_breach_start[key]).total_seconds() / 60
                    fires = elapsed_breach >= rule["threshold_dur_min"]
                else:
                    _threshold_breach_start.pop(key, None)

        if fires:
            # 'on' wins over 'off' for the same zone
            if result[zone] != "on":
                result[zone] = rule["fan_action"]
            rule_zones.add(zone)

    # 3. Limit-switch auto-control for zones with no override/rule and a target profile
    profile = _load_target_profile(run_id)
    for zone in range(1, 7):
        if zone in override_zones:
            _last_fan_mode[zone]     = "manual"
            _last_fan_setpoint[zone] = None
            _last_fan_trigger[zone]  = None
            continue

        if zone in rule_zones:
            _last_fan_mode[zone]     = "rule"
            _last_fan_setpoint[zone] = None
            _last_fan_trigger[zone]  = None
            continue

        pts    = profile.get(zone)
        actual = tc_map.get(zone)
        if pts is None or actual is None:
            _last_fan_mode[zone]     = "none"
            _last_fan_setpoint[zone] = None
            _last_fan_trigger[zone]  = None
            continue

        setpoint = _interp_target(pts, elapsed_min)
        if setpoint is None:
            _last_fan_mode[zone]     = "none"
            _last_fan_setpoint[zone] = None
            _last_fan_trigger[zone]  = None
            continue

        _last_fan_setpoint[zone] = round(setpoint, 2)

        tolerance = _zone_tolerance(zone)
        trigger   = setpoint + tolerance
        _last_fan_trigger[zone] = round(trigger, 2)

        # Limit switch with hysteresis
        current_on = _fan_on[zone]
        if actual > trigger:
            desired_on = True
        elif actual <= setpoint:
            desired_on = False
        else:
            desired_on = current_on  # hysteresis band — hold current state

        # Anti-chatter: require DEADBAND_HOLD consecutive ticks to change
        if desired_on == current_on:
            _fan_hold_counts[zone] = 0
            fan_on = desired_on
        else:
            _fan_hold_counts[zone] += 1
            if _fan_hold_counts[zone] >= DEADBAND_HOLD:
                fan_on = desired_on
                _fan_hold_counts[zone] = 0
            else:
                fan_on = current_on

        _fan_on[zone] = fan_on
        result[zone] = "on" if fan_on else "off"
        _last_fan_mode[zone] = "limit"

    return result


def _write_fan_state_json(fan_states):
    """Write fan_state.json with current states, modes, setpoints, and trigger temp."""
    zones = {}
    for z in range(1, 7):
        zones[str(z)] = {
            "state":    fan_states.get(z),
            "mode":     _last_fan_mode.get(z, "none"),
            "setpoint": _last_fan_setpoint.get(z),
            "trigger":  _last_fan_trigger.get(z),
        }
    data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "zones": zones,
    }
    try:
        with open(FAN_STATE_JSON, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Could not write fan_state.json: {e}")


def _load_tc_zone_map():
    try:
        with open(TC_ZONE_MAP_FILE) as f:
            raw = json.load(f)
        return {k: int(v) for k, v in raw.items()}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        print(f"Could not load tc_zone_map.json: {e}")
        return {}


def _save_tc_zone_map(mapping):
    try:
        tmp = TC_ZONE_MAP_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(mapping, f)
        os.replace(tmp, TC_ZONE_MAP_FILE)
    except Exception as e:
        print(f"Could not save tc_zone_map.json: {e}")


def check_deviations(run_id, tc_readings, run_started_at):
    """Detect temp deviations from the target profile and log structured events."""
    profile = _load_target_profile(run_id)
    if not profile:
        return  # No target profile — nothing to compare against

    now = datetime.now()
    started_at = datetime.fromisoformat(run_started_at)
    elapsed_min = (now - started_at).total_seconds() / 60
    tc_map = {ch: temp for ch, temp in tc_readings}

    for zone in range(1, 7):
        tc_val = tc_map.get(zone)
        pts = profile.get(zone)
        if tc_val is None or not pts:
            continue

        target = _interp_target(pts, elapsed_min)
        if target is None:
            continue

        diff = tc_val - target
        abs_diff = abs(diff)
        direction = "above" if diff > 0 else "below"
        key = (run_id, zone)
        tracking = _deviation_tracking.get(key)

        if abs_diff >= DEVIATION_THRESHOLD_C:
            if tracking is None:
                # Start tracking a new potential deviation
                _deviation_tracking[key] = {
                    "breach_start": now, "event_id": None, "max_dev": abs_diff
                }
            else:
                tracking["max_dev"] = max(tracking["max_dev"], abs_diff)
                if tracking["event_id"] is None:
                    elapsed_breach = (now - tracking["breach_start"]).total_seconds() / 60
                    if elapsed_breach >= DEVIATION_HOLD_MIN:
                        # Breach persisted long enough — create DB event
                        event_id = sakedb.create_deviation_event(
                            run_id, zone, tracking["breach_start"].isoformat(),
                            tracking["max_dev"], direction, DEVIATION_THRESHOLD_C
                        )
                        tracking["event_id"] = event_id
                else:
                    # Update max deviation on existing event
                    sakedb.update_deviation_max(tracking["event_id"], tracking["max_dev"])
        else:
            if tracking is not None:
                if tracking["event_id"] is not None:
                    sakedb.close_deviation_event(
                        tracking["event_id"], now.isoformat(), tracking["max_dev"]
                    )
                del _deviation_tracking[key]

def init_sht30():
    i2c = board.I2C()
    return adafruit_sht31d.SHT31D(i2c)

def read_sht30(sensor):
    return sensor.temperature - SHT30_TEMP_OFFSET_C, sensor.relative_humidity


# -----------------------------------------------
# HX711 LOAD CELL THREAD
# -----------------------------------------------
def run_hx711_thread(scale_id, hx_instance):
    """Background thread: reads HX711 every 0.5 s, updates weight_state[scale_id]."""
    cfg_units = "kg"
    try:
        with open(os.path.join(_BASE_DIR, "scale_config.json")) as f:
            _sc = json.load(f)
        cfg_units = _sc["scales"].get(str(scale_id), {}).get("units", "kg")
    except Exception:
        pass

    try:
        print(f"HX711 scale {scale_id} initialized.")
        while True:
            try:
                weight, raw_avg = hx_instance.get_weight(samples=SAMPLES_PER_READ, units=cfg_units)
                weight_state[scale_id]['kg'] = weight
                weight_state[scale_id]['raw'] = raw_avg
                log_weight(scale_id, weight, cfg_units)
            except Exception as e:
                print(f"HX711 scale {scale_id} read error: {e}")
            time.sleep(0.5)
    except Exception as e:
        print(f"HX711 scale {scale_id} thread failed: {e} -- running without scale")


# -----------------------------------------------
# DATA WRITERS
# -----------------------------------------------
def write_csv(timestamp, sht_temp, sht_humidity, tc_readings):
    """Append a row to the CSV file, rotating when MAX_CSV_ROWS is exceeded."""
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            headers = ["timestamp", "sht30_temp_c", "sht30_humidity_rh"]
            headers += [f"TC{ch}_temp_c" for ch, _ in tc_readings]
            writer.writerow(headers)
        row = [timestamp, sht_temp, sht_humidity]
        row += [f"{temp:.2f}" if temp is not None else "ERROR" for _, temp in tc_readings]
        writer.writerow(row)

    try:
        with open(CSV_FILE, "r") as f:
            line_count = sum(1 for _ in f)
        if line_count > MAX_CSV_ROWS:
            datestr = datetime.now().strftime("%Y%m%d_%H%M%S")
            os.rename(CSV_FILE, f"{CSV_FILE}.{datestr}.bak")
    except Exception as e:
        print(f"CSV rotation error: {e}")


def write_json(timestamp, sht_temp, sht_humidity, tc_readings):
    """Write latest readings to a JSON file for the HTML page to fetch."""
    data = {
        "timestamp": timestamp,
        "sht30": {
            "temp_c": round(sht_temp, 2) if sht_temp is not None else None,
            "humidity_rh": round(sht_humidity, 2) if sht_humidity is not None else None
        },
        "thermocouples": {
            f"TC{ch}": round(temp, 2) if temp is not None else None
            for ch, temp in tc_readings
        },
        "weight_kg_1": round(weight_state[1]['kg'], 4) if weight_state[1]['kg'] is not None else None,
        "weight_kg_2": round(weight_state[2]['kg'], 4) if weight_state[2]['kg'] is not None else None,
        "weight_kg_3": round(weight_state[3]['kg'], 4) if weight_state[3]['kg'] is not None else None,
        "weight_kg_4": round(weight_state[4]['kg'], 4) if weight_state[4]['kg'] is not None else None,
        "zones": {}
    }
    tmp_file = JSON_FILE + ".tmp"
    with open(tmp_file, "w") as f:
        json.dump(data, f)
    os.replace(tmp_file, JSON_FILE)


def _handle_shutdown(signum, frame):
    """Mark active run as completed on clean shutdown."""
    if _active_run_id is not None:
        try:
            sakedb.end_run(_active_run_id)
            print(f"Run {_active_run_id} marked as completed.")
        except Exception as e:
            print(f"Could not end run cleanly: {e}")
    fan_gpio.cleanup()
    raise SystemExit(0)


def _watchdog_thread():
    """Warn if DB writes stop during an active run."""
    while True:
        time.sleep(10)
        if _active_run_id is not None and _last_db_write_time > 0:
            age = time.time() - _last_db_write_time
            if age > 30:
                print(f"[WATCHDOG] WARNING: No DB write in {age:.0f}s during active run {_active_run_id}")


# -----------------------------------------------
# MAIN
# -----------------------------------------------
if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    # Init DB and resolve active run
    sakedb.init_db()
    stale = sakedb.get_active_run()
    if stale:
        sakedb.mark_crashed(stale["id"])
        print(f"Previous run '{stale['name']}' (id={stale['id']}) marked as crashed.")

    # Note: server.py handles the web server / API.
    # WriteSensors.py only collects sensor data.
    # Run server.py separately: python server.py
    print("Sensor collector started. Run 'python server.py' for the web interface.")
    print("A new run will be created via the web UI; sensor data will attach to it.")

    fan_gpio.init_fans()
    threading.Thread(target=_watchdog_thread, daemon=True).start()

    # Load scale config and spawn one thread per configured scale
    try:
        _scale_instances = load_scale_config()
    except Exception as _e:
        print(f"Could not load scale_config.json: {_e} -- running without scales")
        _scale_instances = {}
    for _sid, _hx in _scale_instances.items():
        _t = threading.Thread(target=run_hx711_thread, args=(_sid, _hx), daemon=True)
        _t.start()

    # Load SHT30 offset from config (overrides module-level default)
    global SHT30_TEMP_OFFSET_C
    try:
        with open(os.path.join(_BASE_DIR, "scale_config.json")) as _f:
            _cfg = json.load(_f)
        SHT30_TEMP_OFFSET_C = float(_cfg.get("sensors", {}).get("sht30_temp_offset_c", SHT30_TEMP_OFFSET_C))
    except Exception:
        pass

    # Initialize SHT30
    try:
        sht30 = init_sht30()
        print("SHT30 initialized.")
    except Exception as e:
        sht30 = None
        print(f"SHT30 init failed: {e}")

    device_id_to_channel = _load_tc_zone_map()
    next_channel = max(device_id_to_channel.values(), default=0) + 1

    while True:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        devices = discover_devices()

        for d in devices:
            device_id = format_device_id(d)
            if device_id not in device_id_to_channel:
                if next_channel <= MAX_THERMOCOUPLES:
                    device_id_to_channel[device_id] = next_channel
                    next_channel += 1
                    _save_tc_zone_map(device_id_to_channel)

        assigned = []
        for d in devices:
            device_id = format_device_id(d)
            ch = device_id_to_channel.get(device_id)
            if ch is not None and ch <= MAX_THERMOCOUPLES:
                assigned.append((ch, d))
        assigned.sort(key=lambda x: x[0])

        # Read SHT30
        sht_temp, sht_humidity = None, None
        if sht30:
            try:
                sht_temp, sht_humidity = read_sht30(sht30)
                print(f"SHT30 -- Temp: {sht_temp:.2f} °C | Humidity: {sht_humidity:.2f} %RH")
            except Exception as e:
                print(f"SHT30 -- ERROR ({e})")

        # Read thermocouples
        tc_readings = []
        for ch, d in assigned:
            try:
                temp_c = read_temp_c(d)
                tc_readings.append((ch, temp_c))
                print(f"TC{ch}: {temp_c:.2f} °C")
            except Exception as e:
                tc_readings.append((ch, None))
                print(f"TC{ch}: ERROR ({e})")

        for _sid in range(1, 5):
            if weight_state[_sid]['kg'] is not None:
                print(f"Scale {_sid}: {weight_state[_sid]['kg']:.3f} kg  (raw: {weight_state[_sid]['raw']:.0f})")

        write_csv(timestamp, sht_temp, sht_humidity, tc_readings)
        write_json(timestamp, sht_temp, sht_humidity, tc_readings)

        # Write to database if a run is active
        active = sakedb.get_active_run()
        if active:
            _active_run_id = active["id"]
            reading = {
                "recorded_at": timestamp,
                "sht_temp": round(sht_temp, 2) if sht_temp is not None else None,
                "humidity": round(sht_humidity, 2) if sht_humidity is not None else None,
            }
            for ch, temp in tc_readings:
                reading[f"tc{ch}"] = round(temp, 2) if temp is not None else None

            # Evaluate fan rules / PID and set GPIO
            try:
                fan_states = evaluate_fan_state(active, tc_readings)
                for zone, state in fan_states.items():
                    on = state == "on"
                    fan_gpio.set_fan(zone, on)
                    reading[f"fan{zone}"] = 1 if on else 0
                _write_fan_state_json(fan_states)
            except Exception as e:
                print(f"Fan rule evaluation failed: {e}")

            # Check for temperature deviations from target profile
            try:
                check_deviations(_active_run_id, tc_readings, active["started_at"])
            except Exception as e:
                print(f"Deviation check failed: {e}")

            for _sid in range(1, 5):
                _wkg = weight_state[_sid]['kg']
                reading[f"weight_lbs_{_sid}"] = round(_wkg * 2.20462, 4) if _wkg is not None else None
            reading["weight_lbs"] = reading["weight_lbs_1"]

            try:
                sakedb.insert_reading(_active_run_id, reading)
                _last_db_write_time = time.time()
            except Exception as e:
                print(f"DB write failed: {e}")

        print("-" * 40)
        time.sleep(2)
