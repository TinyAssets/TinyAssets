#!/usr/bin/env bash
# Deletes everything the facet probe makes and proves it is gone (facet-probe.yml runs it
# before deploying and again at the end, whatever happened):
#   1. the door's Workflow, ta-facetprobe-door, with its instances (their params and outputs);
#   2. each named Worker, force-deleted, so its Durable Object namespaces and every object's
#      storage, the facets' databases among them, go with it; a script never there (404)
#      counts as gone; name the door first, since it binds the others' classes, and the
#      dummy last, since the others bind it;
#   3. the dummy KV namespace: the id in $FACETPROBE_KV_FILE when this run made one, and any
#      namespace titled ta-facetprobe* that the token can list;
# then reads back that no Workflow, Worker or Durable Object namespace whose name starts with
# ta-facetprobe is left anywhere on the account (the lists may lag, so they are retried for a
# minute). Exits 1 if anything remains or a deletion cannot be proven.
#
#   CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... [FACETPROBE_KV_FILE=...] \
#       delete.sh ta-facetprobe-door ta-facetprobe ... ta-facetprobe-dummy
set -uo pipefail
api="${FACETPROBE_API_BASE:-https://api.cloudflare.com/client/v4}/accounts/${CLOUDFLARE_ACCOUNT_ID}"
auth="Authorization: Bearer ${CLOUDFLARE_API_TOKEN}"
prefix="ta-facetprobe"
workflow="ta-facetprobe-door"
failed=0
body="$(mktemp)"
trap 'rm -f "${body}"' EXIT

errors_of() {
  python3 -c 'import json,sys; print([e.get("message") for e in json.load(open(sys.argv[1])).get("errors") or []])' "$1" 2>/dev/null || true
}

# 1. The Workflow. A Workflow never made answers 404; step 4's list is the proof either way.
status="$(curl -sS --max-time 60 -X DELETE -H "${auth}" -o "${body}" -w '%{http_code}' "${api}/workflows/${workflow}")" \
  || status="curl failed"
echo "workflow ${workflow}: delete HTTP ${status} $(errors_of "${body}")"

# 2. The Workers.
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
    echo "::error::${name} is still deployed (delete HTTP ${status} ${errors}; check HTTP ${check}). Delete it by hand: Workers & Pages, ${name}, Settings, Delete."
    failed=1
  fi
done

# 3. The dummy KV namespace.
kv_ids=""
if [ -n "${FACETPROBE_KV_FILE:-}" ] && [ -s "${FACETPROBE_KV_FILE}" ]; then
  kv_ids="$(cat "${FACETPROBE_KV_FILE}")"
fi
status="$(curl -sS --max-time 60 -H "${auth}" -o "${body}" -w '%{http_code}' "${api}/storage/kv/namespaces?per_page=100")" \
  || status="curl failed"
if [ "${status}" = "200" ]; then
  listed="$(python3 - "${body}" "${prefix}" <<'PY'
import json, sys
rows = json.load(open(sys.argv[1])).get("result") or []
print(" ".join(r["id"] for r in rows if str(r.get("title", "")).startswith(sys.argv[2])))
PY
)"
  kv_ids="$(echo "${kv_ids} ${listed}" | tr ' ' '\n' | sort -u | tr '\n' ' ')"
elif [ -n "${kv_ids}" ]; then
  echo "::warning::the KV namespace list is unreadable (HTTP ${status}); deleting this run's namespace by id only"
else
  echo "KV: the token cannot list namespaces (HTTP ${status}) and this run made none"
fi
for id in ${kv_ids}; do
  status="$(curl -sS --max-time 60 -X DELETE -H "${auth}" -o "${body}" -w '%{http_code}' "${api}/storage/kv/namespaces/${id}")" \
    || status="curl failed"
  check="$(curl -sS --max-time 60 -H "${auth}" -o /dev/null -w '%{http_code}' "${api}/storage/kv/namespaces/${id}")" || check="curl failed"
  if [ "${check}" = "404" ]; then
    echo "KV namespace ${id}: gone (delete HTTP ${status})"
  else
    echo "::error::KV namespace ${id} is still there (delete HTTP ${status} $(errors_of "${body}"); check HTTP ${check}). Delete it by hand: Storage & Databases, KV, ${prefix}-kv."
    failed=1
  fi
done

# 4. Nothing named ta-facetprobe* left: Workers, Durable Object namespaces, Workflows.
left="unread"
for attempt in 1 2 3 4 5 6 7; do
  found=""
  readable=1
  for list in "workers/scripts" "workers/durable_objects/namespaces?per_page=1000" "workflows?per_page=100"; do
    status="$(curl -sS --max-time 60 -H "${auth}" -o "${body}" -w '%{http_code}' "${api}/${list}")" || status="curl failed"
    if [ "${status}" != "200" ]; then
      readable=0
      echo "list ${list%%\?*}: HTTP ${status}"
      continue
    fi
    found="${found} $(python3 - "${body}" "${prefix}" "${list%%\?*}" <<'PY'
import json, sys
rows = json.load(open(sys.argv[1])).get("result") or []
prefix, kind = sys.argv[2], sys.argv[3]
names = []
for r in rows:
    if kind == "workers/scripts":
        found = [r.get("id")]
    elif kind.endswith("namespaces"):
        found = [r.get("script"), r.get("name")]
    else:
        found = [r.get("name"), r.get("script_name")]
    if any(str(name or "").startswith(prefix) for name in found):
        names.append(f"{kind}:{'/'.join(str(n) for n in found if n)}")
print(" ".join(sorted(names)))
PY
)"
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
  echo "::error::Could not read every list (Workers, Durable Object namespaces, Workflows), so the cleanup is unproven."
  failed=1
elif [ -n "${left}" ]; then
  echo "::error::Still listed after the deletes: ${left}"
  failed=1
else
  echo "Workers, Durable Object namespaces and Workflows: none named ${prefix}* left"
fi
exit "${failed}"
