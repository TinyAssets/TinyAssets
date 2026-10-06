"""D7a memory identity and owner-door isolation."""
import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from tinyassets import memory_items, onboarding


@pytest.fixture
def universe(tmp_path):
    root = tmp_path / "u-owner"
    root.mkdir()
    return root


def test_legacy_ids_are_stable_and_only_persist_on_write(universe):
    path = universe / "MEMORY.md"
    original = "# Memory\n\nKeep this introduction.\n- Likes tea\n- Likes tea\n"
    path.write_text(original, encoding="utf-8")
    items = memory_items.list_items(universe)
    assert memory_items.list_items(universe) == items
    assert path.read_text(encoding="utf-8") == original
    assert len({item["id"] for item in items}) == 2
    added = memory_items.set_item(universe, None, "Likes quiet mornings")
    assert re.fullmatch(r"m_[0-9a-f]{4,8}", added["id"])
    assert memory_items.list_items(universe) == items + [added]
    saved = path.read_text(encoding="utf-8")
    assert "Keep this introduction." in saved
    assert all(f"- [{item['id']}]" in saved for item in items)


def test_edit_keeps_id_and_delete_removes_only_target(universe):
    first = memory_items.set_item(universe, None, "First")
    second = memory_items.set_item(universe, None, "Second")
    edited = memory_items.set_item(universe, first["id"], "Changed")
    assert edited == {"id": first["id"], "text": "Changed"}
    memory_items.delete_item(universe, second["id"])
    assert memory_items.list_items(universe) == [edited]


@pytest.mark.parametrize("text", ["", " \t", "two\nlines", "nul\x00", None])
def test_invalid_text_does_not_change_memory(universe, text):
    with pytest.raises(ValueError):
        memory_items.set_item(universe, None, text)
    assert not (universe / "MEMORY.md").exists()


def test_unknown_id_and_duplicate_ids_refuse_without_rewriting(universe):
    memory_items.set_item(universe, None, "Keep")
    before = (universe / "MEMORY.md").read_bytes()
    with pytest.raises(ValueError, match="unknown"):
        memory_items.delete_item(universe, "m_ffff")
    assert (universe / "MEMORY.md").read_bytes() == before
    (universe / "MEMORY.md").write_text("- [m_abcd] one\n- [m_abcd] two\n")
    with pytest.raises(ValueError, match="duplicate"):
        memory_items.list_items(universe)


class Request:
    def __init__(self, method="GET", body=None, *, origin="https://tinyassets.io", query=None):
        self.method = method
        self.query_params = query or {}
        self.body = json.dumps(body or {}).encode()
        self.headers = {"content-type": "application/json", "origin": origin,
                        "host": "tinyassets.io", "content-length": str(len(self.body))}

    async def stream(self):
        yield self.body


def test_owner_door_and_cross_user_refusal(monkeypatch, universe):
    from tinyassets.api import helpers
    from tinyassets.auth import middleware
    from tinyassets.onboarding import owner_sessions

    monkeypatch.setattr(owner_sessions, "lookup", lambda cookie: {
        "identity_json": json.dumps({"user_id": "owner"})})

    monkeypatch.setattr(helpers, "_base_path", lambda: universe.parent)
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://tinyassets.io"})
    monkeypatch.setattr(middleware, "current_identity", lambda: SimpleNamespace(user_id="owner"))
    monkeypatch.setattr(onboarding, "_read_home",
                        lambda identity: "u-owner" if identity.user_id == "owner" else "")

    def call(*args, **kwargs):
        response = asyncio.run(onboarding._handle_memory(Request(*args, **kwargs)))
        assert response.headers["cache-control"] == "no-store"
        return response.status_code, json.loads(response.body)

    assert call()[1]["items"] == []
    status, saved = call("POST", {"text": "Tea"})
    assert status == 200 and saved["universe_id"] == "u-owner"
    item_id = saved["items"][0]["id"]
    assert call("POST", {"id": item_id, "text": "Coffee"})[0] == 200
    status, deleted = call("POST", {"delete": item_id})
    assert status == 200 and deleted["items"] == []
    status, restored = call("POST", {"undo": deleted["history"][0]["id"]})
    assert status == 200 and restored["items"] == [{"id": item_id, "text": "Coffee"}]
    assert call("POST", {"undo": saved["history"][0]["id"]})[0] == 409
    assert call("POST", {"text": "Bad", "universe_id": "u-other"})[0] == 403
    assert call(query={"universe": "u-other"})[0] == 403
    assert call("POST", {"text": "Bad"}, origin="https://evil.example")[0] == 403
    for bad in (True, -1, 2**64, "1"):
        assert call("POST", {"undo": bad})[0] == 400
    assert memory_items.list_items(universe) == restored["items"]
    monkeypatch.setattr(middleware, "current_identity", lambda: SimpleNamespace(user_id="stranger"))
    assert call()[0] == 404
    assert call("POST", {"text": "Bad", "universe_id": "u-owner"})[0] == 404


def test_route_keeps_authentication_and_dark_flag(monkeypatch):
    from starlette.responses import JSONResponse

    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: False)
    assert asyncio.run(onboarding._handle_memory(Request())).status_code == 404
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required",
                        lambda: JSONResponse({"error": "sign_in"}, status_code=401))
    assert asyncio.run(onboarding._handle_memory(Request())).status_code == 401
