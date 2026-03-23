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

            CREATE INDEX IF NOT EXISTS idx_readings_run ON sensor_readings(run_id);
            CREATE INDEX IF NOT EXISTS idx_readings_time ON sensor_readings(recorded_at);
        """)


# ── Runs ──────────────────────────────────────────────────────────────────────

def create_run(name):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO runs (name, started_at, status) VALUES (?, ?, 'active')",
            (name, datetime.now().isoformat())
        )
        return cur.lastrowid


def end_run(run_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE runs SET ended_at=?, status='completed' WHERE id=?",
            (datetime.now().isoformat(), run_id)
        )


def mark_crashed(run_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE runs SET status='crashed' WHERE id=? AND ended_at IS NULL",
            (run_id,)
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
