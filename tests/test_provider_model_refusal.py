"""A source refusing ONE model is not an unreadable reply, and not a dead end.

Live 2026-09-28, free-only universe ``u-01ky3zh1arr8qth8jee7zx63pq``, turn
``b1675c7d6bc742ba9db964c666a5fad6``: round 1 on ``qwen/qwen3.8-27b:free`` was
rate limited (429), the capacity path moved on, and round 2 on
``thinkingmachines/inkling:free`` got HTTP 403. The owner read "the connected
model replied in a format this universe could not read" -- nothing had replied
-- with detail "compute provider returned HTTP 403": the body naming WHICH
refusal it was had been thrown away. The router had also cooled the whole
connection, and the turn stopped with more accepted free models in its order.

Two days earlier the same universe died on our own context measurement: a large
tool result overflowed a 262k-token model while a 1M-token free model sat in the
same order.

Every test here drives the real writer, router, ``ApiKeyHttpProvider``,
coordinator and journal; only the wire is synthetic.
"""

import json
from dataclasses import replace

import pytest

from tests import test_interactive_http_agent as integration
from tinyassets.exceptions import AllProvidersExhaustedError

rig = integration.rig
reader = integration.reader
served = integration.served
agent = integration.agent

#: The envelope OpenRouter used on the same turn's 429 (server log, 2026-09-28
#: 19:46Z): ``{"error": {"message", "code", "metadata"}, "user_id"}``. The 403's
#: own body was never recorded -- that is the bug -- so the words are synthetic
#: and the shape is recorded.
RECORDED_SHAPE_403 = json.dumps({
    "error": {
        "message": "Synthetic refusal: this model is not available to this key",
        "code": 403,
        "metadata": {"provider_name": "Synthetic Lab", "model_slug": "vendor/model:free"},
    },
    "user_id": "user_synthetic",
})


def _order(agent, monkeypatch, models, *, contexts=None):
    """The owner's explicit order: the selected model, then ``models``."""
    from tinyassets.providers import discovery_snapshot
    from tinyassets.providers.agent_model_plan import AgentModelPlan
    from tinyassets.providers.discovery_protocols import discovery_protocol
    from tinyassets.providers.model_policy import (
        Catalog,
        ModelPolicy,
        ModelRef,
        SourceModelPolicy,
    )

    contexts = contexts or {}
    original = discovery_snapshot.read_http_discovery_document

    def added(**kwargs):
        value = original(**kwargs)
        if "models/user" in kwargs["url"]:
            for row in value["data"]:
                if row["id"] in contexts:
                    row["context_length"] = contexts[row["id"]]
            for name in models:
                row = integration.authority_tests.snapshot_tests._model(name)
                if name in contexts:
                    row["context_length"] = contexts[name]
                value["data"].append(row)
        return value

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", added)
    snapshot = integration.authority_tests.snapshot_tests._refresh(agent.served.rig)
    selected = agent.served.context.model_selection
    interaction = replace(
        discovery_protocol(snapshot.models.provider_scope).text_interaction, needs_tools=True,
    )
    agent.served.context = replace(agent.served.context, agent_model_plan=AgentModelPlan(
        Catalog("owner", agent.served.context.universe_dir.name, (snapshot.models,)),
        ModelPolicy(
            generation=7, mode="explicit", saved_default=selected,
            fallbacks=tuple(ModelRef(selected.connection_id, name) for name in models),
        ),
        interaction,
        policy_source="saved",
        # As production builds it (served_model_plan): one policy per source,
        # whose interaction REPLACES the plan's for that source's models.
        source_policies=(SourceModelPolicy(selected.connection_id, interaction),),
    ))


def _record(exc):
    from tinyassets.universe_server import _served_failure_notice, _served_failure_record

    record = _served_failure_record(exc)
    return record, _served_failure_notice(exc, record)


# --------------------------------------------------------------------------
# Classification: the provider's refusal, in the provider's words.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [403, 404, 410])
def test_a_refusal_is_reported_as_a_refusal_in_the_sources_own_words(agent, status):
    agent.capacity_failures[1] = status
    agent.failure_bodies[1] = RECORDED_SHAPE_403.replace("403", str(status))
    with pytest.raises(AllProvidersExhaustedError) as error:
        integration.run(agent)
    attempt = error.value.attempts[-1]
    assert attempt.failure_class == "provider_refused"
    assert attempt.side_effect_state == "none"
    # The body is the only place the source says which refusal this was.
    assert f"HTTP {status}" in attempt.detail
    assert "not available to this key" in attempt.detail

    record, notice = _record(error.value)
    assert record.code == "provider_refused"
    assert record.stage == "model_request"
    assert record.effects == "none"
    assert "not available to this key" in record.provider_detail
    assert "format" not in notice
    assert "refused to serve this model" in notice
    assert "Choose another model" in notice
    assert "Nothing ran." in notice


def test_a_refused_model_does_not_cool_the_connection(agent):
    """Cooling the source skips every sibling the turn could move to next."""
    agent.capacity_failures[1] = 403
    agent.failure_bodies[1] = RECORDED_SHAPE_403
    with pytest.raises(AllProvidersExhaustedError):
        integration.run(agent)
    provider = agent.served.context.model_selection.connection_id
    assert agent.served.router._quota.cooldown_remaining(provider) == 0


def test_a_refusal_releases_its_reservation_instead_of_charging_it(agent):
    """A status the source answered with is a confirmed pre-generation answer."""
    agent.capacity_failures[1] = 403
    with pytest.raises(AllProvidersExhaustedError):
        integration.run(agent)
    with agent.journal._ledger.connection() as conn:
        rows = conn.execute(
            "SELECT state, actual_total_tokens FROM served_provider_budget_reservations"
        ).fetchall()
    assert [tuple(row) for row in rows] == [("succeeded", 0)]


def test_an_unrecognized_status_keeps_its_body_as_detail(agent):
    """Still a protocol error, but no longer one with the source's words dropped."""
    agent.capacity_failures[1] = 418
    agent.failure_bodies[1] = '{"error":{"message":"short and stout"}}'
    with pytest.raises(AllProvidersExhaustedError) as error:
        integration.run(agent)
    attempt = error.value.attempts[-1]
    assert attempt.failure_class == "provider_protocol_error"
    assert "HTTP 418" in attempt.detail and "short and stout" in attempt.detail


# --------------------------------------------------------------------------
# Fallback: the next model the owner accepted answers the turn.
# --------------------------------------------------------------------------


def test_a_refused_model_moves_the_turn_to_the_next_accepted_model(agent, monkeypatch):
    _order(agent, monkeypatch, ["future-vendor/another-model"])
    agent.capacity_failures[2] = 403
    agent.failure_bodies[2] = RECORDED_SHAPE_403
    assert integration.run(agent) == "finished exact answer"
    turn = agent.latest()
    assert turn.state == "completed"
    assert [round.state for round in turn.rounds] == ["received", "failed", "received"]
    assert agent.wires[-1][1]["body"]["model"] == "future-vendor/another-model"
    # The tool the first round ran is carried, not re-run.
    assert len(agent.tools) == 1
    # Same owner grant, same zero ceilings.
    assert all(
        set(wire[1]["body"]["provider"]["max_price"].values()) == {"0"}
        for wire in agent.wires
    )


def test_the_live_sequence_rate_limit_then_refusal_then_an_answer(agent, monkeypatch):
    """2026-09-28: 429 on the first free model, 403 on the second, then a third."""
    _order(agent, monkeypatch, ["lab/refusing:free", "lab/answering:free"])
    agent.requested_rounds = 0
    agent.capacity_failures.update({1: 429, 2: 403})
    agent.failure_bodies[2] = RECORDED_SHAPE_403
    assert integration.run(agent) == "finished exact answer"
    models = [wire[1]["body"]["model"] for wire in agent.wires]
    assert models[1:] == ["lab/refusing:free", "lab/answering:free"]
    assert len(set(models)) == 3


def test_refusals_walk_the_whole_accepted_list_trying_each_model_once(agent, monkeypatch):
    """Several dead free models in a row must not end the turn early.

    Live 2026-09-28 the pool had three in a row (429, withdrawn, 403). The bound
    is the owner's list: every accepted model is tried exactly once, then the
    turn reports, with every attempt in its evidence.
    """
    siblings = [f"lab/model-{index}:free" for index in range(6)]
    _order(agent, monkeypatch, siblings)
    agent.requested_rounds = 0
    agent.capacity_failures.update({index: 403 for index in range(1, 20)})
    with pytest.raises(AllProvidersExhaustedError) as error:
        integration.run(agent)
    models = [wire[1]["body"]["model"] for wire in agent.wires]
    assert len(models) == 7 and len(set(models)) == 7
    assert set(models[1:]) == set(siblings)
    assert [a.failure_class for a in error.value.attempts] == ["provider_refused"] * 7
    record, _ = _record(error.value)
    assert record.code == "provider_refused"


def test_the_first_answering_model_after_many_refusals_answers(agent, monkeypatch):
    _order(agent, monkeypatch, [f"lab/model-{index}:free" for index in range(5)])
    agent.requested_rounds = 0
    agent.capacity_failures.update({index: 403 for index in range(1, 6)})
    assert integration.run(agent) == "finished exact answer"
    assert agent.wires[-1][1]["body"]["model"] == "lab/model-4:free"
    assert len(agent.wires) == 6


def test_an_empty_accepted_order_stays_empty_on_a_refusal(agent, monkeypatch):
    integration._with_fallback(agent, monkeypatch, empty=True)
    agent.capacity_failures[1] = 403
    with pytest.raises(AllProvidersExhaustedError):
        integration.run(agent)
    assert len(agent.wires) == 1


def test_a_refusal_mixed_with_capacity_is_left_to_the_capacity_path(agent, monkeypatch):
    """Only a round made ENTIRELY of refusals is this path's to advance."""
    from types import SimpleNamespace

    from tinyassets.agent_turn_coordinator import AgentTurnCoordinator

    turn = AgentTurnCoordinator.__new__(AgentTurnCoordinator)
    turn.plan = SimpleNamespace()
    turn.adapter = SimpleNamespace()
    turn.turn = SimpleNamespace(state="held_transport")
    mixed = AllProvidersExhaustedError("x", attempts=[
        SimpleNamespace(failure_class="provider_refused", side_effect_state="none"),
        SimpleNamespace(failure_class="provider_rate_limited", side_effect_state="none"),
    ])
    assert turn._next_after_refusal(mixed) is False


def test_a_source_left_hot_for_a_sibling_is_cooled_when_the_turn_ends_on_refusals(
    agent, monkeypatch,
):
    """429 -> sibling on the same source -> 403s to the end of the list.

    The capacity path withheld the source's cooldown to try a sibling; the turn
    then left the source through the refusal path, which used to leave it hot, so
    the next turn asked the capped source again (Codex, 2026-09-28).
    """
    _order(agent, monkeypatch, ["lab/refusing-a:free", "lab/refusing-b:free"])
    agent.requested_rounds = 0
    agent.capacity_failures.update({1: 429, 2: 403, 3: 403})
    with pytest.raises(AllProvidersExhaustedError):
        integration.run(agent)
    assert len(agent.wires) == 3
    provider = agent.served.context.model_selection.connection_id
    assert agent.served.router._quota.cooldown_remaining(provider) > 0


@pytest.mark.parametrize("next_source,cooled", [("source-b", True), ("source-a", False)])
def test_a_hot_source_is_cooled_only_when_a_refusal_leaves_it(
    next_source, cooled, monkeypatch,
):
    import dataclasses
    from types import SimpleNamespace

    import tinyassets.agent_turn_coordinator as module
    from tinyassets.agent_turn_coordinator import AgentTurnCoordinator
    from tinyassets.providers.model_policy import ModelRef

    failed = ModelRef("source-a", "capped:free")
    refused = ModelRef("source-a", "refused:free")
    turn = AgentTurnCoordinator.__new__(AgentTurnCoordinator)
    turn.plan = SimpleNamespace()
    turn.adapter = SimpleNamespace()
    turn.turn = SimpleNamespace(state="held_transport")
    turn.context = SimpleNamespace(model_selection=refused)
    turn.visited, turn.exhaustion, turn.spent_attempts = set(), (), []
    turn.execution_kind = "engine_inference"
    # No owner: a hand-built coordinator records no durable refusal
    # (tests/test_refused_models_recorded.py drives that through a real turn).
    turn.owner = None
    boundary = SimpleNamespace()
    turn._hot_capacity = (failed, boundary)
    left = []
    turn._cool_abandoned_source = lambda *args: left.append(args)
    turn._next_candidate = lambda: ModelRef(next_source, "next:free")
    real_replace = dataclasses.replace

    def fake_replace(obj, **changes):
        # The coordinator's context is a frozen dataclass in production.
        if isinstance(obj, SimpleNamespace):
            return SimpleNamespace(**{**vars(obj), **changes})
        return real_replace(obj, **changes)

    monkeypatch.setattr(module, "replace", fake_replace)
    exc = AllProvidersExhaustedError("x", attempts=[
        SimpleNamespace(failure_class="provider_refused", side_effect_state="none"),
    ])
    assert turn._next_after_refusal(exc) is True
    assert left == ([(failed, boundary)] if cooled else [])


# --------------------------------------------------------------------------
# Context fit: our own measurement moves the turn to a model that fits.
# --------------------------------------------------------------------------


def test_a_turn_too_large_for_the_model_moves_to_an_accepted_model_that_fits(
    agent, monkeypatch,
):
    from tinyassets import universe_intelligence

    # Both small models are skipped in ONE step: neither is ever sent.
    _order(
        agent, monkeypatch, ["lab/also-small:free", "lab/large-window:free"],
        contexts={"lab/also-small:free": 32000, "lab/large-window:free": 1_048_576},
    )
    agent.requested_rounds = 0
    router = agent.served.router
    tried = []
    real_call = router.call

    async def call(*args, **kwargs):
        tried.append(kwargs["universe_context"].model_selection.model_id)
        return await real_call(*args, **kwargs)

    monkeypatch.setattr(router, "call", call)
    answer = universe_intelligence._call_writer(
        "x" * 150_000, system="exact system",
        universe_context=agent.served.context, config=agent.config,
    )
    assert answer == "finished exact answer"
    assert [wire[1]["body"]["model"] for wire in agent.wires] == ["lab/large-window:free"]
    # The measured size rules out every model too small at once; the other small
    # model is never even routed.
    assert "lab/also-small:free" not in tried
    assert tried[-1] == "lab/large-window:free" and len(tried) == 2


def test_a_turn_too_large_for_every_accepted_model_still_says_so(agent, monkeypatch):
    from tinyassets import universe_intelligence

    _order(agent, monkeypatch, ["lab/also-small:free"])
    with pytest.raises(PermissionError) as error:
        universe_intelligence._call_writer(
            "x" * 150_000, system="exact system",
            universe_context=agent.served.context, config=agent.config,
        )
    assert not agent.wires
    record, _ = _record(error.value)
    assert record.code == "context_window_exceeded"


def test_an_earlier_models_refusal_never_names_the_last_models_failure():
    """Live 2026-09-29, turn b804819f: rounds on three models, the last one failing
    for a reason we could not classify. The notice named the SECOND model's
    refusal -- "refused to serve this model" -- beside the third model's detail.
    Only the last failed attempt is the turn's cause."""
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic

    def failed(failure_class, skip_class, detail):
        return ProviderAttemptDiagnostic(
            provider="api_key_http:source", status="failed", skip_class=skip_class,
            detail=detail, failure_class=failure_class,
        )

    exc = AllProvidersExhaustedError("exhausted", attempts=[
        failed("provider_rate_limited", "quota_or_cooldown", "HTTP 429: busy"),
        failed("provider_refused", "provider_error", "HTTP 403: gated"),
        failed(None, "unknown", "ProxyRequestError: outbound request failed"),
    ])
    record, notice = _record(exc)
    assert record.code == "unknown"
    assert "refused" not in notice
    assert "outbound request failed" in record.provider_detail
