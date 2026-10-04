"""The broker process: ``python -m tinyassets.broker.process``.

Started and supervised by the daemon (:mod:`.supervisor`), with the daemon's
environment minus the platform's own secrets (``platform_secrets.child_env``).
It is the only process that resolves an outbound credential; every caller
reaches it through the socket.

Configuration is the command line, never the environment a caller could set:

* ``--socket``: the Unix socket to serve (created 0600);
* ``--state``: the broker's own state (op records, the fence);
* ``--data-root``: where the connection ledger and the vaults live;
* ``--owner-uid``: the uid served as the owner channel (the daemon's);
* ``--proof-sha256``: the hash of the owner lease proof for the acquired generation.

The lease: until the owner lease (S8a) holds a hashed proof per acquisition,
the daemon mints a proof at start and hands its hash to the broker it spawns.
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


def lease_verifier(proof_sha256: str, owner_generation: int):
    expected = bytes.fromhex(proof_sha256)

    def verify(generation: int, proof: str) -> bool:
        if generation != owner_generation or not isinstance(proof, str):
            return False
        return hmac.compare_digest(hashlib.sha256(proof.encode("utf-8")).digest(), expected)

    return verify


class _Dispatchers:
    """One trusted dispatcher per grant, built from the ledger's own config."""

    def __init__(self, data_root: Path, *, allow_test_fixtures: bool) -> None:
        self._data_root = data_root
        self._allow_test_fixtures = allow_test_fixtures
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()

    def ledger_for(self, principal: str):
        from tinyassets.storage.outbound_connections import ConnectionLedger

        return ConnectionLedger(self._data_root / "outbound.db",
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
                dispatch = _build_credential_broker_dispatch(config)
                self._cache[key] = dispatch
        return dispatch


async def serve(args: argparse.Namespace) -> None:
    from tinyassets.storage.outbound_connections import _sanitize_child_environment

    _sanitize_child_environment()  # no TLS key logging, no ambient proxies
    state = Path(args.state)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    dispatchers = _Dispatchers(Path(args.data_root),
                               allow_test_fixtures=args.allow_test_fixtures)
    server = BrokerServer(
        ledger_for=dispatchers.ledger_for, dispatch_for=dispatchers.dispatch_for,
        ops=OpStore(state / "ops.db"),
        fence=Fence(state / "fence.json",
                    verify_lease_proof=lease_verifier(args.proof_sha256, args.generation)),
        roles={int(args.owner_uid): OWNER},
    )
    socket_path = Path(args.socket)
    socket_path.unlink(missing_ok=True)
    old_umask = os.umask(0o177)  # the socket is created 0600
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
    parser.add_argument("--generation", required=True, type=int)
    parser.add_argument("--allow-test-fixtures", action="store_true")
    asyncio.run(serve(parser.parse_args(argv)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
