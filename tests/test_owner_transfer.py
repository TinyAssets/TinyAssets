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


def test_deleted_owner_previews_are_purged_and_cannot_be_repopulated(transfer_door, monkeypatch):
    from tinyassets.account_deletion import write_tombstone
    from tinyassets.api.helpers import _base_path

    client, _, _ = transfer_door
    token = preview(client).json()["preview"]
    assert token in transfer._PENDING
    write_tombstone(_base_path(), A)
    transfer.clear_owner_previews(A)
    assert token not in transfer._PENDING
    assert download(client, token).status_code == 403
    assert preview(client).status_code == 403


def test_agent_rules_are_previewed_bound_and_included_with_selected_agent(
        transfer_door, monkeypatch):
    from tests.test_okf_policy_export import projection, rule

    client, provider, handle = transfer_door
    provider.write(handle, "agent", "/cc/AGENTS.md", b"Help the owner", max_bytes=100)
    policy = projection(owner=A, entries=[rule("hand_off", scope=HOME_A)])
    monkeypatch.setattr(transfer, "_policy", lambda bound: policy)
    view = preview(client, paths=["AGENTS.md", PATH]).json()
    assert view["rules"][0]["path"] == "agents/main/rules.json"
    assert json.loads(view["rules"][0]["content"])["behaviours"]["workspace.files"] == "hand_off"
    result = download(client, view["preview"], selected=["AGENTS.md"])
    assert result.status_code == 200, result.text
    bundle = ingest_zip(result.content)
    assert bundle.files["agents/main/rules.json"].decode() == view["rules"][0]["content"]
    without_agent = ingest_zip(download(client, view["preview"]).content)
    assert "agents/main/rules.json" not in without_agent.files
    policy["generation"] += 1
    assert download(client, view["preview"], selected=["AGENTS.md"]).status_code == 409


def test_policy_wrong_owner_unavailable_and_colliding_file_are_refused(transfer_door, monkeypatch):
    from tests.test_okf_policy_export import projection
    from tinyassets.storage.outbound_connections import ProxyRequestError

    client, provider, handle = transfer_door
    provider.write(handle, "agent", "/cc/AGENTS.md", b"Help the owner", max_bytes=100)
    monkeypatch.setattr(transfer, "_policy", lambda bound: projection(owner=B))
    assert preview(client, paths=["AGENTS.md"]).status_code == 400
    def unavailable(bound):
        raise ProxyRequestError("unavailable")
    monkeypatch.setattr(transfer, "_policy", unavailable)
    assert preview(client, paths=["AGENTS.md"]).status_code == 503
    monkeypatch.setattr(transfer, "_policy", lambda bound: projection(owner=A))
    provider.write(handle, "fake-rules", "/cc/agents/main/rules.json", b"{}", max_bytes=100)
    paths = ["AGENTS.md", "agents/main/rules.json"]
    view = preview(client, paths=paths).json()
    assert download(client, view["preview"], selected=paths).status_code == 400


def test_rules_resolve_unselected_canonical_identity_in_the_same_snapshot(
        transfer_door, monkeypatch):
    from tests.test_okf_policy_export import projection, rule

    client, provider, handle = transfer_door
    instructions = "agents/editor/AGENTS.md"
    settings = "agents/editor/settings.yaml"
    provider.write(handle, "agent", "/cc/" + instructions, b"Edit notes", max_bytes=100)
    provider.write(handle, "identity", "/cc/" + settings,
                   b"schema_version: 1\nagent:\n  id: durable-editor\n", max_bytes=100)
    policy = projection(owner=A, entries=[rule("hand_off", scope=HOME_A, agent="durable-editor")])
    monkeypatch.setattr(transfer, "_policy", lambda bound: policy)
    view = preview(client, paths=[instructions]).json()
    assert [row["path"] for row in view["files"]] == [instructions]
    assert "durable-editor" not in json.dumps(view)
    assert json.loads(view["rules"][0]["content"])["behaviours"]["workspace.files"] == "hand_off"
    result = download(client, view["preview"], selected=[instructions])
    assert result.status_code == 200, result.text
    assert settings not in ingest_zip(result.content).files
    provider.write(handle, "changed-identity", "/cc/" + settings,
                   b"schema_version: 1\nagent:\n  id: changed-id\n", max_bytes=100)
    assert download(client, view["preview"], selected=[instructions]).status_code == 409
    provider.write(handle, "invalid-identity", "/cc/" + settings, b"[invalid]", max_bytes=100)
    assert preview(client, paths=[instructions]).status_code == 400


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


def test_import_uses_owned_package_custody_and_never_runs_code(transfer_door, tmp_path):
    import base64

    from tests.test_okf_transfer import archive
    from tinyassets import command_center_packages as packages
    from tinyassets.api.helpers import _base_path

    client, provider, handle = transfer_door
    before = provider.read_many(handle, [], max_total=0).generation
    code = f"from pathlib import Path; Path({str(tmp_path / 'executed')!r}).touch()"
    raw = archive({"run/agent.py": code.encode()})
    response = request(client, {
        "operation": "import", "command_center": HOME_A,
        "archive": base64.b64encode(raw).decode(),
    })
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["state"] == "quarantined" and receipt["import_id"].startswith("import_")
    assert not (tmp_path / "executed").exists()
    assert provider.read_many(handle, [], max_total=0).generation == before
    with packages._db(_base_path()) as conn:
        row = dict(conn.execute("SELECT * FROM bundle_imports").fetchone())
        assert (row["owner_id"], row["command_center_id"], row["epoch"]) == (
            A, HOME_A, handle.epoch,
        )
        assert conn.execute("SELECT COUNT(*) FROM pins").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM blobs WHERE author_id=?", (A,)).fetchone()[0] == 1
    blob = json.loads(packages.read_blob(_base_path(), receipt["sha256"]))
    assert base64.b64decode(blob["files"]["run/agent.py"]).decode() == code


def test_import_auth_and_archive_rejection_precede_custody(transfer_door):
    import base64

    from tinyassets import command_center_packages as packages
    from tinyassets.api.helpers import _base_path

    client, _, _ = transfer_door
    document = {
        "operation": "import", "command_center": HOME_A,
        "archive": base64.b64encode(b"not a zip").decode(),
    }
    assert request(client, document).status_code == 400
    document["archive"] = "not base64"
    assert request(client, document).status_code == 400
    client.cookies.clear()
    assert request(client, document).status_code == 403
    assert not packages.database_path(_base_path()).exists()


def test_import_quota_refusal_retains_no_blob_or_custody_record(transfer_door, monkeypatch):
    import base64

    from tests.test_okf_transfer import archive
    from tinyassets import command_center_packages as packages
    from tinyassets import storage_accounting
    from tinyassets.api.helpers import _base_path

    def refused(*args, **kwargs):
        raise storage_accounting.StorageRefused({"error": "quota test"}, account_id=A)

    client, _, _ = transfer_door
    monkeypatch.setattr(storage_accounting, "reserve", refused)
    result = request(client, {
        "operation": "import", "command_center": HOME_A,
        "archive": base64.b64encode(archive({"workspace/note": b"hello"})).decode(),
    })
    assert result.status_code == 413
    with packages._db(_base_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bundle_imports").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0] == 0


def test_import_erasure_uses_owner_even_when_centers_change(transfer_door):
    from tinyassets import command_center_packages as packages
    from tinyassets.account_deletion import _delete_satellite_rows
    from tinyassets.api.helpers import _base_path

    with packages._db(_base_path()) as conn:
        for import_id, owner, center in (("mine", A, HOME_B), ("theirs", B, HOME_A)):
            conn.execute("INSERT INTO bundle_imports VALUES (?,?,?,?,?,'quarantined',?)",
                         (import_id, owner, center, 1, "0" * 64, time.time()))
    _delete_satellite_rows(packages.database_path(_base_path()), principal=A, home=HOME_A,
                           counts={}, label="packages")
    with packages._db(_base_path()) as conn:
        assert [r[0] for r in conn.execute("SELECT import_id FROM bundle_imports")] == ["theirs"]


def test_private_import_erasure_removes_real_bytes_and_preserves_peer_custody(transfer_door):
    import base64

    from tests.test_okf_transfer import archive
    from tinyassets import command_center_packages as packages
    from tinyassets.account_deletion import _delete_satellite_rows
    from tinyassets.api.helpers import _base_path

    client, _, _ = transfer_door
    receipts = []
    for value in (b"private only", b"also owned by another account", b"published retention"):
        result = request(client, {
            "operation": "import", "command_center": HOME_A,
            "archive": base64.b64encode(archive({"workspace/note": value})).decode(),
        })
        assert result.status_code == 200, result.text
        receipts.append(result.json())
    private, shared, published = (r["sha256"] for r in receipts)
    peer_bytes = packages.read_blob(_base_path(), shared)
    packages.store_blob(_base_path(), author_id=B, blob=peer_bytes)
    packages.record_version(_base_path(), author_id=A, name="Published", version=1,
                            sha256=published)
    _delete_satellite_rows(packages.database_path(_base_path()), principal=A, home=HOME_A,
                           counts={}, label="packages")
    assert not packages._blob_path(_base_path(), private).exists()
    assert packages.read_blob(_base_path(), shared) == peer_bytes
    assert packages.read_blob(_base_path(), published)
    with packages._db(_base_path()) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM bundle_imports WHERE owner_id=?", (A,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT author_id FROM blobs WHERE blob_sha256=?", (shared,),
        ).fetchone()[0] == B
        assert conn.execute(
            "SELECT COUNT(*) FROM blobs WHERE blob_sha256=?", (private,),
        ).fetchone()[0] == 0


def test_import_erasure_is_retryable_after_physical_failure(transfer_door, monkeypatch):
    import base64

    from tests.test_okf_transfer import archive
    from tinyassets import command_center_packages as packages
    from tinyassets import universe_files
    from tinyassets.api.helpers import _base_path

    client, _, _ = transfer_door
    result = request(client, {
        "operation": "import", "command_center": HOME_A,
        "archive": base64.b64encode(archive({"workspace/note": b"private"})).decode(),
    }).json()
    real_unlink = universe_files.unlink_data_path
    with monkeypatch.context() as patch:
        def fail(path):
            raise OSError("simulated interrupted removal")
        patch.setattr(universe_files, "unlink_data_path", fail)
        with pytest.raises(OSError):
            packages.erase_private_imports(_base_path(), owner=A)
    with packages._db(_base_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bundle_imports").fetchone()[0] == 1
    assert packages.read_blob(_base_path(), result["sha256"])
    assert universe_files.unlink_data_path is real_unlink
    packages.erase_private_imports(_base_path(), owner=A)
    assert not packages._blob_path(_base_path(), result["sha256"]).exists()


def test_peer_blob_creation_racing_erasure_retains_real_bytes(transfer_door, monkeypatch):
    import base64
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from tests.test_okf_transfer import archive
    from tinyassets import command_center_packages as packages
    from tinyassets import universe_files
    from tinyassets.api.helpers import _base_path

    client, _, _ = transfer_door
    result = request(client, {
        "operation": "import", "command_center": HOME_A,
        "archive": base64.b64encode(archive({"workspace/note": b"shared after erasure"})).decode(),
    }).json()
    base = _base_path()
    raw = packages.read_blob(base, result["sha256"])
    started = threading.Event()
    real_unlink = universe_files.unlink_data_path
    futures = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        def peer():
            started.set()
            return packages.store_blob(base, author_id=B, blob=raw)
        def remove(path):
            futures.append(executor.submit(peer))
            assert started.wait(2)
            real_unlink(path)
        monkeypatch.setattr(universe_files, "unlink_data_path", remove)
        packages.erase_private_imports(base, owner=A)
        assert futures[0].result(timeout=5) == result["sha256"]
    assert packages.read_blob(base, result["sha256"]) == raw
    assert packages.blob_owned(base, B, result["sha256"])


def test_discovery_requires_protected_owner_and_lists_bound_center(transfer_door):
    client, _, _ = transfer_door
    body = {"operation": "list", "command_center": HOME_A}
    response = request(client, body)
    assert response.status_code == 200, response.text
    assert response.json()["files"] == [{"path": PATH, "size": 7}]
    assert request(client, {**body, "command_center": HOME_B}).status_code == 403
    client.cookies.clear()
    assert request(client, body).status_code == 403
