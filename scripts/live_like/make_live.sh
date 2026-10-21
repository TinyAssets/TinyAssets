#!/usr/bin/env bash
# Start/check/stop a synthetic workload against the production Compose definition.
set -euo pipefail

state=/run/tinyassets-live-like
env_file=/etc/tinyassets/env
compose=/opt/tinyassets/compose.yml
volume=tinyassets-data
container=tinyassets-daemon

probe_mcp() {
  # Anonymous MCP must answer with the application's auth refusal, not a proxy
  # or an open unauthenticated endpoint.
  test "$(curl --silent --max-time 2 --output /dev/null --write-out '%{http_code}' \
      http://127.0.0.1:8001/mcp)" = 401
}

volume_root() {
  docker volume inspect "$volume" --format '{{ .Mountpoint }}'
}

case "${1:-}" in
  start)
    test "$(id -u)" -eq 0
    image="${2:?usage: make_live.sh start <image>}"
    test -f "$compose"
    root="$(volume_root)"
    test -f "$root/.layout.json"
    if test ! -f "$root/release-state.json"; then
      docker pull -q "$image" >/dev/null
      revision="$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")"
      [[ "$revision" =~ ^[0-9a-f]{40}$ ]]
      python3 - "$root/release-state.json" "$revision" "$image" <<'PY'
import datetime, json, pathlib, sys
path, revision, image = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
path.write_text(json.dumps({
    "git_sha": revision, "image_tag": revision[:12], "image_ref": image,
    "deployed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}) + "\n", encoding="utf-8")
PY
      chown 1001:1100 "$root/release-state.json"
      chmod 0664 "$root/release-state.json"
    fi
    install -d -m 0755 "$state" /etc/tinyassets /run/tinyassets-boxhostd/broker
    if test ! -f "$env_file"; then
      printf 'TINYASSETS_IMAGE=%s\nTINYASSETS_MCP_CANARY_URL=http://127.0.0.1:8001/mcp\nUNIVERSE_SERVER_DEV_USER=alice\nBACKUP_DEST=spaces:tinyassets-live-like/workflow-backups\n' "$image" > "$env_file"
    else
      python3 - "$env_file" "$image" <<'PY'
import pathlib, sys
path, image = pathlib.Path(sys.argv[1]), sys.argv[2]
lines = [line for line in path.read_text().splitlines() if not line.startswith('TINYASSETS_IMAGE=')]
path.write_text('\n'.join(lines + [f'TINYASSETS_IMAGE={image}']) + '\n')
PY
    fi
    for name in agent-interchange.env request-idempotency.env app-ingress.env broker.env; do
      test -f "/etc/tinyassets/$name" || install -m 0640 /dev/null "/etc/tinyassets/$name"
    done
    test -f /etc/tinyassets/broker-rclone.conf || install -m 0644 /dev/null /etc/tinyassets/broker-rclone.conf
    chown root:tinyassets "$env_file" /etc/tinyassets/{agent-interchange.env,request-idempotency.env,app-ingress.env}
    chmod 0640 "$env_file" /etc/tinyassets/{agent-interchange.env,request-idempotency.env,app-ingress.env}
    # Use the installed production renderer so platform secrets never reach the daemon.
    command -v tinyassets-env >/dev/null
    printf 'alice' | tinyassets-env set UNIVERSE_SERVER_DEV_USER
    tinyassets-env render-daemon-env
    # These are the actual production service definitions and Compose flags.
    # Through the daemon unit, as the droplet runs the stack: its start jobs
    # serialize with the watchdogs', and restart applies the env just rendered.
    # cloudflared starts but cannot reach the edge with synthetic credentials;
    # this gate proves the loopback workload, not Cloudflare edge connectivity.
    systemctl restart tinyassets-daemon.service
    for _ in $(seq 1 90); do
      if probe_mcp; then
        break
      fi
      if test "$(docker inspect -f '{{.RestartCount}}' "$container")" -ge 2; then
        docker logs "$container" --tail 30 >&2
        echo 'daemon restarted repeatedly before it served the fixture' >&2
        exit 1
      fi
      sleep 2
    done
    probe_mcp
    # A held engine-style socket under the runtime directory reproduces the
    # special inode that stopped the first C6 backup snapshot.
    socket="$root/.universe-sidecars/a1/engine.sock"
    cat > "$state/sidecar.py" <<'PY'
import pathlib, signal, socket, sys, time
path = pathlib.Path(sys.argv[1])
path.unlink(missing_ok=True)
server = socket.socket(socket.AF_UNIX)
server.bind(str(path))
server.listen(4)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True:
    time.sleep(1)
PY
    systemd-run --unit=tinyassets-live-like-sidecar --property=Restart=on-failure \
      /usr/bin/python3 "$state/sidecar.py" "$socket"
    docker exec -d "$container" /opt/venv/bin/python -B -c '
import sqlite3, time
db = sqlite3.connect("/data/.tinyassets.db", timeout=10)
while True:
    db.execute("UPDATE agent_turns SET turn_id=turn_id WHERE universe_id=?", ("a1",))
    db.commit()
    time.sleep(0.1)
'
    systemctl enable --now tinyassets-backup.timer
    bash "$0" check
    ;;
  check)
    root="$(volume_root)"
    test "$(docker inspect -f '{{.State.Running}}' "$container")" = true
    if docker container inspect tinyassets-broker >/dev/null 2>&1; then
      test "$(docker inspect -f '{{.State.Running}}' tinyassets-broker)" = true
    fi
    probe_mcp
    test -S "$root/.universe-sidecars/a1/engine.sock"
    systemctl is-active --quiet tinyassets-live-like-sidecar.service
    systemctl is-active --quiet tinyassets-backup.timer
    systemctl is-enabled --quiet tinyassets-backup.timer
    # A live writer must advance the database's WAL or main file.
    first="$(stat -c '%Y:%s' "$root/.tinyassets.db-wal" 2>/dev/null || true)"
    sleep 2
    second="$(stat -c '%Y:%s' "$root/.tinyassets.db-wal" 2>/dev/null || true)"
    test -n "$first" && test "$first" != "$second"
    echo 'live_like=ok sockets=present writes=active backup_timer=armed'
    ;;
  stop)
    test "$(id -u)" -eq 0
    systemctl stop tinyassets-live-like-sidecar.service 2>/dev/null || true
    rm -rf "$state"
    docker compose --env-file "$env_file" -f "$compose" stop daemon logs 2>/dev/null || true
    docker stop tinyassets-broker >/dev/null 2>&1 || true
    systemctl disable --now tinyassets-backup.timer 2>/dev/null || true
    ;;
  *) echo 'usage: make_live.sh start <image>|check|stop' >&2; exit 2 ;;
esac
