"""Lost push outcomes require persisted run authority before broker custody."""
# ruff: noqa: F811 -- imported fixtures
import socket
import sys

import pytest

from tests.test_broker_discovery_http import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tests.test_broker_workspace_consumers import rig  # noqa: F401
from tinyassets import runs
from tinyassets import workspace_intents as intents

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"), reason="Unix peer credentials")


@pytest.fixture
def pending(rig):
    from tinyassets.storage.effector_consents import grant_consent
    from tinyassets.storage.workspace_authority import workspace_consent_destination

    runs.initialize_runs_db(rig.root)
    with runs._connect(rig.root) as conn:
        conn.execute("INSERT INTO runs (run_id,branch_def_id,thread_id,actor,owner_user_id,"
                     "queue_universe_id,started_at) VALUES ('run','b','t','universe:cc-alice',"
                     "'alice','cc-alice',0)")
    with rig.ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET scopes_json=?", ('["git_write:owner/repo"]',))
    grant_consent(rig.base, sink="workspace", destination=workspace_consent_destination(
        "workspace_push", "owner/repo", connection_id="conn-a", host="models.example.com"),
        granted_by="alice")
    rig.intent = intents.record_push_intent(
        rig.base, run_id="run", node_id="push", connection_id="conn-a", grant_id="grant-a",
        universe_id="cc-alice", host="models.example.com", repo="owner/repo",
        remote_ref="refs/heads/tiny/cc-alice/work", sha="a" * 40)
    return rig


def test_reconcile_uses_persisted_run_and_broker_not_injected_custody(pending):
    requests = []

    def execute(request):
        requests.append(request)
        return {"ok": True, "observed_sha": "a" * 40}

    answer = intents.reconcile_push_intents(
        pending.base, execute=execute, credential_ref_for=lambda cid: "vault://foreign")
    assert answer == [(pending.intent, "done")]
    assert requests[0]["credential_ref"] == "vault://http/synthetic"
    assert not (pending.root / "outbound.db").exists()


@pytest.mark.parametrize("change", ["owner", "center", "missing", "revoked", "host", "scope"])
def test_unadmitted_or_changed_intent_never_contacts_remote(pending, change):
    if change in {"owner", "center", "missing"}:
        with runs._connect(pending.root) as conn:
            conn.execute({
                "owner": "UPDATE runs SET owner_user_id='bob'",
                "center": "UPDATE runs SET queue_universe_id='cc-bob'",
                "missing": "DELETE FROM runs",
            }[change])
    else:
        with pending.ledger._connect() as conn:
            conn.execute({
                "revoked": "UPDATE outbound_connection_grants SET revoked_at='gone'",
                "host": "UPDATE outbound_connections SET git_host='other.example.com'",
                "scope": "UPDATE outbound_connections SET scopes_json='[\"GET\"]'",
            }[change])
    sent = []
    assert intents.reconcile_push_intents(
        pending.base, execute=lambda request: sent.append(request),
        credential_ref_for=lambda cid: "vault://bypass", revalidate=lambda intent: True,
    ) == [(pending.intent, "sent")]
    assert not sent
    assert intents.open_intents(pending.base)[0].attempts == 1
    assert not (pending.root / "outbound.db").exists()


def test_broker_outage_defers_and_unscoped_helper_refuses(pending, monkeypatch):
    from tinyassets.broker import supervisor
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    sent = []
    assert intents.reconcile_push_intents(
        pending.base, execute=lambda request: sent.append(request)) == [(pending.intent, "sent")]
    assert not sent
    with pytest.raises(RuntimeError, match="admitted intent scope"):
        intents._credential_ref(pending.base, "conn-a")
    assert not (pending.root / "outbound.db").exists()
