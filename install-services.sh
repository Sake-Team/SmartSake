#!/usr/bin/env bash
# install-services.sh
# Installs SmartSake systemd services and enables them to start on boot.
# Run once on the Pi: sudo bash install-services.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

echo "==> Installing SmartSake services from $SCRIPT_DIR"

# Copy unit files
cp "$SCRIPT_DIR/smartsake-server.service"  "$SYSTEMD_DIR/"
cp "$SCRIPT_DIR/smartsake-sensors.service" "$SYSTEMD_DIR/"

# Reload systemd, enable, and start both services
systemctl daemon-reload

systemctl enable smartsake-server.service
systemctl enable smartsake-sensors.service

systemctl restart smartsake-server.service
systemctl restart smartsake-sensors.service

echo ""
echo "==> Done! Services are running and will start automatically on boot."
echo ""
echo "    Check status:"
echo "      sudo systemctl status smartsake-server"
echo "      sudo systemctl status smartsake-sensors"
echo ""
echo "    View live logs:"
echo "      sudo journalctl -u smartsake-server -f"
echo "      sudo journalctl -u smartsake-sensors -f"
echo ""
echo "    Stop services:"
echo "      sudo systemctl stop smartsake-server smartsake-sensors"
