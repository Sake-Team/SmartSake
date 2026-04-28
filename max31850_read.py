"""Diagnostic tool for MAX31850K thermocouples + SHT30.
Imports shared sensor helpers from sensors.py — no duplicated logic.
Logs each reading cycle to data/temperature_readings.csv.
"""
import csv
import os
import time
from datetime import datetime, timezone
from sensors import (
    init_sht30, read_sht30, discover_devices,
    read_temp_c, format_device_id, MAX_THERMOCOUPLES
)

CSV_PATH = os.path.join(os.path.dirname(__file__), "data", "temperature_readings.csv")
CSV_HEADER = ["timestamp", "temp1", "temp2", "temp3", "temp4", "temp5", "temp6"]


def write_csv_row(timestamp: str, temps: list) -> None:
    """Append a row of thermocouple readings to the CSV log."""
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    file_exists = os.path.isfile(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(CSV_HEADER)
        writer.writerow([timestamp] + [f"{t:.2f}" if t is not None else "ERROR" for t in temps])


if __name__ == "__main__":
    print(f"Looking for up to {MAX_THERMOCOUPLES} MAX31850K thermocouples...")

    # Initialize SHT30
    try:
        sht30 = init_sht30()
        print("SHT30 sensor initialized.\n")
    except Exception as e:
        sht30 = None
        print(f"SHT30 init failed: {e}\n")

    # Stable mapping from device_id -> channel number (persists across loop iterations)
    device_id_to_channel = {}
    next_channel = 1

    while True:
        devices = discover_devices()
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"

        # Assign channels to newly discovered devices
        for d in devices:
            device_id = format_device_id(d)
            if device_id not in device_id_to_channel and next_channel <= MAX_THERMOCOUPLES:
                device_id_to_channel[device_id] = next_channel
                next_channel += 1

        # Port-by-port discovery status
        print(f"Discovered {len(devices)} of {MAX_THERMOCOUPLES} port(s):")
        for i, d in enumerate(devices, start=1):
            print(f"  Port {i}: {format_device_id(d)}")
        if len(devices) < MAX_THERMOCOUPLES:
            print(f"  Ports {len(devices) + 1}–{MAX_THERMOCOUPLES}: not connected")

        # SHT30 ambient reading
        if sht30:
            try:
                temp_c, humidity = read_sht30(sht30)
                print(f"SHT30 -- Temp: {temp_c:.2f} °C | Humidity: {humidity:.2f} %RH")
            except Exception as e:
                print(f"SHT30 -- ERROR ({e})")
        else:
            print("SHT30 -- Not available")
        print()

        # Build sorted list of assigned channels
        assigned = sorted(
            [
                (device_id_to_channel[format_device_id(d)], d)
                for d in devices
                if format_device_id(d) in device_id_to_channel
                and device_id_to_channel[format_device_id(d)] <= MAX_THERMOCOUPLES
            ],
            key=lambda x: x[0],
        )

        if not assigned:
            print("No MAX31850K devices assigned.")
            print("-" * 40)
            time.sleep(2)
            continue

        # Read temperatures; build fixed-length list indexed by channel
        temps = [None] * MAX_THERMOCOUPLES
        print("Reading temperatures...\n")
        for ch, d in assigned:
            device_id = format_device_id(d)
            try:
                temp_c = read_temp_c(d)
                temps[ch - 1] = temp_c
                print(f"TC{ch} ({device_id}): {temp_c:.2f} °C")
            except Exception as e:
                print(f"TC{ch} ({device_id}): ERROR ({e})")

        write_csv_row(timestamp, temps)
        print("-" * 40)
        time.sleep(2)
