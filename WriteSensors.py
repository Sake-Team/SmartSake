import glob
import time
import csv
import os
import signal
import board
import adafruit_sht31d
from datetime import datetime, timedelta
import db as sakedb
import fan_gpio

# Base directory for 1-Wire devices
W1_BASE = "/sys/bus/w1/devices"
MAX_THERMOCOUPLES = 6
CSV_FILE = "sensor_data.csv"
JSON_FILE = "sensor_latest.json"

# SHT30 calibration offset (°C).  Positive = sensor reads too high; adjust until
# readings match a reference thermometer placed at the same location.
SHT30_TEMP_OFFSET_C = 0.0

# Active run id — set at startup
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


def evaluate_fan_state(run, tc_readings):
    """Return {zone: 'on'|'off'|None} based on overrides then rules.

    Priority: manual override > enabled rules > None (no action).
    For conflicting rules on the same zone, 'on' wins over 'off'.
    """
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

    return result


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

def discover_devices():
    return sorted(glob.glob(f"{W1_BASE}/3b-*"))

def read_temp_c(device_folder):
    device_file = f"{device_folder}/w1_slave"
    with open(device_file, "r") as f:
        lines = f.readlines()
    if not lines[0].strip().endswith("YES"):
        raise RuntimeError("CRC check failed")
    temp_pos = lines[1].find("t=")
    if temp_pos == -1:
        raise RuntimeError("Temperature data not found")
    temp_milli_c = int(lines[1][temp_pos + 2:])
    return temp_milli_c / 1000.0

def format_device_id(device_folder: str) -> str:
    return device_folder.split("/")[-1]

def write_csv(timestamp, sht_temp, sht_humidity, tc_readings):
    """Append a row to the CSV file."""
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            # Write header on first run
            headers = ["timestamp", "sht30_temp_c", "sht30_humidity_rh"]
            headers += [f"TC{ch}_temp_c" for ch, _ in tc_readings]
            writer.writerow(headers)
        row = [timestamp, sht_temp, sht_humidity]
        row += [f"{temp:.2f}" if temp is not None else "ERROR" for _, temp in tc_readings]
        writer.writerow(row)

def write_json(timestamp, sht_temp, sht_humidity, tc_readings):
    """Write latest readings to a JSON file for the HTML page to fetch."""
    import json
    data = {
        "timestamp": timestamp,
        "sht30": {
            "temp_c": round(sht_temp, 2) if sht_temp is not None else None,
            "humidity_rh": round(sht_humidity, 2) if sht_humidity is not None else None
        },
        "thermocouples": {
            f"TC{ch}": round(temp, 2) if temp is not None else None
            for ch, temp in tc_readings
        }
    }
    with open(JSON_FILE, "w") as f:
        json.dump(data, f)

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

        # Write outputs
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

            # Evaluate fan rules and set GPIO
            try:
                fan_states = evaluate_fan_state(active, tc_readings)
                for zone, state in fan_states.items():
                    on = state == "on"
                    fan_gpio.set_fan(zone, on)
                    reading[f"fan{zone}"] = 1 if on else 0
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
