#!/usr/bin/env python3
"""
Load Cell Reader - Sake Table Scale System
Hardware: 233 FX29X 040A 0200 L ND Load Cell + SparkFun HX711 (SEN-13879)
Platform: Raspberry Pi 4B
Team: Benjamin Lin, Anastasia Myers, Makenna Hull, Natalie Cupples

Designed to integrate with the main sake brewing control system.
Outputs live weight readings every 2 seconds to console and shared data file.
"""

import RPi.GPIO as GPIO
import time
import statistics
import json
import os
from datetime import datetime

# ─────────────────────────────────────────────
# GPIO PIN CONFIGURATION (BCM numbering)
# ─────────────────────────────────────────────
# Connect HX711 SEN-13879 to these GPIO pins:
#   HX711 DAT  → GPIO 5  (Pin 29)
#   HX711 CLK  → GPIO 6  (Pin 31)
#   HX711 VCC  → 3.3V    (Pin 1)
#   HX711 GND  → GND     (Pin 6)

HX711_DAT_PIN = 5   # GPIO 5  | Physical Pin 29
HX711_CLK_PIN = 6   # GPIO 6  | Physical Pin 31

# ─────────────────────────────────────────────
# CALIBRATION SETTINGS
# ─────────────────────────────────────────────
# 233 FX29X 040A 0200 L ND specs:
#   Capacity:       200 lbf (889.6 N)
#   Output:         2 mV/V nominal
#   Excitation:     3-10V (using 3.3V from Pi)
#
# CALIBRATION STEPS (run calibrate() function first):
#   1. Place nothing on scale → record TARE_OFFSET
#   2. Place known weight → calculate CALIBRATION_FACTOR
#
# Default values — MUST be updated after physical calibration!
TARE_OFFSET        = 4519      # Raw ADC value with no load (update after calibration)
CALIBRATION_FACTOR = 8084.2936       # Raw units per gram (update after calibration)
UNITS              = "kg"      # Display units: "kg", "lbs", or "g"

# ─────────────────────────────────────────────
# SYSTEM SETTINGS
# ─────────────────────────────────────────────
READ_INTERVAL_SEC  = 2         # Seconds between live readings
SAMPLES_PER_READ   = 10        # Readings averaged per output (noise reduction)
DATA_LOG_FILE      = "/home/kojitable/MainCode/scale_data.json"  # Shared data file
ENABLE_DATA_LOG    = True      # Set False to disable file logging


# ─────────────────────────────────────────────
# HX711 DRIVER CLASS
# ─────────────────────────────────────────────
class HX711:
    """
    Bit-bang driver for the HX711 24-bit ADC amplifier.
    Compatible with SparkFun SEN-13879.
    """

    def __init__(self, dat_pin, clk_pin, gain=128):
        self.DAT = dat_pin
        self.CLK = clk_pin
        self.gain = gain
        self._offset = 0
        self._scale = 1.0

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.CLK, GPIO.OUT)
        GPIO.setup(self.DAT, GPIO.IN)
        GPIO.output(self.CLK, False)

        # Set gain: 128 = 1 pulse, 64 = 3 pulses, 32 = 2 pulses
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
        """Returns True when HX711 has data available (DAT pin LOW)."""
        return GPIO.input(self.DAT) == 0

    def reset(self):
        """Reset and power cycle the HX711."""
        GPIO.output(self.CLK, True)
        time.sleep(0.0001)
        GPIO.output(self.CLK, False)
        time.sleep(0.0004)

    def _read_raw(self):
        """Read one 24-bit raw value from HX711 via bit-bang."""
        # Wait for chip to be ready
        timeout = time.time() + 1.0
        while not self.is_ready():
            if time.time() > timeout:
                raise TimeoutError("HX711 not responding — check wiring!")
            time.sleep(0.001)

        raw_data = 0
        for _ in range(24):
            GPIO.output(self.CLK, True)
            GPIO.output(self.CLK, False)
            raw_data = (raw_data << 1) | GPIO.input(self.DAT)

        # Send gain pulses (sets gain for NEXT reading)
        for _ in range(self._gain_pulses):
            GPIO.output(self.CLK, True)
            GPIO.output(self.CLK, False)

        # Convert 24-bit two's complement to signed integer
        if raw_data & 0x800000:
            raw_data -= 0x1000000

        return raw_data

    def read_average(self, samples=10):
        """Return the average of multiple raw readings (reduces noise)."""
        readings = []
        for _ in range(samples):
            try:
                readings.append(self._read_raw())
            except TimeoutError:
                raise
            time.sleep(0.005)

        # Remove outliers using median filtering
        if len(readings) >= 5:
            median = statistics.median(readings)
            readings = [r for r in readings if abs(r - median) < 3 * statistics.stdev(readings)]

        return statistics.mean(readings) if readings else 0

    def tare(self, samples=30):
        """Zero the scale — call with nothing on the load cell."""
        print(f"  Taring... reading {samples} samples (keep scale empty)")
        self._offset = self.read_average(samples)
        print(f"  Tare offset set to: {self._offset:.0f}")
        return self._offset

    def set_scale(self, factor):
        """Set the calibration factor (raw units per gram)."""
        self._scale = factor

    def get_weight(self, samples=10, units="kg"):
        """Return calibrated weight in specified units."""
        raw = self.read_average(samples) - self._offset
        grams = raw / self._scale

        if units == "kg":
            return grams / 1000.0
        elif units == "lbs":
            return grams / 453.592
        else:
            return grams

    def power_down(self):
        """Put HX711 into low-power mode."""
        GPIO.output(self.CLK, False)
        GPIO.output(self.CLK, True)
        time.sleep(0.0001)

    def power_up(self):
        """Wake HX711 from low-power mode."""
        GPIO.output(self.CLK, False)
        time.sleep(0.0004)


# ─────────────────────────────────────────────
# CALIBRATION ROUTINE
# ─────────────────────────────────────────────
def calibrate(hx):
    """
    Interactive calibration routine.
    Run this once to find your TARE_OFFSET and CALIBRATION_FACTOR,
    then update the constants at the top of this file.
    """
    print("\n" + "="*55)
    print("  CALIBRATION MODE — 233 FX29X Load Cell")
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

    print(f"\n  ✓ Calibration complete!")
    print(f"  TARE_OFFSET        = {offset:.0f}")
    print(f"  CALIBRATION_FACTOR = {factor:.4f}")
    print(f"\n  → Update these values at the top of load_cell_hx711.py")
    print("="*55)

    return offset, factor


# ─────────────────────────────────────────────
# DATA LOGGING (shared with main system)
# ─────────────────────────────────────────────
def log_weight(weight, units):
    """
    Write the current weight to a shared JSON file that the
    main sake control system can read.
    Format matches the sensor data structure used by the PLC/Pi system.
    """
    if not ENABLE_DATA_LOG:
        return

    log_dir = os.path.dirname(DATA_LOG_FILE)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    data = {
        "timestamp": datetime.now().isoformat(),
        "weight_value": round(weight, 4),
        "weight_units": units,
        "sensor": "233_FX29X_040A_0200",
        "amplifier": "HX711_SEN13879"
    }

    try:
        with open(DATA_LOG_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except IOError as e:
        print(f"  [WARN] Could not write to log file: {e}")


# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────
def main():
    print("\n" + "="*55)
    print("  Sake Table Scale — Load Cell Monitor")
    print("  233 FX29X + HX711 SEN-13879 | RPi 4B")
    print("="*55)
    print(f"  DAT Pin:  GPIO {HX711_DAT_PIN} (Physical Pin 29)")
    print(f"  CLK Pin:  GPIO {HX711_CLK_PIN} (Physical Pin 31)")
    print(f"  Interval: {READ_INTERVAL_SEC}s  |  Avg samples: {SAMPLES_PER_READ}")
    print(f"  Units:    {UNITS}")
    print("="*55)

    hx = HX711(HX711_DAT_PIN, HX711_CLK_PIN, gain=128)

    # Apply saved calibration values
    hx._offset = TARE_OFFSET
    hx.set_scale(CALIBRATION_FACTOR)

    # Warn if not yet calibrated
    if TARE_OFFSET == 0 and CALIBRATION_FACTOR == 1.0:
        print("\n  ⚠  WARNING: Default calibration values detected!")
        print("  Run with --calibrate flag first for accurate readings.\n")
        run_cal = input("  Run calibration now? (y/n): ").strip().lower()
        if run_cal == "y":
            TARE_OFFSET_new, CAL_FACTOR_new = calibrate(hx)
            hx._offset = TARE_OFFSET_new
            hx.set_scale(CAL_FACTOR_new)
        else:
            print("  Continuing with uncalibrated readings...\n")

    print("\n  Reading live weight data — press Ctrl+C to stop\n")
    print(f"  {'Time':<12} {'Weight':>12}  {'Raw ADC':>12}")
    print("  " + "-"*40)

    try:
        while True:
            try:
                weight = hx.get_weight(samples=SAMPLES_PER_READ, units=UNITS)
                raw_avg = hx.read_average(samples=5)
                timestamp = datetime.now().strftime("%H:%M:%S")

                # Console output
                print(f"  {timestamp:<12} {weight:>10.3f} {UNITS}  {raw_avg:>12.0f}")

                # Log to shared data file for main system integration
                log_weight(weight, UNITS)

            except TimeoutError as e:
                print(f"  [ERROR] {e}")

            time.sleep(READ_INTERVAL_SEC)

    except KeyboardInterrupt:
        print("\n\n  Stopped by user.")
    finally:
        GPIO.cleanup()
        print("  GPIO cleaned up. Goodbye.\n")


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if "--calibrate" in sys.argv:
        GPIO.setmode(GPIO.BCM)
        hx = HX711(HX711_DAT_PIN, HX711_CLK_PIN)
        calibrate(hx)
        GPIO.cleanup()
    else:
        main()
