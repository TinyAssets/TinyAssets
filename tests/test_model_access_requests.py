"""The person-driven rail composes model setup without agent self-consent."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tests.test_provider_serving_binding import _seed_universe
from tinyassets.api import pending_requests as requests
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.provider_assignment import load_provider_assignment
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.storage.pending_requests import get_request


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from tinyassets.daemon_server import grant_universe_access, set_founder_home

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    universe, binding = _seed_universe(tmp_path)
    set_founder_home(tmp_path, founder_sub="owner-1", universe_id="u-owner",
                     platform_generated=True)
    grant_universe_access(tmp_path, universe_id="u-owner", actor_id="owner-1", permission="admin")
    # Exercise real assignment/custody/readiness logic with a deterministic
    # executor boundary, not whichever subscription CLI the test host installed.
    monkeypatch.setattr("tinyassets.providers.call.get_provider_router", lambda: SimpleNamespace(
        _providers={"codex": SimpleNamespace(is_available=lambda: True)},
    ))
    return tmp_path, universe, binding


@contextmanager
def owner():
    with identity_context(Identity(user_id="owner-1", username="owner", capabilities=["write"])):
        yield


def ask(rig, **changes):
    action = {"type": "bind_model_access", "agent_binding_id": rig[2]["agent_binding_id"],
              "expected_revision": rig[2]["revision"], "provider": "codex",
              "model_access": {"codex": ModelAccess("explicit", ("",)).document()}}
    action.update(changes)
    with owner():
        return requests.request_from_user(universe_id="u-owner", payload={
            "kind": "Models", "title": "Choose models", "body": "Use my connected subscription",
            "action": action, "fields": [],
        })


def answer(row):
    with owner():
        return requests.answer_request(universe_id="u-owner", payload={
            "request_id": row["request_id"], "values": {}, "dont_ask_again": True,
        })


def test_model_request_is_non_authorizing_until_owner_answers(rig):
    row = ask(rig)
    assert row["status"] == "pending", row
    assert load_provider_assignment(rig[0], universe_id="u-owner") is None
    assert "reconnects with this access" in row["grant_sentence"]
    assert "free models only" in row["grant_sentence"]
    result = answer(row)
    assert result.get("status") == "answered", result
    assert get_request(rig[1], row["request_id"])["status"] == "answered"
    assignment = load_provider_assignment(rig[0], universe_id="u-owner")
    assert assignment.state == "ready" and assignment.generation == 1
    assert assignment.candidates[0].access == ModelAccess("explicit", ("",))


@pytest.mark.parametrize("router", [None, SimpleNamespace(_providers={}),
    SimpleNamespace(_providers={"codex": SimpleNamespace(is_available=lambda: False)})])
def test_missing_executor_keeps_approved_setup_pending(rig, monkeypatch, router):
    from tinyassets.custom_agents import get_binding

    monkeypatch.setattr("tinyassets.providers.call.get_provider_router", lambda: router)
    row = ask(rig)
    result = answer(row)
    assert result["error"] == "provider_authority_denied", result
    assert get_request(rig[1], row["request_id"])["status"] == "pending"
    assert load_provider_assignment(rig[0], universe_id="u-owner").state == "ready"
    binding = get_binding(rig[0], universe_id="u-owner", binding_id=rig[2]["agent_binding_id"])
    assert binding["status"] != "serving"


@pytest.mark.parametrize("change", [
    {"expected_revision": 99}, {"agent_binding_id": "foreign-binding"},
    {"provider": "not-connected", "model_access": {"not-connected": ModelAccess().document()}},
    {"expected_revision": True}, {"consent_version": 1},
])
def test_invalid_model_setup_is_refused_before_raising_a_tab(rig, change):
    assert ask(rig, **change)["error"] == "request_invalid"
    assert load_provider_assignment(rig[0], universe_id="u-owner") is None


def test_invalid_model_document_explains_the_exact_schema(rig):
    result = ask(rig, model_access={"codex": {"models": ["gpt-6-astra"]}})
    assert result["error"] == "request_invalid"
    message = result["detail"]
    for expected in ("model_scope", "model_ids", "cost_caps", "explicit", "discovered"):
        assert expected in message, result
    assert load_provider_assignment(rig[0], universe_id="u-owner") is None


@pytest.mark.parametrize("document", [
    {"model_scope": "discovered", "model_ids": [], "cost_caps": None, "extra": True},
    {"model_scope": "discovered", "model_ids": ["gpt-6-astra"], "cost_caps": None},
    {"model_scope": "explicit", "model_ids": [], "cost_caps": None},
    {"model_scope": "explicit", "model_ids": ["gpt-6-astra"], "cost_caps": {"tokens": True}},
])
def test_document_guidance_does_not_relax_validation(rig, document):
    assert ask(rig, model_access={"codex": document})["error"] == "request_invalid"
    assert load_provider_assignment(rig[0], universe_id="u-owner") is None


def test_partial_reconnect_retries_without_rebinding(rig, monkeypatch):
    from tinyassets import provider_serving_binding as serving

    row = ask(rig)
    real_enable = serving.set_serving

    def fail(**kwargs):
        raise PermissionError("source temporarily unavailable")

    monkeypatch.setattr(serving, "set_serving", fail)
    assert answer(row)["error"] == "provider_authority_denied"
    assert get_request(rig[1], row["request_id"])["status"] == "pending"
    before = load_provider_assignment(rig[0], universe_id="u-owner")
    monkeypatch.setattr(serving, "set_serving", real_enable)
    monkeypatch.setattr(serving, "bind_serving_provider",
                        lambda **kwargs: pytest.fail("partial retry re-published access"))
    assert answer(row)["status"] == "answered"
    assert load_provider_assignment(rig[0], universe_id="u-owner") == before


def test_resolution_failure_retries_without_binding_or_enabling(rig, monkeypatch):
    from tinyassets import provider_serving_binding as serving
    from tinyassets.storage import pending_requests as storage

    row = ask(rig)
    resolve = storage.resolve_request
    monkeypatch.setattr(storage, "resolve_request", lambda *args, **kwargs: False)
    assert answer(row)["error"] == "request_resolution_unconfirmed"
    monkeypatch.setattr(storage, "resolve_request", resolve)
    monkeypatch.setattr(serving, "bind_serving_provider", lambda **kw: pytest.fail("rebind"))
    monkeypatch.setattr(serving, "set_serving", lambda **kw: pytest.fail("reenable"))
    assert answer(row)["status"] == "answered"


def test_served_agent_cannot_answer_model_access_request(rig, monkeypatch):
    from tinyassets import engine_mcp_server as engine

    monkeypatch.setattr(engine, "_ACTOR_ID", "owner-1")
    monkeypatch.setattr(engine, "_GRAPH_ID", "u-owner")
    from tests.engine_authority_helpers import seed_bound_engine
    seed_bound_engine(monkeypatch)
    row = ask(rig)
    result = json.loads(engine.write_graph(target="pending_request", operation="answer",
                                          payload_json=json.dumps({"request_id": row["request_id"],
                                                                   "values": {}})))
    assert "person you asked" in result["error"]
    assert get_request(rig[1], row["request_id"])["status"] == "pending"
    assert load_provider_assignment(rig[0], universe_id="u-owner") is None


def test_repeated_failed_publication_then_partial_reconnect_is_recoverable(rig, monkeypatch):
    from tinyassets import provider_serving_binding as serving

    row = ask(rig)
    real_publish = serving.set_binding_provider_ref_in_transaction
    attempts = []

    def fail_twice(*args, **kwargs):
        attempts.append(1)
        if len(attempts) <= 2:
            raise PermissionError("publication interrupted")
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(serving, "set_binding_provider_ref_in_transaction", fail_twice)
    for generation in (1, 2):
        assert answer(row)["error"] == "provider_authority_denied"
        state = load_provider_assignment(rig[0], universe_id="u-owner")
        assert state.state == "failed" and state.generation == generation
    real_enable = serving.set_serving
    monkeypatch.setattr(serving, "set_serving", lambda **kw: {"error": "temporary"})
    assert answer(row)["error"] == "provider_authority_denied"
    assert load_provider_assignment(rig[0], universe_id="u-owner").generation == 3
    monkeypatch.setattr(serving, "set_serving", real_enable)
    monkeypatch.setattr(serving, "bind_serving_provider", lambda **kw: pytest.fail("rebind"))
    assert answer(row)["status"] == "answered"
    assert len(attempts) == 3


def test_another_binding_cannot_replace_the_assignment_under_an_old_ask(rig):
    from tinyassets.custom_agents import create_binding
    from tinyassets.provider_serving_binding import bind_serving_provider

    row = ask(rig)
    other = create_binding(rig[0], universe_id="u-owner", created_by="owner-1",
                           definition_id=rig[2]["agent_definition_id"],
                           payload={"schema_version": 1, "name": "Other agent", "role": "writer"})
    bind_serving_provider(base_path=rig[0], universe_dir=rig[1], universe_id="u-owner",
                          owner_user_id="owner-1", agent_binding_id=other["agent_binding_id"],
                          expected_revision=1, provider="codex")
    before = load_provider_assignment(rig[0], universe_id="u-owner")
    assert answer(row)["error"] == "provider_authority_denied"
    assert load_provider_assignment(rig[0], universe_id="u-owner") == before
    assert get_request(rig[1], row["request_id"])["status"] == "pending"


def test_changed_home_refuses_answer_without_publishing(rig):
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    row = ask(rig)
    with SQLiteProviderWorkAuthorityStore(rig[0]).connection() as conn:
        conn.execute("UPDATE founder_home SET universe_id='u-other' WHERE founder_sub='owner-1'")
    assert answer(row)["error"] == "provider_authority_denied"
    assert load_provider_assignment(rig[0], universe_id="u-owner") is None
    assert get_request(rig[1], row["request_id"])["status"] == "pending"


def test_model_setup_does_not_change_existing_spending_ceilings(rig):
    from tinyassets.provider_serving_binding import bind_serving_provider

    existing = ModelAccess("explicit", ("",), (("input_usd", 3),))
    bound = bind_serving_provider(
        base_path=rig[0], universe_dir=rig[1], universe_id="u-owner", owner_user_id="owner-1",
        agent_binding_id=rig[2]["agent_binding_id"], expected_revision=1, provider="codex",
        model_access={"codex": existing},
    )
    refused = ask(rig, expected_revision=bound["agent_binding"]["revision"])
    assert refused["error"] == "request_invalid"
    changed = ModelAccess("explicit", ("", "named-model"), existing.cost_caps)
    row = ask(rig, expected_revision=bound["agent_binding"]["revision"],
              model_access={"codex": changed.document()})
    assert row["status"] == "pending"
    assert "within spending limits you already set" in row["grant_sentence"]


def test_other_accepted_sources_are_preserved_exactly(rig, monkeypatch):
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.provider_serving_binding import bind_serving_provider

    monkeypatch.setenv("TINYASSETS_ALLOW_CLAUDE_SERVING", "1")
    write_credential_vault(rig[1], [
        {"credential_type": "llm_subscription", "service": "codex", "auth_json_b64": "e30="},
        {"credential_type": "llm_subscription", "service": "claude", "oauth_token": "fixture-only"},
    ], owner_user_id="owner-1", universe_id="u-owner")
    access = {name: ModelAccess("explicit", ("",)) for name in ("codex", "claude-code")}
    bound = bind_serving_provider(
        base_path=rig[0], universe_dir=rig[1], universe_id="u-owner", owner_user_id="owner-1",
        agent_binding_id=rig[2]["agent_binding_id"], expected_revision=1,
        provider="codex", model_access=access,
    )
    revision = bound["agent_binding"]["revision"]
    assert ask(rig, expected_revision=revision)["error"] == "request_invalid"  # drops Claude
    proposal = {name: item.document() for name, item in access.items()}
    proposal["codex"] = ModelAccess("explicit", ("", "named-model")).document()
    accepted = ask(rig, expected_revision=revision, model_access=proposal)
    assert accepted["status"] == "pending"
    assert accepted["action"]["proposed_membership"]["claude-code"] == proposal["claude-code"]
    proposal["claude-code"] = ModelAccess("discovered").document()
    assert ask(rig, expected_revision=revision, model_access=proposal)["error"] == "request_invalid"
