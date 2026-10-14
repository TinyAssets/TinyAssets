"""Protected import confirmation, actual box bytes and durable package recovery."""

from __future__ import annotations

import base64
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.test_okf_transfer import archive
from tests.test_owner_transfer import HOME_A, HOME_B, URL, A, B, door, request, transfer_door  # noqa: F401
from tinyassets import command_center_packages as packages
from tinyassets.api.helpers import _base_path
from tinyassets.boxes.provider import WriteMode
from tinyassets.okf.import_activation import activate_import, preview_import
from tinyassets.onboarding import owner_sessions


def retain(client, files=None):
    response = request(
        client,
        {
            "operation": "import",
            "command_center": HOME_A,
            "archive": base64.b64encode(
                archive(
                    files
                    or {
                        "workspace/imported.md": b"Private imported note",
                        "run/agent.py": b'raise RuntimeError("never run")',
                    }
                )
            ).decode(),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["import_id"]


def review(client, import_id):
    response = request(
        client, {"operation": "import-preview", "command_center": HOME_A, "import_id": import_id}
    )
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, token):
    return request(
        client,
        {
            "operation": "import-activate",
            "command_center": HOME_A,
            "request": token,
            "named_binaries": [],
        },
    )


def test_review_then_copy_is_inert_private_and_idempotent(transfer_door):  # noqa: F811
    client, provider, handle = transfer_door
    imported = retain(client)
    plan = review(client, imported)
    assert plan["state"] == "awaiting_confirmation" and not plan["runs_code"]
    assert any(row["content"] == "Private imported note" for row in plan["files"])
    assert provider.stat(handle, "/cc/" + plan["destination"]) is None
    before = provider.read(handle, "/cc/workspace/note.md", max_bytes=100).data
    first = confirm(client, plan["request"])
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "copied" and not first.json()["runs_code"]
    for row in plan["files"]:
        assert (
            provider.read(handle, "/cc/" + row["to"], max_bytes=10000).data.decode()
            == row["content"]
        )
    assert provider.stat(handle, "/cc/run/agent.py") is None
    assert provider.read(handle, "/cc/workspace/note.md", max_bytes=100).data == before
    generation = provider.committed_generation(handle)
    assert confirm(client, plan["request"]).json() == first.json()
    assert provider.committed_generation(handle) == generation
    peer = provider.bind(HOME_B, account_id=B)
    assert provider.stat(peer, "/cc/" + plan["destination"]) is None


def test_foreign_owner_wrong_origin_and_epoch_cannot_confirm(transfer_door):  # noqa: F811
    client, provider, handle = transfer_door
    imported = retain(client)
    plan = review(client, imported)
    body = {
        "operation": "import-activate",
        "command_center": HOME_A,
        "request": plan["request"],
        "named_binaries": [],
    }
    assert request(client, body, origin="https://foreign.example").status_code == 403
    client.cookies.set(owner_sessions.COOKIE, B + "-session", domain="tinyassets.io", path="/")
    assert request(client, {**body, "command_center": HOME_B}, owner=B).status_code == 400
    client.cookies.set(owner_sessions.COOKIE, A + "-session", domain="tinyassets.io", path="/")
    provider.destroy(handle, "destroy-import-placement")
    assert confirm(client, plan["request"]).status_code == 400


def test_changed_destination_refuses_without_overwriting(transfer_door):  # noqa: F811
    client, provider, handle = transfer_door
    plan = review(client, retain(client))
    target = plan["files"][0]["to"]
    provider.write(handle, "competing-file", "/cc/" + target, b"Owner content", max_bytes=100)
    assert confirm(client, plan["request"]).status_code == 409
    assert provider.read(handle, "/cc/" + target, max_bytes=100).data == b"Owner content"


def test_lost_write_response_replays_same_operation_without_overwrite(transfer_door, monkeypatch):  # noqa: F811
    client, provider, handle = transfer_door
    plan = review(client, retain(client))
    original = provider.write
    operations = []

    def lost(bound, operation, *args, **kwargs):
        assert kwargs["mode"] is WriteMode.CREATE
        operations.append(operation)
        result = original(bound, operation, *args, **kwargs)
        if len(operations) == 1:
            raise OSError("response lost after write committed")
        return result

    monkeypatch.setattr(provider, "write", lost)
    first = confirm(client, plan["request"])
    assert first.status_code == 503
    pin = packages.pin_for_request(_base_path(), universe_id=HOME_A, request_id=plan["request"])
    assert pin["state"] == "activating" and pin["progress"]["attempting"]
    retry = confirm(client, plan["request"])
    assert retry.status_code == 200, retry.text
    assert operations[0] == operations[1]
    assert len(retry.json()["completed"]) == len(plan["files"])


def test_concurrent_confirmation_cannot_pass_live_claim(transfer_door, monkeypatch):  # noqa: F811
    client, provider, handle = transfer_door
    imported = retain(client)
    plan = preview_import(_base_path(), provider, handle, imported)
    entered, release = threading.Event(), threading.Event()
    original = provider.write

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(provider, "write", blocked)
    base = _base_path()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(activate_import, base, provider, handle, plan["request"])
        assert entered.wait(10)
        try:
            with pytest.raises(packages.PackageError, match="already being installed"):
                activate_import(base, provider, handle, plan["request"])
        finally:
            release.set()
        assert first.result(timeout=10)["state"] == "copied"


def test_changed_completed_bytes_cannot_be_reported_as_success(transfer_door, monkeypatch):  # noqa: F811
    client, provider, handle = transfer_door
    plan = review(client, retain(client))
    original = provider.write

    def changed(bound, operation, path, data, **kwargs):
        result = original(bound, operation, path, data, **kwargs)
        if path.endswith("workspace/imported.md"):
            original(bound, "owner-edit", path, b"Owner edit", max_bytes=100)
        return result

    monkeypatch.setattr(provider, "write", changed)
    response = confirm(client, plan["request"])
    assert response.status_code == 409, response.text
    pin = packages.pin_for_request(_base_path(), universe_id=HOME_A, request_id=plan["request"])
    assert pin["state"] == "activating"


@pytest.mark.parametrize("data", [b"\xff\x00", b"\x00\x01image-data"])
def test_binary_copy_requires_named_confirmation(transfer_door, data):  # noqa: F811
    client, provider, handle = transfer_door
    plan = review(client, retain(client, {"workspace/photo.bin": data}))
    assert any(row["binary"] and row["content"] is None for row in plan["files"])
    assert confirm(client, plan["request"]).status_code == 400
    response = request(
        client,
        {
            "operation": "import-activate",
            "command_center": HOME_A,
            "request": plan["request"],
            "named_binaries": ["workspace/photo.bin"],
        },
    )
    assert response.status_code == 200, response.text
    assert (
        provider.read(
            handle, "/cc/" + plan["destination"] + "/workspace/photo.bin", max_bytes=100
        ).data
        == data
    )


def test_tombstone_blocks_unstarted_copy_and_repeated_preview_reuses_pin(transfer_door):  # noqa: F811
    from tinyassets.account_deletion import write_tombstone
    from tinyassets.storage.current_home import CurrentHomeChanged

    client, provider, handle = transfer_door
    imported = retain(client)
    plan = review(client, imported)
    assert review(client, imported)["request"] == plan["request"]
    write_tombstone(_base_path(), A)
    with pytest.raises(CurrentHomeChanged):
        activate_import(_base_path(), provider, handle, plan["request"])
    assert provider.stat(handle, "/cc/" + plan["destination"]) is None
