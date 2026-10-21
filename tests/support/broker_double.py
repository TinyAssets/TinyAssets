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
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

import pytest

from tests.support.admission import drive, projection

#: The broker reads peer credentials off a Unix socket. Everything in this
#: module is inert where that does not exist; the suite is proven on Linux.
SUPPORTED = sys.platform != "win32" and hasattr(socket, "SO_PEERCRED")
_BROKER_PROCESS = ContextVar("test_broker_process", default=False)


@contextmanager
def broker_process():
    """Model the broker's separate process within a threaded protocol fixture."""
    token = _BROKER_PROCESS.set(True)
    try:
        yield
    finally:
        _BROKER_PROCESS.reset(token)

#: The proof the double's fence accepts. Startup normally mints this and hands
#: its hash to the privileged launcher; no test may depend on the value.
_PROOF = "pytest-in-process-broker-double-lease-proof"

_START_TIMEOUT_S = 15.0


def broker_ledger_path(data_root: str | Path) -> Path:
    """Where the ledger actually lives. Seed fixtures through this, never
    ``<data_root>/outbound.db``: that path belongs to nothing now."""
    return Path(data_root).resolve() / ".broker" / "outbound.db"


def seed_unchecked_definition(**fields):
    """Inject legacy/foreign metadata for runtime refusal tests, without registration.

    Real registration now rejects these references before they enter the registry.
    Keep the runtime guards exercised against already-stored untrusted rows too.
    """
    from tinyassets.providers.definition import ProviderDefinition, _definition_id, _load, _write

    definition = ProviderDefinition(id=_definition_id(**fields), **fields,
                                    visibility="private", created_at="now")
    center = definition.universe_id
    _write(center, [*_load(center), definition.as_dict()])
    return definition


def seed_owner_admission(data_root: Path, *, principal: str, center: str) -> None:
    """Seed real mapper records before a fixture's in-process broker starts."""
    if not SUPPORTED:
        return  # The broker double itself is Linux-only.
    from tinyassets.broker.owner_identities import OwnerIdentities

    state = Path(data_root) / ".broker/state"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    state.chmod(0o700)
    state.parent.chmod(0o700)
    from tests.support.admission import Plans

    identities = OwnerIdentities(state / "owner-identities.db", initialize=True,
                                 plans=Plans(data_root))
    identities.resolve(principal, allocate=True)
    drive(identities.admit(principal, routing=projection(identities.path)))


def _peer_uid(sock: socket.socket) -> int:
    raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    return struct.unpack("3i", raw)[1]


class _InProcessBroker:
    """One served broker plus the supervisor surface the daemon side uses."""

    def __init__(self, data_root: Path, socket_path: Path) -> None:
        from tinyassets.broker.fence import Fence
        from tinyassets.broker.ops import OpStore
        from tinyassets.broker.owner_identities import OwnerIdentities
        from tinyassets.broker.policy import PolicyStore
        from tinyassets.broker.process import _Dispatchers
        from tinyassets.broker.server import OWNER, BrokerServer

        self.data_root = data_root
        self.socket_path = socket_path
        state = data_root / ".broker"
        state.mkdir(parents=True, exist_ok=True)
        state.chmod(0o700)
        from tests.support.admission import Plans

        identity_path = state / "state/owner-identities.db"
        seeded_admission = identity_path.exists()
        identity_path.parent.mkdir(mode=0o700, exist_ok=True)
        self.identities = OwnerIdentities(identity_path, initialize=True, plans=Plans(data_root))
        policy_store = PolicyStore(data_root, self.identities) if seeded_admission else None
        if policy_store is not None:
            policy_store.bootstrap()
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
            policy_store=policy_store,
            data_root=data_root,
        )
        # Synthetic shared processes exchange the production snapshot through
        # the real router dispatcher; process suites exercise kernel IPC.
        from tinyassets.router_credentials import routing_dispatch
        from tinyassets.routing_snapshot import RoutingSnapshot, _snapshots

        _snapshots.setdefault(data_root, RoutingSnapshot())
        routing = self.server.routing
        routing._router = lambda request: routing_dispatch(data_root, request)
        async def changed(owner):
            return await asyncio.to_thread(routing.publish)
        routing.changed = changed
        from tests.support.admission import bind

        bind(self.identities.path, routing)
        routing.publish()
        self._loop = asyncio.new_event_loop()
        self._listener: asyncio.AbstractServer | None = None
        listening = threading.Event()
        failure: list[BaseException] = []

        @broker_process()
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
                    pending = [task for task in asyncio.all_tasks()
                               if task is not asyncio.current_task()]
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                finally:
                    done.set()

            asyncio.run_coroutine_threadsafe(shutdown(), self._loop)
            done.wait(6)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("in-process broker thread did not stop")
        self._loop.close()


class _Registry:
    """Lazily started brokers for one test, keyed by resolved data root."""

    def __init__(self) -> None:
        self._brokers: dict[Path, _InProcessBroker] = {}
        self._sockets: list[str] = []
        self._lock = threading.RLock()

    def supervisor_for(self, data_root) -> _InProcessBroker:
        from tinyassets.owner_runtime import broker_egress, current

        if current() is not None:
            return broker_egress()
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

    from tinyassets import owner_runtime
    from tinyassets.boxes import layout as role_modes
    from tinyassets.broker import server, supervisor

    current = owner_runtime.current
    monkeypatch.setattr(owner_runtime, "current",
                        lambda: None if _BROKER_PROCESS.get() else current())
    run_stream = server._Connection._run

    @broker_process()
    def stream(*args, **kwargs):
        return run_stream(*args, **kwargs)

    # Raw stream threads do not inherit ContextVars; asyncio.to_thread does.
    monkeypatch.setattr(server._Connection, "_run", stream)

    if os.getuid() != 0:
        # The broker reads the daemon's liveness proofs and vault records
        # through gid 1102, which only the real image grants. Same-uid here,
        # so the group hand-off is this process's own gid.
        monkeypatch.setattr(role_modes, "BROKER_READ_GID", os.getgid())

    registry = _Registry()
    monkeypatch.setattr(supervisor, "get_supervisor", registry.supervisor_for)
    from tinyassets import routing_snapshot

    snapshot = routing_snapshot.snapshot

    def started_snapshot(root):
        root = Path(root)
        # Concurrent synthetic router clients share one completed startup, just
        # as they share the registry's broker. Do not expose its empty projection
        # while the first caller is still installing the startup snapshot.
        with registry._lock:
            exists = root in routing_snapshot._snapshots
            result = snapshot(root)
            if not exists:
                registry.supervisor_for(root)
            return result

    monkeypatch.setattr(routing_snapshot, "snapshot", started_snapshot)
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


@pytest.fixture(autouse=True)
def owner_protocol_transport(request, monkeypatch, in_process_broker):
    """Protocol units inject startup transport; process proofs use the real relay.

    The unit broker uses one UID for both ends and accepts OWNER frames. Keep
    that socket protocol fixture separate from the real BOX admission suites.
    """
    if in_process_broker is None:
        return
    install_owner_transport(
        monkeypatch, in_process_broker,
        patch_egress=not request.module.__name__.startswith("tests.test_owner_"),
    )


@pytest.fixture(autouse=True)
def deployed_router_app(monkeypatch, in_process_broker):
    """Provision the synthetic platform's gate before constructing its router.

    Process-boundary proofs opt out with role_split. Existing closed or damaged
    gates are never replaced, and requests use the production reader unchanged.
    """
    if in_process_broker is None:
        return
    import json

    from tinyassets import universe_server
    from tinyassets.router_traffic import NAME
    from tinyassets.storage import data_dir

    create = universe_server.create_streamable_http_app

    def deployed(*args, **kwargs):
        root = data_dir()
        root.mkdir(parents=True, exist_ok=True)
        try:
            with (root / NAME).open("x") as stream:
                json.dump({"ordinary": True}, stream)
        except FileExistsError:
            pass
        return create(*args, **kwargs)

    monkeypatch.setattr(universe_server, "create_streamable_http_app", deployed)


def install_owner_transport(monkeypatch, in_process_broker, *, patch_egress=True):
    """Install the same broker protocol fixture in pytest and spawned probes."""
    from tinyassets import owner_runtime
    from tinyassets.broker.client import BrokerClient
    from tinyassets.storage.agent_request_usage import UsageStore

    connect_owner = BrokerClient._connect_owner
    lease_owner = UsageStore._lease
    egress = owner_runtime.broker_egress

    def connect(client, sock):
        if owner_runtime.current() is not None:
            return connect_owner(client, sock)
        sock.connect(str(client._path))
        if client._verify_peer:
            client._verify_peer(sock)

    class Lease:
        def __init__(self, root, usage_id):
            from tinyassets.process_liveness import hold_liveness, owner_token

            self.owner_token = owner_token(root, broker_readable=True)
            self.token = "request_" + usage_id
            self.held = hold_liveness(root, self.token, broker_readable=True)

        def close(self):
            from tinyassets.singleton_lock import release_singleton_lock

            if self.held is not None:
                release_singleton_lock(self.held)
                self.held = None

    def lease(store, center, usage_id):
        if owner_runtime.current() is not None:
            return lease_owner(store, center, usage_id)
        return Lease(store.base, usage_id)

    def transport():
        if owner_runtime.current() is not None:
            return egress()
        from tinyassets.storage import data_dir

        return in_process_broker.supervisor_for(data_dir())

    from tinyassets import owner_channel

    channel_request = owner_channel.request

    def channel(center, document):
        if owner_runtime.current() is not None or document.get("op") != "MODEL_SOURCE":
            return channel_request(center, document)
        from tinyassets.broker.supervisor import get_supervisor
        from tinyassets.storage import data_dir

        live = get_supervisor(data_dir())
        if live is None:
            from tinyassets.storage.outbound_connections import ProxyRequestError

            raise ProxyRequestError("source publication broker unavailable")
        definition = document["definition"]
        client = BrokerClient(live.socket_path, principal=definition["owner_user_id"],
                              command_center=center, fence=live.fence,
                              verify_peer=live.verify_broker)
        return client.publish_model_source(definition)

    monkeypatch.setattr(owner_channel, "request", channel)
    monkeypatch.setattr(BrokerClient, "_connect_owner", connect)
    monkeypatch.setattr(UsageStore, "_lease", lease)
    if patch_egress:
        monkeypatch.setattr(owner_runtime, "broker_egress", transport)

    from tinyassets.broker import owner_reads
    from tinyassets.control_plane import next_wake

    remote = owner_reads.remote
    report = next_wake.report

    def shared(operation, **args):
        if operation == "plan" and owner_runtime.current() is None:
            return {"tier": "free", "ends_at": None}
        return remote(operation, **args)

    def wake(root):
        if owner_runtime.current() is not None:
            return report(root)
        return next_wake.next_wake_at(root)

    monkeypatch.setattr(owner_reads, "remote", shared)
    monkeypatch.setattr(next_wake, "report", wake)


@pytest.fixture(autouse=True)
def local_hook_channel(request, monkeypatch, tmp_path):
    """Hook store units inject an admitted owner and the router RPC transport.

    Router writes run against a separate temp root. Real runtime and role-split
    fixtures retain the real relay and credential socket.
    """
    if request.node.get_closest_marker("role_split") is not None or not SUPPORTED:
        return
    from contextvars import ContextVar
    from types import SimpleNamespace

    from tinyassets import owner_runtime, router_credentials
    from tinyassets.storage import webhook_hooks

    scope = ContextVar("hook_unit_admission", default=None)
    current = owner_runtime.current
    credential = owner_runtime.router_credential
    mint = webhook_hooks.mint
    routes = {}
    shared = tmp_path / "router"

    def runtime():
        admitted = scope.get()
        return current() or (SimpleNamespace(principal=admitted[1]) if admitted else None)

    def dispatch(owner, document):
        token = scope.set(None)
        try:
            return router_credentials.dispatch(shared, owner, document)
        finally:
            scope.reset(token)

    def rpc(operation, **fields):
        if current() is not None:
            return credential(operation, **fields)
        admitted = scope.get()
        if operation == "hook_mint" and admitted:
            shared.mkdir(exist_ok=True)
            # The fixture promises an admitted owner: create its real mapping
            # before exercising both production mint admission checks.
            from tinyassets.broker.admission import digest

            token = scope.set(None)
            try:
                seed_owner_admission(shared, principal=admitted[1],
                                     center="hook-" + digest(admitted[1])[:16])
            finally:
                scope.reset(token)
            result = dispatch(admitted[1], {"operation": operation})
            routes[webhook_hooks._hash_token(result["token"])] = admitted[1]
            return result
        if operation == "hook_revoke" and fields["token_hash"] in routes:
            return dispatch(routes[fields["token_hash"]], {"operation": operation, **fields})
        return credential(operation, **fields)

    def admitted_mint(root, **fields):
        if current() is not None:
            return mint(root, **fields)
        token = scope.set((root, fields.get("owner_principal_id")))
        try:
            return mint(root, **fields)
        finally:
            scope.reset(token)

    monkeypatch.setattr(owner_runtime, "current", runtime)
    monkeypatch.setattr(owner_runtime, "router_credential", rpc)
    monkeypatch.setattr(webhook_hooks, "mint", admitted_mint)
