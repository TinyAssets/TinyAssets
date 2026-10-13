"""Explicit test setup for the current serving-owner admission boundary."""

import sqlite3

from tinyassets.storage import DB_FILENAME


def seed_engine_authority(root, *, actor="actor-a", graph="u-a"):
    """Seed real public schemas, without replacing any existing serving creator."""
    from tinyassets.commons_catalog import create_binding, publish_definition
    from tinyassets.daemon_server import grant_universe_access

    definition = publish_definition(root, author_id=actor, payload={
        "schema_version": 1, "name": "Engine admission fixture", "description": "test",
        "tags": [], "components": {"identity": {"kind": "soul", "config": {}}},
    })
    with sqlite3.connect(root / DB_FILENAME) as conn:
        exists = conn.execute(
            "SELECT 1 FROM agent_bindings WHERE universe_id = ? AND status = 'serving'",
            (graph,),
        ).fetchone()
    if not exists:
        binding = create_binding(root, universe_id=graph,
            definition_id=definition["agent_definition_id"], created_by=actor,
            payload={"schema_version": 1, "name": "Engine admission fixture", "role": "writer"})
        with sqlite3.connect(root / DB_FILENAME) as conn:
            conn.execute("UPDATE agent_bindings SET status = 'serving' WHERE agent_binding_id = ?",
                         (binding["agent_binding_id"],))
    grant_universe_access(root, universe_id=graph, actor_id=actor,
                          permission="admin", granted_by=actor)


def seed_bound_engine(monkeypatch):
    """Create current authority for a fixture's explicit server pins."""
    from tinyassets import engine_mcp_server as server
    from tinyassets.storage import data_dir

    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    seed_engine_authority(data_dir(), actor=server._ACTOR_ID, graph=server._GRAPH_ID)


def mock_engine_admission(monkeypatch, graphs):
    """Handler-unit setup only; route and authority integration tests use SQLite.

    The handler's graph/actor pins and its downstream consent/ownership checks
    remain real. This replaces only the separately integration-tested admission
    query, never operation-specific authorization.
    """
    from tinyassets import engine_mcp_http as http
    from tinyassets import engine_mcp_server as server

    actor = server._ACTOR_ID
    monkeypatch.setattr(http, "engine_tools_authorized",
                        lambda *, actor_id, graph_id, root=None:
                        bool(actor_id) and actor_id == actor and graph_id in graphs)
