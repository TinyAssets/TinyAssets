"""The live /mcp surface advertises exactly the seven canonical public tools.

The legacy fat tools (universe, extensions, goals, gates, wiki) are no longer
registered at all (2026-09-30): hidden-but-dispatchable was a route to another
user's private branch history.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from pydantic import ValidationError as PydanticValidationError

import tinyassets.universe_server as universe_server
from tinyassets.universe_server import (
    mcp,
    read_graph,
    write_graph,
)

LEGACY_FAT_TOOLS = frozenset({"universe", "extensions", "goals", "gates", "wiki"})

CANONICAL_PUBLIC_TOOLS = {
    "read_graph",
    "write_graph",
    "run_graph",
    "read_page",
    "write_page",
    "converse",  # 2026-07-02 relay reshape: chatbot -> universe intelligence
    "get_status",
}

EXPECTED_ANNOTATIONS = {
    "read_graph": {"readOnlyHint": True, "idempotentHint": True},
    "write_graph": {"readOnlyHint": False, "openWorldHint": True},
    "run_graph": {"readOnlyHint": False, "openWorldHint": True},
    "read_page": {"readOnlyHint": True, "idempotentHint": True},
    "write_page": {"readOnlyHint": False, "openWorldHint": True},
    "converse": {"readOnlyHint": False, "openWorldHint": True},
    "get_status": {"readOnlyHint": True, "idempotentHint": True},
}


def _advertised_tools():
    """tools/list as a real MCP client sees it (middleware applied)."""
    return asyncio.run(mcp.list_tools(run_middleware=True))


def _registered_tools():
    """Every tool registered on the server (middleware bypassed)."""
    return asyncio.run(mcp.list_tools(run_middleware=False))


def test_live_surface_advertises_exactly_canonical_public_tools() -> None:
    advertised = {tool.name for tool in _advertised_tools()}
    assert advertised == CANONICAL_PUBLIC_TOOLS
    assert "converse" in advertised  # the relay handle is user-facing
    # No legacy fat tool leaks onto the advertised surface.
    assert LEGACY_FAT_TOOLS.isdisjoint(advertised)


def test_legacy_tools_are_not_registered_at_all() -> None:
    """Registered is what a client can DISPATCH, listed or not. Only the
    canonical handles remain."""
    registered = {tool.name for tool in _registered_tools()}
    assert registered == CANONICAL_PUBLIC_TOOLS
    assert LEGACY_FAT_TOOLS.isdisjoint(registered)


def test_handle_annotations_match_contract() -> None:
    tools = {tool.name: tool for tool in _advertised_tools()}
    for name, expected in EXPECTED_ANNOTATIONS.items():
        ann = tools[name].annotations
        for key, value in expected.items():
            assert getattr(ann, key) == value, f"{name}.{key}"


def test_converse_advertises_optional_turn_input_method() -> None:
    tool = next(tool for tool in _advertised_tools() if tool.name == "converse")
    properties = tool.parameters["properties"]
    method = properties["input_method"]

    assert "voice_active" not in properties
    assert method["type"] == "string"
    assert method["default"] == "unknown"
    assert set(method["enum"]) == {"typed", "spoken", "app_action", "unknown"}
    assert "specific turn" in method["description"]
    assert "never authority or consent" in method["description"]


def test_public_converse_boundary_rejects_replaced_voice_active_field() -> None:
    with pytest.raises(
        (FastMCPValidationError, PydanticValidationError),
        match="Unexpected keyword argument",
    ):
        asyncio.run(mcp.call_tool("converse", {"message": "hi", "voice_active": True}))


def test_write_graph_advertises_declarative_import_envelope() -> None:
    tool = next(tool for tool in _advertised_tools() if tool.name == "write_graph")
    description = tool.parameters["properties"]["payload_json"]["description"]

    assert "source_json and adapter are sibling top-level keys" in description
    assert "never nest source_json inside adapter" in description
    assert "agent-interchange-adapter/v1" in description
    assert "adapter_ref" in description
    assert "adapter_version" in description
    assert "rules" in description
    assert "copy, constant, namespace_preserve, or omit" in description
    assert "source_path" in description
    assert "target_path" in description
    assert "classification" in description
    assert "preserved or normalized" in description
    assert (
        "unsupported, omitted_secret, requires_private_binding, or requires_runtime"
        in description
    )
    assert "target_path writes must be unique and non-overlapping" in description
    assert (
        "credential or private source paths must use omit with "
        "omitted_secret or requires_private_binding"
        in description
    )
    assert "every source scalar or empty-container path exactly once" in description


def test_write_graph_advertises_agent_remix_lineage_contract() -> None:
    tool = next(tool for tool in _advertised_tools() if tool.name == "write_graph")
    description = tool.parameters["properties"]["payload_json"]["description"]

    assert "target=agent operation=publish or remix" in description
    assert "schema_version=1" in description
    assert "non-empty name" in description
    assert "lineage is keyed by each child component key" in description
    assert "definition_id, component_key, and credit_share" in description
    example = (
        '{"lineage":{"x":[{"definition_id":"agent_1",'
        '"component_key":"x","credit_share":1}]}}'
    )
    assert example in description
    assert json.loads(example)["lineage"]["x"] == [
        {
            "definition_id": "agent_1",
            "component_key": "x",
            "credit_share": 1,
        }
    ]
    assert "Never pass a single object as a lineage value" in description
    assert "Credit shares for one child component must total at most 1" in description
    assert (
        "definition_fingerprint and component_fingerprint must be supplied together"
        in description
    )


def test_write_graph_advertises_private_binding_contract() -> None:
    tool = next(tool for tool in _advertised_tools() if tool.name == "write_graph")
    description = tool.parameters["properties"]["payload_json"]["description"]

    assert "target=agent_binding operation=bind or update" in description
    assert "schema_version=1 and a non-empty name" in description
    assert "binding JSON is private" in description
    assert "never put provider, resource, or channel references" in description
    assert "public agent definition or export" in description


def test_read_graph_status_returns_operator_status() -> None:
    payload = json.loads(read_graph(target="status"))
    assert "schema_version" in payload


def test_unknown_target_is_reported() -> None:
    payload = json.loads(read_graph(target="bogus"))
    assert payload["error"] == "unknown_target"
    assert payload["handle"] == "read_graph"
    assert {
        "agents",
        "agent",
        "agent_bindings",
        "agent_binding",
    } <= set(payload["allowed_targets"])


def test_custom_agent_reads_route_through_graph_handle(monkeypatch) -> None:
    observed: list[dict[str, object]] = []

    def fake_custom_agents(**kwargs):
        observed.append(kwargs)
        return {"routed": kwargs["action"]}

    # Raw public reads stay complete through the domain door; connector public
    # reads now project metadata (covered by test_agent_read_pages). Private
    # stage/binding reads still delegate unchanged from the connector.
    from tinyassets.api.graph_reads import read_graph as complete_read

    # Reads dispatch through the shared domain read.
    # Patched by import path, not a module object held here: a test that reloads
    # tinyassets modules earlier in the run leaves a stale object behind, and the
    # connector resolves the dispatch from sys.modules on every call.
    monkeypatch.setattr("tinyassets.api.graph_reads._custom_agents_impl", fake_custom_agents)

    listed = json.loads(
        complete_read(target="agents", query="coding", tags="agent,coding", limit=5)
    )
    exact = json.loads(
        complete_read(target="agent", agent_definition_id="agent_123")
    )
    stage = json.loads(
        read_graph(target="agent", agent_stage_id="agent_stage_123")
    )
    bindings = json.loads(
        read_graph(target="agent_bindings", graph_id="universe-a", limit=7)
    )
    binding = json.loads(
        read_graph(
            target="agent_binding",
            graph_id="universe-a",
            agent_binding_id="agent_binding_123",
        )
    )

    assert [listed, exact, stage, bindings, binding] == [
        {"routed": "list_agents"},
        {"routed": "get_agent"},
        {"routed": "get_import_stage"},
        {"routed": "list_bindings"},
        {"routed": "get_binding"},
    ]
    assert observed[0]["query"] == "coding"
    assert observed[1]["definition_id"] == "agent_123"
    assert observed[2]["stage_id"] == "agent_stage_123"
    assert observed[3]["universe_id"] == "universe-a"
    assert observed[4]["binding_id"] == "agent_binding_123"


def test_custom_agent_writes_route_through_graph_handle(monkeypatch) -> None:
    observed: list[dict[str, object]] = []

    def fake_custom_agents(**kwargs):
        observed.append(kwargs)
        return {"routed": kwargs["action"]}

    monkeypatch.setattr(
        universe_server,
        "_custom_agents_impl",
        fake_custom_agents,
        raising=False,
    )
    monkeypatch.setattr(universe_server, "write_gate_rejection", lambda name: None)

    published = json.loads(
        write_graph(
            target="agent",
            operation="remix",
            payload_json='{"schema_version":1}',
            idempotency_key="agent-publish-request-1",
        )
    )
    imported = json.loads(
        write_graph(
            target="agent",
            operation="import",
            payload_json='{"schema_version":1}',
        )
    )
    staged = json.loads(
        write_graph(
            target="agent",
            operation="stage_import",
            payload_json='{"source_json":{},"adapter":{}}',
            idempotency_key="agent-stage-request-1",
        )
    )
    published_stage = json.loads(
        write_graph(
            target="agent",
            operation="publish_stage",
            agent_stage_id="agent_stage_123",
            idempotency_key="agent-stage-publish-1",
        )
    )
    exported = json.loads(
        write_graph(
            target="agent",
            operation="convert_export",
            agent_definition_id="agent_123",
            payload_json='{"adapter_ref":"commons:foreign-export"}',
            idempotency_key="agent-export-1",
        )
    )
    bound = json.loads(
        write_graph(
            target="agent_binding",
            operation="bind",
            graph_id="universe-a",
            agent_definition_id="agent_123",
            payload_json='{"schema_version":1}',
        )
    )
    updated = json.loads(
        write_graph(
            target="agent_binding",
            operation="update",
            graph_id="universe-a",
            agent_definition_id="agent_456",
            agent_binding_id="agent_binding_123",
            expected_revision=4,
            payload_json='{"schema_version":1}',
        )
    )

    assert [published, imported, staged, published_stage, exported, bound, updated] == [
        {"routed": "publish_agent"},
        {"routed": "import_agent"},
        {"routed": "stage_import"},
        {"routed": "publish_stage"},
        {"routed": "convert_export"},
        {"routed": "create_binding"},
        {"routed": "update_binding"},
    ]
    assert observed[0]["idempotency_key"] == "agent-publish-request-1"
    assert observed[2]["idempotency_key"] == "agent-stage-request-1"
    assert observed[3]["stage_id"] == "agent_stage_123"
    assert observed[4]["definition_id"] == "agent_123"
    assert observed[5]["definition_id"] == "agent_123"
    assert observed[6]["binding_id"] == "agent_binding_123"
    assert observed[6]["expected_revision"] == 4


def test_unknown_custom_agent_operation_is_reported(monkeypatch) -> None:
    monkeypatch.setattr(universe_server, "write_gate_rejection", lambda name: None)

    payload = json.loads(
        write_graph(target="agent", operation="overwrite-in-place")
    )

    assert payload["error"] == "unknown_agent_operation"
    assert payload["target"] == "agent"
    assert payload["allowed_operations"] == [
        "publish",
        "remix",
        "import",
        "stage_import",
        "publish_stage",
        "convert_export",
    ]


def test_custom_agent_definition_and_binding_round_trip(monkeypatch, tmp_path) -> None:
    from tinyassets.api import permissions
    from tinyassets.daemon_server import grant_universe_access

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(universe_server, "write_gate_rejection", lambda name: None)
    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: True)
    monkeypatch.setattr(permissions, "current_actor_id", lambda: "alice")

    definition_payload = {
        "schema_version": 1,
        "name": "Connector coding agent",
        "description": "Creates and tests user-authored branches.",
        "tags": ["coding", "agent"],
        "components": {
            "identity": {
                "kind": "soul",
                "config": {"instructions": "Work in small verified steps."},
            },
            "workflow": {
                "kind": "branch_set",
                "config": {"refs": ["branch-test-and-iterate"]},
            },
        },
    }
    published = json.loads(
        write_graph(
            target="agent",
            operation="publish",
            payload_json=json.dumps(definition_payload),
            idempotency_key="connector-agent-round-trip",
        )
    )
    definition_id = published["agent"]["agent_definition_id"]

    public = json.loads(
        read_graph(target="agent", agent_definition_id=definition_id)
    )
    assert public["agent"]["component_count"] == 2
    component = json.loads(read_graph(target="agent", agent_definition_id=definition_id,
                                     field_name="workflow"))
    assert component["complete"] is True
    assert json.loads(component["chunk"])["kind"] == "branch_set"

    grant_universe_access(
        tmp_path,
        universe_id="universe-a",
        actor_id="alice",
        permission="admin",
        granted_by="alice",
    )
    binding_payload = {
        "schema_version": 1,
        "name": "My connector coding agent",
        "role": "Maintain the test-and-iterate loop",
        "resources": {
            "github": {"resource_binding_id": "resource-github-1"},
        },
        "channels": {
            "slack": {
                "adapter_ref": "commons:slack",
                "address_ref": "channel-address-1",
            }
        },
    }
    bound = json.loads(
        write_graph(
            target="agent_binding",
            operation="bind",
            graph_id="universe-a",
            agent_definition_id=definition_id,
            payload_json=json.dumps(binding_payload),
        )
    )
    binding_id = bound["binding"]["agent_binding_id"]

    private = json.loads(
        read_graph(
            target="agent_binding",
            graph_id="universe-a",
            agent_binding_id=binding_id,
        )
    )
    assert private["binding"]["status"] == "configured"
    assert private["binding"]["configuration"]["channels"]["slack"] == {
        "adapter_ref": "commons:slack",
        "address_ref": "channel-address-1",
    }


def test_goal_write_and_read_round_trip(monkeypatch, tmp_path) -> None:
    """write_graph(goal) routes to the same handler read_graph(goals) reads."""
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "canonical-handle-test")

    from tinyassets.catalog import invalidate_backend_cache

    invalidate_backend_cache()
    try:
        proposed = json.loads(
            write_graph(
                target="goal",
                name="Canonical handle smoke goal",
                tags="pr178,smoke",
                visibility="public",
            )
        )
        assert proposed["status"] == "proposed"

        searched = json.loads(
            read_graph(target="goals", query="Canonical handle smoke")
        )
        assert searched["count"] >= 1
        assert any(
            goal["goal_id"] == proposed["goal"]["goal_id"]
            for goal in searched["goals"]
        )
    finally:
        invalidate_backend_cache()

