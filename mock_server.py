"""
mock_server.py — Local UI preview server for SmartSake.

Serves the real HTML/CSS/JS from this directory but returns realistic
mock data for all API endpoints. No hardware, no Pi, no sensors required.

Usage:
    pip install flask
    python mock_server.py
    # then open http://localhost:8080 in your browser
"""

import json
import math
import os
import random
import time
from datetime import datetime, timedelta

from flask import Flask, jsonify, send_from_directory

BASE_DIR = os.path.dirname(__file__)
app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now():
    return datetime.now().isoformat(timespec="seconds")

def _fake_temp(base=32.0, drift=0.8):
    """Simulate a thermocouple reading with slight drift."""
    return round(base + random.uniform(-drift, drift), 2)

def _fake_readings(run_id, n=120):
    """Generate n fake historical readings spread over the last 24 hours."""
    now = datetime.now()
    readings = []
    for i in range(n):
        t = now - timedelta(minutes=(n - i) * 12)
        progress = i / n  # 0..1
        # Simulate a typical koji temperature curve: rises then plateaus
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
    "notes": "Mock data — local preview mode",
    "last_reading_at": _now(),
    "humidity_target_min": 82.0,
    "humidity_target_max": 92.0,
    "weight_target_min": 19.5,
    "weight_target_max": 21.0,
}

MOCK_READINGS = _fake_readings(1, 120)

MOCK_REFERENCE_CURVES = [
    {
        "id": 1,
        "name": "Standard Koji (48h)",
        "description": "Typical temperature curve for white rice koji",
        "source": "Traditional reference",
        "points": [
            {"elapsed_min": 0,    "tc1": 28.0, "tc2": 28.0},
            {"elapsed_min": 360,  "tc1": 30.0, "tc2": 30.5},
            {"elapsed_min": 720,  "tc1": 33.0, "tc2": 33.5},
            {"elapsed_min": 1440, "tc1": 36.0, "tc2": 36.5},
            {"elapsed_min": 2160, "tc1": 34.0, "tc2": 34.5},
            {"elapsed_min": 2880, "tc1": 32.0, "tc2": 32.0},
        ],
    }
]

# ── Static pages ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "home.html")

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(BASE_DIR, filename)


# ── Live sensor feed ──────────────────────────────────────────────────────────

@app.route("/api/latest")
def api_latest():
    base = 32.5
    return jsonify({
        "timestamp": _now(),
        "tc1": _fake_temp(base),
        "tc2": _fake_temp(base + 1.2),
        "tc3": _fake_temp(base + 0.8),
        "tc4": _fake_temp(base - 0.3),
        "tc5": _fake_temp(base + 1.5),
        "tc6": _fake_temp(base - 0.8),
        "sht_temp": _fake_temp(24.5, 0.2),
        "humidity": _fake_temp(87.0, 1.5),
        "fan1": 1, "fan2": 1, "fan3": 0,
        "fan4": 0, "fan5": 1, "fan6": 0,
        "weight_lbs_1": 4.92,
        "weight_lbs_2": 4.98,
        "weight_lbs_3": 4.85,
        "weight_lbs_4": 4.95,
        "weight_lbs": 4.92,
        "weight_total_lbs": 19.70,
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
    return jsonify(MOCK_RUN)

@app.route("/api/runs/completed", methods=["GET"])
def api_runs_completed():
    return jsonify([])

@app.route("/api/runs/<int:run_id>", methods=["GET"])
def api_get_run(run_id):
    return jsonify(MOCK_RUN)

@app.route("/api/runs", methods=["POST"])
def api_create_run():
    return jsonify(MOCK_RUN), 201

@app.route("/api/runs/<int:run_id>/end", methods=["POST"])
def api_end_run(run_id):
    return jsonify(MOCK_RUN)

@app.route("/api/runs/<int:run_id>", methods=["DELETE"])
def api_delete_run(run_id):
    return "", 204


# ── Readings ──────────────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/readings", methods=["GET"])
def api_readings(run_id):
    return jsonify(MOCK_READINGS)

@app.route("/api/runs/<int:run_id>/latest", methods=["GET"])
def api_run_latest(run_id):
    return jsonify(MOCK_READINGS[-1])


# ── Target profile ────────────────────────────────────────────────────────────

MOCK_TARGET = [
    {"elapsed_min": 0,    "tc1": 28.0},
    {"elapsed_min": 720,  "tc1": 33.0},
    {"elapsed_min": 1440, "tc1": 36.0},
    {"elapsed_min": 2880, "tc1": 32.0},
]

@app.route("/api/runs/<int:run_id>/target", methods=["GET"])
def api_get_target(run_id):
    return jsonify(MOCK_TARGET)

@app.route("/api/runs/<int:run_id>/target", methods=["POST"])
def api_save_target(run_id):
    return jsonify(MOCK_TARGET), 201


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


# ── Fan overrides / rules / state ──────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/fan-overrides", methods=["GET"])
def api_get_fan_overrides(run_id):
    return jsonify({"overrides": {}})

@app.route("/api/runs/<int:run_id>/zones/<int:zone>/fan", methods=["POST"])
def api_set_fan_override(run_id, zone):
    return jsonify({"ok": True, "override": None})

@app.route("/api/runs/<int:run_id>/zones/<int:zone>/fan", methods=["DELETE"])
def api_clear_fan_override(run_id, zone):
    return jsonify({"ok": True})

@app.route("/api/runs/<int:run_id>/fan-rules", methods=["GET"])
def api_get_fan_rules(run_id):
    return jsonify([])

@app.route("/api/runs/<int:run_id>/fan-rules", methods=["POST"])
def api_create_fan_rule(run_id):
    return jsonify({}), 201

@app.route("/api/runs/<int:run_id>/fan-rules/<int:rule_id>", methods=["PATCH"])
def api_toggle_fan_rule(run_id, rule_id):
    return jsonify({"ok": True})

@app.route("/api/runs/<int:run_id>/fan-rules/<int:rule_id>", methods=["DELETE"])
def api_delete_fan_rule(run_id, rule_id):
    return "", 204

@app.route("/api/fan-state", methods=["GET"])
def api_fan_state():
    return jsonify({
        "timestamp": _now(),
        "zones": {
            "1": {"state": "on",  "mode": "auto", "setpoint": 33.0, "pid_out": 0.7},
            "2": {"state": "on",  "mode": "auto", "setpoint": 33.0, "pid_out": 0.5},
            "3": {"state": "off", "mode": "auto", "setpoint": 33.0, "pid_out": 0.1},
            "4": {"state": "off", "mode": "auto", "setpoint": 33.0, "pid_out": 0.0},
            "5": {"state": "on",  "mode": "auto", "setpoint": 33.0, "pid_out": 0.8},
            "6": {"state": "off", "mode": "auto", "setpoint": 33.0, "pid_out": 0.2},
        }
    })


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
    return jsonify({"id": 99, "label": "Mock event", "elapsed_min": 0}), 201

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
    return jsonify({
        "initial_weight_lbs": 22.0,
        "current_weight_lbs": 19.70,
        "weight_loss_lbs": 2.30,
        "weight_loss_pct": 10.45,
        "weight_target_min": 19.5,
        "weight_target_max": 21.0,
        "weight_breakdown": {"1": 4.92, "2": 4.98, "3": 4.85, "4": 4.95},
    })


# ── Room history ──────────────────────────────────────────────────────────────

@app.route("/api/room-history")
def api_room_history():
    now = datetime.now()
    return jsonify([
        {
            "recorded_at": (now - timedelta(minutes=i * 15)).isoformat(timespec="seconds"),
            "sht_temp": round(24.5 + random.uniform(-0.3, 0.3), 2),
            "humidity": round(87.0 + random.uniform(-1.5, 1.5), 2),
        }
        for i in range(96, 0, -1)
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
    return jsonify(MOCK_REFERENCE_CURVES[0]), 201

@app.route("/api/reference-curves/<int:curve_id>", methods=["DELETE"])
def api_delete_reference_curve(curve_id):
    return "", 204

@app.route("/api/runs/<int:run_id>/target/from-curve/<int:curve_id>", methods=["POST"])
def api_load_curve_as_target(run_id, curve_id):
    return jsonify(MOCK_TARGET), 201


# ── Correlation ───────────────────────────────────────────────────────────────

@app.route("/api/correlation", methods=["GET"])
def api_correlation():
    return jsonify({"error": "Need at least 5 scored runs", "count": 1}), 400


# ── PID config ────────────────────────────────────────────────────────────────

MOCK_PID = {
    "comment": "Mock PID config",
    "default": {"Kp": 2.0, "Ki": 0.1, "Kd": 0.5},
}

@app.route("/api/pid-config", methods=["GET"])
def api_get_pid_config():
    return jsonify(MOCK_PID)

@app.route("/api/pid-config", methods=["POST"])
def api_save_pid_config():
    return jsonify(MOCK_PID)


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
