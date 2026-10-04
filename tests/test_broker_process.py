"""The broker as a real child process, end to end (S6 part 4).

POSIX only. The daemon-side supervisor spawns ``tinyassets.broker.process``,
runs the owner's fence barrier and publishes ``owner.json``; with the switch
on, ``resolve_exact_scoped_proxy`` hands out a proxy whose requests are broker
streams. The http path stays disabled (no deployment flag), so a request is
authorized, admitted, recorded and then refused by the driver: the whole
chain, with no network.
"""

from __future__ import annotations

import os
import socket
import sys

import pytest

from tinyassets.broker.supervisor import ENV_SWITCH, PROCESS, BrokerSupervisor, read_owner
from tinyassets.storage.outbound_connections import (
    ConnectionLedger,
    GrantResolutionError,
    ProxyRequestError,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
    reason="the broker process serves a Unix socket and reads peer credentials",
)


@pytest.fixture
def broker(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_SWITCH, PROCESS)
    root = tmp_path / "data"
    root.mkdir()
    ledger = ConnectionLedger(root / "outbound.db",
                              verify_authenticated_principal=lambda: "alice")
    ledger.create_connection(
        connection_id="conn-a", owner_user_id="alice", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("POST",), provider="http",
        destination="compute:conn-a", credential_ref="vault://http/synthetic",
        allowed_endpoints=[{"host": "models.example.com", "path_template": "/v1/chat",
                            "methods": ["POST"]}],
    )
    ledger.grant_connection(grant_id="grant-a", connection_id="conn-a",
                            owner_user_id="alice", universe_id="cc-alice")
    supervisor = BrokerSupervisor(root, child_env=lambda env: {
        k: v for k, v in env.items() if k != "TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED"})
    supervisor.start()
    yield ledger, supervisor, root
    supervisor.stop()


def test_the_supervisor_publishes_the_owner_pair_with_tight_permissions(broker):
    _, supervisor, root = broker
    owner = read_owner(root)
    assert owner["generation"] == 1 and owner["token"]
    assert oct((root / ".broker" / "owner.json").stat().st_mode & 0o777) == "0o600"
    assert oct(supervisor.socket_path.stat().st_mode & 0o777) == "0o600"


def test_a_request_crosses_the_broker_and_the_driver_refuses_it(broker):
    ledger, _, root = broker
    proxy = ledger.resolve_exact_scoped_proxy(universe_id="cc-alice", grant_id="grant-a",
                                              connection_id="conn-a")
    with pytest.raises(ProxyRequestError):
        proxy.request("POST", {"url": "https://models.example.com/v1/chat", "body": {}})
    # It was the broker: the operation is in the broker's own record, sent
    # (may_have_sent is durable before the driver ran) and settled as failed.
    import sqlite3

    with sqlite3.connect(root / ".broker" / "state" / "ops.db") as conn:
        rows = conn.execute("SELECT namespace, state, sent FROM ops").fetchall()
    assert rows == [("alice|cc-alice", "failed", 1)]


def test_a_revoked_grant_is_refused_by_the_ledger_before_the_broker(broker):
    ledger, _, _ = broker
    ledger.revoke_grant("grant-a")
    with pytest.raises(GrantResolutionError):
        ledger.resolve_exact_scoped_proxy(universe_id="cc-alice", grant_id="grant-a",
                                          connection_id="conn-a")


def test_selected_but_not_running_is_a_loud_refusal(broker):
    ledger, supervisor, root = broker
    supervisor.stop()
    with pytest.raises(ProxyRequestError, match="not running"):
        ledger.resolve_exact_scoped_proxy(universe_id="cc-alice", grant_id="grant-a",
                                          connection_id="conn-a")


def test_a_crashed_broker_is_restarted_with_the_same_owner_token(broker, monkeypatch):
    import threading

    _, supervisor, root = broker
    before = read_owner(root)
    restarted = threading.Event()
    fence = supervisor._fence

    def observe_fence():
        fence()
        restarted.set()

    monkeypatch.setattr(supervisor, "_fence", observe_fence)
    crashed = supervisor._process
    crashed.kill()
    crashed.wait(timeout=10)
    assert restarted.wait(20), "replacement broker never completed its fence barrier"
    assert supervisor._process is not crashed
    assert supervisor._process.poll() is None
    assert read_owner(root) == before
    assert os.path.exists(supervisor.socket_path)


def test_a_new_supervisor_rotates_generation_and_refuses_the_old_pair(broker):
    from hashlib import sha256

    from tinyassets.broker.fence import Fence
    from tinyassets.broker.process import lease_verifier

    _, supervisor, root = broker
    before = read_owner(root)
    supervisor.stop()
    replacement = BrokerSupervisor(root, child_env=supervisor._child_env)
    try:
        replacement.start()
        after = read_owner(root)
        assert after["generation"] == before["generation"] + 1
        assert after["token"] != before["token"]
        verify = lease_verifier(sha256(replacement._proof.encode()).hexdigest(),
                                after["generation"])
        assert verify(after["generation"], replacement._proof)
        assert not verify(before["generation"], replacement._proof)
        fence = Fence(root / ".broker" / "state" / "fence.json", verify_lease_proof=verify)
        assert not fence.admits(before["generation"], before["token"])
    finally:
        replacement.stop()

