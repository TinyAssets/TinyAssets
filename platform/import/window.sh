#!/usr/bin/env bash
# The cutover import window's host side: run as root on the droplet by
# .github/workflows/import-window.yml, from a private stage that also holds the job's bundle
# (import.mjs) and the control record's tiers (control.json). import_research.md §1 (the copy),
# §3 (where the job runs) and §4 (the rehearsal); tasks.md §9 item 7. Deleted at the cutover
# with platform/import/.
#
#   window.sh <mode> <run> <rehearsal|window> [delete]
#
# - The copy (census, when the run has none): the newest manifest on the backup remote, read
#   with the droplet's own rclone and BACKUP_DEST (/etc/tinyassets/env, as the backup writes it),
#   held to its shape by the job (`copy plan`); the free disk checked before the download and
#   again before the images are decompressed, each time keeping the disk watch's 20% free; the
#   archive and each pinned snapshot copied, each image decompressed sparse; every digest held to
#   the manifest (`copy digests`); then the shared layer extracted. All of it in a 0700 work
#   folder of the run's, ta-import-<run> under /var/tmp, never a live volume.
# - One Node for the whole run: the census that makes the copy pulls node:22 once and keeps
#   its registry digest in the work folder (`image`); every later mode of the run runs that
#   digest alone, and refuses a copy that names none.
# - Each job in `docker run --rm`, one folder writable for the step's output. The jobs without
#   network see the work folder read-only at its own path (so the kernel's loop devices name
#   its images); the modes that reach R2 and Queues see the bundles alone (bundle/out), never
#   the shared layer, the images, the census table or the private bundles. census and bundle
#   mount each pinned image read-only (MOUNT_OPTIONS, backup.ts's) for the job, and unmount it
#   after; census first runs every copied store through `integrity_check` (`copy stores`), and
#   bundle first checks the disk has room for as many bytes again as the images hold.
# - Output: the job's stdout and stderr kept 0600 in the work folder, and let out only through
#   `copy release` (no principal, no owner key, no path of the copy). The host tools' own
#   messages go to the work folder's host.private.log; this script prints fixed words and counts.
# - Credentials (stage, verify, wipe, admit, dlq) arrive on stdin, one a line, in
#   CREDENTIALS' order, and reach only the job, piped to its stdin (`docker run -i`), so they
#   are in no argument, no container config (`docker inspect`), no environment and no file;
#   the exclusion list (bundle) is stdin whole, piped to the job the same way.
# - The work folder is this host's own: root's, 0700, refused otherwise (/var/tmp is anyone's).
# - One run at a time on this host (a lock), and a copy made for a rehearsal is never run as
#   the window, nor the other way round.
#
# TA_IMPORT_ROOT, TA_IMPORT_ENV and TA_IMPORT_LOCK default to the droplet's places; the
# workflow sets none of them (its test points them at a scratch folder).
set -euo pipefail
umask 077

readonly MOUNT_OPTIONS="ro,norecovery,nosuid,nodev,loop,nouuid"
# The job's Node: this tag, pulled once a run, then that run's digest alone (`image`).
readonly NODE_TAG="node:22"
readonly DIGEST='^node@sha256:[0-9a-f]{64}$'
image=""
# node:sqlite still warns that it is experimental on every start: nothing for the log.
readonly NODE_FLAGS=(--disable-warning=ExperimentalWarning)
# The disk watch warns past 80% used (DISK_WARN_PCT, tinyassets-disk-watch.service), and its
# auto-prune starts at 85%: the copy never takes the disk past the warning.
readonly KEEP_FREE_PERCENT=20
# The job's credential inputs (cli.ts STAGE_INPUTS), in the order stdin hands them.
readonly CREDENTIALS=(CLOUDFLARE_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY
                      CLOUDFLARE_API_TOKEN IMPORT_KEY)

say() { printf '%s\n' "$*"; }
refuse() { say "refused: $*"; exit 2; }

mode="${1:-}"
run="${2:-}"
kind="${3:-}"
extra="${4:-}"
case "$mode" in
  census|bundle|stage|verify|wipe|admit|dlq|clean) ;;
  *) refuse "no such mode" ;;
esac
[[ "$run" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]] \
  || refuse "a run's id is lowercase letters, digits and dashes, at most 64"
[[ "$kind" == rehearsal || "$kind" == window ]] || refuse "a run is a rehearsal or the window"
[[ -z "$extra" || ( "$mode" == dlq && "$extra" == delete ) ]] \
  || refuse "only dlq takes a fourth word, delete"

stage="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
job_file="$stage/import.mjs"
control="$stage/control.json"
root="${TA_IMPORT_ROOT:-/var/tmp}"
env_file="${TA_IMPORT_ENV:-/etc/tinyassets/env}"
lock="${TA_IMPORT_LOCK:-/run/lock/tinyassets-import-window.lock}"
[[ "$root" == /* && -d "$root" && ! -L "$root" ]] || refuse "the work root is no folder"
work="$root/ta-import-$run"
log="$work/host.private.log"

exec 9>"$lock"
flock -n 9 || refuse "another import window run holds this host"

network=none
case "$mode" in
  stage|verify|wipe|admit|dlq)
    network=bridge
    for name in "${CREDENTIALS[@]}"; do
      value=""
      IFS= read -r value || true
      [[ -n "$value" ]] || refuse "the run was handed no ${name}"
      printf -v "$name" '%s' "$value"
    done
    unset value
    ;;
esac

# --- The job ------------------------------------------------------------------------------

# job <writable folder or -> [-i] <job's arguments...>: the job in its container, its output
# kept 0600 and let out through `copy release`; returns the job's exit code.
job() {
  local writable="$1"
  shift
  # A job with the network and the credentials reads the bundles alone.
  local readable="$work"
  [[ "$network" == none ]] || readable="$work/bundle/out"
  local args=(run --rm --network "$network" -v "$stage:$stage:ro" -v "$readable:$readable:ro")
  if [[ "$writable" != - ]]; then
    args+=(-v "$writable:$writable")
  fi
  if [[ "${1:-}" == -i ]]; then
    args+=(-i)
    shift
  fi
  local status=0
  if [[ "$network" != none ]]; then
    # The credentials on the job's stdin, one a line (printf is a builtin: no argument holds one).
    args+=(-i)
    printf '%s\n' "$CLOUDFLARE_ACCOUNT_ID" "$R2_ACCESS_KEY_ID" "$R2_SECRET_ACCESS_KEY" \
        "$CLOUDFLARE_API_TOKEN" "$IMPORT_KEY" \
      | docker "${args[@]}" "$image" node "${NODE_FLAGS[@]}" "$job_file" "$@" \
        > "$work/job.out.private" 2> "$work/job.err.private" || status=$?
  else
    docker "${args[@]}" "$image" node "${NODE_FLAGS[@]}" "$job_file" "$@" \
      > "$work/job.out.private" 2> "$work/job.err.private" || status=$?
  fi
  local released=0
  docker run --rm --network none -v "$stage:$stage:ro" -v "$work:$work:ro" "$image" \
    node "${NODE_FLAGS[@]}" "$job_file" copy release "$work" "$work/job.out.private" "$work/job.err.private" \
    2>> "$log" || released=$?
  if [[ "$released" -ne 0 ]]; then
    say "the job's output was not let out (release ${released}); the job ended ${status}"
    exit 4
  fi
  return "$status"
}

# The job's own word on the copy, for this script alone (never printed).
plan() {
  docker run --rm --network none -v "$stage:$stage:ro" -v "$work:$work:ro" "$image" \
    node "${NODE_FLAGS[@]}" "$job_file" copy plan "$work" --name "$1" > "$work/plan.private" 2>> "$log"
}

# The census that makes the copy: node:22 pulled once, its digest kept as the run's Node.
record_image() {
  docker pull -q "$NODE_TAG" >> "$log" 2>&1 || refuse "the job's image could not be pulled"
  image="$(docker image inspect -f '{{index .RepoDigests 0}}' "$NODE_TAG" 2>> "$log")" || true
  [[ "$image" =~ $DIGEST ]] || refuse "the job's image has no registry digest"
  printf '%s\n' "$image" > "$work/image"
}

# Every later mode: the run's own Node, by its digest, or refused.
use_image() {
  image="$(cat "$work/image" 2>/dev/null)" || true
  [[ "$image" =~ $DIGEST ]] || refuse "this run's copy names no Node image: run clean, then census"
  docker image inspect "$image" > /dev/null 2>&1 || docker pull -q "$image" >> "$log" 2>&1 \
    || refuse "the run's Node image could not be pulled"
}

# --- The copy -----------------------------------------------------------------------------

room() {  # <bytes> <what>: refused unless the disk keeps KEEP_FREE_PERCENT free after them
  local size avail
  read -r size avail < <(df -B1 --output=size,avail "$work" | tail -n 1)
  [[ "$size" =~ ^[0-9]+$ && "$avail" =~ ^[0-9]+$ ]] || refuse "the disk's size could not be read"
  if (( avail - $1 < size * KEEP_FREE_PERCENT / 100 )); then
    refuse "the copy needs $1 bytes for $2; the disk has ${avail} free of ${size} and keeps" \
           "${KEEP_FREE_PERCENT}% free"
  fi
}

remote_size() {  # <remote object>: its bytes
  local bytes
  bytes="$(rclone size --json "$1" 2>> "$log" \
           | sed -n 's/.*"bytes":[[:space:]]*\([0-9][0-9]*\).*/\1/p')" || true
  [[ "$bytes" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$bytes"
}

copy_backup() (
  if [[ -f "$env_file" ]]; then
    # As restore-credentials.sh reads it: a bad line in the env file must not stop the read.
    set +e
    set -a
    # shellcheck disable=SC1090
    . "$env_file" > /dev/null 2>&1
    set +a
    set -e
  fi
  [[ -n "${BACKUP_DEST:-}" ]] || refuse "BACKUP_DEST is not set (/etc/tinyassets/env)"
  local dest="${BACKUP_DEST%/}" manifest archive bytes need total n uid sid
  manifest="$(rclone lsf --files-only "$dest/platform" 2>> "$log" \
    | grep -E '^tinyassets-platform-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}Z\.json$' \
    | sort | tail -n 1)" || true
  [[ -n "$manifest" ]] || refuse "the backup remote lists no platform manifest"
  rclone cat "$dest/platform/$manifest" > "$work/manifest.json" 2>> "$log" \
    || refuse "the newest manifest could not be read"
  plan "$manifest" || refuse "the newest manifest is no platform manifest of its own name"
  archive="$(sed -n 's/^archive //p' "$work/plan.private")"
  local images=()
  mapfile -t images < <(sed -n 's/^image //p' "$work/plan.private")
  total="${#images[@]}"

  need="$(remote_size "$dest/$archive")" || refuse "the archive's size could not be read"
  n=0
  for each in "${images[@]}"; do
    read -r uid sid <<< "$each"
    n=$((n + 1))
    bytes="$(remote_size "$dest/$uid/$sid.img.gz")" \
      || refuse "image $n of $total's snapshot is not on the remote"
    need=$((need + bytes))
  done
  room "$need" "the download"

  rclone copyto "$dest/$archive" "$work/$archive" 2>> "$log" \
    || refuse "the archive could not be copied"
  n=0
  for each in "${images[@]}"; do
    read -r uid sid <<< "$each"
    n=$((n + 1))
    rclone copyto "$dest/$uid/$sid.img.gz" "$work/$uid.img.gz" 2>> "$log" \
      || refuse "image $n of $total's snapshot could not be copied"
  done

  need="$(gzip -dc "$work/$archive" 2>> "$log" | wc -c)" || refuse "the archive is no gzip"
  n=0
  for each in "${images[@]}"; do
    read -r uid _ <<< "$each"
    n=$((n + 1))
    bytes="$(gzip -dc "$work/$uid.img.gz" 2>> "$log" | wc -c)" \
      || refuse "image $n of $total's snapshot is no gzip"
    need=$((need + bytes))
  done
  room "$need" "the images and the shared layer"

  n=0
  for each in "${images[@]}"; do
    read -r uid _ <<< "$each"
    n=$((n + 1))
    # cp reads the pipe through /dev/stdin, writing each run of zeros as a hole.
    # shellcheck disable=SC2216
    gzip -dc "$work/$uid.img.gz" 2>> "$log" \
      | cp --sparse=always /dev/stdin "$work/$uid.img" 2>> "$log" \
      || refuse "image $n of $total could not be decompressed"
    rm -f -- "$work/$uid.img.gz"
  done
  job - copy digests "$work" || exit $?
  mkdir -m 700 "$work/data"
  tar -xzf "$work/$archive" -C "$work/data" --no-same-owner 2>> "$log" \
    || refuse "the archive could not be extracted whole"
  : > "$work/copy.done"
  say "the copy holds the archive and ${total} images"
)

# --- The images' mounts, for one job ------------------------------------------------------

mounted=()
unmount_images() {
  local at
  for at in "${mounted[@]}"; do
    umount "$at" 2>> "$log" || say "an image stayed mounted: run clean"
  done
  mounted=()
}

mount_images() {
  local uid n=0 total
  total="$(grep -c '^image ' "$work/plan.private" || true)"
  trap unmount_images EXIT
  [[ -d "$work/mnt" ]] || mkdir -m 700 "$work/mnt"
  while read -r uid _; do
    n=$((n + 1))
    [[ -z "$(losetup -j "$work/$uid.img")" ]] \
      || refuse "image $n of $total is already held by a loop device"
    [[ -d "$work/mnt/$uid" ]] || mkdir -m 700 "$work/mnt/$uid"
    mount -t xfs -o "$MOUNT_OPTIONS" "$work/$uid.img" "$work/mnt/$uid" 2>> "$log" \
      || refuse "image $n of $total could not be mounted read-only"
    mounted+=("$work/mnt/$uid")
  done < <(sed -n 's/^image //p' "$work/plan.private")
}

# --- The modes ----------------------------------------------------------------------------

own_folder() {
  [[ "$(stat -c '%u %a' "$work")" == "$(id -u) 700" ]] \
    || refuse "this run's work folder is not this host's own 0700 folder"
}

has_copy() {
  [[ -d "$work" && ! -L "$work" ]] || refuse "this run has no copy: run census first"
  own_folder
  [[ -f "$work/copy.done" ]] || refuse "this run's copy was left unfinished: run clean, then census"
  [[ "$(cat "$work/kind" 2>/dev/null)" == "$kind" ]] \
    || refuse "this run's copy was not made for a ${kind} run"
}

if [[ "$mode" == clean ]]; then
  if [[ ! -e "$work" ]]; then
    say "the run has no copy: nothing to clean"
    exit 0
  fi
  [[ -d "$work" && ! -L "$work" ]] || refuse "the run's work folder is no folder"
  own_folder
  if [[ -f "$work/kind" && "$(cat "$work/kind")" != "$kind" ]]; then
    refuse "this run's copy was not made for a ${kind} run"
  fi
  for at in "$work"/mnt/*; do
    if [[ -d "$at" ]] && mountpoint -q "$at"; then
      umount "$at" 2>> "$log" || refuse "an image could not be unmounted"
    fi
  done
  if grep -qF " $work/" /proc/self/mountinfo; then
    refuse "something is still mounted in the run's work folder"
  fi
  rm -rf -- "$work"
  say "the run's copy is unmounted and deleted"
  exit 0
fi

case "$mode" in
  census)
    if [[ -e "$work" ]]; then
      has_copy
      use_image
    else
      mkdir -m 700 "$work"
      printf '%s\n' "$kind" > "$work/kind"
      record_image
      copy_backup
    fi
    mount_images
    job - copy stores "$work" || exit $?
    rm -rf -- "$work/census"
    mkdir -m 700 "$work/census"
    job "$work/census" census "$work" --tiers "$control" \
      --table "$work/census/census.private.tsv"
    ;;
  bundle)
    has_copy
    use_image
    [[ ! -e "$work/bundle" ]] || refuse "this run's bundles are made already, and stand as made"
    mount_images
    # The bundles hold every imported owner's bytes again, and the private folder each excluded
    # owner's: the images' file bytes (du crosses into their read-only mounts) bound them.
    need="$(du -sbc "$work/mnt" 2>> "$log" | tail -n 1 | cut -f1)" \
      || refuse "the images' bytes could not be read"
    room "$need" "the bundles"
    mkdir -m 700 "$work/bundle"
    flags=()
    if [[ "$kind" == rehearsal ]]; then
      flags=(--rehearsal "$run")
    fi
    status=0
    job "$work/bundle" -i bundle "$work" --tiers "$control" --exclude - \
      --out "$work/bundle/out" --private "$work/bundle/private" "${flags[@]}" || status=$?
    if [[ "$status" -ne 0 && "$status" -ne 3 ]]; then
      rm -rf -- "$work/bundle"
      say "nothing was bundled"
    fi
    exit "$status"
    ;;
  stage|verify|wipe|admit|dlq)
    has_copy
    use_image
    [[ -d "$work/bundle/out" ]] || refuse "this run has no bundles: run bundle first"
    out="$work/bundle/out"
    record="$work/window/record.private.json"
    [[ -d "$work/window" ]] || mkdir -m 700 "$work/window"
    case "$mode" in
      stage)
        flags=()
        if [[ "$kind" == rehearsal ]]; then
          flags=(--rehearsal)
        fi
        job - stage "$out" --run "$run" "${flags[@]}"
        ;;
      dlq)
        flags=()
        if [[ "$extra" == delete ]]; then
          flags=(--delete)
        fi
        job - dlq "$out" --run "$run" "${flags[@]}"
        ;;
      *)
        job "$work/window" "$mode" "$out" --run "$run" --record "$record"
        ;;
    esac
    ;;
esac
