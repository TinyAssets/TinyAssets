#!/usr/bin/env bash
# Restore the broker's outbound-HTTP flag (dropped when the deploy rewrote broker.env), recreate the broker
# on the running image, and re-place its hotfix files.
set -euo pipefail
cp -a /etc/tinyassets/broker.env /etc/tinyassets/broker.env.bak-$(date -u +%Y%m%dT%H%M%SZ)
grep -q '^TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED=' /etc/tinyassets/broker.env || \
  grep '^TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED=' /etc/tinyassets/env >> /etc/tinyassets/broker.env
IMG=$(docker inspect tinyassets-broker --format '{{.Config.Image}}')
TINYASSETS_IMAGE="$IMG" docker compose --env-file /etc/tinyassets/env -f /opt/tinyassets/compose.yml \
  up -d --no-deps --force-recreate broker
python3 - <<'EOF'
import json, os, subprocess
plan = [e for e in json.load(open("/root/restore-plan.json")) if e["container"] == "tinyassets-broker"]
for e in plan:
    c, rel, src = e["container"], e["path"], e["source"]
    parent = os.path.dirname(rel)
    if subprocess.run(["docker", "exec", c, "test", "-d", f"/app/{parent}"]).returncode:
        subprocess.run(["docker", "cp", os.path.dirname(src), f"{c}:/app/{os.path.dirname(parent)}/"], check=True)
    subprocess.run(["docker", "cp", src, f"{c}:/app/{rel}"], check=True)
print(len(plan), "broker files placed")
EOF
docker restart tinyassets-broker >/dev/null
for i in $(seq 1 30); do [ "$(docker inspect tinyassets-broker --format '{{.State.Status}}')" = running ] && break; sleep 2; done
docker exec tinyassets-broker sh -c 'env | grep -c TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED'
docker restart tinyassets-daemon >/dev/null
for i in $(seq 1 40); do s=$(docker inspect tinyassets-daemon --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}'); [ "$s" = healthy ] && break; sleep 3; done
echo "daemon=$s app=$(curl -s -o /dev/null -w '%{http_code}' https://tinyassets.io/app) pulse=$(curl -s -o /dev/null -w '%{http_code}' https://tinyassets.io/mcp/pulse)"
