#!/bin/bash
# onboarding.sh — full first-time setup for SmartSake on Raspberry Pi
# Run once as the kojitable user (or any sudo-capable user):
#   bash scripts/onboarding.sh

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
info() { echo -e "${CYAN}→${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
die()  { echo -e "${RED}✗ $*${NC}"; exit 1; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_FILE="$REPO_DIR/systemd/smartsake.service"
INSTALL_DIR="/etc/systemd/system"
CURRENT_USER="${SUDO_USER:-$(whoami)}"

echo ""
echo "=========================================="
echo "  SmartSake — Pi Onboarding"
echo "  Repo: $REPO_DIR"
echo "  User: $CURRENT_USER"
echo "=========================================="
echo ""

# ── Require sudo ──────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    die "Run with sudo: sudo bash scripts/onboarding.sh"
fi

# ── Detect Pi ─────────────────────────────────────────────────────────────────
if [[ ! -f /proc/device-tree/model ]]; then
    warn "Not running on a Raspberry Pi — hardware setup steps will be skipped."
    IS_PI=false
else
    PI_MODEL=$(tr -d '\0' < /proc/device-tree/model)
    ok "Detected: $PI_MODEL"
    IS_PI=true
fi

# ── Find config.txt (Bookworm moved it) ───────────────────────────────────────
if [[ -f /boot/firmware/config.txt ]]; then
    CONFIG_TXT="/boot/firmware/config.txt"
elif [[ -f /boot/config.txt ]]; then
    CONFIG_TXT="/boot/config.txt"
else
    CONFIG_TXT=""
    warn "Could not find config.txt — hardware overlays will not be applied automatically."
fi

# ── 1. System packages ────────────────────────────────────────────────────────
info "Installing system packages..."
apt-get update -q
apt-get install -y -q \
    python3 \
    python3-pip \
    python3-dev \
    python3-smbus \
    i2c-tools \
    git \
    sqlite3
ok "System packages installed."

# ── 2. Python packages ────────────────────────────────────────────────────────
info "Installing Python packages..."

# Detect if we need --break-system-packages (Pi OS Bookworm / Python 3.11+)
PIP_FLAGS=""
if python3 -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
    PIP_FLAGS="--break-system-packages"
    info "Python 3.11+ detected — using --break-system-packages"
fi

pip3 install $PIP_FLAGS \
    flask \
    RPi.GPIO \
    adafruit-blinka \
    adafruit-circuitpython-sht31d

ok "Python packages installed."

# ── 3. Enable I2C (for SHT30 env probe) ──────────────────────────────────────
if $IS_PI; then
    info "Enabling I2C interface..."
    if command -v raspi-config &>/dev/null; then
        raspi-config nonint do_i2c 0
        ok "I2C enabled via raspi-config."
    elif [[ -n "$CONFIG_TXT" ]]; then
        if ! grep -q "^dtparam=i2c_arm=on" "$CONFIG_TXT"; then
            echo "dtparam=i2c_arm=on" >> "$CONFIG_TXT"
            ok "I2C overlay added to $CONFIG_TXT."
        else
            ok "I2C already enabled in $CONFIG_TXT."
        fi
    else
        warn "Could not enable I2C automatically — enable it manually via raspi-config."
    fi
fi

# ── 4. Enable 1-Wire (for MAX31850K thermocouples on GPIO 4) ──────────────────
if $IS_PI; then
    info "Enabling 1-Wire interface (GPIO 4)..."
    if command -v raspi-config &>/dev/null; then
        raspi-config nonint do_onewire 0
        ok "1-Wire enabled via raspi-config."
    elif [[ -n "$CONFIG_TXT" ]]; then
        if ! grep -q "^dtoverlay=w1-gpio" "$CONFIG_TXT"; then
            echo "dtoverlay=w1-gpio" >> "$CONFIG_TXT"
            ok "1-Wire overlay added to $CONFIG_TXT."
        else
            ok "1-Wire already enabled in $CONFIG_TXT."
        fi
    else
        warn "Could not enable 1-Wire automatically — enable it manually via raspi-config."
    fi
fi

# ── 5. Generate and install systemd service ───────────────────────────────────
info "Installing systemd service..."

if [[ ! -f "$SERVICE_FILE" ]]; then
    die "Service template not found at $SERVICE_FILE — is the repo complete?"
fi

# Write a service file with the actual repo path and current user substituted in
cat > "$INSTALL_DIR/smartsake.service" <<EOF
[Unit]
Description=SmartSake — web server + sensor collector
After=local-fs.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$REPO_DIR
ExecStart=/usr/bin/python3 $REPO_DIR/server.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
ExecStartPre=/bin/sleep 3

[Install]
WantedBy=multi-user.target
EOF

ok "Service installed to $INSTALL_DIR/smartsake.service"
ok "  User: $CURRENT_USER"
ok "  WorkingDirectory: $REPO_DIR"

# ── 6. Enable and start service ───────────────────────────────────────────────
info "Enabling and starting SmartSake service..."
systemctl daemon-reload
systemctl enable smartsake.service
systemctl start smartsake.service
sleep 2
ok "Service started."

# ── 7. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "=========================================="
echo -e "  ${GREEN}SmartSake onboarding complete!${NC}"
echo "=========================================="
echo ""
systemctl is-active --quiet smartsake && \
    echo -e "  Service status: ${GREEN}running${NC}" || \
    echo -e "  Service status: ${RED}not running — check logs below${NC}"
echo ""
echo "  Useful commands:"
echo "    sudo bash scripts/start.sh     — start"
echo "    sudo bash scripts/stop.sh      — stop"
echo "    sudo bash scripts/restart.sh   — restart"
echo "    bash scripts/status.sh         — service + sensor status"
echo "    journalctl -u smartsake -f     — live logs"
echo ""

if $IS_PI; then
    echo -e "  ${YELLOW}NOTE: A reboot is required to activate 1-Wire and I2C overlays.${NC}"
    echo -e "  ${YELLOW}      After rebooting, thermocouples and the SHT30 will be detected.${NC}"
    echo ""
    read -rp "  Reboot now? [y/N] " REBOOT_ANSWER
    if [[ "${REBOOT_ANSWER,,}" == "y" ]]; then
        info "Rebooting..."
        reboot
    else
        warn "Remember to reboot before expecting sensor data."
    fi
fi
