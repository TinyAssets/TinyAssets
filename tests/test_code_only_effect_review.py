"""Real effect/review/admission composition; no model or network is contacted."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from tests.test_run_provider_session import (
    _branch,
    _seed_open_serving_assignment,
    _seed_serving_assignment,
)
from tinyassets import agent_rules, effectors
from tinyassets.daemon_server import set_founder_home
from tinyassets.effectors import authenticated_external_call as aec
from tinyassets.foreground_run_provider import _ForegroundRunProviderSession
from tinyassets.platform_runtime_provenance import (
    CLOUD,
    ProcessProvenanceObservation,
    RuntimeProvenance,
)
from tinyassets.providers.base import BaseProvider, ProviderResponse, UniverseContext
from tinyassets.providers.call import UniverseBoundProviderCall
from tinyassets.providers.router import ProviderRouter
from tinyassets.runs import create_run, update_run_status
from tinyassets.storage.outbound_connections import ConnectionLedger
from tinyassets.storage.provider_work_authority import db_path


class Terminal(BaseProvider):
    name = family = "codex"
    # This synthetic terminal returns text and implements no tools. Actual
    # native adapters are tested separately and refuse text-only requests.
    supports_text_only = True

    def __init__(self):
        self.calls = []
        self.answers = ['{"verdict":"proceed","reason":"within scope"}']
        self.after_call = lambda: None

    async def complete(self, prompt, system, config, *, universe_dir=None):
        self.calls.append((prompt, system, config))
        self.after_call()
        answer = self.answers.pop(0) if self.answers else "invalid"
        return ProviderResponse(text=answer, provider=self.name, model="synthetic",
                                family=self.family, latency_ms=1, input_tokens=5,
                                output_tokens=5, cost_microunits=1)


@pytest.fixture
def rig(tmp_path, monkeypatch, request):
    import tinyassets.platform_runtime_provenance as provenance
    from tinyassets.providers import call
    from tinyassets.storage.effector_consents import grant_consent

    observation = ProcessProvenanceObservation(resolver=lambda: RuntimeProvenance(
        verdict=CLOUD, reason="instance_match", metadata_reachable=True,
        expected_identity_prepared=True))
    observation.observe()
    monkeypatch.setattr(provenance, "_PROCESS_OBSERVATION", observation)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", "1")
    terminal = Terminal()
    router = ProviderRouter({"codex": terminal})
    monkeypatch.setattr(call, "get_provider_router", lambda: router)
    set_founder_home(tmp_path, founder_sub="acct_alice", universe_id="universe_alice",
                     platform_generated=True)
    if (getattr(request.node, "callspec", None)
            and request.node.callspec.params.get("case") == "budget"):
        monkeypatch.setattr("tinyassets.provider_serving_binding._MAX_BINDING_INVOCATIONS", 1)
    manifest = getattr(request, "param", "legacy") == "manifest"
    from tinyassets.provider_assignment_manifest import ModelAccess

    if getattr(request, "param", "legacy") == "http":
        from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider

        selected = _seed_open_serving_assignment(tmp_path, monkeypatch)
        terminal.name = selected
        router.register(terminal)
        complete = ApiKeyHttpProvider._complete_sync

        def capture_http(self, prompt, system, config, **kwargs):
            terminal.calls.append((prompt, system, config))
            return complete(self, prompt, system, config, **kwargs)

        class ModelProxy:
            def request(self, verb, wire):
                assert verb == "POST"
                assert wire["body"].keys() <= {
                    "model", "messages", "system", "temperature", "max_tokens"}
                return {"status": 200, "body": json.dumps({
                    "choices": [{"message": {"content": terminal.answers.pop(0)}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 5}})}

            def close(self):
                pass

        monkeypatch.setattr(ApiKeyHttpProvider, "_complete_sync", capture_http)
        monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy", lambda *a, **k: ModelProxy())
    else:
        _seed_serving_assignment(
            tmp_path, model_access={"codex": ModelAccess("explicit", ("",))} if manifest else None)
    universe = tmp_path / "universe_alice"
    branch = _branch(node_count=1)
    node = branch.node_defs[0]
    node.prompt_template = ""
    node.effects = [aec.EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL]
    node.output_keys = ["out"]
    run_id = create_run(tmp_path, branch_def_id=branch.branch_def_id, thread_id="synthetic",
                        inputs={}, actor="universe:universe_alice", owner_user_id="acct_alice")
    update_run_status(tmp_path, run_id, status="running")

    def provider(prompt, system="", *, role="writer", config=None,
                 universe_context=None, operation=None, **kwargs):
        return router.call_sync(role, prompt, system, config,
                                universe_context=universe_context, operation=operation).text

    # The actual router governs every invocation, no session's mock fast path.
    provider.__module__ = "tinyassets.providers.call"
    from tinyassets.foreground_run_provider import captured_work_preference

    session = _ForegroundRunProviderSession(
        tmp_path, universe_id=universe.name, principal_id="acct_alice", provider_call=provider,
        model_preference_data=captured_work_preference(
            tmp_path, universe_id=universe.name, principal_id="acct_alice") if manifest else None)
    session.prepare(run_id=run_id, branch=branch, branch_version_id=None,
                    allowed_statuses={"running"})
    wrapper = UniverseBoundProviderCall(session, UniverseContext(universe_dir=universe),
                                       "run_graph")
    ledger = ConnectionLedger(tmp_path / "outbound.db",
                              verify_authenticated_principal=lambda: "acct_alice")
    ledger.create_connection(connection_id="synthetic-http", owner_user_id="acct_alice",
                             connection_class="outbound-http", scopes=("POST",),
                             provider="http", destination="example.com",
                             credential_ref="vault://http/synthetic", connection_type="http",
                             auth_scheme="bearer", allowed_endpoints=[{
                                 "host": "example.com", "path_template": "/synthetic",
                                 "methods": ["POST"]}])
    ledger.grant_connection(grant_id="synthetic-grant", connection_id="synthetic-http",
                            owner_user_id="acct_alice", universe_id=universe.name)
    grant_consent(universe, sink=aec.EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
                  destination="example.com", granted_by="acct_alice")
    sends = []

    class Proxy:
        def request(self, verb, wire):
            sends.append((verb, wire))
            return {"status": 200, "body": '{"ok":true}'}

        def close(self):
            pass

    monkeypatch.setattr(aec, "_open_connection_proxy", lambda **kwargs: Proxy())
    packet = {"sink": aec.EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
              "connection_id": "synthetic-http", "grant_id": "synthetic-grant",
              "verb": "POST", "request": {"path": "/synthetic", "body": {"text": "test"}}}

    def fire(*, bound_run=None, bound_provider=None):
        chain = effectors.EffectChain(
            run_id=run_id if bound_run is None else bound_run, base_path=str(universe),
            review_provider=wrapper if bound_provider is None else bound_provider,
            review_active=True)
        result = effectors._fire_node_effects(node, {"out": json.dumps(packet)}, chain=chain,
                                             schema_defaulted=set(), node_key="n1")
        return result[aec.EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL]

    yield locals()
    session.close()


@pytest.mark.parametrize("rig", ["legacy", "manifest", "http"], indirect=True)
def test_code_only_effect_reaches_review_and_fake_send(rig):
    result = rig["fire"]()
    assert result.get("delivered"), result
    assert len(rig["terminal"].calls) == len(rig["sends"]) == 1
    session = rig["session"]
    assert session._receipt.max_invocations == 2
    assert session._receipt.principal_id == "acct_alice"
    assert session._receipt.work_item_id == rig["run_id"]
    assert not rig["terminal"].calls[0][2].engine_mcp_enabled
    assert rig["terminal"].calls[0][2].text_only is True
    if rig["manifest"]:
        assert session._receipt.authority_scope == "manifest"
        assert session._work_candidates is not None
    with pytest.raises(Exception, match="no prompt node"):
        rig["wrapper"]("ordinary call must still fail")
    assert len(rig["terminal"].calls) == 1


@pytest.mark.parametrize("case", ["foreign_run", "foreign_owner", "foreign_universe",
                                  "foreign_author", "missing_run", "unprepared", "cancel_requested",
                                  "cancelled", "revoked", "unavailable", "budget", "closed"])
def test_invalid_authority_never_calls_provider_or_sends(rig, case):
    session, base = rig["session"], rig["tmp_path"]
    kwargs = {}
    if case == "foreign_run":
        kwargs["bound_run"] = "another-run"
    elif case == "foreign_owner":
        session._principal_id = "acct_other"
    elif case == "foreign_universe":
        kwargs["bound_provider"] = replace(rig["wrapper"], universe_context=UniverseContext(
            universe_dir=base / "universe_other"))
    elif case == "foreign_author":
        session._branch_snapshot["author"] = "acct_other"
    elif case in {"missing_run", "unprepared"}:
        session._run_id = "missing" if case == "missing_run" else ""
        kwargs["bound_run"] = session._run_id
    elif case == "cancel_requested":
        from tinyassets.runs import request_cancel

        assert request_cancel(base, rig["run_id"])
    elif case == "cancelled":
        update_run_status(base, rig["run_id"], status="cancelled")
    elif case == "closed":
        session.close()
    elif case != "budget":
        with sqlite3.connect(db_path(base)) as conn:
            if case == "revoked":
                conn.execute("UPDATE provider_work_bindings SET state='revoked'")
            elif case == "unavailable":
                conn.execute("DELETE FROM provider_assignments")
    result = rig["fire"](**kwargs)
    assert result["error_kind"] == "auto_review_unavailable", result
    assert not rig["terminal"].calls and not rig["sends"]


@pytest.mark.parametrize("answers,calls,kind", [
    (['{"verdict":"needs_approval","reason":"outside scope"}'], 1,
     "auto_review_needs_approval"),
    (["invalid", "invalid"], 2, "auto_review_unavailable"),
    (["invalid", '{"verdict":"proceed","reason":"within scope"}'], 2, None),
])
def test_review_verdict_retry_and_budget(rig, answers, calls, kind):
    rig["terminal"].answers = answers
    result = rig["fire"]()
    assert len(rig["terminal"].calls) == calls
    if kind:
        assert result["error_kind"] == kind, result
        assert not rig["sends"]
    else:
        assert result.get("delivered"), result
        assert len(rig["sends"]) == 1
    # Same run's finite receipt cannot be replenished by starting another review.
    if calls == 2:
        assert rig["fire"]()["error_kind"] == "auto_review_unavailable"
        assert len(rig["terminal"].calls) == 2


def test_rule_denial_does_not_spend_review_or_send(rig):
    agent_rules.set_rule(rig["universe"], "app.write", agent_rules.ASK_FIRST)
    assert rig["fire"]()["error_kind"] == "rule_ask_first"
    assert not rig["terminal"].calls and not rig["sends"]


def test_missing_consent_still_holds_after_positive_review(rig):
    from tinyassets.storage.effector_consents import revoke_consent

    revoke_consent(rig["universe"], sink=aec.EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
                   destination="example.com")
    assert rig["fire"]()["error_kind"] == "missing_consent"
    assert len(rig["terminal"].calls) == 1 and not rig["sends"]


@pytest.mark.parametrize("change", ["cancel", "revoke", "move_home"])
def test_authority_change_during_review_never_sends(rig, change):
    def after_call():
        if change == "cancel":
            update_run_status(rig["tmp_path"], rig["run_id"], status="cancelled")
        elif change == "move_home":
            set_founder_home(rig["tmp_path"], founder_sub="acct_alice",
                             universe_id="universe_other", platform_generated=True)
        else:
            with sqlite3.connect(db_path(rig["tmp_path"])) as conn:
                conn.execute("UPDATE provider_work_bindings SET state='revoked'")

    rig["terminal"].after_call = after_call
    result = rig["fire"]()
    assert result["error_kind"] == "auto_review_unavailable", result
    assert len(rig["terminal"].calls) == 1 and not rig["sends"]


def test_prompt_workflow_can_still_review_after_its_node_call(rig):
    from tinyassets.foreground_run_provider import _content_digest

    session = rig["session"]
    # Before admission: represent the immutable prompt-node variant of this run.
    session._branch_snapshot["node_defs"][0]["prompt_template"] = "Generate output"
    session._branch_digest = _content_digest(session._branch_snapshot)
    rig["terminal"].answers.insert(0, "node output")
    assert rig["wrapper"]("Generate output") == "node output"
    assert rig["fire"]().get("delivered")
    assert session._receipt.max_invocations == 3
    assert len(rig["terminal"].calls) == 2 and len(rig["sends"]) == 1


def test_review_diagnostics_keep_sanitized_admission_cause(rig):
    result = rig["fire"](bound_run="foreign")
    assert "effect review does not belong" in result["review"]["reason"]
    assert "nothing was sent" in result["hint"]


def test_review_purpose_rejects_text_substitution_and_expired_context(rig):
    from tinyassets import agent_review

    purpose = agent_review._ReviewPurpose(
        rig["wrapper"], rig["universe"], rig["run_id"], "a" * 64, "expected text")
    token = agent_review._PURPOSE.set(purpose)
    try:
        with pytest.raises(PermissionError, match="text-only purpose"):
            rig["wrapper"]("substituted", agent_review.SAFETY_REQUIREMENTS)
        purpose.attempts = agent_review.REVIEW_MAX_ATTEMPTS
        with pytest.raises(Exception, match="attempt allowance exhausted"):
            rig["wrapper"]("expected text", agent_review.SAFETY_REQUIREMENTS)
        purpose.active = False
        with pytest.raises(PermissionError, match="does not belong"):
            rig["wrapper"]("expected text", agent_review.SAFETY_REQUIREMENTS)
    finally:
        agent_review._PURPOSE.reset(token)
    assert not rig["terminal"].calls and not rig["sends"]


def test_review_attempts_share_parent_dispatch_budget_and_keep_two_attempt_ceiling(rig):
    from tinyassets.request_budget import TurnRequestBudget

    budget = TurnRequestBudget("acct_alice", "universe_alice", max_requests=1)
    rig["session"]._request_budget = budget
    rig["terminal"].answers = ["invalid", '{"verdict":"proceed","reason":"ok"}']
    result = rig["fire"]()
    assert result["error_kind"] == "auto_review_unavailable"
    assert len(rig["terminal"].calls) == 1 and not rig["sends"]
    receipt = budget.receipt()
    assert receipt["dispatched"] == 1
    assert receipt["sources"][0]["purpose"] == "review"
    assert receipt["sources"][0]["succeeded"] == 1
    assert rig["terminal"].calls[0][2].text_only is True


def test_review_and_prompt_call_share_one_parent_without_renewal(rig):
    from tinyassets.foreground_run_provider import _content_digest

    session = rig["session"]
    session._branch_snapshot["node_defs"][0]["prompt_template"] = "Generate output"
    session._branch_digest = _content_digest(session._branch_snapshot)
    rig["terminal"].answers = ["node answer", '{"verdict":"proceed","reason":"ok"}']
    assert rig["wrapper"]("Generate output") == "node answer"
    assert rig["fire"]().get("delivered")
    receipt = session._request_budget.receipt()
    assert receipt["dispatched"] == 2
    assert [(row["purpose"], row["dispatched"]) for row in receipt["sources"]] == [
        ("helper", 1), ("review", 1),
    ]


@pytest.mark.parametrize("rig", ["manifest"], indirect=True)
def test_spent_free_candidates_do_not_consume_review_attempts_before_paid_fallback(
    rig, monkeypatch,
):
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.request_budget import TurnRequestBudget

    session = rig["session"]
    budget = TurnRequestBudget("acct_alice", "universe_alice", free_pool_limit=2)
    session._request_budget = budget
    spent = [ModelRef("synthetic-free-a", "m"), ModelRef("synthetic-free-b", "m")]
    for ref in spent:
        ordinal = budget.reserve(owner="acct_alice", universe="universe_alice",
                                 source_ref=ref.connection_id, model=ref.model_id, free=True)
        budget.dispatched(ordinal)
        budget.settle(ordinal, "succeeded")
    capture = session._capture_choices

    def capture_with_spent_prefix():
        capture()
        original = session._work_candidates.next_candidate

        def candidates(policy=None, *args, local_exclusions=(), **kwargs):
            return next((ref for ref in spent if ref not in local_exclusions), None) or original(
                policy, *args, local_exclusions=local_exclusions, **kwargs,
            )

        monkeypatch.setattr(session._work_candidates, "next_candidate", candidates)

    monkeypatch.setattr(session, "_capture_choices", capture_with_spent_prefix)
    monkeypatch.setattr("tinyassets.request_budget.candidate_is_metered_free",
                        lambda ctx, catalog, **kw: ctx.model_selection in spent)
    assert rig["fire"]().get("delivered")
    assert len(rig["terminal"].calls) == len(rig["sends"]) == 1
    assert budget.receipt()["dispatched"] == 3
