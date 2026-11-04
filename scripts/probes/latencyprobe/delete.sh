#!/usr/bin/env bash
# Deletes everything the latency probe makes and proves it is gone (latency-probe.yml runs it
# before deploying and again at the end, whatever happened):
#   1. each named Worker, force-deleted, so its Durable Object namespaces and every object's
#      storage go with it; a script never there (404) counts as gone; the door first, since it
#      binds the owner's class;
#   2. every R2 bucket whose name starts with ta-latencyprobe- (the driver empties this run's
#      bucket first; a bucket that is not empty refuses its delete, and that fails this script);
# then reads back that no Worker, Durable Object namespace or R2 bucket whose name starts with
# ta-latencyprobe is left on the account (the lists may lag, so they are retried for a
# minute). Exits 1 if anything remains or a deletion cannot be proven.
#
#   CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... delete.sh ta-latencyprobe-door ...
set -uo pipefail
api="${LATENCYPROBE_API_BASE:-https://api.cloudflare.com/client/v4}/accounts/${CLOUDFLARE_ACCOUNT_ID}"
auth="Authorization: Bearer ${CLOUDFLARE_API_TOKEN}"
prefix="ta-latencyprobe"
failed=0
body="$(mktemp)"
trap 'rm -f "${body}"' EXIT

errors_of() {
  python3 -c 'import json,sys; print([e.get("message") for e in json.load(open(sys.argv[1])).get("errors") or []])' "$1" 2>/dev/null || true
}

# The names under $1 (workers/scripts, workers/durable_objects/namespaces, r2/buckets) that
# start with the prefix, space-separated; returns 1 when the list is unreadable.
listed() {
  local status
  status="$(curl -sS --max-time 60 -H "${auth}" -o "${body}" -w '%{http_code}' "${api}/$1")" \
    || status="curl failed"
  if [ "${status}" != "200" ]; then
    echo "list $1: HTTP ${status} $(errors_of "${body}")" >&2
    return 1
  fi
  python3 - "${body}" "${prefix}" "$1" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
prefix, kind = sys.argv[2], sys.argv[3]
result = data.get("result")
if kind.startswith("r2/buckets"):
    rows = (result or {}).get("buckets") if isinstance(result, dict) else None
else:
    rows = result
if not isinstance(rows, list):
    raise SystemExit(1)
info = data.get("result_info") or {}
if info.get("total_pages") is not None and int(info["total_pages"]) > 1:
    raise SystemExit(1)
names = []
for r in rows:
    found = [r.get("id"), r.get("name"), r.get("script")]
    if any(str(n or "").startswith(prefix) for n in found):
        names.append(next(str(n) for n in found if n))
print(" ".join(sorted(set(names))))
PY
}

# 1. The Workers.
for name in "$@"; do
  case "${name}" in
    "${prefix}"*) ;;
    *) echo "::error::refusing to delete ${name}: not a ${prefix} name"; failed=1; continue ;;
  esac
  url="${api}/workers/scripts/${name}"
  status="$(curl -sS --max-time 60 -X DELETE -H "${auth}" -o "${body}" -w '%{http_code}' "${url}?force=true")" \
    || status="curl failed"
  errors="$(errors_of "${body}")"
  check="$(curl -sS --max-time 60 -H "${auth}" -o /dev/null -w '%{http_code}' "${url}")" || check="curl failed"
  if [ "${check}" = "404" ]; then
    echo "${name}: gone (delete HTTP ${status})"
  else
    echo "::error::${name} is still deployed (delete HTTP ${status} ${errors}; check HTTP ${check})."
    failed=1
  fi
done

# 2. The buckets.
if buckets="$(listed "r2/buckets?per_page=1000")"; then
  for bucket in ${buckets}; do
    status="$(curl -sS --max-time 60 -X DELETE -H "${auth}" -o "${body}" -w '%{http_code}' "${api}/r2/buckets/${bucket}")" \
      || status="curl failed"
    check="$(curl -sS --max-time 60 -H "${auth}" -o /dev/null -w '%{http_code}' "${api}/r2/buckets/${bucket}")" || check="curl failed"
    if [ "${check}" = "404" ]; then
      echo "bucket ${bucket}: gone (delete HTTP ${status})"
    else
      echo "::error::bucket ${bucket} is still there (delete HTTP ${status} $(errors_of "${body}"); check HTTP ${check}). Empty and delete it by hand: R2, ${bucket}."
      failed=1
    fi
  done
else
  # The workflow makes its bucket with this same token: a token that cannot reach R2 made
  # none, so there is no bucket to find, and step 3 reads the other lists alone.
  echo "R2: the token cannot list buckets, so no run with it made one; R2 is not checked"
  LATENCYPROBE_NO_R2=1
fi

# 3. Nothing named ta-latencyprobe* left.
left="unread"
for attempt in 1 2 3 4 5 6 7; do
  found=""
  readable=1
  for list in workers/scripts "workers/durable_objects/namespaces?per_page=1000" "r2/buckets?per_page=1000"; do
    if names="$(listed "${list}")"; then
      found="${found} ${names:+${list}:${names}}"
    elif [ "${list#r2/}" != "${list}" ] && [ "${LATENCYPROBE_NO_R2:-}" = "1" ]; then
      :  # this run never had R2: an unreadable bucket list is no residue of it
    else
      readable=0
    fi
  done
  found="$(echo "${found}" | xargs)"
  if [ "${readable}" = "1" ]; then
    left="${found}"
    if [ -z "${left}" ]; then
      break
    fi
  fi
  if [ "${attempt}" != "7" ]; then
    sleep 10
  fi
done
if [ "${left}" = "unread" ]; then
  echo "::error::Could not read every list (Workers, Durable Object namespaces, R2 buckets), so the cleanup is unproven."
  failed=1
elif [ -n "${left}" ]; then
  echo "::error::Still listed after the deletes: ${left}"
  failed=1
else
  echo "Workers, Durable Object namespaces and R2 buckets: none named ${prefix}* left"
fi
exit "${failed}"
