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
from load_cell_hx711 import HX711, HX711_DAT_PIN, HX711_CLK_PIN, TARE_OFFSET, CALIBRATION_FACTOR, UNITS, SAMPLES_PER_READ, log_weight

CSV_FILE = "sensor_data.csv"
JSON_FILE = "sensor_latest.json"
MAX_CSV_ROWS = 43200  # ~24 hrs at 2-second interval

# SHT30 calibration offset (°C).  Positive = sensor reads too high; adjust until
# readings match a reference thermometer placed at the same location.
SHT30_TEMP_OFFSET_C = 0.0

# Shared state updated by background threads
weight_state = {'kg': None, 'raw': None}
_active_run_id = None

# ── Deviation detection state ─────────────────────────────────────────────────
# Keys: (run_id, zone) → {breach_start: datetime, event_id: int|None, max_dev: float}
_deviation_tracking = {}

# Cached target profile per run to avoid a DB query every loop iteration
_cached_profile = {"run_id": None, "points": {}}  # points: {zone: [(elapsed_min, temp)]}

# Threshold rules: track when a breach started per (run_id, zone, rule_id)
_threshold_breach_start = {}

DEVIATION_THRESHOLD_C = 2.0   # °C above/below target to trigger
DEVIATION_HOLD_MIN    = 10.0  # minutes a breach must persist before logging

# ── PID fan-control constants ─────────────────────────────────────────────────
DEADBAND_DEG   = 0.5    # °C — within deadband, fan holds current state
DEADBAND_HOLD  = 3      # consecutive ticks outside deadband required to switch
INTEGRAL_CLAMP = 20.0   # anti-windup: max abs value of integral accumulator
_SENSOR_LOOP_S = 2      # nominal loop interval (seconds), used as fallback dt

_BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
PID_CONFIG_FILE = os.path.join(_BASE_DIR, "pid_config.json")
FAN_STATE_JSON  = os.path.join(_BASE_DIR, "fan_state.json")

# ── PID runtime state (one per zone, reset on import) ────────────────────────
_pid_integrals   = {z: 0.0   for z in range(1, 7)}
_pid_prev_errors = {z: 0.0   for z in range(1, 7)}
_pid_hold_counts = {z: 0     for z in range(1, 7)}
_pid_fan_on      = {z: False for z in range(1, 7)}
_pid_last_time   = None     # datetime of last PID tick, for dt calculation

# PID config cache
_pid_cfg       = {}
_pid_cfg_mtime = 0.0

# Mode/setpoint side-channel populated by evaluate_fan_state → _write_fan_state_json
_last_fan_mode     = {z: "none" for z in range(1, 7)}
_last_fan_setpoint = {z: None   for z in range(1, 7)}
_last_fan_pid_out  = {z: None   for z in range(1, 7)}


def _load_target_profile(run_id):
    """Return {zone: [(elapsed_min, temp_target)]} — cached until run_id changes."""
    if _cached_profile["run_id"] == run_id:
        return _cached_profile["points"]
    rows = sakedb.get_target_profile(run_id)
    points = {}
    col_map = {1: "temp1_target", 2: "temp2_target", 3: "temp3_target",
               4: "temp4_target", 5: "temp5_target", 6: "temp6_target"}
    for zone, col in col_map.items():
        pts = [(r["elapsed_min"], r[col]) for r in rows if r[col] is not None]
        if pts:
            points[zone] = pts
    _cached_profile["run_id"] = run_id
    _cached_profile["points"] = points
    return points


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


def _load_pid_config():
    """Load pid_config.json, reloading automatically when the file changes."""
    global _pid_cfg, _pid_cfg_mtime
    try:
        mtime = os.path.getmtime(PID_CONFIG_FILE)
        if mtime != _pid_cfg_mtime:
            with open(PID_CONFIG_FILE) as f:
                _pid_cfg = json.load(f)
            _pid_cfg_mtime = mtime
    except Exception:
        if not _pid_cfg:
            _pid_cfg = {"default": {"Kp": 1.0, "Ki": 0.05, "Kd": 0.1}}
    return _pid_cfg


def _pid_gains(zone):
    cfg = _load_pid_config()
    default = cfg.get("default", {"Kp": 1.0, "Ki": 0.05, "Kd": 0.1})
    return cfg.get(f"zone{zone}", default)


def evaluate_fan_state(run, tc_readings):
    """Return {zone: 'on'|'off'|None} based on overrides, rules, then PID.

    Priority: manual override > enabled rules > PID auto-control > None.
    For conflicting rules on the same zone, 'on' wins over 'off'.
    Side-effect: updates _last_fan_mode/_last_fan_setpoint/_last_fan_pid_out.
    """
    global _pid_last_time, _pid_integrals, _pid_prev_errors
    global _pid_hold_counts, _pid_fan_on
    global _last_fan_mode, _last_fan_setpoint, _last_fan_pid_out

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

    # 3. PID auto-control for zones with no override/rule and a target profile
    if _pid_last_time is None:
        dt = float(_SENSOR_LOOP_S)
    else:
        dt = max(0.5, (now - _pid_last_time).total_seconds())
    _pid_last_time = now

    profile = _load_target_profile(run_id)
    for zone in range(1, 7):
        if zone in override_zones:
            _last_fan_mode[zone]     = "manual"
            _last_fan_setpoint[zone] = None
            _last_fan_pid_out[zone]  = None
            continue

        if zone in rule_zones:
            _last_fan_mode[zone]     = "rule"
            _last_fan_setpoint[zone] = None
            _last_fan_pid_out[zone]  = None
            continue

        pts    = profile.get(zone)
        actual = tc_map.get(zone)
        if pts is None or actual is None:
            _last_fan_mode[zone]     = "none"
            _last_fan_setpoint[zone] = None
            _last_fan_pid_out[zone]  = None
            continue

        setpoint = _interp_target(pts, elapsed_min)
        if setpoint is None:
            _last_fan_mode[zone]     = "none"
            _last_fan_setpoint[zone] = None
            _last_fan_pid_out[zone]  = None
            continue

        _last_fan_setpoint[zone] = round(setpoint, 2)

        # PID compute — error > 0 means actual > setpoint (too hot → fan on)
        gains = _pid_gains(zone)
        Kp = gains.get("Kp", 1.0)
        Ki = gains.get("Ki", 0.05)
        Kd = gains.get("Kd", 0.1)

        error = actual - setpoint
        _pid_integrals[zone] = max(-INTEGRAL_CLAMP,
                               min(INTEGRAL_CLAMP,
                               _pid_integrals[zone] + error * dt))
        derivative = (error - _pid_prev_errors[zone]) / dt
        _pid_prev_errors[zone] = error
        pid_output = Kp * error + Ki * _pid_integrals[zone] + Kd * derivative
        _last_fan_pid_out[zone] = round(pid_output, 3)

        # Deadband + hold: prevent chattering around setpoint
        current_on = _pid_fan_on[zone]
        desired_on = pid_output > 0  # positive = too hot = cool with fan

        if abs(error) <= DEADBAND_DEG:
            fan_on = current_on          # within deadband — hold
            _pid_hold_counts[zone] = 0
        elif desired_on != current_on:
            _pid_hold_counts[zone] += 1
            if _pid_hold_counts[zone] >= DEADBAND_HOLD:
                fan_on = desired_on
                _pid_hold_counts[zone] = 0
            else:
                fan_on = current_on      # not yet — need more consecutive ticks
        else:
            _pid_hold_counts[zone] = 0
            fan_on = desired_on

        _pid_fan_on[zone] = fan_on
        result[zone] = "on" if fan_on else "off"
        _last_fan_mode[zone] = "pid"

    return result


def _write_fan_state_json(fan_states):
    """Write fan_state.json with current states, modes, setpoints, and PID output."""
    zones = {}
    for z in range(1, 7):
        zones[str(z)] = {
            "state":    fan_states.get(z),
            "mode":     _last_fan_mode.get(z, "none"),
            "setpoint": _last_fan_setpoint.get(z),
            "pid_out":  _last_fan_pid_out.get(z),
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
def run_hx711_thread():
    """Background thread: reads HX711 every 0.5 s, updates weight_state."""
    try:
        hx = HX711(HX711_DAT_PIN, HX711_CLK_PIN, gain=128)
        hx._offset = TARE_OFFSET
        hx.set_scale(CALIBRATION_FACTOR)
        print("HX711 initialized.")

        while True:
            try:
                weight, raw_avg = hx.get_weight(samples=SAMPLES_PER_READ, units=UNITS)
                weight_state['kg'] = weight
                weight_state['raw'] = raw_avg
                log_weight(weight, UNITS)
            except Exception as e:
                print(f"HX711 read error: {e}")
            time.sleep(0.5)
    except Exception as e:
        print(f"HX711 thread failed to initialize: {e} -- running without scale")


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
        "weight_kg": round(weight_state['kg'], 4) if weight_state['kg'] is not None else None,
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

    # Start HX711 weight thread (fails gracefully if scale not attached)
    hx_thread = threading.Thread(target=run_hx711_thread, daemon=True)
    hx_thread.start()

    # Initialize SHT30
    try:
        sht30 = init_sht30()
        print("SHT30 initialized.")
    except Exception as e:
        sht30 = None
        print(f"SHT30 init failed: {e}")

    device_id_to_channel = {}
    next_channel = 1

    while True:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        devices = discover_devices()

        for d in devices:
            device_id = format_device_id(d)
            if device_id not in device_id_to_channel:
                if next_channel <= MAX_THERMOCOUPLES:
                    device_id_to_channel[device_id] = next_channel
                    next_channel += 1

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

        if weight_state['kg'] is not None:
            print(f"Weight: {weight_state['kg']:.3f} {UNITS}  (raw: {weight_state['raw']:.0f})")

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

            try:
                sakedb.insert_reading(_active_run_id, reading)
            except Exception as e:
                print(f"DB write failed: {e}")

        print("-" * 40)
        time.sleep(2)
