#!/usr/bin/env bash
# restart.sh — Kill SmartSake server and restart it.
#
# Default behavior: bring up the SmartSake WiFi AP on wlan0 with the static
# IP defined in scripts/ap-config.env (192.168.50.1 by default), then start
# the server. Mobile devices join SSID `SmartSake` and reach the dashboard at
# http://192.168.50.1:8080.
#
# Run from the SmartSake directory: ./restart.sh
# Optional flags:
#   --status     Show running process and exit
#   --logs       Tail server.log and exit
#   --ap         (compatibility) Same as default — explicitly bring AP up
#   --no-ap      Tear down AP, return to home WiFi (sudo), then restart server
#   --skip-ap    Restart server without touching network state at all
#
# AP mode broadcasts the SSID + password defined in scripts/ap-config.env.
# Mobile devices connect to that SSID and reach http://<AP_GATEWAY>:8080.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="$SCRIPT_DIR/server.py"
LOG="$SCRIPT_DIR/server.log"
AP_HELPER="$SCRIPT_DIR/scripts/ap-mode.sh"

# Default action for the AP helper. Overridden by flags.
AP_ACTION="start"

# ── Log rotation (non-systemd path only) ───────────────────────────────────
# Under systemd everything goes to journal which rotates itself. Here we
# guard against a forever-growing server.log on dev / no-systemd boxes:
# rotate when >50 MB, prune backups older than 7 days.
rotate_log() {
    if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || stat -f%z "$LOG" 2>/dev/null || echo 0)" -gt 52428800 ]; then
        mv "$LOG" "$LOG.$(date +%Y%m%d-%H%M%S)"
    fi
    # Prune old rotated logs
    find "$SCRIPT_DIR" -maxdepth 1 -name 'server.log.*' -mtime +7 -delete 2>/dev/null || true
}

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

# Ensure Tailscale is connected. Idempotent: no-op if already up. Best-effort:
# a missing binary or auth failure is warned and does not abort restart.sh.
ensure_tailscale_up() {
    if ! command -v tailscale >/dev/null 2>&1; then
        echo "[restart] tailscale not installed — skipping."
        return 0
    fi
    if tailscale ip -4 >/dev/null 2>&1; then
        echo "[restart] Tailscale already up ($(tailscale ip -4))."
        return 0
    fi
    echo "[restart] Bringing up Tailscale…"
    if [ "$(id -u)" -ne 0 ]; then
        sudo tailscale up || echo "[restart] WARNING: tailscale up failed — continuing."
    else
        tailscale up || echo "[restart] WARNING: tailscale up failed — continuing."
    fi
}

# Run scripts/ap-mode.sh with the requested action. Best-effort: a missing
# helper, no wlan0, missing sudo, or hostapd/dnsmasq failures are warned and
# do not abort restart.sh — the server still comes up so dev boxes work.
run_ap_helper() {
    local action="$1"
    if [ ! -x "$AP_HELPER" ]; then
        echo "[restart] AP helper not found or not executable ($AP_HELPER) — skipping AP $action."
        echo "[restart] Try: chmod +x $AP_HELPER"
        return 0
    fi
    if [ "$(id -u)" -ne 0 ]; then
        if ! sudo "$AP_HELPER" "$action"; then
            echo "[restart] WARNING: ap-mode.sh $action failed — continuing without AP changes."
        fi
    else
        if ! "$AP_HELPER" "$action"; then
            echo "[restart] WARNING: ap-mode.sh $action failed — continuing without AP changes."
        fi
    fi
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
        AP_ACTION="start"
        ;;
    --no-ap|--client)
        AP_ACTION="stop"
        ;;
    --skip-ap|--no-network)
        AP_ACTION="skip"
        ;;
    "")
        # No flag — default to bringing the SmartSake AP up.
        AP_ACTION="start"
        ;;
    *)
        echo "[restart] Unknown flag: $1"
        echo "Usage: $0 [--status|--logs|--ap|--no-ap|--skip-ap]"
        exit 1
        ;;
esac

# ── AP action (default: start, with static IP from ap-config.env) ───────────

case "$AP_ACTION" in
    start)
        echo "[restart] Bringing up SmartSake WiFi AP (static IP from scripts/ap-config.env)…"
        run_ap_helper start
        ;;
    stop)
        echo "[restart] Tearing down AP, restoring home WiFi…"
        run_ap_helper stop
        ;;
    skip)
        echo "[restart] Skipping AP setup (--skip-ap)."
        ;;
esac

# ── Ensure Tailscale is up (for off-site access) ────────────────────────────
ensure_tailscale_up

# ── Fix CRLF line endings on all source files ───────────────────────────────

echo "[restart] Fixing line endings..."
fix_line_endings

# ── Rotate server.log if it has grown unbounded ─────────────────────────────
rotate_log

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
