#!/usr/bin/env python3
"""
Hardware smoke test harness for SmartSake.
Exercises each subsystem and reports pass/fail without starting the full server.

Usage:
  python test_hardware.py [--all] [--scale N] [--zone N]
  --all      Run all tests (default)
  --scale N  Test only scale N
  --zone N   Test only fan zone N
"""

import sys
import os
import json
import time
import statistics

# ── GPIO: graceful import ────────────────────────────────────────────────────
_GPIO = None
_gpio_available = False
try:
    import RPi.GPIO as GPIO
    _GPIO = GPIO
    _gpio_available = True
except (ImportError, RuntimeError):
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Results accumulator ───────────────────────────────────────────────────────
_results = {}


def _record(label, status, detail=""):
    _results[label] = (status, detail)
    tag = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]", "WARN": "[WARN]"}.get(status, f"[{status}]")
    msg = f"  {label}: {tag}"
    if detail:
        msg += f" — {detail}"
    print(msg)


# ── Test: SHT30 ───────────────────────────────────────────────────────────────
def test_sht30():
    print("\n--- SHT30 I2C Sensor ---")
    try:
        import board
        import adafruit_sht31d
        i2c = board.I2C()
        sensor = adafruit_sht31d.SHT31D(i2c)
        temp = sensor.temperature
        hum = sensor.relative_humidity
        if not (-10 <= temp <= 80):
            _record("SHT30", "FAIL", f"temp={temp:.2f}C out of range [-10, 80]")
            return
        if not (0 <= hum <= 100):
            _record("SHT30", "FAIL", f"humidity={hum:.2f}% out of range [0, 100]")
            return
        _record("SHT30", "PASS", f"temp={temp:.2f}C hum={hum:.2f}%")
    except Exception as e:
        _record("SHT30", "FAIL", str(e))


# ── Test: Thermocouples ───────────────────────────────────────────────────────
def test_thermocouples():
    print("\n--- MAX31850K Thermocouples ---")
    try:
        from sensors import discover_devices, read_temp_c, format_device_id
        devices = discover_devices()
        if not devices:
            _record("Thermocouples", "WARN", "no devices found (may not be connected yet)")
            return
        passed = 0
        for i, d in enumerate(devices, start=1):
            dev_id = format_device_id(d)
            try:
                temp = read_temp_c(d)
                if -10 <= temp <= 200:
                    print(f"  TC{i} [{dev_id}]: {temp:.1f}C [PASS]")
                    passed += 1
                else:
                    print(f"  TC{i} [{dev_id}]: {temp:.1f}C [FAIL] out of range")
            except Exception as e:
                print(f"  TC{i} [{dev_id}]: ERROR — {e}")
        _record("Thermocouples", "PASS", f"{passed}/{len(devices)} found")
    except Exception as e:
        _record("Thermocouples", "FAIL", str(e))


# ── Test: Load cells ──────────────────────────────────────────────────────────
def test_scale(scale_id):
    label = f"Scale {scale_id}"
    print(f"\n--- {label} ---")

    config_path = os.path.join(SCRIPT_DIR, "scale_config.json")
    try:
        with open(config_path) as f:
            config = json.load(f)
    except Exception as e:
        _record(label, "FAIL", f"could not read scale_config.json: {e}")
        return

    cfg = config["scales"].get(str(scale_id))
    if cfg is None:
        _record(label, "SKIP", "not in scale_config.json")
        return
    if cfg["dat_pin"] is None or cfg["clk_pin"] is None:
        _record(label, "SKIP", "not wired")
        return

    if not _gpio_available:
        _record(label, "SKIP", "RPi.GPIO not available on this machine")
        return

    try:
        from load_cell_hx711 import HX711
        hx = HX711(cfg["dat_pin"], cfg["clk_pin"])
        samples = []
        for _ in range(5):
            samples.append(hx._read_raw())
            time.sleep(0.05)
        mean_val = statistics.mean(samples)
        stdev_val = statistics.stdev(samples) if len(samples) > 1 else 0
        pin_label = f"GPIO{cfg['dat_pin']}/{cfg['clk_pin']}"
        if stdev_val < 500:
            _record(label, "PASS", f"[{pin_label}] mean={mean_val:.0f} stdev={stdev_val:.1f}")
        else:
            _record(label, "FAIL", f"[{pin_label}] stdev={stdev_val:.1f} > 500 (unstable signal)")
        _GPIO.cleanup()
    except Exception as e:
        _record(label, "FAIL", str(e))


def test_all_scales():
    config_path = os.path.join(SCRIPT_DIR, "scale_config.json")
    try:
        with open(config_path) as f:
            config = json.load(f)
        for sid_str in config["scales"]:
            test_scale(int(sid_str))
    except Exception as e:
        _record("Scales", "FAIL", f"could not read scale_config.json: {e}")


# ── Test: Fan relay channels ──────────────────────────────────────────────────
def test_zone(zone_id):
    label = f"Fan Zone {zone_id}"
    print(f"\n--- {label} ---")

    try:
        from fan_gpio import FAN_PINS
        pin = FAN_PINS.get(zone_id)
    except Exception as e:
        _record(label, "FAIL", f"could not import fan_gpio: {e}")
        return

    if pin is None:
        _record(label, "SKIP", "not wired")
        return

    if not _gpio_available:
        _record(label, "SKIP", "RPi.GPIO not available on this machine")
        return

    try:
        _GPIO.setmode(_GPIO.BCM)
        _GPIO.setup(pin, _GPIO.OUT, initial=_GPIO.HIGH)  # HIGH = OFF for active-LOW relay
        # Pulse: ON 0.5s then OFF (active-LOW: LOW = ON)
        _GPIO.output(pin, _GPIO.LOW)
        time.sleep(0.5)
        _GPIO.output(pin, _GPIO.HIGH)
        _GPIO.cleanup()

        answer = input(f"  Listen for relay click on zone {zone_id} [GPIO{pin}]. Did you hear it? [y/N]: ").strip().lower()
        if answer == "y":
            _record(label, "PASS", f"[GPIO{pin}] relay pulsed — click confirmed")
        else:
            _record(label, "FAIL", f"[GPIO{pin}] relay pulsed — click NOT confirmed")
    except Exception as e:
        _record(label, "FAIL", str(e))


def test_all_zones():
    try:
        from fan_gpio import FAN_PINS
        for zone_id in sorted(FAN_PINS.keys()):
            test_zone(zone_id)
    except Exception as e:
        _record("Fan Zones", "FAIL", f"could not import fan_gpio: {e}")


# ── Test: Database ────────────────────────────────────────────────────────────
def test_database():
    print("\n--- Database ---")
    try:
        import db as sakedb
        sakedb.init_db()
        # Insert a test run then delete it
        run_id = sakedb.create_run("__hw_test__")
        reading = {
            "recorded_at": "2000-01-01 00:00:00",
            "tc1": 25.0, "sht_temp": 24.0, "humidity": 80.0,
        }
        sakedb.insert_reading(run_id, reading)
        row = sakedb.get_latest_reading(run_id)
        sakedb.delete_run(run_id)
        if row and row.get("tc1") == 25.0:
            _record("Database", "PASS", "init + insert + retrieve + delete OK")
        else:
            _record("Database", "FAIL", "row mismatch after insert")
    except Exception as e:
        _record("Database", "FAIL", str(e))


# ── Summary ───────────────────────────────────────────────────────────────────
def print_summary():
    print("\n=== Hardware Test Summary ===")
    for label, (status, detail) in _results.items():
        tag = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP", "WARN": "WARN"}.get(status, status)
        line = f"  {label:<20} {tag}"
        if detail:
            line += f"  ({detail})"
        print(line)
    print("===========================")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]

    run_scale = None
    run_zone = None
    run_all = "--all" in args or not args

    if "--scale" in args:
        run_scale = int(args[args.index("--scale") + 1])
        run_all = False
    if "--zone" in args:
        run_zone = int(args[args.index("--zone") + 1])
        run_all = False

    if run_all:
        test_sht30()
        test_thermocouples()
        test_all_scales()
        test_all_zones()
        test_database()
    else:
        if run_scale is not None:
            test_scale(run_scale)
        if run_zone is not None:
            test_zone(run_zone)

    print_summary()
