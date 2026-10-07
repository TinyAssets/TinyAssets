"""The broker cannot be switched on while every role shares one uid (v1 deviation (c))."""

from __future__ import annotations

import pytest

from tinyassets.broker.supervisor import (
    ENV_SWITCH,
    PROCESS,
    BrokerUidSplitRequired,
    start_broker,
)


def test_selecting_the_broker_on_a_shared_uid_host_refuses_to_start(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_SWITCH, PROCESS)
    with pytest.raises(BrokerUidSplitRequired, match="uid split"):
        start_broker(tmp_path)


def test_unselected_the_daemon_starts_without_it(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_SWITCH, raising=False)
    assert start_broker(tmp_path) is None


def test_foreign_process_cannot_use_inherited_owner_state(monkeypatch, tmp_path):
    from tinyassets.broker import supervisor as module

    monkeypatch.setattr(module, "_protect_daemon", lambda: None)
    supervisor = module.BrokerSupervisor(tmp_path)
    monkeypatch.setattr(module.os, "getpid", lambda: supervisor._pid + 1)
    with pytest.raises(BrokerUidSplitRequired, match="inherited"):
        supervisor.fence()


def test_wrong_broker_peer_is_rejected_before_fence_is_sent(monkeypatch, tmp_path):
    from tinyassets.broker import supervisor as module

    monkeypatch.setattr(module, "_protect_daemon", lambda: None)
    monkeypatch.setattr(module, "_peer", lambda sock: (42, 1001, 1001))
    supervisor = module.BrokerSupervisor(tmp_path)

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

    monkeypatch.setattr(module.socket, "socket", lambda *args: Socket())
    monkeypatch.setattr(module.socket, "AF_UNIX", 1, raising=False)
    with pytest.raises(BrokerUidSplitRequired, match="broker identity"):
        supervisor._fence()


def test_legacy_worker_refuses_before_allocating_a_process(monkeypatch, tmp_path):
    from tinyassets.storage.outbound_connections import ConnectionLedger, ProxyRequestError

    monkeypatch.setenv(ENV_SWITCH, PROCESS)
    ledger = ConnectionLedger(tmp_path / "outbound.db")
    with pytest.raises(ProxyRequestError, match="legacy proxy worker is forbidden"):
        ledger._start_scoped_proxy(grant_id="grant", universe_id="alice", provider="http",
                                   destination="https://example.com", scopes=("GET",),
                                   owner_user_id="alice", connection_type="http")


def test_stopped_supervisor_invalidates_existing_channel(monkeypatch, tmp_path):
    from tinyassets.broker import supervisor as module
    from tinyassets.storage.outbound_connections import ProxyRequestError

    monkeypatch.setattr(module, "_protect_daemon", lambda: None)
    supervisor = module.BrokerSupervisor(tmp_path)
    supervisor._pair = (1, "private-token")
    assert supervisor.fence() == (1, "private-token")
    supervisor.stop()
    with pytest.raises(ProxyRequestError, match="not running"):
        supervisor.fence()
