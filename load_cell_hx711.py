#!/usr/bin/env python3
"""
Load Cell Calibration Tool - Sake Table Scale System
Hardware: 233 FX29X 040A 0200 L ND Load Cell + SparkFun HX711 (SEN-13879)
Platform: Raspberry Pi 4B
Team: Benjamin Lin, Anastasia Myers, Makenna Hull, Natalie Cupples

Standalone CALIBRATION TOOL ONLY.
# Main data loop has moved to WriteSensors.py
"""

import RPi.GPIO as GPIO
import time
import statistics
import json
import os
import sys
from datetime import datetime

# -----------------------------------------------
# SYSTEM SETTINGS
# -----------------------------------------------
READ_INTERVAL_SEC  = 0.5       # Seconds between readings
SAMPLES_PER_READ   = 10      # Readings averaged per output


# -----------------------------------------------
# HX711 DRIVER CLASS
# -----------------------------------------------
class HX711:
    """Bit-bang driver for the HX711 24-bit ADC (SparkFun SEN-13879)."""

    def __init__(self, dat_pin, clk_pin, gain=128):
        self.DAT = dat_pin
        self.CLK = clk_pin
        self._offset = 0
        self._scale = 1.0

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.CLK, GPIO.OUT)
        GPIO.setup(self.DAT, GPIO.IN)
        GPIO.output(self.CLK, False)

        if gain == 128:
            self._gain_pulses = 1
        elif gain == 64:
            self._gain_pulses = 3
        elif gain == 32:
            self._gain_pulses = 2
        else:
            raise ValueError("Gain must be 32, 64, or 128")

        self.reset()

    def is_ready(self):
        return GPIO.input(self.DAT) == 0

    def reset(self):
        GPIO.output(self.CLK, True)
        time.sleep(0.0001)
        GPIO.output(self.CLK, False)
        time.sleep(0.0004)

    def _read_raw(self):
        timeout = time.time() + 1.0
        while not self.is_ready():
            if time.time() > timeout:
                raise TimeoutError("HX711 not responding -- check wiring!")
            time.sleep(0.001)

        raw_data = 0
        for _ in range(24):
            GPIO.output(self.CLK, True)
            GPIO.output(self.CLK, False)
            raw_data = (raw_data << 1) | GPIO.input(self.DAT)

        for _ in range(self._gain_pulses):
            GPIO.output(self.CLK, True)
            GPIO.output(self.CLK, False)

        if raw_data & 0x800000:
            raw_data -= 0x1000000

        return raw_data

    def read_average(self, samples=10):
        # HX711 24-bit signed range: -8388608 to 8388607
        # Clamp to physically plausible range for a 200lbf load cell on 3.3V
        RAW_MIN = -9000000
        RAW_MAX =  9000000

        readings = []
        for _ in range(samples):
            try:
                val = self._read_raw()
                # Reject clearly invalid hardware reads (clipped/floating values)
                if RAW_MIN <= val <= RAW_MAX:
                    readings.append(val)
                else:
                    print(f"  [SKIP] Rejected bad raw read: {val}")
            except TimeoutError:
                raise
            time.sleep(0.01)  # Slightly longer delay for signal stability

        if not readings:
            raise ValueError("No valid readings -- check wiring (DAT/CLK pins)")

        # Median filter: remove values more than 1.5x IQR from median
        if len(readings) >= 4:
            readings.sort()
            q1 = readings[len(readings) // 4]
            q3 = readings[(3 * len(readings)) // 4]
            iqr = q3 - q1
            margin = max(iqr * 1.5, 500)  # minimum margin avoids over-filtering near zero
            median = statistics.median(readings)
            filtered = [r for r in readings if abs(r - median) <= margin]
            if filtered:
                readings = filtered

        return statistics.mean(readings)

    def tare(self, samples=30):
        print(f"  Taring... reading {samples} samples (keep scale empty)")
        self._offset = self.read_average(samples)
        print(f"  Tare offset: {self._offset:.0f}")
        return self._offset

    def set_scale(self, factor):
        self._scale = factor

    def get_weight(self, samples=10, units="kg") -> tuple:
        """Read weight. Returns (weight_value, raw_avg) — one read batch, two outputs."""
        raw_avg = self.read_average(samples)
        raw = raw_avg - self._offset
        grams = raw / self._scale
        if units == "kg":
            weight_value = grams / 1000.0
        elif units == "lbs":
            weight_value = grams / 453.592
        else:
            weight_value = grams
        return weight_value, raw_avg

    def power_down(self):
        GPIO.output(self.CLK, False)
        GPIO.output(self.CLK, True)
        time.sleep(0.0001)

    def power_up(self):
        GPIO.output(self.CLK, False)
        time.sleep(0.0004)


# -----------------------------------------------
# CALIBRATION ROUTINE
# -----------------------------------------------
def calibrate(hx):
    print("\n" + "="*55)
    print("  CALIBRATION MODE -- 233 FX29X Load Cell")
    print("="*55)

    input("\n  Step 1: Remove ALL weight from the load cell.\n"
          "  Press ENTER when ready...")
    offset = hx.tare(samples=30)

    known_weight_g = float(input(
        "\n  Step 2: Place a known weight on the load cell.\n"
        "  Enter the weight in GRAMS: "
    ))
    input("  Press ENTER when weight is placed and stable...")

    raw = hx.read_average(30) - offset
    factor = raw / known_weight_g

    print(f"\n  Calibration complete!")
    print(f"  TARE_OFFSET        = {offset:.0f}")
    print(f"  CALIBRATION_FACTOR = {factor:.4f}")
    print(f"\n  Update these values at the top of load_cell_hx711.py")
    print("="*55)

    return offset, factor


# -----------------------------------------------
# CONFIG LOADER
# -----------------------------------------------
def load_scale_config(path="scale_config.json"):
    """Load scale_config.json and return {scale_id: HX711_instance} for configured scales."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isabs(path):
        path = os.path.join(script_dir, path)

    with open(path) as f:
        config = json.load(f)

    instances = {}
    for scale_id_str, cfg in config["scales"].items():
        if cfg["dat_pin"] is None or cfg["clk_pin"] is None:
            continue
        try:
            hx = HX711(cfg["dat_pin"], cfg["clk_pin"])
            hx._offset = cfg["tare_offset"]
            hx.set_scale(cfg["calibration_factor"])
            instances[int(scale_id_str)] = hx
        except Exception as e:
            print(f"  [WARN] Scale {scale_id_str} failed to init: {e} -- skipping")

    return instances


# -----------------------------------------------
# DATA LOGGING
# -----------------------------------------------
def log_weight(scale_id, weight, units):
    """Write current weight to JSON for main system integration."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_file = os.path.join(script_dir, f"scale_{scale_id}_data.json")

    try:
        data = {
            "timestamp": datetime.now().isoformat(),
            "weight_value": round(weight, 4),
            "weight_units": units,
            "sensor": "233_FX29X_040A_0200",
            "amplifier": "HX711_SEN13879"
        }
        with open(data_file, "w") as f:
            json.dump(data, f, indent=2)
    except (IOError, OSError, PermissionError) as e:
        print(f"  [WARN] Scale {scale_id} log failed -- {e}")


# -----------------------------------------------
# CONFIG WRITE-BACK HELPER
# -----------------------------------------------
def _write_scale_config(config, path):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp, path)


# -----------------------------------------------
# ENTRY POINT
# -----------------------------------------------
if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "scale_config.json")

    if "--calibrate" in sys.argv:
        if "--scale" not in sys.argv:
            print("  Usage: python3 load_cell_hx711.py --calibrate --scale N")
            sys.exit(1)
        scale_id = int(sys.argv[sys.argv.index("--scale") + 1])

        with open(config_path) as f:
            config = json.load(f)

        scale_cfg = config["scales"].get(str(scale_id))
        if scale_cfg is None:
            print(f"Scale {scale_id} not found in scale_config.json.")
            sys.exit(1)
        if scale_cfg["dat_pin"] is None or scale_cfg["clk_pin"] is None:
            print(f"Scale {scale_id} has no GPIO pins configured. Update scale_config.json first.")
            sys.exit(1)

        hx = HX711(scale_cfg["dat_pin"], scale_cfg["clk_pin"])
        offset, factor = calibrate(hx)

        config["scales"][str(scale_id)]["tare_offset"] = int(round(offset))
        config["scales"][str(scale_id)]["calibration_factor"] = round(factor, 4)
        _write_scale_config(config, config_path)

        print(f"Scale {scale_id} calibrated. scale_config.json updated.")
        GPIO.cleanup()

    elif "--tare" in sys.argv:
        if "--scale" not in sys.argv:
            print("  Usage: python3 load_cell_hx711.py --tare --scale N")
            sys.exit(1)
        scale_id = int(sys.argv[sys.argv.index("--scale") + 1])

        with open(config_path) as f:
            config = json.load(f)

        scale_cfg = config["scales"].get(str(scale_id))
        if scale_cfg is None:
            print(f"Scale {scale_id} not found in scale_config.json.")
            sys.exit(1)
        if scale_cfg["dat_pin"] is None or scale_cfg["clk_pin"] is None:
            print(f"Scale {scale_id} has no GPIO pins configured. Update scale_config.json first.")
            sys.exit(1)

        hx = HX711(scale_cfg["dat_pin"], scale_cfg["clk_pin"])
        offset = hx.tare(30)

        config["scales"][str(scale_id)]["tare_offset"] = int(round(offset))
        _write_scale_config(config, config_path)

        print(f"Scale {scale_id} tare updated. scale_config.json updated.")
        GPIO.cleanup()

    elif "--list" in sys.argv:
        with open(config_path) as f:
            config = json.load(f)

        for scale_id_str, cfg in config["scales"].items():
            pin_status = "configured" if cfg["dat_pin"] is not None else "not wired"
            cal_status = "calibrated" if cfg["tare_offset"] != 0 else "uncalibrated"
            print(f"Scale {scale_id_str} ({cfg['label']}): "
                  f"pins={cfg['dat_pin']}/{cfg['clk_pin']} [{pin_status}], "
                  f"calibration [{cal_status}]")

    else:
        print("  Sake Table Scale -- Calibration Tool")
        print("  Usage:")
        print("    python3 load_cell_hx711.py --calibrate --scale N   # full calibration")
        print("    python3 load_cell_hx711.py --tare --scale N        # re-tare only")
        print("    python3 load_cell_hx711.py --list                  # show all scales")
