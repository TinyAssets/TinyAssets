"""Refresh runs entirely inside the broker with durable single-flight rotation."""
# ruff: noqa: F811 -- imported pytest fixtures
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace

import pytest

from tests.support.broker_ipc import discovery  # noqa: F401
from tests.test_broker_server import Script, broker  # noqa: F401
from tinyassets.broker import credential_refresh, vault
from tinyassets.broker.client import BrokerClient
from tinyassets.broker.ops import new_op_id
from tinyassets.connection_oauth import tokens
from tinyassets.credential_vault import http_credential_record

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Unix broker transport")


@pytest.fixture
def refresh_case(discovery, monkeypatch):
    with discovery.ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET auth_scheme='oauth2'")
    universe = discovery.root / "cc-alice"
    universe.mkdir()
    initial = tokens.TokenBundle("old-access", "https://issuer.example/token", "client",
                                 refresh_token="single-use", expires_at=1)
    vault.write_credential_vault(universe, [http_credential_record(
        destination="synthetic", token=tokens.encode(initial))], owner_user_id="alice")
    spent = []
    def spend(current):
        assert current.refresh_token == "single-use", "token spent twice"
        spent.append(current)
        return replace(current, access_token="new-access", refresh_token="rotated",
                       expires_at=time.time() + 3600)
    monkeypatch.setattr(tokens, "refresh", spend)
    from tinyassets.broker.tokens import ConnectionTokens

    store = ConnectionTokens(universe_dir=universe, owner_user_id="alice")
    def dispatch_for(*args):
        def dispatch(grant, verb, request, *, guard, **kwargs):
            with guard():
                result = store.current("synthetic", store._read("synthetic"))
            assert result.access_token == "new-access"
            return Script([b"refreshed stream"])
        return dispatch
    discovery.broker.server._dispatch_for = dispatch_for
    def client():
        return BrokerClient(discovery.broker.path, principal="alice", command_center="cc-alice",
            fence=lambda: (discovery.broker.state["generation"], discovery.broker.state["token"]))
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


def test_rotation_holds_admission_and_retries_write_before_use(refresh_case, monkeypatch):
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


def test_rejected_token_refreshes_only_that_stored_access_token(refresh_case):
    store = refresh_case.store
    store.current("synthetic", store._read("synthetic"), rejected="old-access")
    store.current("synthetic", store._read("synthetic"), rejected="old-access")
    assert len(refresh_case.spent) == 1


def test_async_client_rotates_without_blocking_demultiplexer(refresh_case):
    import asyncio

    from tinyassets.broker.aclient import AsyncBrokerClient

    async def run():
        client = AsyncBrokerClient(refresh_case.broker.path, principal="alice",
            command_center="cc-alice", fence=lambda: (
                refresh_case.broker.state["generation"], refresh_case.broker.state["token"]))
        try:
            async with client.stream(grant_id="grant-a", connection_id="conn-a", verb="GET",
                                     request={}, op_id=new_op_id()) as stream:
                assert (await stream.head())["status"] == 200
                assert b"".join([part async for part in stream.body()]) == b"refreshed stream"
        finally:
            await client.close()
    asyncio.run(run())
    assert len(refresh_case.spent) == 1
