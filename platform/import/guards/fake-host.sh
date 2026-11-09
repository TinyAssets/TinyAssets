#!/usr/bin/env bash
# The import host's tools and the runner's SSH, faked for the window's test (runner.test's
# `host` and `step`, host.test's `host.py`), by the name it is linked as: rclone over local
# folders (FAKE_REMOTE/<remote>/<path>, each remote answering only with its key in rclone's
# own environment, and recording which remotes' keys it was given, never a value); docker
# running the job's command in plain Node, its stdin passed on, and pulling and naming one
# image (FAKE_DIGEST) without a registry, refusing any rclone key in its environment; mount,
# umount and losetup that only record; df saying FAKE_DF_SIZE and FAKE_DF_AVAIL; ssh answering
# the stage's mktemp, the marker (FAKE_MARKER) and the egress trace (FAKE_EGRESS,
# FAKE_METADATA, FAKE_EGRESS_STATUS; FAKE_NO_IMAGE for a copy that names no Node), and
# recording each other call's stdin; ssh-keyscan offering FAKE_HOSTKEY; ssh-keygen making
# that key; scp doing nothing. Every call is recorded in FAKE_LOG. FAKE_FLIP names an object
# whose off-site copy (remote taoff) gets a changed byte.
set -euo pipefail
tool="$(basename "$0")"
printf '%s %s\n' "$tool" "$*" >> "$FAKE_LOG"
# <remote>:<path> -> its local folder, refused unless rclone was given that remote's key.
at() {
  local remote="${1%%:*}" path="${1#*:}" upper
  upper="$(printf '%s' "$remote" | tr '[:lower:]' '[:upper:]')"
  local key="RCLONE_CONFIG_${upper}_ACCESS_KEY_ID" secret="RCLONE_CONFIG_${upper}_SECRET_ACCESS_KEY"
  [[ -n "${!key:-}" && -n "${!secret:-}" ]] || { echo "no key for $remote" >&2; exit 7; }
  printf '%s/%s/%s' "$FAKE_REMOTE" "$remote" "${path#/}"
}
case "$tool" in
  rclone)
    printf 'rclone-keys %s\n' "$(env | sed -n 's/^RCLONE_CONFIG_\([A-Z]*\)_ACCESS_KEY_ID=.*/\1/p' \
                                 | sort | tr '\n' ' ')" >> "$FAKE_LOG"
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
      cat)
        path="$(at "$1")"
        [[ -f "$path" ]] || exit 3
        cat "$path"
        ;;
      size)
        path="$(at "${*: -1}")"
        [[ -f "$path" ]] || exit 3
        printf '{"count":1,"bytes":%s,"sizeless":0}\n' "$(stat -c %s "$path")"
        ;;
      copyto)
        from="$1"
        to="$2"
        [[ "$from" != *:* ]] || from="$(at "$from")"
        if [[ "$to" == *:* ]]; then
          remote="${to%%:*}"
          to="$(at "$to")"
          mkdir -p "$(dirname "$to")"
          cp "$from" "$to"
          if [[ "$remote" == taoff && -n "${FAKE_FLIP:-}" && "$to" == *"$FAKE_FLIP" ]]; then
            # A byte more, still gzip where it was (a second member), so only its digest says.
            if [[ "$to" == *.gz ]]; then
              printf 'x' | gzip -c >> "$to"
            else
              printf 'x' >> "$to"
            fi
          fi
        else
          cp "$from" "$to"
        fi
        ;;
      *) exit 9 ;;
    esac
    ;;
  docker)
    if env | grep -qE '^(RCLONE_CONFIG_|SPACES_|R2_|CLOUDFLARE_|IMPORT_KEY=)'; then
      echo "a key reached docker's environment" >&2
      exit 8
    fi
    case "$1" in
      pull) [[ "$3" == node:22 || "$3" == "$FAKE_DIGEST" ]] || exit 9; exit 0 ;;
      image)
        if [[ "$3" == -f ]]; then
          [[ "$5" == node:22 ]] || exit 9
          printf '%s\n' "$FAKE_DIGEST"
        else
          [[ "$3" == "$FAKE_DIGEST" ]] || exit 1
        fi
        exit 0
        ;;
      run) shift ;;
      *) exit 9 ;;
    esac
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --rm|-i) shift ;;
        --network|-v|-e) shift 2 ;;
        node@sha256:*) [[ "$1" == "$FAKE_DIGEST" ]] || exit 9; shift; break ;;
        *) exit 9 ;;
      esac
    done
    exec "$@"
    ;;
  mount|umount|losetup) ;;
  df) printf 'Size Avail\n%s %s\n' "$FAKE_DF_SIZE" "$FAKE_DF_AVAIL" ;;
  ssh)
    last="${*: -1}"
    if [[ "${*: -3:1}" == mktemp ]]; then
      echo /tmp/tinyassets-import-window.abc123
      exit 0
    fi
    case "$last" in
      "cat /etc/tinyassets-import-host") printf '%s\n' "${FAKE_MARKER:-}"; exit 0 ;;
      *cdn-cgi/trace*)
        if [[ -n "${FAKE_NO_IMAGE:-}" ]]; then
          echo image=none
          exit 3
        fi
        printf 'fl=1\nh=www.cloudflare.com\nip=%s\nts=1\n\nmetadata=%s\n' "${FAKE_EGRESS:-}" \
          "${FAKE_METADATA-blocked}"
        exit "${FAKE_EGRESS_STATUS:-0}"
        ;;
    esac
    printf 'STDIN %s\n' "$(cat)" >> "$FAKE_LOG"
    echo "the step ran"
    ;;
  ssh-keyscan) printf '# %s:22 SSH-2.0-fake\n%s %s\n' "${*: -1}" "${*: -1}" "$FAKE_HOSTKEY" ;;
  ssh-keygen)
    file="${*: -1}"
    printf 'fake private key\n' > "$file"
    printf '%s fake\n' "$FAKE_HOSTKEY" > "$file.pub"
    ;;
  scp) ;;
  *) exit 9 ;;
esac
