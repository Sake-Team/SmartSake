import csv
import glob
import os
import time
from datetime import datetime, timezone

# Base directory for 1-Wire devices
W1_BASE = "/sys/bus/w1/devices"

# Number of thermocouple ports
NUM_PORTS = 6

# Path to the CSV file where readings are written
CSV_PATH = os.path.join(os.path.dirname(__file__), "data", "temperature_readings.csv")
CSV_HEADER = ["timestamp", "temp1", "temp2", "temp3", "temp4", "temp5", "temp6"]
# Maximum number of thermocouples to read
MAX_THERMOCOUPLES = 6

def discover_devices():
    """
    Discover MAX31850K devices.
    They usually appear as '3b-xxxxxxxxxxxx'
    Returns a sorted list of up to NUM_PORTS device folder paths.
    """
    return sorted(glob.glob(f"{W1_BASE}/3b-*"))[:NUM_PORTS]

def read_temp_c(device_folder):
    """
    Read temperature in Celsius from a MAX31850K device.
    """
    device_file = f"{device_folder}/w1_slave"

    with open(device_file, "r") as f:
        lines = f.readlines()

    # First line should end with YES if CRC is valid
    if not lines[0].strip().endswith("YES"):
        raise RuntimeError("CRC check failed")

    # Temperature is on second line after 't='
    temp_pos = lines[1].find("t=")
    if temp_pos == -1:
        raise RuntimeError("Temperature data not found")

    temp_milli_c = int(lines[1][temp_pos + 2 :])
    return temp_milli_c / 1000.0

def write_csv_row(timestamp, temps):
    """
    Append a row of temperature readings to the CSV file.
    temps is a list of NUM_PORTS values (float or None for missing ports).
    """
    file_exists = os.path.isfile(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(CSV_HEADER)
        writer.writerow([timestamp] + [f"{t:.2f}" if t is not None else "ERROR" for t in temps])

def format_device_id(device_folder: str) -> str:
    return device_folder.split("/")[-1]

if __name__ == "__main__":
    print(f"Looking for up to {MAX_THERMOCOUPLES} MAX31850K thermocouples...")

    print(f"Discovered {len(devices)} of {NUM_PORTS} thermocouple port(s):")
    for i, d in enumerate(devices, start=1):
        print(f"  Port {i}: {d.split('/')[-1]}")
    if len(devices) < NUM_PORTS:
        print(f"  Ports {len(devices)+1}-{NUM_PORTS}: not found")
    # Keep a stable mapping from device_id -> TC#
    device_id_to_channel = {}
    next_channel = 1

    while True:
        devices = discover_devices()

        # Assign channels to newly discovered devices (stable across loops)
        for d in devices:
            device_id = format_device_id(d)
            if device_id not in device_id_to_channel:
                if next_channel <= MAX_THERMOCOUPLES:
                    device_id_to_channel[device_id] = next_channel
                    next_channel += 1

    while True:
        temps = []
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"

        for i in range(1, NUM_PORTS + 1):
            if i <= len(devices):
                try:
                    temp_c = read_temp_c(devices[i - 1])
                    temps.append(temp_c)
                    print(f"Port {i} ({devices[i-1].split('/')[-1]}): {temp_c:.2f} °C")
                except Exception as e:
                    temps.append(None)
                    print(f"Port {i} ({devices[i-1].split('/')[-1]}): ERROR ({e})")
            else:
                temps.append(None)
                print(f"Port {i}: not connected")
        # Choose devices that have a channel assigned, sorted by channel number
        assigned = []
        for d in devices:
            device_id = format_device_id(d)
            ch = device_id_to_channel.get(device_id)
            if ch is not None and ch <= MAX_THERMOCOUPLES:
                assigned.append((ch, d))
        assigned.sort(key=lambda x: x[0])

        if not assigned:
            print("No MAX31850K devices found (or none assigned).")
            print("-" * 40)
            time.sleep(2)
            continue

        # Display mapping occasionally (every loop is fine; change if noisy)
        print("Thermocouple mapping:")
        for ch, d in assigned:
            print(f" - TC{ch}: {format_device_id(d)}")

        print("\nReading temperatures...\n")
        for ch, d in assigned:
            device_id = format_device_id(d)
            try:
                temp_c = read_temp_c(d)
                print(f"TC{ch} ({device_id}) : {temp_c:.2f} °C")
            except Exception as e:
                print(f"TC{ch} ({device_id}) : ERROR ({e})")

        write_csv_row(timestamp, temps)
        print("-" * 40)
        time.sleep(2)