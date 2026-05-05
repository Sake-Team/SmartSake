#!/usr/bin/env python3
"""
SmartSake — Standalone Load Cell Test Tool

Tests HX711 load cells independently from the main sensor loop and Flask server.
Reads scale_config.json for pin assignments and calibration, then continuously
displays live weight readings with raw ADC values, stability, and drift info.

Usage:
    python3 test_scales.py                  # test all configured scales
    python3 test_scales.py --scale 1        # test only scale 1
    python3 test_scales.py --scale 1,3      # test scales 1 and 3
    python3 test_scales.py --raw            # show raw ADC only (no calibration)
    python3 test_scales.py --fast           # faster polling (fewer samples, less accuracy)
    python3 test_scales.py --log FILE       # append CSV readings to a file
    python3 test_scales.py --tare           # tare all scales before starting
    python3 test_scales.py --once           # take one reading and exit
"""

import sys
import os
import time
import json
import signal
import argparse
from datetime import datetime
from collections import deque

# ── Graceful hardware import ────────────────────────────────────────────────
try:
    import RPi.GPIO as GPIO
    _GPIO_AVAILABLE = True
except ImportError:
    print("[WARN] RPi.GPIO not available — running in dry-run mode (simulated data)")
    _GPIO_AVAILABLE = False

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from load_cell_hx711 import HX711, load_scale_config


# ── ANSI helpers ────────────────────────────────────────────────────────────
BOLD    = "\033[1m"
DIM     = "\033[2m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
CYAN    = "\033[36m"
RESET   = "\033[0m"
CLEAR   = "\033[2J\033[H"


def stability_indicator(history):
    """Return (label, color) based on reading variance over recent history."""
    if len(history) < 3:
        return "INIT", YELLOW
    vals = list(history)
    spread = max(vals) - min(vals)
    if spread < 0.002:       # < 2g spread
        return "STABLE", GREEN
    elif spread < 0.010:     # < 10g spread
        return "SETTLING", YELLOW
    else:
        return "UNSTABLE", RED


def format_weight(kg):
    """Format weight with automatic unit selection."""
    if kg is None:
        return "  --.----- kg  |   --.----- lbs"
    lbs = kg * 2.20462
    g = kg * 1000
    if abs(g) < 100:
        return f"  {g:>9.2f} g   |  {lbs:>9.5f} lbs"
    return f"  {kg:>9.5f} kg  |  {lbs:>9.5f} lbs"


def parse_args():
    p = argparse.ArgumentParser(
        description="SmartSake load cell test tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Press Ctrl+C to stop. Press 't' + Enter to re-tare during a run."
    )
    p.add_argument("--scale", type=str, default=None,
                   help="Comma-separated scale IDs to test (default: all)")
    p.add_argument("--raw", action="store_true",
                   help="Show raw ADC values only (skip calibration)")
    p.add_argument("--fast", action="store_true",
                   help="Faster polling: 3 samples per read (less accurate)")
    p.add_argument("--log", type=str, default=None,
                   help="Append CSV readings to this file")
    p.add_argument("--tare", action="store_true",
                   help="Tare all scales before starting continuous reads")
    p.add_argument("--once", action="store_true",
                   help="Take one reading per scale and exit")
    p.add_argument("--samples", type=int, default=None,
                   help="Samples per reading (default: 10, or 3 with --fast)")
    p.add_argument("--interval", type=float, default=1.0,
                   help="Seconds between readings (default: 1.0)")
    return p.parse_args()


def load_config():
    """Load scale_config.json and return the raw config dict."""
    config_path = os.path.join(SCRIPT_DIR, "scale_config.json")
    if not os.path.exists(config_path):
        print(f"{RED}ERROR: scale_config.json not found at {config_path}{RESET}")
        sys.exit(1)
    with open(config_path) as f:
        return json.load(f)


def init_scales(config, scale_filter=None):
    """Initialize HX711 instances. Returns {scale_id: (hx, cfg_dict)}."""
    scales = {}
    for sid_str, cfg in config.get("scales", {}).items():
        sid = int(sid_str)
        if scale_filter and sid not in scale_filter:
            continue
        if cfg.get("dat_pin") is None or cfg.get("clk_pin") is None:
            print(f"  Scale {sid} ({cfg.get('label', '?')}): {DIM}no pins configured — skipping{RESET}")
            continue
        try:
            hx = HX711(cfg["dat_pin"], cfg["clk_pin"])
            hx._offset = cfg.get("tare_offset", 0)
            hx.set_scale(cfg.get("calibration_factor", 1.0))

            # Load multi-point calibration if available
            cal_pts = cfg.get("calibration_points")
            if cal_pts and isinstance(cal_pts, list) and len(cal_pts) >= 2:
                try:
                    hx.set_calibration_points(cal_pts)
                    cal_info = f"multi-point ({len(cal_pts)} pts)"
                except Exception as e:
                    cal_info = f"single-point (multi-point failed: {e})"
                    hx._cal_points = []
            elif cfg.get("tare_offset", 0) != 0:
                cal_info = f"single-point (offset={hx._offset}, factor={hx._scale})"
            else:
                cal_info = f"{YELLOW}uncalibrated{RESET}"

            label = cfg.get("label", f"Scale {sid}")
            print(f"  {GREEN}Scale {sid}{RESET} ({label}): "
                  f"DAT={cfg['dat_pin']} CLK={cfg['clk_pin']} — {cal_info}")
            scales[sid] = (hx, cfg)

        except Exception as e:
            print(f"  {RED}Scale {sid}: FAILED to init — {e}{RESET}")

    return scales


def run_tare(scales, samples=30):
    """Tare all scales and update scale_config.json."""
    config_path = os.path.join(SCRIPT_DIR, "scale_config.json")
    with open(config_path) as f:
        config = json.load(f)

    print(f"\n{BOLD}Taring {len(scales)} scale(s) — keep them EMPTY...{RESET}")
    time.sleep(1)

    for sid, (hx, cfg) in sorted(scales.items()):
        label = cfg.get("label", f"Scale {sid}")
        print(f"  Taring {label}...", end=" ", flush=True)
        try:
            offset = hx.tare(samples)
            config["scales"][str(sid)]["tare_offset"] = int(round(offset))
            print(f"{GREEN}OK{RESET} (offset={offset:.0f})")
        except Exception as e:
            print(f"{RED}FAILED — {e}{RESET}")

    # Write back
    tmp = config_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp, config_path)
    print(f"  {DIM}Tare offsets saved to scale_config.json{RESET}\n")


def run_continuous(scales, args):
    """Main polling loop — continuously read and display weights."""
    samples = args.samples or (3 if args.fast else 10)
    interval = args.interval
    raw_mode = args.raw
    log_file = None
    log_path = args.log

    # Stability tracking: last 10 readings per scale
    history = {sid: deque(maxlen=10) for sid in scales}
    # Drift tracking: first stable reading per scale
    baseline = {sid: None for sid in scales}
    read_count = 0

    if log_path:
        write_header = not os.path.exists(log_path)
        log_file = open(log_path, "a")
        if write_header:
            cols = ["timestamp"] + [f"scale_{sid}_kg" for sid in sorted(scales)] + \
                   [f"scale_{sid}_raw" for sid in sorted(scales)]
            log_file.write(",".join(cols) + "\n")
        print(f"  {DIM}Logging to {log_path}{RESET}")

    sorted_ids = sorted(scales.keys())
    n = len(sorted_ids)

    print(f"\n{BOLD}{'='*70}")
    print(f"  LIVE LOAD CELL TEST — {n} scale(s), {samples} samples/read, {interval}s interval")
    print(f"{'='*70}{RESET}")
    print(f"  {DIM}Press Ctrl+C to stop{RESET}\n")

    try:
        while True:
            read_count += 1
            now = datetime.now()
            ts = now.strftime("%H:%M:%S")
            ts_iso = now.isoformat()

            # Build the display block
            lines = []
            lines.append(f"  {DIM}[{ts}] Reading #{read_count} ({samples} samples){RESET}")
            lines.append(f"  {'─'*66}")

            csv_kg = {}
            csv_raw = {}

            for sid in sorted_ids:
                hx, cfg = scales[sid]
                label = cfg.get("label", f"Scale {sid}")

                try:
                    t0 = time.time()
                    if raw_mode:
                        raw_avg = hx.read_average(samples)
                        weight_kg = None
                    else:
                        weight_kg, raw_avg = hx.get_weight(samples=samples)
                    elapsed_ms = (time.time() - t0) * 1000

                    # Track stability
                    track_val = raw_avg if raw_mode else weight_kg
                    if track_val is not None:
                        history[sid].append(track_val)
                    stab_label, stab_color = stability_indicator(
                        history[sid] if not raw_mode else
                        deque([r for r in history[sid]], maxlen=10)
                    )

                    # Drift from baseline
                    drift_str = ""
                    if not raw_mode and weight_kg is not None:
                        if baseline[sid] is None and stab_label == "STABLE":
                            baseline[sid] = weight_kg
                        if baseline[sid] is not None:
                            drift_g = (weight_kg - baseline[sid]) * 1000
                            drift_str = f"  drift: {drift_g:+.1f}g"

                    # Format output
                    if raw_mode:
                        lines.append(
                            f"  {BOLD}{label:>10s}{RESET}:  "
                            f"raw = {CYAN}{raw_avg:>12.0f}{RESET}  "
                            f"[{stab_color}{stab_label:>9s}{RESET}]  "
                            f"{DIM}{elapsed_ms:.0f}ms{RESET}"
                        )
                    else:
                        lines.append(
                            f"  {BOLD}{label:>10s}{RESET}: "
                            f"{format_weight(weight_kg)}  "
                            f"[{stab_color}{stab_label:>9s}{RESET}]  "
                            f"{DIM}raw={raw_avg:.0f}  {elapsed_ms:.0f}ms{drift_str}{RESET}"
                        )

                    csv_kg[sid] = weight_kg
                    csv_raw[sid] = raw_avg

                except TimeoutError:
                    lines.append(
                        f"  {BOLD}{label:>10s}{RESET}:  "
                        f"{RED}TIMEOUT — check wiring (DAT={cfg['dat_pin']}, CLK={cfg['clk_pin']}){RESET}"
                    )
                    csv_kg[sid] = None
                    csv_raw[sid] = None

                except Exception as e:
                    lines.append(
                        f"  {BOLD}{label:>10s}{RESET}:  "
                        f"{RED}ERROR — {e}{RESET}"
                    )
                    csv_kg[sid] = None
                    csv_raw[sid] = None

            lines.append("")

            # Print (overwrite previous block for clean display)
            if read_count > 1:
                # Move cursor up to overwrite previous output
                up = len(lines) + 1
                sys.stdout.write(f"\033[{up}A\033[J")

            for line in lines:
                print(line)

            # CSV logging
            if log_file:
                row = [ts_iso]
                for sid in sorted_ids:
                    row.append(f"{csv_kg.get(sid, '')}" if csv_kg.get(sid) is not None else "")
                for sid in sorted_ids:
                    row.append(f"{csv_raw.get(sid, ''):.0f}" if csv_raw.get(sid) is not None else "")
                log_file.write(",".join(row) + "\n")
                log_file.flush()

            if args.once:
                break

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n  {DIM}Stopped by user.{RESET}")
    finally:
        if log_file:
            log_file.close()
            print(f"  {DIM}Log saved to {log_path}{RESET}")


def run_single_reading(scales, samples):
    """Take one reading per scale and print results."""
    print(f"\n{BOLD}Single reading — {samples} samples per scale{RESET}\n")
    for sid, (hx, cfg) in sorted(scales.items()):
        label = cfg.get("label", f"Scale {sid}")
        try:
            weight_kg, raw_avg = hx.get_weight(samples=samples)
            lbs = weight_kg * 2.20462
            g = weight_kg * 1000
            print(f"  {BOLD}{label}{RESET}:")
            print(f"    Weight:  {weight_kg:.5f} kg  |  {lbs:.5f} lbs  |  {g:.2f} g")
            print(f"    Raw ADC: {raw_avg:.0f}")
            if hx._cal_points:
                print(f"    Cal:     multi-point ({len(hx._cal_points)} pts)")
            else:
                print(f"    Cal:     offset={hx._offset:.0f}, factor={hx._scale:.4f}")
        except Exception as e:
            print(f"  {BOLD}{label}{RESET}: {RED}{e}{RESET}")
    print()


# ── Entry point ─────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    if not _GPIO_AVAILABLE:
        print(f"\n{RED}Cannot test load cells without RPi.GPIO (not on a Pi?).{RESET}")
        sys.exit(1)

    print(f"\n{BOLD}SmartSake Load Cell Test Tool{RESET}")
    print(f"{'─'*40}")

    # Parse scale filter
    scale_filter = None
    if args.scale:
        try:
            scale_filter = [int(s.strip()) for s in args.scale.split(",")]
        except ValueError:
            print(f"{RED}Invalid --scale value. Use comma-separated numbers (e.g., 1,3){RESET}")
            sys.exit(1)

    # Load config and init scales
    config = load_config()
    scales = init_scales(config, scale_filter)

    if not scales:
        print(f"\n{RED}No scales initialized. Check scale_config.json and wiring.{RESET}")
        GPIO.cleanup()
        sys.exit(1)

    try:
        # Optional tare
        if args.tare:
            run_tare(scales)

        # Single reading or continuous
        if args.once:
            samples = args.samples or 10
            run_single_reading(scales, samples)
        else:
            run_continuous(scales, args)
    finally:
        GPIO.cleanup()
        print(f"  {DIM}GPIO cleaned up.{RESET}")


if __name__ == "__main__":
    main()
