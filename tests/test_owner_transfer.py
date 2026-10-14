"""Real middleware/session/BoxProvider boundary for private transfer approvals."""
from __future__ import annotations

import json
import time

import pytest

from tests.test_owner_door import HOME_A, HOME_B, A, B, _headers, door  # noqa: F401
from tinyassets.agent_loop import served_chat
from tinyassets.boxes.local import LocalBoxProvider
from tinyassets.okf.transfer import ingest_zip
from tinyassets.onboarding import owner_sessions
from tinyassets.owner_door import transfer

URL = "/app/api/transfer"
PATH = "workspace/note.md"


@pytest.fixture
def transfer_door(door, monkeypatch):  # noqa: F811
    client, root = door
    provider = LocalBoxProvider(
        boxes_root=root / "boxes", state_dir=root / "box-state",
        owner_of={HOME_A: A, HOME_B: B}.get, allow_unisolated=True,
    )
    monkeypatch.setattr(served_chat, "_box_provider", provider)
    monkeypatch.setattr(transfer, "_PENDING", {})
    handle = provider.bind(HOME_A, account_id=A)
    provider.write(handle, "initial", "/cc/" + PATH, b"My note", max_bytes=100)
    with owner_sessions.store() as conn:
        for owner in (A, B):
            conn.execute(
                "INSERT INTO owner_sessions VALUES (?,?,?)",
                (owner_sessions.hashed(owner + "-session"), json.dumps({"user_id": owner}),
                 time.time() + 600),
            )
    client.cookies.set(owner_sessions.COOKIE, A + "-session", domain="tinyassets.io", path="/")
    yield client, provider, handle
    provider.close()


def request(client, document, owner=A, origin="https://tinyassets.io"):
    return client.post(URL, headers={**_headers(owner), "Origin": origin}, json=document)


def preview(client, paths=None, center=HOME_A, **kwargs):
    return request(client, {
        "operation": "preview", "command_center": center, "paths": paths or [PATH],
    }, **kwargs)


def download(client, token, **kwargs):
    document = {
        "operation": "export", "command_center": HOME_A, "preview": token,
        "selected": [PATH], "named_binaries": [], "included_contacts": {}, "profile": "export",
    }
    document.update(kwargs)
    return request(client, document)


def test_real_owner_session_preview_and_private_download(transfer_door):
    client, _, _ = transfer_door
    response = preview(client)
    assert response.status_code == 200, response.text
    item = response.json()["files"][0]
    assert item["content"] == "My note" and item["selected"] is False
    result = download(client, response.json()["preview"])
    assert result.status_code == 200, result.text
    assert ingest_zip(result.content).files[PATH] == b"My note"
    assert result.headers["content-type"] == "application/octet-stream"
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in result.headers["content-disposition"]


def test_bearer_cannot_preview_or_download_without_owner_cookie(transfer_door):
    client, _, _ = transfer_door
    token = preview(client).json()["preview"]
    client.cookies.clear()
    assert preview(client).status_code == 403
    assert download(client, token).status_code == 403


def test_wrong_origin_wrong_session_and_other_owner_are_denied(transfer_door):
    client, _, _ = transfer_door
    assert preview(client, origin="https://evil.example").status_code == 403
    assert preview(client, owner=B).status_code == 403
    assert preview(client, center=HOME_B).status_code == 403
    client.cookies.set(owner_sessions.COOKIE, B + "-session", domain="tinyassets.io", path="/")
    assert preview(client, owner=B).status_code == 403


def test_stale_source_and_replaced_preview_do_not_download(transfer_door):
    client, provider, handle = transfer_door
    first = preview(client).json()["preview"]
    second = preview(client).json()["preview"]
    assert download(client, first).status_code == 400
    provider.write(handle, "edit", "/cc/" + PATH, b"New note", max_bytes=100)
    assert download(client, second).status_code == 409


def test_restart_expiry_and_provider_replacement_invalidate_approval(transfer_door, monkeypatch):
    client, provider, _ = transfer_door
    token = preview(client).json()["preview"]
    with monkeypatch.context() as patch:
        patch.setattr(transfer.time, "monotonic", lambda: 10**15)
        assert download(client, token).status_code == 400
    class Replacement:
        bind = provider.bind
    token = preview(client).json()["preview"]
    with monkeypatch.context() as patch:
        patch.setattr(served_chat, "_box_provider", Replacement())
        assert download(client, token).status_code == 400
    token = preview(client).json()["preview"]
    monkeypatch.setattr(transfer, "_PENDING", {})
    assert download(client, token).status_code == 400


def test_unconfigured_provider_has_no_host_fallback(transfer_door, monkeypatch):
    client, _, _ = transfer_door
    monkeypatch.setattr(served_chat, "_box_provider", None)
    response = preview(client)
    assert response.status_code == 503
    assert response.json() == {"error": "box_provider_unavailable"}


def test_preview_bytes_cannot_override_blocked_credentials(transfer_door):
    client, provider, handle = transfer_door
    provider.write(handle, "secret", "/cc/" + PATH,
                   b"Bearer abcdef1234567890qwerty0987654321", max_bytes=100)
    view = preview(client).json()
    assert view["files"][0]["blocked"] == "detected credential"
    assert download(client, view["preview"]).status_code == 400
    assert download(client, view["preview"], files={PATH: "safe"}).status_code == 400


def test_contact_approval_is_per_occurrence_and_private_only(transfer_door):
    client, provider, handle = transfer_door
    provider.write(handle, "contacts", "/cc/" + PATH,
                   b"a@example.com and b@example.com", max_bytes=100)
    view = preview(client).json()
    assert [c["index"] for c in view["files"][0]["contacts"]] == [0, 1]
    result = download(client, view["preview"], included_contacts={PATH: [1]})
    assert ingest_zip(result.content).files[PATH] == b"[contact removed] and b@example.com"
    assert download(client, view["preview"], included_contacts={PATH: [2]}).status_code == 400
    assert download(client, view["preview"], included_contacts={PATH: [True]}).status_code == 400


def test_preview_capacity_is_explicit_and_retains_previous_preview(transfer_door, monkeypatch):
    client, _, _ = transfer_door
    token = preview(client).json()["preview"]
    monkeypatch.setattr(transfer, "_CAPACITY", 1)
    assert preview(client).status_code == 503
    assert download(client, token).status_code == 200
