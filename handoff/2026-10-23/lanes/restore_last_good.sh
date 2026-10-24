#!/usr/bin/env bash
# Restore the last known-good production state after the mixed-bundle rollback (2026-10-23):
# old box host code + old box image (with its in-place hotfixes) + the container hotfix bytes.
# Everything replaced is backed up first under /var/lib/tinyassets-deploy/restore-<stamp>/.
set -euo pipefail
B=/opt/tinyassets/boxhostd
OLD=$B/harness-d57f2894463b099916f812706d479ea49a56bc5a7b491d17e08704bb5bc5ee6a
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BK=/var/lib/tinyassets-deploy/restore-$STAMP
say() { echo "== $(date -u +%T) $*"; }
healthy() {
  for i in $(seq 1 60); do
    s=$(docker inspect "$1" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')
    case "$s" in healthy) return 0;; running) sleep 5; return 0;; esac; sleep 3
  done; return 1
}

say "back up the new host bundle and container files"
mkdir -p "$BK/host" "$BK/containers"
cp -a $B/tinyassets $B/deploy $B/scripts "$BK/host/"
readlink $B/harness-current > "$BK/host/harness-current.target"

say "host bundle -> old (tinyassets from the old box image, deploy/scripts from 2c51a529)"
rm -rf $B/tinyassets.new-restore && cp -a $OLD/app/tinyassets $B/tinyassets.new-restore
chmod -R u+w $B/tinyassets.new-restore
rm -rf $B/tinyassets && mv $B/tinyassets.new-restore $B/tinyassets
rm -rf $B/deploy $B/scripts && cp -a /root/oldbundle/deploy /root/oldbundle/scripts $B/
ln -sfn $(basename $OLD) $B/harness-current.tmp && mv -fT $B/harness-current.tmp $B/harness-current

say "restart boxhostd"
systemctl restart boxhostd; sleep 3; systemctl is-active boxhostd

say "container hotfix bytes"
python3 - <<'EOF'
import json, subprocess
plan = json.load(open("/root/restore-plan.json"))
plan.append({"container": "tinyassets-daemon", "path": "tinyassets/auth/workos_provider.py",
             "source": "/root/workos_provider.py.hotfix"})
for e in plan:
    subprocess.run(["docker", "cp", e["source"], f'{e["container"]}:/app/{e["path"]}'], check=True)
print(len(plan), "files placed")
EOF

say "restart broker then daemon"
docker restart tinyassets-broker >/dev/null; healthy tinyassets-broker && echo broker-ok
docker restart tinyassets-daemon >/dev/null; healthy tinyassets-daemon && echo daemon-ok
sleep 5
echo "smoke app=$(curl -s -o /dev/null -w '%{http_code}' https://tinyassets.io/app) pulse=$(curl -s -o /dev/null -w '%{http_code}' https://tinyassets.io/mcp/pulse)"
echo "backup: $BK"
