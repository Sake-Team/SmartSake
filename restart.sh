#!/usr/bin/env bash
# restart.sh — Kill all running server.py instances and restart cleanly.
# Run from the SmartSake directory: ./restart.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="$SCRIPT_DIR/server.py"
LOGFILE="$SCRIPT_DIR/server.log"

echo "[restart] Stopping all server.py instances..."
pkill -f "python.*server\.py" || true
sleep 1

# Confirm nothing is still listening on 8080
if ss -tlnp 2>/dev/null | grep -q ':8080'; then
    echo "[restart] Port 8080 still in use — waiting 2s..."
    sleep 2
fi

echo "[restart] Starting server..."
nohup python "$SERVER" >> "$LOGFILE" 2>&1 &
NEW_PID=$!

sleep 1
if kill -0 "$NEW_PID" 2>/dev/null; then
    echo "[restart] Server started (PID $NEW_PID). Log: $LOGFILE"
else
    echo "[restart] ERROR: server failed to start. Check $LOGFILE"
    exit 1
fi
