#!/usr/bin/env bash
# Prepare the device named by the stable box-store mount unit.
box_store_require_block() {
    [[ "$1" == /dev/* && -b "$1" ]] || {
        echo 'configured BOX_STORE_DEVICE is missing or not a block device' >&2
        return 1
    }
}
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    return 0
fi
set -euo pipefail

config=/etc/tinyassets/box-store.conf
[[ -f "$config" && ! -L "$config" && "$(stat -c %u "$config")" == 0 ]] || {
    echo 'root-owned box-store.conf required' >&2; exit 1;
}
mode="$(stat -c %a "$config")"
(( (8#$mode & 8#22) == 0 )) || { echo 'box-store.conf is writable by non-root' >&2; exit 1; }
# The configuration is root-owned and contains only BOX_STORE_DEVICE.
source "$config"
device="${BOX_STORE_DEVICE:?BOX_STORE_DEVICE must be pinned in box-store.conf}"
[[ "$device" != auto ]] || { echo 'BOX_STORE_DEVICE=auto is unsafe; rerun the installer' >&2; exit 1; }
mkdir -p /run/boxhostd /var/lib/boxhostd/owners
if [[ "$device" == /var/lib/boxhostd/boxes.img ]]; then
    if [[ ! -e "$device" ]]; then
        free="$(df -B1 --output=avail /var/lib/boxhostd | tail -1)"
        size=$((8 * 1024 * 1024 * 1024))
        reserve=$((6 * 1024 * 1024 * 1024))
        (( free >= size + reserve )) || { echo 'box image would breach 6 GiB root reserve' >&2; exit 1; }
        stage="${device}.new.$$"
        trap 'rm -f -- "${stage:-}"' EXIT
        fallocate -l "$size" "$stage"
        chmod 0600 "$stage"
        mkfs.xfs -f "$stage"
        mv -n -- "$stage" "$device"
        [[ ! -e "$stage" ]] || { echo 'box image appeared during installation' >&2; exit 1; }
        trap - EXIT
    fi
    [[ -f "$device" && ! -L "$device" && "$(stat -c %s "$device")" == 8589934592 ]] || {
        echo 'existing box image has unexpected shape' >&2; exit 1;
    }
    # Never format an existing image. The block count proves it is still allocated.
    [[ $(( $(stat -c %b "$device") * 512 )) -ge 8589934592 ]] || {
        echo 'box image is sparse' >&2; exit 1;
    }
    loop="$(losetup -j "$device" -n -O NAME | head -1)"
    if [[ -z "$loop" ]]; then
        loop="$(losetup --find --show --direct-io=on "$device")"
    fi
    losetup --direct-io=on "$loop"
    echo 0 > "/sys/block/${loop##*/}/queue/discard_max_bytes"
    device="$loop"
else
    box_store_require_block "$device" || exit 1
    # A newly attached raw volume is formatted once. Any existing filesystem,
    # including an unexpected type, is retained and rejected below.
    if ! blkid -p -s TYPE -o value "$device" >/dev/null 2>&1; then
        signatures="$(wipefs -n --noheadings --output TYPE "$device")" || {
            echo 'could not inspect box volume signatures' >&2; exit 1;
        }
        [[ -z "${signatures//[[:space:]]/}" ]] || {
            echo 'box volume has an existing signature; refusing to format it' >&2; exit 1;
        }
        mkfs.xfs "$device"
    fi
fi
[[ "$(blkid -s TYPE -o value "$device")" == xfs ]] || {
    echo 'box store must already be XFS; refusing to format it' >&2; exit 1;
}
ln -sfn "$device" /run/boxhostd/box-store-device
