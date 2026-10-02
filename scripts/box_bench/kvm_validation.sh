#!/usr/bin/env bash
# target-architecture slice S0: Firecracker + gVisor on a throwaway DigitalOcean droplet.
#
# Runs as root on the droplet that .github/workflows/box-kvm-validation.yml creates
# and destroys. Prints one JSON document on stdout; progress goes to stderr.
# Never run this on the production droplet.
set -euo pipefail

PACK="${1:-10}"
MEM_MIB="${2:-512}"
W=/root/boxbench
FC_VERSION=v1.17.0
GVISOR_RELEASE=release-20260928.0
KERNEL_URL=https://s3.amazonaws.com/spec.ccfc.min/firecracker-ci/v1.15/x86_64/vmlinux-6.1.155
log() { echo "[kvm-validation] $*" >&2; }

mkdir -p "$W"
cd "$W"

log "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >&2
apt-get install -y -qq debootstrap e2fsprogs curl python3 bzip2 ca-certificates >&2

log "host facts"
nested="$(cat /sys/module/kvm_intel/parameters/nested 2>/dev/null \
  || cat /sys/module/kvm_amd/parameters/nested 2>/dev/null || echo unknown)"
host_json=$(python3 - "$nested" <<'PY'
import json, os, platform, sys
cpu = next((l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")), "")
mem = next(int(l.split()[1]) // 1024 for l in open("/proc/meminfo") if l.startswith("MemTotal:"))
print(json.dumps({"kernel": platform.release(), "cpus": os.cpu_count(), "mem_mib": mem,
                  "cpu_model": cpu, "dev_kvm": os.path.exists("/dev/kvm"),
                  "kvm_nested": sys.argv[1],
                  "vmx_or_svm": any(f in open("/proc/cpuinfo").read() for f in (" vmx", " svm"))}))
PY
)
if [ ! -e /dev/kvm ]; then
  echo "{\"host\": ${host_json}, \"error\": \"no /dev/kvm on this droplet\"}"
  exit 0
fi

log "firecracker ${FC_VERSION}"
base="https://github.com/firecracker-microvm/firecracker/releases/download/${FC_VERSION}"
tgz="firecracker-${FC_VERSION}-x86_64.tgz"
curl -fsSL -o "$tgz" "${base}/${tgz}"
curl -fsSL -o "${tgz}.sha256.txt" "${base}/${tgz}.sha256.txt"
want="$(awk '{print $1}' "${tgz}.sha256.txt")"
got="$(sha256sum "$tgz" | awk '{print $1}')"
[ "$want" = "$got" ] || { log "firecracker checksum mismatch"; exit 1; }
tar xzf "$tgz"
install -m755 "release-${FC_VERSION}-x86_64/firecracker-${FC_VERSION}-x86_64" /usr/local/bin/firecracker
curl -fsSL -o vmlinux "$KERNEL_URL"

log "gVisor ${GVISOR_RELEASE}"
gbase="https://github.com/google/gvisor/releases/download/${GVISOR_RELEASE}"
curl -fsSL -o gvisor.tar.bz2 "${gbase}/gvisor-x86_64.tar.bz2"
curl -fsSL -o SHA256SUMS "${gbase}/SHA256SUMS"
want="$(awk '$2 ~ /gvisor-x86_64.tar.bz2$/ {print $1}' SHA256SUMS)"
got="$(sha256sum gvisor.tar.bz2 | awk '{print $1}')"
[ "$want" = "$got" ] || { log "gVisor checksum mismatch"; exit 1; }
mkdir -p gvisor && tar xjf gvisor.tar.bz2 -C gvisor
install -m755 gvisor/runsc /usr/local/bin/runsc
cp -r gvisor/gvisor-bin /usr/local/bin/

log "rootfs (debootstrap, with a python vsock exec agent as PID 1)"
debootstrap --variant=minbase --include=python3-minimal,procps bookworm rootfs \
  http://deb.debian.org/debian >&2
mkdir -p rootfs/cc
cat > rootfs/sbin/box-agent.py <<'PY'
import socket, subprocess
srv = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
srv.bind((socket.VMADDR_CID_ANY, 5000))
srv.listen(64)
while True:
    conn, _ = srv.accept()
    with conn:
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        proc = subprocess.run(["/bin/sh", "-c", data.decode().strip()], cwd="/cc",
                              capture_output=True)
        conn.sendall(f"{proc.returncode}\n".encode() + proc.stdout + proc.stderr)
PY
cat > rootfs/sbin/box-init <<'SH'
#!/bin/sh
mount -t proc proc /proc
mount -t sysfs sys /sys
mount -t tmpfs tmpfs /tmp
exec /usr/bin/python3 /sbin/box-agent.py
SH
chmod 755 rootfs/sbin/box-init
mkfs.ext4 -q -F -d rootfs rootfs.ext4 1024M >&2

log "firecracker measurements (${PACK} boxes at ${MEM_MIB} MiB)"
fc_json="$(python3 /root/fc_bench.py "$W" "$PACK" "$MEM_MIB")"
log "gVisor measurements"
gv_json="$(python3 /root/gv_bench.py "$W" "$PACK" || echo '{"error": "gv_bench failed"}')"

echo "{\"host\": ${host_json}, \"firecracker\": ${fc_json}, \"gvisor\": ${gv_json}}"
