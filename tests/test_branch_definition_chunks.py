"""Recover a whole adapter through the actual model doors, without running it."""

import hashlib
import json

import pytest

from tests.test_branch_read_authority import (
    _seed_branch,
)
from tests.test_branch_read_authority import (
    branch_authority_env as branch_authority_env,
)
from tinyassets.engine_read_views import project_branch


def test_lossless_chunks_are_bounded_even_with_escaped_source_and_envelope():
    from tinyassets.engine_mcp_server import _untrusted

    document = {
        "branch_def_id": "b",
        "node_defs": [
            {
                "node_id": "store_note",
                "source_code": ('\\"\n雪😀\ud800' * 6000),
            }
        ],
    }
    offset, chunks, metadata = 0, [], set()
    while True:
        reply = project_branch(
            json.dumps(document),
            field_name="@definition",
            offset=offset,
            max_chars=32768,
            budget=2000,
            render_text=lambda text: _untrusted("branch:b by other", text),
        )
        assert len(reply.encode()) <= 2000
        wrapped = json.loads(reply)
        assert wrapped["untrusted"] is True
        page = wrapped["content"]
        assert page["offset"] == offset
        assert page["offset_unit"] == "unicode_code_points"
        metadata.add((page["sha256"], page["total_chars"], page["total_utf8_bytes"]))
        chunks.append(page["chunk"])
        if page["complete"]:
            assert page["next_offset"] is None
            break
        assert page["next_offset"] == offset + len(page["chunk"]) > offset
        offset = page["next_offset"]
    text = "".join(chunks)
    assert metadata == {(hashlib.sha256(text.encode()).hexdigest(), len(text), len(text.encode()))}
    assert json.loads(text) == document


@pytest.mark.parametrize(
    "kwargs",
    [
        {"offset": -1},
        {"offset": True},
        {"offset": 9999},
        {"max_chars": 0},
        {"max_chars": 32769},
        {"max_chars": True},
        {"field_name": "source_code"},
        {"budget": 1},
    ],
)
def test_invalid_chunk_requests_are_explicit(kwargs):
    arguments = dict(field_name="@definition", offset=0, max_chars=100, budget=2000)
    arguments.update(kwargs)
    result = json.loads(
        project_branch('{"branch_def_id":"b"}', render_text=lambda text: text, **arguments)
    )
    assert "error" in result and "chunk" not in result


def test_digest_detects_mutation_and_eof_is_an_empty_final_chunk():
    def read(value, offset=0):
        return json.loads(
            project_branch(
                json.dumps({"source": value}),
                offset=offset,
                field_name="@definition",
                budget=1000,
                render_text=lambda t: t,
            )
        )

    first, changed = read("before"), read("after!")
    assert first["sha256"] != changed["sha256"]
    eof = read("before", first["total_chars"])
    assert eof["complete"] and eof["next_offset"] is None and eof["chunk"] == ""


@pytest.mark.parametrize("door", ["connector", "engine"])
@pytest.mark.parametrize("author,visibility", [("alice", "private"), ("bob", "public")])
def test_served_read_recovers_real_stored_source(
    branch_authority_env, monkeypatch, door, author, visibility
):
    from tests.engine_authority_helpers import seed_bound_engine
    from tinyassets import engine_mcp_server as engine
    from tinyassets import universe_server as connector
    from tinyassets.daemon_server import save_branch_definition
    from tinyassets.engine_result_bounds import bound_tool_text, resolve_ceiling

    base, authenticate = branch_authority_env
    branch = _seed_branch(
        base,
        branch_def_id="adapter",
        author=author,
        visibility=visibility,
        node_ids=("store_note",),
    )
    source = "# never execute this adapter\n" + '雪😀\\"\n' * 6000
    branch["node_defs"][0]["source_code"] = source
    save_branch_definition(base, branch_def=branch)
    authenticate("alice")
    monkeypatch.setattr(engine, "_ACTOR_ID", "alice")
    monkeypatch.setattr(engine, "_GRAPH_ID", "u-alice")
    seed_bound_engine(monkeypatch)
    read = connector.read_graph if door == "connector" else engine.read_graph
    preview = json.loads(read(target="branch", branch_id="adapter"))
    preview = preview.get("content", preview)
    assert "complete" in preview, preview
    assert preview["complete"] and preview["next_offset"] is None
    assert preview["node_defs"][0]["source_code"] != source
    request = preview["definition_read_with"]
    chunks, hashes = [], set()
    while True:
        raw = read(**request, output_max_chars=32768)
        assert bound_tool_text(raw, tool="read_graph", limit=resolve_ceiling()) is None
        wrapped = json.loads(raw)
        if door == "engine" and author == "bob":
            assert wrapped["untrusted"] and wrapped["source"] == "branch:adapter by bob"
        page = wrapped.get("content", wrapped)
        chunks.append(page["chunk"])
        hashes.add(page["sha256"])
        if page["next_offset"] is None:
            break
        request["output_offset"] = page["next_offset"]
    complete = "".join(chunks)
    assert hashes == {hashlib.sha256(complete.encode()).hexdigest()}
    assert json.loads(complete)["node_defs"][0]["source_code"] == source


@pytest.mark.parametrize("door", ["connector", "engine"])
def test_foreign_private_definition_is_indistinguishable_from_missing(
    branch_authority_env, monkeypatch, door
):
    from tests.engine_authority_helpers import seed_bound_engine
    from tinyassets import engine_mcp_server as engine
    from tinyassets import universe_server as connector
    from tinyassets.daemon_server import delete_branch_definition

    base, authenticate = branch_authority_env
    _seed_branch(
        base, branch_def_id="private", author="bob", visibility="private", node_ids=("secret",)
    )
    authenticate("alice")
    monkeypatch.setattr(engine, "_ACTOR_ID", "alice")
    monkeypatch.setattr(engine, "_GRAPH_ID", "u-alice")
    seed_bound_engine(monkeypatch)
    read = connector.read_graph if door == "connector" else engine.read_graph
    arguments = dict(
        target="branch", branch_id="private", field_name="@definition", output_offset=10
    )
    denied = read(**arguments)
    delete_branch_definition(base, branch_def_id="private")
    assert denied == read(**arguments)
    assert "error" in json.loads(denied) and "sha256" not in denied and "chunk" not in denied
