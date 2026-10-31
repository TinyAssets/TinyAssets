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
# ta-facetprobe is left anywhere on the account (every list read page by page; the lists
# may lag, so they are retried for a minute; the Worker list, which is not paged, in one
# read), and that the door's Workflow answers 404.
# Exits 1 if anything remains or a deletion cannot be proven, a list page that answers 200
# but does not parse among them.
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
rows="$(mktemp)"
trap 'rm -f "${body}" "${rows}"' EXIT

errors_of() {
  python3 -c 'import json,sys; print([e.get("message") for e in json.load(open(sys.argv[1])).get("errors") or []])' "$1" 2>/dev/null || true
}

# Every row of a list, page by page, into $2 (one JSON object a line). Returns 1 on a page
# that is not HTTP 200, one that does not parse, or more than 50 pages. An endpoint that
# ignores `page` repeats its rows, which ends the walk.
list_all() {
  : > "$2"
  local page=1 status more
  while [ "${page}" -le 50 ]; do
    status="$(curl -sS --max-time 60 -H "${auth}" -o "${body}" -w '%{http_code}' "${api}/$1?per_page=100&page=${page}")" \
      || status="curl failed"
    if [ "${status}" != "200" ]; then
      echo "list $1: HTTP ${status}"
      return 1
    fi
    more="$(python3 - "${body}" "$2" <<'PY'
import json, sys
body = json.load(open(sys.argv[1]))
rows = body.get("result")
if not isinstance(rows, list):
    raise SystemExit(1)
seen = set(open(sys.argv[2]).read().splitlines())
fresh = [line for line in (json.dumps(r, sort_keys=True) for r in rows) if line not in seen]
with open(sys.argv[2], "a") as out:
    out.writelines(line + "\n" for line in fresh)
info = body.get("result_info") or {}
last = info.get("total_pages") is not None and int(info.get("page") or 0) >= int(info["total_pages"])
print("no" if not fresh or len(rows) < 100 or last else "yes")
PY
)" || { echo "list $1: page ${page} answered 200 but did not parse"; return 1; }
    if [ "${more}" != "yes" ]; then
      return 0
    fi
    page=$((page + 1))
  done
  echo "list $1: more than 50 pages"
  return 1
}

# The Worker list in one read, with no paging parameters (it is not paged, and nothing in the
# repo has sent it any), into $2 as list_all writes. Returns 1 if it is not HTTP 200, does
# not parse, or its result_info says there is more than this page.
list_once() {
  : > "$2"
  local status
  status="$(curl -sS --max-time 60 -H "${auth}" -o "${body}" -w '%{http_code}' "${api}/$1")" || status="curl failed"
  if [ "${status}" != "200" ]; then
    echo "list $1: HTTP ${status}"
    return 1
  fi
  python3 - "${body}" "$2" <<'PY' || { echo "list $1: answered 200 but did not parse, or says it has more pages"; return 1; }
import json, sys
body = json.load(open(sys.argv[1]))
rows = body.get("result")
if not isinstance(rows, list):
    raise SystemExit(1)
info = body.get("result_info") or {}
pages = info.get("total_pages")
total = info.get("total_count")
if (pages is not None and int(pages) > 1) or (total is not None and int(total) > len(rows)):
    raise SystemExit(1)
with open(sys.argv[2], "w") as out:
    out.writelines(json.dumps(r, sort_keys=True) + "\n" for r in rows)
PY
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
if list_all storage/kv/namespaces "${rows}"; then
  if ! listed="$(python3 - "${rows}" "${prefix}" <<'PY'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
print(" ".join(r["id"] for r in rows if str(r.get("title", "")).startswith(sys.argv[2])))
PY
)"; then
    echo "::error::the KV namespace list did not parse"
    failed=1
  fi
  kv_ids="$(echo "${kv_ids} ${listed}" | tr ' ' '\n' | sort -u | tr '\n' ' ')"
elif [ -n "${kv_ids}" ]; then
  echo "::warning::the KV namespace list is unreadable; deleting this run's namespace by id only"
else
  echo "KV: the token cannot list namespaces and this run made none"
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

# 4. Nothing named ta-facetprobe* left: Workers, Durable Object namespaces, Workflows; and
# the door's Workflow itself answers 404.
left="unread"
for attempt in 1 2 3 4 5 6 7; do
  found=""
  readable=1
  for list in "workers/scripts" "workers/durable_objects/namespaces" "workflows"; do
    if [ "${list}" = "workers/scripts" ]; then
      list_once "${list}" "${rows}"
    else
      list_all "${list}" "${rows}"
    fi
    # shellcheck disable=SC2181
    if [ "$?" != "0" ]; then
      readable=0
      continue
    fi
    if ! names="$(python3 - "${rows}" "${prefix}" "${list}" <<'PY'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
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
)"; then
      echo "list ${list}: did not parse"
      readable=0
      continue
    fi
    found="${found} ${names}"
  done
  status="$(curl -sS --max-time 60 -H "${auth}" -o "${body}" -w '%{http_code}' "${api}/workflows/${workflow}")" || status="curl failed"
  if [ "${status}" = "200" ]; then
    found="${found} workflows:${workflow}"
  elif [ "${status}" != "404" ]; then
    echo "workflow ${workflow}: HTTP ${status}"
    readable=0
  fi
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
