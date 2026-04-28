#!/usr/bin/env python3
"""
Daily database backup for SmartSake.
Copies sake.db → backups/sake_YYYYMMDD.db, retains last 7 backups.
Run via systemd timer or manually.
"""
import shutil
import os
from pathlib import Path
from datetime import datetime

BASE_DIR    = Path(__file__).parent
DB_FILE     = BASE_DIR / "sake.db"
BACKUP_DIR  = BASE_DIR / "backups"
KEEP_DAYS   = 7

def run_backup():
    if not DB_FILE.exists():
        print(f"[backup] sake.db not found at {DB_FILE} — skipping")
        return

    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    dest  = BACKUP_DIR / f"sake_{stamp}.db"

    shutil.copy2(DB_FILE, dest)
    print(f"[backup] {DB_FILE.name} → {dest.name}")

    # Prune old backups — keep the most recent KEEP_DAYS
    backups = sorted(BACKUP_DIR.glob("sake_*.db"), key=lambda p: p.stat().st_mtime)
    for old in backups[:-KEEP_DAYS]:
        old.unlink()
        print(f"[backup] pruned {old.name}")

if __name__ == "__main__":
    run_backup()
