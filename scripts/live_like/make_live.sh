#!/usr/bin/env bash
# Start/check/stop a synthetic workload against the production Compose definition.
set -euo pipefail

state=/run/tinyassets-live-like
env_file=/etc/tinyassets/env
compose=/opt/tinyassets/compose.yml
volume=tinyassets-data
container=tinyassets-daemon

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
    for name in daemon.env agent-interchange.env request-idempotency.env app-ingress.env broker.env; do
      test -f "/etc/tinyassets/$name" || install -m 0640 /dev/null "/etc/tinyassets/$name"
    done
    test -f /etc/tinyassets/broker-rclone.conf || install -m 0644 /dev/null /etc/tinyassets/broker-rclone.conf
    # Keep the daemon environment in step with the generated host source.
    cp "$env_file" /etc/tinyassets/daemon.env
    chown root:tinyassets /etc/tinyassets/{env,daemon.env,agent-interchange.env,request-idempotency.env,app-ingress.env}
    chmod 0640 /etc/tinyassets/{env,daemon.env,agent-interchange.env,request-idempotency.env,app-ingress.env}
    # These are the actual production service definitions and Compose flags.
    # The external Cloudflare tunnel has no PR credentials; gate probes loopback.
    docker compose --env-file "$env_file" -f "$compose" up -d --timeout 20 broker daemon logs
    for _ in $(seq 1 90); do
      if curl --fail --silent --max-time 2 http://127.0.0.1:8001/mcp >/dev/null; then
        break
      fi
      sleep 2
    done
    curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8001/mcp >/dev/null
    # A held engine-style socket under the runtime directory reproduces the
    # special inode that stopped the first C6 backup snapshot.
    socket="$root/.universe-sidecars/a1/engine.sock"
    python3 - "$socket" <<'PY' > "$state/sidecar.log" 2>&1 &
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
    echo "$!" > "$state/sidecar.pid"
    docker exec -d "$container" /opt/venv/bin/python -B -c '
import sqlite3, time
db = sqlite3.connect("/data/.tinyassets.db", timeout=10)
while True:
    db.execute("UPDATE agent_turns SET turn_id=turn_id WHERE universe_id=?", ("a1",))
    db.commit()
    time.sleep(0.1)
'
    systemctl enable --now tinyassets-backup.timer
    "$0" check
    ;;
  check)
    root="$(volume_root)"
    test "$(docker inspect -f '{{.State.Running}}' "$container")" = true
    test "$(docker inspect -f '{{.State.Running}}' tinyassets-broker)" = true
    curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8001/mcp >/dev/null
    test -S "$root/.universe-sidecars/a1/engine.sock"
    test -f "$state/sidecar.pid"
    kill -0 "$(cat "$state/sidecar.pid")"
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
    if test -f "$state/sidecar.pid"; then
      kill "$(cat "$state/sidecar.pid")" 2>/dev/null || true
    fi
    rm -rf "$state"
    docker compose --env-file "$env_file" -f "$compose" stop daemon broker logs 2>/dev/null || true
    systemctl disable --now tinyassets-backup.timer 2>/dev/null || true
    ;;
  *) echo 'usage: make_live.sh start <image>|check|stop' >&2; exit 2 ;;
esac
