"""A source refusing ONE model never holds a run; a node pin may name the access method.

Live 2026-10-01 00:05Z, universe ``u-01ky3zh1arr8qth8jee7zx63pq`` (a free
account whose only source is an OpenRouter key, daily allowance fresh, chat
working on the same key for 24 rounds). Its one-prompt-node workflow failed four
times:

1. run ``2a67c381980a42de``: OpenRouter answered the run's model with HTTP 403
   "only available on agentic harness endpoints" (routing step "Gate Free
   Endpoints by Agentic Harness"), and the run HELD -- a model-scoped refusal
   stopped the run instead of moving to the next model;
3. runs ``c772bd9d3aed4e2c`` / ``e498ff01dee2458e``: the universe pinned the node
   with ``{"provider": "api_key_http", "model_id": ...}`` and the run refused
   "workflow requests an unavailable accepted provider", naming neither the
   shape nor the refs it would have taken.

Only the remote transports are synthetic (``_Wire`` from the run-parity suite):
the router, compiler, admission, receipts and the real ``ApiKeyHttpProvider``
all run, and the 403 body is the one OpenRouter sends.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_free_account_run_provider_parity import (  # noqa: F401
    A_HOME,
    A_OWNER,
    LIVE_MODELS,
    _branch,
    _run,
    _seed_universe,
    _Wire,
    wires,
)
from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)

pytestmark = pytest.mark.usefixtures("cloud_runtime")

#: OpenRouter's own refusal for a free model outside an agentic harness.
HARNESS_403 = json.dumps({"error": {
    "message": f"{LIVE_MODELS[0]} is only available on agentic harness endpoints",
    "code": 403,
    "metadata": {"failed_routing_step": "Gate Free Endpoints by Agentic Harness"},
}})
#: OpenRouter's daily free-model cap: the whole key, every model.
DAILY_429 = json.dumps({"error": {
    "code": 429,
    "message": "Rate limit exceeded: free-models-per-day. "
               "Add 10 credits to unlock 1000 free model requests per day",
}})


class _RefusingWire(_Wire):
    """The parity wire plus per-model whole-response answers (status, body)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.answers: dict[str, tuple[int, str]] = {}

    def request(self, verb: str, document: dict[str, Any]) -> dict[str, Any]:
        model = document["body"]["model"]
        if model in self.answers:
            self.requests.append((verb, document))
            status, body = self.answers[model]
            return {"status": status, "headers": {}, "body": body}
        return super().request(verb, document)


def _seed(tmp_path, monkeypatch, wires) -> str:  # noqa: F811 - the imported fixture
    provider = _seed_universe(
        tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a",
    )
    refusing = _RefusingWire(models=wires[A_OWNER].models)
    wires[A_OWNER] = refusing
    return provider


def _pinned(policy: dict[str, Any], *, branch_def_id: str = "branch_pinned") -> BranchDefinition:
    node = NodeDefinition(
        node_id="generate_note", display_name="Generate note",
        prompt_template="Write a short morning focus note.", output_keys=["note"],
        model_hint="writer", llm_policy=policy,
    )
    return BranchDefinition(
        branch_def_id=branch_def_id, name="Morning Focus Note", author=A_OWNER,
        visibility="private",
        graph_nodes=[GraphNodeRef(id=node.node_id, node_def_id=node.node_id)],
        edges=[EdgeDefinition(from_node="START", to_node=node.node_id),
               EdgeDefinition(from_node=node.node_id, to_node="END")],
        entry_point=node.node_id, node_defs=[node],
        state_schema=[{"name": "note", "type": "str", "default": ""}],
    )


# --------------------------------------------------------------------------
# (a) A model refusal is the model's, never the source's or the account's.
# --------------------------------------------------------------------------


def test_an_agentic_harness_403_steps_to_the_next_model_in_the_same_run(
    tmp_path, monkeypatch, authenticate_request, wires,  # noqa: F811
):
    from tinyassets.providers import call as call_module

    authenticate_request(A_OWNER)
    provider = _seed(tmp_path, monkeypatch, wires)
    wires[A_OWNER].answers[LIVE_MODELS[0]] = (403, HARNESS_403)

    record = _run(tmp_path, monkeypatch, _branch(owner=A_OWNER), A_HOME)

    assert record["status"] == "completed", record["error"]
    assert wires[A_OWNER].sent_models == list(LIVE_MODELS), wires[A_OWNER].sent_models
    assert record["output"]["note"] == "morning focus note"
    # The source stayed eligible: a refusal names one model.
    assert call_module.get_provider_router()._quota.cooldown_remaining(provider) == 0


def test_the_refused_model_is_not_called_on_the_next_run(
    tmp_path, monkeypatch, authenticate_request, wires,  # noqa: F811
):
    authenticate_request(A_OWNER)
    _seed(tmp_path, monkeypatch, wires)
    wires[A_OWNER].answers[LIVE_MODELS[0]] = (403, HARNESS_403)
    first = _run(tmp_path, monkeypatch, _branch(owner=A_OWNER), A_HOME)
    assert first["status"] == "completed", first["error"]
    wires[A_OWNER].requests.clear()

    second = _run(tmp_path, monkeypatch, _branch(owner=A_OWNER, branch_def_id="branch_two"),
                  A_HOME)

    assert second["status"] == "completed", second["error"]
    assert wires[A_OWNER].sent_models == [LIVE_MODELS[1]], wires[A_OWNER].sent_models


def test_a_chat_turn_orders_a_model_a_run_was_refused_last(
    tmp_path, monkeypatch, authenticate_request, wires,  # noqa: F811
):
    """One refusal memory for both surfaces: chat stopped where runs failed."""
    from tinyassets.provider_serving_binding import resolve_serving_agent_binding
    from tinyassets.providers.served_model_plan import prepare_owned_model_plan

    authenticate_request(A_OWNER)
    _seed(tmp_path, monkeypatch, wires)
    wires[A_OWNER].answers[LIVE_MODELS[0]] = (403, HARNESS_403)
    assert _run(tmp_path, monkeypatch, _branch(owner=A_OWNER), A_HOME)["status"] == "completed"

    prepared = prepare_owned_model_plan(
        base=tmp_path, universe=tmp_path / A_HOME, owner=A_OWNER,
        agent=resolve_serving_agent_binding(tmp_path, universe_id=A_HOME,
                                            owner_user_id=A_OWNER),
    )
    order = [item.ref.model_id for item in prepared.plan.order(A_OWNER, A_HOME).candidates]
    assert order[0] == LIVE_MODELS[1] and order[-1] == LIVE_MODELS[0], order


def test_chat_and_runs_read_a_refusal_round_with_one_test():
    """The same attempts mean the same thing on both surfaces."""
    from tinyassets import agent_turn_coordinator, foreground_run_provider  # noqa: F401
    from tinyassets.providers import agent_capacity_boundary as boundary_module
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic
    from tinyassets.providers.model_policy import ModelRef

    assert (agent_turn_coordinator.uniform_pre_generation_failure
            is boundary_module.uniform_pre_generation_failure)
    refused = ProviderAttemptDiagnostic(
        provider="api_key_http:p", status="failed", skip_class="provider_error",
        detail="403", failure_class="provider_refused", side_effect_state="none",
    )
    ref = ModelRef("api_key_http:p", "m")
    found = boundary_module.refusal_boundary(ref, [refused], execution_kind="engine_inference")
    assert found is not None and found.exhaustion.scope == "model"
    # A native agent's tools may have acted, and it carries no completion proof
    # here: its refusal holds the run rather than replaying on another model.
    assert boundary_module.refusal_boundary(ref, [refused], execution_kind="native_agent") is None
    # Evidence from another source says nothing about this model.
    other = ModelRef("api_key_http:q", "m")
    assert boundary_module.refusal_boundary(
        other, [refused], execution_kind="engine_inference",
    ) is None
    # A round that may have acted is never replayed, on either surface.
    acted = ProviderAttemptDiagnostic(
        provider="api_key_http:p", status="failed", skip_class="provider_error",
        detail="403", failure_class="provider_refused", side_effect_state="unknown",
    )
    assert boundary_module.refusal_boundary(
        ref, [acted], execution_kind="engine_inference",
    ) is None
    assert not boundary_module.uniform_pre_generation_failure([acted], "provider_refused")


def test_a_daily_cap_429_still_cools_the_whole_source(
    tmp_path, monkeypatch, authenticate_request, wires,  # noqa: F811
):
    from tinyassets.exceptions import WorkModelExhaustedError
    from tinyassets.providers import call as call_module

    authenticate_request(A_OWNER)
    provider = _seed(tmp_path, monkeypatch, wires)
    wires[A_OWNER].answers[LIVE_MODELS[0]] = (429, DAILY_429)

    record = _run(tmp_path, monkeypatch, _branch(owner=A_OWNER), A_HOME)

    assert record["status"] == "failed"
    assert WorkModelExhaustedError.MESSAGE in record["error"], record["error"]
    assert "provider_daily_quota" in record["error"], record["error"]
    # Account-wide: the sibling is not asked, and the source is cooled.
    assert wires[A_OWNER].sent_models == [LIVE_MODELS[0]], wires[A_OWNER].sent_models
    assert call_module.get_provider_router()._quota.cooldown_remaining(provider) > 0


def test_a_cooled_source_says_it_is_cooling_down_not_spent(
    tmp_path, monkeypatch, authenticate_request, wires,  # noqa: F811
):
    """Live run 72232f47e4144838: "(account scope)" alone read as a spent allowance."""
    from tinyassets.providers import call as call_module

    authenticate_request(A_OWNER)
    provider = _seed(tmp_path, monkeypatch, wires)
    router = call_module.get_provider_router()
    router._cool(None, provider, 120, reason="provider_protocol_error")

    record = _run(tmp_path, monkeypatch, _branch(owner=A_OWNER), A_HOME)

    assert record["status"] == "failed"
    assert wires[A_OWNER].sent_models == []
    assert "cooling down" in record["error"], record["error"]
    assert "provider_protocol_error" in record["error"], record["error"]
    assert "not a spent allowance" in record["error"], record["error"]


# --------------------------------------------------------------------------
# (c) Node pins: the natural shape runs; what cannot resolve lists the refs.
# --------------------------------------------------------------------------


def test_a_pin_naming_the_access_method_runs_on_the_named_model(
    tmp_path, monkeypatch, authenticate_request, wires,  # noqa: F811
):
    authenticate_request(A_OWNER)
    _seed(tmp_path, monkeypatch, wires)
    branch = _pinned({"preferred": {"provider": "api_key_http", "model_id": LIVE_MODELS[1]}})

    record = _run(tmp_path, monkeypatch, branch, A_HOME)

    assert record["status"] == "completed", record["error"]
    assert wires[A_OWNER].sent_models == [LIVE_MODELS[1]], wires[A_OWNER].sent_models


def test_a_pin_naming_no_source_lists_the_universes_refs(
    tmp_path, monkeypatch, authenticate_request, wires,  # noqa: F811
):
    from tinyassets.api.runs import MODEL_PIN_ACTION, _classify_run_outcome_error
    from tinyassets.runs import _classify_failure

    authenticate_request(A_OWNER)
    provider = _seed(tmp_path, monkeypatch, wires)
    branch = _pinned({"preferred": {"provider": "openrouter", "model_id": LIVE_MODELS[1]}})

    record = _run(tmp_path, monkeypatch, branch, A_HOME)

    assert record["status"] == "failed"
    assert wires[A_OWNER].sent_models == []
    assert "model pin provider 'openrouter'" in record["error"], record["error"]
    assert provider in record["error"], record["error"]
    assert '"model_id": "<model>"' in record["error"], record["error"]
    # Both stored-error readers name it a pin to edit, never a rate limit.
    assert _classify_run_outcome_error(record["error"]) == (
        "permission_denied:provider_not_bound", MODEL_PIN_ACTION,
    )
    assert _classify_failure(record) == "permission_denied:provider_not_bound"


def test_an_ambiguous_pin_refuses_with_the_choices():
    from tinyassets.providers.model_pins import ModelPinError, resolve_pin_source

    sources = {
        "api_key_http:provdef_a": ("vendor/one:free", "vendor/shared:free"),
        "api_key_http:provdef_b": ("vendor/two:free", "vendor/shared:free"),
        "codex": None,
    }
    # The model disambiguates.
    assert resolve_pin_source("api_key_http", "vendor/two:free", sources) == (
        "api_key_http:provdef_b"
    )
    # An exact ref is taken as is.
    assert resolve_pin_source("codex", "", sources) == "codex"
    for model in ("vendor/shared:free", ""):
        with pytest.raises(ModelPinError) as caught:
            resolve_pin_source("api_key_http", model, sources)
        message = str(caught.value)
        assert "api_key_http:provdef_a" in message and "api_key_http:provdef_b" in message
        assert "codex" not in message
    with pytest.raises(ModelPinError) as caught:
        resolve_pin_source("openrouter", "vendor/one:free", sources)
    assert all(ref in str(caught.value) for ref in sources)


def test_a_background_pin_is_resolved_by_the_model_its_source_offers(tmp_path, monkeypatch):
    """The background lane resolves a bare pin model-aware, before its snapshot.

    gpt-6-astra on #4162: it passed no models, so two ``api_key_http`` sources
    with disjoint catalogues refused a pin the model alone identified.
    """
    from types import SimpleNamespace

    from tinyassets import provider_assignment
    from tinyassets.background_served_provider import _BackgroundAssignedProviderSession
    from tinyassets.providers import discovery_snapshot
    from tinyassets.providers.model_pins import ModelPinError

    offered = {"provdef_a": ("vendor/one:free",), "provdef_b": ("vendor/two:free",)}
    assignment = SimpleNamespace(
        owner_user_id=A_OWNER,
        candidates=[SimpleNamespace(provider=f"api_key_http:{key}") for key in offered],
    )
    monkeypatch.setattr(provider_assignment, "load_provider_assignment_in_transaction",
                        lambda _conn, universe_id: assignment)
    asked = []

    def discover(*, owner_user_id, universe_id, definition_id):
        asked.append(definition_id)
        models = tuple(SimpleNamespace(model_id=m) for m in offered[definition_id])
        return SimpleNamespace(models=SimpleNamespace(models=models))

    monkeypatch.setattr(discovery_snapshot, "refresh_model_discovery", discover)
    session = _BackgroundAssignedProviderSession.__new__(_BackgroundAssignedProviderSession)
    session._base_path = tmp_path
    session._task = SimpleNamespace(universe_id=A_HOME)
    (tmp_path / A_HOME).mkdir()

    policy = {"preferred": {"provider": "api_key_http", "model_id": "vendor/two:free"}}
    assert session._resolved_policy(policy)["preferred"]["provider"] == "api_key_http:provdef_b"
    assert sorted(asked) == ["provdef_a", "provdef_b"]
    # An exact ref is untouched and asks nothing.
    asked.clear()
    exact = {"preferred": {"provider": "api_key_http:provdef_a"}}
    assert session._resolved_policy(exact) is exact and asked == []
    with pytest.raises(ModelPinError) as caught:
        session._resolved_policy({"preferred": {"provider": "api_key_http"}})
    assert "api_key_http:provdef_a" in str(caught.value)
    assert "api_key_http:provdef_b" in str(caught.value)


def test_an_exact_ref_the_owner_does_not_hold_keeps_the_existing_refusal():
    """No list of this universe's refs in answer to a ref it does not hold."""
    from tinyassets.providers.model_pins import resolve_pin_source

    sources = {"api_key_http:provdef_mine": ("vendor/one:free",)}
    foreign = "api_key_http:provdef_someone_else"
    assert resolve_pin_source(foreign, "vendor/one:free", sources) == foreign


def test_a_cooldown_reason_lives_and_expires_with_its_cooldown(monkeypatch):
    """One authority: the reason is part of the cooldown, never a second map."""
    from tinyassets.providers import quota as quota_module

    clock = [1000.0]
    monkeypatch.setattr(quota_module.time, "monotonic", lambda: clock[0])
    quota = quota_module.QuotaTracker()
    quota.cooldown("src", 60, reason="provider_rate_limited")
    assert quota.cooldown_reason("src") == "provider_rate_limited"
    # A sticky daily cooldown is not replaced by a shorter one, nor is its cause.
    quota.cooldown("day", 3600, daily_detail="daily cap", reason="provider_daily_quota")
    quota.cooldown("day", 60, reason="provider_rate_limited")
    assert quota.cooldown_reason("day") == "provider_daily_quota"
    clock[0] += 61
    assert quota.cooldown_reason("src") == ""
    assert quota.available("src")
