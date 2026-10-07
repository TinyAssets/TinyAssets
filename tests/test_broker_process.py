"""The broker as a real child process, end to end (S6 part 4).

POSIX only. The fixture supplies process startup while the daemon-side
supervisor runs the owner's fence barrier and retains it in memory; with the switch
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

from tinyassets.broker.supervisor import ENV_SWITCH, PROCESS, BrokerSupervisor, get_supervisor
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
    # Real broker transport/ledger test under the oracle's unprivileged uid.
    # Only role startup is substituted here; distinct-uid launcher acceptance
    # runs scripts/role_launcher_oracle.py in the production image.
    import subprocess
    import time
    from hashlib import sha256

    from tinyassets.broker import supervisor as module

    processes = []
    socket_path = root / "broker.sock"
    monkeypatch.setattr(module, "BROKER_SOCKET", socket_path)
    monkeypatch.setattr(module, "_protect_daemon", lambda: None)

    def peer(self, sock):
        assert module._peer(sock)[1:] == (os.getuid(), os.getgid())

    def acquire(self):
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        socket_path.unlink(missing_ok=True)
        argv = [sys.executable, "-m", "tinyassets.broker.process",
                "--socket", str(socket_path), "--state", str(root / ".broker/state"),
                "--data-root", str(root), "--owner-uid", str(os.getuid()),
                "--proof-sha256", sha256(self._proof.encode()).hexdigest()]
        environment = dict(os.environ)
        environment.pop("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", None)
        process = subprocess.Popen(argv, env=environment, close_fds=True)
        processes.append(process)
        deadline = time.monotonic() + 20
        while not socket_path.exists():
            assert process.poll() is None, "fixture broker exited"
            assert time.monotonic() < deadline, "fixture broker timed out"
            time.sleep(0.02)

    monkeypatch.setattr(BrokerSupervisor, "_acquire", acquire)
    monkeypatch.setattr(BrokerSupervisor, "verify_broker", peer)
    supervisor = BrokerSupervisor(root)
    supervisor.start()
    supervisor._test_processes = processes
    try:
        yield ledger, supervisor, root
    finally:
        current = get_supervisor(root)
        if current is not None:
            current.stop()
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)


def test_the_supervisor_publishes_the_owner_pair_with_tight_permissions(broker):
    _, supervisor, root = broker
    owner = get_supervisor(root)
    assert owner is supervisor
    generation, token = owner.fence()
    assert generation == 1 and token
    assert not (root / ".broker" / "owner.json").exists()
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
    _, supervisor, root = broker
    before = supervisor.fence()
    crashed = supervisor._test_processes[-1]
    crashed.kill()
    crashed.wait(timeout=10)
    # Lifecycle belongs to the launcher; the fixture simulates its restart
    # without calling stop/start or re-fencing the daemon's in-memory state.
    supervisor._acquire()
    assert supervisor._test_processes[-1] is not crashed
    assert supervisor._test_processes[-1].poll() is None
    assert get_supervisor(root).fence() == before
    supervisor._fence()
    assert supervisor.fence() == before
    assert os.path.exists(supervisor.socket_path)


def test_a_new_supervisor_rotates_generation_and_refuses_the_old_pair(broker):
    from hashlib import sha256

    from tinyassets.broker.fence import Fence
    from tinyassets.broker.process import lease_verifier

    _, supervisor, root = broker
    before = supervisor.fence()
    supervisor.stop()
    replacement = BrokerSupervisor(root)
    try:
        replacement.start()
        after = replacement.fence()
        assert after[0] == before[0] + 1
        assert after[1] != before[1]
        verify = lease_verifier(sha256(replacement._proof.encode()).hexdigest())
        assert verify(after[0], replacement._proof)
        assert not verify(after[0], supervisor._proof)
        fence = Fence(root / ".broker" / "state" / "fence.json", verify_lease_proof=verify)
        assert not fence.admits(*before)
        assert fence.admits(*after)
        assert not (root / ".broker/owner.json").exists()
    finally:
        replacement.stop()
