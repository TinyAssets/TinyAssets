"""The broker process: ``python -m tinyassets.broker.process``.

Started and supervised by the privileged launcher with a static environment.
It is the only process that resolves an outbound credential; every caller
reaches it through the socket.

Configuration is the command line, never the environment a caller could set:

* ``--socket``: the Unix socket to serve (0660 in the role-split IPC directory);
* ``--state``: the broker's own state (op records, the fence);
* ``--data-root``: where the connection ledger and the vaults live;
* ``--owner-uid``: the uid served as the owner channel (the daemon's);
* ``--proof-sha256``: the hash of the owner lease proof for the acquired generation;
* ``--mapper-channel``/``--mapper-pid``: DA2's inherited read-only mapper pair
  and its one authenticated peer (bounded bootstrap only).

The lease: until the owner lease (S8a) holds a hashed proof per acquisition,
the daemon mints a proof at start and hands its hash to the launcher.
That is the single-process lease's own rule (one owner, one proof per acquisition) carried
across the process boundary; ``verify_lease_proof`` switches to the lease
authority when it exists.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import os
import signal
import threading
from pathlib import Path
from typing import Any

from tinyassets.broker.fence import Fence
from tinyassets.broker.ops import OpStore
from tinyassets.broker.server import OWNER, BrokerServer


def lease_verifier(proof_sha256: str):
    expected = bytes.fromhex(proof_sha256)

    def verify(generation: int, proof: str) -> bool:
        if not isinstance(proof, str):
            return False
        return hmac.compare_digest(hashlib.sha256(proof.encode("utf-8")).digest(), expected)

    return verify


class _Dispatchers:
    """One trusted dispatcher per grant, built from the ledger's own config."""

    def __init__(self, data_root: Path, *, allow_test_fixtures: bool,
                 role_split: bool = False) -> None:
        self._data_root = data_root
        self._ledger_root = data_root / ".broker" if role_split else data_root
        self._allow_test_fixtures = allow_test_fixtures
        self._role_split = role_split
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()

    def ledger_for(self, principal: str):
        from tinyassets.storage.outbound_connections import ConnectionLedger

        return ConnectionLedger(self._ledger_root / "outbound.db", data_root=self._data_root,
                                allow_test_fixtures=self._allow_test_fixtures,
                                verify_authenticated_principal=lambda: principal)

    def dispatch_for(self, principal: str, command_center: str, grant_id: str, resource: Any):
        from tinyassets.storage.outbound_connections import _build_credential_broker_dispatch

        key = f"{command_center}\0{grant_id}\0{principal}"
        with self._lock:
            dispatch = self._cache.get(key)
            if dispatch is None:
                config = self.ledger_for(principal).broker_dispatch_config(
                    grant_id=grant_id, universe_id=command_center,
                    provider=resource.provider, destination=resource.destination,
                    owner_user_id=resource.owner_user_id,
                    connection_type=resource.connection_type,
                )
                config["allow_local_refresh"] = not self._role_split
                dispatch = _build_credential_broker_dispatch(config)
                self._cache[key] = dispatch
        return dispatch


async def serve(args: argparse.Namespace) -> None:
    from tinyassets.storage.outbound_connections import _sanitize_child_environment

    _sanitize_child_environment()  # no TLS key logging, no ambient proxies
    role_split = getattr(args, "role_split", False)
    if role_split:
        import ctypes

        fields = dict(line.split(":", 1) for line in Path(
            "/proc/self/status").read_text().splitlines())
        if (os.getresuid() != (1002, 1002, 1002)
                or os.getresgid() != (1002, 1002, 1002) or os.getgroups() != [1102]
                or any(int(fields[key], 16) != 0 for key in
                       ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))
                or int(fields["NoNewPrivs"]) != 1):
            raise RuntimeError("broker role identity is not retired")
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(4, 0, 0, 0, 0) != 0 or libc.prctl(3, 0, 0, 0, 0) != 0:
            raise RuntimeError("broker non-dumpability failed")
        os.umask(0o077)  # private SQLite journals and proxy runtime files
    state = Path(args.state)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    dispatchers = _Dispatchers(Path(args.data_root),
                               allow_test_fixtures=args.allow_test_fixtures,
                               role_split=role_split)
    # Initialization belongs to the privileged, fenced volume migration. A
    # missing map must never silently restart allocation at the first UID.
    from tinyassets.broker.owner_identities import OwnerIdentities

    identity_path = state / "owner-identities.db"
    identities = OwnerIdentities(identity_path) if role_split and identity_path.exists() else None
    if getattr(args, "mapper_channel", None) is not None:
        # DA2: only the bounded bootstrap passes this inherited descriptor.
        from tinyassets.broker import mapper_channel

        if not role_split or args.mapper_pid is None:
            raise RuntimeError("mapper channel requires the role-split bootstrap")
        mapper_channel.start(args.mapper_channel, args.mapper_pid, identities)
    server = BrokerServer(
        ledger_for=dispatchers.ledger_for, dispatch_for=dispatchers.dispatch_for,
        ops=OpStore(state / "ops.db"),
        fence=Fence(state / "fence.json",
                    verify_lease_proof=lease_verifier(args.proof_sha256),
                    lease_sha256=args.proof_sha256),
        roles={int(args.owner_uid): OWNER},
        owner_identities=identities,
    )
    socket_path = Path(args.socket)
    socket_path.unlink(missing_ok=True)
    old_umask = os.umask(0o117 if role_split else 0o177)
    try:
        listener = await server.serve(socket_path)
    finally:
        os.umask(old_umask)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    async with listener:
        await stop.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tinyassets.broker.process")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--owner-uid", required=True, type=int)
    parser.add_argument("--proof-sha256", required=True)
    parser.add_argument("--role-split", action="store_true")
    parser.add_argument("--allow-test-fixtures", action="store_true")
    parser.add_argument("--mapper-channel", type=int)
    parser.add_argument("--mapper-pid", type=int)
    args = parser.parse_args(argv)
    asyncio.run(serve(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
