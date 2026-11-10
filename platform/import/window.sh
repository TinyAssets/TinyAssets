#!/usr/bin/env bash
# The cutover import window's host side: run as root on the run's import host (a disposable
# Droplet, tinyassets-import-<run>, that .github/workflows/import-window.yml makes at the
# run's census and destroys after its clean: platform/import/host.py), from a private stage
# that also holds the job's bundle (import.mjs) and the control record's tiers (control.json).
# import_research.md §1 (the copy), §3 (where the job runs) and §4 (the rehearsal);
# import_host_research.md (the import host); tasks.md §9 item 7. Deleted at the cutover with
# platform/import/.
#
#   window.sh <mode> <run> <rehearsal|window> [delete]
#
# - The host: refused unless its marker (/etc/tinyassets-import-host, which the import host's
#   cloud-init writes) holds this run's id, and refused on any host that holds the old stack
#   (/var/lib/boxhostd or /etc/tinyassets/env). So it never runs on the production droplet,
#   whatever points it there.
# - The copy (census, when the run has none): the newest manifest on the primary backup store
#   (SOURCE, the bucket the nightly backup writes), read with a read-only Spaces key handed on
#   stdin and held in rclone's environment alone, never a file; held to its shape by the job
#   (`copy plan`); the free disk checked before the download and again before the images are
#   decompressed, each time keeping a margin free; the archive and each pinned snapshot
#   copied, each image decompressed sparse; every digest held to the manifest
#   (`copy digests`); then the shared layer extracted. All of it in a 0700 work folder of the
#   run's, ta-import-<run> under /var/tmp, never a live volume.
# - One Node for the whole run: the census that makes the copy pulls node:22 once, prints its
#   registry digest (public, so the rehearsal's and the window's can be compared) and keeps it
#   in the work folder (`image`); every later mode of the run runs that digest alone, and
#   refuses a copy that names none.
# - Each job in `docker run --rm`, one folder writable for the step's output. The jobs without
#   network see the work folder read-only at its own path (so the kernel's loop devices name
#   its images); the modes that reach R2 and Queues see the bundles alone (bundle/out), never
#   the shared layer, the images, the census table or the private bundles. census and bundle
#   mount each pinned image read-only (MOUNT_OPTIONS, backup.ts's) for the job, and unmount it
#   after; census first runs every copied store through `integrity_check` (`copy stores`), and
#   bundle first checks the disk has room for as many bytes again as the images hold.
# - The off-site copy (offsite, the real window alone): the objects the window's copy was made
#   from (its manifest, which must still be the store's newest, its archive and every pinned
#   snapshot) copied from SOURCE to the R2 bucket tinyassets-final-backup, then each read back
#   from R2 and held to the copy's manifest (`copy digests`), the read-back deleted after. It
#   only reads SOURCE. Its keys reach rclone's environment alone, and no job is networked.
# - Output: the job's stdout and stderr kept 0600 in the work folder, and let out only through
#   `copy release` (no principal, no owner key, no path of the copy). The host tools' own
#   messages go to the work folder's host.private.log; this script prints fixed words and counts.
#   A remote's refusal names the operation, rclone's exit code and an allowlisted S3 code and
#   HTTP status (`cause`), never rclone's own words, which go to the log alone.
# - Credentials arrive on stdin, one a line: the source key (census, offsite) in
#   SOURCE_CREDENTIALS' order, then the off-site key (offsite) in OFFSITE_CREDENTIALS'; each
#   refused by its name when it holds a space or a control character, and the run refused when
#   a line follows the last (a credential that held a line break); the
#   job's (stage, verify, wipe, admit, dlq) in CREDENTIALS' order, which reach only the job,
#   piped to its stdin (`docker run -i`), so they are in no argument, no container config
#   (`docker inspect`), no environment and no file. The exclusion list (bundle) is stdin
#   whole, piped to the job the same way.
# - The work folder is this host's own: root's, 0700, refused otherwise (/var/tmp is anyone's).
# - One run at a time on this host (a lock), and a copy made for a rehearsal is never run as
#   the window, nor the other way round.
#
# TA_IMPORT_ROOT, TA_IMPORT_LOCK and TA_IMPORT_HOST_ROOT (put before the marker's path and the
# old stack's, which are checked at / as well) default to the host's own places; the workflow
# sets none of them (its test points them at a scratch folder).
set -euo pipefail
umask 077

readonly MOUNT_OPTIONS="ro,norecovery,nosuid,nodev,loop,nouuid"
# The job's Node: this tag, pulled once a run, then that run's digest alone (`image`).
readonly NODE_TAG="node:22"
readonly DIGEST='^node@sha256:[0-9a-f]{64}$'
image=""
# node:sqlite still warns that it is experimental on every start: nothing for the log.
readonly NODE_FLAGS=(--disable-warning=ExperimentalWarning)
# A margin the copy leaves free on the import host's disk (160 GB, the run's alone), so a copy
# that would fill it stops loudly before it starts (Fact 8) rather than part-way through.
readonly KEEP_FREE_PERCENT=20
# The job's credential inputs (cli.ts STAGE_INPUTS), in the order stdin hands them.
readonly CREDENTIALS=(CLOUDFLARE_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY
                      CLOUDFLARE_API_TOKEN IMPORT_KEY)
# The primary backup store's read-only Spaces key (census, offsite), in stdin's order.
readonly SOURCE_CREDENTIALS=(SPACES_ACCESS_KEY_ID SPACES_SECRET_ACCESS_KEY)
# The off-site bucket's R2 key (offsite), after the source's, in stdin's order.
readonly OFFSITE_CREDENTIALS=(CLOUDFLARE_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY)
# The primary backup store: the bucket and prefix the nightly backup writes (BACKUP_DEST,
# install-host-services.yml), on its region's Spaces endpoint. Neither is a secret.
readonly SOURCE="tasrc:workflow-backups-jonnyton-sfo3/workflow-backups"
readonly SOURCE_ENDPOINT="sfo3.digitaloceanspaces.com"
# The off-site copy of the window's final backup: the same layout under the same prefix.
readonly OFFSITE="taoff:tinyassets-final-backup/workflow-backups"
readonly MANIFEST_NAME='^tinyassets-platform-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}Z[.]json$'
# What a failed rclone call may say of its cause: one of these S3 error codes and one of these
# HTTP statuses (rclone's `status code: NNN`), each found in its stderr, or "unknown". Nothing
# else of that stderr is printed (it names the remote's paths and host); it goes to the log.
readonly S3_CODES=(AccessDenied InvalidAccessKeyId SignatureDoesNotMatch NoSuchBucket NoSuchKey
                   AuthorizationHeaderMalformed InvalidRequest PermanentRedirect
                   RequestTimeTooSkewed Forbidden)
readonly HTTP_STATUSES=(400 403 404)

say() { printf '%s\n' "$*"; }
refuse() { say "refused: $*"; exit 2; }

mode="${1:-}"
run="${2:-}"
kind="${3:-}"
extra="${4:-}"
case "$mode" in
  census|bundle|stage|verify|wipe|admit|dlq|offsite|clean) ;;
  *) refuse "no such mode" ;;
esac
[[ "$run" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]] \
  || refuse "a run's id is lowercase letters, digits and dashes, at most 64"
[[ "$kind" == rehearsal || "$kind" == window ]] || refuse "a run is a rehearsal or the window"
[[ -z "$extra" || ( "$mode" == dlq && "$extra" == delete ) ]] \
  || refuse "only dlq takes a fourth word, delete"
[[ "$mode" != offsite || "$kind" == window ]] \
  || refuse "offsite copies the real window's final backup alone, never a rehearsal's"

# The host: this run's import host, and never one that holds the old stack.
host_root="${TA_IMPORT_HOST_ROOT:-}"
for path in /var/lib/boxhostd /etc/tinyassets/env; do
  [[ ! -e "$path" && ! -e "$host_root$path" ]] \
    || refuse "this host holds the old stack (${path}): the window runs on its import host alone"
done
[[ "$(cat "$host_root/etc/tinyassets-import-host" 2>/dev/null)" == "$run" ]] \
  || refuse "this host is not this run's import host (its marker does not name the run)"

stage="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
job_file="$stage/import.mjs"
control="$stage/control.json"
root="${TA_IMPORT_ROOT:-/var/tmp}"
lock="${TA_IMPORT_LOCK:-/run/lock/tinyassets-import-window.lock}"
[[ "$root" == /* && -d "$root" && ! -L "$root" ]] || refuse "the work root is no folder"
work="$root/ta-import-$run"
log="$work/host.private.log"

exec 9>"$lock"
flock -n 9 || refuse "another import window run holds this host"

# take <name>...: each from stdin's next line, into this shell alone (never exported). No
# credential holds a space or a control character: one that does (a secret pasted with a
# trailing CR or space) is refused by its name, before any request signs with it.
take() {
  local name value
  for name in "$@"; do
    value=""
    IFS= read -r value || true
    [[ -n "$value" ]] || refuse "the run was handed no ${name}"
    [[ "$value" != *[[:space:][:cntrl:]]* ]] \
      || refuse "the run was handed a ${name} that holds a space or a control character"
    printf -v "$name" '%s' "$value"
  done
  # Nothing after them: a line more is a credential that held a line break, and every value
  # after it shifted by one, so the run is refused before any is used.
  local rest=""
  if IFS= read -r rest || [[ -n "$rest" ]]; then
    refuse "the run was handed a line more than its credentials: one of $* holds a line break"
  fi
}

network=none
case "$mode" in
  census) take "${SOURCE_CREDENTIALS[@]}" ;;
  offsite) take "${SOURCE_CREDENTIALS[@]}" "${OFFSITE_CREDENTIALS[@]}" ;;
  stage|verify|wipe|admit|dlq)
    network=bridge
    take "${CREDENTIALS[@]}"
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
    # The credentials on the job's stdin, one a line, in CREDENTIALS' own order (printf is a
    # builtin: no argument holds one).
    args+=(-i)
    for name in "${CREDENTIALS[@]}"; do printf '%s\n' "${!name}"; done \
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
  say "the job's Node is ${image}"
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

# The remotes, each defined in one rclone's environment for that call alone (never exported,
# never a file): the primary store with its read-only key, the off-site bucket with its own.
# Neither makes or checks a bucket (no_check_bucket), so neither key needs to.
source_rclone() {
  RCLONE_CONFIG_TASRC_TYPE=s3 RCLONE_CONFIG_TASRC_PROVIDER=DigitalOcean \
  RCLONE_CONFIG_TASRC_ENDPOINT="$SOURCE_ENDPOINT" \
  RCLONE_CONFIG_TASRC_ACCESS_KEY_ID="$SPACES_ACCESS_KEY_ID" \
  RCLONE_CONFIG_TASRC_SECRET_ACCESS_KEY="$SPACES_SECRET_ACCESS_KEY" \
  RCLONE_CONFIG_TASRC_NO_CHECK_BUCKET=true \
    command rclone "$@"
}
offsite_rclone() {
  RCLONE_CONFIG_TAOFF_TYPE=s3 RCLONE_CONFIG_TAOFF_PROVIDER=Cloudflare \
  RCLONE_CONFIG_TAOFF_ENDPOINT="https://${CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com" \
  RCLONE_CONFIG_TAOFF_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
  RCLONE_CONFIG_TAOFF_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
  RCLONE_CONFIG_TAOFF_NO_CHECK_BUCKET=true \
    command rclone "$@"
}
both_rclone() {  # one call that reads the source and writes the off-site bucket
  RCLONE_CONFIG_TASRC_TYPE=s3 RCLONE_CONFIG_TASRC_PROVIDER=DigitalOcean \
  RCLONE_CONFIG_TASRC_ENDPOINT="$SOURCE_ENDPOINT" \
  RCLONE_CONFIG_TASRC_ACCESS_KEY_ID="$SPACES_ACCESS_KEY_ID" \
  RCLONE_CONFIG_TASRC_SECRET_ACCESS_KEY="$SPACES_SECRET_ACCESS_KEY" \
  RCLONE_CONFIG_TASRC_NO_CHECK_BUCKET=true \
  RCLONE_CONFIG_TAOFF_TYPE=s3 RCLONE_CONFIG_TAOFF_PROVIDER=Cloudflare \
  RCLONE_CONFIG_TAOFF_ENDPOINT="https://${CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com" \
  RCLONE_CONFIG_TAOFF_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
  RCLONE_CONFIG_TAOFF_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
  RCLONE_CONFIG_TAOFF_NO_CHECK_BUCKET=true \
    command rclone "$@"
}

cause() {  # <stderr file>: "code <S3_CODES' or unknown>, status <HTTP_STATUSES' or unknown>"
  local code=unknown status=unknown each
  for each in "${S3_CODES[@]}"; do
    if grep -qE "(^|[^[:alnum:]])${each}([^[:alnum:]]|\$)" "$1"; then
      code="$each"
      break
    fi
  done
  for each in "${HTTP_STATUSES[@]}"; do
    if grep -qiE "status ?code: ?${each}([^0-9]|\$)" "$1"; then
      status="$each"
      break
    fi
  done
  printf 'code %s, status %s' "$code" "$status"
}

# remote <operation> <what> <stdout's file> <rclone> <arguments...>: one rclone call, its stderr
# to the log alone. A failure is refused with <what>, the operation, rclone's exit code and its
# `cause`, never a byte of its stderr (Fact 8: loud, and nothing of the remote's names).
remote() {
  local op="$1" what="$2" out="$3" status=0 err="$work/rclone.err.private" why
  shift 3
  "$@" > "$out" 2> "$err" || status=$?
  cat "$err" >> "$log"
  why="$(cause "$err")"
  rm -f -- "$err"
  [[ "$status" -eq 0 ]] || refuse "${what} (${op} failed: rclone exit ${status}, ${why})"
}

size_of() {  # <rclone> <remote object> <what>: its bytes, in `bytes`, or refused
  remote size "$3" "$work/size.private" "$1" size --json "$2"
  bytes="$(sed -n 's/.*"bytes":[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$work/size.private")"
  rm -f -- "$work/size.private"
  [[ "$bytes" =~ ^[0-9]+$ ]] || refuse "$3 (its size answered no byte count)"
}

# newest: the newest manifest's name on the primary store, or refused: a listing that failed
# apart from one that holds no manifest.
newest_manifest() {
  local listed
  remote list "the backup remote's platform manifests could not be listed" \
    "$work/listing.private" source_rclone lsf --files-only "$SOURCE/platform"
  listed="$(wc -l < "$work/listing.private")"
  newest="$(grep -E "$MANIFEST_NAME" "$work/listing.private" | sort | tail -n 1)" || true
  rm -f -- "$work/listing.private"
  [[ -n "$newest" ]] || refuse "the backup remote's platform folder was listed and holds no" \
                               "platform manifest (objects listed: ${listed})"
}

# fetch <rclone> <remote> <folder>: plan.private's archive and images from the remote into the
# folder, each stage after its room is checked, each image decompressed sparse; then held to
# the folder's manifest.json by the job (`copy digests`).
fetch() {
  local rc="$1" from="$2" dir="$3" archive bytes need total n uid sid
  archive="$(sed -n 's/^archive //p' "$work/plan.private")"
  local images=()
  mapfile -t images < <(sed -n 's/^image //p' "$work/plan.private")
  total="${#images[@]}"

  size_of "$rc" "$from/$archive" "the archive's size could not be read"
  need="$bytes"
  n=0
  for each in "${images[@]}"; do
    read -r uid sid <<< "$each"
    n=$((n + 1))
    size_of "$rc" "$from/$uid/$sid.img.gz" "image $n of $total's snapshot's size could not be read"
    need=$((need + bytes))
  done
  room "$need" "the download"

  remote copy "the archive could not be copied" /dev/null \
    "$rc" copyto "$from/$archive" "$dir/$archive"
  n=0
  for each in "${images[@]}"; do
    read -r uid sid <<< "$each"
    n=$((n + 1))
    remote copy "image $n of $total's snapshot could not be copied" /dev/null \
      "$rc" copyto "$from/$uid/$sid.img.gz" "$dir/$uid.img.gz"
  done

  # The images' apparent bytes. Each is written sparse below, so this over-counts what the
  # disk gives them; on the import host's 160 GB the over-count binds nothing.
  need="$(gzip -dc "$dir/$archive" 2>> "$log" | wc -c)" || refuse "the archive is no gzip"
  n=0
  for each in "${images[@]}"; do
    read -r uid _ <<< "$each"
    n=$((n + 1))
    bytes="$(gzip -dc "$dir/$uid.img.gz" 2>> "$log" | wc -c)" \
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
    gzip -dc "$dir/$uid.img.gz" 2>> "$log" \
      | cp --sparse=always /dev/stdin "$dir/$uid.img" 2>> "$log" \
      || refuse "image $n of $total could not be decompressed"
    rm -f -- "$dir/$uid.img.gz"
  done
  job - copy digests "$dir" || exit $?
}

copy_backup() (
  newest_manifest
  manifest="$newest"
  remote read "the newest manifest could not be read" "$work/manifest.json" \
    source_rclone cat "$SOURCE/platform/$manifest"
  plan "$manifest" || refuse "the newest manifest is no platform manifest of its own name"
  printf '%s\n' "$manifest" > "$work/manifest.name"
  fetch source_rclone "$SOURCE" "$work"
  archive="$(sed -n 's/^archive //p' "$work/plan.private")"
  mkdir -m 700 "$work/data"
  tar -xzf "$work/$archive" -C "$work/data" --no-same-owner 2>> "$log" \
    || refuse "the archive could not be extracted whole"
  : > "$work/copy.done"
  say "the copy holds the archive and $(grep -c '^image ' "$work/plan.private") images"
)

# The window's final backup copied off-site, then read back and held to the copy's manifest.
offsite_copy() (
  manifest="$(cat "$work/manifest.name" 2>/dev/null)" || true
  [[ "$manifest" =~ $MANIFEST_NAME ]] \
    || refuse "this run's copy names no manifest: run clean, then census"
  newest_manifest
  [[ "$newest" == "$manifest" ]] \
    || refuse "the backup remote's newest manifest is not the one this run's copy was made from"
  archive="$(sed -n 's/^archive //p' "$work/plan.private")"
  images=()
  mapfile -t images < <(sed -n 's/^image //p' "$work/plan.private")
  total="${#images[@]}"

  remote copy "the manifest could not be copied off-site" /dev/null \
    both_rclone copyto "$SOURCE/platform/$manifest" "$OFFSITE/platform/$manifest"
  remote copy "the archive could not be copied off-site" /dev/null \
    both_rclone copyto "$SOURCE/$archive" "$OFFSITE/$archive"
  n=0
  for each in "${images[@]}"; do
    read -r uid sid <<< "$each"
    n=$((n + 1))
    remote copy "image $n of $total's snapshot could not be copied off-site" /dev/null \
      both_rclone copyto "$SOURCE/$uid/$sid.img.gz" "$OFFSITE/$uid/$sid.img.gz"
  done

  # Read back from the off-site bucket alone, into a folder of its own, and held to the
  # manifest the copy was made from.
  back="$work/offsite"
  rm -rf -- "$back"
  mkdir -m 700 "$back"
  remote read "the off-site manifest could not be read back" "$back/manifest.json" \
    offsite_rclone cat "$OFFSITE/platform/$manifest"
  cmp -s "$back/manifest.json" "$work/manifest.json" \
    || refuse "the off-site manifest read back is not the copy's"
  fetch offsite_rclone "$OFFSITE" "$back"
  rm -rf -- "$back"
  say "the off-site copy holds the manifest, the archive and ${total} images, each read back" \
      "and held to the manifest's digests"
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
  offsite)
    has_copy
    use_image
    offsite_copy
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
