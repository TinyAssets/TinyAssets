"""Credential issuance uses real protected stores and fake, local owner sessions."""
import asyncio
import json

import pytest

from tests.support.outside_grants import LocalBroker
from tests.test_inline_owner_sessions import request
from tests.test_inline_owner_sessions import setup as owner_setup  # noqa: F401
from tests.test_pending_requests import _login, _logout, _make_universe
from tinyassets.api import pending_requests
from tinyassets.api_keys import ApiKeyStore, _client
from tinyassets.onboarding.api_keys import handle, mint


def action(center="home"):
    return {"type": "credential_request", "provider": "tinyassets", "name": "Muse",
            "scopes": [{"universe": center, "agent": "main", "capability": c}
                       for c in ("read_graph", "converse", "run_graph")]}


@pytest.fixture
def context(owner_setup, tmp_path, monkeypatch):  # noqa: F811
    from tinyassets import onboarding, router_credentials
    from tinyassets.daemon_server import set_founder_home

    home = _make_universe(tmp_path, "home", admin="alice")
    set_founder_home(tmp_path, founder_sub="alice", universe_id="home")
    _login("alice")
    monkeypatch.setattr(pending_requests, "_notify_owner", lambda *a: None)
    row = pending_requests.request_from_user(universe_id="home", payload={"action": action()})
    assert "request_id" in row, row
    keys, broker = ApiKeyStore(tmp_path), LocalBroker(tmp_path, monkeypatch)
    monkeypatch.setattr("tinyassets.api_keys.current_store", lambda: keys)
    # The router's own mint, without the routing projection this rig has no need of.
    monkeypatch.setattr(router_credentials, "mint_key", lambda root, owner, **kw: (
        ApiKeyStore(root).mint(owner, **kw)))
    document = {}

    async def read(*args, **kwargs):
        return document

    monkeypatch.setattr(onboarding, "_read_small_json", read)

    def call(operation, *, cookie="protected", origin="https://tinyassets.io", **extra):
        document.clear()
        if operation == "mint":
            document.update(extra)
            result = asyncio.run(mint(request(cookie=cookie, origin=origin)))
            return result.status_code, json.loads(result.body)
        if operation.startswith("owner_"):
            document.update(operation=operation, **extra)
        else:
            document.update(operation="request_" + operation, universe_id="home",
                            request_id=row["request_id"], **extra)
        if operation == "create":
            document.setdefault("expected_digest", row["action"]["snapshot_digest"])
        result = asyncio.run(handle(request(cookie=cookie, origin=origin)))
        return result.status_code, json.loads(result.body)

    yield home, row, keys, broker, call
    _logout()


@pytest.mark.verifies("owner-daemon-in-box:Outside-client grants are owner policy in the broker")
def test_an_agents_key_request_is_minted_only_as_the_owner_was_shown(context):
    _, _, keys, broker, call = context
    status, preview = call("preview")
    assert status == 200 and preview["existing"] is None
    status, held = call("create")
    assert status == 200 and set(held) == {"request_id", "digest"}
    assert keys.list("alice") == {"keys": []}  # the box holds a request, never a key
    assert call("mint", request_id=held["request_id"], digest="sha256:" + "0" * 64)[0] == 403
    assert call("mint", request_id=held["request_id"], digest=held["digest"],
                cookie="")[0] == 403
    assert keys.list("alice") == {"keys": []}
    status, key = call("mint", request_id=held["request_id"], digest=held["digest"])
    assert status == 200 and key["key"].startswith("ta_key_")
    [grant] = broker.store.outside_list("alice")
    assert (grant["client"], grant["mode"], grant["generation"]) == (
        _client(key["key_id"]), "scoped", 1)
    assert grant["scopes"] == preview["action"]["scopes"]
    # The held request applies once: a replay mints nothing and changes no grant.
    assert call("mint", request_id=held["request_id"], digest=held["digest"])[0] == 403
    assert [k["key_id"] for k in keys.list("alice")["keys"]] == [key["key_id"]]
    assert broker.store.outside_list("alice")[0]["generation"] == 1
    status, receipt = call("finish")
    assert status == 200 and receipt == {"key_id": key["key_id"], "provider": "tinyassets",
                                         "status": "created"}
