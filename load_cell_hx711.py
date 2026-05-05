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
        self._cal_points = []  # [(raw, weight_g), ...] sorted by raw — multi-point curve

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
        if factor == 0:
            print("  [WARN] Calibration factor is 0 — using 1.0 to avoid division by zero")
            factor = 1.0
        self._scale = factor

    def set_calibration_points(self, points):
        """Set multi-point calibration curve.

        points: list of dicts with 'raw' and 'weight_g' keys.
        Requires at least 2 points (e.g., empty + one known weight).
        Points are sorted by raw value for piecewise-linear interpolation.
        """
        if not points or len(points) < 2:
            self._cal_points = []
            return
        sorted_pts = sorted(
            [(float(p["raw"]), float(p["weight_g"])) for p in points],
            key=lambda x: x[0]
        )
        # Deduplicate: if multiple points have the same raw value, keep the last one
        deduped = {}
        for raw, wg in sorted_pts:
            deduped[raw] = wg
        if len(deduped) < len(sorted_pts):
            print(f"  [WARN] {len(sorted_pts) - len(deduped)} duplicate raw value(s) "
                  f"removed from calibration points (kept last entry per raw value)")
        self._cal_points = sorted(deduped.items(), key=lambda x: x[0])

    def _interp_weight_g(self, raw_avg):
        """Piecewise-linear interpolation from raw ADC to grams using calibration points."""
        pts = self._cal_points
        if not pts:
            return None

        # Clamp/extrapolate: below first point or above last point,
        # use the slope of the nearest segment
        if raw_avg <= pts[0][0]:
            if len(pts) >= 2:
                r0, w0 = pts[0]
                r1, w1 = pts[1]
                slope = (w1 - w0) / (r1 - r0) if r1 != r0 else 0
                return w0 + slope * (raw_avg - r0)
            return pts[0][1]

        if raw_avg >= pts[-1][0]:
            if len(pts) >= 2:
                r0, w0 = pts[-2]
                r1, w1 = pts[-1]
                slope = (w1 - w0) / (r1 - r0) if r1 != r0 else 0
                return w1 + slope * (raw_avg - r1)
            return pts[-1][1]

        # Find the segment containing raw_avg
        for i in range(len(pts) - 1):
            r0, w0 = pts[i]
            r1, w1 = pts[i + 1]
            if r0 <= raw_avg <= r1:
                t = (raw_avg - r0) / (r1 - r0) if r1 != r0 else 0
                return w0 + t * (w1 - w0)

        return pts[-1][1]  # fallback

    def get_weight(self, samples=10, units="kg") -> tuple:
        """Read weight. Returns (weight_kg, raw_avg).

        Always returns weight in KG regardless of the `units` parameter
        (units param is kept for backwards compat but ignored — all internal
        storage and DB writes expect kg).
        """
        raw_avg = self.read_average(samples)

        if self._cal_points:
            # Multi-point piecewise-linear interpolation
            grams = self._interp_weight_g(raw_avg)
        else:
            # Legacy single-point: (raw - offset) / factor = grams
            raw = raw_avg - self._offset
            grams = raw / self._scale

        weight_kg = grams / 1000.0
        return weight_kg, raw_avg

    def power_down(self):
        GPIO.output(self.CLK, False)
        GPIO.output(self.CLK, True)
        time.sleep(0.0001)

    def power_up(self):
        GPIO.output(self.CLK, False)
        time.sleep(0.0004)


# -----------------------------------------------
# CALIBRATION ROUTINES
# -----------------------------------------------
def calibrate(hx):
    """Legacy single-point calibration (zero + one known weight)."""
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
    print("="*55)

    return offset, factor


def calibrate_multipoint(hx):
    """Multi-point calibration: record raw readings at multiple known weights.

    Returns a list of calibration points: [{"raw": R, "weight_g": W, "label": L}, ...]
    """
    print("\n" + "="*55)
    print("  MULTI-POINT CALIBRATION -- 233 FX29X Load Cell")
    print("="*55)
    print("\n  You will record the raw ADC value at multiple known weights.")
    print("  Start with the scale EMPTY (0g), then add known weights one at a time.")
    print("  Enter 'done' when you have enough points (minimum 2).\n")

    points = []
    step = 1

    while True:
        if step == 1:
            label = input(f"  Point {step}: Remove ALL weight. Press ENTER when ready...")
            weight_g = 0.0
            label = "empty"
        else:
            weight_input = input(f"\n  Point {step}: Enter known weight in GRAMS (or 'done' to finish): ").strip()
            if weight_input.lower() == "done":
                if len(points) < 2:
                    print("  Need at least 2 points! Keep going.")
                    continue
                break
            try:
                weight_g = float(weight_input)
            except ValueError:
                print("  Invalid number. Try again.")
                continue
            if weight_g < 0:
                print("  Weight must be >= 0. Try again.")
                continue
            label = input(f"  Label for this point (optional, e.g. '2kg plate'): ").strip() or f"{weight_g}g"
            input("  Press ENTER when weight is placed and stable...")

        print(f"  Reading {30} samples...")
        raw = hx.read_average(30)
        points.append({"raw": round(raw, 1), "weight_g": weight_g, "label": label})
        print(f"  ✓ Point {step}: raw={raw:.0f}, weight={weight_g}g ({label})")
        step += 1

    # Sort by weight for display
    points.sort(key=lambda p: p["weight_g"])
    print(f"\n  Multi-point calibration complete! {len(points)} points recorded:")
    for p in points:
        print(f"    {p['label']:>20s}:  {p['weight_g']:>8.1f}g  (raw={p['raw']:.0f})")
    print("="*55)

    return points


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
            hx._offset = cfg.get("tare_offset", 0)
            hx.set_scale(cfg.get("calibration_factor", 1.0))

            # Load multi-point calibration curve if available
            cal_pts = cfg.get("calibration_points")
            if cal_pts and isinstance(cal_pts, list) and len(cal_pts) >= 2:
                try:
                    hx.set_calibration_points(cal_pts)
                    print(f"  Scale {scale_id_str}: multi-point calibration "
                          f"({len(cal_pts)} points)")
                except Exception as e:
                    print(f"  [WARN] Scale {scale_id_str}: bad calibration points ({e}) "
                          f"— falling back to single-point (offset={hx._offset}, factor={hx._scale})")
                    hx._cal_points = []
            else:
                print(f"  Scale {scale_id_str}: legacy single-point calibration "
                      f"(offset={hx._offset}, factor={hx._scale})")

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

    if "--calibrate-multipoint" in sys.argv:
        if "--scale" not in sys.argv:
            print("  Usage: python3 load_cell_hx711.py --calibrate-multipoint --scale N")
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
        points = calibrate_multipoint(hx)

        config["scales"][str(scale_id)]["calibration_points"] = points
        # Also derive legacy offset+factor from first two points for backwards compat
        pts_sorted = sorted(points, key=lambda p: p["weight_g"])
        zero_pt = pts_sorted[0]
        load_pt = pts_sorted[-1]
        config["scales"][str(scale_id)]["tare_offset"] = int(round(zero_pt["raw"]))
        if load_pt["weight_g"] > 0:
            raw_delta = load_pt["raw"] - zero_pt["raw"]
            factor = raw_delta / load_pt["weight_g"]
            config["scales"][str(scale_id)]["calibration_factor"] = round(factor, 4)

        _write_scale_config(config, config_path)
        print(f"Scale {scale_id} multi-point calibration saved to scale_config.json.")
        GPIO.cleanup()

    elif "--calibrate" in sys.argv:
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
        # Clear any old multi-point curve when doing single-point cal
        config["scales"][str(scale_id)].pop("calibration_points", None)
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
            pin_status = "configured" if cfg.get("dat_pin") is not None else "not wired"
            cal_pts = cfg.get("calibration_points")
            if cal_pts and len(cal_pts) >= 2:
                cal_status = f"multi-point ({len(cal_pts)} pts)"
            elif cfg.get("tare_offset", 0) != 0:
                cal_status = "single-point"
            else:
                cal_status = "uncalibrated"
            print(f"Scale {scale_id_str} ({cfg.get('label', '?')}): "
                  f"pins={cfg.get('dat_pin')}/{cfg.get('clk_pin')} [{pin_status}], "
                  f"calibration [{cal_status}]")

    else:
        print("  Sake Table Scale -- Calibration Tool")
        print("  Usage:")
        print("    python3 load_cell_hx711.py --calibrate --scale N             # single-point cal")
        print("    python3 load_cell_hx711.py --calibrate-multipoint --scale N  # multi-point curve")
        print("    python3 load_cell_hx711.py --tare --scale N                  # re-tare only")
        print("    python3 load_cell_hx711.py --list                            # show all scales")
