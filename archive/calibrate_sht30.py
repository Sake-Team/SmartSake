#!/usr/bin/env python3
"""
Usage: python3 calibrate_sht30.py <reference_temp_c>

Reads the current SHT30 temperature, computes offset = current_reading - reference,
writes sht30_temp_offset_c to scale_config.json, prints confirmation.
"""
import json
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent
SENSOR_JSON = BASE_DIR / "sensor_latest.json"
SCALE_CONFIG = BASE_DIR / "scale_config.json"
STALE_SECONDS = 30


def _read_from_file():
    if not SENSOR_JSON.exists():
        return None
    age = time.time() - SENSOR_JSON.stat().st_mtime
    if age > STALE_SECONDS:
        return None
    try:
        with open(SENSOR_JSON) as f:
            data = json.load(f)
        val = data.get("sht30", {}).get("temp_c")
        return float(val) if val is not None else None
    except Exception:
        return None


def _read_direct():
    import board
    import adafruit_sht31d
    i2c = board.I2C()
    sensor = adafruit_sht31d.SHT31D(i2c)
    return sensor.temperature


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 calibrate_sht30.py <reference_temp_c>")
        sys.exit(1)
    try:
        reference = float(sys.argv[1])
    except ValueError:
        print("Error: reference_temp_c must be a number")
        sys.exit(1)

    reading = _read_from_file()
    if reading is None:
        print("sensor_latest.json missing or stale — reading sensor directly...")
        try:
            reading = _read_direct()
        except Exception as e:
            print(f"Error reading SHT30 directly: {e}")
            sys.exit(1)

    offset = reading - reference

    try:
        with open(SCALE_CONFIG) as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"Could not load scale_config.json: {e}")
        sys.exit(1)

    if "sensors" not in cfg:
        cfg["sensors"] = {}
    cfg["sensors"]["sht30_temp_offset_c"] = round(offset, 3)

    tmp = str(SCALE_CONFIG) + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, SCALE_CONFIG)
    except Exception as e:
        print(f"Could not write scale_config.json: {e}")
        sys.exit(1)

    print(f"SHT30 read: {reading:.2f}°C | Reference: {reference:.2f}°C | Offset: {offset:+.3f}°C → scale_config.json updated")


if __name__ == "__main__":
    main()
