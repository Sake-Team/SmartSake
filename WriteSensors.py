import glob
import time
import csv
import os
import signal
import board
import adafruit_sht31d
from datetime import datetime
import db as sakedb

# Base directory for 1-Wire devices
W1_BASE = "/sys/bus/w1/devices"
MAX_THERMOCOUPLES = 6
CSV_FILE = "sensor_data.csv"
JSON_FILE = "sensor_latest.json"

# Active run id — set at startup
_active_run_id = None

def init_sht30():
    i2c = board.I2C()
    return adafruit_sht31d.SHT31D(i2c)

def read_sht30(sensor):
    return sensor.temperature, sensor.relative_humidity

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
            try:
                sakedb.insert_reading(_active_run_id, reading)
            except Exception as e:
                print(f"DB write failed: {e}")

        print("-" * 40)
        time.sleep(2)
