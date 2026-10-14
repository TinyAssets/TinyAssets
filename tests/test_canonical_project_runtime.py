"""Actual project request binding and refusal, without a global SQL fallback."""

import json

import pytest

from tests.support.project_snapshot import records
from tests.test_okf_canonical_bundle import bundle  # noqa: F401
from tinyassets.api import runtime_ops
from tinyassets.boxes.provider import BoxAuthError
from tinyassets.okf.bundle import InvalidBundle


def test_request_uses_authenticated_owner_and_canonical_bytes(
    bundle,  # noqa: F811
    tmp_path,
    monkeypatch,
    signed_in,  # noqa: F811
):  # noqa: F811
    source = records(bundle, tmp_path)
    signed_in("owner-a")
    monkeypatch.setattr(runtime_ops, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(
        "tinyassets.agent_loop.served_chat.configured_box_provider",
        lambda: (bundle.provider, "test"),
    )
    args = {
        "universe_id": "a",
        "project_id": "proj1",
        "key": "note",
        "value": '"Canonical"',
        "request_id": "request-1",
    }
    assert json.loads(runtime_ops._action_project_memory_set(args))["version"] == 1
    assert json.loads(runtime_ops._action_project_memory_set(args))["version"] == 1
    assert json.loads(runtime_ops._action_project_memory_get(args))["value"] == "Canonical"
    snapshot = source.snapshot()
    record = next(e for e in snapshot.entries.values() if e.metadata["table"] == "project_memory")
    path = "/cc/brain/knowledge/" + snapshot.paths[record.id]
    bundle.provider.write(
        bundle.handle,
        "owner-edit",
        path,
        __import__("dataclasses").replace(record, body='"Owner edited"').encode(),
        max_bytes=10000,
    )
    assert json.loads(runtime_ops._action_project_memory_get(args))["value"] == "Owner edited"
    assert not (tmp_path / ".project_memory.db").exists()
    signed_in("owner-b")
    with pytest.raises(BoxAuthError):
        runtime_ops._action_project_memory_get(args)


def test_unmigrated_and_unbound_namespaces_refuse(bundle, tmp_path, monkeypatch, signed_in):  # noqa: F811
    signed_in("owner-a")
    monkeypatch.setattr(runtime_ops, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(
        "tinyassets.agent_loop.served_chat.configured_box_provider",
        lambda: (bundle.provider, "test"),
    )
    args = {"universe_id": "a", "project_id": "proj1", "key": "note"}
    with pytest.raises(InvalidBundle, match="migration"):
        runtime_ops._action_project_memory_get(args)
    records(bundle, tmp_path)
    with pytest.raises(InvalidBundle, match="binding"):
        runtime_ops._action_project_memory_get({**args, "project_id": "unassigned"})
    assert "request_id" in json.loads(runtime_ops._action_project_memory_set(args))["error"]


def test_omitted_center_uses_binding_without_a_host_home_directory(
    bundle,  # noqa: F811
    tmp_path,
    monkeypatch,
    signed_in,  # noqa: F811
):  # noqa: F811
    from tinyassets.storage import _connect

    records(bundle, tmp_path)
    signed_in("owner-a")
    monkeypatch.setattr(runtime_ops, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(
        "tinyassets.agent_loop.served_chat.configured_box_provider",
        lambda: (bundle.provider, "test"),
    )
    with _connect(tmp_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS founder_home("
            "founder_sub TEXT PRIMARY KEY, universe_id TEXT)"
        )
        conn.execute(
            "INSERT INTO founder_home(founder_sub,universe_id) VALUES (?,?)", ("owner-a", "a")
        )
    assert not (tmp_path / "a").exists()
    assert (
        json.loads(
            runtime_ops._action_project_memory_get({"project_id": "proj1", "key": "missing"})
        )["found"]
        is False
    )


def test_explicit_center_cannot_skip_principal_deletion(bundle, tmp_path, monkeypatch, signed_in):  # noqa: F811
    from tinyassets.account_deletion import principal_digest
    from tinyassets.storage import _connect
    from tinyassets.storage.current_home import CurrentHomeChanged

    records(bundle, tmp_path)
    signed_in("owner-a")
    monkeypatch.setattr(runtime_ops, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(
        "tinyassets.agent_loop.served_chat.configured_box_provider",
        lambda: (bundle.provider, "test"),
    )
    with _connect(tmp_path) as conn:
        conn.execute("CREATE TABLE deleted_principals(founder_sub TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO deleted_principals VALUES (?)", (principal_digest("owner-a"),))
    with pytest.raises(CurrentHomeChanged, match="deleted"):
        runtime_ops._canonical_source({"universe_id": "a"}, "project")
    with pytest.raises(ValueError, match="string"):
        runtime_ops._canonical_source({"universe_id": None}, "project")
