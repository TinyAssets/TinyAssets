#!/usr/bin/env bash
# Disposable S3 endpoint with the root rclone configuration shape used by deploy.
set -euo pipefail

name=tinyassets-live-like-s3
bucket=tinyassets-live-like
port=19000
image='netiedge/minio:RELEASE.2025-01-20T14-49-07Z@sha256:e6c282005f6c8496256f8fd07e9a8b82073344c217ea87436d9af888db67eb0e'
config=/root/.config/rclone/rclone.conf

case "${1:-}" in
  up)
    test "$(id -u)" -eq 0
    command -v rclone >/dev/null
    if docker container inspect "$name" >/dev/null 2>&1; then
      echo "$name already exists; run down first" >&2
      exit 1
    fi
    gateway="$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}')"
    test -n "$gateway"
    docker run -d --name "$name" --restart no \
      -p "${port}:9000" \
      -e MINIO_ROOT_USER=livegateaccess \
      -e MINIO_ROOT_PASSWORD=livegatesecret12345 \
      "$image" server /data >/dev/null
    install -d -m 0700 /root/.config/rclone
    if test -e "$config"; then
      echo "refusing to replace existing root rclone config" >&2
      docker rm -f "$name" >/dev/null
      exit 1
    fi
    (umask 077; cat > "$config" <<EOF
[spaces]
type = s3
provider = Minio
access_key_id = livegateaccess
secret_access_key = livegatesecret12345
endpoint = http://${gateway}:${port}
acl = private
no_check_bucket = true
EOF
    )
    for _ in $(seq 1 60); do
      if rclone mkdir "spaces:${bucket}" --retries 1 --low-level-retries 1 >/dev/null 2>&1 \
          && rclone lsd spaces: --retries 1 --low-level-retries 1 2>/dev/null \
            | awk '{print $NF}' | grep -Fxq "$bucket"; then
        printf 'BACKUP_DEST=spaces:%s/workflow-backups\n' "$bucket"
        exit 0
      fi
      sleep 1
    done
    echo 'S3 endpoint never became ready' >&2
    exit 1
    ;;
  down)
    test "$(id -u)" -eq 0
    docker rm -f "$name" >/dev/null 2>&1 || true
    if test -f "$config" && grep -q 'access_key_id = livegateaccess' "$config"; then
      rm "$config"
    fi
    ;;
  *) echo 'usage: s3_remote.sh up|down' >&2; exit 2 ;;
esac
