"""A zero-cost source's transient refusal must not dead-end the turn.

Live 2026-09-25, free-only account ``u-01ky3zh1arr8qth8jee7zx63pq``: the first
message after a successful connect failed with ``provider_rate_limited`` and
``attempts=1``. The source's contract maps HTTP 429 to capacity scope
``unknown``; ``capacity_boundary`` collapses anything that is not unanimously
``model`` to an ACCOUNT exhaustion, and an account exhaustion removes every
sibling model on that connection from the order -- so there was no second
candidate and nothing was retried. The router had also cooled the whole source,
which would have skipped a sibling even if one had survived.

The conservatism is right when being wrong costs money. On a source whose
accepted ceilings are all zero it costs nothing, and it is what makes a freshly
connected universe mute on its very first message.
"""

import json
from dataclasses import replace

import pytest

from tests import test_interactive_http_agent as integration
from tinyassets.exceptions import AllProvidersExhaustedError
from tinyassets.providers.model_capacity import (
    TRANSIENT_CAPACITY,
    CapacitySignal,
    free_sibling_retry,
)

rig = integration.rig
reader = integration.reader
served = integration.served
agent = integration.agent


# --------------------------------------------------------------------------
# The policy predicate: one definition, two readers (router + coordinator).
# --------------------------------------------------------------------------


# The two `confirmed_free_only` unit tests that stood here went with the
# function on 2026-09-25. It existed only to gate this policy on the owner's
# price, which is the plan branch the founder's "all user accounts should be the
# same" rule forbids; `tests/test_one_path_for_every_account.py` now pins that
# the policy cannot read a ceiling at all.


@pytest.mark.parametrize("failure", sorted(TRANSIENT_CAPACITY))
def test_an_unknown_scope_transient_window_may_try_a_sibling(failure):
    assert free_sibling_retry(scope="unknown", failure_class=failure) is True


@pytest.mark.parametrize("scope,failure", [
    # A source that reported the ACCOUNT is evidence; never retry past it.
    ("account", "provider_rate_limited"),
    # Exhausted credit is about money, not a transient window.
    ("unknown", "provider_credit_exhausted"),
    # Model-local needs no policy: the existing order already keeps siblings.
    ("model", "provider_rate_limited"),
    ("unknown", None),
])
def test_proven_or_nontransient_capacity_keeps_the_conservative_policy(scope, failure):
    """NARROWED 2026-09-25. This used to carry a fifth row -- "a source that can
    spend must keep the conservative treatment", `("unknown",
    "provider_rate_limited", (Charge("request_usd", 1, True),))` -- which is the
    founder-rule violation itself: the same unknown-scope 429 continued to a
    sibling on a free source and was cooled with no retry on a paid one.

    Every reason that remains here is something the SOURCE reported, so each row
    holds for every account. What a paid source loses by not being treated
    conservatively is one more request, priced by the ceilings that constrain
    every attempt regardless; what it gained was a dead end its free neighbour
    never hit."""
    assert free_sibling_retry(scope=scope, failure_class=failure) is False


def test_capacity_signal_scope_stays_observed_evidence():
    """The policy reads the scope; it never rewrites the source's own signal."""
    signal = CapacitySignal("unknown", "provider_rate_limited", 60)
    assert signal.scope == "unknown"


# --------------------------------------------------------------------------
# End to end through the real router, adapter, journal and provider.
# --------------------------------------------------------------------------


def test_free_only_rate_limit_continues_to_a_sibling_model(agent, monkeypatch):
    """The founder's turn: 429 on the first model, answered by the next one."""
    alternate = integration._with_fallback(agent, monkeypatch)
    agent.capacity_failures[2] = 429
    assert integration.run(agent) == "finished exact answer"
    assert len(agent.wires) == 3 and len(agent.tools) == 1
    turn = agent.latest()
    assert turn.state == "completed"
    assert [round.state for round in turn.rounds] == ["received", "failed", "received"]
    assert agent.wires[-1][1]["body"]["model"] == alternate
    # The retry stays inside the same owner grant at the same zero ceilings.
    assert all(
        set(wire[1]["body"]["provider"]["max_price"].values()) == {"0"}
        for wire in agent.wires
    )


def test_free_only_rate_limit_does_not_cool_the_whole_source(agent):
    """A cooled source skips its own siblings, which is the same dead end."""
    agent.capacity_failures[1] = 429
    with pytest.raises(AllProvidersExhaustedError):
        integration.run(agent)
    provider = agent.served.context.model_selection.connection_id
    assert agent.served.router._quota.cooldown_remaining(provider, owner="owner") == 0


def test_exhausted_credit_still_stops_at_the_first_model(agent, monkeypatch):
    """402 is the account and it is about money: never widen, never retry."""
    integration._with_fallback(agent, monkeypatch)
    agent.capacity_failures[2] = 402
    with pytest.raises(AllProvidersExhaustedError):
        integration.run(agent)
    assert len(agent.wires) == 2 and len(agent.tools) == 1
    provider = agent.served.context.model_selection.connection_id
    assert agent.served.router._quota.cooldown_remaining(provider, owner="owner") > 0


def test_sibling_retries_are_bounded_and_every_attempt_is_recorded(agent, monkeypatch):
    """Three tries, then report -- with all of them in the failure evidence."""
    from tinyassets.providers import discovery_snapshot
    from tinyassets.providers.agent_model_plan import AgentModelPlan
    from tinyassets.providers.discovery_protocols import discovery_protocol
    from tinyassets.providers.model_policy import Catalog, ModelPolicy, ModelRef

    siblings = [f"future-vendor/model-{index}" for index in range(5)]
    original = discovery_snapshot.read_http_discovery_document

    def added(**kwargs):
        value = original(**kwargs)
        if "models/user" in kwargs["url"]:
            value["data"].extend(
                integration.authority_tests.snapshot_tests._model(name) for name in siblings
            )
        return value

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", added)
    snapshot = integration.authority_tests.snapshot_tests._refresh(agent.served.rig)
    selected = agent.served.context.model_selection
    agent.served.context = replace(agent.served.context, agent_model_plan=AgentModelPlan(
        Catalog("owner", agent.served.context.universe_dir.name, (snapshot.models,)),
        ModelPolicy(
            generation=7, mode="explicit", saved_default=selected,
            fallbacks=tuple(ModelRef(selected.connection_id, name) for name in siblings),
        ),
        replace(
            discovery_protocol(snapshot.models.provider_scope).text_interaction, needs_tools=True,
        ),
        policy_source="saved",
    ))
    agent.capacity_failures.update({index: 429 for index in range(1, 9)})
    with pytest.raises(AllProvidersExhaustedError) as error:
        integration.run(agent)
    # One first attempt plus at most three narrowed retries.
    assert len(agent.wires) == 4
    assert len(error.value.attempts) == 4
    assert {attempt.failure_class for attempt in error.value.attempts} == {"provider_rate_limited"}
    assert len({wire[1]["body"]["model"] for wire in agent.wires}) == 4


@pytest.mark.parametrize("kind,narrows", [("engine_inference", True), ("native_agent", False)])
def test_only_engine_inference_narrows_an_unproven_account_exhaustion(kind, narrows):
    """A native executor runs on ONE subscription, so its account IS the source.

    Its rate limit carries no capacity scope, so it observes as ``unknown``, and
    narrowing would mean nothing for a source where the account IS the source:
    unmetered-per-subscription is not free-per-model. Live proof this matters:
    narrowing there replaced the account exclusion that was letting a mixed
    universe fall through to its HTTP source, and two
    ``test_mixed_agent_execution`` cases went red in CI.

    The gate that stops it is ``execution_kind``, which describes the SOURCE, not
    the owner's plan -- so this survives removing the price gate unchanged
    (2026-09-25), and `test_one_path_for_every_account` pins that both kinds
    behave the same way whatever the account pays.
    """
    from types import SimpleNamespace

    from tinyassets.agent_turn_coordinator import AgentTurnCoordinator
    from tinyassets.providers.agent_capacity_boundary import CapacityBoundary
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.model_policy import Exhaustion, ModelRef

    ref = ModelRef("some-source", "some-model")
    turn = AgentTurnCoordinator.__new__(AgentTurnCoordinator)
    turn.execution_kind = kind
    turn.free_sibling_retries = 0
    turn.context = SimpleNamespace(model_selection=ref)
    # A plan object at all: the policy no longer asks it for ceilings.
    turn.plan = SimpleNamespace()
    turn.config = ModelConfig(absolute_cap_s=120)
    boundary = CapacityBoundary(
        Exhaustion("account", ref), True, "provider_rate_limited", 60.0, "unknown",
    )
    exhaustion, narrowed = turn._narrowed(boundary)
    assert narrowed is narrows
    assert exhaustion.scope == ("model" if narrows else "account")
    assert turn.free_sibling_retries == (1 if narrows else 0)


def test_narrowing_cannot_reach_a_connection_the_account_rule_excludes(agent, monkeypatch):
    """The narrowing removed the only thing vouching for a sibling CONNECTION.

    An account exhaustion is what excludes another connection that shares this
    source's scope without a verified account identity
    (``capacity_identity_unverified``). Narrowing to one model takes that
    exclusion away, so the turn must not reach such a connection THROUGH the
    narrowing: a narrowed candidate has to be a sibling on the same grant, and
    anything else falls back to the unnarrowed exclusion — which here refuses.

    Founder review of #3981, finding 2: this guard had no test behind it, and
    its first version was too blunt, stopping turns that the conservative rule
    would happily have advanced (it broke two native mixed-source tests).
    """
    from tinyassets.providers.agent_model_plan import AgentModelPlan
    from tinyassets.providers.discovery_protocols import discovery_protocol
    from tinyassets.providers.model_policy import (
        Catalog,
        ConnectionModels,
        Model,
        ModelPolicy,
        ModelRef,
        Pricing,
    )

    snapshot = integration.authority_tests.snapshot_tests._refresh(agent.served.rig)
    selected = agent.served.context.model_selection
    # Same provider scope, no verified account identity: the account rule calls
    # this one `capacity_identity_unverified` and refuses it.
    elsewhere = ConnectionModels(
        connection_id="api_key_http:some-other-definition",
        provider_scope=snapshot.models.provider_scope, source_kind="http", freshness="fresh",
        owner_filtered=True, executor_tools=True,
        models=(Model("other-vendor/free-model", True, frozenset({"text"}),
                      context_tokens=100_000, pricing=Pricing("fresh", unmetered=True)),),
    )
    agent.served.context = replace(agent.served.context, agent_model_plan=AgentModelPlan(
        Catalog("owner", agent.served.context.universe_dir.name,
                (snapshot.models, elsewhere)),
        ModelPolicy(
            generation=7, mode="explicit", saved_default=selected,
            fallbacks=(ModelRef(elsewhere.connection_id, "other-vendor/free-model"),),
        ),
        replace(
            discovery_protocol(snapshot.models.provider_scope).text_interaction, needs_tools=True,
        ),
        policy_source="saved",
    ))
    agent.capacity_failures[1] = 429
    with pytest.raises(AllProvidersExhaustedError) as error:
        integration.run(agent)
    # One request, to the grant that was actually authorized for this source.
    assert len(agent.wires) == 1 and not agent.tools
    assert {attempt.provider for attempt in error.value.attempts} == {selected.connection_id}


# --------------------------------------------------------------------------
# The owner's own failure record must carry the source's words, not our class.
# --------------------------------------------------------------------------


def test_capacity_detail_carries_the_sources_own_words(agent):
    """`Detail: "provider_rate_limited"` told the founder only our class name."""
    from tinyassets import universe_server

    agent.capacity_failures[1] = 429
    with pytest.raises(AllProvidersExhaustedError) as error:
        integration.run(agent)
    detail = error.value.attempts[-1].detail
    assert "429" in detail and "synthetic refusal" in detail
    record = universe_server._served_failure_record(error.value)
    assert record.code == "provider_rate_limited"
    assert "synthetic refusal" in record.provider_detail
    assert record.provider_detail != "provider_rate_limited"


def test_capacity_detail_is_bounded(agent):
    """A source can answer with a megabyte; the record takes one line of it."""
    from tinyassets.conversation_failure import DETAIL_LIMIT
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider

    body = json.dumps({"error": {"message": "A" * 4000}})
    detail = ApiKeyHttpProvider._capacity_detail(429, {"body": body})
    assert len(detail) <= DETAIL_LIMIT
    assert "A" * 400 not in detail


@pytest.mark.parametrize("secret,body", [
    ("sk-or-v1-0123456789abcdefghijklmnop",
     '{"error":{"message":"key sk-or-v1-0123456789abcdefghijklmnop rejected"}}'),
    ("Bearer hunter2hunter2hunter2",
     "Authorization: Bearer hunter2hunter2hunter2\nrate limited"),
    ("topsecretvalue0123",
     '{"headers":{"x-api-key":"topsecretvalue0123"},"error":"rate limited"}'),
    ("topsecretvalue0123", "rate limited; retry at /v1/keys?api_key=topsecretvalue0123"),
])
def test_capacity_detail_passes_the_secret_scrub(agent, secret, body):
    """The body is the owner's own words, and still untrusted transport.

    This asserts the scrub gate FIRES, not merely that the result is short --
    a length-only assertion stayed green with ``redacted_failure_detail``
    deleted, which made it decorative (founder review of #3981, finding 1).
    """
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider

    detail = ApiKeyHttpProvider._capacity_detail(429, {"body": body})
    assert secret not in detail
    assert "[redacted]" in detail
    # The source's actual explanation survives the scrub.
    assert "rate limited" in detail or "rejected" in detail


# --------------------------------------------------------------------------
# Withholding the cooldown buys a sibling attempt. Once there is no sibling
# left to buy, the source gets cooled -- or a daily cap costs four requests a
# turn, every turn, forever.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("retry_after,budget,expected", [
    (30.0, 120.0, True),      # a wait a turn can absorb
    (120.0, 120.0, True),     # exactly the budget is not longer than it
    (600.0, 120.0, False),    # the source named longer than a turn may live
    (None, 120.0, True),      # no window named
    (600.0, None, True),      # no turn budget known
    (float("inf"), 120.0, True),   # malformed: keep the prior answer
    (float("nan"), 120.0, True),
    (600.0, 0, True),         # malformed budget: keep the prior answer
    (600.0, True, True),      # a bool is not a budget
])
def test_a_window_longer_than_the_turn_takes_the_cooldown_back(retry_after, budget, expected):
    assert free_sibling_retry(
        scope="unknown", failure_class="provider_rate_limited",
        retry_after_s=retry_after, turn_budget_s=budget,
    ) is expected


def test_cooling_a_source_honours_its_own_retry_after():
    """Restrictive-only, and the source's stated window wins over the default."""
    from tinyassets.providers.quota import QuotaTracker
    from tinyassets.providers.router import COOLDOWN_UNAVAILABLE, ProviderRouter

    router = ProviderRouter.__new__(ProviderRouter)
    router._quota = QuotaTracker()
    assert router.cool_source("some-source", retry_after_s=45, owner="owner") == 46
    assert router._quota.cooldown_remaining("some-source", owner="owner") > 0
    assert router.cool_source("other-source", owner="owner") == COOLDOWN_UNAVAILABLE
    assert router.cool_source(
        "third", retry_after_s=float("nan"), owner="owner",
    ) == COOLDOWN_UNAVAILABLE
    with pytest.raises(ValueError):
        router.cool_source("", owner="owner")


def _cooldown(agent):
    provider = agent.served.context.model_selection.connection_id
    return agent.served.router._quota.cooldown_remaining(provider, owner="owner")


def test_the_last_narrowed_attempt_cools_the_source(agent, monkeypatch):
    """Four refusals exhaust the budget, and the fourth cools the connection."""
    integration._with_fallback(agent, monkeypatch)
    agent.capacity_failures.update({index: 429 for index in range(1, 9)})
    with pytest.raises(AllProvidersExhaustedError):
        integration.run(agent)
    assert _cooldown(agent) > 0, "the source stayed hot after its last attempt"


def test_a_daily_cap_does_not_cost_a_full_budget_every_turn(agent, monkeypatch):
    """The live shape of a free account at its daily cap: every model refuses.

    The first turn spends its budget discovering that. Every turn after it must
    be answered off the cooldown, not by sending the same requests again.
    """
    integration._with_fallback(agent, monkeypatch)
    agent.capacity_failures.update({index: 429 for index in range(1, 40)})
    with pytest.raises(AllProvidersExhaustedError):
        integration.run(agent)
    spent = len(agent.wires)
    assert spent > 1, "the first turn did not try a sibling at all"
    assert _cooldown(agent) > 0
    with pytest.raises(AllProvidersExhaustedError) as second:
        integration.run(agent)
    assert len(agent.wires) == spent, "the second turn paid for the cap again"
    assert [a.status for a in second.value.attempts] == ["skipped"]
    assert second.value.attempts[0].skip_class == "quota_or_cooldown"


def test_a_short_window_still_buys_the_sibling_attempt(agent, monkeypatch):
    """The fix must not cool a source whose sibling would have answered."""
    alternate = integration._with_fallback(agent, monkeypatch)
    agent.capacity_failures[2] = 429
    assert integration.run(agent) == "finished exact answer"
    assert agent.wires[-1][1]["body"]["model"] == alternate
    assert _cooldown(agent) == 0, "a source that answered was cooled anyway"


def test_moving_to_another_source_cools_the_one_left_behind(agent, monkeypatch):
    """A source the turn walks away from is done with, so its cooldown applies.

    The account exclusion legitimately allows an independently-scoped second
    connection. Reaching it means this source got no sibling attempt out of its
    withheld cooldown, so nothing was bought and the cooldown goes back on.
    """
    from tinyassets.providers.agent_model_plan import AgentModelPlan
    from tinyassets.providers.discovery_protocols import discovery_protocol
    from tinyassets.providers.model_policy import (
        Catalog,
        ConnectionModels,
        Model,
        ModelPolicy,
        ModelRef,
        Pricing,
    )

    snapshot = integration.authority_tests.snapshot_tests._refresh(agent.served.rig)
    selected = agent.served.context.model_selection
    # A DIFFERENT provider scope, so the account exclusion admits it.
    independent = ConnectionModels(
        connection_id="api_key_http:an-independent-definition",
        provider_scope="an-independent-scope", source_kind="http", freshness="fresh",
        owner_filtered=True, executor_tools=True,
        models=(Model("other-vendor/free-model", True, frozenset({"text"}),
                      context_tokens=100_000, pricing=Pricing("fresh", unmetered=True)),),
    )
    agent.served.context = replace(agent.served.context, agent_model_plan=AgentModelPlan(
        Catalog("owner", agent.served.context.universe_dir.name,
                (snapshot.models, independent)),
        ModelPolicy(
            generation=7, mode="explicit", saved_default=selected,
            fallbacks=(ModelRef(independent.connection_id, "other-vendor/free-model"),),
        ),
        replace(
            discovery_protocol(snapshot.models.provider_scope).text_interaction, needs_tools=True,
        ),
        policy_source="saved",
    ))
    agent.capacity_failures[1] = 429
    with pytest.raises(BaseException):
        integration.run(agent)
    assert _cooldown(agent) > 0, "the source the turn walked away from stayed hot"
