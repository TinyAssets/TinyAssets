"""Every consent answer door requires interactive proof before any mutation."""

import asyncio
import json

import pytest
from starlette.requests import Request

from tests.owner_answer import answer_request as owner_answer
from tests.test_pending_requests import _login, _logout, _make_universe
from tinyassets.api import pending_requests as api
from tinyassets.storage import pending_requests as store

KINDS = (
    "publish", "install", "connect", "connect_http", "extend_http", "rotate_http",
    "remove_http", "grant_workspace_consent", "bind_model_access", "grant_patch_intake",
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    home = _make_universe(tmp_path, "u-owner", admin="owner")
    _login("owner")
    yield home
    _logout()


def ask(home, kind):
    # Include incomplete/legacy requests: refusal must precede validation and
    # owner Clear/Deny must still recover them without executing their actions.
    return store.create_request(home, kind="Consent", title="Owner decision", body="Review",
                                fields=[], action={"type": kind}, dedupe_key=kind)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("entry", ["api", "write_graph"])
def test_every_bearer_answer_variant_is_inert(home, kind, entry):
    from tinyassets.universe_server import write_graph

    row = ask(home, kind)
    before = store.get_request(home, row["request_id"])
    for variant in (
        {"values": {}}, {"dismiss": True}, {"decision": "declined"},
        {"item_id": "forged", "values": {}}, {"decision": "retry", "values": {}},
        {"dont_ask_again": True, "values": {}},
        {"owner_session": {"user_id": "owner"}, "values": {}},
        {"scope": "always", "approval_token": "forged", "values": {}},
    ):
        payload = {"request_id": row["request_id"], **variant}
        if entry == "api":
            result = api.answer_request(universe_id=home.name, payload=payload)
        else:
            result = json.loads(write_graph(target="connection", operation="answer_request",
                                           graph_id=home.name, payload_json=json.dumps(payload)))
        assert result["error"] == "interactive_approval_required", result
        assert "approval sheet in the app" in result["detail"]
        assert store.get_request(home, row["request_id"]) == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("choice", [{"dismiss": True}, {"decision": "declined"}])
def test_owner_clear_deny_and_unmute_recovery_do_not_enable_bearer(home, kind, choice):
    row = ask(home, kind)
    result = owner_answer(universe_id=home.name, payload={
        "request_id": row["request_id"], "dont_ask_again": True, **choice,
    })
    assert result["status"] == ("dismissed" if choice.get("dismiss") else "answered"), result
    api.unmute_request(universe_id=home.name, payload={"dedupe_key": row["dedupe_key"]})
    replacement = ask(home, kind)
    assert replacement and replacement["request_id"] != row["request_id"]
    refused = api.answer_request(universe_id=home.name, payload={
        "request_id": replacement["request_id"], "values": {},
    })
    assert refused["error"] == "interactive_approval_required"
    assert store.get_request(home, replacement["request_id"])["status"] == "pending"
    recovered = owner_answer(universe_id=home.name, payload={
        "request_id": replacement["request_id"], **choice,
    })
    assert recovered["status"] == result["status"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("proof", ["missing", "forged", "cross-origin", "other-owner"])
def test_protected_answer_rejects_invalid_proof(home, kind, proof):
    row = ask(home, kind)
    kwargs = {"cookie": "" if proof == "missing" else "forged"}
    if proof == "cross-origin":
        kwargs = {"origin": "https://evil.example"}
    if proof == "other-owner":
        from tests.owner_answer import session_cookie

        _login("outsider")
        cookie = session_cookie()
        _login("owner")
        kwargs = {"cookie": cookie}
    result = owner_answer(universe_id=home.name,
                          payload={"request_id": row["request_id"], "dismiss": True}, **kwargs)
    assert result["error"] == "interactive_approval_required"
    assert store.get_request(home, row["request_id"])["status"] == "pending"


@pytest.mark.parametrize("kind", ["publish", "install"])
def test_immutable_pin_cannot_be_hidden_by_a_plain_question(home, monkeypatch, kind):
    row = ask(home, "answer")
    monkeypatch.setattr(api, "_consent_pin", lambda *_: {"record": {"action": {"type": kind}}})
    result = api.answer_request(universe_id=home.name,
                                payload={"request_id": row["request_id"], "values": {}})
    assert result["error"] == "interactive_approval_required"
    assert store.get_request(home, row["request_id"])["status"] == "pending"


def test_plain_questions_keep_bearer_answers(home):
    row = api.request_from_user(universe_id=home.name, payload={
        "kind": "Question", "title": "Which?", "body": "Choose",
        "fields": [{"name": "choice", "type": "text", "label": "Choice"}],
        "action": {"type": "answer"},
    })
    assert api.answer_request(universe_id=home.name, payload={
        "request_id": row["request_id"], "values": {},
    })["status"] == "answered"


def test_oauth_token_answer_requires_owner_proof_before_deposit(home):
    row = ask(home, "connect")
    result = api.answer_connect_with_token(universe_id=home.name,
                                           request_id=row["request_id"], token="agent-token")
    assert result["error"] == "interactive_approval_required"
    assert store.get_request(home, row["request_id"])["status"] == "pending"


@pytest.mark.parametrize("door", ["oauth_exchange", "rules", "preview", "decide", "edit"])
def test_bearer_cannot_mint_retry_or_write_authority(home, monkeypatch, door):
    from tinyassets import onboarding
    from tinyassets.onboarding.inline_requests import handle_approval
    from tinyassets.onboarding.model_connect import handle_model_connect

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://tinyassets.io/mcp"})
    body = json.dumps({"owner_session": {"user_id": "owner"}, "scope": "always"}).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request({"type": "http", "method": "POST", "path": "/app/" + door,
                       "path_params": {"operation": door}, "headers": [
                           (b"origin", b"https://tinyassets.io"),
                           (b"content-type", b"application/json"),
                       ]}, receive)
    handler = (handle_model_connect if door == "oauth_exchange" else
               onboarding._handle_rules if door == "rules" else handle_approval)
    response = asyncio.run(handler(request))
    assert response.status_code == 403
    assert json.loads(response.body)["error"] == "interactive_approval_required"
