"""
Historian module for Raspberry Pi sake fermentation monitoring.
Provides SQLite persistence for fermentation runs and sensor readings,
with hourly aggregation views and end-of-run CSV report generation.
"""

import sqlite3
import threading
import csv
import datetime
from typing import Optional


class Historian:
    def __init__(self, db_path: str = 'sake_history.db'):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        schema = """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            ts TEXT NOT NULL,
            sht_temp_c REAL,
            sht_humidity_rh REAL,
            tc1 REAL, tc2 REAL, tc3 REAL, tc4 REAL, tc5 REAL, tc6 REAL,
            weight_kg REAL,
            z1_setpoint REAL, z1_relay INTEGER, z1_pid_out REAL, z1_alarm TEXT,
            z2_setpoint REAL, z2_relay INTEGER, z2_pid_out REAL, z2_alarm TEXT,
            z3_setpoint REAL, z3_relay INTEGER, z3_pid_out REAL, z3_alarm TEXT,
            z4_setpoint REAL, z4_relay INTEGER, z4_pid_out REAL, z4_alarm TEXT,
            z5_setpoint REAL, z5_relay INTEGER, z5_pid_out REAL, z5_alarm TEXT,
            z6_setpoint REAL, z6_relay INTEGER, z6_pid_out REAL, z6_alarm TEXT,
            FOREIGN KEY(run_id) REFERENCES runs(id)
        );

        CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);
        CREATE INDEX IF NOT EXISTS idx_readings_run_id ON readings(run_id);

        DROP VIEW IF EXISTS hourly_stats;
        CREATE VIEW hourly_stats AS
        SELECT
            run_id,
            strftime('%Y-%m-%d %H:00:00', ts) AS hour,
            AVG(tc1) as avg_tc1, MIN(tc1) as min_tc1, MAX(tc1) as max_tc1,
            AVG(tc2) as avg_tc2, MIN(tc2) as min_tc2, MAX(tc2) as max_tc2,
            AVG(tc3) as avg_tc3, MIN(tc3) as min_tc3, MAX(tc3) as max_tc3,
            AVG(tc4) as avg_tc4, MIN(tc4) as min_tc4, MAX(tc4) as max_tc4,
            AVG(tc5) as avg_tc5, MIN(tc5) as min_tc5, MAX(tc5) as max_tc5,
            AVG(tc6) as avg_tc6, MIN(tc6) as min_tc6, MAX(tc6) as max_tc6,
            AVG(sht_temp_c) as avg_sht_temp,
            AVG(sht_humidity_rh) as avg_sht_humidity,
            AVG(weight_kg) as avg_weight
        FROM readings
        GROUP BY run_id, hour;
        """
        with self.lock:
            self.conn.executescript(schema)
            self.conn.commit()

    def start_run(self, name: str) -> int:
        ts = datetime.datetime.now().isoformat()
        with self.lock:
            with self.conn:
                cursor = self.conn.cursor()
                cursor.execute("INSERT INTO runs (name, started_at) VALUES (?, ?)", (name, ts))
                return cursor.lastrowid

    def end_run(self, run_id: int, notes: str = None):
        ts = datetime.datetime.now().isoformat()
        with self.lock:
            with self.conn:
                self.conn.execute(
                    "UPDATE runs SET ended_at = ?, notes = ? WHERE id = ?",
                    (ts, notes, run_id)
                )
        self._write_report(run_id)  # outside lock — report may take time

    def log_reading(self, run_id, ts, sht_temp, sht_humidity, tc_readings, weight_kg, zone_states):
        try:
            tcs = [None] * 6
            for ch, temp in tc_readings:
                if 1 <= ch <= 6:
                    tcs[ch - 1] = temp

            z_data = []
            for i in range(1, 7):
                state = zone_states.get(i, {})
                relay = state.get('relay_state')
                z_data.extend([
                    state.get('setpoint_c'),
                    1 if relay is True else (0 if relay is False else None),
                    state.get('pid_output'),
                    state.get('alarm_level'),
                ])

            query = """
            INSERT INTO readings (
                run_id, ts, sht_temp_c, sht_humidity_rh,
                tc1, tc2, tc3, tc4, tc5, tc6, weight_kg,
                z1_setpoint, z1_relay, z1_pid_out, z1_alarm,
                z2_setpoint, z2_relay, z2_pid_out, z2_alarm,
                z3_setpoint, z3_relay, z3_pid_out, z3_alarm,
                z4_setpoint, z4_relay, z4_pid_out, z4_alarm,
                z5_setpoint, z5_relay, z5_pid_out, z5_alarm,
                z6_setpoint, z6_relay, z6_pid_out, z6_alarm
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """
            params = [run_id, ts, sht_temp, sht_humidity] + tcs + [weight_kg] + z_data
            with self.lock:
                with self.conn:
                    self.conn.execute(query, params)
        except Exception as e:
            print(f"[Historian] log_reading error: {e}")

    def export_run_csv(self, run_id: int, path: str) -> str:
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM readings WHERE run_id = ? ORDER BY ts", (run_id,))
            rows = cursor.fetchall()
            colnames = [d[0] for d in cursor.description]

        with open(path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(colnames)
            writer.writerows(rows)
        return path

    def _write_report(self, run_id: int):
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT name FROM runs WHERE id = ?", (run_id,))
            row = cursor.fetchone()
        if not row:
            return

        # Sanitize name for use in filename
        safe_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in row[0])
        date_str = datetime.datetime.now().strftime('%Y-%m-%d')
        path = f"run_{run_id}_{safe_name}_{date_str}.csv"

        self.export_run_csv(run_id, path)

        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM hourly_stats WHERE run_id = ? ORDER BY hour", (run_id,))
            stats = cursor.fetchall()
            stat_cols = [d[0] for d in cursor.description]

        if stats:
            with open(path, 'a', newline='') as f:
                f.write('\n\nHOURLY SUMMARY\n')
                writer = csv.writer(f)
                writer.writerow(stat_cols)
                writer.writerows(stats)

        print(f"[Historian] Run {run_id} report written to {path}")

    def get_active_run(self) -> Optional[int]:
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT id FROM runs WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            return row[0] if row else None
