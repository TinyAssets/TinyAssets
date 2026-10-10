from __future__ import annotations

import hashlib
import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest

from tinyassets.effectors.outbound_boundary import (
    AmbiguousEffectOutcome,
    BatchEffectItem,
    confirm_held_effect,
    execute_capped_action,
    execute_effect_batch,
    execute_replay_safe_effect,
    hold_receipt_finalization_failure,
)
from tinyassets.storage.external_write_receipts import (
    STATUS_FAILED,
    STATUS_HELD,
    STATUS_PENDING,
    STATUS_SUCCEEDED,
    lookup_receipt,
    try_reserve_receipt,
)
from tinyassets.storage.outbound_connections import (
    ActionCap,
    ConnectionLedger,
)


@pytest.fixture(autouse=True)
def fixture_stream_envelope(monkeypatch):
    from tinyassets.storage import outbound_connections as outbound

    original = outbound._TestFixtureNetworkDriver.__call__

    def response(self, **kwargs):
        result = original(self, **kwargs)
        return {"status": 201, "reason": "Created", "headers": {}, "body": json.dumps(result)}

    monkeypatch.setattr(outbound._TestFixtureNetworkDriver, "__call__", response)


@dataclass
class _RecordedDispatch:
    path: str

    def __eq__(self, other):
        path = Path(self.path)
        records = (
            [
                json.loads(line)["request"]
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            if path.exists()
            else []
        )
        return records == other


@dataclass(eq=False)
class _FailOnceDispatch(_RecordedDispatch):
    pass


@dataclass(eq=False)
class _AmbiguousDispatch(_RecordedDispatch):
    pass


def _ledger_with_cap(
    tmp_path,
    dispatch=None,
    *,
    verify_authenticated_principal=None,
) -> tuple[ConnectionLedger, object]:
    runtime_id = hashlib.sha256(b"grant-1").hexdigest()
    dispatch_class = type(dispatch) if dispatch is not None else _RecordedDispatch
    calls = dispatch_class(
        str(tmp_path / ".broker" / ".outbound-proxy" / runtime_id / "network.jsonl")
    )
    mode = (
        "fail-once"
        if isinstance(calls, _FailOnceDispatch)
        else "ambiguous"
        if isinstance(calls, _AmbiguousDispatch)
        else "issue"
    )
    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        allow_test_fixtures=True,
        verify_authenticated_principal=(
            verify_authenticated_principal or (lambda: "user-1")
        ),
    )
    ledger.create_connection(
        connection_id="conn-1",
        owner_user_id="user-1",
        connection_class="issue-writer",
        scopes=("issues:write",),
        provider=f"test-fixture.{mode}",
        destination="github.com/acme/widgets",
        credential_ref="test-fixture://nonsecret",
    )
    ledger.grant_connection(
        grant_id="grant-1",
        connection_id="conn-1",
        owner_user_id="user-1",
        universe_id="universe-1",
        unprompted_action_cap=ActionCap(
            name="issues-per-action",
            maximum=2,
            unit="issues",
        ),
    )
    proxy = ledger.resolve_exact_scoped_proxy(
        universe_id="universe-1",
        grant_id="grant-1", connection_id="conn-1",
    )
    return ledger, (proxy, calls)


def _authority(ledger):
    from tinyassets.broker.connection_authority import BrokerConnectionAuthority

    return BrokerConnectionAuthority(
        ledger._data_root, "universe-1", ledger.require_authenticated_principal_id)


def test_unprompted_cap_is_machine_readable_and_a_separate_authority_axis(tmp_path):
    ledger, _ = _ledger_with_cap(tmp_path)

    decision = ledger.evaluate_unprompted_action_cap(
        grant_id="grant-1",
        action_value=3,
        action_unit="issues",
    )

    assert decision.as_dict() == {
        "status": "held",
        "cap": {
            "name": "issues-per-action",
            "maximum": 2,
            "unit": "issues",
        },
        "action_value": 3,
        "action_unit": "issues",
        "authorization_axis": "unprompted_action",
    }
    parameters = inspect.signature(
        ledger.evaluate_unprompted_action_cap
    ).parameters
    assert "tool_authorized" not in parameters
    assert "spend_cap" not in parameters


def test_above_cap_holds_without_execution_or_consumption_until_confirmation(
    tmp_path,
):
    ledger, (proxy, calls) = _ledger_with_cap(tmp_path)
    universe = tmp_path / "universe-1"

    held = execute_capped_action(
        universe_dir=universe,
        ledger=_authority(ledger),
        grant_id="grant-1",
        proxy=proxy,
        tool_authorized=True,
        action_value=3,
        action_unit="issues",
        effect_key="effect-1",
        sink="github_issue",
        run_id="run-1",
        verb="issues:write",
        request={"title": "Needs confirmation"},
    )

    assert held["status"] == "held"
    assert held["cap"]["name"] == "issues-per-action"
    assert held["consumption"] == {"funds": 0, "quota": 0}
    assert held["remediation"] == {
        "action": "confirm_held_effect",
        "effect_key": "effect-1",
        "message": "Confirm this effect to exceed cap 'issues-per-action'.",
    }
    assert calls == []
    receipt = lookup_receipt(
        universe,
        idempotency_hint="effect-1",
        sink="github_issue",
    )
    assert receipt is not None
    assert receipt["status"] == STATUS_HELD

    confirm_held_effect(
        universe_dir=universe,
        ledger=_authority(ledger),
        grant_id="grant-1",
        effect_key="effect-1",
        sink="github_issue",
    )
    executed = execute_capped_action(
        universe_dir=universe,
        ledger=_authority(ledger),
        grant_id="grant-1",
        proxy=proxy,
        tool_authorized=True,
        action_value=3,
        action_unit="issues",
        effect_key="effect-1",
        sink="github_issue",
        run_id="run-2",
        verb="issues:write",
        request={"title": "Needs confirmation"},
    )
    assert executed["status"] == "executed"
    assert calls == [{"title": "Needs confirmation"}]

    replay = execute_capped_action(
        universe_dir=universe,
        ledger=_authority(ledger),
        grant_id="grant-1",
        proxy=proxy,
        tool_authorized=True,
        action_value=3,
        action_unit="issues",
        effect_key="effect-1",
        sink="github_issue",
        run_id="run-3",
        verb="issues:write",
        request={"title": "Needs confirmation"},
    )
    assert replay["status"] == "executed"
    assert replay["replay"] is True
    assert calls == [{"title": "Needs confirmation"}]


def test_confirmed_hold_refuses_a_later_different_decision(tmp_path):
    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        allow_test_fixtures=True,
        verify_authenticated_principal=lambda: "user-1",
    )
    ledger.create_connection(
        connection_id="conn-1",
        owner_user_id="user-1",
        connection_class="repository-writer",
        scopes=("create_issue", "delete_repo"),
        provider="test-fixture.issue",
        destination="github.com/acme/production",
        credential_ref="test-fixture://nonsecret",
    )
    ledger.grant_connection(
        grant_id="grant-1",
        connection_id="conn-1",
        owner_user_id="user-1",
        universe_id="universe-1",
        unprompted_action_cap=ActionCap(
            name="repository-actions",
            maximum=2,
            unit="actions",
        ),
    )
    proxy = ledger.resolve_exact_scoped_proxy(
        universe_id="universe-1",
        grant_id="grant-1", connection_id="conn-1",
    )
    universe = tmp_path / "universe-1"
    held = execute_capped_action(
        universe_dir=universe,
        ledger=_authority(ledger),
        grant_id="grant-1",
        proxy=proxy,
        tool_authorized=True,
        action_value=5,
        action_unit="actions",
        effect_key="effect-reviewed",
        sink="github_repository",
        run_id="run-hold",
        verb="create_issue",
        request={"title": "benign issue the owner reviewed"},
    )
    confirmation = confirm_held_effect(
        universe_dir=universe,
        ledger=_authority(ledger),
        grant_id="grant-1",
        effect_key="effect-reviewed",
        sink="github_repository",
    )

    assert confirmation["held_decision"] == {
        "verb": "create_issue",
        "request": {"title": "benign issue the owner reviewed"},
        "action_value": 5,
        "action_unit": "actions",
        "cap": held["cap"],
    }
    with pytest.raises(PermissionError, match="held decision"):
        execute_capped_action(
            universe_dir=universe,
            ledger=_authority(ledger),
            grant_id="grant-1",
            proxy=proxy,
            tool_authorized=True,
            action_value=10_000_000,
            action_unit="actions",
            effect_key="effect-reviewed",
            sink="github_repository",
            run_id="run-replay",
            verb="delete_repo",
            request={"repo": "acme/production", "confirm": "yes"},
        )


def test_same_action_at_cap_executes_without_hold(tmp_path):
    ledger, (proxy, calls) = _ledger_with_cap(tmp_path)

    result = execute_capped_action(
        universe_dir=tmp_path / "universe-1",
        ledger=_authority(ledger),
        grant_id="grant-1",
        proxy=proxy,
        tool_authorized=True,
        action_value=2,
        action_unit="issues",
        effect_key="effect-at-cap",
        sink="github_issue",
        run_id="run-1",
        verb="issues:write",
        request={"title": "At cap"},
    )

    assert result["status"] == "executed"
    assert calls == [{"title": "At cap"}]

    replay = execute_capped_action(
        universe_dir=tmp_path / "universe-1",
        ledger=_authority(ledger),
        grant_id="grant-1",
        proxy=proxy,
        tool_authorized=True,
        action_value=2,
        action_unit="issues",
        effect_key="effect-at-cap",
        sink="github_issue",
        run_id="run-2",
        verb="issues:write",
        request={"title": "At cap"},
    )
    assert replay["replay"] is True
    assert calls == [{"title": "At cap"}]


def test_confirmed_cap_execution_is_acquired_once_under_concurrent_replay(
    tmp_path,
):
    ledger, (proxy, calls) = _ledger_with_cap(tmp_path)
    universe = tmp_path / "universe-1"
    execute_capped_action(
        universe_dir=universe,
        ledger=_authority(ledger),
        grant_id="grant-1",
        proxy=proxy,
        tool_authorized=True,
        action_value=3,
        action_unit="issues",
        effect_key="effect-concurrent",
        sink="github_issue",
        run_id="run-hold",
        verb="issues:write",
        request={"title": "Concurrent"},
    )
    confirm_held_effect(
        universe_dir=universe,
        ledger=_authority(ledger),
        grant_id="grant-1",
        effect_key="effect-concurrent",
        sink="github_issue",
    )

    def execute(run_id):
        return execute_capped_action(
            universe_dir=universe,
            ledger=_authority(ledger),
            grant_id="grant-1",
            proxy=proxy,
            tool_authorized=True,
            action_value=3,
            action_unit="issues",
            effect_key="effect-concurrent",
            sink="github_issue",
            run_id=run_id,
            verb="issues:write",
            request={"title": "Concurrent"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute, f"run-{index}") for index in range(2)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except RuntimeError as exc:
                outcomes.append({"status": "in_flight", "error": str(exc)})

    assert calls == [{"title": "Concurrent"}]
    assert sum(outcome["status"] == "executed" for outcome in outcomes) >= 1


def test_confirmed_cap_failure_can_retry_without_losing_confirmation(tmp_path):
    dispatch = _FailOnceDispatch(str(tmp_path / "calls.jsonl"))
    ledger, (proxy, calls) = _ledger_with_cap(tmp_path, dispatch)
    universe = tmp_path / "universe-1"
    execute_capped_action(
        universe_dir=universe,
        ledger=_authority(ledger),
        grant_id="grant-1",
        proxy=proxy,
        tool_authorized=True,
        action_value=3,
        action_unit="issues",
        effect_key="effect-retry",
        sink="github_issue",
        run_id="run-hold",
        verb="issues:write",
        request={"title": "Retry"},
    )
    confirm_held_effect(
        universe_dir=universe,
        ledger=_authority(ledger),
        grant_id="grant-1",
        effect_key="effect-retry",
        sink="github_issue",
    )

    failed = execute_capped_action(
        universe_dir=universe,
        ledger=_authority(ledger),
        grant_id="grant-1",
        proxy=proxy,
        tool_authorized=True,
        action_value=3,
        action_unit="issues",
        effect_key="effect-retry",
        sink="github_issue",
        run_id="run-failed",
        verb="issues:write",
        request={"title": "Retry"},
    )
    retried = execute_capped_action(
        universe_dir=universe,
        ledger=_authority(ledger),
        grant_id="grant-1",
        proxy=proxy,
        tool_authorized=True,
        action_value=3,
        action_unit="issues",
        effect_key="effect-retry",
        sink="github_issue",
        run_id="run-retry",
        verb="issues:write",
        request={"title": "Retry"},
    )

    assert failed["status"] == STATUS_FAILED
    assert failed["confirmation"]["authorized_by"] == "user-1"
    assert retried["status"] == "executed"
    assert calls == [{"title": "Retry"}, {"title": "Retry"}]


def test_confirmed_cap_ambiguous_hold_preserves_grant_and_confirmation(tmp_path):
    dispatch = _AmbiguousDispatch(str(tmp_path / "calls.jsonl"))
    ledger, (proxy, _calls) = _ledger_with_cap(tmp_path, dispatch)
    universe = tmp_path / "universe-1"
    execute_capped_action(
        universe_dir=universe,
        ledger=_authority(ledger),
        grant_id="grant-1",
        proxy=proxy,
        tool_authorized=True,
        action_value=3,
        action_unit="issues",
        effect_key="effect-ambiguous-cap",
        sink="github_issue",
        run_id="run-hold",
        verb="issues:write",
        request={"title": "Ambiguous"},
    )
    confirm_held_effect(
        universe_dir=universe,
        ledger=_authority(ledger),
        grant_id="grant-1",
        effect_key="effect-ambiguous-cap",
        sink="github_issue",
    )

    held = execute_capped_action(
        universe_dir=universe,
        ledger=_authority(ledger),
        grant_id="grant-1",
        proxy=proxy,
        tool_authorized=True,
        action_value=3,
        action_unit="issues",
        effect_key="effect-ambiguous-cap",
        sink="github_issue",
        run_id="run-ambiguous",
        verb="issues:write",
        request={"title": "Ambiguous"},
    )

    assert held["status"] == STATUS_HELD
    assert held["grant_id"] == "grant-1"
    assert held["confirmation"]["authorized_by"] == "user-1"


def test_tool_authorization_denial_is_not_overridden_by_cap(tmp_path):
    ledger, (proxy, calls) = _ledger_with_cap(tmp_path)

    with pytest.raises(PermissionError, match="tool authorization"):
        execute_capped_action(
            universe_dir=tmp_path / "universe-1",
            ledger=_authority(ledger),
            grant_id="grant-1",
            proxy=proxy,
            tool_authorized=False,
            action_value=1,
            action_unit="issues",
            effect_key="effect-denied",
            sink="github_issue",
            run_id="run-1",
            verb="issues:write",
            request={"title": "Denied"},
        )
    assert calls == []


def test_cap_cannot_be_evaluated_for_a_different_grant_than_proxy(tmp_path):
    ledger, (proxy, calls) = _ledger_with_cap(tmp_path)
    ledger.create_connection(
        connection_id="conn-uncapped",
        owner_user_id="user-1",
        connection_class="other-writer",
        scopes=("issues:write",),
        provider="github",
        destination="github.com/acme/other",
        credential_ref="vault://github/other",
    )
    ledger.grant_connection(
        grant_id="grant-uncapped",
        connection_id="conn-uncapped",
        owner_user_id="user-1",
        universe_id="universe-1",
    )

    with pytest.raises(PermissionError, match="proxy grant"):
        execute_capped_action(
            universe_dir=tmp_path / "universe-1",
            ledger=_authority(ledger),
            grant_id="grant-uncapped",
            proxy=proxy,
            tool_authorized=True,
            action_value=3,
            action_unit="issues",
            effect_key="effect-mismatch",
            sink="github_issue",
            run_id="run-1",
            verb="issues:write",
            request={"title": "Bypass"},
        )
    assert calls == []


def test_action_caps_reject_non_finite_values(tmp_path):
    with pytest.raises(ValueError, match="finite"):
        ActionCap(name="bad", maximum=float("nan"), unit="issues")

    ledger, _ = _ledger_with_cap(tmp_path)
    with pytest.raises(ValueError, match="finite"):
        ledger.evaluate_unprompted_action_cap(
            grant_id="grant-1",
            action_value=float("nan"),
            action_unit="issues",
        )


def test_action_cap_rejects_mismatched_units(tmp_path):
    ledger, _ = _ledger_with_cap(tmp_path)

    with pytest.raises(ValueError, match="does not match cap unit"):
        ledger.evaluate_unprompted_action_cap(
            grant_id="grant-1",
            action_value=1,
            action_unit="pull_requests",
        )


def test_caller_supplied_grant_owner_cannot_confirm_held_effect(tmp_path):
    principal = {"user_id": "user-1"}
    ledger, (proxy, _calls) = _ledger_with_cap(
        tmp_path,
        verify_authenticated_principal=lambda: principal["user_id"],
    )
    universe = tmp_path / "universe-1"
    execute_capped_action(
        universe_dir=universe,
        ledger=_authority(ledger),
        grant_id="grant-1",
        proxy=proxy,
        tool_authorized=True,
        action_value=3,
        action_unit="issues",
        effect_key="effect-owner",
        sink="github_issue",
        run_id="run-1",
        verb="issues:write",
        request={"title": "Owner only"},
    )

    forged_owner = ledger.get_grant("grant-1").owner_user_id
    with pytest.raises(TypeError, match="authorized_by"):
        confirm_held_effect(
            universe_dir=universe,
            ledger=_authority(ledger),
            grant_id="grant-1",
            effect_key="effect-owner",
            sink="github_issue",
            authorized_by=forged_owner,
        )
    principal["user_id"] = "attacker"
    with pytest.raises(PermissionError, match="confirmation requires a current grant"):
        confirm_held_effect(
            universe_dir=universe,
            ledger=_authority(ledger),
            grant_id="grant-1",
            effect_key="effect-owner",
            sink="github_issue",
        )
    assert _calls == []


def test_effect_intent_is_journaled_before_fire_and_replay_consults_journal(
    tmp_path,
):
    universe = tmp_path / "universe-1"
    calls: list[str] = []

    def invoke():
        calls.append("fired")
        pending = lookup_receipt(
            universe,
            idempotency_hint="effect:v1:abc",
            sink="github_issue",
        )
        assert pending is not None
        assert pending["status"] == STATUS_PENDING
        return {"issue_id": 17}

    first = execute_replay_safe_effect(
        universe_dir=universe,
        effect_key="effect:v1:abc",
        sink="github_issue",
        run_id="run-1",
        invoke=invoke,
    )
    replay = execute_replay_safe_effect(
        universe_dir=universe,
        effect_key="effect:v1:abc",
        sink="github_issue",
        run_id="run-2",
        invoke=invoke,
    )

    assert first["status"] == STATUS_SUCCEEDED
    assert first["replay"] is False
    assert replay["status"] == STATUS_SUCCEEDED
    assert replay["replay"] is True
    assert calls == ["fired"]


def test_ambiguous_outcome_reconciles_with_destination_and_persists_terminal(
    tmp_path,
):
    universe = tmp_path / "universe-1"

    def invoke():
        raise AmbiguousEffectOutcome("connection dropped after send")

    result = execute_replay_safe_effect(
        universe_dir=universe,
        effect_key="effect:v1:ambiguous",
        sink="github_issue",
        run_id="run-1",
        invoke=invoke,
        reconcile=lambda effect_key: {
            "status": STATUS_SUCCEEDED,
            "evidence": {"remote_id": "issue-17", "effect_key": effect_key},
        },
    )

    assert result["status"] == STATUS_SUCCEEDED
    assert result["reconciled"] is True
    receipt = lookup_receipt(
        universe,
        idempotency_hint="effect:v1:ambiguous",
        sink="github_issue",
    )
    assert receipt is not None
    assert receipt["status"] == STATUS_SUCCEEDED
    assert receipt["evidence"]["terminal"] is True
    assert receipt["evidence"]["remote_id"] == "issue-17"


def test_pending_replay_without_reconciliation_interface_holds_for_remediation(
    tmp_path,
):
    universe = tmp_path / "universe-1"
    try_reserve_receipt(
        universe,
        idempotency_hint="effect:v1:pending",
        sink="legacy_destination",
        run_id="run-crashed",
        now=0.0,
    )

    result = execute_replay_safe_effect(
        universe_dir=universe,
        effect_key="effect:v1:pending",
        sink="legacy_destination",
        run_id="run-replay",
        invoke=lambda: pytest.fail("pending replay must not fire"),
    )

    assert result["status"] == STATUS_HELD
    assert result["reason"] == "reconciliation_unavailable"
    assert result["remediation"]["action"] == "inspect_destination_then_resolve"
    receipt = lookup_receipt(
        universe,
        idempotency_hint="effect:v1:pending",
        sink="legacy_destination",
    )
    assert receipt is not None
    assert receipt["status"] == STATUS_HELD
    assert receipt["evidence"]["terminal"] is True


def test_receipt_finalize_failure_is_persisted_as_actionable_terminal_hold(
    tmp_path,
):
    universe = tmp_path / "universe-1"
    try_reserve_receipt(
        universe,
        idempotency_hint="effect:v1:finalize-race",
        sink="github_issue",
        run_id="run-1",
    )

    hold = hold_receipt_finalization_failure(
        universe_dir=universe,
        effect_key="effect:v1:finalize-race",
        sink="github_issue",
        run_id="run-1",
        destination_evidence={"remote_id": "issue-17"},
    )

    assert hold["status"] == STATUS_HELD
    assert hold["reason"] == "receipt_finalize_failed"
    receipt = lookup_receipt(
        universe,
        idempotency_hint="effect:v1:finalize-race",
        sink="github_issue",
    )
    assert receipt is not None
    assert receipt["status"] == STATUS_HELD
    assert receipt["evidence"]["remote_id"] == "issue-17"
    assert receipt["evidence"]["remediation"]["action"] == (
        "inspect_destination_then_resolve"
    )


def test_known_effect_failure_persists_terminal_result(tmp_path):
    universe = tmp_path / "universe-1"
    calls = 0

    def fail():
        nonlocal calls
        calls += 1
        raise ValueError("provider rejected request")

    first = execute_replay_safe_effect(
        universe_dir=universe,
        effect_key="effect:v1:failed",
        sink="github_issue",
        run_id="run-1",
        invoke=fail,
    )
    assert first["status"] == STATUS_FAILED
    assert first["terminal"] is True
    assert calls == 1
    receipt = lookup_receipt(
        universe,
        idempotency_hint="effect:v1:failed",
        sink="github_issue",
    )
    assert receipt is not None
    assert receipt["status"] == STATUS_FAILED
    assert receipt["evidence"]["terminal"] is True


def test_batch_admission_failure_holds_every_item_before_any_effect_fires():
    calls: list[str] = []
    items = [
        BatchEffectItem(
            item_id="item-a",
            admit=lambda: (True, ""),
            execute=lambda: calls.append("item-a") or {"status": "succeeded"},
        ),
        BatchEffectItem(
            item_id="item-b",
            admit=lambda: (False, "grant revoked"),
            execute=lambda: calls.append("item-b") or {"status": "succeeded"},
        ),
    ]

    result = execute_effect_batch(items)

    assert result["status"] == STATUS_HELD
    assert calls == []
    assert result["items"] == [
        {
            "item_id": "item-a",
            "status": "not_fired",
            "reason": "batch admission failed",
        },
        {
            "item_id": "item-b",
            "status": STATUS_HELD,
            "reason": "grant revoked",
        },
    ]


def test_batch_effect_failure_stops_further_fire_without_claiming_rollback():
    calls: list[str] = []

    def execute(item_id, result):
        return lambda: calls.append(item_id) or result

    result = execute_effect_batch(
        [
            BatchEffectItem(
                item_id="item-a",
                admit=lambda: (True, ""),
                execute=execute(
                    "item-a",
                    {"status": STATUS_SUCCEEDED, "remote_id": "remote-a"},
                ),
            ),
            BatchEffectItem(
                item_id="item-b",
                admit=lambda: (True, ""),
                execute=execute(
                    "item-b",
                    {
                        "status": STATUS_HELD,
                        "reason": "reconciliation_inconclusive",
                    },
                ),
            ),
            BatchEffectItem(
                item_id="item-c",
                admit=lambda: (True, ""),
                execute=execute("item-c", {"status": STATUS_SUCCEEDED}),
            ),
        ]
    )

    assert result["status"] == STATUS_HELD
    assert result["rollback_claimed"] is False
    assert calls == ["item-a", "item-b"]
    assert result["items"] == [
        {
            "item_id": "item-a",
            "status": STATUS_SUCCEEDED,
            "reason": "terminal effect not reversed",
            "disposition": "terminal_not_reversed",
            "remote_id": "remote-a",
        },
        {
            "item_id": "item-b",
            "status": STATUS_HELD,
            "reason": "reconciliation_inconclusive",
        },
        {
            "item_id": "item-c",
            "status": "not_fired",
            "reason": "nothing further fired after item-b",
        },
    ]
