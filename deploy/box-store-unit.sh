#!/usr/bin/env bash
# Render the device service with a dependency on the configured block device.
set -euo pipefail
device="${1:?BOX_STORE_DEVICE required}"
template="${2:?unit template required}"
device_unit=''
if [[ "$device" != /var/lib/boxhostd/boxes.img ]]; then
    [[ "$device" == /dev/* ]] || { echo 'BOX_STORE_DEVICE must be a /dev path' >&2; exit 1; }
    device_unit="$(systemd-escape --path --suffix=device "$device")"
fi
while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == '@BOX_STORE_DEVICE_DEPENDENCIES@' ]]; then
        if [[ -n "$device_unit" ]]; then
            printf 'Requires=%s\nAfter=%s\n' "$device_unit" "$device_unit"
        fi
    else
        printf '%s\n' "$line"
    fi
done < "$template"
