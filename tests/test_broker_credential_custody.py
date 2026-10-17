"""Only the broker writes credentials, including across a real process boundary."""
from __future__ import annotations

import ast
import asyncio
import json
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets.broker.http_connect import connect_operation
from tinyassets.credential_vault import custody_metadata_path, load_credential_vault


def test_no_vault_writer_outside_broker():
    forbidden = {"write_credential_vault", "forget_credential", "exclusive_credential_vault",
                 "_write_credential_vault_locked", "_persist_credential_vault_file",
                 "_persist_role_vault", "erase_owner", "refresh_credential"}
    violations = []
    for path in Path("tinyassets").rglob("*.py"):
        if path.is_relative_to(Path("tinyassets/broker")):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if any(alias.name in forbidden for alias in node.names):
                    violations.append((str(path), node.lineno))
            elif isinstance(node, ast.Call):
                name = (node.func.id if isinstance(node.func, ast.Name) else
                        node.func.attr if isinstance(node.func, ast.Attribute) else "")
                if name in forbidden:
                    violations.append((str(path), node.lineno))
    assert violations == []


def _broker_process(root, path, channel):
    from tinyassets.broker import vault
    from tinyassets.broker.fence import Fence
    from tinyassets.broker.ops import OpStore
    from tinyassets.broker.process import _Dispatchers
    from tinyassets.broker.server import OWNER, BrokerServer

    root = Path(root)
    state = root / ".broker"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    dispatchers = _Dispatchers(root, allow_test_fixtures=True)
    fence = Fence(state / "fence.json", verify_lease_proof=lambda *_: True)
    server = BrokerServer(ledger_for=dispatchers.ledger_for,
        dispatch_for=dispatchers.dispatch_for, ops=OpStore(state / "ops.db"),
        fence=fence, roles={os.getuid(): OWNER})
    original = vault.write_credential_vault

    def observed(*args, **kwargs):
        result = original(*args, **kwargs)
        channel.send(("writer", os.getpid()))
        return result

    vault.write_credential_vault = observed

    async def serve():
        listener = await server.serve(Path(path))
        channel.send(("ready", fence.barrier(1, "fixture-proof")))
        try:
            while not channel.poll():
                await asyncio.sleep(0.01)
            channel.recv()
        finally:
            listener.close()
            await listener.wait_closed()
    asyncio.run(serve())


@pytest.mark.parametrize("scheme,secret", [
    ("bearer", "fixture-bearer"),
    ("basic", "fixture-user:fixture-password"),
    ("header", "fixture-header"),
    ("url_secret", "fixture-url-token"),
    ("oauth1a", json.dumps({"api_key": "key", "api_secret": "secret",
                            "access_token": "access", "access_token_secret": "access-secret"})),
    ("oauth2", json.dumps({"scheme": "oauth2", "v": 1, "access_token": "access",
                          "refresh_token": "refresh", "client_id": "client",
                          "token_url": "https://auth.example.com/token"})),
])
def test_deposit_is_written_in_broker_process(tmp_path, monkeypatch, scheme, secret):
    from tinyassets.api.http_connection import _ids
    from tinyassets.broker import supervisor, vault
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    path = tmp_path / "broker.sock"
    root = tmp_path / "shared"
    process = ctx.Process(target=_broker_process, args=(str(root), str(path), child))
    process.start()
    child.close()
    try:
        assert parent.poll(20), "broker did not start"
        kind, pair = parent.recv()
        assert kind == "ready"
        live = SimpleNamespace(socket_path=path, fence=lambda: pair, verify_broker=lambda _: None)
        monkeypatch.setattr(supervisor, "get_supervisor", lambda _: live)

        def forbidden(*args, **kwargs):
            raise AssertionError("daemon wrote custody")
        monkeypatch.setattr(vault, "write_credential_vault", forbidden)
        policy = dict(auth_scheme=scheme, scopes=["GET"], access_mode="exact", git_host="",
                      endpoints=[{"host": "api.example.com", "methods": ["GET"],
                                  "path_template": "/{secret}" if scheme == "url_secret"
                                  else "/data"}])
        args = dict(principal="owner", command_center="center", destination="fixture",
                    policy=policy)
        prepared = connect_operation(root, **args, action="prepare")
        result = connect_operation(root, **args, action="commit",
                                   expected=prepared["revision"], secret=secret)
        assert result.get("projection", {}).get("status") == "provisioned", result
        assert parent.poll(5)
        assert parent.recv() == ("writer", process.pid)
        assert process.pid != os.getpid()
        assert load_credential_vault(root / "center")[0]["token"] == secret
        connection, grant = _ids(universe_id="center", destination="fixture")
        ledger = ConnectionLedger(root / ".broker/outbound.db", data_root=root)
        assert ledger.get_grant(grant).connection_id == connection
        assert custody_metadata_path(root).exists()
        assert not (root / ".tinyassets.db").exists()
        assert not (root / "center").exists()
        assert secret not in json.dumps(result)
    finally:
        if process.is_alive():
            parent.send("stop")
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()
    assert process.exitcode == 0
