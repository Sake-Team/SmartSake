#!/bin/bash
# stop.sh — stop the SmartSake service

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo: sudo bash scripts/stop.sh"
    exit 1
fi

if ! systemctl is-active --quiet smartsake 2>/dev/null; then
    echo "SmartSake is not running."
    exit 0
fi

systemctl stop smartsake.service
echo "SmartSake stopped."
