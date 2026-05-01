#!/usr/bin/env python3
"""
Database backup and space-based auto-pruning for SmartSake.
Uses SQLite online backup API to safely copy a WAL-mode database.
Copies smartsake.db -> backups/smartsake_YYYYMMDD_HHMM.db, retains last 28 backups.
After backup, checks disk space and prunes oldest unlocked runs if below threshold.
Run via systemd timer (every 6 hours) or manually.
"""
import sqlite3
import os
import sys
from pathlib import Path
from datetime import datetime

BASE_DIR       = Path(__file__).parent
DB_FILE        = BASE_DIR / "smartsake.db"
BACKUP_DIR     = BASE_DIR / "backups"
KEEP_BACKUPS   = 28           # ~7 days at 4x/day
MIN_FREE_MB    = 500          # start pruning when disk drops below this

def run_backup():
    if not DB_FILE.exists():
        print(f"[backup] smartsake.db not found at {DB_FILE} — skipping")
        return

    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
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

    # Prune old backup files — keep the most recent KEEP_BACKUPS
    backups = sorted(BACKUP_DIR.glob("smartsake_*.db"), key=lambda p: p.stat().st_mtime)
    for old in backups[:-KEEP_BACKUPS]:
        old.unlink()
        print(f"[backup] pruned backup {old.name}")


def run_prune():
    """Auto-prune oldest unlocked runs when disk space is low."""
    if not DB_FILE.exists():
        return
    import shutil
    free_mb = shutil.disk_usage(str(BASE_DIR)).free / (1024 * 1024)
    print(f"[prune] disk free: {free_mb:.0f} MB (threshold: {MIN_FREE_MB} MB)")
    if free_mb >= MIN_FREE_MB:
        print(f"[prune] space OK, no pruning needed")
        return
    # Import db module from same directory
    sys.path.insert(0, str(BASE_DIR))
    import db as sakedb
    sakedb.init_db()
    count = sakedb.prune_for_space(MIN_FREE_MB)
    if count:
        new_free = shutil.disk_usage(str(BASE_DIR)).free / (1024 * 1024)
        print(f"[prune] deleted {count} unlocked run(s), freed to {new_free:.0f} MB")
    else:
        print(f"[prune] no unlocked runs to delete — lock some runs or free space manually")


if __name__ == "__main__":
    run_backup()
    run_prune()
