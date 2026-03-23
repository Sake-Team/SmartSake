"""
SmartSake Flask server.
Replaces the SimpleHTTPServer thread in WriteSensors.py.

Start with:  python server.py
Default port: 8080
"""

import json
import os
from flask import Flask, jsonify, request, send_from_directory, abort

import db

BASE_DIR = os.path.dirname(__file__)
SENSOR_JSON = os.path.join(BASE_DIR, "sensor_latest.json")
SCALE_JSON  = os.path.join(BASE_DIR, "scale_data.json")

app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")

db.init_db()


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
    """Merges sensor_latest.json + scale_data.json into one response."""
    data = {}

    if os.path.exists(SENSOR_JSON):
        try:
            with open(SENSOR_JSON) as f:
                data.update(json.load(f))
        except Exception:
            pass

    if os.path.exists(SCALE_JSON):
        try:
            with open(SCALE_JSON) as f:
                scale = json.load(f)
                data["weight_value"] = scale.get("weight_value")
                data["weight_units"] = scale.get("weight_units")
        except Exception:
            pass

    return jsonify(data)


# ── Runs ──────────────────────────────────────────────────────────────────────

@app.route("/api/runs", methods=["GET"])
def api_list_runs():
    return jsonify(db.get_all_runs())


@app.route("/api/runs/active", methods=["GET"])
def api_active_run():
    run = db.get_active_run()
    if not run:
        return jsonify({"error": "No active run"}), 404
    # attach last reading timestamp so UI can detect stale/crashed
    reading = db.get_latest_reading(run["id"])
    run["last_reading_at"] = reading["recorded_at"] if reading else None
    return jsonify(run)


@app.route("/api/runs", methods=["POST"])
def api_create_run():
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    run_id = db.create_run(name)
    return jsonify(db.get_run(run_id)), 201


@app.route("/api/runs/<int:run_id>", methods=["GET"])
def api_get_run(run_id):
    run = db.get_run(run_id)
    if not run:
        abort(404)
    return jsonify(run)


@app.route("/api/runs/<int:run_id>/end", methods=["POST"])
def api_end_run(run_id):
    run = db.get_run(run_id)
    if not run:
        abort(404)
    db.end_run(run_id)
    return jsonify(db.get_run(run_id))


@app.route("/api/runs/<int:run_id>", methods=["DELETE"])
def api_delete_run(run_id):
    if not db.get_run(run_id):
        abort(404)
    db.delete_run(run_id)
    return "", 204


# ── Readings ──────────────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/readings", methods=["GET"])
def api_readings(run_id):
    if not db.get_run(run_id):
        abort(404)
    return jsonify(db.get_all_readings(run_id))


@app.route("/api/runs/<int:run_id>/latest", methods=["GET"])
def api_run_latest(run_id):
    if not db.get_run(run_id):
        abort(404)
    reading = db.get_latest_reading(run_id)
    if not reading:
        return jsonify({}), 204
    return jsonify(reading)


# ── Target profile ────────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/target", methods=["GET"])
def api_get_target(run_id):
    if not db.get_run(run_id):
        abort(404)
    return jsonify(db.get_target_profile(run_id))


@app.route("/api/runs/<int:run_id>/target", methods=["POST"])
def api_save_target(run_id):
    if not db.get_run(run_id):
        abort(404)
    rows = request.get_json(force=True, silent=True)
    if not isinstance(rows, list):
        return jsonify({"error": "expected JSON array"}), 400
    db.save_target_profile(run_id, rows)
    return jsonify(db.get_target_profile(run_id)), 201


# ── Zone notes ────────────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/zones", methods=["GET"])
def api_get_all_notes(run_id):
    if not db.get_run(run_id):
        abort(404)
    return jsonify(db.get_zone_notes(run_id))


@app.route("/api/runs/<int:run_id>/zones/<int:zone>", methods=["GET"])
def api_get_zone_note(run_id, zone):
    if not db.get_run(run_id):
        abort(404)
    notes = db.get_zone_notes(run_id)
    return jsonify({"note": notes.get(str(zone), "")})


@app.route("/api/runs/<int:run_id>/zones/<int:zone>", methods=["PUT"])
def api_save_zone_note(run_id, zone):
    if not db.get_run(run_id):
        abort(404)
    body = request.get_json(force=True, silent=True) or {}
    db.save_zone_note(run_id, zone, body.get("note", ""))
    return jsonify({"ok": True})


# ── CSV export ────────────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/export.csv")
def api_export_csv(run_id):
    run = db.get_run(run_id)
    if not run:
        abort(404)
    readings = db.get_all_readings(run_id)

    lines = ["Timestamp,TC1,TC2,TC3,TC4,TC5,TC6,SHT_Temp,Humidity,"
             "Fan1,Fan2,Fan3,Fan4,Fan5,Fan6,Weight_lbs"]
    for r in readings:
        lines.append(",".join(str(r.get(k) or "") for k in [
            "recorded_at",
            "tc1","tc2","tc3","tc4","tc5","tc6",
            "sht_temp","humidity",
            "fan1","fan2","fan3","fan4","fan5","fan6",
            "weight_lbs"
        ]))

    from flask import Response
    filename = run["name"].replace(" ", "_") + ".csv"
    return Response(
        "\n".join(lines),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


if __name__ == "__main__":
    print("SmartSake server starting on http://0.0.0.0:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)
