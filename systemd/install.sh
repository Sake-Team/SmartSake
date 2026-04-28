#!/usr/bin/env bash
# Run as root: sudo bash systemd/install.sh
set -e
SYSTEMD=/etc/systemd/system
REPO=$(dirname "$(realpath "$0")")/..

cp "$REPO/systemd/smartsake-sensors.service" $SYSTEMD/
cp "$REPO/systemd/smartsake-server.service"  $SYSTEMD/
cp "$REPO/systemd/smartsake-backup.service"  $SYSTEMD/
cp "$REPO/systemd/smartsake-backup.timer"    $SYSTEMD/

systemctl daemon-reload
systemctl enable --now smartsake-sensors.service
systemctl enable --now smartsake-server.service
systemctl enable --now smartsake-backup.timer

echo "SmartSake services installed and started."
echo "Check status: systemctl status smartsake-sensors smartsake-server"
