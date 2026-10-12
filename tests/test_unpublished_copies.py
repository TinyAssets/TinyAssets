"""Unpublished copies stay out of the commons and show where they came from."""
from __future__ import annotations

import json
import sqlite3

from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition
from tinyassets.daemon_server import initialize_author_server, save_branch_definition


def _seed_public(base, *, branch_id: str, author: str, prompt: str) -> str:
    from tinyassets.branch_versions import publish_branch_version

    initialize_author_server(base)
    node = NodeDefinition(node_id="n1", display_name="N1", prompt_template=prompt)
    branch = BranchDefinition(
        branch_def_id=branch_id,
        name="Original shape",
        author=author,
        visibility="public",
        graph_nodes=[GraphNodeRef(id="n1", node_def_id="n1")],
        edges=[EdgeDefinition(from_node="n1", to_node="END")],
        entry_point="n1",
        node_defs=[node],
        state_schema=[],
    )
    save_branch_definition(base, branch_def=branch.to_dict())
    version = publish_branch_version(
        base, branch.to_dict(), publisher=author, public=True,
    )
    return version.branch_version_id


def test_private_remix_is_not_in_the_commons_and_shows_origin_and_changes(
    tmp_path, monkeypatch, authenticate_request,
):
    from tinyassets.api.branches import (
        _ext_branch_build,
        _ext_branch_describe,
        _ext_branch_get,
        _ext_branch_list,
    )

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    authenticate_request("alice")
    version_id = _seed_public(tmp_path, branch_id="original", author="alice", prompt="do X")
    authenticate_request("bob")
    spec = {
        "name": "Bob's remix",
        "fork_from": version_id,
        "description": "A private edit",
        "node_defs": [{
            "node_id": "n1",
            "display_name": "N1",
            "prompt_template": "do Y",
        }],
        "edges": [{"from": "n1", "to": "END"}],
        "entry_point": "n1",
    }
    built = json.loads(_ext_branch_build({"spec_json": json.dumps(spec)}))
    assert built["status"] == "built", built
    bid = built["branch_def_id"]
    assert built["origin"]["author"] == "alice"
    assert built["origin"]["branch_version_id"] == version_id
    assert built["credit_author"] == "alice"
    assert "node n1 prompt changed" in built["changes"]
    assert not (tmp_path / "ledger.json").exists()

    got = json.loads(_ext_branch_get({"branch_def_id": bid}))
    described = json.loads(_ext_branch_describe({"branch_def_id": bid}))
    listed = json.loads(_ext_branch_list({"scope": "published"}))
    assert got["origin"]["branch_def_id"] == "original"
    assert got["credit_author"] == "alice"
    assert got["author"] == "bob"
    assert "node n1 prompt changed" in got["changes"]
    assert "node n1 prompt changed" in described["changes"]
    assert "Origin:" in described["summary"]
    assert bid not in {row["branch_def_id"] for row in listed["branches"]}


def test_private_branch_write_stays_out_of_ledger_and_public_write_lands(
    tmp_path, monkeypatch, authenticate_request,
):
    from tinyassets.api.branches import _dispatch_branch_action, _ext_branch_build

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    authenticate_request("alice")
    private = json.loads(_dispatch_branch_action(
        "build_branch",
        _ext_branch_build,
        {"spec_json": json.dumps({
            "name": "Private draft",
            "node_defs": [{
                "node_id": "n1", "display_name": "N1", "prompt_template": "hi",
            }],
            "edges": [{"from": "n1", "to": "END"}],
            "entry_point": "n1",
        })},
    ))
    assert private["status"] == "built"
    assert not (tmp_path / "ledger.json").exists()

    public = json.loads(_dispatch_branch_action(
        "build_branch",
        _ext_branch_build,
        {"spec_json": json.dumps({
            "name": "Public shape",
            "visibility": "public",
            "node_defs": [{
                "node_id": "n1", "display_name": "N1", "prompt_template": "hi",
            }],
            "edges": [{"from": "n1", "to": "END"}],
            "entry_point": "n1",
        })},
    ))
    assert public["status"] == "built"
    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert [entry["action"] for entry in ledger] == ["build_branch"]
    assert ledger[0]["target"] == public["branch_def_id"]


def test_get_ledger_refuses_the_base_folder(tmp_path, monkeypatch):
    from tinyassets.api.universe import _action_get_ledger

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    (tmp_path / "ledger.json").write_text("[]", encoding="utf-8")
    refused = json.loads(_action_get_ledger(universe_id="."))
    assert refused["error"] == "Invalid universe_id."
    assert "entries" not in refused


def test_import_agent_stages_before_publishing(tmp_path, monkeypatch, authenticate_request):
    from tinyassets.api.custom_agents import custom_agents
    from tinyassets.custom_agents import list_definitions, publish_definition
    from tinyassets.storage import db_path

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    bundle_id = "bundle_" + ("ab" * 16)
    authenticate_request("alice")
    published = publish_definition(
        tmp_path,
        author_id="alice",
        payload={
            "schema_version": 1,
            "name": "Original",
            "description": "The original words",
            "tags": ["agent"],
            "bundle_id": bundle_id,
            "components": {
                "identity": {"kind": "soul", "config": {"voice": "clear"}},
            },
        },
    )
    portable = published["portable_definition"]
    modified = json.loads(json.dumps(portable))
    modified.pop("content_fingerprint", None)
    modified["description"] = "Bob's wording"
    modified["lineage"] = {
        "identity": [{
            "definition_id": published["agent_definition_id"],
            "component_key": "identity",
            "credit_share": 1.0,
        }],
    }

    def bundle_count() -> int:
        with sqlite3.connect(db_path(tmp_path)) as conn:
            return conn.execute("SELECT COUNT(*) FROM commons_bundles").fetchone()[0]

    before = bundle_count()
    authenticate_request(None)
    anonymous = custom_agents(action="import_agent", payload=modified)
    assert anonymous["error"] == "authentication_required"

    authenticate_request("bob")
    staged = custom_agents(
        action="import_agent",
        payload=modified,
        idempotency_key="bob-remix",
    )
    assert staged["status"] == "staged"
    stage = staged["stage"]
    assert stage["owner_id"] == "bob"
    assert stage["credit_author"] == "alice"
    assert stage["origin"]["agent_definition_id"] == published["agent_definition_id"]
    assert stage["origin"]["author"] == "alice"
    assert "description changed" in stage["changes"]
    assert bundle_count() == before
    assert [row["author_id"] for row in list_definitions(tmp_path)] == ["alice"]

    again = custom_agents(
        action="import_agent",
        payload=modified,
        idempotency_key="bob-remix",
    )
    assert again["stage"]["copy_id"] == stage["copy_id"]
    published_copy = custom_agents(
        action="publish_stage",
        stage_id=stage["copy_id"],
        idempotency_key="bob-publish",
    )
    assert published_copy["status"] == "published"
    assert published_copy["agent"]["author_id"] == "bob"
    authors = sorted(row["author_id"] for row in list_definitions(tmp_path))
    assert authors == ["alice", "bob"]
    assert bundle_count() == before + 1
