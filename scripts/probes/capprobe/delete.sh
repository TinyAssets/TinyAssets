#!/usr/bin/env bash
# Force-deletes the named probe Workers and proves each is gone (cap-probe.yml runs it
# before deploying and again at the end). force=true deletes a script's Durable Object
# namespaces with it, so no object state outlives a run. A script that was never there
# (404) counts as gone. Exits 1 if any script is still deployed.
#
#   CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... delete.sh ta-capprobe ta-capprobe-callee
set -uo pipefail
failed=0
for name in "$@"; do
  url="https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/workers/scripts/${name}"
  body="$(mktemp)"
  status="$(curl -sS --max-time 60 -X DELETE -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
    -o "${body}" -w '%{http_code}' "${url}?force=true")" || status="curl failed"
  check="$(curl -sS --max-time 60 -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
    -o /dev/null -w '%{http_code}' "${url}")" || check="curl failed"
  if [ "${check}" = "404" ]; then
    echo "${name}: gone (delete HTTP ${status})"
  else
    errors="$(python3 -c 'import json,sys; print([e.get("message") for e in json.load(open(sys.argv[1])).get("errors") or []])' "${body}" 2>/dev/null || true)"
    echo "::error::${name} is still deployed (delete HTTP ${status} ${errors}; check HTTP ${check}). Delete it by hand: Workers & Pages, ${name}, Settings, Delete."
    failed=1
  fi
  rm -f "${body}"
done
exit "${failed}"
