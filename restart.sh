#!/usr/bin/env bash
# restart.sh — Canonical way to restart SmartSake.
#
# Uses systemd if the service is installed, falls back to direct python
# for development or non-systemd environments.
#
# Run from the SmartSake directory: ./restart.sh
# Optional flags:
#   --status   Show service status and exit
#   --logs     Tail the journal and exit

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="smartsake"
SERVER="$SCRIPT_DIR/server.py"

# ── Helpers ──────────────────────────────────────────────────────────────────

has_systemd() {
    systemctl --version &>/dev/null 2>&1 && \
    systemctl is-enabled "$SERVICE_NAME" &>/dev/null 2>&1
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
        if has_systemd; then
            systemctl status "$SERVICE_NAME" --no-pager
        else
            echo "[restart] systemd service not installed — checking for running process"
            pgrep -af "python.*server\.py" || echo "  (not running)"
        fi
        exit 0
        ;;
    --logs)
        if has_systemd; then
            journalctl -u "$SERVICE_NAME" -f --no-pager -n 50
        else
            echo "[restart] systemd not available — tailing server.log"
            tail -f "$SCRIPT_DIR/server.log" 2>/dev/null || echo "  (no log file)"
        fi
        exit 0
        ;;
esac

# ── Restart ──────────────────────────────────────────────────────────────────

if has_systemd; then
    echo "[restart] Restarting via systemd..."
    sudo systemctl restart "$SERVICE_NAME"
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "[restart] Service running."
        show_url
    else
        echo "[restart] ERROR: service failed to start."
        journalctl -u "$SERVICE_NAME" --no-pager -n 15
        exit 1
    fi
else
    # Fallback for dev/non-systemd: direct python
    echo "[restart] No systemd service — using direct mode."
    echo "[restart] Stopping existing instances..."
    pkill -f "python.*server\.py" 2>/dev/null || true
    sleep 1

    # Wait for port to clear
    if ss -tlnp 2>/dev/null | grep -q ':8080'; then
        echo "[restart] Port 8080 still in use — waiting..."
        sleep 2
    fi

    echo "[restart] Starting server..."
    nohup python3 "$SERVER" >> "$SCRIPT_DIR/server.log" 2>&1 &
    NEW_PID=$!
    sleep 2

    if kill -0 "$NEW_PID" 2>/dev/null; then
        echo "[restart] Server started (PID $NEW_PID). Log: $SCRIPT_DIR/server.log"
        show_url
        # Auto-open on Pi with display
        DISPLAY=:0 xdg-open "http://$(hostname -I 2>/dev/null | awk '{print $1}'):8080" 2>/dev/null || true
    else
        echo "[restart] ERROR: server failed to start. Check server.log"
        exit 1
    fi
fi
