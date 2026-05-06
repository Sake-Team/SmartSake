#!/usr/bin/env python3
"""
SmartSake — DB Safety Test

Verifies the SQLite layer in db.py — fresh-init, idempotent re-init,
integrity_check on corruption, supersede-leftover-active-runs on
create_run, end_run closing deviation events, schema migration paths,
threadlocal connection isolation, and roundtrip of insert_reading.

Mirrors test_fan_state.py: plain `assert`, ANSI-coloured PASS/FAIL
output, no pytest/unittest dependency.

Usage:
    python3 test_db_safety.py
"""

import os
import sys
import sqlite3
import tempfile
import threading
import traceback
from datetime import datetime
from pathlib import Path

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
import db as sakedb  # noqa: E402


# ── Test runner ─────────────────────────────────────────────────────────────
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


# ── DB scaffolding ──────────────────────────────────────────────────────────

def _new_temp_db():
    """Create a fresh temp DB path, monkey-patch db.DB_FILE to point to it.

    Returns (path_str, restore_fn). restore_fn cleans up wal/shm + temp file.
    """
    fd, path = tempfile.mkstemp(suffix=".db", prefix="smartsake_dbtest_")
    os.close(fd)
    os.remove(path)  # init_db creates it
    orig = sakedb.DB_FILE
    sakedb.DB_FILE = Path(path)
    # Reset thread-local connection state so every test starts fresh.
    sakedb._local = threading.local()

    def _restore():
        # Close this thread's lingering connection (if any).
        try:
            sakedb.close_conn()
        except Exception:
            pass
        sakedb.DB_FILE = orig
        sakedb._local = threading.local()
        for ext in ("", "-wal", "-shm", "-journal"):
            p = path + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    return path, _restore


# ── Tests ───────────────────────────────────────────────────────────────────

def test_fresh_init_creates_all_tables():
    path, restore = _new_temp_db()
    try:
        sakedb.init_db()
        with sakedb.get_conn() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            tables = {r["name"] for r in rows}
        # Spot-check the core tables this product cares about.
        for required in ("runs", "sensor_readings", "fan_overrides",
                         "fan_rules", "deviation_events", "target_profiles",
                         "zone_notes", "run_events", "run_metadata",
                         "reference_curves", "reference_curve_points"):
            assert required in tables, f"missing table: {required}"
    finally:
        restore()


def test_init_db_is_idempotent():
    path, restore = _new_temp_db()
    try:
        sakedb.init_db()
        sakedb.close_conn()  # force a fresh connection on second init
        sakedb.init_db()
        with sakedb.get_conn() as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            assert row[0] == "ok", f"integrity check failed after re-init: {row[0]}"
            # Still exactly one runs table
            n = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='runs'"
            ).fetchone()[0]
            assert n == 1, f"expected one runs table, got {n}"
    finally:
        restore()


def test_integrity_check_raises_on_corrupt_db():
    path, restore = _new_temp_db()
    try:
        # Init once normally so a real SQLite file exists with WAL pages.
        sakedb.init_db()
        sakedb.close_conn()
        # Now scribble garbage over the middle of the page-1 header so the
        # SQLite header magic stays but interior pages don't pass integrity.
        with open(path, "r+b") as f:
            f.seek(100)
            f.write(b"\x00" * 200)
            # Also overwrite a chunk further in to clobber any B-tree pages.
            f.seek(2048)
            f.write(b"\xff" * 1024)
        # Force fresh connection
        sakedb._local = threading.local()
        raised = False
        try:
            sakedb.init_db()
        except sqlite3.DatabaseError:
            raised = True
        except Exception as e:
            # Some forms of corruption surface as a generic OperationalError;
            # still an error path — accept it but record what type.
            print(f"        {DIM}init_db raised {type(e).__name__}: {e}{RESET}")
            raised = True
        assert raised, (
            "init_db should raise (DatabaseError or similar) on corrupt DB; "
            "instead it returned silently — pragma integrity_check protection broken"
        )
    finally:
        restore()


def test_create_run_supersedes_leftover_actives():
    path, restore = _new_temp_db()
    try:
        sakedb.init_db()
        # Manually plant 2 leftover active runs (ended_at IS NULL).
        with sakedb.get_conn() as conn:
            now1 = "2024-01-01T10:00:00"
            now2 = "2024-01-01T11:00:00"
            conn.execute(
                "INSERT INTO runs (name, started_at, status) VALUES (?, ?, 'active')",
                ("orphan-1", now1)
            )
            conn.execute(
                "INSERT INTO runs (name, started_at, status) VALUES (?, ?, 'active')",
                ("orphan-2", now2)
            )
        # Sanity: 2 active rows present
        with sakedb.get_conn() as conn:
            n_before = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE ended_at IS NULL"
            ).fetchone()[0]
        assert n_before == 2, f"setup: expected 2 active rows, got {n_before}"

        new_id = sakedb.create_run("fresh-run")
        assert new_id is not None and new_id > 0

        with sakedb.get_conn() as conn:
            n_active = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE ended_at IS NULL"
            ).fetchone()[0]
            superseded = conn.execute(
                "SELECT name, status, ended_at FROM runs "
                "WHERE name IN ('orphan-1', 'orphan-2')"
            ).fetchall()
        assert n_active == 1, (
            f"after create_run, expected exactly 1 active row, got {n_active}"
        )
        for r in superseded:
            assert r["status"] == "superseded", (
                f"orphan {r['name']} should be 'superseded', got {r['status']!r}"
            )
            assert r["ended_at"] is not None, (
                f"orphan {r['name']} should have ended_at set"
            )
    finally:
        restore()


def test_end_run_closes_open_deviation_events():
    path, restore = _new_temp_db()
    try:
        sakedb.init_db()
        run_id = sakedb.create_run("dev-test")
        # Open two deviation events.
        ev1 = sakedb.create_deviation_event(
            run_id, 1, datetime.now().isoformat(), 2.5, "above", 2.0
        )
        ev2 = sakedb.create_deviation_event(
            run_id, 2, datetime.now().isoformat(), 3.0, "below", 2.0
        )
        # Verify open
        opens = sakedb.get_open_deviation_events(run_id)
        assert len(opens) == 2, f"expected 2 open events, got {len(opens)}"

        sakedb.end_run(run_id)

        opens_after = sakedb.get_open_deviation_events(run_id)
        assert len(opens_after) == 0, (
            f"expected 0 open events after end_run, got {len(opens_after)}"
        )
        # The run itself should also be closed
        run = sakedb.get_run(run_id)
        assert run["status"] == "completed"
        assert run["ended_at"] is not None
    finally:
        restore()


def test_get_active_run_returns_most_recent():
    path, restore = _new_temp_db()
    try:
        sakedb.init_db()
        # 3 ended runs, 1 active
        for i in range(3):
            with sakedb.get_conn() as conn:
                conn.execute(
                    "INSERT INTO runs (name, started_at, ended_at, status) "
                    "VALUES (?, ?, ?, 'completed')",
                    (f"old-{i}", f"2024-01-0{i+1}T00:00:00",
                     f"2024-01-0{i+1}T01:00:00")
                )
        # Active run starts last → most-recent ordering.
        active_id = sakedb.create_run("the-active-one")

        active = sakedb.get_active_run()
        assert active is not None
        assert active["id"] == active_id, (
            f"get_active_run returned id={active['id']}, expected {active_id}"
        )
        assert active["name"] == "the-active-one"
    finally:
        restore()


def test_open_close_deviation_event_roundtrip():
    path, restore = _new_temp_db()
    try:
        sakedb.init_db()
        run_id = sakedb.create_run("roundtrip")
        ev1 = sakedb.create_deviation_event(
            run_id, 1, datetime.now().isoformat(), 2.0, "above", 2.0
        )
        ev2 = sakedb.create_deviation_event(
            run_id, 2, datetime.now().isoformat(), 2.5, "below", 2.0
        )
        opens = sakedb.get_open_deviation_events(run_id)
        assert len(opens) == 2, f"expected 2 open, got {len(opens)}"

        sakedb.close_deviation_event(ev1, datetime.now().isoformat(), 3.0)

        opens2 = sakedb.get_open_deviation_events(run_id)
        assert len(opens2) == 1, f"after closing one, expected 1 open, got {len(opens2)}"
        assert opens2[0]["id"] == ev2, (
            f"the still-open event should be ev2={ev2}, got {opens2[0]['id']}"
        )
    finally:
        restore()


def test_legacy_schema_gets_migrated():
    """Pre-existing sensor_readings without weight_lbs_<n> columns gets migrated."""
    path, restore = _new_temp_db()
    try:
        # Build a legacy-schema DB by hand BEFORE init_db runs.
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                recorded_at DATETIME NOT NULL,
                tc1 REAL, tc2 REAL, tc3 REAL,
                tc4 REAL, tc5 REAL, tc6 REAL,
                sht_temp REAL, humidity REAL,
                fan1 INTEGER, fan2 INTEGER, fan3 INTEGER,
                fan4 INTEGER, fan5 INTEGER, fan6 INTEGER,
                weight_lbs REAL
            )
        """)
        conn.commit()
        conn.close()

        sakedb._local = threading.local()
        sakedb.init_db()

        with sakedb.get_conn() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(sensor_readings)")}
        for c in ("weight_lbs_1", "weight_lbs_2", "weight_lbs_3", "weight_lbs_4"):
            assert c in cols, f"migration did not add {c}; cols are {sorted(cols)}"
    finally:
        restore()


def test_insert_reading_round_trip():
    path, restore = _new_temp_db()
    try:
        sakedb.init_db()
        run_id = sakedb.create_run("rt")
        reading = {
            "recorded_at": "2024-05-05T12:34:56",
            "tc1": 30.1, "tc2": 31.2, "tc3": 32.3,
            "tc4": 33.4, "tc5": 34.5, "tc6": 35.6,
            "sht_temp": 25.7, "humidity": 88.9,
            "fan1": 1, "fan2": 0, "fan3": 1,
            "fan4": 0, "fan5": 1, "fan6": 0,
            "weight_lbs": 12.34,
            "weight_lbs_1": 1.1, "weight_lbs_2": 2.2,
            "weight_lbs_3": 3.3, "weight_lbs_4": 4.4,
        }
        sakedb.insert_reading(run_id, reading)
        latest = sakedb.get_latest_reading(run_id)
        assert latest is not None, "insert_reading + get_latest_reading roundtrip returned None"
        for key, expected in reading.items():
            actual = latest.get(key)
            # SQLite gives back floats for REAL, ints for INTEGER — equality works.
            assert actual == expected, (
                f"field {key!r}: expected {expected!r}, got {actual!r}"
            )
    finally:
        restore()


def test_threadlocal_connection_isolation():
    """Each thread must get a distinct sqlite3.Connection object."""
    path, restore = _new_temp_db()
    try:
        sakedb.init_db()
        # Capture the main-thread connection
        main_conn = sakedb.get_conn()

        captured = {}

        def _worker(tag):
            # Each worker thread gets its OWN _local because threading.local()
            # is per-thread by definition. We're verifying get_conn caches per
            # thread and yields a distinct sqlite3.Connection per thread.
            c = sakedb.get_conn()
            captured[tag] = c
            try:
                # Quick sanity query
                c.execute("SELECT COUNT(*) FROM runs").fetchone()
            finally:
                sakedb.close_conn()

        t1 = threading.Thread(target=_worker, args=("a",))
        t2 = threading.Thread(target=_worker, args=("b",))
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert "a" in captured and "b" in captured, "both worker threads should have run"
        assert captured["a"] is not captured["b"], (
            "two worker-thread connections should be distinct objects"
        )
        assert captured["a"] is not main_conn, (
            "worker connection should differ from main-thread connection"
        )
        assert captured["b"] is not main_conn
    finally:
        restore()


# ── Entry point ─────────────────────────────────────────────────────────────

TESTS = [
    ("fresh DB initializes cleanly with all tables",      test_fresh_init_creates_all_tables),
    ("init_db is idempotent (call twice, integrity ok)",  test_init_db_is_idempotent),
    ("integrity_check raises on corrupt DB",              test_integrity_check_raises_on_corrupt_db),
    ("create_run auto-supersedes leftover active runs",   test_create_run_supersedes_leftover_actives),
    ("end_run closes open deviation events",              test_end_run_closes_open_deviation_events),
    ("get_active_run returns the most recent active",     test_get_active_run_returns_most_recent),
    ("get_open_deviation_events / close roundtrip",       test_open_close_deviation_event_roundtrip),
    ("legacy sensor_readings schema migrates cleanly",    test_legacy_schema_gets_migrated),
    ("insert_reading round-trips all numeric fields",     test_insert_reading_round_trip),
    ("threadlocal connections are isolated per thread",   test_threadlocal_connection_isolation),
]


def main():
    print(f"\n{BOLD}SmartSake DB Safety Test{RESET}")
    print(f"{DIM}{'─' * 60}{RESET}")
    print(f"{DIM}Tests: {len(TESTS)}  |  Module: db.py{RESET}\n")

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
