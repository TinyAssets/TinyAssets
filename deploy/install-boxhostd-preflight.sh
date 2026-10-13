#!/usr/bin/env bash
# P2a only: install a pinned gVisor and an idle service. Never touches Docker.
set -euo pipefail

[[ "$(id -u)" -eq 0 ]] || { echo 'root required' >&2; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || { echo 'unsupported host architecture' >&2; exit 1; }
release=release-20260928.0
archive_sha=f3ed9131bc252259df150e270154180188f9df56b73a2312940325e1f522a2d6
runsc_sha=88a87d5d6d06160d4c144f51f95ec6dd6fe0fc6b02697eb877876683c5b57a91
root=/opt/tinyassets/boxhostd
target="${root}/${release}"
source_dir="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$root"
# G4 and the store require XFS userspace tools.
if ! command -v mkfs.xfs >/dev/null || ! command -v xfs_quota >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends xfsprogs >/dev/null
fi
mkdir -p /etc/tinyassets /var/lib/boxhostd/owners
if [[ ! -e /etc/tinyassets/box-store.conf ]]; then
    printf '%s\n' 'BOX_STORE_DEVICE=auto' \
        > /etc/tinyassets/box-store.conf
    chmod 0600 /etc/tinyassets/box-store.conf
fi
archive="$(mktemp /tmp/boxhostd-runsc.XXXXXX)"
stage="$(mktemp -d "${root}/.stage.XXXXXX")"
trap 'rm -f -- "$archive"; [[ -n "${stage:-}" ]] && rm -rf -- "$stage"' EXIT

if [[ -e "$target" && ! -x "${target}/runsc" ]]; then
    echo 'incomplete pinned runtime directory; refusing to overwrite it' >&2
    exit 1
fi
if [[ ! -d "$target" ]]; then
    curl --proto '=https' --tlsv1.2 -fsSL --retry 3 \
        "https://github.com/google/gvisor/releases/download/${release}/gvisor-x86_64.tar.bz2" \
        -o "$archive"
    echo "${archive_sha}  ${archive}" | sha256sum --strict -c -
    tar -xjf "$archive" -C "$stage"
    [[ -x "${stage}/runsc" && -d "${stage}/gvisor-bin" ]] \
        || { echo 'gVisor archive missing runtime files' >&2; exit 1; }
    chmod -R a+rX "$stage"
    mv -- "$stage" "$target"
    stage=''
fi

# Recheck the installed binary on every deploy; the archive digest pins the
# whole release at installation. Refuse an unexpected pre-existing target.
echo "${runsc_sha}  ${target}/runsc" | sha256sum --strict -c -
# Computed from the pinned archive, whose digest matches the release SHA256SUMS.
cat <<EOF | sha256sum --strict -c -
46ff778d9e22c16cd1cb0aed13edd110af5e73533036831f56d9eebd05ec43f5  ${target}/gvisor-bin/checkpointgofer
3315d7ad7c2d3751d349e4976fa02da1da6fd7e41746c35622304e5fabe4fce0  ${target}/gvisor-bin/gvisor-sentry-prewarmer
e10e6e8072a0822f5e80dc203d156f0602b15fab90b85fac1d7bffd79477b267  ${target}/gvisor-bin/gvisor_sentry
03af90d07ea466c10ecc3e3ba839b2a59540959dd3286aea3886917cba850c11  ${target}/gvisor-bin/runsc-fd-parking
a0dde4d5e75d4f8e4287bc2e631b96536e83c80f574fb4ff62c58d472d4f58c5  ${target}/gvisor-bin/runsc-metric-server
EOF
"${target}/runsc" --version | grep -F "$release" >/dev/null
changed=0
for name in boxhostd_idle.py box_preflight.py box_store_durability.py box-store-device.sh box-store-device-stop.sh box-store-check.sh; do
    if ! cmp -s "${source_dir}/${name}" "${root}/${name}"; then
        install -m 0755 "${source_dir}/${name}" "${root}/${name}"
        changed=1
    fi
done
for name in boxhostd.service box-store-device.service var-lib-boxhostd-owners.mount; do
    if ! cmp -s "${source_dir}/${name}" "/etc/systemd/system/${name}"; then
        install -m 0644 "${source_dir}/${name}" "/etc/systemd/system/${name}"
        changed=1
    fi
done
if [[ "$(readlink "${root}/current" 2>/dev/null || true)" != "$release" ]]; then
    ln -s "$release" "${root}/.current.new.$$"
    mv -Tf "${root}/.current.new.$$" "${root}/current"
    changed=1
fi
if [[ "$changed" -eq 1 ]]; then
    systemctl daemon-reload
fi
# An earlier P2a install may already have its empty loop image mounted. Once
# the approved volume appears, switch the mount before starting boxhostd.
volume=/dev/disk/by-id/scsi-0DO_Volume_tinyassets-box-store
if [[ -b "$volume" && "$(readlink /run/boxhostd/box-store-device 2>/dev/null || true)" == /dev/loop* ]]; then
    [[ -z "$(find /var/lib/boxhostd/owners -mindepth 1 -maxdepth 1 -print -quit)" ]] || {
        echo 'existing loop store contains data; refusing automatic switch' >&2; exit 1;
    }
    systemctl stop boxhostd.service var-lib-boxhostd-owners.mount box-store-device.service
fi
systemctl enable --now var-lib-boxhostd-owners.mount
systemctl is-active --quiet var-lib-boxhostd-owners.mount
if [[ "$changed" -eq 1 ]]; then
    systemctl restart boxhostd.service
fi
systemctl enable --now boxhostd.service
systemctl is-active --quiet boxhostd.service
echo "boxhostd idle; runsc ${release} installed"
