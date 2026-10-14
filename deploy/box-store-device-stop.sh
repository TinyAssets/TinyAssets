#!/usr/bin/env bash
set -euo pipefail
device="$(readlink /run/boxhostd/box-store-device 2>/dev/null || true)"
rm -f /run/boxhostd/box-store-device
if [[ "$device" == /dev/loop* ]]; then
    losetup -d "$device"
fi
