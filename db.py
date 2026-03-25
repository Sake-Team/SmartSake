import sqlite3
import os
from datetime import datetime

DB_FILE = os.path.join(os.path.dirname(__file__), "smartsake.db")


def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    with get_conn() as conn:
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
                temp1_target REAL, temp2_target REAL, temp3_target REAL,
                temp4_target REAL, temp5_target REAL, temp6_target REAL
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
                temp1_target    REAL,
                temp2_target    REAL,
                temp3_target    REAL,
                temp4_target    REAL,
                temp5_target    REAL,
                temp6_target    REAL
            );

            CREATE INDEX IF NOT EXISTS idx_readings_run ON sensor_readings(run_id);
            CREATE INDEX IF NOT EXISTS idx_readings_time ON sensor_readings(recorded_at);
            CREATE INDEX IF NOT EXISTS idx_overrides_run ON fan_overrides(run_id);
            CREATE INDEX IF NOT EXISTS idx_rules_run ON fan_rules(run_id, zone);
            CREATE INDEX IF NOT EXISTS idx_deviations_run ON deviation_events(run_id, zone);
            CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id);
            CREATE INDEX IF NOT EXISTS idx_refcurve_points ON reference_curve_points(curve_id);
        """)
        _seed_reference_curves(conn)


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
                (0,   30, 30, 30, 30, 30, 30),
                (360, 31, 31, 31, 31, 31, 31),
                (720, 33, 33, 33, 33, 33, 33),   # kiri-kaeshi
                (960, 36, 36, 36, 36, 36, 36),
                (1200, 38, 38, 38, 38, 38, 38),  # naka-shigoto
                (1440, 40, 40, 40, 40, 40, 40),
                (1680, 41, 41, 41, 41, 41, 41),  # shimai-shigoto
                (2880, 36, 36, 36, 36, 36, 36),  # finish
            ],
        },
        {
            "name": "Mugi Koji (Barley)",
            "description": "44-hour barley koji profile for shochu and miso production",
            "source": "Traditional barley koji guidelines",
            "points": [
                (0,   32, 32, 32, 32, 32, 32),
                (480, 34, 34, 34, 34, 34, 34),
                (720, 36, 36, 36, 36, 36, 36),
                (1080, 40, 40, 40, 40, 40, 40),
                (1440, 42, 42, 42, 42, 42, 42),
                (1800, 40, 40, 40, 40, 40, 40),
                (2280, 35, 35, 35, 35, 35, 35),
                (2640, 33, 33, 33, 33, 33, 33),
            ],
        },
        {
            "name": "Soy Koji (Extended)",
            "description": "60-hour koji profile for soy sauce and miso",
            "source": "Extended fermentation guidelines",
            "points": [
                (0,   30, 30, 30, 30, 30, 30),
                (600, 32, 32, 32, 32, 32, 32),
                (1200, 36, 36, 36, 36, 36, 36),
                (1440, 39, 39, 39, 39, 39, 39),
                (1800, 42, 42, 42, 42, 42, 42),
                (2400, 42, 42, 42, 42, 42, 42),
                (2880, 40, 40, 40, 40, 40, 40),
                (3240, 36, 36, 36, 36, 36, 36),
                (3600, 33, 33, 33, 33, 33, 33),
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
               (curve_id, elapsed_min, temp1_target, temp2_target, temp3_target,
                temp4_target, temp5_target, temp6_target)
               VALUES (?,?,?,?,?,?,?,?)""",
            [(curve_id, p[0], p[1], p[2], p[3], p[4], p[5], p[6]) for p in c["points"]]
        )


# ── Runs ──────────────────────────────────────────────────────────────────────

def create_run(name):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO runs (name, started_at, status) VALUES (?, ?, 'active')",
            (name, datetime.now().isoformat())
        )
        return cur.lastrowid


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
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC"
        ).fetchall()
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


# ── Sensor readings ───────────────────────────────────────────────────────────

def insert_reading(run_id, data):
    """data keys: tc1-tc6, sht_temp, humidity, fan1-fan6, weight_lbs (all optional)"""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO sensor_readings
                (run_id, recorded_at,
                 tc1, tc2, tc3, tc4, tc5, tc6,
                 sht_temp, humidity,
                 fan1, fan2, fan3, fan4, fan5, fan6,
                 weight_lbs)
            VALUES
                (?, ?,
                 ?, ?, ?, ?, ?, ?,
                 ?, ?,
                 ?, ?, ?, ?, ?, ?,
                 ?)
        """, (
            run_id,
            data.get("recorded_at", datetime.now().isoformat()),
            data.get("tc1"), data.get("tc2"), data.get("tc3"),
            data.get("tc4"), data.get("tc5"), data.get("tc6"),
            data.get("sht_temp"), data.get("humidity"),
            data.get("fan1"), data.get("fan2"), data.get("fan3"),
            data.get("fan4"), data.get("fan5"), data.get("fan6"),
            data.get("weight_lbs"),
        ))


def get_latest_reading(run_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sensor_readings WHERE run_id=? ORDER BY recorded_at DESC LIMIT 1",
            (run_id,)
        ).fetchone()
        return dict(row) if row else None


def get_all_readings(run_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sensor_readings WHERE run_id=? ORDER BY recorded_at ASC",
            (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_readings_sampled(run_id, n=300):
    """Return up to n evenly-strided readings for run_id."""
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM sensor_readings WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        if total == 0:
            return []
        stride = max(1, total // n)
        rows = conn.execute("""
            WITH rn AS (
                SELECT *, (ROW_NUMBER() OVER (ORDER BY recorded_at ASC) - 1) AS rn
                FROM sensor_readings WHERE run_id = ?
            )
            SELECT * FROM rn WHERE rn % ? = 0
            ORDER BY recorded_at ASC
        """, (run_id, stride)).fetchall()
        return [dict(r) for r in rows]


# ── Target profiles ───────────────────────────────────────────────────────────

def save_target_profile(run_id, rows):
    """rows: list of dicts with elapsed_min, temp1_target … temp6_target"""
    with get_conn() as conn:
        conn.execute("DELETE FROM target_profiles WHERE run_id=?", (run_id,))
        conn.executemany("""
            INSERT INTO target_profiles
                (run_id, elapsed_min,
                 temp1_target, temp2_target, temp3_target,
                 temp4_target, temp5_target, temp6_target)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (run_id, r["elapsed_min"],
             r.get("temp1_target"), r.get("temp2_target"), r.get("temp3_target"),
             r.get("temp4_target"), r.get("temp5_target"), r.get("temp6_target"))
            for r in rows
        ])


def get_target_profile(run_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM target_profiles WHERE run_id=? ORDER BY elapsed_min ASC",
            (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


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
               'polish_ratio', 'quality_score', 'tasting_notes')
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
                (curve_id, elapsed_min, temp1_target, temp2_target, temp3_target,
                 temp4_target, temp5_target, temp6_target)
            VALUES (?,?,?,?,?,?,?,?)
        """, [(curve_id, p['elapsed_min'],
               p.get('temp1_target'), p.get('temp2_target'), p.get('temp3_target'),
               p.get('temp4_target'), p.get('temp5_target'), p.get('temp6_target'))
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
                (run_id, elapsed_min, temp1_target, temp2_target, temp3_target,
                 temp4_target, temp5_target, temp6_target)
            SELECT ?, elapsed_min, temp1_target, temp2_target, temp3_target,
                   temp4_target, temp5_target, temp6_target
            FROM reference_curve_points WHERE curve_id=?
        """, (run_id, curve_id))


# ── Correlation (Phase 4C) ─────────────────────────────────────────────────────

def get_scored_run_count():
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM run_metadata WHERE quality_score IS NOT NULL"
        ).fetchone()[0]


def get_correlation_data(variable):
    """Return [(run_id, run_name, x_value, y_value)] for the requested variable.

    Only runs with a quality_score are included.
    """
    with get_conn() as conn:
        if variable == 'avg_humidity_stage2':
            # Average humidity during hours 12-24 (elapsed minutes 720-1440)
            rows = conn.execute("""
                SELECT r.id, r.name, m.quality_score,
                       AVG(sr.humidity) AS x_val
                FROM runs r
                JOIN run_metadata m ON m.run_id = r.id
                JOIN sensor_readings sr ON sr.run_id = r.id
                WHERE m.quality_score IS NOT NULL
                  AND sr.humidity IS NOT NULL
                  AND (CAST((julianday(sr.recorded_at) - julianday(r.started_at)) * 1440 AS INTEGER))
                      BETWEEN 720 AND 1440
                GROUP BY r.id
                HAVING AVG(sr.humidity) IS NOT NULL
            """).fetchall()

        elif variable == 'total_weight_loss_pct':
            rows = conn.execute("""
                SELECT r.id, r.name, m.quality_score,
                       ((first_w.weight_lbs - last_w.weight_lbs) / first_w.weight_lbs * 100) AS x_val
                FROM runs r
                JOIN run_metadata m ON m.run_id = r.id
                JOIN (SELECT run_id, weight_lbs FROM sensor_readings
                      WHERE (run_id, recorded_at) IN
                            (SELECT run_id, MIN(recorded_at) FROM sensor_readings GROUP BY run_id)
                        AND weight_lbs IS NOT NULL) first_w ON first_w.run_id = r.id
                JOIN (SELECT run_id, weight_lbs FROM sensor_readings
                      WHERE (run_id, recorded_at) IN
                            (SELECT run_id, MAX(recorded_at) FROM sensor_readings GROUP BY run_id)
                        AND weight_lbs IS NOT NULL) last_w ON last_w.run_id = r.id
                WHERE m.quality_score IS NOT NULL
                  AND first_w.weight_lbs > 0
            """).fetchall()

        elif variable == 'avg_temp_all_zones':
            rows = conn.execute("""
                SELECT r.id, r.name, m.quality_score,
                       AVG((COALESCE(sr.tc1,0) + COALESCE(sr.tc2,0) + COALESCE(sr.tc3,0) +
                            COALESCE(sr.tc4,0) + COALESCE(sr.tc5,0) + COALESCE(sr.tc6,0)) /
                           NULLIF(
                               (sr.tc1 IS NOT NULL) + (sr.tc2 IS NOT NULL) + (sr.tc3 IS NOT NULL) +
                               (sr.tc4 IS NOT NULL) + (sr.tc5 IS NOT NULL) + (sr.tc6 IS NOT NULL), 0
                           )) AS x_val
                FROM runs r
                JOIN run_metadata m ON m.run_id = r.id
                JOIN sensor_readings sr ON sr.run_id = r.id
                WHERE m.quality_score IS NOT NULL
                GROUP BY r.id
                HAVING x_val IS NOT NULL
            """).fetchall()

        elif variable == 'peak_deviation':
            # Max absolute deviation from target profile (uses sampled data)
            # Fetch per-run in Python to allow linear interpolation
            scored = conn.execute("""
                SELECT r.id, r.name, m.quality_score
                FROM runs r JOIN run_metadata m ON m.run_id = r.id
                WHERE m.quality_score IS NOT NULL
            """).fetchall()
            result = []
            for row in scored:
                rid = row['id']
                profile = conn.execute(
                    "SELECT elapsed_min, temp1_target FROM target_profiles WHERE run_id=? ORDER BY elapsed_min",
                    (rid,)
                ).fetchall()
                if not profile:
                    continue
                readings = conn.execute("""
                    WITH rn AS (
                        SELECT recorded_at, tc1, tc2, tc3, tc4, tc5, tc6,
                               (ROW_NUMBER() OVER (ORDER BY recorded_at) - 1) AS rn,
                               COUNT(*) OVER () AS total
                        FROM sensor_readings WHERE run_id=?
                    )
                    SELECT * FROM rn WHERE rn % MAX(1, total/300) = 0
                """, (rid,)).fetchall()
                run_start = conn.execute(
                    "SELECT started_at FROM runs WHERE id=?", (rid,)
                ).fetchone()['started_at']
                from datetime import datetime as _dt
                start_dt = _dt.fromisoformat(run_start)
                profile_pts = [(r['elapsed_min'], r['temp1_target']) for r in profile]
                max_dev = 0.0
                for sr in readings:
                    rec_dt = _dt.fromisoformat(sr['recorded_at'])
                    elapsed = (rec_dt - start_dt).total_seconds() / 60
                    # Linear interpolation
                    target = _interp(profile_pts, elapsed)
                    if target is None:
                        continue
                    for col in ('tc1', 'tc2', 'tc3', 'tc4', 'tc5', 'tc6'):
                        v = sr[col]
                        if v is not None:
                            max_dev = max(max_dev, abs(v - target))
                result.append((rid, row['name'], row['quality_score'], max_dev))
            return result
        else:
            return []

        return [(r['id'], r['name'], r['quality_score'], r['x_val']) for r in rows]


def _interp(profile_pts, elapsed):
    """Linear interpolation of target temp at elapsed_min. Clamps at edges."""
    if not profile_pts:
        return None
    if elapsed <= profile_pts[0][0]:
        return profile_pts[0][1]
    if elapsed >= profile_pts[-1][0]:
        return profile_pts[-1][1]
    for i in range(len(profile_pts) - 1):
        t0, v0 = profile_pts[i]
        t1, v1 = profile_pts[i + 1]
        if t0 <= elapsed <= t1:
            frac = (elapsed - t0) / (t1 - t0)
            return v0 + frac * (v1 - v0)
    return None


def compute_pearson_r(points):
    """Compute Pearson r from [(x, y), ...]. Returns float or None."""
    pts = [(x, y) for x, y in points if x is not None and y is not None]
    n = len(pts)
    if n < 3:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in pts)
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 3)


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
