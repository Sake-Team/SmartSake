#!/usr/bin/env python3
"""
Daily database backup for SmartSake.
Uses SQLite online backup API to safely copy a WAL-mode database.
Copies smartsake.db → backups/smartsake_YYYYMMDD.db, retains last 7 backups.
Run via systemd timer or manually.
"""
import sqlite3
import os
from pathlib import Path
from datetime import datetime

BASE_DIR    = Path(__file__).parent
DB_FILE     = BASE_DIR / "smartsake.db"
BACKUP_DIR  = BASE_DIR / "backups"
KEEP_DAYS   = 7

def run_backup():
    if not DB_FILE.exists():
        print(f"[backup] smartsake.db not found at {DB_FILE} — skipping")
        return

    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    dest  = BACKUP_DIR / f"smartsake_{stamp}.db"

    # Use SQLite online backup API — safe for WAL-mode databases.
    # shutil.copy2 would skip the -wal file, producing corrupt/stale backups.
    src_conn = sqlite3.connect(str(DB_FILE))
    dst_conn = sqlite3.connect(str(dest))
    try:
        src_conn.backup(dst_conn)
        print(f"[backup] {DB_FILE.name} → {dest.name}")
    finally:
        dst_conn.close()
        src_conn.close()

    # Prune old backups — keep the most recent KEEP_DAYS
    backups = sorted(BACKUP_DIR.glob("smartsake_*.db"), key=lambda p: p.stat().st_mtime)
    for old in backups[:-KEEP_DAYS]:
        old.unlink()
        print(f"[backup] pruned {old.name}")

if __name__ == "__main__":
    run_backup()
