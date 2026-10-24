#!/usr/bin/env bash
# Second half of restore_last_good.sh: place container hotfix bytes (creating missing folders), restart, smoke.
set -euo pipefail
healthy() {
  for i in $(seq 1 60); do
    s=$(docker inspect "$1" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}')
    case "$s" in healthy) return 0;; running) sleep 5; return 0;; esac; sleep 3
  done; return 1
}
python3 - <<'EOF'
import json, os, subprocess
plan = json.load(open("/root/restore-plan.json"))
plan.append({"container": "tinyassets-daemon", "path": "tinyassets/auth/workos_provider.py",
             "source": "/root/workos_provider.py.hotfix"})
for e in plan:
    c, rel, src = e["container"], e["path"], e["source"]
    parent = os.path.dirname(rel)
    if subprocess.run(["docker", "exec", c, "test", "-d", f"/app/{parent}"]).returncode:
        # docker cp of a directory creates it; copy the staged folder, then the exact file below.
        subprocess.run(["docker", "cp", os.path.dirname(src), f"{c}:/app/{os.path.dirname(parent)}/"],
                       check=True)
    subprocess.run(["docker", "cp", src, f"{c}:/app/{rel}"], check=True)
print(len(plan), "files placed")
EOF
docker restart tinyassets-broker >/dev/null; healthy tinyassets-broker && echo broker-ok
docker restart tinyassets-daemon >/dev/null; healthy tinyassets-daemon && echo daemon-ok
sleep 5
echo "smoke app=$(curl -s -o /dev/null -w '%{http_code}' https://tinyassets.io/app) pulse=$(curl -s -o /dev/null -w '%{http_code}' https://tinyassets.io/mcp/pulse)"
