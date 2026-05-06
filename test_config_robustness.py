#!/usr/bin/env python3
"""
SmartSake — Config Robustness Test

Exercises the JSON config loaders that the sensor loop and server depend on.
Real-world failure modes exercised here: SD card hiccup mid-write, manual
edits with typos, partial writes, non-UTF8 bytes, empty file. The system
must degrade gracefully — log a warning, fall back to safe defaults, and
not crash.

Mirrors test_fan_state.py: plain `assert`, ANSI-coloured PASS/FAIL output,
no pytest/unittest dependency.

Usage:
    python3 test_config_robustness.py
"""

import os
import sys
import json
import tempfile
import threading
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


# ── ANSI helpers (match test_fan_state.py) ──────────────────────────────────
BOLD    = "\033[1m"
DIM     = "\033[2m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
CYAN    = "\033[36m"
RESET   = "\033[0m"

if os.name == "nt":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ── Module under test ───────────────────────────────────────────────────────
import WriteSensors as ws  # noqa: E402
import server               # noqa: E402  (imports flask + db.init_db)


# ── Helpers ─────────────────────────────────────────────────────────────────
_results = []  # list of (name, passed_bool, optional_traceback)


def runtest(name, fn):
    """Run one test; print PASS/FAIL; record outcome."""
    try:
        fn()
    except AssertionError:
        tb = traceback.format_exc()
        print(f"  [{RED}FAIL{RESET}] {name}")
        for line in tb.rstrip().splitlines():
            print(f"        {DIM}{line}{RESET}")
        _results.append((name, False, tb))
    except Exception:
        tb = traceback.format_exc()
        print(f"  [{RED}ERROR{RESET}] {name}")
        for line in tb.rstrip().splitlines():
            print(f"        {DIM}{line}{RESET}")
        _results.append((name, False, tb))
    else:
        print(f"  [{GREEN}PASS{RESET}] {name}")
        _results.append((name, True, None))


def _write_tmp(content_bytes, suffix=".json"):
    """Write bytes to a NamedTemporaryFile (closed) and return its path."""
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="smartsake_cfg_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content_bytes)
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        raise
    return path


def _reset_zone_cfg_state():
    """Force WriteSensors to re-read from disk on the next call."""
    ws._zone_cfg = {}
    ws._zone_cfg_mtime = 0


def _reset_tc_zone_map_state():
    ws._tc_zone_map = {}
    ws._tc_zone_map_mtime = 0


def _reset_no_run_overrides():
    with ws._no_run_overrides_lock:
        ws._no_run_overrides.clear()


def _reset_server_json_cache():
    server._json_cache.clear()


# ── Tests: WriteSensors._load_zone_config ───────────────────────────────────

def test_zone_cfg_empty_file():
    """Empty zone_config.json — loader returns the safe-default dict, no exception."""
    path = _write_tmp(b"")
    orig = ws.ZONE_CONFIG_FILE
    ws.ZONE_CONFIG_FILE = path
    try:
        _reset_zone_cfg_state()
        cfg = ws._load_zone_config()
        assert isinstance(cfg, dict), f"expected dict, got {type(cfg).__name__}"
        # When _zone_cfg was empty, loader must populate the safe default.
        assert "default" in cfg, f"expected 'default' fallback key, got {list(cfg)}"
        assert cfg["default"]["tolerance_c"] == ws.DEFAULT_TOLERANCE_C
    finally:
        ws.ZONE_CONFIG_FILE = orig
        try: os.remove(path)
        except OSError: pass
        _reset_zone_cfg_state()


def test_zone_cfg_truncated_json():
    """Truncated mid-value — graceful failure, returns previous/default cfg."""
    path = _write_tmp(b'{"zone1": {"setp')
    orig = ws.ZONE_CONFIG_FILE
    ws.ZONE_CONFIG_FILE = path
    try:
        _reset_zone_cfg_state()
        cfg = ws._load_zone_config()
        # Should not raise. Should return the safe default dict (empty cache).
        assert isinstance(cfg, dict)
        assert "default" in cfg, f"truncated JSON should fall back to default cfg, got {cfg}"
    finally:
        ws.ZONE_CONFIG_FILE = orig
        try: os.remove(path)
        except OSError: pass
        _reset_zone_cfg_state()


def test_zone_cfg_trailing_comma():
    """Manual-edit trailing comma — graceful failure, falls back to default."""
    bad = b'{"zone1": {"tolerance_c": 1.0,}, }'
    path = _write_tmp(bad)
    orig = ws.ZONE_CONFIG_FILE
    ws.ZONE_CONFIG_FILE = path
    try:
        _reset_zone_cfg_state()
        cfg = ws._load_zone_config()
        assert isinstance(cfg, dict)
        assert "default" in cfg, f"trailing-comma JSON should yield default cfg, got {cfg}"
    finally:
        ws.ZONE_CONFIG_FILE = orig
        try: os.remove(path)
        except OSError: pass
        _reset_zone_cfg_state()


def test_zone_cfg_wrong_type_for_setpoint():
    """`setpoint_c: "thirty"` — _zone_setpoint_override returns None instead of crashing."""
    cfg = {"zone1": {"setpoint_c": "thirty", "tolerance_c": 1.0}}
    path = _write_tmp(json.dumps(cfg).encode("utf-8"))
    orig = ws.ZONE_CONFIG_FILE
    ws.ZONE_CONFIG_FILE = path
    try:
        _reset_zone_cfg_state()
        loaded = ws._load_zone_config()
        assert loaded.get("zone1", {}).get("setpoint_c") == "thirty"
        # Fixed: was crashing on float("thirty"). Now wraps in try/except and
        # returns None so the zone falls back to "no setpoint" (operator notices
        # via the dashboard's "No setpoint configured" badge).
        result = ws._zone_setpoint_override(1)
        assert result is None, (
            f"expected None for unparseable setpoint, got {result!r}"
        )
    finally:
        ws.ZONE_CONFIG_FILE = orig
        try: os.remove(path)
        except OSError: pass
        _reset_zone_cfg_state()


def test_zone_cfg_missing_required_key():
    """zone1 has its own dict but missing tolerance_c.

    Documents a real bug: _zone_tolerance falls back to the *module*
    DEFAULT_TOLERANCE_C (1.0) rather than the user-supplied
    cfg["default"]["tolerance_c"] (1.5), because the existence of cfg["zone1"]
    short-circuits the cfg.get(... default) path. The user's default block is
    silently ignored when a per-zone block is partial. Flagged in report.
    """
    cfg = {"zone1": {"setpoint_c": 30.0}, "default": {"tolerance_c": 1.5}}
    path = _write_tmp(json.dumps(cfg).encode("utf-8"))
    orig = ws.ZONE_CONFIG_FILE
    ws.ZONE_CONFIG_FILE = path
    try:
        _reset_zone_cfg_state()
        tol = ws._zone_tolerance(1)
        # Fixed: now resolves zone-tolerance → default-block-tolerance → module
        # constant, so a partial zone entry no longer shadows the user-defined
        # default block.
        assert tol == 1.5, (
            f"expected user-defined default 1.5, got {tol}"
        )
        # And a zone with no entry at all also gets the user default:
        tol2 = ws._zone_tolerance(2)
        assert tol2 == 1.5, (
            f"zone with no entry should use user-defined default 1.5, got {tol2}"
        )
    finally:
        ws.ZONE_CONFIG_FILE = orig
        try: os.remove(path)
        except OSError: pass
        _reset_zone_cfg_state()


def test_zone_cfg_non_utf8_bytes():
    """Random binary garbage — graceful failure."""
    path = _write_tmp(b"\xff\xfe\x00\x01\x02 not json at all \x80\x81\x82")
    orig = ws.ZONE_CONFIG_FILE
    ws.ZONE_CONFIG_FILE = path
    try:
        _reset_zone_cfg_state()
        cfg = ws._load_zone_config()
        assert isinstance(cfg, dict)
        assert "default" in cfg, "non-UTF8 garbage should fall back to defaults"
    finally:
        ws.ZONE_CONFIG_FILE = orig
        try: os.remove(path)
        except OSError: pass
        _reset_zone_cfg_state()


def test_zone_cfg_unknown_extra_keys():
    """Unknown extra keys — loader keeps them silently (forward-compat)."""
    cfg = {
        "zone1": {"tolerance_c": 1.0, "future_field_xyz": "ignored"},
        "experimental_top_level": {"foo": 1},
    }
    path = _write_tmp(json.dumps(cfg).encode("utf-8"))
    orig = ws.ZONE_CONFIG_FILE
    ws.ZONE_CONFIG_FILE = path
    try:
        _reset_zone_cfg_state()
        loaded = ws._load_zone_config()
        # Must NOT raise. Extra keys should pass through.
        assert loaded.get("experimental_top_level") == {"foo": 1}
        assert loaded["zone1"]["future_field_xyz"] == "ignored"
        # _zone_tolerance still returns the right value despite extras
        assert ws._zone_tolerance(1) == 1.0
    finally:
        ws.ZONE_CONFIG_FILE = orig
        try: os.remove(path)
        except OSError: pass
        _reset_zone_cfg_state()


def test_zone_cfg_negative_or_absurd_values():
    """Document downstream behaviour for nonsensical numerics."""
    cfg = {
        "zone1": {"tolerance_c": -5.0, "setpoint_c": 1000.0},
        "zone2": {"tolerance_c": 0.0,  "setpoint_c": -200.0},
    }
    path = _write_tmp(json.dumps(cfg).encode("utf-8"))
    orig = ws.ZONE_CONFIG_FILE
    ws.ZONE_CONFIG_FILE = path
    try:
        _reset_zone_cfg_state()
        # Loader accepts whatever JSON gives us — no clamp / no validation.
        # Document the consequences:
        assert ws._zone_tolerance(1) == -5.0, "loader does NOT reject negative tolerance"
        assert ws._zone_setpoint_override(1) == 1000.0, "loader does NOT clamp huge setpoint"
        assert ws._zone_tolerance(2) == 0.0
        # _classify_alarm guards against tolerance <= 0, so a 0 tolerance
        # silently disables alarms for that zone — good.
        level, reason = ws._classify_alarm(50.0, 50.0, 0.0)
        assert level is None and reason is None, (
            f"_classify_alarm with tolerance=0 should return (None,None), "
            f"got ({level!r},{reason!r})"
        )
        # Negative tolerance also gets rejected by _classify_alarm guard.
        level, reason = ws._classify_alarm(50.0, 50.0, -5.0)
        assert level is None and reason is None, (
            f"_classify_alarm with negative tolerance should return (None,None), "
            f"got ({level!r},{reason!r})"
        )
    finally:
        ws.ZONE_CONFIG_FILE = orig
        try: os.remove(path)
        except OSError: pass
        _reset_zone_cfg_state()


# ── Tests: WriteSensors._read_scale_cfg ─────────────────────────────────────

def test_scale_cfg_empty_file():
    path = _write_tmp(b"")
    orig = ws.SCALE_CONFIG_FILE
    ws.SCALE_CONFIG_FILE = path
    try:
        result = ws._read_scale_cfg(1)
        assert result is None, f"empty scale_config should return None, got {result}"
    finally:
        ws.SCALE_CONFIG_FILE = orig
        try: os.remove(path)
        except OSError: pass


def test_scale_cfg_truncated():
    path = _write_tmp(b'{"scales": {"1": {"tare')
    orig = ws.SCALE_CONFIG_FILE
    ws.SCALE_CONFIG_FILE = path
    try:
        result = ws._read_scale_cfg(1)
        assert result is None, f"truncated scale_config should return None, got {result}"
    finally:
        ws.SCALE_CONFIG_FILE = orig
        try: os.remove(path)
        except OSError: pass


def test_scale_cfg_missing_scale_id():
    """File is valid but doesn't contain scale_id — returns tuple of Nones, not None."""
    cfg = {"scales": {"2": {"tare_offset": 100, "calibration_factor": 1.0}}}
    path = _write_tmp(json.dumps(cfg).encode("utf-8"))
    orig = ws.SCALE_CONFIG_FILE
    ws.SCALE_CONFIG_FILE = path
    try:
        result = ws._read_scale_cfg(1)
        # Documented behaviour: missing scale_id returns (units_default, None, None, None)
        assert result is not None, "valid JSON without target scale should still return a tuple"
        units, tare, factor, points = result
        assert units == "kg", f"missing scale should default units to 'kg', got {units!r}"
        assert tare is None and factor is None and points is None, (
            f"missing scale should have None tare/factor/points, got {result}"
        )
    finally:
        ws.SCALE_CONFIG_FILE = orig
        try: os.remove(path)
        except OSError: pass


def test_scale_cfg_unknown_extra_keys_ignored():
    cfg = {
        "scales": {"1": {
            "units": "kg", "tare_offset": 50, "calibration_factor": 1.5,
            "future_field_zzz": True,
        }},
        "experimental_top_key": [1, 2, 3],
    }
    path = _write_tmp(json.dumps(cfg).encode("utf-8"))
    orig = ws.SCALE_CONFIG_FILE
    ws.SCALE_CONFIG_FILE = path
    try:
        result = ws._read_scale_cfg(1)
        units, tare, factor, points = result
        assert units == "kg" and tare == 50 and factor == 1.5
        # No exception — extras ignored.
    finally:
        ws.SCALE_CONFIG_FILE = orig
        try: os.remove(path)
        except OSError: pass


def test_scale_cfg_wrong_type_for_factor():
    """calibration_factor as string — loader returns the string verbatim, downstream must cope."""
    cfg = {"scales": {"1": {"tare_offset": "fifty", "calibration_factor": "1.5"}}}
    path = _write_tmp(json.dumps(cfg).encode("utf-8"))
    orig = ws.SCALE_CONFIG_FILE
    ws.SCALE_CONFIG_FILE = path
    try:
        result = ws._read_scale_cfg(1)
        assert result is not None
        units, tare, factor, points = result
        # Loader does no type coercion. It's the consumer's problem.
        assert tare == "fifty"
        assert factor == "1.5"
    finally:
        ws.SCALE_CONFIG_FILE = orig
        try: os.remove(path)
        except OSError: pass


# ── Tests: WriteSensors._load_tc_zone_map ───────────────────────────────────

def test_tc_zone_map_empty_file():
    """_load_tc_zone_map raises TCZoneMapError on empty/invalid file."""
    path = _write_tmp(b"")
    orig = ws.TC_ZONE_MAP_FILE
    ws.TC_ZONE_MAP_FILE = path
    try:
        _reset_tc_zone_map_state()
        try:
            ws._load_tc_zone_map()
        except ws.TCZoneMapError:
            pass  # Expected — empty file is loud-fail by design.
        else:
            raise AssertionError("expected TCZoneMapError for empty tc_zone_map.json")
    finally:
        ws.TC_ZONE_MAP_FILE = orig
        try: os.remove(path)
        except OSError: pass
        _reset_tc_zone_map_state()


def test_tc_zone_map_truncated():
    path = _write_tmp(b'{"3b-aaaaaaaaa": 1, "3b-bbbb')
    orig = ws.TC_ZONE_MAP_FILE
    ws.TC_ZONE_MAP_FILE = path
    try:
        _reset_tc_zone_map_state()
        try:
            ws._load_tc_zone_map()
        except ws.TCZoneMapError:
            pass
        else:
            raise AssertionError("expected TCZoneMapError for truncated map")
    finally:
        ws.TC_ZONE_MAP_FILE = orig
        try: os.remove(path)
        except OSError: pass
        _reset_tc_zone_map_state()


def test_tc_zone_map_non_utf8_bytes():
    path = _write_tmp(b"\xff\xfe\x00\x01garbage")
    orig = ws.TC_ZONE_MAP_FILE
    ws.TC_ZONE_MAP_FILE = path
    try:
        _reset_tc_zone_map_state()
        try:
            ws._load_tc_zone_map()
        except ws.TCZoneMapError:
            pass
        else:
            raise AssertionError("expected TCZoneMapError for non-UTF8 garbage")
    finally:
        ws.TC_ZONE_MAP_FILE = orig
        try: os.remove(path)
        except OSError: pass
        _reset_tc_zone_map_state()


def test_tc_zone_map_wrong_type_for_zone():
    """Zone value isn't an int — TCZoneMapError."""
    bad = {"3b-aaaaaaaaa": "one", "3b-bbbbbbbbb": 2, "3b-ccccccccc": 3,
           "3b-ddddddddd": 4, "3b-eeeeeeeee": 5, "3b-fffffffff": 6}
    path = _write_tmp(json.dumps(bad).encode("utf-8"))
    orig = ws.TC_ZONE_MAP_FILE
    ws.TC_ZONE_MAP_FILE = path
    try:
        _reset_tc_zone_map_state()
        try:
            ws._load_tc_zone_map()
        except ws.TCZoneMapError:
            pass
        else:
            raise AssertionError("expected TCZoneMapError for non-int zone value")
    finally:
        ws.TC_ZONE_MAP_FILE = orig
        try: os.remove(path)
        except OSError: pass
        _reset_tc_zone_map_state()


def test_tc_zone_map_missing_required_zone():
    """Map present but missing zone 6 — TCZoneMapError."""
    bad = {"3b-aaaaaaaaa": 1, "3b-bbbbbbbbb": 2, "3b-ccccccccc": 3,
           "3b-ddddddddd": 4, "3b-eeeeeeeee": 5}  # no zone 6
    path = _write_tmp(json.dumps(bad).encode("utf-8"))
    orig = ws.TC_ZONE_MAP_FILE
    ws.TC_ZONE_MAP_FILE = path
    try:
        _reset_tc_zone_map_state()
        try:
            ws._load_tc_zone_map()
        except ws.TCZoneMapError as e:
            assert "Missing zones" in str(e), f"unexpected error message: {e}"
        else:
            raise AssertionError("expected TCZoneMapError for missing zone 6")
    finally:
        ws.TC_ZONE_MAP_FILE = orig
        try: os.remove(path)
        except OSError: pass
        _reset_tc_zone_map_state()


def test_tc_zone_map_unknown_extras_silently_accepted():
    """All 6 zones plus an extra device id — loader accepts, then duplicate-zone check fires."""
    # Accepted-with-extras isn't really possible because the validator enforces
    # 6 unique channels and rejects duplicates, but keys without values aren't
    # disallowed. Pure-superset case (7 entries with 7 unique zone numbers) is
    # impossible since channels must be 1..6. So this case effectively can't
    # exist as a "forward-compat" loader feature — document that.
    # Test the inverse: valid 6-entry map loads cleanly.
    good = {f"3b-{i:09d}": i for i in range(1, 7)}
    path = _write_tmp(json.dumps(good).encode("utf-8"))
    orig = ws.TC_ZONE_MAP_FILE
    ws.TC_ZONE_MAP_FILE = path
    try:
        _reset_tc_zone_map_state()
        m = ws._load_tc_zone_map()
        assert sorted(m.values()) == [1, 2, 3, 4, 5, 6]
    finally:
        ws.TC_ZONE_MAP_FILE = orig
        try: os.remove(path)
        except OSError: pass
        _reset_tc_zone_map_state()


# ── Tests: WriteSensors._load_no_run_overrides ──────────────────────────────

def test_no_run_overrides_empty_file():
    """Empty no_run_overrides.json — loader leaves dict empty, no exception."""
    path = _write_tmp(b"")
    orig_func = ws._no_run_overrides_path
    ws._no_run_overrides_path = lambda: path
    _reset_no_run_overrides()
    try:
        ws._load_no_run_overrides()
        # Should not throw, dict should remain empty
        assert ws._no_run_overrides == {}
    finally:
        ws._no_run_overrides_path = orig_func
        _reset_no_run_overrides()
        try: os.remove(path)
        except OSError: pass


def test_no_run_overrides_truncated():
    path = _write_tmp(b'{"1": {"action": "on", "expires_at"')
    orig_func = ws._no_run_overrides_path
    ws._no_run_overrides_path = lambda: path
    _reset_no_run_overrides()
    try:
        ws._load_no_run_overrides()
        assert ws._no_run_overrides == {}, "truncated JSON should leave overrides empty"
    finally:
        ws._no_run_overrides_path = orig_func
        _reset_no_run_overrides()
        try: os.remove(path)
        except OSError: pass


def test_no_run_overrides_non_utf8():
    path = _write_tmp(b"\xff\xfe\x00\x01\x02garbage")
    orig_func = ws._no_run_overrides_path
    ws._no_run_overrides_path = lambda: path
    _reset_no_run_overrides()
    try:
        ws._load_no_run_overrides()
        assert ws._no_run_overrides == {}
    finally:
        ws._no_run_overrides_path = orig_func
        _reset_no_run_overrides()
        try: os.remove(path)
        except OSError: pass


def test_no_run_overrides_wrong_type_zone_key():
    """Non-int zone keys are skipped, valid entries kept."""
    cfg = {
        "1": {"action": "on", "expires_at": None},
        "not_an_int": {"action": "off", "expires_at": None},
        "2": {"action": "garbage_action", "expires_at": None},  # invalid action
    }
    path = _write_tmp(json.dumps(cfg).encode("utf-8"))
    orig_func = ws._no_run_overrides_path
    ws._no_run_overrides_path = lambda: path
    _reset_no_run_overrides()
    try:
        ws._load_no_run_overrides()
        assert 1 in ws._no_run_overrides, "valid zone-1 entry should be loaded"
        assert "not_an_int" not in ws._no_run_overrides
        assert 2 not in ws._no_run_overrides, "invalid action should be filtered out"
    finally:
        ws._no_run_overrides_path = orig_func
        _reset_no_run_overrides()
        try: os.remove(path)
        except OSError: pass


def test_no_run_overrides_unknown_extras_ignored():
    cfg = {
        "1": {"action": "on", "expires_at": None, "future_field_xyz": "kept-or-not"},
    }
    path = _write_tmp(json.dumps(cfg).encode("utf-8"))
    orig_func = ws._no_run_overrides_path
    ws._no_run_overrides_path = lambda: path
    _reset_no_run_overrides()
    try:
        ws._load_no_run_overrides()
        assert 1 in ws._no_run_overrides
        # Loader copies only action/expires_at — extras dropped (good).
        assert ws._no_run_overrides[1].get("action") == "on"
        # Loader explicitly constructs {"action":..., "expires_at":...} so
        # the extra key should NOT be present.
        assert "future_field_xyz" not in ws._no_run_overrides[1]
    finally:
        ws._no_run_overrides_path = orig_func
        _reset_no_run_overrides()
        try: os.remove(path)
        except OSError: pass


# ── Tests: server._read_json_cached ─────────────────────────────────────────

def test_read_json_cached_empty_file():
    path = _write_tmp(b"")
    try:
        _reset_server_json_cache()
        result = server._read_json_cached(path)
        assert result == {}, f"empty file should return empty dict, got {result!r}"
    finally:
        try: os.remove(path)
        except OSError: pass


def test_read_json_cached_truncated_json():
    path = _write_tmp(b'{"a": 1, "b": "tru')
    try:
        _reset_server_json_cache()
        result = server._read_json_cached(path)
        assert result == {}, f"truncated JSON should return empty dict, got {result!r}"
    finally:
        try: os.remove(path)
        except OSError: pass


def test_read_json_cached_trailing_comma():
    path = _write_tmp(b'{"a": 1, "b": 2,}')
    try:
        _reset_server_json_cache()
        result = server._read_json_cached(path)
        assert result == {}, f"trailing-comma should return {{}}, got {result!r}"
    finally:
        try: os.remove(path)
        except OSError: pass


def test_read_json_cached_non_utf8():
    path = _write_tmp(b"\xff\xfe\x00\x01garbage")
    try:
        _reset_server_json_cache()
        result = server._read_json_cached(path)
        assert result == {}
    finally:
        try: os.remove(path)
        except OSError: pass


def test_read_json_cached_unknown_extras():
    """Unknown keys are passed through verbatim (forward-compat)."""
    payload = {"existing_key": 1, "future_field_xyz": [1, 2, 3]}
    path = _write_tmp(json.dumps(payload).encode("utf-8"))
    try:
        _reset_server_json_cache()
        result = server._read_json_cached(path)
        assert result == payload, f"expected verbatim passthrough, got {result!r}"
    finally:
        try: os.remove(path)
        except OSError: pass


def test_read_json_cached_missing_file():
    """Path that doesn't exist — returns {} via OSError handler."""
    fake = os.path.join(tempfile.gettempdir(), "smartsake_does_not_exist_xyz.json")
    if os.path.exists(fake):
        os.remove(fake)
    _reset_server_json_cache()
    result = server._read_json_cached(fake)
    assert result == {}, f"missing file should return {{}}, got {result!r}"


# ── Entry point ─────────────────────────────────────────────────────────────

TESTS = [
    # _load_zone_config
    ("zone_config: empty file falls back to default cfg",        test_zone_cfg_empty_file),
    ("zone_config: truncated JSON falls back to default cfg",    test_zone_cfg_truncated_json),
    ("zone_config: trailing-comma JSON falls back to default",   test_zone_cfg_trailing_comma),
    ("zone_config: wrong type for setpoint (documents fragility)", test_zone_cfg_wrong_type_for_setpoint),
    ("zone_config: missing tolerance_c uses default",            test_zone_cfg_missing_required_key),
    ("zone_config: non-UTF8 bytes fall back to default",         test_zone_cfg_non_utf8_bytes),
    ("zone_config: unknown extra keys ignored",                  test_zone_cfg_unknown_extra_keys),
    ("zone_config: negative/absurd values not clamped (documents)", test_zone_cfg_negative_or_absurd_values),
    # _read_scale_cfg
    ("scale_cfg: empty file returns None",                       test_scale_cfg_empty_file),
    ("scale_cfg: truncated JSON returns None",                   test_scale_cfg_truncated),
    ("scale_cfg: missing scale_id returns tuple of Nones",       test_scale_cfg_missing_scale_id),
    ("scale_cfg: unknown extra keys ignored",                    test_scale_cfg_unknown_extra_keys_ignored),
    ("scale_cfg: wrong type for factor passes through (documents)", test_scale_cfg_wrong_type_for_factor),
    # _load_tc_zone_map
    ("tc_zone_map: empty file raises TCZoneMapError",            test_tc_zone_map_empty_file),
    ("tc_zone_map: truncated JSON raises TCZoneMapError",        test_tc_zone_map_truncated),
    ("tc_zone_map: non-UTF8 bytes raises TCZoneMapError",        test_tc_zone_map_non_utf8_bytes),
    ("tc_zone_map: non-int zone value raises TCZoneMapError",    test_tc_zone_map_wrong_type_for_zone),
    ("tc_zone_map: missing required zone raises TCZoneMapError", test_tc_zone_map_missing_required_zone),
    ("tc_zone_map: valid 6-entry map loads cleanly",             test_tc_zone_map_unknown_extras_silently_accepted),
    # _load_no_run_overrides
    ("no_run_overrides: empty file leaves dict empty",           test_no_run_overrides_empty_file),
    ("no_run_overrides: truncated JSON leaves dict empty",       test_no_run_overrides_truncated),
    ("no_run_overrides: non-UTF8 bytes leaves dict empty",       test_no_run_overrides_non_utf8),
    ("no_run_overrides: bad zone keys / actions filtered out",   test_no_run_overrides_wrong_type_zone_key),
    ("no_run_overrides: unknown extras dropped on load",         test_no_run_overrides_unknown_extras_ignored),
    # server._read_json_cached
    ("server._read_json_cached: empty file returns {}",          test_read_json_cached_empty_file),
    ("server._read_json_cached: truncated JSON returns {}",      test_read_json_cached_truncated_json),
    ("server._read_json_cached: trailing-comma returns {}",      test_read_json_cached_trailing_comma),
    ("server._read_json_cached: non-UTF8 bytes returns {}",      test_read_json_cached_non_utf8),
    ("server._read_json_cached: unknown extras pass through",    test_read_json_cached_unknown_extras),
    ("server._read_json_cached: missing file returns {}",        test_read_json_cached_missing_file),
]


def main():
    print(f"\n{BOLD}SmartSake Config Robustness Test{RESET}")
    print(f"{DIM}{'─' * 60}{RESET}")
    print(f"{DIM}Tests: {len(TESTS)}  |  Modules: WriteSensors.py, server.py{RESET}\n")

    for name, fn in TESTS:
        runtest(name, fn)

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = len(_results) - passed
    print(f"\n{DIM}{'─' * 60}{RESET}")
    if failed == 0:
        print(f"{BOLD}{GREEN}{passed} passed, {failed} failed{RESET}\n")
        sys.exit(0)
    else:
        print(f"{BOLD}{RED}{passed} passed, {failed} failed{RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
