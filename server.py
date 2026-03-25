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
    """
    Returns the latest sensor readings in a flat format matching DB column names:
      tc1–tc6, sht_temp, humidity, weight_lbs, timestamp

    Reads sensor_latest.json (written by WriteSensors.py) and scale_data.json,
    then normalizes the nested sensor_latest structure into flat keys so the
    dashboard and zone pages use the same key names as the historical readings API.
    """
    raw = {}

    if os.path.exists(SENSOR_JSON):
        try:
            with open(SENSOR_JSON) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                raw = loaded
        except Exception:
            pass

    # Normalize sensor_latest.json nested structure → flat DB column names
    result = {"timestamp": raw.get("timestamp")}

    # Thermocouples: {"TC1": 28.4, "TC2": null, ...}
    tcs = raw.get("thermocouples", {})
    for i in range(1, 7):
        val = tcs.get(f"TC{i}")
        result[f"tc{i}"] = round(val, 2) if isinstance(val, (int, float)) else None

    # Fan states — WriteSensors.py does not write these to sensor_latest.json yet;
    # include as null so the response shape matches DB sensor_readings rows.
    for i in range(1, 7):
        result[f"fan{i}"] = None

    # SHT30 environmental sensor
    sht = raw.get("sht30", {})
    sht_temp = sht.get("temp_c")
    humidity  = sht.get("humidity_rh")
    result["sht_temp"] = round(sht_temp, 2) if isinstance(sht_temp, (int, float)) else None
    result["humidity"] = round(humidity,  2) if isinstance(humidity,  (int, float)) else None

    # Scale data
    if os.path.exists(SCALE_JSON):
        try:
            with open(SCALE_JSON) as f:
                scale = json.load(f)
            weight_val   = scale.get("weight_value")
            weight_units = scale.get("weight_units", "lbs")
            if isinstance(weight_val, (int, float)):
                # Convert to lbs if needed
                if str(weight_units).lower() in ("kg", "kilogram", "kilograms"):
                    weight_val = round(weight_val * 2.20462, 3)
                result["weight_lbs"] = round(weight_val, 3)
            else:
                result["weight_lbs"] = None
        except Exception:
            result["weight_lbs"] = None
    else:
        result["weight_lbs"] = None

    return jsonify(result)


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
    n = request.args.get('n', type=int)
    if n and n > 0:
        return jsonify(db.get_readings_sampled(run_id, n))
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


# ── Fan overrides ─────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/fan-overrides", methods=["GET"])
def api_get_fan_overrides(run_id):
    if not db.get_run(run_id):
        abort(404)
    return jsonify({"overrides": db.get_all_fan_overrides(run_id)})


@app.route("/api/runs/<int:run_id>/zones/<int:zone>/fan", methods=["POST"])
def api_set_fan_override(run_id, zone):
    if not db.get_run(run_id):
        abort(404)
    if zone < 1 or zone > 6:
        return jsonify({"error": "zone must be 1-6"}), 400
    body = request.get_json(force=True, silent=True) or {}
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
    db.set_fan_override(run_id, zone, action, duration)
    override = db.get_fan_override(run_id, zone)
    return jsonify({"ok": True, "override": override})


@app.route("/api/runs/<int:run_id>/zones/<int:zone>/fan", methods=["DELETE"])
def api_clear_fan_override(run_id, zone):
    if not db.get_run(run_id):
        abort(404)
    if zone < 1 or zone > 6:
        return jsonify({"error": "zone must be 1-6"}), 400
    db.clear_fan_override(run_id, zone)
    return jsonify({"ok": True})


# ── Fan rules ─────────────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/fan-rules", methods=["GET"])
def api_get_fan_rules(run_id):
    if not db.get_run(run_id):
        abort(404)
    zone = request.args.get('zone', type=int)
    return jsonify(db.get_fan_rules(run_id, zone))


@app.route("/api/runs/<int:run_id>/fan-rules", methods=["POST"])
def api_create_fan_rule(run_id):
    if not db.get_run(run_id):
        abort(404)
    body = request.get_json(force=True, silent=True) or {}
    zone = body.get('zone')
    rule_type = body.get('rule_type')
    fan_action = body.get('fan_action')

    if zone not in range(1, 7):
        return jsonify({"error": "zone must be 1-6"}), 400
    if rule_type not in ('time_window', 'threshold'):
        return jsonify({"error": "rule_type must be time_window or threshold"}), 400
    if fan_action not in ('on', 'off'):
        return jsonify({"error": "fan_action must be on or off"}), 400

    kwargs = {}
    if rule_type == 'time_window':
        start = body.get('elapsed_min_start')
        end = body.get('elapsed_min_end')
        if start is None or end is None or not isinstance(start, int) or not isinstance(end, int):
            return jsonify({"error": "time_window requires elapsed_min_start and elapsed_min_end (integers)"}), 400
        if start >= end:
            return jsonify({"error": "elapsed_min_start must be less than elapsed_min_end"}), 400
        kwargs = {'elapsed_min_start': start, 'elapsed_min_end': end}
    else:
        temp = body.get('threshold_temp_c')
        direction = body.get('threshold_dir')
        dur = body.get('threshold_dur_min')
        if temp is None or direction not in ('above', 'below') or not isinstance(dur, int) or dur <= 0:
            return jsonify({"error": "threshold requires threshold_temp_c, threshold_dir (above/below), threshold_dur_min (int > 0)"}), 400
        kwargs = {'threshold_temp_c': float(temp), 'threshold_dir': direction, 'threshold_dur_min': dur}

    rule_id = db.create_fan_rule(run_id, zone, rule_type, fan_action, **kwargs)
    return jsonify(db.get_fan_rules(run_id)[0] if False else
                   next((r for r in db.get_fan_rules(run_id) if r['id'] == rule_id), {})), 201


@app.route("/api/runs/<int:run_id>/fan-rules/<int:rule_id>", methods=["PATCH"])
def api_toggle_fan_rule(run_id, rule_id):
    if not db.get_run(run_id):
        abort(404)
    body = request.get_json(force=True, silent=True) or {}
    enabled = body.get('enabled')
    if enabled is None:
        return jsonify({"error": "enabled required"}), 400
    db.set_fan_rule_enabled(rule_id, bool(enabled))
    return jsonify({"ok": True})


@app.route("/api/runs/<int:run_id>/fan-rules/<int:rule_id>", methods=["DELETE"])
def api_delete_fan_rule(run_id, rule_id):
    if not db.get_run(run_id):
        abort(404)
    db.delete_fan_rule(rule_id)
    return "", 204


# ── Deviation events ───────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/deviations", methods=["GET"])
def api_get_deviations(run_id):
    if not db.get_run(run_id):
        abort(404)
    zone = request.args.get('zone', type=int)
    return jsonify(db.get_deviation_events(run_id, zone))


@app.route("/api/runs/<int:run_id>/deviations/active", methods=["GET"])
def api_get_active_deviations(run_id):
    if not db.get_run(run_id):
        abort(404)
    return jsonify(db.get_open_deviation_events(run_id))


# ── Run events (stage markers) ─────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/events", methods=["GET"])
def api_get_run_events(run_id):
    if not db.get_run(run_id):
        abort(404)
    return jsonify(db.get_run_events(run_id))


@app.route("/api/runs/<int:run_id>/events", methods=["POST"])
def api_create_run_event(run_id):
    run = db.get_run(run_id)
    if not run:
        abort(404)
    body = request.get_json(force=True, silent=True) or {}
    label = (body.get('label') or '').strip()
    elapsed_min = body.get('elapsed_min')
    if not label:
        return jsonify({"error": "label required"}), 400
    if elapsed_min is None:
        # Auto-calculate from run start if not provided
        from datetime import datetime as _dt
        started = _dt.fromisoformat(run['started_at'])
        elapsed_min = int((_dt.now() - started).total_seconds() / 60)
    event_id = db.create_run_event(run_id, label, int(elapsed_min))
    return jsonify({"id": event_id, "label": label, "elapsed_min": int(elapsed_min)}), 201


@app.route("/api/runs/<int:run_id>/events/<int:event_id>", methods=["DELETE"])
def api_delete_run_event(run_id, event_id):
    if not db.get_run(run_id):
        abort(404)
    db.delete_run_event(event_id)
    return "", 204


# ── Run metadata ───────────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/metadata", methods=["GET"])
def api_get_metadata(run_id):
    if not db.get_run(run_id):
        abort(404)
    return jsonify(db.get_run_metadata(run_id))


@app.route("/api/runs/<int:run_id>/metadata", methods=["PATCH"])
def api_patch_metadata(run_id):
    if not db.get_run(run_id):
        abort(404)
    body = request.get_json(force=True, silent=True) or {}

    # Validate provided fields
    if 'polish_ratio' in body and body['polish_ratio'] is not None:
        try:
            pr = int(body['polish_ratio'])
            if not (0 <= pr <= 100):
                raise ValueError
            body['polish_ratio'] = pr
        except (TypeError, ValueError):
            return jsonify({"error": "polish_ratio must be 0-100"}), 400

    if 'quality_score' in body and body['quality_score'] is not None:
        try:
            qs = int(body['quality_score'])
            if not (1 <= qs <= 5):
                raise ValueError
            body['quality_score'] = qs
        except (TypeError, ValueError):
            return jsonify({"error": "quality_score must be 1-5"}), 400

    if 'koji_variety' in body and body['koji_variety'] not in (None, 'yellow', 'white', 'black', 'other'):
        return jsonify({"error": "koji_variety must be yellow, white, black, or other"}), 400

    if 'inoculation_rate' in body and body['inoculation_rate'] is not None:
        try:
            body['inoculation_rate'] = float(body['inoculation_rate'])
            if body['inoculation_rate'] <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "inoculation_rate must be a positive number"}), 400

    return jsonify(db.upsert_run_metadata(run_id, body))


# ── Reference curves ──────────────────────────────────────────────────────────

@app.route("/api/reference-curves", methods=["GET"])
def api_list_reference_curves():
    return jsonify(db.get_all_reference_curves())


@app.route("/api/reference-curves/<int:curve_id>", methods=["GET"])
def api_get_reference_curve(curve_id):
    curve = db.get_reference_curve(curve_id)
    if not curve:
        abort(404)
    return jsonify(curve)


@app.route("/api/reference-curves", methods=["POST"])
def api_create_reference_curve():
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    points = body.get('points', [])
    if not isinstance(points, list) or not points:
        return jsonify({"error": "points must be a non-empty array"}), 400
    for p in points:
        if 'elapsed_min' not in p:
            return jsonify({"error": "each point must have elapsed_min"}), 400
    try:
        curve_id = db.create_reference_curve(
            name, body.get('description', ''), body.get('source', ''), points
        )
    except Exception as e:
        if 'UNIQUE' in str(e):
            return jsonify({"error": "a curve with that name already exists"}), 409
        raise
    return jsonify(db.get_reference_curve(curve_id)), 201


@app.route("/api/reference-curves/<int:curve_id>", methods=["DELETE"])
def api_delete_reference_curve(curve_id):
    if not db.get_reference_curve(curve_id):
        abort(404)
    db.delete_reference_curve(curve_id)
    return "", 204


@app.route("/api/runs/<int:run_id>/target/from-curve/<int:curve_id>", methods=["POST"])
def api_load_curve_as_target(run_id, curve_id):
    if not db.get_run(run_id):
        abort(404)
    if not db.get_reference_curve(curve_id):
        abort(404)
    db.load_curve_as_target(run_id, curve_id)
    return jsonify(db.get_target_profile(run_id)), 201


# ── Correlation ────────────────────────────────────────────────────────────────

_CORR_VARIABLES = ('avg_humidity_stage2', 'total_weight_loss_pct', 'avg_temp_all_zones', 'peak_deviation')


@app.route("/api/correlation", methods=["GET"])
def api_correlation():
    variable = request.args.get('variable')
    if variable not in _CORR_VARIABLES:
        return jsonify({"error": f"variable must be one of: {', '.join(_CORR_VARIABLES)}"}), 400
    count = db.get_scored_run_count()
    if count < 5:
        return jsonify({"error": "Need at least 5 scored runs", "count": count}), 400
    rows = db.get_correlation_data(variable)
    points = [{"run_id": r[0], "run_name": r[1], "x": r[3], "y": r[2]}
              for r in rows if r[3] is not None]
    pearson_r = db.compute_pearson_r([(p['x'], p['y']) for p in points])
    return jsonify({"variable": variable, "n": len(points),
                    "pearson_r": pearson_r, "points": points})


# ── Phase 2: Completed runs, weight/humidity analytics ───────────────────────

@app.route("/api/runs/completed", methods=["GET"])
def api_runs_completed():
    return jsonify(db.get_completed_runs())


@app.route("/api/runs/<int:run_id>/weight-analytics", methods=["GET"])
def api_weight_analytics(run_id):
    run = db.get_run(run_id)
    if not run:
        abort(404)
    data = db.get_weight_analytics(run_id)
    # Include target band from runs table
    data["weight_target_min"] = run.get("weight_target_min")
    data["weight_target_max"] = run.get("weight_target_max")
    return jsonify(data)


@app.route("/api/runs/<int:run_id>/weight-targets", methods=["PUT"])
def api_weight_targets(run_id):
    if not db.get_run(run_id):
        abort(404)
    body = request.get_json(silent=True) or {}
    t_min = body.get("target_min")
    t_max = body.get("target_max")
    if t_min is None or t_max is None:
        return jsonify({"error": "target_min and target_max required"}), 400
    if not (isinstance(t_min, (int, float)) and isinstance(t_max, (int, float))):
        return jsonify({"error": "target_min and target_max must be numbers"}), 400
    db.update_run_weight_targets(run_id, float(t_min), float(t_max))
    return jsonify({"ok": True})


@app.route("/api/runs/<int:run_id>/humidity-targets", methods=["PUT"])
def api_humidity_targets(run_id):
    if not db.get_run(run_id):
        abort(404)
    body = request.get_json(silent=True) or {}
    t_min = body.get("target_min")
    t_max = body.get("target_max")
    if t_min is None or t_max is None:
        return jsonify({"error": "target_min and target_max required"}), 400
    if not (0 <= float(t_min) <= 100 and 0 <= float(t_max) <= 100):
        return jsonify({"error": "target_min and target_max must be 0-100"}), 400
    db.update_run_humidity_targets(run_id, float(t_min), float(t_max))
    run = db.get_run(run_id)
    return jsonify({"ok": True,
                    "humidity_target_min": run.get("humidity_target_min"),
                    "humidity_target_max": run.get("humidity_target_max")})


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
