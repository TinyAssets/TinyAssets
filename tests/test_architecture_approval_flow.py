"""Request -> protected owner session -> real broker IPC -> public proof -> CI gate."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from tests.owner_answer import answer_request
from tests.support.architecture import action
from tests.test_architecture_attestation import configured_signer, guard
from tinyassets.api.pending_requests import answer_request as bearer_answer
from tinyassets.api.pending_requests import request_from_user
from tinyassets.architecture_approval import public_attestation, validate_action
from tinyassets.auth.middleware import _auth_challenge_path, identity_context
from tinyassets.auth.provider import Identity
from tinyassets.daemon_server import grant_universe_access
from tinyassets.storage.pending_requests import get_request


@pytest.fixture
def setup(tmp_path, monkeypatch, in_process_broker):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    home = tmp_path / "cc"
    home.mkdir()
    grant_universe_access(tmp_path, universe_id="cc", actor_id="alice",
                          permission="admin", granted_by="alice")
    signer, trust = configured_signer(tmp_path)
    # The existing broker fixture runs actual frames/fences/peer checks on Linux.
    server = in_process_broker.supervisor_for(tmp_path).server
    server._architecture_signer = signer
    with identity_context(Identity(user_id="alice", username="alice")):
        yield home, trust, server


def ask():
    result = request_from_user(universe_id="cc", payload={"action": action(),
                               "kind": "Misleading title", "title": "Just click",
                               "body": "Nothing to read"})
    assert not result.get("error"), result
    return result


def read_public():
    return asyncio.run(public_attestation(SimpleNamespace(path_params={"pr": 4574})))


def test_request_to_guard_with_protected_session_and_real_broker(setup, tmp_path):
    home, trust, _ = setup
    row = ask()
    assert row["kind"] == "Architecture"
    assert "I have been briefed and agree" in row["body"]
    assert "README Direction" in row["body"]
    for item in action()["release_critical_files"] + list(action()["briefing"].values()):
        assert item in row["body"]
    assert row["action"] == validate_action(action())
    payload = {"request_id": row["request_id"], "values": {}}
    assert bearer_answer(universe_id="cc", payload=payload)["error"] == (
        "interactive_approval_required")
    assert answer_request(universe_id="cc", payload=payload, cookie="")["error"] == (
        "interactive_approval_required")
    assert read_public().status_code == 404
    result = answer_request(universe_id="cc", payload=payload)
    assert result["status"] == "answered", result
    assert get_request(home, row["request_id"])["status"] == "answered"
    response = read_public()
    assert response.status_code == 200
    envelope = json.loads(response.body)
    assert envelope == result["attestation"]
    assert "private_key" not in response.body.decode() and "briefing" not in response.body.decode()
    assert guard(tmp_path, action(), trust, envelope) == 0
    assert guard(tmp_path, action(diff="d" * 64), trust, envelope) == 2


@pytest.mark.parametrize("decision", [{"decision": "declined"}, {"dismiss": True}])
def test_declining_or_dismissing_issues_nothing(setup, decision):
    row = ask()
    payload = {"request_id": row["request_id"], **decision}
    assert bearer_answer(universe_id="cc", payload=payload)["error"] == (
        "interactive_approval_required")
    assert not answer_request(universe_id="cc", payload=payload).get("error")
    assert read_public().status_code == 404


def test_other_protected_owner_cannot_sign_for_founder(setup, tmp_path):
    grant_universe_access(tmp_path, universe_id="cc", actor_id="bob",
                          permission="admin", granted_by="alice")
    with identity_context(Identity(user_id="bob", username="bob")):
        row = ask()
        result = answer_request(universe_id="cc", payload={"request_id": row["request_id"],
                                                           "values": {}})
    assert result["error"] == "architecture_approval_unavailable"
    assert result["request_pending"] is True
    assert read_public().status_code == 404


def test_mutation_after_display_refuses(setup):
    home, _, _ = setup
    row = ask()
    from tinyassets.storage.pending_requests import _db

    with _db(home) as conn:
        conn.execute("UPDATE pending_requests SET action_json=? WHERE request_id=?",
                     (json.dumps(action(diff="d" * 64)), row["request_id"]))
    result = answer_request(universe_id="cc", payload={"request_id": row["request_id"],
                                                       "values": {}})
    assert result["error"] == "request_invalid"
    assert read_public().status_code == 404


def test_public_auth_carveout_is_read_surface_only():
    assert not _auth_challenge_path("/app/attestations/architecture/4574")
    for path in ("/app/attestations/architecture/issue", "/app/attestations/architecture/4574/edit",
                 "/app/approvals/answer"):
        assert _auth_challenge_path(path)


def test_broker_rejects_non_owner_channel_and_stale_fence(setup, tmp_path, in_process_broker):
    from tinyassets.broker.client import BrokerClient, BrokerRefused
    from tinyassets.broker.server import BOX

    live = in_process_broker.supervisor_for(tmp_path)
    client = BrokerClient(live.socket_path, principal="alice", command_center="cc",
                          fence=lambda: (0, "forged"), timeout=2)
    with pytest.raises(BrokerRefused):
        client.architecture({"operation": "read", "pr": 4574})
    live.server._roles = {uid: BOX for uid in live.server._roles}
    with pytest.raises(RuntimeError, match="unavailable"):
        client.architecture({"operation": "read", "pr": 4574})
