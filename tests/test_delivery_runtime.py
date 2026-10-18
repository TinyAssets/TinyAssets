"""Accepted structured delivery executes its pinned graph as the receiver.

Real graph, SQLite, executor and OS attempt lock. Only the external provider is
a deterministic test double. This is internal execution proof, not public intake.
"""

# ruff: noqa: F811 -- pytest resolves the imported fixture by parameter name

import contextvars
import json

import pytest

from tests.test_delivery_attempts import reserved  # noqa: F401
from tests.test_delivery_reservations import delivery_env  # noqa: F401
from tests.test_receiver_links import env as management_env  # noqa: F401
from tinyassets import delivery_runtime as runtime
from tinyassets import runs
from tinyassets.auth.middleware import current_identity_or_none
from tinyassets.daemon_server import revoke_universe_access
from tinyassets.storage import deliveries
from tinyassets.storage.delivery_lock import try_attempt_lock


def _attempt(base, receipt):
    with deliveries.transaction(base) as conn:
        return dict(conn.execute(
            "SELECT * FROM graph_delivery_attempts WHERE delivery_id=?",
            (receipt["delivery_id"],),
        ).fetchone())


def test_deletion_only_pending_delivery_never_dispatches_and_drain_is_empty(
        reserved, provider_probe, monkeypatch):
    from tinyassets import owner_activity

    base, receipt, run_id = reserved
    gate = owner_activity.Activity()
    gate.admit_deletion_only()
    monkeypatch.setattr(owner_activity, "activity", gate)
    monkeypatch.setattr(runs, "_get_executor",
                        lambda **kwargs: pytest.fail("deletion startup submitted background work"))
    with pytest.raises(RuntimeError, match="draining"):
        runtime.reconcile_deliveries(base)
    assert _attempt(base, receipt)["state"] == "pending"
    assert not provider_probe
    assert gate.quiesce(0)
    assert gate.status()["active"] == 0


@pytest.fixture
def provider_probe(monkeypatch):
    calls = []
    sender_only = contextvars.ContextVar("sender_private_context", default=None)
    sender_only.set("must not cross to receiver")

    def bind(provider_call, universe_id, *, principal_id):
        assert current_identity_or_none().user_id == "receiver"
        assert sender_only.get() is None
        assert principal_id == "receiver"
        assert universe_id == "u-receiver"

        def provider(prompt, system="", **kwargs):
            # The provider owns explicit receiver binding: prompt timeout workers
            # intentionally have no ambient request ContextVars to borrow.
            assert principal_id == "receiver"
            assert universe_id == "u-receiver"
            assert sender_only.get() is None
            calls.append(prompt)
            return json.dumps({"result": "processed exactly 🧪", "extra": "receiver-private"})

        return provider

    monkeypatch.setattr("tinyassets.api.runs._bind_run_provider_call", bind)
    return calls


def test_accepted_occurrence_executes_once_in_fresh_receiver_context(reserved, provider_probe):
    base, receipt, run_id = reserved
    sender = current_identity_or_none()
    assert sender.user_id == "sender"
    assert runtime.reconcile_deliveries(base) == 1
    runs.wait_for(run_id, timeout=10)
    assert _attempt(base, receipt)["state"] == "completed"
    assert len(provider_probe) == 1
    from tinyassets.run_admission_envelope import resolve_admitted_execution

    admitted = resolve_admitted_execution(base, runs.get_run(base, run_id))
    assert admitted.recursion_limit == runs.DEFAULT_RECURSION_LIMIT
    assert admitted.concurrency_budget_override is None
    assert any(node.prompt_template and provider_probe[0].startswith(
        node.prompt_template.split("{")[0]
    ) for node in admitted.branch.node_defs)
    assert provider_probe[0].startswith("private prompt exact input 🍉")
    assert current_identity_or_none() is sender
    runtime.dispatch_accepted_delivery(base, delivery_id=receipt["delivery_id"])
    runs.wait_for(run_id, timeout=10)
    assert len(provider_probe) == 1
    with deliveries.transaction(base) as conn:
        assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        public = deliveries.read_receipt_in_transaction(
            conn, delivery_id=receipt["delivery_id"], principal_id="sender", universe_id="u-sender",
        )
    assert "run_id" not in public
    assert "receiver-private" not in json.dumps(public)
    assert public["status"] == "completed"


def test_proven_unstarted_reservation_recovers_after_startup(reserved, provider_probe):
    base, receipt, run_id = reserved
    runs.update_run_status(base, run_id, status="interrupted", error="prior process stopped")
    runtime.reconcile_deliveries(base)
    runs.wait_for(run_id, timeout=10)
    assert _attempt(base, receipt)["state"] == "completed"
    assert len(provider_probe) == 1


def test_started_attempt_is_interrupted_without_reexecution(reserved, provider_probe):
    base, receipt, run_id = reserved
    with try_attempt_lock(base, delivery_id=receipt["delivery_id"], attempt=1) as guard:
        with deliveries.transaction(base) as conn:
            assert deliveries.start_attempt_in_transaction(conn, guard)
    runtime.reconcile_deliveries(base)
    runs.wait_for(run_id, timeout=10)
    assert _attempt(base, receipt)["state"] == "interrupted"
    assert provider_probe == []


def test_live_attempt_lock_prevents_recovery_or_second_execution(reserved, provider_probe):
    base, receipt, run_id = reserved
    with try_attempt_lock(base, delivery_id=receipt["delivery_id"], attempt=1):
        runtime.reconcile_deliveries(base)
        runs.wait_for(run_id, timeout=10)
        assert _attempt(base, receipt)["state"] == "pending"
        assert provider_probe == []
    runtime.reconcile_deliveries(base)
    runs.wait_for(run_id, timeout=10)
    assert _attempt(base, receipt)["state"] == "completed"


def test_receiver_authority_revocation_prevents_execution(reserved, provider_probe):
    base, receipt, run_id = reserved
    revoke_universe_access(base, universe_id="u-receiver", actor_id="receiver")
    runtime.reconcile_deliveries(base)
    runs.wait_for(run_id, timeout=10)
    assert _attempt(base, receipt)["state"] == "failed"
    assert provider_probe == []


def test_provider_binding_failure_is_terminal_without_ambient_fallback(reserved, monkeypatch):
    base, receipt, run_id = reserved
    def unavailable(*args):
        assert current_identity_or_none().user_id == "receiver"
        raise PermissionError("private receiver authority diagnostic")
    monkeypatch.setattr(runtime, "_receiver_provider", unavailable)
    runtime.reconcile_deliveries(base)
    runs.wait_for(run_id, timeout=10)
    assert _attempt(base, receipt)["state"] == "failed"
    assert _attempt(base, receipt)["safe_reason"] == "receiver_processing_failed"


@pytest.mark.parametrize("value", [
    {"handle_id": "foreign"}, {"nested": [{"file_id": "foreign"}]},
    {"type": "file_bundle", "files": []},
])
def test_unsupported_file_references_fail_explicitly(value):
    with pytest.raises(ValueError, match="delivery_file_transfer_not_implemented"):
        runtime.reject_file_references(value)


def test_data_fields_are_not_credential_redacted():
    runtime.reject_file_references({"key": "literal", "token": "literal", "nested": [1, False]})
