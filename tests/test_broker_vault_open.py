"""The broker asks the daemon for one http secret and never opens the envelope."""

import json
import os
import threading

import pytest

from tests.support.broker_ipc import Script, discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets import credential_vault as vault
from tinyassets.broker.client import BrokerClient
from tinyassets.broker.ops import new_op_id
from tinyassets.broker.vault_open import prepare
from tinyassets.storage.outbound_connections import ProxyRequestError
from tinyassets.vault_envelope import is_envelope

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Unix broker transport")

SECRET = "capability-service-token"


def _client(case, opener):
    return BrokerClient(
        case.broker.path, principal="alice", command_center="cc-alice",
        fence=lambda: (case.broker.state["generation"], case.broker.state["token"]),
        vault_open_factory=lambda grant, connection: opener,
        timeout=10,
    )


def _deposit(case):
    universe = case.root / "cc-alice"
    universe.mkdir()
    vault.write_credential_vault(
        universe,
        [vault.http_credential_record(destination="synthetic", token=SECRET)],
        owner_user_id="alice",
    )
    return universe


def test_daemon_opens_only_the_grant_secret(discovery):  # noqa: F811
    universe = _deposit(discovery)
    opener = prepare(
        discovery.root, principal="alice", command_center="cc-alice",
        grant_id="grant-a", connection_id="conn-a",
    )
    assert opener({
        "op": "VAULT_OPEN", "sequence": 1, "credential_ref": "vault://http/synthetic",
    }) == SECRET
    with pytest.raises(PermissionError, match="scope mismatch"):
        opener({
            "op": "VAULT_OPEN", "sequence": 2, "credential_ref": "vault://http/other",
        })
    raw = (universe / ".credential-vault.json").read_bytes()
    assert is_envelope(json.loads(raw))
    assert SECRET.encode() not in raw


def test_broker_receives_the_secret_the_daemon_opened(discovery, monkeypatch):  # noqa: F811
    _deposit(discovery)
    calls = []
    real = vault.read_http_credential

    def wrapped(*args, **kwargs):
        calls.append(threading.get_ident())
        return real(*args, **kwargs)

    monkeypatch.setattr(vault, "read_http_credential", wrapped)
    seen = []

    def dispatch_for(*_args):
        def dispatch(grant, verb, request, *, guard, vault_open=None, **kwargs):
            del grant, verb, request, kwargs
            with guard():
                seen.append(vault_open("vault://http/synthetic"))
            return Script([b"opened"])

        return dispatch

    discovery.broker.server._dispatch_for = dispatch_for
    opener = prepare(
        discovery.root, principal="alice", command_center="cc-alice",
        grant_id="grant-a", connection_id="conn-a",
    )
    result = _client(discovery, opener).request(
        grant_id="grant-a", connection_id="conn-a", verb="GET",
        request={"url": "https://models.example.com/catalogue"}, op_id=new_op_id(),
    )
    assert seen == [SECRET]
    assert result["status"] == 200
    assert SECRET not in result["body"]
    assert calls == [threading.get_ident()]


def test_a_different_ref_returns_no_secret(discovery):  # noqa: F811
    _deposit(discovery)

    def dispatch_for(*_args):
        def dispatch(grant, verb, request, *, guard, vault_open=None, **kwargs):
            del grant, verb, request, kwargs
            with guard():
                vault_open("vault://http/other")
            return Script([b"opened"])

        return dispatch

    discovery.broker.server._dispatch_for = dispatch_for
    opener = prepare(
        discovery.root, principal="alice", command_center="cc-alice",
        grant_id="grant-a", connection_id="conn-a",
    )
    with pytest.raises(ProxyRequestError, match="outbound request failed") as caught:
        _client(discovery, opener).request(
            grant_id="grant-a", connection_id="conn-a", verb="GET",
            request={"url": "https://models.example.com/catalogue"}, op_id=new_op_id(),
        )
    assert SECRET not in str(caught.value)
