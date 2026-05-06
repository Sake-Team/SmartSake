#!/usr/bin/env bash
# restart.sh — Kill SmartSake server and restart it.
#
# Run from the SmartSake directory: ./restart.sh
# Optional flags:
#   --status   Show running process and exit
#   --logs     Tail server.log and exit
#   --ap       Bring up Pi WiFi access-point first (sudo), then restart server
#   --no-ap    Tear down AP, return to home WiFi (sudo), then restart server
#
# AP mode broadcasts the SSID + password defined in scripts/ap-config.env.
# Mobile devices connect to that SSID and reach http://<gateway-ip>:8080.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="$SCRIPT_DIR/server.py"
LOG="$SCRIPT_DIR/server.log"
AP_HELPER="$SCRIPT_DIR/scripts/ap-mode.sh"

# ── Fix line endings (in case pulled from Windows) ──────────────────────────
fix_line_endings() {
    find "$SCRIPT_DIR" -maxdepth 1 \( -name "*.sh" -o -name "*.py" -o -name "*.html" \) \
        -exec sed -i 's/\r$//' {} +
}

show_url() {
    local ip
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    echo ""
    echo "  Open: http://${ip:-localhost}:8080"
    echo ""
}

# ── Flag handling ────────────────────────────────────────────────────────────

case "${1:-}" in
    --status)
        pgrep -af "python.*server\.py" || echo "  (not running)"
        exit 0
        ;;
    --logs)
        tail -f "$LOG" 2>/dev/null || echo "  (no log file)"
        exit 0
        ;;
    --ap)
        if [ ! -x "$AP_HELPER" ]; then
            echo "[restart] AP helper not found or not executable: $AP_HELPER"
            echo "[restart] Try: chmod +x $AP_HELPER"
            exit 1
        fi
        echo "[restart] Bringing up SmartSake WiFi AP…"
        if [ "$(id -u)" -ne 0 ]; then
            sudo "$AP_HELPER" start
        else
            "$AP_HELPER" start
        fi
        ;;
    --no-ap)
        if [ ! -x "$AP_HELPER" ]; then
            echo "[restart] AP helper not found: $AP_HELPER"
            exit 1
        fi
        echo "[restart] Tearing down AP, restoring home WiFi…"
        if [ "$(id -u)" -ne 0 ]; then
            sudo "$AP_HELPER" stop
        else
            "$AP_HELPER" stop
        fi
        ;;
esac

# ── Fix CRLF line endings on all source files ───────────────────────────────

echo "[restart] Fixing line endings..."
fix_line_endings

# ── Kill existing server ────────────────────────────────────────────────────

echo "[restart] Stopping existing server..."
pkill -f "python.*server\.py" 2>/dev/null || true
sleep 1

# Wait for port to clear
if ss -tlnp 2>/dev/null | grep -q ':8080'; then
    echo "[restart] Port 8080 still in use — waiting..."
    sleep 2
fi

# ── Start server ────────────────────────────────────────────────────────────

echo "[restart] Starting server..."
nohup python3 "$SERVER" >> "$LOG" 2>&1 &
NEW_PID=$!
sleep 2

if kill -0 "$NEW_PID" 2>/dev/null; then
    echo "[restart] Server started (PID $NEW_PID). Log: $LOG"
    show_url
else
    echo "[restart] ERROR: server failed to start. Check server.log"
    tail -20 "$LOG"
    exit 1
fi
