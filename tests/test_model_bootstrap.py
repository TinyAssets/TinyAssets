"""Real owner-scoped deposit -> candidate -> unanswered free-model consent."""

import json

import pytest

from tests.test_model_bootstrap_candidate import row
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.onboarding.hosted_model_auth import HostedAuthError, load_preset
from tinyassets.onboarding.model_bootstrap import complete_bootstrap


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from tinyassets.api import pending_requests
    from tinyassets.daemon_server import grant_universe_access, set_founder_home
    from tinyassets.providers import discovery_http

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    (tmp_path / "u-owner").mkdir()
    set_founder_home(tmp_path, founder_sub="owner", universe_id="u-owner", platform_generated=True)
    grant_universe_access(tmp_path, universe_id="u-owner", actor_id="owner", permission="admin")
    monkeypatch.setattr(discovery_http, "read_granted_discovery_document",
                        lambda **kw: {"data": [row()]})
    original_request = pending_requests.request_from_user
    def checked_request(**kwargs):
        result = original_request(**kwargs)
        assert not result.get("error"), result
        return result
    monkeypatch.setattr(pending_requests, "request_from_user", checked_request)
    with identity_context(Identity(user_id="owner", username="owner", capabilities=["write"])):
        yield tmp_path


def finish(base, **kwargs):
    return complete_bootstrap(base=base, uid="u-owner", owner="owner",
                              preset=load_preset("openrouter_user_models_v1"), **kwargs)


def test_deposit_composes_existing_request_but_does_not_answer_or_serve(rig):
    from tinyassets.credential_vault import load_credential_vault
    from tinyassets.provider_assignment import load_provider_assignment

    result = finish(rig, key="test-only-secret")
    assert result["status"] == "confirmation_required"
    assert "test-only-secret" not in json.dumps(result)
    access = result["request"]["action"]["model_access"]
    assert len(access) == 1
    assert next(iter(access.values())) == {
        "model_scope": "discovered", "model_ids": [], "cost_caps": None,
    }
    assert load_provider_assignment(rig, universe_id="u-owner") is None
    assert load_credential_vault(rig / "u-owner")[0]["token"] == "test-only-secret"
    resumed = finish(rig)
    assert resumed["request_id"] == result["request_id"]


def test_second_oauth_result_cannot_replace_first_deposit(rig):
    from tinyassets.credential_vault import load_credential_vault

    finish(rig, key="first-key")
    with pytest.raises(HostedAuthError, match="model_setup_changed"):
        finish(rig, key="later-key")
    assert load_credential_vault(rig / "u-owner")[0]["token"] == "first-key"


def test_disconnect_then_guided_reconnect_requires_fresh_approval(rig):
    from tinyassets.api.http_connection import remove_http
    from tinyassets.api.model_access_requests import execute_action
    from tinyassets.custom_agents import get_binding
    from tinyassets.onboarding.model_setup import model_setup_state
    from tinyassets.provider_assignment_manifest import parse_model_access
    from tinyassets.provider_serving_binding import bind_serving_provider

    first = finish(rig, key="first-test-key")
    action = first["request"]["action"]
    bound = bind_serving_provider(base_path=rig, universe_dir=rig / "u-owner",
        owner_user_id="owner", universe_id="u-owner",
        agent_binding_id=action["agent_binding_id"], expected_revision=action["expected_revision"],
        provider=action["provider"], model_access=parse_model_access(action["model_access"]))
    previous = bound["agent_binding"]
    result = remove_http(universe_id="u-owner",
                         payload={"destination": "model:openrouter_user_models_v1"})
    assert result["status"] == "removed"
    assert model_setup_state(rig, universe=rig / "u-owner", uid="u-owner",
                             owner="owner") == "disconnected"
    fresh = finish(rig, key="second-test-key")
    assert fresh["status"] == "confirmation_required"
    assert fresh["request_id"] != first["request_id"]
    assert fresh["request"]["action"]["expected_revision"] == previous["revision"]
    assert fresh["request"]["action"]["previous_membership"] == {}
    assert get_binding(rig, universe_id="u-owner",
                       binding_id=previous["agent_binding_id"]) == previous
    with pytest.raises(PermissionError, match="model connection changed"):
        execute_action("u-owner", action)


def test_partial_bootstrap_removal_invalidates_old_pending_approval(rig):
    from tinyassets.api.http_connection import remove_http
    from tinyassets.api.model_access_requests import execute_action

    first = finish(rig, key="first-test-key")
    remove_http(universe_id="u-owner", payload={"destination": "model:openrouter_user_models_v1"})
    second = finish(rig, key="second-test-key")
    assert second["request_id"] != first["request_id"]
    with pytest.raises(PermissionError, match="model connection changed"):
        execute_action("u-owner", first["request"]["action"])


def test_concurrent_manual_and_oauth_keys_have_one_deposit_winner(rig, monkeypatch):
    """T4: hold the winning deposit while another acquisition contests setup."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from tinyassets.api import http_connection
    from tinyassets.credential_vault import load_credential_vault

    entered, release, contested = Event(), Event(), Event()
    original = http_connection.connect_http
    calls = []

    def parked(**kwargs):
        calls.append(kwargs["payload"]["secret"])
        entered.set()
        assert release.wait(5)
        return original(**kwargs)

    monkeypatch.setattr(http_connection, "connect_http", parked)

    def run(key, second=False):
        with identity_context(Identity(user_id="owner", username="owner", capabilities=["write"])):
            if second:
                contested.set()
            return finish(rig, key=key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, "winning-key")
        assert entered.wait(5)
        second = pool.submit(run, "losing-key", True)
        assert contested.wait(5)
        release.set()
        assert first.result(timeout=15)["status"] == "confirmation_required"
        with pytest.raises(HostedAuthError, match="model_setup_changed"):
            second.result(timeout=15)
    assert calls == ["winning-key"]
    assert load_credential_vault(rig / "u-owner")[0]["token"] == "winning-key"


def test_catalogue_failure_is_resumable_without_another_oauth_exchange(rig, monkeypatch):
    from tinyassets.onboarding import model_bootstrap_candidate

    original = model_bootstrap_candidate.prepare_candidate
    def unavailable(**kwargs):
        raise HostedAuthError("no_eligible_free_agent_model", 409)
    monkeypatch.setattr(model_bootstrap_candidate, "prepare_candidate", unavailable)
    with pytest.raises(HostedAuthError):
        finish(rig, key="already-deposited")
    monkeypatch.setattr(model_bootstrap_candidate, "prepare_candidate", original)
    assert finish(rig)["status"] == "confirmation_required"


def test_home_rebinding_refuses_new_deposit(rig):
    from tinyassets.credential_vault import load_credential_vault
    from tinyassets.daemon_server import set_founder_home

    set_founder_home(rig, founder_sub="owner", universe_id="other-home")
    with pytest.raises(PermissionError):
        finish(rig, key="must-not-land")
    assert load_credential_vault(rig / "u-owner") == []


def test_empty_resume_requires_authorization_not_generic_server_error(rig):
    with pytest.raises(HostedAuthError, match="model_authorization_required"):
        finish(rig)


def test_owner_approval_uses_existing_enable_path(rig):
    from tests.owner_answer import answer_request
    from tinyassets.onboarding.model_setup import model_setup_state
    from tinyassets.provider_assignment import load_provider_assignment

    result = finish(rig, key="synthetic-key")
    answered = answer_request(universe_id="u-owner", payload={
        "request_id": result["request_id"], "values": {},
    })
    assert not answered.get("error"), answered
    assert load_provider_assignment(rig, universe_id="u-owner") is not None
    assert model_setup_state(rig, universe=rig / "u-owner", uid="u-owner",
                             owner="owner") == "connected"
    assert finish(rig)["status"] == "connected"


def test_partial_activation_resumes_same_request_without_rotating_custody(rig, monkeypatch):
    from tests.owner_answer import answer_request
    from tinyassets import provider_serving_binding
    from tinyassets.provider_assignment import load_provider_assignment

    result = finish(rig, key="synthetic-key")
    real_enable = provider_serving_binding.set_serving
    def interrupted(**kwargs):
        raise PermissionError("synthetic activation interruption")
    monkeypatch.setattr(provider_serving_binding, "set_serving", interrupted)
    payload = {"request_id": result["request_id"], "values": {}}
    first = answer_request(universe_id="u-owner", payload=payload)
    assert first["request_pending"] is True
    assignment = load_provider_assignment(rig, universe_id="u-owner")
    assert assignment is not None
    resumed = finish(rig)
    assert resumed["request_id"] == result["request_id"]
    assert "free models only" in resumed["request"]["grant_sentence"]
    assert load_provider_assignment(rig, universe_id="u-owner") == assignment
    monkeypatch.setattr(provider_serving_binding, "set_serving", real_enable)
    assert not answer_request(universe_id="u-owner", payload=payload).get("error")
    assert finish(rig)["status"] == "connected"
