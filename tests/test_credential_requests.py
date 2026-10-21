"""Credential issuance uses real protected stores and fake, local owner sessions."""
import asyncio
import json

import pytest

from tests.test_inline_owner_sessions import request
from tests.test_inline_owner_sessions import setup as owner_setup  # noqa: F401
from tests.test_pending_requests import _login, _logout, _make_universe
from tinyassets.api import pending_requests
from tinyassets.api_keys import ApiKeyStore
from tinyassets.onboarding.api_keys import handle
from tinyassets.outside_authority import OutsideClientAuthority


def action(center="home"):
    return {"type": "credential_request", "provider": "tinyassets", "name": "Muse",
            "scopes": [{"universe": center, "agent": "main", "capability": c}
                       for c in ("read_graph", "converse", "run_graph")]}


@pytest.fixture
def context(owner_setup, tmp_path, monkeypatch):  # noqa: F811
    from tinyassets import onboarding
    from tinyassets.daemon_server import set_founder_home

    home = _make_universe(tmp_path, "home", admin="alice")
    set_founder_home(tmp_path, founder_sub="alice", universe_id="home")
    _login("alice")
    monkeypatch.setattr(pending_requests, "_notify_owner", lambda *a: None)
    row = pending_requests.request_from_user(universe_id="home", payload={"action": action()})
    assert "request_id" in row, row
    keys, authority = ApiKeyStore(tmp_path), OutsideClientAuthority(tmp_path)
    monkeypatch.setattr("tinyassets.api_keys.current_store", lambda: keys)
    monkeypatch.setattr("tinyassets.outside_authority.current_store", lambda: authority)
    document = {}

    async def read(*args, **kwargs):
        return document

    monkeypatch.setattr(onboarding, "_read_small_json", read)

    def call(operation, *, cookie="protected", origin="https://tinyassets.io", **extra):
        document.clear()
        if operation.startswith("owner_"):
            document.update(operation=operation, **extra)
        else:
            document.update(operation="request_" + operation, universe_id="home",
                            request_id=row["request_id"], **extra)
        if operation == "create":
            document.setdefault("expected_digest", row["action"]["snapshot_digest"])
        result = asyncio.run(handle(request(cookie=cookie, origin=origin)))
        return result.status_code, json.loads(result.body)

    yield home, row, keys, authority, call
    _logout()














