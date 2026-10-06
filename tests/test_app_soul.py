"""Personal files stay behind the owner boundary and preserve exact bytes."""
import asyncio
import json

import pytest

from tests.test_memory_items import Request
from tinyassets import harness_history, onboarding
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.onboarding import owner_sessions
from tinyassets.onboarding.soul import handle_soul


@pytest.fixture
def app(tmp_path, monkeypatch):
    from tinyassets.api import helpers

    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "app_config", lambda: {
        "resource": "https://tinyassets.io", "issuer": "https://issuer.example"})
    monkeypatch.setattr(onboarding, "_read_home", lambda identity: "u-" + identity.user_id)
    for owner in ("alice", "bob"):
        (tmp_path / ("u-" + owner)).mkdir()
    monkeypatch.setattr(owner_sessions, "lookup", lambda cookie: {
        "identity_json": json.dumps({"user_id": cookie})} if cookie else None)

    def call(method="GET", data=None, *, actor="alice", cookie="alice", query=None,
             origin="https://tinyassets.io", handler=handle_soul):
        request = Request(method, data, origin=origin, query=query)
        request.cookies = {owner_sessions.COOKIE: cookie}
        with identity_context(Identity(user_id=actor, username=actor)):
            result = asyncio.run(handler(request))
        return result.status_code, json.loads(result.body)

    return tmp_path, call


def test_save_reload_conflict_and_undo(app):
    root, call = app
    before = b"# Soul\r\nMy style\r\n"
    (root / "u-alice" / "soul.md").write_bytes(before)
    status, doc = call()
    assert status == 200
    soul = doc["documents"][0]
    assert soul["text"].encode() == before
    assert call("POST", {**soul, "text": "# Soul\nNew style"})[0] == 200
    assert call()[1]["documents"][0]["text"] == "# Soul\nNew style"
    assert call("POST", {**soul, "text": "stale"})[0] == 409
    change = harness_history.list_history(root / "u-alice")[0]
    harness_history.undo(root / "u-alice", change["id"])
    assert (root / "u-alice" / "soul.md").read_bytes() == before
    assert not (root / "u-bob" / "soul.md").exists()


@pytest.mark.parametrize("handler,data", [
    (handle_soul, {"path": "soul.md", "text": "bad", "revision": "absent"}),
    (onboarding._handle_memory, {"text": "bad"}),
])
@pytest.mark.parametrize("overrides", [
    {"cookie": ""}, {"cookie": "bob"}, {"origin": "https://evil.example"},
    {"query": {"universe_id": "u-bob"}},
])
def test_owner_proof_matrix_refuses_without_mutation(app, handler, data, overrides):
    root, call = app
    assert call("POST", data, handler=handler, **overrides)[0] == 403
    assert list((root / "u-alice").iterdir()) == []
    assert list((root / "u-bob").iterdir()) == []


def test_foreign_reads_and_body_selectors_are_refused(app):
    root, call = app
    (root / "u-bob" / "MEMORY.md").write_text("Bob private fact")
    assert call(query={"universe": "u-bob"})[0] == 403
    assert call()[1]["documents"][2]["text"] == ""
    assert call("POST", {"universe_id": "u-bob"})[0] == 403
    assert call("POST", {"path": "../u-bob/MEMORY.md", "text": "bad"})[0] == 400
    assert (root / "u-bob" / "MEMORY.md").read_text() == "Bob private fact"


def test_linked_soul_refuses_and_agent_cannot_write_soul(app):
    root, call = app
    secret = root / "u-bob" / "soul.md"
    secret.write_text("Bob private soul")
    (root / "u-alice" / "soul.md").symlink_to(secret)
    assert call()[0] == 409
    assert call("POST", {"path": "soul.md", "text": "bad", "revision": "absent"})[0] == 409
    assert secret.read_text() == "Bob private soul"
    with pytest.raises(ValueError, match="requires the owner"):
        harness_history.write_file(root / "u-bob", "soul.md", "bad", who="agent")


def test_unauthenticated_soul_is_refused(monkeypatch):
    from tinyassets.auth import middleware

    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(middleware, "current_identity_or_none", lambda: None)
    assert asyncio.run(handle_soul(Request())).status_code == 401


def test_full_memory_larger_than_default_json_limit_and_empty_save(app):
    root, call = app
    text = "- A remembered fact\n" * 500
    data = {"path": "MEMORY.md", "text": text, "revision": "absent"}
    status, saved = call("POST", data)
    assert status == 200
    memory = saved["documents"][2]
    assert memory["text"] == text
    assert call("POST", {**memory, "text": ""})[0] == 200
    assert (root / "u-alice" / "MEMORY.md").read_bytes() == b""


def test_save_receipt_cannot_adopt_a_concurrent_agents_revision(app, monkeypatch):
    root, call = app
    original_write = harness_history._write

    def agent_writes_after_owner(conn, universe, path, content, who):
        result = original_write(conn, universe, path, content, who)
        (universe / path).write_bytes(b"Agent's newer memory")
        return result

    monkeypatch.setattr(harness_history, "_write", agent_writes_after_owner)
    status, response = call("POST", {
        "path": "MEMORY.md", "text": "Owner's draft", "revision": "absent"})
    assert status == 200
    assert response["documents"][2]["text"] == "Agent's newer memory"
    assert response["saved"] == {
        "path": "MEMORY.md", "revision": harness_history.digest(b"Owner's draft")}
    monkeypatch.setattr(harness_history, "_write", original_write)
    assert call("POST", {**response["saved"], "text": "Owner's draft again"})[0] == 409
    assert (root / "u-alice" / "MEMORY.md").read_bytes() == b"Agent's newer memory"
