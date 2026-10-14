"""REST output uses the real MCP reader, durable runs and confined API identities."""
import json

import pytest

from tests import test_rest_api_keys as key_tests
from tests.test_rest_api_keys import mint, scope

real = key_tests.real


def saved_run(base, output, *, center="home", owner="alice", completed=True):
    from tinyassets.runs import create_run, update_run_status

    rid = create_run(base, branch_def_id="test-output", thread_id="", inputs={},
                     actor=owner, owner_user_id=owner, queue_universe_id=center)
    if completed:
        update_run_status(base, rid, status="running")
        update_run_status(base, rid, status="completed", output=output)
    return rid


def endpoint(rid, center="home"):
    return f"/api/command-centers/{center}/runs/{rid}/output"


def reader(keys, authority, center="home", owner="alice"):
    return {"authorization": "Bearer " + mint(
        keys, authority, scope("read_graph", universe=center), owner=owner)}


def test_unicode_result_is_lossless_across_client_restart(real):
    from starlette.testclient import TestClient

    base, keys, authority, http = real
    result = json.dumps({"messages": [{"seq": 1, "text": "Hello 🌍 café 汉字" * 800}]},
                        ensure_ascii=False)
    rid = saved_run(base, {"result": result, "saved": False})
    auth = reader(keys, authority)
    first = http.get(endpoint(rid), headers=auth, params={"field_name": "result"})
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    page = first.json()
    assert page["status"] == "completed" and page["run_id"] == rid
    assert page["length"] == 8192 and "value" not in page
    assert page["offset_unit"] == "unicode_code_points" and page["encoding"] == "text"
    chunks = [page["chunk"]]
    http.close()
    with TestClient(http.app) as restarted:
        while page["next_offset"] is not None:
            response = restarted.get(endpoint(rid), headers=auth, params={
                "field_name": "result", "output_offset": page["next_offset"],
                "output_max_chars": 32768})
            assert response.status_code == 200, response.text
            page = response.json()
            assert "value" not in page
            chunks.append(page["chunk"])
        assert json.loads("".join(chunks)) == json.loads(result)


def test_catalog_typed_values_and_queued_status(real):
    base, keys, authority, http = real
    auth = reader(keys, authority)
    output = {f"f{i:02}": i for i in range(70)}
    output["structured"] = {"ok": True, "text": "🌍"}
    rid = saved_run(base, output)
    catalog = http.get(endpoint(rid), headers=auth).json()["output_catalog"]
    assert len(catalog["fields"]) == 64 and catalog["next_offset"] == 64
    assert "chunk" not in catalog and catalog["offset_unit"] == "fields"
    final = http.get(endpoint(rid), headers=auth, params={"output_offset": 64}).json()
    assert len(final["output_catalog"]["fields"]) == 7
    assert final["output_catalog"]["next_offset"] is None
    value = http.get(endpoint(rid), headers=auth, params={"field_name": "structured"}).json()
    assert value["value"] == json.loads(value["chunk"]) == output["structured"]
    assert value["encoding"] == "json"
    queued = saved_run(base, {}, completed=False)
    response = http.get(endpoint(queued), headers=auth)
    assert response.status_code == 200 and response.json()["status"] == "queued"
    assert response.json()["output_catalog"]["total_fields"] == 0


def test_center_binding_and_cross_owner_isolation(real):
    base, keys, authority, http = real
    work = saved_run(base, {"result": "alice-private"}, center="work")
    bobs = saved_run(base, {"result": "bob-private"}, center="bobs", owner="bob")
    auth = reader(keys, authority)
    for rid in (work, bobs, "missing"):
        response = http.get(endpoint(rid), headers=auth, params={"field_name": "result"})
        assert response.status_code == 404, response.text
        assert "private" not in response.text
    assert http.get(endpoint(work, "work"), headers=auth).status_code == 403
    assert http.get(endpoint(bobs, "bobs"), headers=auth).status_code == 403
    own = http.get(endpoint(work, "work"), headers=reader(keys, authority, "work"),
                   params={"field_name": "result"})
    assert own.status_code == 200 and own.json()["value"] == "alice-private"
    # Even a broad key belonging to another owner cannot read a private center.
    from tinyassets.api.visibility import set_universe_visibility

    set_universe_visibility("home", "private", source="owner")
    home = saved_run(base, {"result": "alice-private"})
    denied = http.get(endpoint(home), headers=reader(keys, authority, owner="bob"),
                      params={"field_name": "result"})
    assert denied.status_code in (400, 403, 404), denied.text
    assert "alice-private" not in denied.text


def test_read_grant_revocation_and_no_anonymous_access(real):
    base, keys, authority, http = real
    rid = saved_run(base, {"result": "private"})
    assert http.get(endpoint(rid)).status_code == 401
    runner = {"authorization": "Bearer " + mint(keys, authority, scope("run_graph"))}
    assert http.get(endpoint(rid), headers=runner).status_code == 403
    minted = keys.mint("alice", name="reader", scopes=[scope("read_graph")], authority=authority)
    auth = {"authorization": "Bearer " + minted["key"]}
    assert http.get(endpoint(rid), headers=auth).status_code == 200
    keys.revoke("alice", minted["key_id"], authority=authority)
    assert http.get(endpoint(rid), headers=auth).status_code == 401


@pytest.mark.parametrize("query", [
    "output_offset=-1", "output_offset=1.2", "output_offset=+1", "output_offset=",
    "output_offset=١", "output_offset=" + "9" * 100,
    "output_max_chars=0", "output_max_chars=32769", "output_max_chars=true",
    "field_name=result&field_name=other", "output_offset=0&output_offset=1",
    "query=/etc/passwd", "file_path=secret", "output_offset=2", "field_name=missing",
    "field_name=result&output_offset=9999",
])
def test_invalid_parameters_cannot_bypass_bounds_or_select_files(real, query):
    base, keys, authority, http = real
    rid = saved_run(base, {"result": "small"})
    response = http.get(endpoint(rid) + "?" + query, headers=reader(keys, authority))
    assert response.status_code == 400, response.text


def test_maximum_chunk_and_no_whole_value_leak(real):
    base, keys, authority, http = real
    rid = saved_run(base, {"result": "x" * 40000})
    response = http.get(endpoint(rid), headers=reader(keys, authority), params={
        "field_name": "result", "output_max_chars": 32768})
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["chunk"]) == data["next_offset"] == 32768
    assert data["truncated"] and "value" not in data
