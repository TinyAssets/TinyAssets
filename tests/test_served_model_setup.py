"""Served setup uses the owner/home and preference boundaries, never grants."""

import asyncio
import json

import pytest

from tinyassets import engine_mcp_server as engine
from tinyassets.auth.middleware import current_identity


@pytest.fixture
def bound(monkeypatch):
    monkeypatch.setattr(engine, "_ACTOR_ID", "owner-setup")
    monkeypatch.setattr(engine, "_GRAPH_ID", "u-setup")
    monkeypatch.setattr(engine, "_engine_run_admit", lambda **kw: True)
    from tests.engine_authority_helpers import mock_engine_admission
    mock_engine_admission(monkeypatch, {"u-setup"})


@pytest.mark.parametrize("target", ["model_options", "agent_bindings", "agent_binding"])
def test_model_reads_pin_universe_and_restore_identity(bound, monkeypatch, target):
    seen = []
    before = current_identity()

    def read(**kwargs):
        seen.append((kwargs, current_identity().user_id))
        return json.dumps({"model_id": "untrusted catalogue text"})

    monkeypatch.setattr("tinyassets.universe_server.read_graph", read)
    # model_options is read from the shared domain dispatch and projected by the
    # engine (projecting the connector's projection would be a second bound), so
    # the pin and identity are asserted at whichever read the target reaches.
    monkeypatch.setattr("tinyassets.api.graph_reads.read_graph", read)
    selectors = {"agent_binding_id": "binding-x"} if target == "agent_binding" else {}
    output = engine.read_graph(target=target, **selectors)
    assert seen == [({"target": target, "graph_id": "u-setup", **selectors}, "owner-setup")]
    assert "untrusted catalogue text" in output
    if target == "model_options":
        assert "untrusted" in output.lower()
    assert current_identity() == before


def test_missing_binding_id_does_not_delegate(bound, monkeypatch):
    monkeypatch.setattr("tinyassets.universe_server.read_graph",
                        lambda **kw: pytest.fail("missing selector delegated"))
    assert "agent_binding_id" in json.loads(engine.read_graph(target="agent_binding"))["error"]


def test_unbound_model_reads_never_delegate(monkeypatch):
    monkeypatch.setattr(engine, "_ACTOR_ID", "")
    monkeypatch.setattr("tinyassets.universe_server.read_graph",
                        lambda **kw: pytest.fail("unbound read delegated"))
    for target in ("model_options", "agent_bindings", "agent_binding"):
        assert "refusing" in json.loads(engine.read_graph(target=target))["error"]


@pytest.fixture
def home(bound, tmp_path, monkeypatch):
    from tinyassets.daemon_server import grant_universe_access, set_founder_home

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("tinyassets.api.helpers._base_path", lambda: tmp_path)
    (tmp_path / "u-setup").mkdir()
    set_founder_home(tmp_path, founder_sub="owner-setup", universe_id="u-setup",
                     platform_generated=True)
    grant_universe_access(tmp_path, universe_id="u-setup", actor_id="owner-setup",
                          permission="admin")
    return tmp_path


AUTO = {"version": 1, "mode": "automatic", "saved_default": None, "fallbacks": []}
PIN = {"version": 1, "mode": "explicit", "fallbacks": [],
       "saved_default": {"provider_ref": "future-source", "model_id": "new-model"}}


def save(policy=AUTO, generation=0):
    return json.loads(engine.write_graph(
        target="model_preferences", operation="save",
        payload_json=json.dumps({"expected_generation": generation, "policy": policy}),
    ))


def test_real_preference_save_conflict_and_no_inference_grant(home):
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    first = save(PIN)
    assert first["generation"] == 1 and first["policy"] == PIN
    stale = save()
    assert stale["error"] == "model_preferences_conflict"
    assert stale["generation"] == 1 and stale["policy"] == PIN
    assert save(generation=1)["policy"] == AUTO
    with SQLiteProviderWorkAuthorityStore(home).connection() as conn:
        assert conn.execute("SELECT count(*) FROM provider_work_bindings").fetchone()[0] == 0


def test_changed_home_cannot_receive_old_pinned_preference(home):
    from tinyassets.storage.model_preferences import ModelPreferenceStore
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    with SQLiteProviderWorkAuthorityStore(home).connection() as conn:
        conn.execute("UPDATE founder_home SET universe_id=? WHERE founder_sub=?",
                     ("u-other", "owner-setup"))
    assert save()["error"] == "model_preference_home_changed"
    assert ModelPreferenceStore(home).get("owner-setup", "u-setup").generation == 0


def test_preference_home_change_between_validation_and_commit_is_refused(home, monkeypatch):
    from tinyassets import shared_self
    from tinyassets.storage.model_preferences import ModelPreferenceStore
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    require_home = shared_self.require_founder_home

    def move_after_check(base, universe_id, actor):
        path = require_home(base, universe_id, actor)
        with SQLiteProviderWorkAuthorityStore(home).connection() as conn:
            conn.execute("UPDATE founder_home SET universe_id=? WHERE founder_sub=?",
                         ("u-other", "owner-setup"))
        return path

    monkeypatch.setattr(shared_self, "require_founder_home", move_after_check)
    assert save()["error"] == "model_preference_home_changed"
    assert ModelPreferenceStore(home).get("owner-setup", "u-setup").generation == 0


def test_binding_read_uses_real_universe_scoped_storage(home):
    from tests.test_custom_agents import _binding, _definition
    from tinyassets.custom_agents import create_binding, publish_definition

    definition = publish_definition(
        home, author_id="owner-setup", payload=_definition("Setup fixture"),
    )
    bindings = [create_binding(home, universe_id=uid,
                              definition_id=definition["agent_definition_id"],
                              created_by="owner-setup", payload=_binding())
                for uid in ("u-setup", "u-other")]
    mine = json.loads(engine.read_graph(target="agent_binding",
                                       agent_binding_id=bindings[0]["agent_binding_id"]))
    assert mine["binding"]["agent_binding_id"] == bindings[0]["agent_binding_id"]
    foreign = json.loads(engine.read_graph(target="agent_binding",
                                          agent_binding_id=bindings[1]["agent_binding_id"]))
    assert foreign == {"error": "not_found", "resource": "agent_binding"}
    listed = json.loads(engine.read_graph(target="agent_bindings"))
    assert [b["agent_binding_id"] for b in listed["bindings"]] == [bindings[0]["agent_binding_id"]]


@pytest.mark.parametrize("target,operation,payload", [
    ("agent_binding", "bind_serving_provider", {}),
    ("connection", "connect_http", {}),
    ("connection", "configure_provider_capability", {"capability_kind": "voice"}),
    ("model_preferences", "delete", {}),
])
def test_setup_never_becomes_a_broad_owner_write(bound, target, operation, payload):
    assert "error" in json.loads(engine.write_graph(
        target=target, operation=operation, payload_json=json.dumps(payload),
    ))


def test_discovery_configuration_pins_owner_and_universe(bound, monkeypatch):
    seen = []
    document = {"capability_kind": "model_discovery", "enabled": False, "definition_id": "d"}

    def configure(**kwargs):
        seen.append((kwargs, current_identity().user_id, current_identity().capabilities))
        return {"status": "revoked", "grants_inference": False}

    monkeypatch.setattr(
        "tinyassets.api.provider_capability.configure_provider_capability", configure,
    )
    result = json.loads(engine.write_graph(target="connection",
                                          operation="configure_provider_capability",
                                          payload_json=json.dumps(document)))
    assert seen == [({"universe_id": "u-setup", "payload": document}, "owner-setup", ["write"])]
    assert result["grants_inference"] is False


@pytest.mark.parametrize("through_adapter", [False, True])
def test_canonical_preference_save_uses_same_home_and_generation(home, through_adapter):
    from tinyassets import universe_server
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity

    args = {"target": "model_preferences", "operation": "save",
            "payload_json": json.dumps({"expected_generation": 0, "policy": PIN})}
    with identity_context(Identity(user_id="owner-setup", username="owner-setup",
                                   capabilities=["write"])):
        if through_adapter:
            result = asyncio.run(universe_server.mcp.call_tool("write_graph", args))
            value = result.structured_content
            assert json.loads(result.content[0].text) == value
        else:
            raw = universe_server.write_graph(**args)
            assert isinstance(raw, str)
            value = json.loads(raw)
        # Through the MCP adapter the reply carries the public spelling (C1).
        key = "command_center_id" if through_adapter else "universe_id"
        assert value[key] == "u-setup"
        assert value["policy"] == PIN and value["generation"] == 1
        assert json.loads(universe_server.write_graph(**args))["error"] == (
            "model_preferences_conflict"
        )
        refused = json.loads(universe_server.write_graph(**args, graph_id="u-other"))
        assert refused["error"] == "model_preference_home_changed"
    # Shared and served ingress operate on the same document, not parallel stores.
    assert save(generation=1)["generation"] == 2


@pytest.mark.parametrize("payload", [
    '{"expected_generation":0,"expected_generation":1,"policy":null}',
    '{"expected_generation":true,"policy":{}}',
    '{"expected_generation":0,"policy":{},"actor":"other"}',
    '[]', 'null', 'not json',
])
def test_canonical_preferences_refuse_invalid_documents(home, payload):
    from tinyassets import universe_server
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.storage.model_preferences import ModelPreferenceStore

    with identity_context(Identity(user_id="owner-setup", username="owner-setup",
                                   capabilities=["write"])):
        result = json.loads(universe_server.write_graph(
            target="model_preferences", operation="save", payload_json=payload,
        ))
    assert result["error"] == "invalid_model_preferences"
    assert ModelPreferenceStore(home).get("owner-setup", "u-setup").generation == 0


def test_canonical_preferences_require_identity_and_exact_operation(home):
    from tinyassets import universe_server
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity

    with identity_context(None):
        assert json.loads(universe_server.write_graph(
            target="model_preferences", operation="save",
        ))["auth_required"] is True
    with identity_context(Identity(user_id="owner-setup", username="owner-setup",
                                   capabilities=["write"])):
        assert "only operation=save" in json.loads(universe_server.write_graph(
            target="model_preferences", operation="delete",
        ))["error"]
