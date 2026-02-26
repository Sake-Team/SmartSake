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

    temp_milli_c = int(lines[1][temp_pos+2:])
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


if __name__ == "__main__":
    devices = discover_devices()

    print(f"Discovered {len(devices)} of {NUM_PORTS} thermocouple port(s):")
    for i, d in enumerate(devices, start=1):
        print(f"  Port {i}: {d.split('/')[-1]}")
    if len(devices) < NUM_PORTS:
        print(f"  Ports {len(devices)+1}-{NUM_PORTS}: not found")

    print("\nReading temperatures...\n")

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

        write_csv_row(timestamp, temps)
        print("-" * 40)
        time.sleep(2)
