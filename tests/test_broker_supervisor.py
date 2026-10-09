"""The daemon's side of the broker boundary: adopt-only, same-process, uid 1002.

There is no switch and no launcher. The PID1 bootstrap forks the broker before
it retires and hands the daemon the lease proof in memory, so the only thing
this module can do is refuse: a different process, a peer that is not the
bootstrapped broker, or no acquisition at all.
"""

from __future__ import annotations

import os

import pytest

from tinyassets.broker.supervisor import BrokerSupervisor, BrokerUidSplitRequired

pytestmark = pytest.mark.skipif(
    not hasattr(os, "pidfd_open"), reason="the adoption pins a Linux process handle")

PROOF = "x" * 48

#: A LIVE pid, so the adoption's process handle stays unreadable and the
#: "broker exited" refusal does not fire for every case below. This process
#: stands in for the forked broker; only the uid:gid pair differs from
#: production, which is what ``_protect_daemon`` would have checked.
_ADOPTED = {"proof": PROOF}


def _adopted(module, tmp_path, **changes):
    """A supervisor shaped exactly as the bootstrap hands one over."""
    arguments = {"broker_pid": os.getpid(), "socket_path": tmp_path / "b.sock", **_ADOPTED}
    instance = BrokerSupervisor.from_bootstrap(tmp_path, **(arguments | changes))
    return instance


@pytest.fixture
def adoptable(monkeypatch):
    """Stand in for the retired-daemon check the production image makes."""
    from tinyassets.broker import supervisor as module

    monkeypatch.setattr(module, "_protect_daemon", lambda: None)
    return module


@pytest.mark.parametrize("bad", [
    {"broker_pid": 0}, {"broker_pid": -1}, {"broker_pid": True},
    {"proof": "short"}, {"proof": b"x" * 48},
])
def test_an_invalid_bootstrap_handover_is_refused(adoptable, tmp_path, bad):
    with pytest.raises(BrokerUidSplitRequired, match="invalid broker bootstrap"):
        _adopted(adoptable, tmp_path, **bad)


def test_foreign_process_cannot_use_inherited_owner_state(adoptable, tmp_path, monkeypatch):
    supervisor = _adopted(adoptable, tmp_path)
    monkeypatch.setattr(adoptable.os, "getpid", lambda: supervisor._pid + 1)
    with pytest.raises(BrokerUidSplitRequired, match="inherited"):
        supervisor.fence()


def test_wrong_broker_peer_is_rejected_before_fence_is_sent(adoptable, tmp_path, monkeypatch):
    monkeypatch.setattr(adoptable, "_peer", lambda sock: (42, 1001, 1001))
    supervisor = _adopted(adoptable, tmp_path)

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, value):
            pass

        def connect(self, path):
            pass

        def sendall(self, payload):
            pytest.fail("owner proof sent to the wrong peer")

    monkeypatch.setattr(adoptable.socket, "socket", lambda *args: Socket())
    monkeypatch.setattr(adoptable.socket, "AF_UNIX", 1, raising=False)
    with pytest.raises(BrokerUidSplitRequired, match="broker identity"):
        supervisor._fence()


def test_an_unfenced_acquisition_has_no_channel(adoptable, tmp_path):
    """No fence pair, no egress: never a silent unfenced dispatch."""
    from tinyassets.storage.outbound_connections import ProxyRequestError

    supervisor = _adopted(adoptable, tmp_path)
    with pytest.raises(ProxyRequestError, match="not running"):
        supervisor.fence()


@pytest.mark.role_split
def test_without_an_acquisition_every_consumer_refuses_loudly(tmp_path):
    """``get_supervisor`` is None, and no consumer falls back to a local ledger."""
    from tinyassets.broker.capabilities import capability_operation
    from tinyassets.broker.catalog import connections
    from tinyassets.broker.ledger_queries import GRANTED_RESOURCE, query_ledger
    from tinyassets.storage.outbound_connections import ProxyRequestError

    from tinyassets.broker.supervisor import get_supervisor  # isort: skip

    assert get_supervisor(tmp_path) is None
    with pytest.raises(ProxyRequestError, match="not running"):
        query_ledger(tmp_path, query=GRANTED_RESOURCE, principal="alice",
                     command_center="cc-alice", grant_id="grant-a")
    with pytest.raises(ProxyRequestError, match="not running"):
        capability_operation(tmp_path, principal="alice", command_center="cc-alice",
                             grant_id="grant-a", connection_id="conn-a",
                             capability_kind="model_use")
    with pytest.raises(ProxyRequestError, match="not running"):
        list(connections(tmp_path, principal="alice", command_center="cc-alice"))
    assert not (tmp_path / "outbound.db").exists()
    assert not (tmp_path / ".broker" / "outbound.db").exists()
