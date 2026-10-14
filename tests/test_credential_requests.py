"""Credential issuance uses real protected stores and fake, local owner sessions."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.test_inline_owner_sessions import request
from tests.test_inline_owner_sessions import setup as owner_setup  # noqa: F401
from tests.test_pending_requests import _login, _logout, _make_universe
from tinyassets.api import pending_requests
from tinyassets.api.credential_requests import validate_action
from tinyassets.api_keys import ApiKeyStore
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.onboarding.api_keys import handle
from tinyassets.outside_authority import OutsideClientAuthority
from tinyassets.storage.pending_requests import get_request


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


def test_request_is_captured_and_create_is_owner_only(context):
    home, row, keys, authority, call = context
    assert row["title"] == "Create API key for Muse"
    assert row["fields"] == []
    assert keys.list("alice", authority=authority) == {"keys": []}
    answer = pending_requests.answer_request(universe_id="home", payload={
        "request_id": row["request_id"], "values": {}})
    assert answer["error"] == "interactive_approval_required"
    assert call("create", cookie="")[0] == 403
    assert call("create", origin="https://evil.example")[0] == 403
    with identity_context(Identity("bob", "bob")):
        assert call("create")[0] == 403
    assert get_request(home, row["request_id"])["status"] == "pending"
    assert call("preview")[1]["existing"] is None


def test_secret_once_duplicate_retry_revoke_and_receipt(context):
    home, row, keys, authority, call = context
    status, made = call("create")
    assert status == 200 and "key" in made
    assert call("create")[1] == {"already_created": True, "key_id": made["key_id"]}
    preview = call("preview")[1]
    assert preview["existing"]["key_id"] == made["key_id"]
    assert made["key"] not in json.dumps(preview)
    assert made["key"] not in json.dumps(get_request(home, row["request_id"]))
    assert len(keys.list("alice", authority=authority)["keys"]) == 1
    assert call("revoke")[1]["revoked"] is True
    assert keys.resolve(made["key"]) is None
    assert call("create")[1]["already_created"] is True
    assert call("finish")[1]["status"] == "revoked"
    resolved = get_request(home, row["request_id"])
    assert resolved["answer"]["key_id"] == made["key_id"]
    assert made["key"] not in json.dumps(resolved)
    assert call("create")[0] == 403


def test_cancel_and_foreign_center_never_create(context):
    home, row, keys, authority, call = context
    bad = pending_requests.request_from_user(universe_id="home",
                                             payload={"action": action("other")})
    assert "error" in bad
    result = pending_requests._answer_request(universe_id="home", owner_session={"owner": "alice"},
                                              payload={"request_id": row["request_id"],
                                                       "dismiss": True})
    assert "error" not in result
    assert call("create")[0] == 403
    assert keys.list("alice", authority=authority) == {"keys": []}


def test_tampered_request_or_stale_preview_refuses_creation(context):
    home, row, keys, authority, call = context
    from tinyassets.storage.pending_requests import _db

    forged = action()
    forged["name"] = "Different agent"
    assert call("create", expected_digest="stale")[0] == 403
    with _db(home) as conn:
        conn.execute("UPDATE pending_requests SET action_json=? WHERE request_id=?",
                     (json.dumps(forged), row["request_id"]))
    assert call("preview")[0] == 403
    assert call("create")[0] == 403


def test_parallel_mint_and_restart_cannot_duplicate(tmp_path):
    authority = OutsideClientAuthority(tmp_path)

    def mint(_):
        return ApiKeyStore(tmp_path).mint("alice", name="Muse", scopes=action()["scopes"],
                                         authority=authority, request_ref="fake-request")

    # Initialize both databases before racing only the write transactions.
    authority.inspect("alice")
    ApiKeyStore(tmp_path).list("alice", authority=authority)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(mint, range(4)))
    assert sum("key" in result for result in results) == 1
    assert len({result["key_id"] for result in results}) == 1
    assert mint(5)["already_created"] is True


def test_decline_and_owner_list_revoke(context):
    home, row, keys, authority, call = context
    assert call("decline")[1] == {"status": "declined"}
    assert call("create")[0] == 403
    assert call("owner_list")[1] == {"keys": []}
    made = keys.mint("alice", name="Test fixture", scopes=action()["scopes"], authority=authority)
    with identity_context(Identity("bob", "bob")):
        assert call("owner_list")[0] == 403
        assert call("owner_revoke", key_id=made["key_id"])[0] == 403
    assert call("owner_list")[1]["keys"][0]["name"] == "Test fixture"
    assert call("owner_revoke", key_id=made["key_id"])[1]["revoked"] is True
    assert keys.resolve(made["key"]) is None


@pytest.mark.parametrize("change", [
    {"provider": "third-party"}, {"scopes": []}, {"name": ""},
    {"scopes": [{"universe": "home", "agent": "*", "capability": "read_graph"}]},
    {"scopes": [{"universe": "home", "agent": "main", "capability": "write_graph"}]},
    {"secret": "fake-never-store"},
])
def test_invalid_provider_or_grant_is_refused(change):
    with pytest.raises(ValueError):
        validate_action({**action(), **change})
