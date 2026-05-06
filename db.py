import sqlite3
import os
import threading
from datetime import datetime
from pathlib import Path

DB_FILE = Path(__file__).parent / "smartsake.db"

_local = threading.local()


def get_conn():
    if not hasattr(_local, 'conn') or _local.conn is None:
        conn = sqlite3.connect(str(DB_FILE), timeout=10)
        conn.row_factory = sqlite3.Row
        # journal_mode=WAL is persistent (set once in init_db), no need to repeat
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return _local.conn


def close_conn():
    """Close this thread's cached connection (call on thread exit)."""
    conn = getattr(_local, 'conn', None)
    if conn:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


def init_db():
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                started_at  DATETIME NOT NULL,
                ended_at    DATETIME,
                status      TEXT NOT NULL DEFAULT 'active',
                notes       TEXT
            );

            CREATE TABLE IF NOT EXISTS sensor_readings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      INTEGER NOT NULL REFERENCES runs(id),
                recorded_at DATETIME NOT NULL,
                tc1  REAL, tc2  REAL, tc3  REAL,
                tc4  REAL, tc5  REAL, tc6  REAL,
                sht_temp    REAL,
                humidity    REAL,
                fan1 INTEGER, fan2 INTEGER, fan3 INTEGER,
                fan4 INTEGER, fan5 INTEGER, fan6 INTEGER,
                weight_lbs  REAL
            );

            CREATE TABLE IF NOT EXISTS target_profiles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      INTEGER NOT NULL REFERENCES runs(id),
                elapsed_min INTEGER NOT NULL,
                temp_target REAL
            );

            CREATE TABLE IF NOT EXISTS zone_notes (
                run_id      INTEGER NOT NULL REFERENCES runs(id),
                zone        INTEGER NOT NULL,
                note        TEXT,
                updated_at  DATETIME NOT NULL,
                PRIMARY KEY (run_id, zone)
            );

            CREATE TABLE IF NOT EXISTS fan_overrides (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      INTEGER NOT NULL REFERENCES runs(id),
                zone        INTEGER NOT NULL CHECK(zone BETWEEN 1 AND 6),
                action      TEXT NOT NULL CHECK(action IN ('on','off')),
                expires_at  DATETIME,
                created_at  DATETIME NOT NULL,
                UNIQUE(run_id, zone)
            );

            CREATE TABLE IF NOT EXISTS fan_rules (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id              INTEGER NOT NULL REFERENCES runs(id),
                zone                INTEGER NOT NULL CHECK(zone BETWEEN 1 AND 6),
                rule_type           TEXT NOT NULL CHECK(rule_type IN ('time_window','threshold')),
                elapsed_min_start   INTEGER,
                elapsed_min_end     INTEGER,
                threshold_temp_c    REAL,
                threshold_dir       TEXT CHECK(threshold_dir IN ('above','below')),
                threshold_dur_min   INTEGER,
                fan_action          TEXT NOT NULL CHECK(fan_action IN ('on','off')),
                enabled             INTEGER NOT NULL DEFAULT 1,
                created_at          DATETIME NOT NULL
            );

            CREATE TABLE IF NOT EXISTS deviation_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id          INTEGER NOT NULL REFERENCES runs(id),
                zone            INTEGER NOT NULL CHECK(zone BETWEEN 1 AND 6),
                started_at      DATETIME NOT NULL,
                ended_at        DATETIME,
                max_deviation   REAL NOT NULL,
                direction       TEXT CHECK(direction IN ('above','below')),
                threshold_used  REAL NOT NULL,
                stage           TEXT
            );

            CREATE TABLE IF NOT EXISTS run_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      INTEGER NOT NULL REFERENCES runs(id),
                event_type  TEXT NOT NULL DEFAULT 'stage',
                label       TEXT NOT NULL,
                elapsed_min INTEGER NOT NULL,
                recorded_at DATETIME NOT NULL
            );

            CREATE TABLE IF NOT EXISTS run_metadata (
                run_id              INTEGER PRIMARY KEY REFERENCES runs(id),
                koji_variety        TEXT CHECK(koji_variety IN ('yellow','white','black','other')),
                inoculation_rate    REAL,
                source_rice         TEXT,
                polish_ratio        INTEGER CHECK(polish_ratio BETWEEN 0 AND 100),
                quality_score       INTEGER CHECK(quality_score BETWEEN 1 AND 5),
                tasting_notes       TEXT,
                updated_at          DATETIME NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reference_curves (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                description TEXT,
                source      TEXT,
                created_at  DATETIME NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reference_curve_points (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                curve_id        INTEGER NOT NULL REFERENCES reference_curves(id),
                elapsed_min     INTEGER NOT NULL,
                temp_target     REAL
            );

            CREATE INDEX IF NOT EXISTS idx_readings_run ON sensor_readings(run_id);
            CREATE INDEX IF NOT EXISTS idx_readings_time ON sensor_readings(recorded_at);
            CREATE INDEX IF NOT EXISTS idx_overrides_run ON fan_overrides(run_id);
            CREATE INDEX IF NOT EXISTS idx_rules_run ON fan_rules(run_id, zone);
            CREATE INDEX IF NOT EXISTS idx_deviations_run ON deviation_events(run_id, zone);
            CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id);
            CREATE INDEX IF NOT EXISTS idx_refcurve_points ON reference_curve_points(curve_id);
        """)
        # Add temp_target to reference_curve_points if upgrading from older schema
        # Must run before _seed_reference_curves which inserts using this column
        try:
            conn.execute("ALTER TABLE reference_curve_points ADD COLUMN temp_target REAL")
        except Exception:
            pass  # column already exists

        # Add temp_target to target_profiles if upgrading from older schema
        try:
            conn.execute("ALTER TABLE target_profiles ADD COLUMN temp_target REAL")
        except Exception:
            pass  # column already exists

        # Fix curves seeded before temp_target column existed (all NULL values)
        # Must run BEFORE _seed_reference_curves so it can re-seed after cleanup
        _fix_null_temp_target_curves(conn)

        _seed_reference_curves(conn)

        # Add multi-scale weight columns if upgrading from older schema
        for col in ("weight_lbs_1", "weight_lbs_2", "weight_lbs_3", "weight_lbs_4"):
            try:
                conn.execute(f"ALTER TABLE sensor_readings ADD COLUMN {col} REAL")
            except Exception:
                pass  # column already exists

        # Add weight/humidity target columns if upgrading from older schema
        for col, default in [
            ("weight_target_min", "NULL"),
            ("weight_target_max", "NULL"),
            ("humidity_target_min", "85.0"),
            ("humidity_target_max", "95.0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {col} REAL DEFAULT {default}")
            except Exception:
                pass  # column already exists
        # Composite index for weight analytics rolling-window query
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_readings_run_time "
                "ON sensor_readings(run_id, recorded_at)"
            )
        except Exception:
            pass

        # Add pinned column for auto-prune protection
        try:
            conn.execute("ALTER TABLE runs ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass  # column already exists


def _fix_null_temp_target_curves(conn):
    # Check if any reference curve points have NULL temp_target (pre-migration data)
    bad = conn.execute(
        "SELECT COUNT(*) FROM reference_curve_points WHERE temp_target IS NULL"
    ).fetchone()[0]
    if bad == 0:
        return
    # Only delete built-in curves that have NULL data — preserve user-created curves
    builtin_names = ("Standard Yellow Koji (Ginjo)", "Mugi Koji (Barley)", "Soy Koji (Extended)")
    for name in builtin_names:
        row = conn.execute("SELECT id FROM reference_curves WHERE name=?", (name,)).fetchone()
        if row:
            conn.execute("DELETE FROM reference_curve_points WHERE curve_id=?", (row[0],))
            conn.execute("DELETE FROM reference_curves WHERE id=?", (row[0],))
    print("[db] Cleared corrupted built-in reference curves — will re-seed")


def _seed_reference_curves(conn):
    """Insert built-in koji reference curves if none exist yet."""
    count = conn.execute("SELECT COUNT(*) FROM reference_curves").fetchone()[0]
    if count > 0:
        return
    curves = [
        {
            "name": "Standard Yellow Koji (Ginjo)",
            "description": "48-hour yellow koji profile for ginjo-grade sake rice",
            "source": "Traditional sake brewery guidelines",
            "points": [
                (0,    30),
                (360,  31),
                (720,  33),   # kiri-kaeshi
                (960,  36),
                (1200, 38),   # naka-shigoto
                (1440, 40),
                (1680, 41),   # shimai-shigoto
                (2880, 36),   # finish
            ],
        },
        {
            "name": "Mugi Koji (Barley)",
            "description": "44-hour barley koji profile for shochu and miso production",
            "source": "Traditional barley koji guidelines",
            "points": [
                (0,    32),
                (480,  34),
                (720,  36),
                (1080, 40),
                (1440, 42),
                (1800, 40),
                (2280, 35),
                (2640, 33),
            ],
        },
        {
            "name": "Soy Koji (Extended)",
            "description": "60-hour koji profile for soy sauce and miso",
            "source": "Extended fermentation guidelines",
            "points": [
                (0,    30),
                (600,  32),
                (1200, 36),
                (1440, 39),
                (1800, 42),
                (2400, 42),
                (2880, 40),
                (3240, 36),
                (3600, 33),
            ],
        },
    ]
    now = datetime.now().isoformat()
    for c in curves:
        cur = conn.execute(
            "INSERT INTO reference_curves (name, description, source, created_at) VALUES (?,?,?,?)",
            (c["name"], c["description"], c["source"], now)
        )
        curve_id = cur.lastrowid
        conn.executemany(
            """INSERT INTO reference_curve_points
               (curve_id, elapsed_min, temp_target)
               VALUES (?,?,?)""",
            [(curve_id, p[0], p[1]) for p in c["points"]]
        )


# ── Runs ──────────────────────────────────────────────────────────────────────

def create_run(name):
    now = datetime.now().isoformat()
    with get_conn() as conn:
        # Auto-end any existing active runs (ended_at IS NULL) as 'superseded'
        # before starting a new one. Prevents the multiple-active-runs leak
        # where two near-simultaneous starts (or start without prior end)
        # create orphan rows that block prune_for_space and disagree on which
        # is current. get_active_run returns most-recent only, so older
        # actives would otherwise sit forever.
        conn.execute(
            "UPDATE runs SET ended_at=?, status='superseded' "
            "WHERE ended_at IS NULL",
            (now,)
        )
        conn.execute(
            "UPDATE deviation_events SET ended_at=? "
            "WHERE ended_at IS NULL AND run_id IN "
            "(SELECT id FROM runs WHERE status='superseded' AND ended_at=?)",
            (now, now)
        )
        cur = conn.execute(
            "INSERT INTO runs (name, started_at, status) VALUES (?, ?, 'active')",
            (name, now)
        )
        run_id = cur.lastrowid
        # No overrides — all zones start in auto mode. Fans won't spin
        # immediately because _fan_on starts False and the deadband hold
        # requires one extra tick (~10 s) before switching.
        return run_id


def end_run(run_id):
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE runs SET ended_at=?, status='completed' WHERE id=?",
            (now, run_id)
        )
        # Close any open deviation events
        conn.execute(
            "UPDATE deviation_events SET ended_at=? WHERE run_id=? AND ended_at IS NULL",
            (now, run_id)
        )


def mark_crashed(run_id):
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE runs SET status='crashed' WHERE id=? AND ended_at IS NULL",
            (run_id,)
        )
        conn.execute(
            "UPDATE deviation_events SET ended_at=? WHERE run_id=? AND ended_at IS NULL",
            (now, run_id)
        )


def get_active_run():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def get_all_runs():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT r.*, m.koji_variety
            FROM runs r
            LEFT JOIN run_metadata m ON m.run_id = r.id
            ORDER BY r.started_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_run(run_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None


def delete_run(run_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM zone_notes WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM target_profiles WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM fan_overrides WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM fan_rules WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM deviation_events WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM run_events WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM run_metadata WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM sensor_readings WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM runs WHERE id=?", (run_id,))


def set_run_pinned(run_id, pinned):
    """Lock or unlock a run to protect it from auto-pruning."""
    with get_conn() as conn:
        conn.execute("UPDATE runs SET pinned=? WHERE id=?", (1 if pinned else 0, run_id))


def prune_for_space(min_free_mb=500):
    """Delete unlocked runs (oldest first) until disk has min_free_mb free.

    Only deletes ended, unlocked runs. Never touches active or locked runs.
    Returns the number of runs deleted.
    """
    import shutil
    db_path = str(DB_FILE.parent)
    free_mb = shutil.disk_usage(db_path).free / (1024 * 1024)
    if free_mb >= min_free_mb:
        return 0

    with get_conn() as conn:
        candidates = conn.execute("""
            SELECT id, name FROM runs
            WHERE pinned = 0
              AND status != 'active'
              AND ended_at IS NOT NULL
            ORDER BY ended_at ASC
        """).fetchall()

    count = 0
    for row in candidates:
        delete_run(row['id'])
        count += 1
        # Re-check free space after each deletion
        free_mb = shutil.disk_usage(db_path).free / (1024 * 1024)
        if free_mb >= min_free_mb:
            break
    return count


# ── Sensor readings ───────────────────────────────────────────────────────────

def insert_reading(run_id, data):
    """data keys: tc1-tc6, sht_temp, humidity, fan1-fan6, weight_lbs, weight_lbs_1..4 (all optional)"""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO sensor_readings
                (run_id, recorded_at,
                 tc1, tc2, tc3, tc4, tc5, tc6,
                 sht_temp, humidity,
                 fan1, fan2, fan3, fan4, fan5, fan6,
                 weight_lbs,
                 weight_lbs_1, weight_lbs_2, weight_lbs_3, weight_lbs_4)
            VALUES
                (?, ?,
                 ?, ?, ?, ?, ?, ?,
                 ?, ?,
                 ?, ?, ?, ?, ?, ?,
                 ?,
                 ?, ?, ?, ?)
        """, (
            run_id,
            data.get("recorded_at", datetime.now().isoformat()),
            data.get("tc1"), data.get("tc2"), data.get("tc3"),
            data.get("tc4"), data.get("tc5"), data.get("tc6"),
            data.get("sht_temp"), data.get("humidity"),
            data.get("fan1"), data.get("fan2"), data.get("fan3"),
            data.get("fan4"), data.get("fan5"), data.get("fan6"),
            data.get("weight_lbs"),
            data.get("weight_lbs_1"), data.get("weight_lbs_2"),
            data.get("weight_lbs_3"), data.get("weight_lbs_4"),
        ))


def get_latest_reading(run_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sensor_readings WHERE run_id=? ORDER BY recorded_at DESC LIMIT 1",
            (run_id,)
        ).fetchone()
        return dict(row) if row else None


def stream_readings(run_id):
    """Yield sensor_readings rows one at a time (cursor-based, no fetchall).

    Each yielded item is a sqlite3.Row.  Caller must iterate to completion
    or the cursor will be held open.
    """
    conn = get_conn()
    cursor = conn.execute(
        "SELECT * FROM sensor_readings WHERE run_id=? ORDER BY recorded_at ASC",
        (run_id,)
    )
    for row in cursor:
        yield row


def get_room_history(hours, max_points=600):
    """Return up to max_points sensor readings from the last `hours` hours, across all runs.

    Uses rowid modulo instead of ROW_NUMBER() to avoid materializing all rows.
    """
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    with get_conn() as conn:
        bounds = conn.execute(
            "SELECT COUNT(*), MIN(sr.rowid), MAX(sr.rowid) FROM sensor_readings sr WHERE sr.recorded_at >= ?",
            (cutoff,)
        ).fetchone()
        total, min_rid, max_rid = bounds[0], bounds[1], bounds[2]
        if total == 0:
            return []
        stride = max(1, total // max_points)
        rows = conn.execute("""
            SELECT sr.*, r.name AS run_name
            FROM sensor_readings sr
            JOIN runs r ON sr.run_id = r.id
            WHERE sr.recorded_at >= ? AND (sr.rowid - ?) % ? = 0
            ORDER BY sr.recorded_at ASC
        """, (cutoff, min_rid, stride)).fetchall()
        return [dict(r) for r in rows]


def get_readings_sampled(run_id, n=300):
    """Return up to n evenly-strided readings for run_id.

    Uses rowid modulo instead of ROW_NUMBER() window function to avoid
    materializing all rows before filtering — O(n) output instead of O(total).
    """
    with get_conn() as conn:
        bounds = conn.execute(
            "SELECT COUNT(*), MIN(rowid), MAX(rowid) FROM sensor_readings WHERE run_id=?",
            (run_id,)
        ).fetchone()
        total, min_rid, max_rid = bounds[0], bounds[1], bounds[2]
        if total == 0:
            return []
        stride = max(1, total // n)
        rows = conn.execute("""
            SELECT *,
                   CASE WHEN weight_lbs_1 IS NULL AND weight_lbs_2 IS NULL
                             AND weight_lbs_3 IS NULL AND weight_lbs_4 IS NULL
                        THEN NULL
                        ELSE (COALESCE(weight_lbs_1,0)+COALESCE(weight_lbs_2,0)
                              +COALESCE(weight_lbs_3,0)+COALESCE(weight_lbs_4,0))
                   END AS weight_total_lbs
            FROM sensor_readings
            WHERE run_id = ? AND (rowid - ?) % ? = 0
            ORDER BY recorded_at ASC
        """, (run_id, min_rid, stride)).fetchall()
        return [dict(r) for r in rows]


# ── Target profiles ───────────────────────────────────────────────────────────

def save_target_profile(run_id, rows):
    """rows: list of dicts with elapsed_min, temp_target"""
    with get_conn() as conn:
        conn.execute("DELETE FROM target_profiles WHERE run_id=?", (run_id,))
        conn.executemany("""
            INSERT INTO target_profiles
                (run_id, elapsed_min, temp_target)
            VALUES (?, ?, ?)
        """, [
            (run_id, r["elapsed_min"], r.get("temp_target"))
            for r in rows
        ])


def get_target_profile(run_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM target_profiles WHERE run_id=? ORDER BY elapsed_min ASC",
            (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def clear_target_profile(run_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM target_profiles WHERE run_id=?", (run_id,))


# ── Fan overrides ─────────────────────────────────────────────────────────

def set_fan_override(run_id, zone, action, duration_minutes=None):
    """Upsert a manual fan override for a zone.

    duration_minutes=None means "until end of run" (expires_at stays NULL).
    """
    expires_at = None
    if duration_minutes is not None:
        from datetime import timedelta
        expires_at = (datetime.now() + timedelta(minutes=duration_minutes)).isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO fan_overrides (run_id, zone, action, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id, zone) DO UPDATE SET
                action=excluded.action,
                expires_at=excluded.expires_at,
                created_at=excluded.created_at
        """, (run_id, zone, action, expires_at, datetime.now().isoformat()))


def get_fan_override(run_id, zone):
    """Return the active override for a zone, or None if absent/expired."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT * FROM fan_overrides
            WHERE run_id=? AND zone=?
              AND (expires_at IS NULL OR expires_at > ?)
        """, (run_id, zone, datetime.now().isoformat())).fetchone()
        return dict(row) if row else None


def get_all_fan_overrides(run_id):
    """Return {zone_int: {action, expires_at}} for all active overrides in a run."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT zone, action, expires_at FROM fan_overrides
            WHERE run_id=?
              AND (expires_at IS NULL OR expires_at > ?)
        """, (run_id, datetime.now().isoformat())).fetchall()
        return {r["zone"]: {"action": r["action"], "expires_at": r["expires_at"]}
                for r in rows}


def clear_fan_override(run_id, zone):
    """Remove the manual override for a zone (return to automatic)."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM fan_overrides WHERE run_id=? AND zone=?",
            (run_id, zone)
        )


# ── Fan rules ─────────────────────────────────────────────────────────────────

def create_fan_rule(run_id, zone, rule_type, fan_action,
                    elapsed_min_start=None, elapsed_min_end=None,
                    threshold_temp_c=None, threshold_dir=None, threshold_dur_min=None):
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO fan_rules
                (run_id, zone, rule_type, fan_action,
                 elapsed_min_start, elapsed_min_end,
                 threshold_temp_c, threshold_dir, threshold_dur_min,
                 created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (run_id, zone, rule_type, fan_action,
              elapsed_min_start, elapsed_min_end,
              threshold_temp_c, threshold_dir, threshold_dur_min,
              datetime.now().isoformat()))
        return cur.lastrowid


def get_fan_rules(run_id, zone=None):
    with get_conn() as conn:
        if zone is not None:
            rows = conn.execute(
                "SELECT * FROM fan_rules WHERE run_id=? AND zone=? ORDER BY created_at ASC",
                (run_id, zone)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM fan_rules WHERE run_id=? ORDER BY zone ASC, created_at ASC",
                (run_id,)
            ).fetchall()
        return [dict(r) for r in rows]


def set_fan_rule_enabled(rule_id, enabled):
    with get_conn() as conn:
        conn.execute(
            "UPDATE fan_rules SET enabled=? WHERE id=?",
            (1 if enabled else 0, rule_id)
        )


def delete_fan_rule(rule_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM fan_rules WHERE id=?", (rule_id,))


# ── Deviation events ───────────────────────────────────────────────────────────

def create_deviation_event(run_id, zone, started_at, max_deviation, direction, threshold_used, stage=None):
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO deviation_events
                (run_id, zone, started_at, max_deviation, direction, threshold_used, stage)
            VALUES (?,?,?,?,?,?,?)
        """, (run_id, zone, started_at, max_deviation, direction, threshold_used, stage))
        return cur.lastrowid


def close_deviation_event(event_id, ended_at, max_deviation):
    with get_conn() as conn:
        conn.execute(
            "UPDATE deviation_events SET ended_at=?, max_deviation=? WHERE id=?",
            (ended_at, max_deviation, event_id)
        )


def update_deviation_max(event_id, max_deviation):
    with get_conn() as conn:
        conn.execute(
            "UPDATE deviation_events SET max_deviation=? WHERE id=? AND max_deviation < ?",
            (max_deviation, event_id, max_deviation)
        )


def get_deviation_events(run_id, zone=None):
    with get_conn() as conn:
        if zone is not None:
            rows = conn.execute(
                "SELECT * FROM deviation_events WHERE run_id=? AND zone=? ORDER BY started_at ASC",
                (run_id, zone)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM deviation_events WHERE run_id=? ORDER BY started_at ASC",
                (run_id,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_open_deviation_events(run_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM deviation_events WHERE run_id=? AND ended_at IS NULL",
            (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── Run events (stage markers) ────────────────────────────────────────────────

def create_run_event(run_id, label, elapsed_min, event_type='stage'):
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO run_events (run_id, event_type, label, elapsed_min, recorded_at)
            VALUES (?,?,?,?,?)
        """, (run_id, event_type, label, elapsed_min, datetime.now().isoformat()))
        return cur.lastrowid


def get_run_events(run_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM run_events WHERE run_id=? ORDER BY elapsed_min ASC",
            (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_run_event(event_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM run_events WHERE id=?", (event_id,))


# ── Run metadata ───────────────────────────────────────────────────────────────

_VALID_VARIETIES = ('yellow', 'white', 'black', 'other')


def get_run_metadata(run_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM run_metadata WHERE run_id=?", (run_id,)
        ).fetchone()
        return dict(row) if row else {}


def upsert_run_metadata(run_id, data):
    """Upsert only the keys present in data. Returns updated metadata dict."""
    allowed = ('koji_variety', 'inoculation_rate', 'source_rice',
               'polish_ratio')
    keys = [k for k in allowed if k in data]
    if not keys:
        return get_run_metadata(run_id)
    now = datetime.now().isoformat()
    with get_conn() as conn:
        # Build upsert: insert or update only provided columns
        placeholders = ','.join('?' for _ in keys)
        col_list = ','.join(keys)
        updates = ','.join(f"{k}=excluded.{k}" for k in keys)
        values = [data[k] for k in keys]
        conn.execute(f"""
            INSERT INTO run_metadata (run_id, {col_list}, updated_at)
            VALUES (?, {placeholders}, ?)
            ON CONFLICT(run_id) DO UPDATE SET {updates}, updated_at=excluded.updated_at
        """, [run_id] + values + [now])
    return get_run_metadata(run_id)


# ── Reference curves ──────────────────────────────────────────────────────────

def get_all_reference_curves():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, description, source, created_at FROM reference_curves ORDER BY name ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_reference_curve(curve_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM reference_curves WHERE id=?", (curve_id,)
        ).fetchone()
        if not row:
            return None
        curve = dict(row)
        pts = conn.execute(
            "SELECT * FROM reference_curve_points WHERE curve_id=? ORDER BY elapsed_min ASC",
            (curve_id,)
        ).fetchall()
        curve['points'] = [dict(p) for p in pts]
        return curve


def create_reference_curve(name, description, source, points):
    now = datetime.now().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reference_curves (name, description, source, created_at) VALUES (?,?,?,?)",
            (name, description, source, now)
        )
        curve_id = cur.lastrowid
        conn.executemany("""
            INSERT INTO reference_curve_points
                (curve_id, elapsed_min, temp_target)
            VALUES (?,?,?)
        """, [(curve_id, p['elapsed_min'], p.get('temp_target'))
              for p in points])
        return curve_id


def delete_reference_curve(curve_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM reference_curve_points WHERE curve_id=?", (curve_id,))
        conn.execute("DELETE FROM reference_curves WHERE id=?", (curve_id,))


def load_curve_as_target(run_id, curve_id):
    """Copy reference curve points into a run's target_profiles (replaces existing)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM target_profiles WHERE run_id=?", (run_id,))
        conn.execute("""
            INSERT INTO target_profiles
                (run_id, elapsed_min, temp_target)
            SELECT ?, elapsed_min, temp_target
            FROM reference_curve_points WHERE curve_id=?
        """, (run_id, curve_id))


# ── Curve generation from historical runs ────────────────────────────────────

def _bucket_average_one_source(rows, bucket_min):
    """Bucket a single source's rows into per-bucket zone-averaged temps.

    rows: iterable of {'elapsed_min': float, 'zones': [t1..t6 or None]}.
    Returns {bucket_start_min: avg_temp_c} where each bucket value is the mean
    of per-row "average across present zones" temperatures.
    """
    from collections import defaultdict
    buckets = defaultdict(list)
    for r in rows:
        zones = [t for t in r.get('zones', []) if t is not None]
        if not zones:
            continue
        elapsed = r.get('elapsed_min')
        if elapsed is None or elapsed < 0:
            continue
        bucket = int(elapsed // bucket_min) * bucket_min
        buckets[bucket].append(sum(zones) / len(zones))
    return {b: sum(v) / len(v) for b, v in buckets.items()}


def _combine_buckets(per_source_buckets):
    """Average each bucket across sources. Returns [(elapsed_min, rounded_temp), ...]."""
    from collections import defaultdict
    cross = defaultdict(lambda: {'sum': 0.0, 'count': 0})
    for b in per_source_buckets:
        for bucket, val in b.items():
            cross[bucket]['sum'] += val
            cross[bucket]['count'] += 1
    return [
        (em, round(cross[em]['sum'] / cross[em]['count'], 1))
        for em in sorted(cross) if cross[em]['count']
    ]


def generate_curve_from_runs(run_ids, bucket_min=30):
    """Average zone temperatures from multiple runs into bucketed reference curve points.

    For each run: stream readings from cursor (never fetchall) → bucket on the fly →
    average within bucket. Then average the per-run bucket averages across all runs.
    Returns [(elapsed_min, avg_temp_rounded), ...] sorted by elapsed_min.
    """
    per_run = []
    with get_conn() as conn:
        for run_id in run_ids:
            row = conn.execute("SELECT started_at FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                continue
            started_at = datetime.fromisoformat(row['started_at'])

            def _stream_rows(rid, t0):
                """Yield row dicts from cursor without loading all into memory."""
                cur = conn.execute("""
                    SELECT recorded_at, tc1, tc2, tc3, tc4, tc5, tc6
                    FROM sensor_readings WHERE run_id=?
                    ORDER BY recorded_at ASC
                """, (rid,))
                while True:
                    batch = cur.fetchmany(2000)
                    if not batch:
                        break
                    for r in batch:
                        elapsed = (datetime.fromisoformat(r['recorded_at']) - t0).total_seconds() / 60
                        yield {
                            'elapsed_min': elapsed,
                            'zones': [r['tc' + str(i)] for i in range(1, 7)],
                        }

            per_run.append(_bucket_average_one_source(_stream_rows(run_id, started_at), bucket_min))

    return _combine_buckets(per_run)


def generate_curve_from_csv(csv_text, bucket_min=30):
    """Average zone temperatures from a single CSV into bucketed curve points.

    Returns [(elapsed_min, avg_temp_rounded), ...] sorted by elapsed_min.
    Raises ValueError if the CSV is unparseable or has no usable rows.
    """
    rows = parse_curve_csv(csv_text)
    if not rows:
        raise ValueError("CSV contained no usable temperature rows.")
    return _combine_buckets([_bucket_average_one_source(rows, bucket_min)])


_TS_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
)


def _parse_timestamp(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00").replace("/", "-"))
    except ValueError:
        pass
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _parse_float(s):
    if s is None:
        return None
    s = str(s).strip()
    if not s or s.upper() in ("ERROR", "NA", "NAN", "NULL", "NONE", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_curve_csv(csv_text):
    """Parse a sensor CSV into [{'elapsed_min': float, 'zones': [t1..t6]}].

    Tolerates the run-export header (Timestamp, TC1..TC6, ...), the live writer
    header (timestamp, sht30_temp_c, sht30_humidity_rh, TC1_temp_c..), and any
    CSV that includes either an 'elapsed_min' column or a recognizable timestamp
    column plus zone columns named TC1..TC6 (case-insensitive, with or without
    a _temp_c suffix).

    Missing/invalid temperatures (blank, "ERROR", "NaN", non-numeric) become None.
    If no elapsed_min column is present, elapsed is derived from the first row's
    timestamp.
    """
    import csv
    import io
    import re

    text = (csv_text or "").lstrip("﻿")
    if not text.strip():
        return []

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return []
    norm = [h.strip().lower() for h in header]

    def find_col(*candidates):
        for cand in candidates:
            if cand in norm:
                return norm.index(cand)
        return -1

    elapsed_idx = find_col("elapsed_min", "elapsed_minutes", "elapsed", "time_minutes")
    ts_idx      = find_col("timestamp", "recorded_at", "time", "datetime")

    tc_idx = [-1] * 6
    # Match: tc1, tc_1, tc1_temp_c, temp1, temp1_target, temp_1, zone1, zone_1
    tc_pat = re.compile(
        r"^(?:tc[_ ]?([1-6])(?:[_ ]?temp(?:_c)?)?|temp[_ ]?([1-6])(?:[_ ]?target)?|zone[_ ]?([1-6]))$"
    )
    for i, h in enumerate(norm):
        m = tc_pat.match(h)
        if m:
            zone = int(next(g for g in m.groups() if g is not None)) - 1
            if tc_idx[zone] == -1:
                tc_idx[zone] = i

    if elapsed_idx == -1 and ts_idx == -1:
        raise ValueError("CSV has no 'elapsed_min' or 'time_minutes' column and no timestamp column.")
    if all(idx == -1 for idx in tc_idx):
        raise ValueError(
            "CSV has no temperature columns. "
            "Expected headers like TC1..TC6, temp1..temp6, or zone1..zone6."
        )

    out = []
    origin = None
    for raw in reader:
        if not raw or all(not (c or "").strip() for c in raw):
            continue

        # Pad short rows so indexing is safe
        if len(raw) < len(header):
            raw = raw + [""] * (len(header) - len(raw))

        elapsed = None
        if elapsed_idx != -1:
            elapsed = _parse_float(raw[elapsed_idx])
        if elapsed is None and ts_idx != -1:
            ts = _parse_timestamp(raw[ts_idx])
            if ts is not None:
                if origin is None:
                    origin = ts
                elapsed = (ts - origin).total_seconds() / 60.0
        if elapsed is None:
            continue

        zones = [
            _parse_float(raw[idx]) if idx != -1 and idx < len(raw) else None
            for idx in tc_idx
        ]
        out.append({'elapsed_min': elapsed, 'zones': zones})

    return out


# ── Zone notes ────────────────────────────────────────────────────────────────

def save_zone_note(run_id, zone, text):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO zone_notes (run_id, zone, note, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id, zone) DO UPDATE SET note=excluded.note, updated_at=excluded.updated_at
        """, (run_id, zone, text, datetime.now().isoformat()))


def get_zone_notes(run_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT zone, note FROM zone_notes WHERE run_id=?", (run_id,)
        ).fetchall()
        return {str(r["zone"]): r["note"] for r in rows}


# ── Phase 2: Completed runs, weight/humidity analytics ────────────────────────

def get_completed_runs():
    """Return completed/crashed runs joined with metadata."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT r.*, m.koji_variety
            FROM runs r
            LEFT JOIN run_metadata m ON m.run_id = r.id
            WHERE r.status IN ('completed', 'crashed')
            ORDER BY r.started_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_run_summary(run_id):
    """Return hourly temp stats per zone for a run.

    Returns list of {hour, zones: {1: {min, max, avg}, ...}}.
    Hour 0 = first hour of the run, etc.
    """
    with get_conn() as conn:
        run = conn.execute("SELECT started_at FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            return []
        rows = conn.execute("""
            SELECT
                CAST((julianday(recorded_at) - julianday(?)) * 24 AS INTEGER) AS hour,
                MIN(tc1) AS min1, MAX(tc1) AS max1, AVG(tc1) AS avg1,
                MIN(tc2) AS min2, MAX(tc2) AS max2, AVG(tc2) AS avg2,
                MIN(tc3) AS min3, MAX(tc3) AS max3, AVG(tc3) AS avg3,
                MIN(tc4) AS min4, MAX(tc4) AS max4, AVG(tc4) AS avg4,
                MIN(tc5) AS min5, MAX(tc5) AS max5, AVG(tc5) AS avg5,
                MIN(tc6) AS min6, MAX(tc6) AS max6, AVG(tc6) AS avg6
            FROM sensor_readings
            WHERE run_id = ?
            GROUP BY hour
            ORDER BY hour ASC
        """, (run['started_at'], run_id)).fetchall()

        result = []
        for r in rows:
            h = r['hour']
            if h is None or h < 0:
                continue
            zones = {}
            for z in range(1, 7):
                mn = r[f'min{z}']
                mx = r[f'max{z}']
                av = r[f'avg{z}']
                if mn is not None:
                    zones[z] = {
                        'min': round(mn, 1),
                        'max': round(mx, 1),
                        'avg': round(av, 1),
                    }
            result.append({'hour': h, 'zones': zones})
        return result


def get_weight_analytics(run_id):
    """Return weight analytics for run_id.

    Returns dict with:
        initial_lbs, current_lbs, loss_lbs, loss_pct,
        rate_lbs_per_hr (loss rate over last 60 min),
        scale_count (number of non-null scales in most recent reading),
        samples: [{elapsed_min, weight_lbs}]  (max 120 points, weight_lbs = total)
    """
    _TOTAL = "(COALESCE(weight_lbs_1,0)+COALESCE(weight_lbs_2,0)+COALESCE(weight_lbs_3,0)+COALESCE(weight_lbs_4,0))"
    _ANY = "(weight_lbs_1 IS NOT NULL OR weight_lbs_2 IS NOT NULL OR weight_lbs_3 IS NOT NULL OR weight_lbs_4 IS NOT NULL)"

    with get_conn() as conn:
        run = conn.execute(
            "SELECT started_at FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        if not run:
            return None

        first = conn.execute(f"""
            SELECT {_TOTAL} AS total_lbs, recorded_at FROM sensor_readings
            WHERE run_id=? AND {_ANY}
            ORDER BY recorded_at ASC LIMIT 1
        """, (run_id,)).fetchone()

        last = conn.execute(f"""
            SELECT {_TOTAL} AS total_lbs, recorded_at,
                   weight_lbs_1, weight_lbs_2, weight_lbs_3, weight_lbs_4
            FROM sensor_readings
            WHERE run_id=? AND {_ANY}
            ORDER BY recorded_at DESC LIMIT 1
        """, (run_id,)).fetchone()

        if not first or not last:
            return {
                "initial_lbs": None, "current_lbs": None,
                "loss_lbs": None, "loss_pct": None,
                "rate_lbs_per_hr": None, "scale_count": 0, "samples": [],
            }

        initial_lbs = first["total_lbs"]
        current_lbs = last["total_lbs"]
        loss_lbs = round(initial_lbs - current_lbs, 3)
        loss_pct = round(loss_lbs / initial_lbs * 100, 2) if initial_lbs else None

        scale_count = sum(
            1 for i in range(1, 5)
            if last[f"weight_lbs_{i}"] is not None
        )

        # Rate over last 60 minutes (first vs last point in window)
        rate_lbs_per_hr = None
        window = conn.execute(f"""
            SELECT {_TOTAL} AS total_lbs, recorded_at FROM sensor_readings
            WHERE run_id=? AND {_ANY}
              AND recorded_at >= datetime(
                  (SELECT MAX(recorded_at) FROM sensor_readings
                   WHERE run_id=? AND {_ANY}),
                  '-60 minutes')
            ORDER BY recorded_at ASC
        """, (run_id, run_id)).fetchall()
        if len(window) >= 2:
            from datetime import datetime as _dt
            t0 = _dt.fromisoformat(window[0]["recorded_at"])
            t1 = _dt.fromisoformat(window[-1]["recorded_at"])
            hr_diff = (t1 - t0).total_seconds() / 3600
            if hr_diff > 0:
                rate_lbs_per_hr = round(
                    (window[0]["total_lbs"] - window[-1]["total_lbs"]) / hr_diff, 4
                )

        # Downsampled sparkline (max 120 points) — rowid modulo, no window function
        wt_bounds = conn.execute(
            f"SELECT COUNT(*), MIN(rowid), MAX(rowid) FROM sensor_readings WHERE run_id=? AND {_ANY}",
            (run_id,)
        ).fetchone()
        total_count, wt_min_rid = wt_bounds[0], wt_bounds[1]
        stride = max(1, total_count // 120)
        started_at = run["started_at"]
        samples_raw = conn.execute(f"""
            SELECT {_TOTAL} AS total_lbs, recorded_at
            FROM sensor_readings
            WHERE run_id=? AND {_ANY} AND (rowid - ?) % ? = 0
            ORDER BY recorded_at ASC
        """, (run_id, wt_min_rid or 0, stride)).fetchall()

        from datetime import datetime as _dt
        start_dt = _dt.fromisoformat(started_at)
        samples = []
        for s in samples_raw:
            rec_dt = _dt.fromisoformat(s["recorded_at"])
            elapsed = round((rec_dt - start_dt).total_seconds() / 60, 1)
            samples.append({"elapsed_min": elapsed, "weight_lbs": s["total_lbs"]})

        return {
            "initial_lbs": initial_lbs,
            "current_lbs": current_lbs,
            "loss_lbs": loss_lbs,
            "loss_pct": loss_pct,
            "rate_lbs_per_hr": rate_lbs_per_hr,
            "scale_count": scale_count,
            "samples": samples,
        }


def update_run_weight_targets(run_id, target_min, target_max):
    """Set weight loss target band (lbs) on a run."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE runs SET weight_target_min=?, weight_target_max=? WHERE id=?",
            (target_min, target_max, run_id)
        )


def update_run_humidity_targets(run_id, target_min, target_max):
    """Set humidity target band (%RH) on a run."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE runs SET humidity_target_min=?, humidity_target_max=? WHERE id=?",
            (target_min, target_max, run_id)
        )
