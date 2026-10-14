"""Receiver-owned installation selection, not public-definition authority."""

import json
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


def test_create_refuses_a_second_installation(store):
    from tinyassets.commons_catalog import AgentConflictError

    install(store)
    with pytest.raises(AgentConflictError, match="app_experience"):
        install(store)


def selected(base):
    from tinyassets.consumer_selection import resolve_selection_in_transaction

    with sqlite3.connect(db_path(base)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        return resolve_selection_in_transaction(conn, owner=OWNER, universe=HOME)


def test_no_install_keeps_legacy_default(store):
    assert selected(store) is None


def test_public_definition_and_unselected_install_do_not_activate_handler(store):
    install(store, selected=False)
    assert selected(store) is None


def test_exact_receiver_install_selects_public_data_without_creator_authority(store):
    binding, definition = install(store)
    result = selected(store)
    assert result["binding_id"] == binding["agent_binding_id"]
    assert result["binding_revision"] == 1
    assert result["definition_id"] == definition["agent_definition_id"]
    assert result["branch_version_id"] == "bv-test"
    assert "public-creator" not in json.dumps(result)
    assert "provider" not in result


@pytest.mark.parametrize("sql", [
    "UPDATE agent_bindings SET updated_by='collaborator'",
    "UPDATE agent_bindings SET status='serving'",
    "UPDATE universe_acl SET permission='read'",
    "DELETE FROM founder_home",
])
def test_current_owner_and_nonserving_boundaries_hold(store, sql):
    install(store)
    with sqlite3.connect(db_path(store)) as conn:
        conn.execute(sql)
    with pytest.raises((PermissionError, RuntimeError)):
        selected(store)


def test_ambiguous_active_installations_do_not_pick_one(store):
    binding, _ = install(store)
    legacy_duplicate(store, binding)
    with pytest.raises(PermissionError, match="ambiguous"):
        selected(store)


def test_explicit_disable_does_not_load_or_require_old_source(store):
    binding, _ = install(store)
    config = binding["configuration"]
    config["turn_consumer"] = {"version": 1, "state": "disabled"}
    with sqlite3.connect(db_path(store)) as conn:
        conn.execute("UPDATE agent_bindings SET configuration_json=?", (json.dumps(config),))
        conn.execute("UPDATE agent_definitions SET components_json='broken'")
    assert selected(store) is None


def test_collaborator_cannot_silently_switch_owner_to_default(store):
    binding, _ = install(store)
    config = binding["configuration"]
    config["turn_consumer"] = {"version": 1, "state": "disabled"}
    with sqlite3.connect(db_path(store)) as conn:
        conn.execute("UPDATE agent_bindings SET configuration_json=?,updated_by='collaborator'",
                     (json.dumps(config),))
    with pytest.raises(PermissionError):
        selected(store)


def test_selection_cannot_carry_provider_reference(store):
    binding, _ = install(store)
    config = {**binding["configuration"], "provider_ref": None}
    with sqlite3.connect(db_path(store)) as conn:
        conn.execute("UPDATE agent_bindings SET configuration_json=?", (json.dumps(config),))
    with pytest.raises(PermissionError):
        selected(store)


@pytest.mark.parametrize("change", [{"kind": "foreign.loader"}, {"version": 2},
                                    {"url": "https://example.invalid/code"},
                                    {"input_map": {"message": "same", "history": "same"}}])
def test_unknown_or_ambiguous_public_component_is_inert_and_loud(store, change):
    install(store, source={**component(), **change})
    with pytest.raises(ValueError):
        selected(store)


def test_fingerprint_drift_is_held_not_silently_repaired(store):
    install(store)
    with sqlite3.connect(db_path(store)) as conn:
        conn.execute("UPDATE agent_definitions SET content_fingerprint=?", ("b" * 64,))
    with pytest.raises(PermissionError, match="fingerprint"):
        selected(store)


def test_a_hundred_unrelated_bindings_do_not_block_selection(store):
    """The 100-row cutoff is gone: selection resolves at any list size.

    ``resolve_selection_in_transaction`` used to fetch 101 rows and refuse with
    ``consumer installation list is ambiguous`` at 100 -- BEFORE filtering to the
    ``app_experience`` rows that actually select a consumer. So a hundred
    unrelated bindings stopped the owner's conversation from resolving at all.
    That is a functional cliff, not a limit (founder, 2026-09-30: no structural
    caps on what users build).
    """
    import inspect

    from tinyassets import consumer_selection

    src = inspect.getsource(consumer_selection.resolve_selection_in_transaction)
    assert "LIMIT 101" not in src
    assert "list is ambiguous" not in src

    binding, _definition = install(store)
    # 150 further bindings with no consumer role at all, well past the old cutoff.
    for _ in range(150):
        install(store, selected=False, role=None)

    chosen = selected(store)
    assert chosen is not None, "the owner's one selection is still found"
    assert chosen["binding_id"] == binding["agent_binding_id"]


def test_two_active_selections_are_still_ambiguous(store):
    """Mutation-check: removing the row cutoff must not remove the real check."""
    binding, _ = install(store)
    legacy_duplicate(store, binding)
    with pytest.raises(PermissionError, match="ambiguous"):
        selected(store)
