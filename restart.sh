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
nohup python3 "$SERVER" >> "$LOGFILE" 2>&1 &
NEW_PID=$!

sleep 2
if kill -0 "$NEW_PID" 2>/dev/null; then
    LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    URL="http://${LOCAL_IP:-localhost}:8080"
    echo "[restart] Server started (PID $NEW_PID). Log: $LOGFILE"
    echo ""
    echo "  Open: $URL"
    echo ""
    DISPLAY=:0 xdg-open "$URL" 2>/dev/null || true
else
    echo "[restart] ERROR: server failed to start. Check $LOGFILE"
    exit 1
fi
