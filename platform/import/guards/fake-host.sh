#!/usr/bin/env bash
# The droplet's tools, faked for the window's test (window.test's `host`), by the name it is
# linked as: rclone over a local folder (FAKE_REMOTE, the remote `fake:`), docker running the
# job's command in plain Node, mount, umount and losetup that only record, and df saying
# FAKE_DF_SIZE and FAKE_DF_AVAIL. Every call is recorded in FAKE_LOG.
set -euo pipefail
tool="$(basename "$0")"
printf '%s %s\n' "$tool" "$*" >> "$FAKE_LOG"
at() { local path="${1#fake:}"; printf '%s/%s' "$FAKE_REMOTE" "${path#/}"; }
case "$tool" in
  rclone)
    command="$1"
    shift
    case "$command" in
      lsf)
        dir="$(at "${*: -1}")"
        for path in "$dir"/*; do
          if [[ -f "$path" ]]; then
            basename "$path"
          fi
        done
        ;;
      cat) cat "$(at "$1")" ;;
      size)
        path="$(at "${*: -1}")"
        [[ -f "$path" ]] || exit 3
        printf '{"count":1,"bytes":%s,"sizeless":0}\n' "$(stat -c %s "$path")"
        ;;
      copyto) cp "$(at "$1")" "$2" ;;
      *) exit 9 ;;
    esac
    ;;
  docker)
    [[ "$1" == pull ]] && exit 0
    [[ "$1" == run ]] || exit 9
    shift
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --rm|-i) shift ;;
        --network|-v|-e) shift 2 ;;
        node:22) shift; break ;;
        *) exit 9 ;;
      esac
    done
    exec "$@"
    ;;
  mount|umount|losetup) ;;
  df) printf 'Size Avail\n%s %s\n' "$FAKE_DF_SIZE" "$FAKE_DF_AVAIL" ;;
  *) exit 9 ;;
esac
