# SmartSake Debug Playbooks

Two systems land in the field with the most ways to misbehave: **automatic relay switching** and **load cell calibration**. Each section below walks how it works, what to look at, the command cookbook, and a symptom → cause decision tree.

When in doubt, read the live state files first (`/run/smartsake/*.json`) before assuming the code is wrong. The state files are produced by the same loop that drives the relays, so they tell you exactly what the loop *thinks* is happening.

---

# Part 1 — Automatic relay switching

## How it works (one tick)

Every `SENSOR_INTERVAL_S = 10s`, `start_sensor_loop()` in `WriteSensors.py` does:

1. Hot-reload `tc_zone_map.json` (mtime check).
2. Read all 1-Wire thermocouples, median-filter each.
3. Look up the active run (`db.get_active_run()`); if there is one, call `evaluate_fan_state(run, tc_readings)`. Otherwise call `evaluate_fan_state_no_run(tc_readings)`.
4. For each zone 1–6, call `fan_gpio.set_fan(z, on)` with the desired action.
5. Write `/run/smartsake/fan_state.json` with the per-zone result + reason metadata.
6. Insert a sample row in `samples` (DB) if a run is active.

### Decision hierarchy inside `evaluate_fan_state`

For each zone 1–6, in order:

1. **DB-backed override** (`fan_overrides` table, written by dashboard "Override" UI). If present, sets `mode = "manual"`, action = `on`/`off`. Auto loop is bypassed for this zone. `_fan_on[zone]` is mirrored so a return to auto inherits the right hysteresis state.
2. **Fan rule** (`fan_rules` table — time-window or threshold rules). If a rule fires, sets `mode = "rule"`, action = the rule's `fan_action`. `_threshold_breach_start` tracks how long a threshold has been exceeded for the rule's `threshold_dur_min` requirement.
3. **Curve setpoint** (`target_temp_profile` interp from elapsed minutes). If present, sets `mode = "limit"`, source = `"curve"`.
4. **Zone-config setpoint** (`zone_config.json["zoneN"]["setpoint_c"]` or `default.setpoint_c`). Same `mode = "limit"`, source = `"config"`.
5. **No setpoint at all** → `mode = "none"`, action = `"off"`. Auto loop will never turn this fan on.

### The limit-switch math (modes 3–4)

```python
current_on = _fan_on[zone]                    # last tick's commit
trigger    = setpoint + tolerance             # default tolerance = 1.0 °C
if actual > trigger:        desired_on = True
elif actual <= setpoint:    desired_on = False
else:                       desired_on = current_on   # hysteresis band

if desired_on != current_on:
    _fan_hold_counts[zone] += 1
    if _fan_hold_counts[zone] >= DEADBAND_HOLD:   # = 1 → next tick commits
        fan_on = desired_on
        _fan_hold_counts[zone] = 0
    else:
        fan_on = current_on                       # not yet — keep last state
else:
    _fan_hold_counts[zone] = 0
    fan_on = desired_on
```

So: a zone stays in its last state inside `(setpoint, setpoint+tolerance]`. Crossing above `trigger` arms an ON transition; dropping to `setpoint` or below arms an OFF transition. With `DEADBAND_HOLD = 1` the transition commits on the next tick (~10s later), which is the relay-life protection.

### Active-LOW relays

`fan_gpio.set_fan(z, True)` writes GPIO **LOW** to the pin (relay coil energized → fan ON). False writes HIGH (coil de-energized → fan OFF). `init_fans()` sets every pin to HIGH at boot. The shutdown handler (`SIGTERM`, `SIGINT`) drives every pin HIGH then calls `cleanup()`, so a clean restart leaves all fans OFF. **`SIGKILL` and power loss bypass this** — relays hold their last state until the next boot.

### Override expiry — the subtle bug class

Both run-mode and no-run-mode overrides can have an `expires_at`. When an override expires between ticks, the zone falls back to auto. If the auto loop's hysteresis state (`_fan_on[zone]`) still says `True` from the override, and the temp is sitting inside the deadband, the fan will keep running indefinitely. Both eval functions explicitly call `reset_auto_hysteresis(z)` for any zone whose override just disappeared (`_last_run_override_zones - override_zones` in the run path; `_purge_expired_no_run_overrides` for the no-run path). If you ever see a fan stuck on after a manual override expires, check this is still firing.

### What gets logged

- `[fan]` lines — per-transition events: override start/expiry, hysteresis reset, GPIO write failures.
- `[fan-auto]` lines — every ~60s (every 6 ticks): one-line summary across all zones, e.g. `[fan-auto] z1:21.3→OFF(sp=22,tr=23) | z2:24.7→ON(sp=22,tr=23) | z3:noTC | z4:MAN-on | z5:no-sp | z6:21.0→OFF(sp=22,tr=23)`.
- All goes through stdout, captured by journalctl.

---

## Diagnostic command cookbook (auto fan)

```bash
# Live fan log (transitions + every-60s summary)
journalctl -u smartsake -f | grep -E '\[fan'

# Boot log — was init_fans() called, are the pins set?
journalctl -u smartsake -b | grep -E 'Fan zone|GPIO|sensors\]'

# Current per-zone decision the loop just made
cat /run/smartsake/fan_state.json | jq
# → {"zone1": {"action": "off", "mode": "limit", "setpoint_c": 22.0, "trigger_c": 23.0, ...}, ...}

# Current temps (what the limit-switch is comparing against)
cat /run/smartsake/sensor_latest.json | jq '.tc_1, .tc_2, .tc_3, .tc_4, .tc_5, .tc_6'

# What's the actual GPIO line state? (active-LOW: 0 = fan ON, 1 = fan OFF)
pinctrl get 17,22,23,24,25,27       # newer Pi OS
gpioinfo | grep -E 'line.*(17|22|23|24|25|27):'   # libgpiod path

# Active overrides in the DB (run mode)
sqlite3 smartsake.db "SELECT run_id, zone, action, expires_at FROM fan_overrides
                      WHERE expires_at IS NULL OR expires_at > datetime('now')
                      ORDER BY zone;"

# Active no-run overrides
cat no_run_overrides.json 2>/dev/null | jq

# Active rules for current run
sqlite3 smartsake.db "SELECT * FROM fan_rules WHERE enabled=1
                      AND run_id=(SELECT id FROM runs WHERE ended_at IS NULL LIMIT 1);"

# Current zone setpoints + tolerances (the file the auto loop reads)
cat zone_config.json | jq

# Smoke-test the fan state machine without hardware (uses mocked db + GPIO)
python3 test_fan_state.py

# Failure-injection coverage (mocks set_fan/insert_reading raising mid-tick)
python3 test_failure_injection.py

# Force every relay OFF immediately, no service interaction
python3 -c "import fan_gpio; fan_gpio.init_fans(); [fan_gpio.set_fan(z, False) for z in range(1,7)]; fan_gpio.cleanup()"
```

## Symptom → cause decision tree (auto fan)

### "Fan won't turn on"

1. **Is there a manual override locking it OFF?**
   `cat /run/smartsake/fan_state.json | jq '.zoneN.mode'` — if `"manual"`, the override is winning. Check `fan_overrides` (run) or `no_run_overrides.json`.
2. **Is there a setpoint at all?**
   Mode `"none"` means no curve and no `zone_config.json` setpoint resolved for this zone. The limit-switch never runs. Either start a run with a curve, or set `zone_config.json["zoneN"]["setpoint_c"]`.
3. **Is the TC reading present?**
   Mode `"none"` *with* a setpoint usually means `tc_map.get(zone)` returned None. Check `sensor_latest.json` for `tc_N`. If null, check `tc_zone_map.json` mapping and the 1-Wire bus (`ls /sys/bus/w1/devices/`).
4. **Is the trigger threshold being hit?**
   `[fan-auto]` log will show `z3:21.5→OFF(sp=22,tr=23)` — actual is below trigger, so OFF is correct. Either lower the setpoint/tolerance or wait.
5. **Hysteresis hold blocking?**
   With `DEADBAND_HOLD = 1`, this is rare — should commit on the next tick. If you see `desired_on != current_on` in two consecutive `[fan-auto]` ticks but no transition, something is reseting `_fan_hold_counts`.
6. **GPIO is firing but no fan?**
   `pinctrl get <pin>` shows LOW but no relay click → relay board issue: 12V (or 5V) coil supply absent, jumper unseated, or the SunFounder TS0012 needs its enable jumper. **Active-LOW means a floating GPIO at boot can briefly latch ON** — confirmed start state should be HIGH.

### "Fan won't turn off"

1. **Override stuck on?** Check `fan_overrides` table and `no_run_overrides.json`. If an override has no expiry and was never cleared, it'll hold forever.
2. **Did an override just expire?** If it expired but the fan is still on, the hysteresis-reset path may not have fired. Check `[fan]` lines for `reset_auto_hysteresis` after override expiry. The bug class is documented above.
3. **Curve setpoint at this elapsed minute is high?** Curves can have flat-high regions. `cat /run/smartsake/fan_state.json | jq '.zoneN.setpoint_c'`.
4. **A fan rule is firing** — `mode = "rule"`. Check `fan_rules` table. Rules trump auto.
5. **Service crashed mid-cycle.** SIGKILL or power loss leaves the relay coil in its last state. Restart resets via `init_fans()` (HIGH at boot). If the service is still alive but stuck, `journalctl -u smartsake -f` will show whether the loop is still ticking.

### "Fans oscillate / chatter"

1. **Tolerance too small.** With `tolerance_c = 0`, *any* drift across the setpoint flips the fan. Min recommended: 1.0 °C. Set in `zone_config.json["zoneN"]["tolerance_c"]`. Hot-reloads.
2. **TC noise.** TC reads are median-filtered (`TC_FILTER_WINDOW`), but a flapping probe contact can still cause spikes. `journalctl -u smartsake -f | grep -E 'tc|MAX31850'` for read errors. Check probe physical mount.
3. **`DEADBAND_HOLD` too low.** Default = 1 (commits next tick). Bumping to 2 doubles the dwell at the cost of a 10s slower response.

### "Fans on at boot, before any logic runs"

This is a wiring/relay polarity problem, not a software bug.

- **`init_fans()` sets every pin HIGH.** Active-LOW relays interpret HIGH as OFF. So pins-HIGH-at-boot is correct.
- If a relay is ON before `init_fans()` runs (the ~3 second `ExecStartPre=/bin/sleep 3` window), it's because the GPIO is floating LOW *or* the relay board is wired active-HIGH. Confirm the SunFounder TS0012 jumper position. Verify with `pinctrl get` immediately after a fresh boot.

### "Service restart works but power-cycle leaves a fan on"

Expected. SIGKILL/power-loss bypasses the shutdown handler, so the relay coil holds its last energized state. Hard cutoff: kill the relay board's 12V supply, or pull AC. The dashboard `/api/runs/<id>/emergency-stop` endpoint forces all fans OFF via the running service.

### "Looks right in logs but the load isn't switching"

This is downstream of GPIO. Confirm with a multimeter at the relay terminals (NO/NC swap, a dead board), the 120 VAC contactor (some hardware uses a TS0012 → contactor stage), and the fuse on the fan rail.

---

# Part 2 — Load cell calibration

## How it works

Each scale 1–4 has its own HX711 amplifier on a unique `(DAT, CLK)` GPIO pair. `start_sensor_loop()` reads `scale_config.json`, instantiates one `HX711` per configured scale (those with non-null pins), and spawns a daemon thread per scale via `run_hx711_thread`.

### The HX711 thread (every `WEIGHT_INTERVAL_S = 30s`)

```
hot-reload scale_config.json by mtime → push new tare/factor/points into the live HX711 instance
get_weight(samples=10) → median-filtered raw, weight_kg
write to weight_state[scale_id] (in-memory, primary path)
write scale_<id>_data.json (secondary log)
sleep 30s
```

Hot-reload means **edits to `scale_config.json` take effect within ~30s** without service restart. The dashboard calibration API uses this exact path.

### Two calibration modes

- **Single-point** — `tare_offset` (raw with empty cell) + `calibration_factor` (raw delta per gram). Math: `(raw - tare_offset) / calibration_factor = grams`. Falls into this mode when no `calibration_points` array is present.
- **Multi-point** — `calibration_points: [{raw, weight_g, label}, …]`. Requires ≥2 points. Piecewise-linear interp inside the range; nearest-segment slope extrapolated outside. Whenever ≥2 points exist, the server **also** auto-derives legacy `tare_offset` and `calibration_factor` from the min and max points for backwards compat.

### Server endpoints (calibration page)

All wrap their config writes in `_AtomicCfgSection` (RLock + atomic file replace) so parallel calls (e.g. "Calibrate All" firing 4 POSTs at once) can't clobber each other.

| Endpoint | Effect |
|---|---|
| `POST /api/scale-config/<id>/tare` | Read the live raw value, write `tare_offset`. |
| `POST /api/scale-config/<id>/calibrate` | Body `{kg: <known weight>}`. Computes factor from `(raw - tare) / known_weight_g`. Rejects factor < 1e-3. |
| `POST /api/scale-config/<id>/manual-set` | Body `{tare_offset, calibration_factor}`. Direct edit. Drops `calibration_points`. |
| `POST /api/scale-config/<id>/cal-point` | Body `{weight_g, label}`. Reads current raw, appends to `calibration_points`. Once ≥2 points, derives legacy fields. |
| `POST /api/scale-config/<id>/clear-cal-points` | Drops `calibration_points`. Falls back to single-point. |
| `POST /api/scales/calibrate-all` | Splits a known total weight evenly across 4 cells, calibrates each. |

### Raw value sanity

HX711 24-bit signed range is ±8388608. The driver clamps to ±9000000 (a clipped/floating value gets `[SKIP] Rejected bad raw read` and falls out of the average). With 10 samples per read, the median filter rejects values >1.5×IQR from the median (or a min margin of 500). If all 10 reads are bad, `read_average` raises `ValueError("No valid readings -- check wiring")`.

### `_offset` of 0 vs `tare_offset` of 0

A factory-fresh `scale_config.json` has `tare_offset: 0` for unwired scales — that's not "tared", it's "uncalibrated". You can tell by checking `cat scale_config.json | jq '.scales."N".tare_offset'` against the live raw — if they're far apart, this scale was never tared.

---

## Diagnostic command cookbook (load cell)

```bash
# What's wired and what's calibrated?
python3 load_cell_hx711.py --list
# → Scale 1 (Scale 1): pins=5/6 [configured], calibration [single-point]
# → Scale 4 (Scale 4): pins=20/21 [configured], calibration [uncalibrated]

# Live raw + weight, all 4 scales
watch -n 1 'cat /run/smartsake/sensor_latest.json | jq "{w1: .weight_kg_1, w2: .weight_kg_2, w3: .weight_kg_3, w4: .weight_kg_4, raw1: .weight_raw_1, raw2: .weight_raw_2, raw3: .weight_raw_3, raw4: .weight_raw_4}"'

# Or via API (same data path, no jq)
curl -s http://localhost:8080/api/sensor-latest | jq '.weight_kg_1, .weight_raw_1'

# Just the config for one scale
cat scale_config.json | jq '.scales."1"'

# HX711 init + read errors in the live log
journalctl -u smartsake -f | grep -E 'HX711|scale [0-9]'

# Boot log — did each scale init?
journalctl -u smartsake -b | grep -E 'HX711.*initialized|scale [0-9].*calibration'

# Re-tare a single scale (CLI, requires service stopped — both will fight for the GPIO)
sudo systemctl stop smartsake
python3 load_cell_hx711.py --tare --scale 1
sudo systemctl start smartsake

# Single-point cal via CLI (same caveat — stop the service first)
python3 load_cell_hx711.py --calibrate --scale 1

# Multi-point cal via CLI
python3 load_cell_hx711.py --calibrate-multipoint --scale 1

# Smoke test — read each configured scale 10× and print raw + weight
python3 test_scales.py

# Live re-tare via API (service running, no stop needed)
curl -X POST http://localhost:8080/api/scale-config/1/tare

# Live single-point cal via API (place known weight first)
curl -X POST -H 'Content-Type: application/json' \
  -d '{"kg": 2.5}' \
  http://localhost:8080/api/scale-config/1/calibrate

# Manual set (skip the read, type in known-good values)
curl -X POST -H 'Content-Type: application/json' \
  -d '{"tare_offset": 4166, "calibration_factor": 8000}' \
  http://localhost:8080/api/scale-config/1/manual-set

# Clear multi-point curve
curl -X POST http://localhost:8080/api/scale-config/1/clear-cal-points
```

## Symptom → cause decision tree (load cell)

### "Scale shows 0 / never updates"

1. **Is the scale even configured?** `python3 load_cell_hx711.py --list` — if it shows `[not wired]`, `dat_pin`/`clk_pin` are null in `scale_config.json`. Set them, restart service.
2. **Did the HX711 init?** `journalctl -u smartsake -b | grep "HX711 scale 1 initialized"` should be there. If you see `[WARN] Scale 1 failed to init: ...`, the cause is in the message — usually `RPi.GPIO.setup` failing on a reserved pin or `HX711 not responding -- check wiring!`.
3. **Pin conflict.** If you've put a fan relay or another HX711 on a reserved pin, both will fight. Cross-check `fan_gpio.FAN_PINS` and `scale_config.json`. The **GPIO map** in `CLAUDE.md` is authoritative.
4. **Serial console blocking pin 15** (scale 2 CLK). `sudo raspi-config` → Interface → Serial Port → No login shell, **Yes** hardware. Reboot.
5. **Wiring.** Float on DAT (jumper unseated) → driver hits `TimeoutError("HX711 not responding")` after 1 s and the read fails. Check continuity DAT and CLK.

### "Weight is wildly off"

1. **Is the cell tared?** Empty raw should be near `tare_offset`. `cat scale_config.json | jq '.scales."1".tare_offset'` vs `cat /run/smartsake/sensor_latest.json | jq '.weight_raw_1'`. Near-equal under empty load = tared. Far apart = re-tare.
2. **Sign flipped.** If load makes weight go *negative*, the cell's signal A+/A- is swapped. Either swap the wires or accept a negative `calibration_factor` (works fine — math is symmetric).
3. **Factor calibrated against the wrong weight.** Replace via `--calibrate` CLI or the dashboard. Math sanity: `(raw_under_load - tare_offset) / known_weight_grams = factor`. The shipping default `calibration_factor = 8000` is a placeholder for the FX29X 040A — your physical cell will land somewhere around there but rarely exactly.
4. **Multi-point overrides single-point.** If `calibration_points` array exists (≥2 entries), it wins. Check `cat scale_config.json | jq '.scales."1".calibration_points'`. Either delete the array (`/clear-cal-points` endpoint) or recalibrate it.

### "Weight drifts / wanders by hundreds of grams"

1. **HX711 warm-up.** The amp is temperature-sensitive. After power-on, give it 5–10 min before calibrating or trusting weights. Watching `weight_raw_N` in `sensor_latest.json` should show the drift settling.
2. **Mechanical creep.** The 233 FX29X is a strain gauge — under sustained load it can creep ~0.1% over hours. For a 60 kg load, that's ±60 g. Visible in `room-history.html`.
3. **Vibration / cable microphonics.** Unshielded HX711 cabling near the fan motor or the AC contactor will pick up noise. Route HX711 wiring away from the relay board and AC mains. Twist DAT/CLK pairs.
4. **Excitation supply sag.** HX711 is ratiometric, so excitation noise mostly cancels — but if 5V rail is sagging hard (Pi underpowered, weak USB-C supply), you'll see drift correlated with CPU load.

### "Calibrate All split a 4 kg weight, only one scale's cal stuck"

This was the concurrent-write race; fixed by `_AtomicCfgSection`. If you see it again, a regression has slipped in. Look at `_scale_cfg_section` usage in `server.py` — every read-modify-write of `scale_config.json` should go through it.

### "Multi-point cal looks right but `weight_kg` doesn't match the points"

1. **Order matters for the legacy derive.** The server sorts points by `weight_g` and uses min/max to derive `tare_offset` and `calibration_factor`. The interpolation itself uses the full sorted list. So: with 5 well-distributed points, interp is accurate; with 2 points where the heavy weight is `0.1 kg`, the legacy derive is fine but interp is just a single line.
2. **Duplicate raw values.** `set_calibration_points` dedupes by `raw`, keeping the *last* entry per raw. If you logged the same point twice with different weights (typo), the last one wins. Watch the `[WARN]` line in the journal.
3. **Out-of-range loads.** Loads outside the calibrated raw range get extrapolated using the slope of the nearest segment. Calibrate at the edges of your expected use range.

### "Two scales read the same value when only one is loaded"

Mechanical, not electrical. The koji table top is one rigid plate distributing across 4 cells — that's by design. **Center load splits ~25% per cell**. If pressing one corner moves all four cells, the load path is correct. If pressing one corner *also* moves the diagonal corner more than the adjacent ones, the table is twisting — check the housing torque and the cell mounting.

### "HX711 read error: HX711 not responding -- check wiring!"

The driver waits up to 1 s for `DAT` to go LOW (data-ready). If it never does:

1. DAT pin not actually connected to HX711 OUT. Verify with continuity meter.
2. HX711 not powered (5V/GND).
3. The HX711 entered low-power mode — toggling CLK HIGH-then-LOW resets it. The driver does this automatically in `reset()`, but a flaky CLK line means it never lands.
4. Wrong pins assigned in `scale_config.json` (you set DAT to a pin that's actually wired as CLK).

---

# When all else fails

- **Stop the service, run the standalone CLI** — `sudo systemctl stop smartsake; python3 WriteSensors.py`. Stdout goes to your terminal directly, no journal indirection.
- **Walk through one tick by hand** — set a breakpoint or `print` in `evaluate_fan_state`, fire one read with a known TC value, see what the limit-switch decides.
- **Check `test_failure_injection.py`** — it lists the things that have already gone wrong once. If your symptom matches a test scenario, the test name will tell you what was originally fixed and where to look.
