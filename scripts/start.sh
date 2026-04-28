#!/bin/bash
# start.sh — start the SmartSake service

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo: sudo bash scripts/start.sh"
    exit 1
fi

if ! systemctl is-enabled --quiet smartsake 2>/dev/null; then
    echo "SmartSake service is not installed. Run onboarding first:"
    echo "  sudo bash scripts/onboarding.sh"
    exit 1
fi

systemctl start smartsake.service
sleep 1

if systemctl is-active --quiet smartsake; then
    echo "SmartSake started."
else
    echo "SmartSake failed to start. Check logs:"
    echo "  journalctl -u smartsake -n 30"
    exit 1
fi
