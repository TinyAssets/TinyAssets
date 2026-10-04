"""Workflow runs refresh the owner's sign-in before they pin authority.

Only the served turn refreshed a deposited subscription document (#4032/#4076),
so a workflow run launched the CLI with whatever was stored, however old
(docs/concerns/2026-09-26-pr4032-refresh-launch-integration.md). A run pins the
assignment in its one receipt, and a refresh renews the accepted source, which
moves the assignment. So the refresh has to run before that pin.

The founder's background agent is a user-owned automation, so both lanes are
driven: the foreground `run_graph` path, and the pump's `run_due_automation`,
which binds the owner and starts the branch on the same run path. (These used to
launch the background lane through the consumer's epoch-2 claim pass, which the
fleet prune removed.) Real stores throughout; only the terminal provider, the
token spend and the issuer metadata are synthetic.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime, timezone

from tests import test_background_budget_finalization_e2e as background
from tests import test_run_provider_session as foreground
from tests import test_workflow_http_agent as http_agent
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_run_provider_session import _module_local_cloud_admission  # noqa: F401
from tests.test_subscription_credential_refresh import (
    ID_TOKEN,
    _endpoint_from_the_credential,
)
from tinyassets.storage.provider_work_authority import db_path as authority_db_path

work_agent = http_agent.work_agent
http_wire = http_agent.http_wire

OWNER = "acct_alice"
UID = "universe_alice"
NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)


def _stale_document() -> str:
    return base64.b64encode(json.dumps({
        "tokens": {"id_token": ID_TOKEN, "access_token": "a-1", "refresh_token": "r-1"},
        "last_refresh": "2020-01-01T00:00:00Z",
    }).encode("utf-8")).decode("ascii")


def _redeposit_stale(base_path) -> None:
    """The owner's accepted codex sign-in, now older than the refresh window."""
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.daemon_server import grant_universe_access, set_founder_home
    from tinyassets.onboarding.serving import ensure_founder_serving

    set_founder_home(base_path, founder_sub=OWNER, universe_id=UID, platform_generated=True)
    grant_universe_access(
        base_path, universe_id=UID, actor_id=OWNER, permission="admin", granted_by=OWNER,
    )
    write_credential_vault(
        base_path / UID, [{
            "credential_type": "llm_subscription", "service": "codex",
            "auth_json_b64": _stale_document(),
        }], owner_user_id=OWNER, universe_id=UID,
    )
    renewed = ensure_founder_serving(
        base_path=base_path, universe_dir=base_path / UID, owner_user_id=OWNER,
        universe_id=UID, service="codex",
    )
    assert renewed["status"] == "serving", renewed


def _rotating_spend(monkeypatch) -> list[str]:
    from tinyassets import subscription_refresh

    _endpoint_from_the_credential(monkeypatch)
    spent: list[str] = []

    def spend(document, **_):
        spent.append(document.refresh_token)
        return subscription_refresh._rebuild(
            document, access_token="a-2", refresh_token="r-2", id_token="",
        )

    monkeypatch.setattr(subscription_refresh, "_spend", spend)
    return spent


def _custody_matches_the_vault(base_path) -> bool:
    from tinyassets.credential_vault import current_llm_subscription_custody
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    with SQLiteProviderWorkAuthorityStore(base_path).connection() as conn:
        return current_llm_subscription_custody(
            conn, universe_dir=base_path / UID, owner_user_id=OWNER,
            universe_id=UID, service="codex",
        ) is not None


def _stored_refresh_token(base_path) -> str:
    from tinyassets.credential_vault import load_credential_vault

    record = next(
        r for r in load_credential_vault(base_path / UID)
        if r.get("credential_type") == "llm_subscription"
    )
    return json.loads(base64.b64decode(record["auth_json_b64"]))["tokens"]["refresh_token"]


def test_a_foreground_run_refreshes_before_its_receipt_and_still_completes(
    tmp_path, monkeypatch, authenticate_request,
):
    seed = foreground._seed_serving_assignment

    def seed_then_age(base_path, **kwargs):
        seed(base_path, **kwargs)
        _redeposit_stale(base_path)

    monkeypatch.setattr(foreground, "_seed_serving_assignment", seed_then_age)
    spent = _rotating_spend(monkeypatch)

    response, provider, _ = foreground._run_branch(
        tmp_path, monkeypatch, authenticate_request, foreground._branch(node_count=2),
    )

    assert response["terminal_status"] == "completed", response["terminal_error"]
    # Once for the run, before its one receipt -- not once per node.
    assert spent == ["r-1"]
    assert len(provider.calls) == 2
    assert _stored_refresh_token(tmp_path) == "r-2"
    assert _custody_matches_the_vault(tmp_path)


def _counting_refresh(monkeypatch) -> list[str]:
    """Count calls to the REAL refresh; a spend count cannot see a no-op call."""
    from tinyassets import subscription_refresh

    real = subscription_refresh.refresh_deposited_subscriptions
    calls: list[str] = []

    def counted(**kwargs):
        calls.append(kwargs["owner_user_id"])
        return real(**kwargs)

    monkeypatch.setattr(subscription_refresh, "refresh_deposited_subscriptions", counted)
    return calls


def test_another_users_public_branch_never_refreshes_the_requesters_sign_in(
    tmp_path, monkeypatch, authenticate_request,
):
    """Refused by admission as not the principal's Branch -- and refused before any
    spend or renewal of the requester's credential."""
    seed = foreground._seed_serving_assignment

    def seed_then_age(base_path, **kwargs):
        seed(base_path, **kwargs)
        _redeposit_stale(base_path)

    monkeypatch.setattr(foreground, "_seed_serving_assignment", seed_then_age)
    spent = _rotating_spend(monkeypatch)
    calls = _counting_refresh(monkeypatch)
    branch = foreground._branch(node_count=1, author="acct_bob")
    branch.visibility = "public"

    response, provider, _ = foreground._run_branch(
        tmp_path, monkeypatch, authenticate_request, branch,
    )

    assert response["terminal_status"] == "failed"
    assert provider.calls == []
    assert calls == [] and spent == []
    assert _stored_refresh_token(tmp_path) == "r-1"


def test_an_async_sub_branch_that_runs_first_refreshes_for_itself(
    tmp_path, monkeypatch, authenticate_request,
):
    """A child session may launch before its parent has made any provider call.

    A flag copied from the parent (which had refreshed nothing yet) launched the
    child on the stale sign-in (Codex round 2 on #4082). The child is minted by
    the real sibling path from a parent session that has not been admitted.
    """
    from tinyassets import foreground_run_provider
    from tinyassets.daemon_server import save_branch_definition
    from tinyassets.foreground_run_provider import (
        _session_from_provider_call,
        prepare_foreground_run_provider,
    )
    from tinyassets.runs import create_run, update_run_status

    seed = foreground._seed_serving_assignment

    def seed_then_age(base_path, **kwargs):
        seed(base_path, **kwargs)
        _redeposit_stale(base_path)

    monkeypatch.setattr(foreground, "_seed_serving_assignment", seed_then_age)
    spent = _rotating_spend(monkeypatch)
    calls = _counting_refresh(monkeypatch)
    child_checks = []

    def prepare_then_run_child_first(provider_call, **kwargs):
        prepared = prepare_foreground_run_provider(provider_call, **kwargs)
        if child_checks:
            return prepared
        child_checks.append(False)
        parent = _session_from_provider_call(prepared)
        assert parent is not None and parent.bound_run_id == kwargs["run_id"]
        assert parent._receipt is None and parent._claim is None
        assert not parent._sign_ins_refreshed
        assert calls == [] and spent == []

        child_branch = foreground._branch(node_count=1)
        save_branch_definition(tmp_path, branch_def=child_branch.to_dict())
        child_run_id = create_run(
            tmp_path, branch_def_id=child_branch.branch_def_id, thread_id="thread-child",
            inputs={}, actor=f"universe:{UID}",
        )
        update_run_status(tmp_path, child_run_id, status="running")
        # Use the original preparation function so this hook applies only to
        # the parent, before its first admission or provider invocation.
        child = _session_from_provider_call(prepare_foreground_run_provider(
            prepared, run_id=child_run_id, branch=child_branch,
            branch_version_id=None, allowed_statuses={"running", "queued"},
        ))
        assert child is not None and child is not parent
        try:
            assert child._receipt is None and child._claim is None
            assert child._request_budget is parent._request_budget
            child._refresh_sign_ins()

            assert calls == [OWNER]
            assert spent == ["r-1"]
            assert _stored_refresh_token(tmp_path) == "r-2"
            assert _custody_matches_the_vault(tmp_path)
            assert not parent._sign_ins_refreshed
            child_checks[0] = True
        finally:
            child.close()
        return prepared

    monkeypatch.setattr(foreground_run_provider, "prepare_foreground_run_provider",
                        prepare_then_run_child_first)
    response, _, _ = foreground._run_branch(
        tmp_path, monkeypatch, authenticate_request, foreground._branch(node_count=1),
    )
    assert child_checks == [True], "the child refresh assertions must complete"
    assert response["terminal_status"] == "completed", response
    assert spent == ["r-1"]
    assert _stored_refresh_token(tmp_path) == "r-2"
    assert _custody_matches_the_vault(tmp_path)


# -- The background lane: a due automation ------------------------------------
#
# The founder's always-on agent is a user-owned automation. The pump calls
# run_due_automation, which binds the owner (owner_run_identity) and starts the
# branch through the same async run path as `run_graph`. These drive that pump
# entry for real; only the router's terminal provider is a fake.

DUE_AT = "2026-08-29T12:10:00+00:00"


def run_automation_once(tmp_path, monkeypatch, *, policy=None, agent=False,
                        provider=None, setup_serving=None):
    """Register one automation over the owner's serving universe and run it once.

    Returns the pump's recorded reason, the provider the router called, and the
    run row. The branch is the old background rig's (one node per ``policy``
    entry); the launcher is now the live one.
    """
    from tests.test_automations import _real_providers
    from tinyassets.api.permissions import owner_run_identity
    from tinyassets.automations import register_automation, run_due_automation
    from tinyassets.runs import get_run

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    background._seed_serving_assignment(tmp_path)
    if setup_serving is not None:
        setup_serving()
    background._seed_branch_version(tmp_path, policy=policy, agent=agent)
    # Registration is the owner's own request; the branch is private to them.
    with owner_run_identity(tmp_path, UID, OWNER):
        automation = register_automation(
            tmp_path, universe_id=UID, owner_principal_id=OWNER, name="Always-on agent",
            branch_def_id="branch_repo_spec_loop", interval_seconds=600, now=NOW,
        )
    fake = provider if provider is not None else background._CountingProvider()
    started: list[str] = []
    with _real_providers(codex=fake):
        reason = run_due_automation(
            tmp_path, automation, DUE_AT, now=NOW, on_run_started=started.append,
        )
    run = get_run(tmp_path, started[0]) if started else None
    return reason, fake, run


def test_a_background_automation_refreshes_before_its_receipt_and_still_succeeds(
    tmp_path, monkeypatch,
):
    spent = _rotating_spend(monkeypatch)

    reason, fake, run = run_automation_once(
        tmp_path, monkeypatch, setup_serving=lambda: _redeposit_stale(tmp_path),
    )

    assert reason.startswith("ok:ran:"), (reason, run and run.get("error"))
    assert spent == ["r-1"]
    assert len(fake.calls) == 1
    assert _stored_refresh_token(tmp_path) == "r-2"
    assert _custody_matches_the_vault(tmp_path)


def test_a_background_agent_node_on_native_codex_refreshes_and_completes(
    tmp_path, monkeypatch, work_agent,
):
    """The founder's own shape: a background workflow agent node on codex."""
    from tinyassets.providers.agent_capacity_boundary import NativeCompletionEvidence
    from tinyassets.providers.base import ProviderResponse

    spent = _rotating_spend(monkeypatch)
    launched: list[str] = []

    async def native(self, prompt, system, config, *, universe_dir=None):
        # The launch ARTEFACT: the sealed copy the CLI actually reads.
        document = json.loads((config.credential_snapshot_dir / "auth.json").read_bytes())
        launched.append(document["tokens"]["refresh_token"])
        self.calls.append(config)
        return ProviderResponse(
            text="background native work completed", provider="codex", model="native-default",
            family="codex", latency_ms=1, input_tokens=3, output_tokens=4, cost_microunits=0,
            native_evidence=NativeCompletionEvidence("codex", True, True, "committed"),
        )

    monkeypatch.setattr(background._CountingProvider, "agent_execution_kind", "native_agent",
                        raising=False)
    monkeypatch.setattr(background._CountingProvider, "complete", native)

    reason, _fake, run = run_automation_once(
        tmp_path, monkeypatch, agent=True,
        policy={"preferred": {"provider": "codex", "model": ""}, "fallback_chain": []},
        setup_serving=lambda: _redeposit_stale(tmp_path),
    )

    assert reason.startswith("ok:ran:"), (reason, run and run.get("error"), work_agent.errors)
    assert spent == ["r-1"]
    # The launch ran on the ROTATED sign-in, not the stale one.
    assert launched == ["r-2"]
    assert _custody_matches_the_vault(tmp_path)


def test_a_multi_node_background_automation_refreshes_once_and_keeps_its_receipt(
    tmp_path, monkeypatch,
):
    """The run's one work receipt is replayed by every node; a renewal at node 2
    voided node 1's (Codex refute-review on #4082: two spends, one call, pending)."""
    from tinyassets import subscription_refresh

    spent = _rotating_spend(monkeypatch)
    # Stale again the moment it is rotated: the worst case, a document entering
    # its window between the two nodes.
    monkeypatch.setattr(subscription_refresh, "document_is_stale", lambda *_a: True)
    calls = _counting_refresh(monkeypatch)

    reason, fake, run = run_automation_once(
        tmp_path, monkeypatch, policy=[None, None],
        setup_serving=lambda: _redeposit_stale(tmp_path),
    )

    assert reason.startswith("ok:ran:"), (reason, run and run.get("error"))
    assert calls == [OWNER]
    assert spent == ["r-1"]
    assert len(fake.calls) == 2
    with sqlite3.connect(authority_db_path(tmp_path)) as conn:
        receipts = conn.execute("SELECT COUNT(*) FROM provider_work_receipts").fetchone()[0]
    assert receipts == 1
