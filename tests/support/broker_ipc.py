"""The shared real-broker-IPC fixture: one served broker, scripted upstream.

Eighteen broker consumer suites used to import this from
``tests/test_broker_discovery_http.py``, which made one test module the home of
another's fixture. It lives here instead, so a consumer suite depends on a
support module rather than on another suite's import side effects.

The broker, its fence, its op store and the ledger it opens are real; only the
upstream is scripted, so nothing here reaches the network. The ledger is at
``<data_root>/.broker/outbound.db``, where production puts it.
"""
from __future__ import annotations

import os
import socket
import struct
import threading
from types import SimpleNamespace

import pytest

from tests.support.broker_double import broker_ledger_path
from tests.test_broker_server import Script, broker  # noqa: F401 - re-exported fixture
from tinyassets.broker import supervisor
from tinyassets.providers.discovery_http import read_granted_discovery_document
from tinyassets.storage.outbound_connections import ConnectionLedger

__all__ = ["Script", "broker", "discovery", "broker_ledger_path"]

URL = "https://models.example.com/catalogue"


@pytest.fixture
def discovery(in_process_broker, broker, tmp_path, monkeypatch):  # noqa: F811
    """A served broker whose supervisor the daemon side reaches.

    Takes ``in_process_broker`` so its own ``get_supervisor`` patch is applied
    after the autouse double's and is the one the test sees.
    """
    from tinyassets import role_modes

    # Same-uid protocol fixture; production-image probes use real gid 1102.
    monkeypatch.setattr(role_modes, "BROKER_READ_GID", os.getgid())
    path = broker_ledger_path(tmp_path)
    ledger = ConnectionLedger(path, data_root=tmp_path)
    ledger.create_connection(
        connection_id="conn-a", owner_user_id="alice", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("GET",),
        provider="http", destination="compute:discovery",
        credential_ref="vault://http/synthetic",
        allowed_endpoints=[{"host": "models.example.com", "path_template": "/catalogue",
                            "methods": ["GET"]}],
    )
    ledger.grant_connection(grant_id="grant-a", connection_id="conn-a",
                            owner_user_id="alice", universe_id="cc-alice")
    daemon_thread = threading.get_ident()

    def ledger_for(principal):
        assert threading.get_ident() != daemon_thread
        return ConnectionLedger(path, data_root=tmp_path,
                                verify_authenticated_principal=lambda: principal)

    broker.server._ledger_for = ledger_for
    broker.upstreams["next"] = lambda: Script([b'{"data":', b'["alice"]}'])

    def verify_peer(sock):
        peer = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        pid, uid, _ = struct.unpack("3i", peer)
        assert (pid, uid) == (os.getpid(), os.getuid())

    live = SimpleNamespace(socket_path=broker.path,
                           fence=lambda: (broker.state["generation"], broker.state["token"]),
                           verify_broker=verify_peer)
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: live)

    def read(**changes):
        args = dict(db_path=tmp_path / "outbound.db", grant_id="grant-a",
                    owner_user_id="alice", universe_id="cc-alice", url=URL)
        return read_granted_discovery_document(**(args | changes))

    return SimpleNamespace(read=read, ledger=ledger, broker=broker, root=tmp_path)
