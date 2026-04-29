import glob
import json
import time
import csv
import os
import signal
import threading
from datetime import datetime, timedelta

import db as sakedb
import fan_gpio

# ── Hardware library imports — guarded so missing libs log a warning and degrade
# gracefully rather than crashing the whole process. ──────────────────────────
try:
    from sensors import discover_devices, read_temp_c, format_device_id, MAX_THERMOCOUPLES
    _TC_AVAILABLE = True
except ImportError as _e:
    print(f"[WARN] Thermocouple libs not available ({_e}) — TC readings disabled")
    _TC_AVAILABLE = False
    MAX_THERMOCOUPLES = 6
    def discover_devices(): return []
    def read_temp_c(d): raise RuntimeError("TC libs not loaded")
    def format_device_id(d): return d

try:
    from load_cell_hx711 import HX711, SAMPLES_PER_READ, load_scale_config, log_weight
    _HX_AVAILABLE = True
except ImportError as _e:
    print(f"[WARN] HX711 libs not available ({_e}) — scale readings disabled")
    _HX_AVAILABLE = False
    def load_scale_config(**kw): return {}

try:
    import board as _board
    import adafruit_sht31d as _adafruit_sht31d
    _SHT_AVAILABLE = True
except ImportError as _e:
    print(f"[WARN] SHT30 libs not available ({_e}) — humidity/env probe disabled")
    _SHT_AVAILABLE = False


CSV_FILE = "sensor_data.csv"
JSON_FILE = "sensor_latest.json"
MAX_CSV_ROWS = 43200  # ~24 hrs at 2-second interval

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
_deviation_tracking = {}
_cached_profile = {"run_id": None, "points": {}, "loaded_at": 0.0}
_PROFILE_CACHE_TTL = 60.0  # re-read DB if curve is swapped mid-run
_threshold_breach_start = {}

DEVIATION_THRESHOLD_C = 2.0
DEVIATION_HOLD_MIN    = 10.0

# ── Limit-switch fan-control constants ───────────────────────────────────────
DEADBAND_HOLD       = 3
DEFAULT_TOLERANCE_C = 1.0

_BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
ZONE_CONFIG_FILE = os.path.join(_BASE_DIR, "zone_config.json")
FAN_STATE_JSON   = os.path.join(_BASE_DIR, "fan_state.json")
TC_ZONE_MAP_FILE = os.path.join(_BASE_DIR, "tc_zone_map.json")

_zone_cfg       = {}
_zone_cfg_mtime = 0.0

_fan_hold_counts = {z: 0     for z in range(1, 7)}
_fan_on          = {z: False for z in range(1, 7)}
_last_fan_mode     = {z: "none" for z in range(1, 7)}
_last_fan_setpoint = {z: None   for z in range(1, 7)}
_last_fan_trigger  = {z: None   for z in range(1, 7)}


def _load_target_profile(run_id):
    now = time.time()
    if (_cached_profile["run_id"] == run_id and
            (now - _cached_profile["loaded_at"]) < _PROFILE_CACHE_TTL):
        return _cached_profile["points"]
    rows = sakedb.get_target_profile(run_id)
    pts = [(r["elapsed_min"], r["temp_target"]) for r in rows if r["temp_target"] is not None]
    shared = {z: pts for z in range(1, 7)} if pts else {}
    _cached_profile["run_id"] = run_id
    _cached_profile["points"] = shared
    _cached_profile["loaded_at"] = now
    return shared


def _interp_target(profile_pts, elapsed_min):
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


def _zone_setpoint_override(zone):
    cfg = _load_zone_config()
    v = cfg.get(f"zone{zone}", {}).get("setpoint_c")
    if v is None:
        v = cfg.get("default", {}).get("setpoint_c")
    return float(v) if v is not None else None


def evaluate_fan_state(run, tc_readings):
    global _fan_hold_counts, _fan_on
    global _last_fan_mode, _last_fan_setpoint, _last_fan_trigger

    run_id = run["id"]
    now = datetime.now()
    started_at = datetime.fromisoformat(run["started_at"])
    elapsed_min = (now - started_at).total_seconds() / 60

    result = {z: None for z in range(1, 7)}
    tc_map = {ch: temp for ch, temp in tc_readings}

    overrides = sakedb.get_all_fan_overrides(run_id)
    override_zones = set()
    for zone, ov in overrides.items():
        result[zone] = ov["action"]
        override_zones.add(zone)

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
            if result[zone] != "on":
                result[zone] = rule["fan_action"]
            rule_zones.add(zone)

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
        if actual is None:
            _last_fan_mode[zone]     = "none"
            _last_fan_setpoint[zone] = None
            _last_fan_trigger[zone]  = None
            continue

        setpoint = _interp_target(pts, elapsed_min) if pts else None
        if setpoint is None:
            setpoint = _zone_setpoint_override(zone)
        if setpoint is None:
            _last_fan_mode[zone]     = "none"
            _last_fan_setpoint[zone] = None
            _last_fan_trigger[zone]  = None
            continue

        _last_fan_setpoint[zone] = round(setpoint, 2)

        tolerance = _zone_tolerance(zone)
        trigger   = setpoint + tolerance
        _last_fan_trigger[zone] = round(trigger, 2)

        current_on = _fan_on[zone]
        if actual > trigger:
            desired_on = True
        elif actual <= setpoint:
            desired_on = False
        else:
            desired_on = current_on

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


class TCZoneMapError(Exception):
    """Raised when tc_zone_map.json is missing, malformed, or fails validation."""


def _validate_tc_zone_map(mapping):
    """Enforce static 1..6 zone assignments with no duplicates and no gaps."""
    if not isinstance(mapping, dict) or not mapping:
        raise TCZoneMapError(
            f"tc_zone_map.json is empty. Run scripts/identify_tcs.py to "
            f"assign each probe to a fixed zone (1..6) before starting."
        )

    channels = []
    for device_id, ch in mapping.items():
        if not isinstance(device_id, str) or not device_id.startswith("3b-"):
            raise TCZoneMapError(f"Invalid device id {device_id!r} (expected '3b-...').")
        if not isinstance(ch, int) or ch < 1 or ch > MAX_THERMOCOUPLES:
            raise TCZoneMapError(
                f"Invalid channel {ch!r} for {device_id} "
                f"(must be int in 1..{MAX_THERMOCOUPLES})."
            )
        channels.append(ch)

    dupes = {c for c in channels if channels.count(c) > 1}
    if dupes:
        raise TCZoneMapError(f"Duplicate zone assignments: {sorted(dupes)}.")

    missing = sorted(set(range(1, MAX_THERMOCOUPLES + 1)) - set(channels))
    if missing:
        raise TCZoneMapError(
            f"Missing zones {missing}. All zones 1..{MAX_THERMOCOUPLES} must be assigned. "
            f"Run scripts/identify_tcs.py to (re)build the map."
        )


def _load_tc_zone_map():
    """Load and validate the static probe-to-zone map. Raises TCZoneMapError on any issue."""
    try:
        with open(TC_ZONE_MAP_FILE) as f:
            raw = json.load(f)
    except FileNotFoundError:
        raise TCZoneMapError(
            f"tc_zone_map.json not found at {TC_ZONE_MAP_FILE}. "
            f"Run scripts/identify_tcs.py to create it."
        )
    except json.JSONDecodeError as e:
        raise TCZoneMapError(f"tc_zone_map.json is not valid JSON: {e}")

    try:
        mapping = {k: int(v) for k, v in raw.items()}
    except (TypeError, ValueError) as e:
        raise TCZoneMapError(f"tc_zone_map.json contains non-integer channel values: {e}")

    _validate_tc_zone_map(mapping)
    return mapping


def check_deviations(run_id, tc_readings, run_started_at):
    profile = _load_target_profile(run_id)
    if not profile:
        return

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
                _deviation_tracking[key] = {
                    "breach_start": now, "event_id": None, "max_dev": abs_diff
                }
            else:
                tracking["max_dev"] = max(tracking["max_dev"], abs_diff)
                if tracking["event_id"] is None:
                    elapsed_breach = (now - tracking["breach_start"]).total_seconds() / 60
                    if elapsed_breach >= DEVIATION_HOLD_MIN:
                        event_id = sakedb.create_deviation_event(
                            run_id, zone, tracking["breach_start"].isoformat(),
                            tracking["max_dev"], direction, DEVIATION_THRESHOLD_C
                        )
                        tracking["event_id"] = event_id
                else:
                    sakedb.update_deviation_max(tracking["event_id"], tracking["max_dev"])
        else:
            if tracking is not None:
                if tracking["event_id"] is not None:
                    sakedb.close_deviation_event(
                        tracking["event_id"], now.isoformat(), tracking["max_dev"]
                    )
                del _deviation_tracking[key]


def init_sht30():
    if not _SHT_AVAILABLE:
        return None
    i2c = _board.I2C()
    return _adafruit_sht31d.SHT31D(i2c)


def read_sht30(sensor):
    return sensor.temperature - SHT30_TEMP_OFFSET_C, sensor.relative_humidity


def run_hx711_thread(scale_id, hx_instance):
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


def write_csv(timestamp, sht_temp, sht_humidity, tc_readings):
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


def _watchdog_thread():
    while True:
        time.sleep(10)
        if _active_run_id is not None and _last_db_write_time > 0:
            age = time.time() - _last_db_write_time
            if age > 30:
                print(f"[WATCHDOG] WARNING: No DB write in {age:.0f}s during active run {_active_run_id}")


def _handle_shutdown(signum, frame):
    if _active_run_id is not None:
        try:
            sakedb.end_run(_active_run_id)
            print(f"Run {_active_run_id} marked as completed.")
        except Exception as e:
            print(f"Could not end run cleanly: {e}")
    fan_gpio.cleanup()
    raise SystemExit(0)


def start_sensor_loop():
    """Initialize hardware and run the sensor collection loop forever.

    Can be called from server.py in a background thread, or directly
    when WriteSensors.py is run standalone.  Does NOT call db.init_db()
    (server.py already does that before starting this thread).
    """
    global SHT30_TEMP_OFFSET_C, _active_run_id

    print("[sensors] Sensor loop starting...")
    fan_gpio.init_fans()
    threading.Thread(target=_watchdog_thread, daemon=True).start()

    # Start one HX711 thread per configured scale
    _scale_instances = {}
    if _HX_AVAILABLE:
        try:
            _scale_instances = load_scale_config()
        except Exception as e:
            print(f"[sensors] Could not load scale_config.json: {e} — running without scales")
    for _sid, _hx in _scale_instances.items():
        threading.Thread(target=run_hx711_thread, args=(_sid, _hx), daemon=True).start()

    # Load SHT30 calibration offset from scale_config.json
    try:
        with open(os.path.join(_BASE_DIR, "scale_config.json")) as _f:
            _cfg = json.load(_f)
        SHT30_TEMP_OFFSET_C = float(
            _cfg.get("sensors", {}).get("sht30_temp_offset_c", SHT30_TEMP_OFFSET_C)
        )
    except Exception:
        pass

    # Initialize SHT30
    sht30 = None
    try:
        sht30 = init_sht30()
        if sht30:
            print("[sensors] SHT30 initialized.")
        else:
            print("[sensors] SHT30 disabled (library not available).")
    except Exception as e:
        print(f"[sensors] SHT30 init failed: {e} — continuing without humidity sensor")

    try:
        device_id_to_channel = _load_tc_zone_map()
        print(f"[sensors] Loaded static TC zone map: "
              f"{', '.join(f'{cid[:8]}…→z{ch}' for cid, ch in sorted(device_id_to_channel.items(), key=lambda x: x[1]))}")
    except TCZoneMapError as e:
        print(f"[sensors] FATAL: {e}")
        print("[sensors] Sensor loop will not run until the map is fixed.")
        return

    _warned_unknown_ids = set()

    print("[sensors] Entering read loop (2 s interval).")
    while True:
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            devices = discover_devices()

            for d in devices:
                device_id = format_device_id(d)
                if device_id not in device_id_to_channel and device_id not in _warned_unknown_ids:
                    print(f"[sensors] WARN: unknown thermocouple {device_id} present on bus "
                          f"but not in tc_zone_map.json — ignoring. Run scripts/identify_tcs.py "
                          f"to remap if a probe was replaced.")
                    _warned_unknown_ids.add(device_id)

            assigned = sorted(
                [(device_id_to_channel[format_device_id(d)], d)
                 for d in devices
                 if format_device_id(d) in device_id_to_channel],
                key=lambda x: x[0]
            )

            seen_zones = {ch for ch, _ in assigned}
            missing_zones = sorted(set(range(1, MAX_THERMOCOUPLES + 1)) - seen_zones)
            if missing_zones:
                # Surface — but don't crash — a probe disconnect mid-run.
                key = tuple(missing_zones)
                if key not in _warned_unknown_ids:
                    print(f"[sensors] WARN: zones {missing_zones} expected but no matching "
                          f"probe present on the 1-Wire bus — readings will be None.")
                    _warned_unknown_ids.add(key)

            # Read SHT30
            sht_temp, sht_humidity = None, None
            if sht30:
                try:
                    sht_temp, sht_humidity = read_sht30(sht30)
                except Exception as e:
                    print(f"[sensors] SHT30 read error: {e}")

            # Read thermocouples
            tc_readings = []
            for ch, d in assigned:
                try:
                    temp_c = read_temp_c(d)
                    tc_readings.append((ch, temp_c))
                except Exception as e:
                    tc_readings.append((ch, None))
                    print(f"[sensors] TC{ch} read error: {e}")

            write_csv(timestamp, sht_temp, sht_humidity, tc_readings)
            write_json(timestamp, sht_temp, sht_humidity, tc_readings)

            # DB write — only when a run is active
            active = sakedb.get_active_run()
            if active:
                _active_run_id = active["id"]
                reading = {
                    "recorded_at": timestamp,
                    "sht_temp":  round(sht_temp,    2) if sht_temp    is not None else None,
                    "humidity":  round(sht_humidity, 2) if sht_humidity is not None else None,
                }
                for ch, temp in tc_readings:
                    reading[f"tc{ch}"] = round(temp, 2) if temp is not None else None

                try:
                    fan_states = evaluate_fan_state(active, tc_readings)
                    for zone, state in fan_states.items():
                        on = state == "on"
                        fan_gpio.set_fan(zone, on)
                        reading[f"fan{zone}"] = 1 if on else 0
                    _write_fan_state_json(fan_states)
                except Exception as e:
                    print(f"[sensors] Fan evaluation error: {e}")

                try:
                    check_deviations(_active_run_id, tc_readings, active["started_at"])
                except Exception as e:
                    print(f"[sensors] Deviation check error: {e}")

                for _sid in range(1, 5):
                    _wkg = weight_state[_sid]['kg']
                    reading[f"weight_lbs_{_sid}"] = round(_wkg * 2.20462, 4) if _wkg is not None else None
                reading["weight_lbs"] = reading.get("weight_lbs_1")

                try:
                    sakedb.insert_reading(_active_run_id, reading)
                    global _last_db_write_time
                    _last_db_write_time = time.time()
                except Exception as e:
                    print(f"[sensors] DB write failed: {e}")
            else:
                _active_run_id = None

        except Exception as e:
            print(f"[sensors] Unexpected loop error: {e}")

        time.sleep(2)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    sakedb.init_db()
    stale = sakedb.get_active_run()
    if stale:
        sakedb.mark_crashed(stale["id"])
        print(f"Previous run '{stale['name']}' (id={stale['id']}) marked as crashed.")

    start_sensor_loop()
