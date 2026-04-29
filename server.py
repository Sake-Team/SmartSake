"""
SmartSake Flask server + sensor collector.

Start with:  python server.py
Default port: 8080

Sensor collection (thermocouples, SHT30, load cells) runs in a background
daemon thread started at import time.  No separate WriteSensors.py process
is required — one command runs everything.
"""

import json
import os
import threading
import time
from flask import Flask, jsonify, request, send_from_directory, abort

import db
import fan_gpio

BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
SENSOR_JSON       = os.path.join(BASE_DIR, "sensor_latest.json")
FAN_STATE_JSON    = os.path.join(BASE_DIR, "fan_state.json")
ZONE_CONFIG_FILE  = os.path.join(BASE_DIR, "zone_config.json")
SCALE_CONFIG_FILE = os.path.join(BASE_DIR, "scale_config.json")

app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")

# ── Database init + crash recovery ───────────────────────────────────────────
db.init_db()
_stale = db.get_active_run()
if _stale:
    db.mark_crashed(_stale["id"])
    print(f"[startup] Previous run '{_stale['name']}' (id={_stale['id']}) marked as crashed.")

# ── Start sensor loop in background thread ────────────────────────────────────
def _start_sensor_thread():
    try:
        import WriteSensors
        WriteSensors.start_sensor_loop()
    except Exception as e:
        print(f"[startup] Sensor loop failed to start: {e}")

threading.Thread(target=_start_sensor_thread, daemon=True, name="sensor-loop").start()


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

    Reads sensor_latest.json (written by WriteSensors.py) and normalizes the
    nested structure into flat keys so the
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

    # Fan states — read from fan_state.json written by WriteSensors.py PID loop.
    fan_state_data = {}
    if os.path.exists(FAN_STATE_JSON):
        try:
            with open(FAN_STATE_JSON) as f:
                fan_state_data = json.load(f)
        except Exception:
            pass
    fzones = fan_state_data.get("zones", {})
    for i in range(1, 7):
        z = fzones.get(str(i), {})
        state = z.get("state")
        result[f"fan{i}"] = 1 if state == "on" else (0 if state == "off" else None)

    # SHT30 environmental sensor
    sht = raw.get("sht30", {})
    sht_temp = sht.get("temp_c")
    humidity  = sht.get("humidity_rh")
    result["sht_temp"] = round(sht_temp, 2) if isinstance(sht_temp, (int, float)) else None
    result["humidity"] = round(humidity,  2) if isinstance(humidity,  (int, float)) else None

    # Scale data — read weight_kg_1..4 from sensor_latest.json
    for i in range(1, 5):
        key = f"weight_kg_{i}"
        val = raw.get(key)
        if isinstance(val, (int, float)):
            result[f"weight_lbs_{i}"] = round(val * 2.20462, 3)
        else:
            result[f"weight_lbs_{i}"] = None
    # weight_lbs alias for backwards compat with analytics queries
    result["weight_lbs"] = result["weight_lbs_1"]

    total = sum(
        result[f"weight_lbs_{i}"]
        for i in range(1, 5)
        if result.get(f"weight_lbs_{i}") is not None
    )
    result["weight_total_lbs"] = round(total, 3) if total else None

    return jsonify(result)


# ── Sensor loop status (debug) ────────────────────────────────────────────────

@app.route("/api/sensor-status")
def api_sensor_status():
    """Returns sensor loop health: library availability, last write age, active run."""
    try:
        import WriteSensors as ws
        tc_available  = ws._TC_AVAILABLE
        hx_available  = ws._HX_AVAILABLE
        sht_available = ws._SHT_AVAILABLE
        last_write_age = round(time.time() - ws._last_db_write_time, 1) if ws._last_db_write_time > 0 else None
        active_run_id  = ws._active_run_id
    except Exception as e:
        return jsonify({"error": f"Could not read sensor module state: {e}"}), 500

    sensor_age_s = None
    sensor_ts    = None
    if os.path.exists(SENSOR_JSON):
        try:
            sensor_age_s = round(time.time() - os.path.getmtime(SENSOR_JSON), 1)
            with open(SENSOR_JSON) as f:
                sensor_ts = json.load(f).get("timestamp")
        except Exception:
            pass

    active_run = db.get_active_run()

    return jsonify({
        "sensor_file_age_s":  sensor_age_s,
        "sensor_file_ts":     sensor_ts,
        "last_db_write_age_s": last_write_age,
        "active_run_id":      active_run_id,
        "active_run":         active_run["name"] if active_run else None,
        "libs": {
            "thermocouples": tc_available,
            "hx711_scales":  hx_available,
            "sht30":         sht_available,
        },
    })


# ── Health gate ───────────────────────────────────────────────────────────────

@app.route("/api/health")
def api_health():
    raw = {}
    sensor_age_s = None

    if os.path.exists(SENSOR_JSON):
        try:
            mtime = os.path.getmtime(SENSOR_JSON)
            sensor_age_s = round(time.time() - mtime, 1)
            with open(SENSOR_JSON) as f:
                raw = json.load(f)
        except Exception:
            pass

    # SHT30
    sht_data = raw.get("sht30", {})
    sht_temp = sht_data.get("temp_c")
    sht_hum  = sht_data.get("humidity_rh")
    if sht_temp is None or sht_hum is None:
        sht_status = {"status": "missing"}
    elif not (-10 <= sht_temp <= 80) or not (0 <= sht_hum <= 100):
        sht_status = {"status": "error", "temp_c": sht_temp, "humidity": sht_hum}
    else:
        sht_status = {"status": "ok", "temp_c": sht_temp, "humidity": sht_hum}

    # Thermocouples
    tcs_raw = raw.get("thermocouples", {})
    thermocouples = {}
    for i in range(1, 7):
        val = tcs_raw.get(f"TC{i}")
        if val is None:
            thermocouples[str(i)] = {"status": "missing"}
        elif -10 <= val <= 200:
            thermocouples[str(i)] = {"status": "ok", "temp_c": val}
        else:
            thermocouples[str(i)] = {"status": "missing"}

    # Scales
    scale_cfg = {}
    try:
        with open(SCALE_CONFIG_FILE) as f:
            scale_cfg = json.load(f).get("scales", {})
    except Exception:
        pass

    scales = {}
    for i in range(1, 5):
        cfg = scale_cfg.get(str(i), {})
        if cfg.get("dat_pin") is None:
            scales[str(i)] = {"status": "not_wired"}
        else:
            wkg = raw.get(f"weight_kg_{i}")
            if wkg is not None:
                scales[str(i)] = {"status": "ok", "weight_kg": wkg}
            else:
                scales[str(i)] = {"status": "no_data"}

    # Relays
    relays = {}
    for i in range(1, 7):
        pin = fan_gpio.FAN_PINS.get(i)
        relays[str(i)] = {"status": "wired" if pin is not None else "not_wired"}

    # Ready flag
    tc_ok = any(v["status"] == "ok" for v in thermocouples.values())
    ready = sht_status["status"] == "ok" and tc_ok

    return jsonify({
        "sht30": sht_status,
        "thermocouples": thermocouples,
        "scales": scales,
        "relays": relays,
        "ready": ready,
        "sensor_age_s": sensor_age_s,
    })


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

@app.route("/api/runs/scored", methods=["GET"])
def api_scored_runs():
    min_score = request.args.get('min_score', 1, type=int)
    return jsonify(db.get_scored_runs(min_score))


@app.route("/api/reference-curves/generate", methods=["POST"])
def api_generate_curve():
    body = request.get_json(force=True, silent=True) or {}
    run_ids = body.get('run_ids', [])
    if not run_ids:
        return jsonify({"error": "run_ids required"}), 400
    bucket_min = int(body.get('bucket_min', 30))
    if not (5 <= bucket_min <= 360):
        return jsonify({"error": "bucket_min must be 5–360"}), 400
    pts = db.generate_curve_from_runs(run_ids, bucket_min)
    return jsonify([{"elapsed_min": p[0], "temp_target": p[1]} for p in pts])


_CSV_MAX_BYTES = 25 * 1024 * 1024  # 25 MB


@app.route("/api/reference-curves/generate-from-csv", methods=["POST"])
def api_generate_curve_from_csv():
    """Build a curve from a single uploaded CSV.

    Accepts either:
      - multipart/form-data with a 'file' part (and optional 'bucket_min' field), or
      - application/json with {csv_text, bucket_min}.
    """
    csv_text = None
    bucket_min = 30

    if request.files and 'file' in request.files:
        f = request.files['file']
        data = f.read(_CSV_MAX_BYTES + 1)
        if len(data) > _CSV_MAX_BYTES:
            return jsonify({"error": f"CSV exceeds {_CSV_MAX_BYTES // (1024*1024)} MB limit"}), 413
        try:
            csv_text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                csv_text = data.decode('latin-1')
            except Exception:
                return jsonify({"error": "could not decode CSV (try UTF-8)"}), 400
        if request.form.get('bucket_min'):
            try:
                bucket_min = int(request.form['bucket_min'])
            except ValueError:
                return jsonify({"error": "bucket_min must be an integer"}), 400
    else:
        body = request.get_json(force=True, silent=True) or {}
        csv_text = body.get('csv_text')
        if body.get('bucket_min') is not None:
            try:
                bucket_min = int(body['bucket_min'])
            except (TypeError, ValueError):
                return jsonify({"error": "bucket_min must be an integer"}), 400

    if not csv_text or not csv_text.strip():
        return jsonify({"error": "csv_text or file required"}), 400
    if not (5 <= bucket_min <= 360):
        return jsonify({"error": "bucket_min must be 5–360"}), 400

    try:
        pts = db.generate_curve_from_csv(csv_text, bucket_min)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"failed to parse CSV: {e}"}), 400

    if not pts:
        return jsonify({"error": "no usable rows after bucketing — check timestamp/TC columns"}), 400

    return jsonify([{"elapsed_min": p[0], "temp_target": p[1]} for p in pts])


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
        return jsonify({"error": str(e)}), 500
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
    # Add per-scale breakdown from live sensor_latest.json
    raw_sensor = {}
    if os.path.exists(SENSOR_JSON):
        try:
            with open(SENSOR_JSON) as f:
                raw_sensor = json.load(f)
        except Exception:
            pass
    breakdown = {}
    for i in range(1, 5):
        val = raw_sensor.get(f"weight_kg_{i}")
        breakdown[str(i)] = round(val * 2.20462, 3) if isinstance(val, (int, float)) else None
    data["weight_breakdown"] = breakdown
    return jsonify(data)


@app.route("/api/runs/<int:run_id>/targets", methods=["GET"])
def api_get_run_targets(run_id):
    run = db.get_run(run_id)
    if not run:
        abort(404)
    return jsonify({
        "hum_min": run.get("humidity_target_min"),
        "hum_max": run.get("humidity_target_max"),
        "wt_min":  run.get("weight_target_min"),
        "wt_max":  run.get("weight_target_max"),
    })


@app.route("/api/runs/<int:run_id>/weight-targets", methods=["PUT"])
def api_weight_targets(run_id):
    if not db.get_run(run_id):
        abort(404)
    body = request.get_json(silent=True) or {}
    t_min = body.get("min", body.get("target_min"))
    t_max = body.get("max", body.get("target_max"))
    if t_min is None or t_max is None:
        return jsonify({"error": "min and max required"}), 400
    if not (isinstance(t_min, (int, float)) and isinstance(t_max, (int, float))):
        return jsonify({"error": "min and max must be numbers"}), 400
    db.update_run_weight_targets(run_id, float(t_min), float(t_max))
    return jsonify({"ok": True})


@app.route("/api/runs/<int:run_id>/humidity-targets", methods=["PUT"])
def api_humidity_targets(run_id):
    if not db.get_run(run_id):
        abort(404)
    body = request.get_json(silent=True) or {}
    t_min = body.get("min", body.get("target_min"))
    t_max = body.get("max", body.get("target_max"))
    if t_min is None or t_max is None:
        return jsonify({"error": "min and max required"}), 400
    if not (0 <= float(t_min) <= 100 and 0 <= float(t_max) <= 100):
        return jsonify({"error": "target_min and target_max must be 0-100"}), 400
    db.update_run_humidity_targets(run_id, float(t_min), float(t_max))
    run = db.get_run(run_id)
    return jsonify({"ok": True,
                    "humidity_target_min": run.get("humidity_target_min"),
                    "humidity_target_max": run.get("humidity_target_max")})


# ── Room history ──────────────────────────────────────────────────────────────

@app.route("/api/room-history")
def api_room_history():
    hours = request.args.get('hours', type=float, default=24.0)
    if hours <= 0 or hours > 24 * 7:
        return jsonify({"error": "hours must be between 0 and 168"}), 400
    return jsonify(db.get_room_history(hours))


# ── CSV export ────────────────────────────────────────────────────────────────

@app.route("/api/runs/<int:run_id>/export.csv")
def api_export_csv(run_id):
    run = db.get_run(run_id)
    if not run:
        abort(404)
    readings = db.get_all_readings(run_id)
    zone_notes = db.get_zone_notes(run_id)
    # Combine all zone notes into one string for the Notes column
    notes_str = " | ".join(
        f"Zone {z}: {note}"
        for z, note in sorted(zone_notes.items())
        if note and note.strip()
    )
    # Escape quotes in notes
    notes_escaped = notes_str.replace('"', '""')

    lines = ["Timestamp,TC1,TC2,TC3,TC4,TC5,TC6,SHT_Temp,Humidity,"
             "Fan1,Fan2,Fan3,Fan4,Fan5,Fan6,Weight_lbs,Notes"]
    for r in readings:
        row = ",".join(str(r.get(k) or "") for k in [
            "recorded_at",
            "tc1","tc2","tc3","tc4","tc5","tc6",
            "sht_temp","humidity",
            "fan1","fan2","fan3","fan4","fan5","fan6",
            "weight_lbs"
        ])
        lines.append(f'{row},"{notes_escaped}"')

    from flask import Response
    filename = run["name"].replace(" ", "_") + ".csv"
    return Response(
        "\n".join(lines),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# ── Fan state ─────────────────────────────────────────────────────────────────

@app.route("/api/fan-state", methods=["GET"])
def api_fan_state():
    """Return the latest fan_state.json written by the limit-switch fan-control loop."""
    if not os.path.exists(FAN_STATE_JSON):
        return jsonify({"timestamp": None, "zones": {str(z): {"state": None, "mode": "none",
                        "setpoint": None, "setpoint_source": None, "trigger": None}
                        for z in range(1, 7)}})
    try:
        with open(FAN_STATE_JSON) as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"error": "could not read fan_state.json"}), 500


# ── Zone configuration ─────────────────────────────────────────────────────────

@app.route("/api/zone-config", methods=["GET"])
def api_get_zone_config():
    """Return current zone tolerances from zone_config.json."""
    if not os.path.exists(ZONE_CONFIG_FILE):
        return jsonify({"error": "zone_config.json not found"}), 404
    try:
        with open(ZONE_CONFIG_FILE) as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"error": "could not read zone_config.json"}), 500


@app.route("/api/zone-config", methods=["POST"])
def api_save_zone_config():
    """Save zone tolerances to zone_config.json. Body: JSON object with zone keys."""
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "expected JSON object"}), 400
    # Validate each zone entry
    for key, val in body.items():
        if key == "comment":
            continue
        if not isinstance(val, dict):
            return jsonify({"error": f"config for '{key}' must be an object"}), 400
        if "tolerance_c" in val:
            if not isinstance(val["tolerance_c"], (int, float)) or isinstance(val["tolerance_c"], bool):
                return jsonify({"error": f"tolerance_c for '{key}' must be a number"}), 400
            if not (0 <= val["tolerance_c"] <= 10):
                return jsonify({"error": f"tolerance_c for '{key}' must be between 0 and 10"}), 400
        if "setpoint_c" in val and val["setpoint_c"] is not None:
            if not isinstance(val["setpoint_c"], (int, float)) or isinstance(val["setpoint_c"], bool):
                return jsonify({"error": f"setpoint_c for '{key}' must be a number"}), 400
            if not (0 <= val["setpoint_c"] <= 60):
                return jsonify({"error": f"setpoint_c for '{key}' must be between 0 and 60"}), 400
    try:
        with open(ZONE_CONFIG_FILE, "w") as f:
            json.dump(body, f, indent=2)
        return jsonify(body), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # Dev: python server.py  (sensor loop already running in background thread)
    # Production: use gunicorn via systemd/smartsake.service
    app.run(host="0.0.0.0", port=8080, debug=False)
