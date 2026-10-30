#!/usr/bin/env bash
# Force-deletes the named store-probe Workers, proves each is gone, and proves no Durable
# Object namespace of theirs is left (store-probe.yml runs it before deploying and again
# at the end). force=true deletes a script's Durable Object namespaces with it, so no
# object state outlives a run; the namespace list is then read back (it may lag, so it is
# retried for a minute). A script that was never there (404) counts as gone. Name the
# door first: it binds the others' classes. Exits 1 if any script or namespace remains.
#
#   CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... delete.sh ta-storeprobe-door ta-storeprobe ...
set -uo pipefail
api="https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}"
auth="Authorization: Bearer ${CLOUDFLARE_API_TOKEN}"
failed=0
for name in "$@"; do
  url="${api}/workers/scripts/${name}"
  body="$(mktemp)"
  status="$(curl -sS --max-time 60 -X DELETE -H "${auth}" -o "${body}" -w '%{http_code}' "${url}?force=true")" \
    || status="curl failed"
  check="$(curl -sS --max-time 60 -H "${auth}" -o /dev/null -w '%{http_code}' "${url}")" || check="curl failed"
  if [ "${check}" = "404" ]; then
    echo "${name}: gone (delete HTTP ${status})"
  else
    errors="$(python3 -c 'import json,sys; print([e.get("message") for e in json.load(open(sys.argv[1])).get("errors") or []])' "${body}" 2>/dev/null || true)"
    echo "::error::${name} is still deployed (delete HTTP ${status} ${errors}; check HTTP ${check}). Delete it by hand: Workers & Pages, ${name}, Settings, Delete."
    failed=1
  fi
  rm -f "${body}"
done

# Every Durable Object namespace whose script is one of these names must be gone.
list="$(mktemp)"
left="unread"
for attempt in 1 2 3 4 5 6 7; do
  status="$(curl -sS --max-time 60 -H "${auth}" -o "${list}" -w '%{http_code}' \
    "${api}/workers/durable_objects/namespaces?per_page=1000")" || status="curl failed"
  if [ "${status}" = "200" ]; then
    left="$(python3 - "${list}" "$@" <<'PY'
import json, sys
names = set(sys.argv[2:])
rows = json.load(open(sys.argv[1])).get("result") or []
print(" ".join(sorted(f"{r.get('script')}/{r.get('class')}" for r in rows if r.get("script") in names)))
PY
)"
    if [ -z "${left}" ]; then
      break
    fi
  fi
  if [ "${attempt}" != "7" ]; then
    sleep 10
  fi
done
rm -f "${list}"
if [ "${left}" = "unread" ]; then
  echo "::error::Could not read the Durable Object namespaces (HTTP ${status}), so their deletion is unproven."
  failed=1
elif [ -n "${left}" ]; then
  echo "::error::Durable Object namespaces still listed after the deletes: ${left}"
  failed=1
else
  echo "Durable Object namespaces: none left for $*"
fi
exit "${failed}"
