"""Receiver-owned installation selection, not public-definition authority."""

import sqlite3

import pytest

from tinyassets.commons_catalog import create_binding, publish_definition
from tinyassets.daemon_server import grant_universe_access, set_founder_home
from tinyassets.storage import db_path

OWNER, HOME = "receiver", "u-receiver"


def component():
    return {"kind": "tinyassets.turn-graph.v1", "version": 1,
            "branch_version_id": "bv-test", "content_hash": "a" * 64,
            "input_map": {"message": "message", "history": "context"}, "reply_key": "answer"}


@pytest.fixture
def store(tmp_path):
    (tmp_path / HOME).mkdir()
    set_founder_home(tmp_path, founder_sub=OWNER, universe_id=HOME, platform_generated=True)
    grant_universe_access(tmp_path, universe_id=HOME, actor_id=OWNER,
                          permission="admin", granted_by=OWNER)
    return tmp_path


def install(base, *, selected=True, source=None, role="app_experience"):
    definition = publish_definition(base, author_id="public-creator", payload={
        "schema_version": 1, "name": "Unit test definition", "description": "Test only",
        "tags": [], "components": {"turn": source or component()},
    })
    config = {"schema_version": 1, "name": "Receiver selection"}
    if role is not None:
        config["role"] = role
    if selected:
        config["turn_consumer"] = {
            "version": 1, "state": "active", "component_key": "turn",
            "definition_fingerprint": definition["content_fingerprint"],
        }
    binding = create_binding(
        base, universe_id=HOME, definition_id=definition["agent_definition_id"],
        created_by=OWNER, payload=config,
    )
    return binding, definition


def legacy_duplicate(base, binding):
    """A second active installation, as clients could create before the server
    refused one. Written straight to storage: ``create_binding`` no longer can,
    but rows like it may already exist, and admission must still refuse them."""
    with sqlite3.connect(db_path(base)) as conn:
        conn.execute(
            "INSERT INTO agent_bindings (agent_binding_id, universe_id, agent_definition_id, "
            "configuration_json, revision, status, created_by, updated_by, created_at, updated_at) "
            "SELECT agent_binding_id || '-legacy', universe_id, agent_definition_id, "
            "configuration_json, revision, status, created_by, updated_by, created_at, updated_at "
            "FROM agent_bindings WHERE agent_binding_id = ?",
            (binding["agent_binding_id"],),
        )




def selected(base):
    from tinyassets.consumer_selection import resolve_selection_in_transaction

    with sqlite3.connect(db_path(base)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        return resolve_selection_in_transaction(conn, owner=OWNER, universe=HOME)
























