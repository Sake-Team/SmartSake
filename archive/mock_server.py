"""
mock_server.py — Local UI preview server for SmartSake.

Serves the real HTML/CSS/JS from the project root but returns realistic
mock data for all API endpoints. No hardware, no Pi, no sensors required.

Usage:
    pip install flask
    python archive/mock_server.py
    # then open http://localhost:8080 in your browser
"""

import json
import math
import os
import random
import time
from datetime import datetime, timedelta

from flask import Flask, jsonify, request, send_from_directory

# Serve static files from the project root, not the archive/ directory.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now():
    return datetime.now().isoformat(timespec="milliseconds")

def _fake_temp(base=32.0, drift=0.8):
    """Simulate a thermocouple reading with slight drift."""
    return round(base + random.uniform(-drift, drift), 2)

def _oscillating_temp(zone_index, setpoint=33.0, amplitude=6.0, period_s=60.0):
    """
    Return a temperature that cycles through cold→ok→warm→hot→warm→ok→cold.
    Each zone is phase-shifted so all four states are visible simultaneously.
    """
    phase = (zone_index / 6.0) * 2 * math.pi
    t = time.time()
    sine = math.sin((2 * math.pi * t / period_s) + phase)
    base = setpoint + amplitude * sine
    return round(base + random.uniform(-0.2, 0.2), 2)

def _fake_readings(run_id, n=120):
    """Generate n fake historical readings spread over the last 24 hours."""
    now = datetime.now()
    readings = []
    for i in range(n):
        t = now - timedelta(minutes=(n - i) * 12)
        progress = i / n
        base = 28 + 8 * math.sin(progress * math.pi)
        readings.append({
            "id": i + 1,
            "run_id": run_id,
            "recorded_at": t.isoformat(timespec="seconds"),
            "tc1": round(base + random.uniform(-0.5, 0.5), 2),
            "tc2": round(base + 1.2 + random.uniform(-0.5, 0.5), 2),
            "tc3": round(base + 0.8 + random.uniform(-0.5, 0.5), 2),
            "tc4": round(base - 0.3 + random.uniform(-0.5, 0.5), 2),
            "tc5": round(base + 1.5 + random.uniform(-0.5, 0.5), 2),
            "tc6": round(base - 0.8 + random.uniform(-0.5, 0.5), 2),
            "sht_temp": round(24.5 + random.uniform(-0.3, 0.3), 2),
            "humidity": round(87.0 + random.uniform(-2.0, 2.0), 2),
            "fan1": 1 if progress > 0.4 else 0,
            "fan2": 1 if progress > 0.5 else 0,
            "fan3": 0,
            "fan4": 0,
            "fan5": 1 if progress > 0.6 else 0,
            "fan6": 0,
            "weight_lbs": round(22.0 - progress * 2.5 + random.uniform(-0.05, 0.05), 3),
            "weight_lbs_1": round(5.5 - progress * 0.6, 3),
            "weight_lbs_2": round(5.6 - progress * 0.6, 3),
            "weight_lbs_3": round(5.4 - progress * 0.6, 3),
            "weight_lbs_4": round(5.5 - progress * 0.7, 3),
        })
    return readings

MOCK_RUN = {
    "id": 1,
    "name": "Batch #7 — Yamada Nishiki",
    "started_at": (datetime.now() - timedelta(hours=18)).isoformat(timespec="seconds"),
    "ended_at": None,
    "status": "active",
    "pinned": 0,
    "notes": "Mock data — local preview mode",
    "last_reading_at": _now(),
    "humidity_target_min": 82.0,
    "humidity_target_max": 92.0,
    "weight_target_min": 19.5,
    "weight_target_max": 21.0,
    "tc_zones_mapped": 6,
}

MOCK_READINGS = _fake_readings(1, 120)

def _koji_curve():
    """Build a 48h ginjo koji target profile — one shared target for all zones."""
    base = [
        (0,    28.0),
        (360,  30.5),
        (720,  33.5),
        (1080, 36.5),
        (1440, 39.0),
        (1680, 41.0),
        (2160, 39.0),
        (2520, 36.0),
        (2880, 34.0),
    ]
    return [{"elapsed_min": t, "temp_target": b} for t, b in base]

MOCK_REFERENCE_CURVES = [
    {
        "id": 1,
        "name": "Ginjo Koji (48h)",
        "description": "Standard 48-hour temperature curve for Yamada Nishiki ginjo-grade koji",
        "source": "Traditional reference — Niida Honke method",
        "points": _koji_curve(),
    },
    {
        "id": 2,
        "name": "Mugi Koji / Shochu (44h)",
        "description": "Barley koji for shochu — faster ramp, sustained high plateau",
        "source": "Kagoshima reference",
        "points": [
            {"elapsed_min": t, "temp_target": b}
            for t, b in [(0,30),(240,33),(600,36),(960,40),(1440,42),(1920,40),(2640,36)]
        ],
    },
]

# ── Static pages ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "home.html")

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(BASE_DIR, filename)


# ── Shared mock zone state ────────────────────────────────────────────────────

# Setpoint matches the curve at ~18h (1080 min) — naka-shigoto stage
MOCK_ZONE_SETPOINTS  = {z: 36.5 for z in range(1, 7)}
MOCK_ZONE_MODES      = {i: "auto" for i in range(1, 7)}
MOCK_ZONE_TOLERANCES = {i: 1.0   for i in range(1, 7)}

# Per-run fan overrides keyed by (run_id, zone)
MOCK_FAN_OVERRIDES = {}
# Run-less fan overrides keyed by zone ("on"/"off")
MOCK_NO_RUN_OVERRIDES = {}

def _build_sensor_payload():
    """Build the sensor_latest.json shape used by all zone pages and home."""
    thermocouples = {f"TC{i}": _oscillating_temp(i - 1, setpoint=MOCK_ZONE_SETPOINTS[i]) for i in range(1, 7)}
    sht_temp = _fake_temp(24.5, 0.2)
    humidity = _fake_temp(87.0, 1.5)

    zones = {}
    for i in range(1, 7):
        sp      = MOCK_ZONE_SETPOINTS[i]
        dry     = thermocouples[f"TC{i}"]
        trigger = round(sp + MOCK_ZONE_TOLERANCES[i], 2)
        relay_on = dry > trigger
        zones[i] = {
            "setpoint_c":   sp,
            "tolerance_c":  MOCK_ZONE_TOLERANCES[i],
            "mode":         MOCK_ZONE_MODES[i],
            "relay_state":  relay_on,
            "trigger_c":    trigger,
            "alarm_level":  None,
            "alarm_reason": None,
            "alarm_thresholds": {
                "warn_high_c": sp + 4.0,
                "crit_high_c": sp + 7.0,
                "warn_low_c":  sp - 5.0,
                "crit_low_c":  sp - 8.0,
            },
        }

    return {
        "timestamp": _now(),
        "thermocouples": thermocouples,
        "sht30": {"temp_c": sht_temp, "humidity_rh": humidity},
        "zones": zones,
        # Legacy flat keys for /api/latest consumers
        "tc1": thermocouples["TC1"], "tc2": thermocouples["TC2"],
        "tc3": thermocouples["TC3"], "tc4": thermocouples["TC4"],
        "tc5": thermocouples["TC5"], "tc6": thermocouples["TC6"],
        "sht_temp": sht_temp, "humidity": humidity,
        "fan1": int(zones[1]["relay_state"]), "fan2": int(zones[2]["relay_state"]),
        "fan3": int(zones[3]["relay_state"]), "fan4": int(zones[4]["relay_state"]),
        "fan5": int(zones[5]["relay_state"]), "fan6": int(zones[6]["relay_state"]),
        "weight_kg_1": 2.232, "weight_kg_2": 2.259,
        "weight_kg_3": 2.200, "weight_kg_4": 2.245,
        "weight_raw_1": 412345, "weight_raw_2": 418200,
        "weight_raw_3": 405100, "weight_raw_4": 415300,
        "weight_lbs_1": 4.92, "weight_lbs_2": 4.98,
        "weight_lbs_3": 4.85, "weight_lbs_4": 4.95,
        "weight_lbs": 4.92, "weight_total_lbs": 19.70,
    }


# ── Live sensor feed ──────────────────────────────────────────────────────────

@app.route("/sensor_latest.json")
def sensor_latest():
    """Dynamic mock for the sensor_latest.json static file all zone pages poll."""
    return jsonify(_build_sensor_payload())

@app.route("/api/latest")
def api_latest():
    return jsonify(_build_sensor_payload())


# ── Sensor loop status ────────────────────────────────────────────────────────

@app.route("/api/sensor-status")
def api_sensor_status():
    return jsonify({
        "sensor_file_age_s":   2.1,
        "sensor_file_ts":      _now(),
        "last_db_write_age_s": 9.7,
        "active_run_id":       MOCK_RUN["id"] if MOCK_RUN["status"] == "active" else None,
        "active_run":          MOCK_RUN["name"] if MOCK_RUN["status"] == "active" else None,
        "loop_status":         None,
        "libs": {
            "thermocouples": False,
            "hx711_scales":  False,
            "sht30":         False,
        },
    })


# ── Health ────────────────────────────────────────────────────────────────────

@app.route("/api/health")
def api_health():
    return jsonify({
        "sht30": {"status": "ok", "temp_c": 24.5, "humidity": 87.0},
        "thermocouples": {str(i): {"status": "ok", "temp_c": 32.0 + i * 0.3} for i in range(1, 7)},
        "scales": {
            "1": {"status": "ok", "weight_kg": 2.23},
            "2": {"status": "ok", "weight_kg": 2.26},
            "3": {"status": "ok", "weight_kg": 2.20},
            "4": {"status": "ok", "weight_kg": 2.25},
        },
        "relays": {str(i): {"status": "wired"} for i in range(1, 7)},
        "ready": True,
        "sensor_age_s": 4.2,
    })


# ── Runs ──────────────────────────────────────────────────────────────────────

@app.route("/api/runs", methods=["GET"])
def api_list_runs():
    return jsonify([MOCK_RUN])

@app.route("/api/runs/active", methods=["GET"])
def api_active_run():
    if MOCK_RUN["status"] != "active":
        return jsonify({"error": "No active run"}), 404
    return jsonify(MOCK_RUN)

@app.route("/api/runs/completed", methods=["GET"])
def api_runs_completed():
    return jsonify([])

@app.route("/api/runs/<int:run_id>", methods=["GET"])
def api_get_run(run_id):
    return jsonify(MOCK_RUN)

@app.route("/api/runs", methods=["POST"])
def api_create_run():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    new_run = dict(MOCK_RUN)
    new_run["name"] = name
    new_run["status"] = "active"
    new_run["started_at"] = datetime.now().isoformat(timespec="seconds")
    new_run["ended_at"] = None
    return jsonify(new_run), 201

@app.route("/api/runs/<int:run_id>/end", methods=["POST"])
def api_end_run(run_id):
    ended = dict(MOCK_RUN)
    ended["status"] = "completed"
    ended["ended_at"] = datetime.now().isoformat(timespec="seconds")
    return jsonify(ended)

@app.route("/api/runs/<int:run_id>", methods=["DELETE"])
def api_delete_run(run_id):
    return "", 204

@app.route("/api/runs/<int:run_id>/pin", methods=["POST"])
def api_pin_run(run_id):
    body = request.get_json(silent=True) or {}
    return jsonify({"ok": True, "pinned": bool(body.get("pinned", True))})

@app.route("/api/prune", methods=["POST"])
def api_prune_runs():
    body = request.get_json(silent=True) or {}
    return jsonify({
        "pruned": 0,
        "free_mb_before": 4096,
        "free_mb_after":  4096,
    })


# ── Readings ──────────────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/readings", methods=["GET"])
def api_readings(run_id):
    n = request.args.get('n', 600, type=int)
    n = max(1, min(n, 10000))
    return jsonify(MOCK_READINGS[-n:])

@app.route("/api/runs/<int:run_id>/latest", methods=["GET"])
def api_run_latest(run_id):
    return jsonify(MOCK_READINGS[-1])


# ── Target profile ────────────────────────────────────────────────────────────

MOCK_TARGET = _koji_curve()

@app.route("/api/runs/<int:run_id>/target", methods=["GET"])
def api_get_target(run_id):
    return jsonify(MOCK_TARGET)

@app.route("/api/runs/<int:run_id>/target", methods=["POST"])
def api_save_target(run_id):
    return jsonify(MOCK_TARGET), 201

@app.route("/api/runs/<int:run_id>/target", methods=["DELETE"])
def api_clear_target(run_id):
    return "", 204


# ── Zone notes ────────────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/zones", methods=["GET"])
def api_get_all_notes(run_id):
    return jsonify({"1": "Inoculated 0800", "2": "", "3": "", "4": "", "5": "", "6": ""})

@app.route("/api/runs/<int:run_id>/zones/<int:zone>", methods=["GET"])
def api_get_zone_note(run_id, zone):
    return jsonify({"note": "Inoculated 0800" if zone == 1 else ""})

@app.route("/api/runs/<int:run_id>/zones/<int:zone>", methods=["PUT"])
def api_save_zone_note(run_id, zone):
    return jsonify({"ok": True})


# ── Zone control ──────────────────────────────────────────────────────────────

@app.route("/update_zone", methods=["POST"])
def update_zone():
    data = request.get_json(silent=True) or {}
    zone = int(data.get("zone", 0))
    if zone in MOCK_ZONE_SETPOINTS:
        if "setpoint_c" in data:
            MOCK_ZONE_SETPOINTS[zone] = float(data["setpoint_c"])
        if "mode" in data:
            MOCK_ZONE_MODES[zone] = data["mode"]
        if "tolerance_c" in data:
            MOCK_ZONE_TOLERANCES[zone] = float(data["tolerance_c"])
    return jsonify({"ok": True})


# ── Fan overrides ─────────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/fan-overrides", methods=["GET"])
def api_get_fan_overrides(run_id):
    return jsonify({"overrides": {z: o for (r, z), o in MOCK_FAN_OVERRIDES.items() if r == run_id}})

@app.route("/api/runs/<int:run_id>/zones/<int:zone>/fan", methods=["POST"])
def api_set_fan_override(run_id, zone):
    if zone < 1 or zone > 6:
        return jsonify({"error": "zone must be 1-6"}), 400
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    if action not in ("on", "off"):
        return jsonify({"error": "action must be 'on' or 'off'"}), 400
    duration = body.get("duration_minutes")
    if duration is not None:
        try:
            duration = int(duration)
            if duration <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "duration_minutes must be a positive integer or null"}), 400
    expires_at = None
    if duration is not None:
        expires_at = (datetime.now() + timedelta(minutes=duration)).isoformat(timespec="seconds")
    override = {
        "zone": zone, "action": action,
        "set_at": _now(), "expires_at": expires_at,
    }
    MOCK_FAN_OVERRIDES[(run_id, zone)] = override
    return jsonify({"ok": True, "override": override})

@app.route("/api/runs/<int:run_id>/zones/<int:zone>/fan", methods=["DELETE"])
def api_clear_fan_override(run_id, zone):
    if zone < 1 or zone > 6:
        return jsonify({"error": "zone must be 1-6"}), 400
    MOCK_FAN_OVERRIDES.pop((run_id, zone), None)
    return jsonify({"ok": True})

@app.route("/api/runs/<int:run_id>/emergency-stop", methods=["POST"])
def api_emergency_stop(run_id):
    for zone in range(1, 7):
        MOCK_FAN_OVERRIDES[(run_id, zone)] = {
            "zone": zone, "action": "off",
            "set_at": _now(), "expires_at": None,
        }
    return jsonify({"ok": True, "message": "All fans stopped"})

@app.route("/api/fans/<int:zone>", methods=["POST"])
def api_direct_fan(zone):
    """Direct fan control — works without an active run."""
    if zone < 1 or zone > 6:
        return jsonify({"error": "zone must be 1-6"}), 400
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    if action not in ("on", "off", "auto"):
        return jsonify({"error": "action must be 'on', 'off', or 'auto'"}), 400
    if action == "auto":
        MOCK_NO_RUN_OVERRIDES.pop(zone, None)
        return jsonify({"ok": True, "zone": zone, "fan": "auto"})
    MOCK_NO_RUN_OVERRIDES[zone] = action
    return jsonify({"ok": True, "zone": zone, "fan": action})


# ── Fan rules ─────────────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/fan-rules", methods=["GET"])
def api_get_fan_rules(run_id):
    return jsonify([])

@app.route("/api/runs/<int:run_id>/fan-rules", methods=["POST"])
def api_create_fan_rule(run_id):
    body = request.get_json(silent=True) or {}
    return jsonify({"id": 1, "run_id": run_id, "enabled": 1, **body}), 201

@app.route("/api/runs/<int:run_id>/fan-rules/<int:rule_id>", methods=["PATCH"])
def api_toggle_fan_rule(run_id, rule_id):
    return jsonify({"ok": True})

@app.route("/api/runs/<int:run_id>/fan-rules/<int:rule_id>", methods=["DELETE"])
def api_delete_fan_rule(run_id, rule_id):
    return "", 204


# ── Fan state ──────────────────────────────────────────────────────────────────

@app.route("/api/fan-state", methods=["GET"])
def api_fan_state():
    zones = {}
    for z in range(1, 7):
        sp = MOCK_ZONE_SETPOINTS[z]
        tol = MOCK_ZONE_TOLERANCES[z]
        # Mark a couple of zones as ON to make UI lively
        state = "on" if z in (4, 5) else "off"
        mode = "manual" if (MOCK_RUN["id"], z) in MOCK_FAN_OVERRIDES or z in MOCK_NO_RUN_OVERRIDES else "limit"
        if mode == "manual":
            override = MOCK_FAN_OVERRIDES.get((MOCK_RUN["id"], z))
            state = override["action"] if override else MOCK_NO_RUN_OVERRIDES.get(z, state)
        zones[str(z)] = {
            "state":           state,
            "mode":            mode,
            "setpoint":        sp,
            "setpoint_source": "config",
            "trigger":         round(sp + tol, 2),
            "tolerance":       tol,
            "alarm_level":     None,
            "alarm_reason":    None,
        }
    return jsonify({"timestamp": _now(), "zones": zones})


# ── Deviation events / run events ─────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/deviations", methods=["GET"])
def api_get_deviations(run_id):
    return jsonify([])

@app.route("/api/runs/<int:run_id>/deviations/active", methods=["GET"])
def api_get_active_deviations(run_id):
    return jsonify([])

@app.route("/api/runs/<int:run_id>/events", methods=["GET"])
def api_get_run_events(run_id):
    return jsonify([
        {"id": 1, "label": "Inoculation", "elapsed_min": 0},
        {"id": 2, "label": "First mix",   "elapsed_min": 360},
        {"id": 3, "label": "Second mix",  "elapsed_min": 720},
    ])

@app.route("/api/runs/<int:run_id>/events", methods=["POST"])
def api_create_run_event(run_id):
    body = request.get_json(silent=True) or {}
    label = (body.get('label') or '').strip()
    if not label:
        return jsonify({"error": "label required"}), 400
    elapsed_min = body.get('elapsed_min', 0)
    return jsonify({"id": 99, "label": label, "elapsed_min": int(elapsed_min)}), 201

@app.route("/api/runs/<int:run_id>/events/<int:event_id>", methods=["DELETE"])
def api_delete_run_event(run_id, event_id):
    return "", 204


# ── Metadata ──────────────────────────────────────────────────────────────────

MOCK_META = {
    "run_id": 1, "polish_ratio": 60, "quality_score": None,
    "koji_variety": "yellow", "inoculation_rate": 0.1,
    "rice_variety": "Yamada Nishiki", "notes": "",
}

@app.route("/api/runs/<int:run_id>/metadata", methods=["GET"])
def api_get_metadata(run_id):
    return jsonify(MOCK_META)

@app.route("/api/runs/<int:run_id>/metadata", methods=["PATCH"])
def api_patch_metadata(run_id):
    body = request.get_json(silent=True) or {}
    MOCK_META.update({k: v for k, v in body.items() if k in MOCK_META})
    return jsonify(MOCK_META)


# ── Targets ───────────────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/targets", methods=["GET"])
def api_get_run_targets(run_id):
    return jsonify({"hum_min": 82.0, "hum_max": 92.0, "wt_min": 19.5, "wt_max": 21.0})

@app.route("/api/runs/<int:run_id>/weight-targets", methods=["PUT"])
def api_weight_targets(run_id):
    return jsonify({"ok": True})

@app.route("/api/runs/<int:run_id>/humidity-targets", methods=["PUT"])
def api_humidity_targets(run_id):
    return jsonify({"ok": True, "humidity_target_min": 82.0, "humidity_target_max": 92.0})


# ── Weight analytics ──────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/weight-analytics", methods=["GET"])
def api_weight_analytics(run_id):
    samples = []
    start = datetime.now() - timedelta(hours=18)
    for i, r in enumerate(MOCK_READINGS[::3]):
        rec = datetime.fromisoformat(r["recorded_at"])
        elapsed = round((rec - start).total_seconds() / 60, 1)
        total = sum(r.get(f"weight_lbs_{k}", 0) for k in range(1, 5))
        samples.append({"elapsed_min": elapsed, "weight_lbs": round(total, 3)})
    return jsonify({
        "initial_lbs":      22.0,
        "current_lbs":      19.70,
        "loss_lbs":         2.30,
        "loss_pct":         10.45,
        "rate_lbs_per_hr":  0.128,
        "scale_count":      4,
        "samples":          samples,
        "weight_target_min": 19.5,
        "weight_target_max": 21.0,
        "weight_breakdown": {"1": 4.92, "2": 4.98, "3": 4.85, "4": 4.95},
    })


# ── Run summary ───────────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/summary", methods=["GET"])
def api_run_summary(run_id):
    """Hourly temp stats per zone — list of {hour, zones: {1: {min, max, avg}, ...}}."""
    result = []
    for hour in range(48):
        progress = hour / 48
        base = 28 + 12 * math.sin(progress * math.pi)
        zones = {}
        for z in range(1, 7):
            offset = (z - 3.5) * 0.4
            zones[z] = {
                "min": round(base + offset - 0.6, 1),
                "max": round(base + offset + 0.7, 1),
                "avg": round(base + offset,       1),
            }
        result.append({"hour": hour, "zones": zones})
    return jsonify(result)


# ── Room history ──────────────────────────────────────────────────────────────

@app.route("/api/room-history")
def api_room_history():
    hours = request.args.get('hours', type=float, default=24.0)
    if hours <= 0 or hours > 24 * 7:
        return jsonify({"error": "hours must be between 0 and 168"}), 400
    now = datetime.now()
    n = max(2, int(hours * 4))  # one sample per 15 min
    return jsonify([
        {
            "recorded_at": (now - timedelta(minutes=i * 15)).isoformat(timespec="seconds"),
            "sht_temp": round(24.5 + random.uniform(-0.3, 0.3), 2),
            "humidity": round(87.0 + random.uniform(-1.5, 1.5), 2),
        }
        for i in range(n, 0, -1)
    ])


# ── Reference curves ──────────────────────────────────────────────────────────

@app.route("/api/reference-curves", methods=["GET"])
def api_list_reference_curves():
    return jsonify(MOCK_REFERENCE_CURVES)

@app.route("/api/reference-curves/<int:curve_id>", methods=["GET"])
def api_get_reference_curve(curve_id):
    return jsonify(MOCK_REFERENCE_CURVES[0])

@app.route("/api/reference-curves", methods=["POST"])
def api_create_reference_curve():
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    points = body.get('points', [])
    if not isinstance(points, list) or not points:
        return jsonify({"error": "points must be a non-empty array"}), 400
    new_curve = {
        "id": len(MOCK_REFERENCE_CURVES) + 1,
        "name": name,
        "description": body.get('description', ''),
        "source": body.get('source', ''),
        "points": points,
    }
    return jsonify(new_curve), 201

@app.route("/api/reference-curves/<int:curve_id>", methods=["DELETE"])
def api_delete_reference_curve(curve_id):
    return "", 204

@app.route("/api/reference-curves/generate", methods=["POST"])
def api_generate_curve():
    body = request.get_json(silent=True) or {}
    if not body.get('run_ids'):
        return jsonify({"error": "run_ids required"}), 400
    return jsonify(_koji_curve())

@app.route("/api/reference-curves/generate-from-csv", methods=["POST"])
def api_generate_curve_from_csv():
    if request.files and 'file' in request.files:
        return jsonify(_koji_curve())
    body = request.get_json(silent=True) or {}
    if not body.get('csv_text'):
        return jsonify({"error": "csv_text or file required"}), 400
    return jsonify(_koji_curve())

@app.route("/api/runs/<int:run_id>/target/from-curve/<int:curve_id>", methods=["POST"])
def api_load_curve_as_target(run_id, curve_id):
    return jsonify(MOCK_TARGET), 201


# ── Zone config ───────────────────────────────────────────────────────────────

MOCK_ZONE_CFG = {
    "comment": "Mock zone config",
    "default": {"setpoint_c": 36.5, "tolerance_c": 1.0},
    "zone1": {"setpoint_c": 36.5, "tolerance_c": 1.0, "offset_c": 0.0},
    "zone2": {"setpoint_c": 36.5, "tolerance_c": 1.0, "offset_c": 0.0},
    "zone3": {"setpoint_c": 36.5, "tolerance_c": 1.0, "offset_c": 0.0},
    "zone4": {"setpoint_c": 36.5, "tolerance_c": 1.0, "offset_c": 0.0},
    "zone5": {"setpoint_c": 36.5, "tolerance_c": 1.0, "offset_c": 0.0},
    "zone6": {"setpoint_c": 36.5, "tolerance_c": 1.0, "offset_c": 0.0},
}

@app.route("/api/zone-config", methods=["GET"])
def api_get_zone_config():
    return jsonify(MOCK_ZONE_CFG)

@app.route("/api/zone-config", methods=["POST"])
def api_save_zone_config():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "expected JSON object"}), 400
    MOCK_ZONE_CFG.clear()
    MOCK_ZONE_CFG.update(body)
    return jsonify(MOCK_ZONE_CFG)


# ── Thermocouple probe discovery / zone map ───────────────────────────────────

MOCK_TC_PROBES = [
    {"id": f"3b-00000{i:07d}", "temp_c": round(32.0 + i * 0.4 + random.uniform(-0.3, 0.3), 2)}
    for i in range(1, 7)
]
MOCK_TC_ZONE_MAP = {p["id"]: i + 1 for i, p in enumerate(MOCK_TC_PROBES)}

@app.route("/api/tc-probes", methods=["GET"])
def api_tc_probes():
    # Re-randomize live readings each call
    probes = [{"id": p["id"], "temp_c": round(32.0 + i * 0.4 + random.uniform(-0.3, 0.3), 2)}
              for i, p in enumerate(MOCK_TC_PROBES)]
    return jsonify({"probes": probes, "count": len(probes)})

@app.route("/api/tc-zone-map", methods=["GET"])
def api_get_tc_zone_map():
    return jsonify({"mapping": MOCK_TC_ZONE_MAP})

@app.route("/api/tc-zone-map", methods=["POST"])
def api_set_tc_zone_map():
    body = request.get_json(silent=True) or {}
    mapping = body.get("mapping")
    if not isinstance(mapping, dict):
        return jsonify({"error": "body must contain 'mapping' object"}), 400
    seen = set()
    cleaned = {}
    for device_id, zone in mapping.items():
        if not isinstance(device_id, str) or not device_id.startswith("3b-"):
            return jsonify({"error": f"Invalid device id: {device_id}"}), 400
        try:
            zone = int(zone)
        except (TypeError, ValueError):
            return jsonify({"error": f"Zone must be integer, got: {zone}"}), 400
        if zone < 1 or zone > 6:
            return jsonify({"error": f"Zone must be 1-6, got: {zone}"}), 400
        if zone in seen:
            return jsonify({"error": f"Duplicate zone assignment: {zone}"}), 400
        seen.add(zone)
        cleaned[device_id] = zone
    MOCK_TC_ZONE_MAP.clear()
    MOCK_TC_ZONE_MAP.update(cleaned)
    return jsonify({"ok": True, "mapping": cleaned, "zones_assigned": len(cleaned)})


# ── Scale config ──────────────────────────────────────────────────────────────

MOCK_SCALE_CFG = {
    "scales": {
        str(i): {
            "dat_pin": 5 + (i - 1) * 2,
            "clk_pin": 6 + (i - 1) * 2,
            "tare_offset": 410000 + i * 1000,
            "calibration_factor": 22.5,
        }
        for i in range(1, 5)
    }
}

@app.route("/api/scale-config", methods=["GET"])
def api_get_scale_config():
    return jsonify(MOCK_SCALE_CFG)

@app.route("/api/scale-config/<int:scale_id>/tare", methods=["POST"])
def api_scale_tare(scale_id):
    if not (1 <= scale_id <= 4):
        return jsonify({"error": "scale_id must be 1..4"}), 400
    new_tare = 410000 + scale_id * 1000 + random.randint(-200, 200)
    MOCK_SCALE_CFG["scales"][str(scale_id)]["tare_offset"] = new_tare
    return jsonify({"scale_id": scale_id, "tare_offset": new_tare})

@app.route("/api/scale-config/<int:scale_id>/calibrate", methods=["POST"])
def api_scale_calibrate(scale_id):
    if not (1 <= scale_id <= 4):
        return jsonify({"error": "scale_id must be 1..4"}), 400
    body = request.get_json(silent=True) or {}
    kw = body.get("known_weight_kg")
    try:
        kw = float(kw)
    except (TypeError, ValueError):
        return jsonify({"error": "known_weight_kg must be a number"}), 400
    if kw <= 0 or kw > 200:
        return jsonify({"error": "known_weight_kg must be > 0 and ≤ 200"}), 400
    sc = MOCK_SCALE_CFG["scales"][str(scale_id)]
    factor = round(22.5 + random.uniform(-0.5, 0.5), 4)
    sc["calibration_factor"] = factor
    return jsonify({
        "scale_id":           scale_id,
        "known_weight_kg":    kw,
        "raw":                412345.0,
        "tare_offset":        sc.get("tare_offset"),
        "calibration_factor": factor,
    })


# ── TC calibration ────────────────────────────────────────────────────────────

TC_OFFSET_MAX_ABS_C = 5.0

@app.route("/api/tc-calibration/<int:zone>", methods=["POST"])
def api_set_tc_offset(zone):
    if not (1 <= zone <= 6):
        return jsonify({"error": "zone must be 1..6"}), 400
    body = request.get_json(silent=True) or {}
    try:
        offset = float(body.get("offset_c", 0.0))
    except (TypeError, ValueError):
        return jsonify({"error": "offset_c must be a number"}), 400
    if abs(offset) > TC_OFFSET_MAX_ABS_C:
        return jsonify({"error": f"|offset_c| must be ≤ {TC_OFFSET_MAX_ABS_C}"}), 400
    MOCK_ZONE_CFG.setdefault(f"zone{zone}", {})["offset_c"] = round(offset, 3)
    return jsonify({"zone": zone, "offset_c": round(offset, 3)})

@app.route("/api/tc-calibration/<int:zone>/from-reference", methods=["POST"])
def api_calibrate_tc_from_reference(zone):
    if not (1 <= zone <= 6):
        return jsonify({"error": "zone must be 1..6"}), 400
    body = request.get_json(silent=True) or {}
    try:
        ref = float(body.get("reference_c"))
    except (TypeError, ValueError):
        return jsonify({"error": "reference_c must be a number"}), 400
    if not (-50.0 <= ref <= 200.0):
        return jsonify({"error": "reference_c must be between -50 and 200"}), 400
    raw = round(ref + random.uniform(-0.4, 0.4), 2)
    new_offset = round(raw - ref, 3)
    if abs(new_offset) > TC_OFFSET_MAX_ABS_C:
        return jsonify({"error": f"|computed offset| ({new_offset:+.2f}°C) exceeds ±{TC_OFFSET_MAX_ABS_C}°C"}), 400
    MOCK_ZONE_CFG.setdefault(f"zone{zone}", {})["offset_c"] = new_offset
    return jsonify({"zone": zone, "reference_c": ref, "raw_c": raw, "offset_c": new_offset})

@app.route("/api/tc-calibration/<int:zone>/two-point", methods=["POST"])
def api_calibrate_tc_two_point(zone):
    if not (1 <= zone <= 6):
        return jsonify({"error": "zone must be 1..6"}), 400
    body = request.get_json(silent=True) or {}
    try:
        low_ref  = float(body["low_ref_c"])
        low_raw  = float(body["low_raw_c"])
        high_ref = float(body["high_ref_c"])
        high_raw = float(body["high_raw_c"])
    except (KeyError, TypeError, ValueError) as e:
        return jsonify({"error": f"all four fields required as numbers: low_ref_c, low_raw_c, high_ref_c, high_raw_c ({e})"}), 400
    if high_raw == low_raw:
        return jsonify({"error": "high_raw_c and low_raw_c must differ"}), 400
    if high_ref <= low_ref:
        return jsonify({"error": "high_ref_c must be greater than low_ref_c"}), 400
    slope     = (high_ref - low_ref) / (high_raw - low_raw)
    intercept = low_ref - slope * low_raw
    if not (0.8 <= slope <= 1.2):
        return jsonify({"error": f"computed slope {slope:.4f} is outside 0.8–1.2"}), 400
    key = f"zone{zone}"
    MOCK_ZONE_CFG.setdefault(key, {})
    MOCK_ZONE_CFG[key]["cal_slope"]     = round(slope, 6)
    MOCK_ZONE_CFG[key]["cal_intercept"] = round(intercept, 4)
    MOCK_ZONE_CFG[key].pop("offset_c", None)
    return jsonify({
        "zone": zone,
        "cal_slope":     round(slope, 6),
        "cal_intercept": round(intercept, 4),
        "low_ref_c":     low_ref,  "low_raw_c":  low_raw,
        "high_ref_c":    high_ref, "high_raw_c": high_raw,
    })

@app.route("/api/tc-calibration/<int:zone>/record-point", methods=["POST"])
def api_record_cal_point(zone):
    if not (1 <= zone <= 6):
        return jsonify({"error": "zone must be 1..6"}), 400
    body = request.get_json(silent=True) or {}
    label = body.get("label")
    if label not in ("low", "high"):
        return jsonify({"error": "label must be 'low' or 'high'"}), 400
    try:
        ref = float(body["reference_c"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "reference_c must be a number"}), 400
    if not (-50.0 <= ref <= 200.0):
        return jsonify({"error": "reference_c must be between -50 and 200"}), 400
    raw = round(ref + random.uniform(-0.4, 0.4), 4)
    key = f"zone{zone}"
    MOCK_ZONE_CFG.setdefault(key, {})
    MOCK_ZONE_CFG[key][f"cal_pending_{label}"] = {"raw_c": raw, "ref_c": round(ref, 2)}
    return jsonify({"zone": zone, "label": label, "raw_c": raw, "ref_c": round(ref, 2)})


# ── Export ────────────────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/export.csv")
def api_export_csv(run_id):
    from flask import Response
    lines = ["Timestamp,TC1,TC2,TC3,TC4,TC5,TC6,SHT_Temp,Humidity,Fan1,Fan2,Fan3,Fan4,Fan5,Fan6,Weight_lbs,Notes"]
    for r in MOCK_READINGS:
        lines.append(",".join(str(r.get(k, "")) for k in [
            "recorded_at","tc1","tc2","tc3","tc4","tc5","tc6",
            "sht_temp","humidity","fan1","fan2","fan3","fan4","fan5","fan6","weight_lbs"
        ]) + ',"Mock data"')
    return Response("\n".join(lines), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="mock_batch.csv"'})


if __name__ == "__main__":
    print("SmartSake mock server — http://localhost:8080")
    print("All sensor data is simulated. No hardware required.")
    app.run(host="0.0.0.0", port=8080, debug=False)
