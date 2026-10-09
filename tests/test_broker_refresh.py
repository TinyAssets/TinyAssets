"""Refresh uses the authenticated stream while the daemon keeps vault admission."""
# ruff: noqa: F811 -- imported pytest fixtures
import hashlib
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace

import pytest

from tests.support.broker_ipc import discovery  # noqa: F401
from tests.test_broker_server import Script, broker  # noqa: F401
from tinyassets import credential_refresh
from tinyassets import credential_vault as vault
from tinyassets import rpc_frames as rf
from tinyassets.broker.client import BrokerClient
from tinyassets.broker.ops import new_op_id
from tinyassets.broker.refresh import prepare
from tinyassets.connection_oauth import tokens

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Unix broker transport")


@pytest.fixture
def refresh_case(discovery, monkeypatch):
    with discovery.ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET auth_scheme='oauth2'")
    universe = discovery.root / "cc-alice"
    universe.mkdir()
    initial = tokens.TokenBundle("old-access", "https://issuer.example/token", "client",
                                 refresh_token="single-use", expires_at=1)
    vault.write_credential_vault(universe, [vault.http_credential_record(
        destination="synthetic", token=tokens.encode(initial))], owner_user_id="alice")
    spent = []
    def spend(current):
        assert current.refresh_token == "single-use", "token spent twice"
        spent.append(current)
        return replace(current, access_token="new-access", refresh_token="rotated",
                       expires_at=time.time() + 3600)
    monkeypatch.setattr(tokens, "refresh", spend)
    store = tokens.ConnectionTokens(universe_dir=universe, owner_user_id="alice",
                                    allow_local_refresh=False)
    def dispatch_for(*args):
        def dispatch(grant, verb, request, *, guard, refresh_request=None, **kwargs):
            with guard():
                result = store.current("synthetic", store._read("synthetic"),
                                       refresh_request=refresh_request)
            assert result.access_token == "new-access"
            return Script([b"refreshed stream"])
        return dispatch
    discovery.broker.server._dispatch_for = dispatch_for
    def client():
        return BrokerClient(discovery.broker.path, principal="alice", command_center="cc-alice",
            fence=lambda: (discovery.broker.state["generation"], discovery.broker.state["token"]),
            refresh_factory=lambda grant, connection: prepare(
                discovery.root, principal="alice", command_center="cc-alice",
                grant_id=grant, connection_id=connection))
    discovery.client = client
    discovery.spent = spent
    discovery.store = store
    discovery.universe = universe
    return discovery


def request(client):
    return client.request(grant_id="grant-a", connection_id="conn-a", verb="GET",
                          request={"url": "https://models.example.com/catalogue"},
                          op_id=new_op_id())


def test_concurrent_streams_rotate_once_and_reuse_durable_vault(refresh_case):
    with ThreadPoolExecutor(max_workers=4) as pool:
        answers = list(pool.map(lambda _: request(refresh_case.client()), range(4)))
    assert [a["body"] for a in answers] == ["refreshed stream"] * 4
    assert len(refresh_case.spent) == 1
    assert tokens.decode(refresh_case.store._read("synthetic")).refresh_token == "rotated"
    assert request(refresh_case.client())["status"] == 200
    assert len(refresh_case.spent) == 1


def test_vault_admission_failure_spends_nothing(refresh_case, monkeypatch):
    from tinyassets.storage.outbound_connections import ConnectionAuthorizationError
    def refuse(*args):
        raise credential_refresh.RefreshUnavailable("busy")
    monkeypatch.setattr(credential_refresh, "_hold_vault", refuse)
    with pytest.raises(ConnectionAuthorizationError):
        request(refresh_case.client())
    assert refresh_case.spent == []
    assert tokens.decode(refresh_case.store._read("synthetic")).refresh_token == "single-use"


def test_rotation_holds_admission_and_retries_write_before_ack(refresh_case, monkeypatch):
    original = credential_refresh._hold_vault
    active = []
    attempts = []
    @contextmanager
    def held(stack):
        with stack:
            active.append(True)
            try:
                yield
            finally:
                active.pop()
    def hold(*args):
        stack, write = original(*args)
        def save(*args, **kwargs):
            assert active
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError("transient storage failure")
            return write(*args, **kwargs)
        return held(stack), save
    monkeypatch.setattr(credential_refresh, "_hold_vault", hold)
    spend = tokens.refresh
    def spend_held(current):
        assert active
        return spend(current)
    monkeypatch.setattr(tokens, "refresh", spend_held)
    assert request(refresh_case.client())["status"] == 200
    assert len(attempts) == 2 and len(refresh_case.spent) == 1 and not active


@pytest.mark.parametrize("change", ["principal", "center", "destination", "fence", "revoked"])
def test_foreign_stale_and_revoked_refresh_never_spends(refresh_case, change):
    from tinyassets.storage.outbound_connections import GrantResolutionError
    args = dict(principal="alice", command_center="cc-alice", grant_id="grant-a",
                connection_id="conn-a")
    if change == "principal":
        args["principal"] = "bob"
    elif change == "center":
        args["command_center"] = "cc-bob"
    elif change == "fence":
        refresh_case.broker.state["token"] = "stale"
    elif change == "revoked":
        refresh_case.ledger.revoke_grant("grant-a")
    expected = (GrantResolutionError if change in {"principal", "center", "revoked"}
                else PermissionError)
    with pytest.raises(expected):
        callback = prepare(refresh_case.root, **args)
        callback({"op": "REFRESH", "sequence": 1, "destination": "foreign",
                  "rejected_digest": ""})
    assert not refresh_case.spent


def test_lost_ack_keeps_rotation_and_does_not_replay(refresh_case):
    callback = prepare(refresh_case.root, principal="alice", command_center="cc-alice",
                       grant_id="grant-a", connection_id="conn-a")
    generation, token = (refresh_case.broker.state[k] for k in ("generation", "token"))
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(str(refresh_case.broker.path))
        sock.sendall(rf.control(1, {"op": "OPEN", "op_id": new_op_id(),
            "generation": generation, "token": token, "principal": "alice",
            "command_center": "cc-alice", "grant_id": "grant-a", "connection_id": "conn-a",
            "verb": "GET", "request": {}, "refresh": True, "credit": 1024}))
        assert rf.read_frame_blocking(sock).control()["op"] == "ADMITTED"
        document = rf.read_frame_blocking(sock).control()
        assert document["op"] == "REFRESH"
        callback(document)
        # Intentionally discard ACK after publication, exactly as a transport
        # failure can. Fresh OPEN must use the saved rotation without spending.
    assert request(refresh_case.client())["status"] == 200
    assert len(refresh_case.spent) == 1


def test_rejected_digest_refreshes_only_that_stored_access_token(refresh_case):
    callback = prepare(refresh_case.root, principal="alice", command_center="cc-alice",
                       grant_id="grant-a", connection_id="conn-a")
    document = {"op": "REFRESH", "sequence": 1, "destination": "synthetic",
                "rejected_digest": hashlib.sha256(b"old-access").hexdigest()}
    callback(document)
    callback(document)
    assert len(refresh_case.spent) == 1


def test_broker_without_daemon_callback_cannot_refresh_locally(refresh_case):
    from tinyassets.storage.outbound_connections import ConnectionAuthorizationError
    with pytest.raises(ConnectionAuthorizationError) as caught:
        refresh_case.store.current("synthetic", refresh_case.store._read("synthetic"))
    assert caught.value.failure["provider_detail"] == "daemon refresh admission is unavailable"
    assert not refresh_case.spent


def test_async_client_rotates_without_blocking_demultiplexer(refresh_case):
    import asyncio

    from tinyassets.broker.aclient import AsyncBrokerClient

    async def run():
        client = AsyncBrokerClient(refresh_case.broker.path, principal="alice",
            command_center="cc-alice", fence=lambda: (
                refresh_case.broker.state["generation"], refresh_case.broker.state["token"]),
            refresh_factory=lambda grant, connection: prepare(
                refresh_case.root, principal="alice", command_center="cc-alice",
                grant_id=grant, connection_id=connection))
        try:
            async with client.stream(grant_id="grant-a", connection_id="conn-a", verb="GET",
                                     request={}, op_id=new_op_id()) as stream:
                assert (await stream.head())["status"] == 200
                assert b"".join([part async for part in stream.body()]) == b"refreshed stream"
        finally:
            await client.close()
    asyncio.run(run())
    assert len(refresh_case.spent) == 1
