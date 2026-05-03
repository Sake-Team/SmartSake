#!/usr/bin/env python3
"""
identify_tcs.py — interactive one-shot probe-to-zone mapper.

Run this once per Pi (or after replacing any probe) to write a known-good
tc_zone_map.json. The runtime loader in WriteSensors.py is strict and will
refuse to start without a complete, validated map.

Usage:
  python scripts/identify_tcs.py            # interactive walkthrough
  python scripts/identify_tcs.py --monitor  # live readings only (no write)
  python scripts/identify_tcs.py --check    # validate existing map and exit
"""
import argparse
import json
import os
import sys
import time

# Allow running from anywhere
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, _REPO)

from sensors import discover_devices, read_temp_c, format_device_id, MAX_THERMOCOUPLES

TC_ZONE_MAP_FILE = os.path.join(_REPO, "tc_zone_map.json")
RISE_THRESHOLD_C = 2.0       # how much a probe must climb to be "the one being heated"
SAMPLE_INTERVAL  = 1.0       # seconds between reads in interactive mode
SETTLE_SECONDS   = 15        # seconds of sampling after user heats a probe


def _read_all(devices):
    """Return {device_id: temp_c or None} for every device on the bus."""
    out = {}
    for d in devices:
        did = format_device_id(d)
        try:
            out[did] = read_temp_c(d)
        except Exception:
            out[did] = None
    return out


def cmd_monitor():
    print("Live thermocouple readings. Ctrl-C to stop.\n")
    while True:
        devices = discover_devices()
        if not devices:
            print("No probes on the 1-Wire bus. Check wiring.")
            time.sleep(2)
            continue
        readings = _read_all(devices)
        line = "  ".join(
            f"{did}={t:6.2f}C" if t is not None else f"{did}=  err "
            for did, t in sorted(readings.items())
        )
        print(line, flush=True)
        time.sleep(SAMPLE_INTERVAL)


def cmd_check():
    if not os.path.exists(TC_ZONE_MAP_FILE):
        print(f"ERROR: {TC_ZONE_MAP_FILE} does not exist.")
        return 2
    try:
        with open(TC_ZONE_MAP_FILE) as f:
            mapping = json.load(f)
    except Exception as e:
        print(f"ERROR: {TC_ZONE_MAP_FILE} is not valid JSON: {e}")
        return 2

    if not mapping:
        print("ERROR: tc_zone_map.json is empty.")
        return 2

    channels = list(mapping.values())
    dupes = {c for c in channels if channels.count(c) > 1}
    if dupes:
        print(f"ERROR: duplicate zone numbers: {sorted(dupes)}")
        return 2

    missing = sorted(set(range(1, MAX_THERMOCOUPLES + 1)) - set(channels))
    if missing:
        print(f"ERROR: missing zones {missing} (need 1..{MAX_THERMOCOUPLES}).")
        return 2

    bad = [c for c in channels if not (1 <= c <= MAX_THERMOCOUPLES)]
    if bad:
        print(f"ERROR: out-of-range channels: {bad}")
        return 2

    print("tc_zone_map.json validates. Static assignments:")
    for did, ch in sorted(mapping.items(), key=lambda x: x[1]):
        print(f"  zone {ch}: {did}")

    on_bus = {format_device_id(d) for d in discover_devices()}
    mapped = set(mapping.keys())
    if on_bus and on_bus != mapped:
        if mapped - on_bus:
            print(f"WARN: in map but not on bus right now: {sorted(mapped - on_bus)}")
        if on_bus - mapped:
            print(f"WARN: on bus but not in map: {sorted(on_bus - mapped)}")
    return 0


def cmd_assign():
    devices = discover_devices()
    if len(devices) < MAX_THERMOCOUPLES:
        print(f"Found {len(devices)} probe(s); expected {MAX_THERMOCOUPLES}.")
        if input("Continue anyway? [y/N] ").strip().lower() != "y":
            return 1

    print(f"\nDiscovered {len(devices)} probe(s):")
    baseline = _read_all(devices)
    for did, t in sorted(baseline.items()):
        print(f"  {did}: {t}")
    print()

    if os.path.exists(TC_ZONE_MAP_FILE):
        try:
            with open(TC_ZONE_MAP_FILE) as f:
                existing = json.load(f)
        except Exception:
            existing = {}
        if existing:
            print(f"Existing map at {TC_ZONE_MAP_FILE}:")
            for did, ch in sorted(existing.items(), key=lambda x: x[1]):
                print(f"  zone {ch}: {did}")
            if input("\nOverwrite? [y/N] ").strip().lower() != "y":
                return 1

    mapping = {}
    used_ids = set()
    for zone in range(1, MAX_THERMOCOUPLES + 1):
        print(f"\n=== Zone {zone} ===")
        input(f"Heat the probe you want as zone {zone} (e.g., grip it firmly) "
              f"and press Enter to begin sampling. ")
        print(f"Sampling {SETTLE_SECONDS}s baseline + delta — keep heating...")

        baseline = _read_all(devices)
        time.sleep(SETTLE_SECONDS)
        delta = {}
        latest = _read_all(devices)
        for did, t in latest.items():
            b = baseline.get(did)
            if b is None or t is None or did in used_ids:
                continue
            delta[did] = t - b

        if not delta:
            print("  No usable readings. Try again.")
            return 1

        winner, rise = max(delta.items(), key=lambda kv: kv[1])
        print(f"  Largest rise: {winner}  Δ={rise:+.2f}°C")
        if rise < RISE_THRESHOLD_C:
            print(f"  WARN: rise below {RISE_THRESHOLD_C}°C threshold — probe may not "
                  f"have heated enough. Inspect deltas:")
            for did, dt in sorted(delta.items(), key=lambda kv: -kv[1]):
                print(f"    {did}  Δ={dt:+.2f}°C")
            ans = input(f"  Accept {winner} as zone {zone}? [y/N] ").strip().lower()
            if ans != "y":
                print("  Re-doing this zone.")
                # decrement zone counter by re-running this iteration
                # easiest: just recurse into a tiny retry loop
                while True:
                    input(f"Heat zone {zone} probe again, press Enter: ")
                    baseline = _read_all(devices)
                    time.sleep(SETTLE_SECONDS)
                    latest = _read_all(devices)
                    delta = {did: (latest[did] - baseline[did])
                             for did in latest
                             if latest[did] is not None
                             and baseline.get(did) is not None
                             and did not in used_ids}
                    if not delta:
                        continue
                    winner, rise = max(delta.items(), key=lambda kv: kv[1])
                    print(f"  Largest rise: {winner}  Δ={rise:+.2f}°C")
                    if input(f"  Accept {winner} as zone {zone}? [y/N] ").strip().lower() == "y":
                        break

        mapping[winner] = zone
        used_ids.add(winner)
        print(f"  → zone {zone} = {winner}")
        print("  Let the probe cool for a few seconds before the next zone...")
        time.sleep(3)

    print("\nFinal mapping:")
    for did, ch in sorted(mapping.items(), key=lambda x: x[1]):
        print(f"  zone {ch}: {did}")

    if input(f"\nWrite to {TC_ZONE_MAP_FILE}? [y/N] ").strip().lower() != "y":
        print("Aborted. No file written.")
        return 1

    tmp = TC_ZONE_MAP_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(mapping, f, indent=2, sort_keys=True)
    os.replace(tmp, TC_ZONE_MAP_FILE)
    print(f"Wrote {TC_ZONE_MAP_FILE}.")
    print("Restart the SmartSake service for the new map to take effect.")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--monitor", action="store_true", help="live readings only")
    g.add_argument("--check",   action="store_true", help="validate existing map and exit")
    args = p.parse_args()

    if args.monitor:
        try:
            cmd_monitor()
        except KeyboardInterrupt:
            pass
        return 0
    if args.check:
        return cmd_check()
    return cmd_assign()


if __name__ == "__main__":
    sys.exit(main())
