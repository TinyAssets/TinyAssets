"""The in-process credential broker every non-oracle test reaches.

Production has no fallback: the daemon never opens the connection ledger and
never resolves a credential, so a test that touches a connection needs a REAL
:class:`~tinyassets.broker.server.BrokerServer` to answer it. This plugin
starts one per data root, lazily, on the first
:func:`tinyassets.broker.supervisor.get_supervisor` call, and tears it down at
the end of the test.

What is real here: the server, its op store, its fence, the ledger it opens
(``<data_root>/.broker/outbound.db``) and the dispatch it builds through
:class:`tinyassets.broker.process._Dispatchers`. What is a double: the
privileged bootstrap that would normally fork the broker as uid 1002 and hand
the daemon its lease proof. So the peer check is same-uid rather than
``(broker_pid, 1002, 1002)``, and the broker runs on a thread instead of in its
own process.

A test that exercises the real bootstrap, broker process or cells carries the
``role_split`` marker and gets no double.

Linux-only: the broker classifies peers with ``SO_PEERCRED``.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
from pathlib import Path

import pytest

#: The broker reads peer credentials off a Unix socket. Everything in this
#: module is inert where that does not exist; the suite is proven on Linux.
SUPPORTED = sys.platform != "win32" and hasattr(socket, "SO_PEERCRED")

#: The proof the double's fence accepts. Startup normally mints this and hands
#: its hash to the privileged launcher; no test may depend on the value.
_PROOF = "pytest-in-process-broker-double-lease-proof"

_START_TIMEOUT_S = 15.0


def broker_ledger_path(data_root: str | Path) -> Path:
    """Where the ledger actually lives. Seed fixtures through this, never
    ``<data_root>/outbound.db``: that path belongs to nothing now."""
    return Path(data_root).resolve() / ".broker" / "outbound.db"


def _peer_uid(sock: socket.socket) -> int:
    raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    return struct.unpack("3i", raw)[1]


class _InProcessBroker:
    """One served broker plus the supervisor surface the daemon side uses."""

    def __init__(self, data_root: Path, socket_path: Path) -> None:
        from tinyassets.broker.fence import Fence
        from tinyassets.broker.ops import OpStore
        from tinyassets.broker.owner_identities import OwnerIdentities
        from tinyassets.broker.process import _Dispatchers
        from tinyassets.broker.server import OWNER, BrokerServer

        self.data_root = data_root
        self.socket_path = socket_path
        state = data_root / ".broker"
        state.mkdir(parents=True, exist_ok=True)
        identity_path = state / "owner-identities.db"
        self.identities = OwnerIdentities(identity_path) if identity_path.exists() else None
        # The same dispatcher factory the broker process uses, so the ledger
        # root and the dispatch config are production's, not the test's.
        self._dispatchers = _Dispatchers(data_root, allow_test_fixtures=True)
        self._fence = Fence(
            state / "fence.json",
            verify_lease_proof=lambda generation, proof: proof == _PROOF,
        )
        self.server = BrokerServer(
            ledger_for=self._dispatchers.ledger_for,
            dispatch_for=self._dispatchers.dispatch_for,
            ops=OpStore(state / "ops.db"),
            fence=self._fence,
            roles={os.getuid(): OWNER},
            owner_identities=self.identities,
        )
        self._loop = asyncio.new_event_loop()
        self._listener: asyncio.AbstractServer | None = None
        listening = threading.Event()
        failure: list[BaseException] = []

        def run() -> None:
            asyncio.set_event_loop(self._loop)
            try:
                self._listener = self._loop.run_until_complete(
                    self.server.serve(socket_path))
            except BaseException as exc:  # noqa: BLE001 - reported to the fixture
                failure.append(exc)
                listening.set()
                return
            listening.set()
            self._loop.run_forever()

        self._thread = threading.Thread(
            target=run, name="pytest-broker-double", daemon=True)
        self._thread.start()
        if not listening.wait(_START_TIMEOUT_S):
            raise RuntimeError("the in-process broker double did not start listening")
        if failure:
            raise failure[0]
        self._pair = self._fence.barrier(1, _PROOF)

    # ---- the supervisor surface (tinyassets.broker.supervisor.BrokerSupervisor)
    def fence(self) -> tuple[int, str]:
        return self._pair

    def verify_broker(self, sock: socket.socket) -> None:
        """The double serves from this very process, so the peer is this uid.

        Production compares ``(broker_pid, 1002, 1002)``; the shape of the
        check — a refusal that names the broker identity — is preserved so a
        caller cannot tell the two apart by its error.
        """
        from tinyassets.broker.supervisor import BrokerUidSplitRequired

        if _peer_uid(sock) != os.getuid():
            raise BrokerUidSplitRequired("broker peer does not have the broker identity")

    def close(self) -> None:
        """Stop accepting, let in-flight connections unwind, then stop the loop.

        Stopping the loop first leaves a pending ``_connection`` coroutine to be
        garbage-collected against a closed loop, which pytest reports as an
        unraisable exception in whichever test happens to be running.
        """
        listener = self._listener
        if listener is not None:
            done = threading.Event()

            async def shutdown() -> None:
                listener.close()
                try:
                    await asyncio.wait_for(listener.wait_closed(), 5)
                finally:
                    done.set()

            asyncio.run_coroutine_threadsafe(shutdown(), self._loop)
            done.wait(6)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


class _Registry:
    """Lazily started brokers for one test, keyed by resolved data root."""

    def __init__(self) -> None:
        self._brokers: dict[Path, _InProcessBroker] = {}
        self._sockets: list[str] = []
        self._lock = threading.RLock()

    def supervisor_for(self, data_root) -> _InProcessBroker:
        root = Path(data_root).resolve()
        with self._lock:
            broker = self._brokers.get(root)
            if broker is None:
                # A Unix socket path is capped near 108 bytes and pytest's temp
                # roots are long, so the socket lives in its own short dir.
                directory = tempfile.mkdtemp(prefix="tabd")
                self._sockets.append(directory)
                broker = _InProcessBroker(root, Path(directory) / "b.sock")
                self._brokers[root] = broker
            return broker

    def close(self) -> None:
        with self._lock:
            for broker in self._brokers.values():
                broker.close()
            self._brokers.clear()
            for directory in self._sockets:
                shutil.rmtree(directory, ignore_errors=True)
            self._sockets.clear()


@pytest.fixture(autouse=True)
def in_process_broker(request, monkeypatch):
    """Answer ``get_supervisor`` with a real, lazily started broker.

    Yields the registry, so a test can assert on the served broker
    (``in_process_broker.supervisor_for(root).server``) without starting one it
    does not need.
    """
    if request.node.get_closest_marker("role_split") is not None or not SUPPORTED:
        yield None
        return

    from tinyassets import role_modes
    from tinyassets.broker import supervisor

    if os.getuid() != 0:
        # The broker reads the daemon's liveness proofs and vault records
        # through gid 1102, which only the real image grants. Same-uid here,
        # so the group hand-off is this process's own gid.
        monkeypatch.setattr(role_modes, "BROKER_READ_GID", os.getgid())

    registry = _Registry()
    monkeypatch.setattr(supervisor, "get_supervisor", registry.supervisor_for)
    try:
        yield registry
    finally:
        registry.close()


@pytest.fixture
def broker_ledger(tmp_path):
    """A ledger at the path the broker actually opens, for seeding fixtures."""
    from tinyassets.storage.outbound_connections import ConnectionLedger

    def make(data_root=None, *, principal=None, allow_test_fixtures=True):
        root = Path(data_root if data_root is not None else tmp_path).resolve()
        return ConnectionLedger(
            broker_ledger_path(root), data_root=root,
            allow_test_fixtures=allow_test_fixtures,
            verify_authenticated_principal=(
                None if principal is None else (lambda: principal)),
        )

    return make
