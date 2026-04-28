#!/bin/bash
# update.sh — pull latest code and restart service

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo: sudo bash scripts/update.sh"
    exit 1
fi

echo "Pulling latest code..."
cd "$REPO_DIR"

# Run git pull as the repo owner, not root
REPO_USER=$(stat -c '%U' "$REPO_DIR")
sudo -u "$REPO_USER" git pull

echo "Restarting SmartSake..."
systemctl restart smartsake.service
sleep 2

if systemctl is-active --quiet smartsake; then
    echo "SmartSake updated and restarted."
else
    echo "Service failed to restart after update. Check logs:"
    echo "  journalctl -u smartsake -n 30"
    exit 1
fi
