"""A post-reply learning call must never lock the founder out of the next turn.

Live 2026-09-25 (16:01 and 16:05 PT), production ``919ece4b``, free-only account,
universe ``u-01ky3zh1arr8qth8jee7zx63pq`` on OpenRouter free models. One message
produced three log lines in this order:

1. the founder's turn was ANSWERED (``inclusionai/ling-3.0-flash-fin:free``);
2. ``converse`` then called ``extract_learning``, a SECOND inference on the same
   free source inside the same request. It took an HTTP 429 with no
   ``Retry-After``, so ``Provider api_key_http:provdef_... rate-limited/overloaded
   (provider_rate_limited), cooldown 120s`` -- our own invented fixed window,
   written to the router's SHARED quota map -- then ``converse: learning
   persistence failed``;
3. the founder's NEXT message, inside those two minutes, never reached a model:
   ``class=None attempts=1 [api_key_http:... skipped quota_or_cooldown quota or
   cooldown gate]``, rendered as *"Before anything reached your model -- we could
   not identify why... Detail: "quota or cooldown gate""*.

So on a free source every answered turn bought a ~2 minute lockout, and the
lockout described itself as unexplainable. Three separable defects:

(a) a SECONDARY call (one the platform makes for its own bookkeeping while the
    founder's turn is in flight) wrote the shared cooldown. Note that the
    foreground turn's own 429 would NOT have: it carries an ``agent_request``, so
    the source raises ``SelectedModelCapacityError`` and #3981's free-only rule
    withholds the cooldown. The learning call has no agent request, takes the
    plain ``ProviderRateLimitedError`` path, and was cooling the source that the
    reply had just proved healthy.
(b) ``_attempt_class`` ignores SKIPPED attempts, so the one thing the router had
    measured -- its own cooldown gate, with the seconds remaining right there on
    the attempt -- reached the founder as "we could not identify why".
(c) the answered-by line named the routing identity
    (``api_key_http:provdef_ed0169c8...``) rather than the connection.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import pytest

from tests import test_interactive_http_agent as integration
from tests.inference_usage_helpers import accounting_resolver
from tinyassets import universe_intelligence
from tinyassets.exceptions import AllProvidersExhaustedError, ProviderRateLimitedError
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic

rig = integration.rig
reader = integration.reader
served = integration.served
agent = integration.agent


# --------------------------------------------------------------------------
# (a) A secondary call never writes the shared cooldown.
# --------------------------------------------------------------------------


class _RateLimited:
    """The live wire: 429 with no Retry-After, so our fixed 120s window applies."""

    def __init__(self):
        self.calls = 0

    def close(self):
        pass

    def request(self, verb, document):
        self.calls += 1
        return {"status": 429, "headers": {}, "body": '{"error":{"message":"rate-limited"}}'}


def _rate_limit(agent, monkeypatch):
    wire = _RateLimited()
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy",
                        accounting_resolver(lambda *a, **k: wire))
    return wire


def _secondary_call(agent, config):
    """One plain (non-agent) writer call, exactly as extract_learning makes it."""
    from tinyassets.providers.call import call_provider

    return call_provider(
        "founder said this", system="extract what was taught", role="writer",
        universe_context=agent.served.context, config=config,
        operation="converse", retry_on_exhaustion=False,
    )


def _provider(agent):
    return agent.served.context.model_selection.connection_id


def test_a_secondary_rate_limit_leaves_the_shared_cooldown_untouched(agent, monkeypatch):
    """The whole bug in one assertion: learning's 429 must cost the founder nothing."""
    wire = _rate_limit(agent, monkeypatch)
    config = replace(universe_intelligence._sandboxed_config(agent.served.context),
                     secondary_call=True)
    with pytest.raises(AllProvidersExhaustedError):
        _secondary_call(agent, config)
    assert wire.calls == 1  # asked once, no retry storm
    assert agent.served.router._quota.cooldown_remaining(_provider(agent), owner="owner") == 0
    assert agent.served.router._quota.available(_provider(agent), owner="owner") is True


def test_a_foreground_rate_limit_still_cools_the_source(agent, monkeypatch):
    """The guard against over-fixing: an unmarked call keeps the measured window."""
    _rate_limit(agent, monkeypatch)
    config = universe_intelligence._sandboxed_config(agent.served.context)
    assert config.secondary_call is False
    with pytest.raises(AllProvidersExhaustedError):
        _secondary_call(agent, config)
    assert agent.served.router._quota.cooldown_remaining(_provider(agent), owner="owner") > 0


def test_extract_learning_marks_its_own_call_secondary(monkeypatch, tmp_path):
    """Assert the argument the router branches on, not the absence of an effect."""
    from tinyassets.providers.base import UniverseContext

    captured = {}

    def capture(prompt, system="", **kwargs):
        captured.update(kwargs)
        return "{}"

    monkeypatch.setattr(universe_intelligence, "call_provider", capture)
    (tmp_path / "u-learn").mkdir()
    ctx = UniverseContext(universe_dir=tmp_path / "u-learn", config=None)
    universe_intelligence.extract_learning("taught", "replied", ctx)
    assert captured["config"].secondary_call is True
    assert captured["retry_on_exhaustion"] is False


def test_learning_rate_limit_leaves_the_founder_s_next_turn_eligible(agent, monkeypatch):
    """(1) -> (3) end to end: answered turn, 429 on learning, next turn answered."""
    assert integration.run(agent) == "finished exact answer"
    answered_wires = len(agent.wires)

    original = ApiKeyHttpProvider._resolve_proxy
    _rate_limit(agent, monkeypatch)
    config = replace(universe_intelligence._sandboxed_config(agent.served.context),
                     secondary_call=True)
    with pytest.raises(AllProvidersExhaustedError):
        _secondary_call(agent, config)
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy", accounting_resolver(original))

    # The live symptom was this call raising with one skipped attempt instead.
    assert integration.run(agent) == "finished exact answer"
    assert len(agent.wires) > answered_wires


def test_converse_logs_a_note_when_learning_is_skipped(agent, monkeypatch, caplog):
    """Never raised, never a traceback, never user-visible: one honest line."""
    from tinyassets.providers import call as provider_calls

    def exhausted(*args, **kwargs):
        raise AllProvidersExhaustedError(
            "Served provider exhausted; universe "
            + AllProvidersExhaustedError.NO_WIDENING_MESSAGE,
            attempts=[ProviderAttemptDiagnostic(
                provider=_provider(agent), status="failed",
                skip_class="quota_or_cooldown", detail="rate limited",
                failure_class="provider_rate_limited",
            )],
        )

    monkeypatch.setattr(provider_calls, "call_provider", exhausted)
    monkeypatch.setattr(universe_intelligence, "call_provider", exhausted)
    with caplog.at_level(logging.INFO, logger="tinyassets.universe_intelligence"):
        skipped = universe_intelligence._learn_from_turn(
            agent.served.context, universe_dir=agent.served.context.universe_dir,
            universe_id=agent.served.context.universe_dir.name,
            founder_message="taught", reply="replied", actor_id="owner", agent_id="main",
        )
    assert skipped is False
    records = [r for r in caplog.records if "learning" in r.getMessage()]
    assert records and all(r.exc_info is None for r in records)


# --------------------------------------------------------------------------
# (b) A measured cooldown gate is an explanation, with its seconds.
# --------------------------------------------------------------------------


def _gated(remaining=41):
    """Exactly the live attempts list: one provider, skipped by our own gate."""
    return AllProvidersExhaustedError(
        "Served provider 'api_key_http:provdef_x' exhausted; universe "
        + AllProvidersExhaustedError.NO_WIDENING_MESSAGE,
        attempts=[ProviderAttemptDiagnostic(
            provider="api_key_http:provdef_x", status="skipped",
            skip_class="quota_or_cooldown", detail="quota or cooldown gate",
            cooldown_remaining_s=remaining,
        )],
    )


def test_a_cooldown_gate_is_never_an_unknown_failure():
    from tinyassets.universe_server import _served_failure_code

    assert _served_failure_code(_gated()) == "quota_or_cooldown"


def test_the_gate_notice_names_the_remaining_seconds():
    from tinyassets.universe_server import _served_failure_notice, _served_failure_record

    record = _served_failure_record(_gated(41))
    assert record.code == "quota_or_cooldown"
    assert record.retry_after_s == 41
    notice = _served_failure_notice(_gated(41))
    assert "41 second" in notice
    assert "could not identify why" not in notice
    # Nothing was sent, and the notice must keep saying so.
    assert record.effects == "none" and record.stage == "before_send"


def test_a_class_that_waiting_cannot_fix_carries_no_wait():
    """"Reconnect your provider" must never be paired with "wait 90 seconds"."""
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic
    from tinyassets.universe_server import _served_failure_notice, _served_failure_record

    exc = AllProvidersExhaustedError("exhausted", attempts=[ProviderAttemptDiagnostic(
        provider="api_key_http:provdef_x", status="failed", skip_class="auth_invalid",
        detail="401 unauthorized: invalid_token", retry_after_s=90,
        failure_class=None,
    )])
    record = _served_failure_record(exc)
    assert record.code == "auth_invalid" and record.retry_after_s is None
    assert "second" not in _served_failure_notice(exc)


def test_a_gate_with_no_measured_remaining_still_classifies():
    from tinyassets.universe_server import _served_failure_notice, _served_failure_record

    record = _served_failure_record(_gated(None))
    assert record.code == "quota_or_cooldown" and record.retry_after_s is None
    assert "could not identify why" not in _served_failure_notice(_gated(None))


def test_an_unmeasured_skip_stays_honestly_unknown():
    """A provider absent from the registry explains nothing; do not promote guesses."""
    from tinyassets.universe_server import _served_failure_code

    exc = AllProvidersExhaustedError("exhausted", attempts=[ProviderAttemptDiagnostic(
        provider="codex", status="skipped", skip_class="not_in_registry",
        detail="provider name not registered with daemon",
    )])
    assert _served_failure_code(exc) == "unknown"


def test_an_attempt_that_actually_failed_outranks_a_later_gate():
    """A failure that happened explains more than a gate that skipped someone else."""
    from tinyassets.universe_server import _served_failure_code

    exc = AllProvidersExhaustedError("exhausted", attempts=[
        ProviderAttemptDiagnostic(
            provider="a", status="failed", skip_class="provider_error",
            detail="stream ended", failure_class="provider_idle_timeout",
        ),
        ProviderAttemptDiagnostic(
            provider="b", status="skipped", skip_class="quota_or_cooldown",
            detail="quota or cooldown gate", cooldown_remaining_s=90,
        ),
    ])
    assert _served_failure_code(exc) == "provider_idle_timeout"


def test_a_stored_gate_record_round_trips_with_its_seconds():
    from tinyassets.conversation_failure import (
        normalize_turn_failure,
        read_turn_failure,
        turn_failure,
    )

    record = turn_failure("quota_or_cooldown", stage="before_send", effects="none",
                          retry_after_s=41, ref="abc123")
    stored = normalize_turn_failure(record)
    assert stored["retry_after_s"] == 41
    assert read_turn_failure("platform", json.dumps(stored)).retry_after_s == 41
    # A legacy row that never carried one still reads.
    legacy = {"version": 1, "kind": "turn_failed", "code": "quota_or_cooldown"}
    assert read_turn_failure("platform", json.dumps(legacy)).retry_after_s is None


@pytest.mark.parametrize("value", [0, -5, "41", 41.5, True, 10**9])
def test_an_invalid_wait_is_dropped_not_rendered(value):
    from tinyassets.conversation_failure import failure_notice, turn_failure

    record = turn_failure("quota_or_cooldown", retry_after_s=value)
    assert record.retry_after_s is None
    assert "second" not in failure_notice(record)


# --------------------------------------------------------------------------
# (c) The answered-by line names the connection, not the definition id.
# --------------------------------------------------------------------------


def test_a_bootstrap_connection_resolves_to_its_installed_preset_name(agent, monkeypatch):
    """``model:<preset id>`` is what the guided sign-in deposits; read its name."""
    from tinyassets.providers.source_display import source_display_name

    monkeypatch.setattr("tinyassets.broker.owner_identities.admitted_owner",
                        lambda root, *, center: "owner")
    ledger = agent.served.rig.ledger
    with ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET destination = ? "
                     "WHERE connection_id = ?",
                     ("model:openrouter_user_models_v1", "conn-models"))
        conn.commit()
    assert source_display_name(
        base=agent.served.rig.base, universe_id="u-models",
        provider=_provider(agent),
    ) == "OpenRouter"


def test_a_hand_made_connection_shows_the_owner_s_own_destination(agent, monkeypatch):
    monkeypatch.setattr("tinyassets.broker.owner_identities.admitted_owner",
                        lambda root, *, center: "owner")

    from tinyassets.providers.source_display import source_display_name

    assert source_display_name(
        base=agent.served.rig.base, universe_id="u-models",
        provider=_provider(agent),
    ) == "compute:models"


def test_a_grant_bound_elsewhere_never_lends_its_name(agent, monkeypatch):
    """Never label one universe's source with another universe's connection."""
    from tinyassets.providers import definition as definitions
    from tinyassets.providers.source_display import source_display_name

    monkeypatch.setattr("tinyassets.broker.owner_identities.admitted_owner",
                        lambda root, *, center: "owner")

    foreign = agent.served.rig.ledger.grant_connection(
        grant_id="grant-other", connection_id="conn-models",
        owner_user_id="owner", universe_id="u-other",
    )
    borrowed = definitions.register_definition(
        universe_id="u-models", owner_user_id="owner", access_method="api_key_http",
        protocol="openai_chat", model="legacy-fixed", ref=foreign.grant_id,
    )
    assert source_display_name(
        base=agent.served.rig.base, universe_id="u-models",
        provider=f"api_key_http:{borrowed.id}",
    ) == ""


@pytest.mark.parametrize("provider", ["codex", "claude-code", "api_key_http:absent", ""])
def test_an_unresolvable_source_reports_no_display_name(agent, provider):
    from tinyassets.providers.source_display import source_display_name

    assert source_display_name(
        base=agent.served.rig.base, universe_id="u-models", provider=provider,
    ) == ""


def test_the_writer_receipt_carries_the_connection_name(agent, monkeypatch):
    """What the app renders: the connection's name plus the answering model."""
    from tinyassets.providers.execution_receipt import WriterExecutionReceipt

    monkeypatch.setattr("tinyassets.broker.owner_identities.admitted_owner",
                        lambda root, *, center: "owner")
    ledger = agent.served.rig.ledger
    with ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET destination = ? "
                     "WHERE connection_id = ?",
                     ("model:openrouter_user_models_v1", "conn-models"))
        conn.commit()
    receipt = WriterExecutionReceipt()
    assert integration.run(agent, receipt.observe) == "finished exact answer"
    projected = receipt.projection()
    usage = projected.pop("usage")
    assert usage["dispatched"] == len(agent.wires) == 2
    assert usage["quota_authoritative"] is False
    assert usage["sources"][0]["purpose"] == "reply"
    assert projected == {
        "provider": _provider(agent),
        "provider_display": "OpenRouter",
        "model": "actual-answer-model",
        "model_status": "reported",
    }


def test_a_receipt_with_no_display_name_keeps_its_legacy_shape(agent):
    """Stored history normalizes both shapes; nothing invents a label."""
    from tinyassets.providers.execution_receipt import normalize_execution_receipt

    legacy = {"provider": "codex", "model": "gpt-x", "model_status": "reported"}
    assert normalize_execution_receipt(legacy) == legacy
    assert normalize_execution_receipt({**legacy, "provider_display": "Codex"}) == {
        **legacy, "provider_display": "Codex"}
    # An empty label is absent, never a rendered blank.
    assert normalize_execution_receipt({**legacy, "provider_display": ""}) is None


def test_the_app_prefers_the_display_name_over_the_routing_identity():
    """The renderer must read the label; a raw provdef id is the reported bug."""
    from pathlib import Path

    app = Path(__file__).resolve().parent.parent / "tinyassets" / "onboarding" / "app.html"
    text = app.read_text(encoding="utf-8")
    assert "const display=executionLabel(receipt&&receipt.provider_display,200);" in text
    # The label is preferred, the routing identity is the fallback, and the model
    # is still named beside it.
    assert '"Answered by "+(display||provider)+" · "+(model||' in text


# --------------------------------------------------------------------------
# The rate-limit path itself stays classified; only the cooldown write changes.
# --------------------------------------------------------------------------


def test_a_secondary_call_still_reports_its_real_failure_class(agent, monkeypatch):
    _rate_limit(agent, monkeypatch)
    config = replace(universe_intelligence._sandboxed_config(agent.served.context),
                     secondary_call=True)
    with pytest.raises(AllProvidersExhaustedError) as caught:
        _secondary_call(agent, config)
    assert caught.value.failure_class == ProviderRateLimitedError.failure_class
    assert [a.status for a in caught.value.attempts] == ["failed"]


class _Unauthorized:
    def close(self):
        pass

    def request(self, verb, document):
        return {"status": 401, "headers": {}, "body": '{"error":{"message":"refused"}}'}


def _reconnect_marks(monkeypatch):
    """Record every reconnect mark the router writes for the source it used."""
    from tinyassets.providers.source_health import SOURCE_HEALTH

    marked = []
    monkeypatch.setattr(type(SOURCE_HEALTH), "authentication_failed",
                        lambda self, key: marked.append(key))
    return marked


def test_a_secondary_auth_failure_does_not_quarantine_the_source(agent, monkeypatch):
    """A reconnect mark removes the source from the next turn's plan: same lockout.

    The founder's reply succeeded on this exact credential moments earlier, which
    already cleared this key; letting an optional call put it back would send them
    to reconnect a connection that works.
    """
    marked = _reconnect_marks(monkeypatch)
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy",
                        accounting_resolver(lambda *a, **k: _Unauthorized()))
    config = replace(universe_intelligence._sandboxed_config(agent.served.context),
                     secondary_call=True)
    with pytest.raises(AllProvidersExhaustedError) as caught:
        _secondary_call(agent, config)
    assert [a.skip_class for a in caught.value.attempts] == ["auth_invalid"]
    assert marked == []


def test_a_foreground_auth_failure_still_quarantines_the_source(agent, monkeypatch):
    """The guard against over-fixing: a real turn's 401 still asks for a reconnect."""
    marked = _reconnect_marks(monkeypatch)
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy",
                        accounting_resolver(lambda *a, **k: _Unauthorized()))
    config = universe_intelligence._sandboxed_config(agent.served.context)
    with pytest.raises(AllProvidersExhaustedError):
        _secondary_call(agent, config)
    assert len(marked) == 1
    assert marked[0].universe == "u-models"


def test_a_secondary_call_still_respects_a_cooldown_another_call_set(agent, monkeypatch):
    """Reading the gate is restrictive; only writing it could hurt the founder."""
    agent.served.router._quota.cooldown(_provider(agent), 120, owner="owner")
    wire = _rate_limit(agent, monkeypatch)
    config = replace(universe_intelligence._sandboxed_config(agent.served.context),
                     secondary_call=True)
    with pytest.raises(AllProvidersExhaustedError) as caught:
        _secondary_call(agent, config)
    assert wire.calls == 0  # never hammered a source known to be cooling
    assert [a.skip_class for a in caught.value.attempts] == ["quota_or_cooldown"]


def test_secondary_calls_never_enter_the_turn_coordinator(agent):
    """PR #3986 cools from the coordinator; a secondary call must never reach it.

    The coordinator is only built for a turn with engine tools
    (``_call_writer`` -> ``make_interactive_agent_turn``), and a secondary call
    fails closed to the WebFetch-only floor, so give-up cooling cannot fire for
    one however that PR lands.
    """
    config = universe_intelligence._sandboxed_config(agent.served.context)
    assert config.engine_mcp_enabled is False
    assert replace(config, secondary_call=True).engine_mcp_enabled is False
