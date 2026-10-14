"""Existing graph authority behind the conversation-design installation, with real stores.

The installation is the receiver's own non-serving agent binding
(``role: app_experience``) that the app's custom-UI bridge writes when the
person approves a conversation design. These are installation controls; the
browser side is tests/test_app_consumer_controls.py.
"""

import copy
import json

import pytest

from tinyassets.api.commons_catalog import commons_catalog
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity


def actor(name):
    return identity_context(Identity(user_id=name, username=name, capabilities=["write"]))


TAG = "conversation-design-test"


def definition(name="Shared conversation design"):
    return {
        "schema_version": 1, "name": name, "tags": [TAG],
        "components": {"turn": {
            "kind": "tinyassets.turn-graph.v1", "version": 1,
            "branch_version_id": "bv-shared", "content_hash": "a" * 64,
            "input_map": {"message": "question"}, "reply_key": "answer",
        }, "retained_component": {"kind": "future_renderer", "user_option": "preserve"}},
    }


def configuration(name):
    return {"schema_version": 1, "name": name, "role": "app_experience",
            "customizations": {"private_label": name}}


def publish(payload):
    result = commons_catalog(action="publish_agent", payload=payload)
    assert result.get("status") == "published", result
    return result["agent"]


def install(uid, did, config):
    result = commons_catalog(action="create_binding", universe_id=uid,
                           definition_id=did, payload=config)
    assert result.get("status") == "configured", result
    return result["binding"]


@pytest.fixture
def homes(tmp_path, monkeypatch):
    from tinyassets.daemon_server import grant_universe_access, set_founder_home

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    for owner in ("alice", "bob"):
        uid = "u-" + owner
        (tmp_path / uid).mkdir()
        set_founder_home(tmp_path, founder_sub=owner, universe_id=uid,
                         platform_generated=True)
        grant_universe_access(tmp_path, universe_id=uid, actor_id=owner,
                              permission="admin")
    return tmp_path


def test_other_owner_discovers_remixes_and_applies_without_copying_private_data(homes):
    from tinyassets import conversation_store

    with actor("alice"):
        source = publish(definition())
        private_source = install("u-alice", source["agent_definition_id"],
                                 configuration("alice-private-installation"))
    conversation_store.record_exchange(homes / "u-bob", "bob", "private question", "private answer")
    history = conversation_store.load_recent_readonly(homes / "u-bob", "bob")
    memory = homes / "u-bob" / "private-memory.md"
    memory.write_text("bob-private-memory-verbatim\n", encoding="utf-8")

    with actor("bob"):
        found = commons_catalog(action="list_agents", tags=[TAG])
        assert source["agent_definition_id"] in {a["agent_definition_id"] for a in found["agents"]}
        assert commons_catalog(action="get_binding", universe_id="u-alice",
                             binding_id=private_source["agent_binding_id"])["error"] == "not_found"
        private_config = configuration("bob-private-installation")
        installed = install("u-bob", source["agent_definition_id"], private_config)
        remix = copy.deepcopy(source["portable_definition"])
        remix.pop("content_fingerprint", None)
        remix["name"] = "My remix"
        remix["components"]["turn"]["reply_key"] = "reply"
        remix["lineage"] = {key: [{"definition_id": source["agent_definition_id"],
                                  "component_key": key, "credit_share": 1.0}]
                             for key in remix["components"]}
        child = publish(remix)
        changed = commons_catalog(action="update_binding", universe_id="u-bob",
                                binding_id=installed["agent_binding_id"],
                                expected_revision=installed["revision"],
                                definition_id=child["agent_definition_id"],
                                payload=copy.deepcopy(installed["configuration"]))
        assert changed.get("status") == "configured", changed
        readback = commons_catalog(action="get_binding", universe_id="u-bob",
                                 binding_id=installed["agent_binding_id"])["binding"]
        assert readback["configuration"] == private_config
        assert readback["agent_definition_id"] == child["agent_definition_id"]
        assert readback["revision"] == installed["revision"] + 1
        assert readback["status"] == "configured"
        portable = child["portable_definition"]
        assert (portable["components"]["retained_component"]
                == remix["components"]["retained_component"])
        encoded = json.dumps(portable)
        assert all(sentinel not in encoded for sentinel in (
            "alice-private-installation", "bob-private-installation", "private question",
            "private answer", "bob-private-memory", installed["agent_binding_id"],
        ))

    assert conversation_store.load_recent_readonly(homes / "u-bob", "bob") == history
    assert memory.read_text(encoding="utf-8") == "bob-private-memory-verbatim\n"


def test_revision_conflict_and_foreign_mutation_leave_current_installation(homes):
    with actor("alice"):
        source = publish(definition())
    with actor("bob"):
        binding = install("u-bob", source["agent_definition_id"], configuration("private"))
        args = {"action": "update_binding", "universe_id": "u-bob",
                "binding_id": binding["agent_binding_id"], "expected_revision": 1,
                "definition_id": source["agent_definition_id"],
                "payload": configuration("new private value")}
        assert commons_catalog(**args)["status"] == "configured"
        assert commons_catalog(**args)["error"] == "agent_conflict"
    with actor("alice"):
        assert "error" in commons_catalog(**{**args, "expected_revision": 2})
    with actor("bob"):
        current = commons_catalog(action="get_binding", universe_id="u-bob",
                                binding_id=binding["agent_binding_id"])["binding"]
        assert current["revision"] == 2
        assert current["configuration"] == configuration("new private value")


def test_two_first_creates_race_and_exactly_one_installation_exists(homes):
    """Two tabs that both read "no installation yet" both try to create one.

    The server makes the create its own compare-and-set: one wins, the other is
    a conflict, so turn admission is never left with two to choose between.
    """
    import threading

    from tinyassets.commons_catalog import list_bindings

    with actor("alice"):
        source = publish(definition())
    start = threading.Barrier(2)
    results = []

    def create():
        with actor("bob"):
            start.wait()
            results.append(commons_catalog(
                action="create_binding", universe_id="u-bob",
                definition_id=source["agent_definition_id"],
                payload=configuration("tab")))

    threads = [threading.Thread(target=create) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(r.get("status") or r.get("error") for r in results) == [
        "agent_conflict", "configured"], results
    rows = [b for b in list_bindings(homes, universe_id="u-bob", limit=1000)
            if b["configuration"].get("role") == "app_experience"]
    assert len(rows) == 1, rows

    # Sequentially too: a second create of the role is refused by name.
    with actor("bob"):
        again = commons_catalog(action="create_binding", universe_id="u-bob",
                              definition_id=source["agent_definition_id"],
                              payload=configuration("later"))
    assert again["error"] == "agent_conflict"
    assert "app_experience" in again["detail"]


def test_the_singleton_is_per_owner_and_role_not_global(homes):
    with actor("alice"):
        source = publish(definition())
        mine = install("u-alice", source["agent_definition_id"], configuration("alice"))
    with actor("bob"):
        theirs = install("u-bob", source["agent_definition_id"], configuration("bob"))
        other_role = {"schema_version": 1, "name": "plain"}
        first = install("u-bob", source["agent_definition_id"], other_role)
        second = install("u-bob", source["agent_definition_id"], other_role)
    assert len({mine["agent_binding_id"], theirs["agent_binding_id"],
                first["agent_binding_id"], second["agent_binding_id"]}) == 4


def test_nonserving_installation_preserves_real_serving_authority(tmp_path, monkeypatch):
    from tests.test_open_serving_bind import _bound_and_serving
    from tinyassets.daemon_server import grant_universe_access
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_serving_binding import serving_connection_is_current

    universe, serving, _ = _bound_and_serving(tmp_path, monkeypatch)
    grant_universe_access(tmp_path, universe_id="u-owner", actor_id="owner-1",
                          permission="admin")
    before = load_provider_assignment(tmp_path, universe_id="u-owner")
    with actor("owner-1"):
        source = publish(definition())
        mine = install("u-owner", source["agent_definition_id"], configuration("My design"))
        assert mine["agent_binding_id"] != serving["agent_binding_id"]
        assert "provider_ref" not in mine["configuration"]
        assert mine["status"] == "configured"
        assert serving_connection_is_current(tmp_path, universe_dir=universe,
                                             universe_id="u-owner", owner_user_id="owner-1")
    assert load_provider_assignment(tmp_path, universe_id="u-owner") == before
