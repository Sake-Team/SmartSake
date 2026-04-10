"""Standalone diagnostic tool for MAX31850K thermocouples + SHT30.
Imports shared sensor helpers from sensors.py — no duplicated logic.
"""
import time
from sensors import (
    init_sht30, read_sht30, discover_devices,
    read_temp_c, format_device_id, MAX_THERMOCOUPLES
)

if __name__ == "__main__":
    print(f"Looking for up to {MAX_THERMOCOUPLES} MAX31850K thermocouples...")

    # Initialize SHT30
    try:
        sht30 = init_sht30()
        print("SHT30 sensor initialized.\n")
    except Exception as e:
        sht30 = None
        print(f"SHT30 init failed: {e}\n")

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

        assigned = []
        for d in devices:
            device_id = format_device_id(d)
            ch = device_id_to_channel.get(device_id)
            if ch is not None and ch <= MAX_THERMOCOUPLES:
                assigned.append((ch, d))
        assigned.sort(key=lambda x: x[0])

        # --- SHT30 Reading ---
        if sht30:
            try:
                temp_c, humidity = read_sht30(sht30)
                print(f"SHT30 -- Temp: {temp_c:.2f} °C | Humidity: {humidity:.2f} %RH")
            except Exception as e:
                print(f"SHT30 -- ERROR ({e})")
        else:
            print("SHT30 -- Not available")

        print()

        # --- MAX31850K Readings ---
        if not assigned:
            print("No MAX31850K devices found (or none assigned).")
        else:
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

        print("-" * 40)
        time.sleep(1)
