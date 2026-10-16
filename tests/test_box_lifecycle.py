"""Committed owner-box admission and deletion never enter the cell path."""

import json
from types import SimpleNamespace

import pytest

from tinyassets import account_deletion
from tinyassets.boxes.lifecycle import admit_center, owner_layout
from tinyassets.boxes.provider import DestroyReceipt


def _layout(root, box_layout):
    (root / ".layout.json").write_text(json.dumps({
        "layout": 2, "state": "stable", "box_layout": box_layout,
    }))


def test_committed_admission_reserves_then_provisions_through_host(tmp_path, monkeypatch):
    _layout(tmp_path, "owner-box")
    events = []

    def broker(root, *, event, principal, center):
        events.append(("broker", root, event, principal, center))
        return 17, 300001

    class Provider:
        def bind(self, center, *, account_id, turn_id):
            events.append(("host", center, account_id, turn_id))
            return SimpleNamespace(command_center_id=center, account_id=account_id)

    monkeypatch.setattr("tinyassets.broker.owner_identities.center_admission", broker)
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: (Provider(), None))
    assert admit_center(tmp_path, principal="alice", center="cc-a") == 17
    assert events == [("broker", tmp_path, "admit", "alice", "cc-a"),
                      ("host", "cc-a", "alice", "center-admission-17")]
    assert (tmp_path / "cc-a").is_dir()
    assert not list((tmp_path / "cc-a").iterdir())


def test_committed_admission_refuses_missing_host_and_invalid_layout(tmp_path, monkeypatch):
    _layout(tmp_path, "owner-box")
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: (None, None))
    with pytest.raises(RuntimeError, match="no admitted provider"):
        admit_center(tmp_path, principal="alice", center="cc-a")
    _layout(tmp_path, "unrecognized")
    with pytest.raises(RuntimeError, match="stable, known"):
        owner_layout(tmp_path)


def test_destroy_uses_c12_stable_id_and_validates_receipt(tmp_path, monkeypatch):
    _layout(tmp_path, "owner-box")
    calls = []

    class Provider:
        def bind_for_destroy(self, center, *, account_id):
            calls.append(("bind", center, account_id))
            return object()

        def destroy(self, handle, op_id):
            calls.append(("destroy", op_id))
            return DestroyReceipt("cc-a", op_id, 3, 2)

    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: (Provider(), None))
    first = account_deletion._destroy_owner_box(tmp_path, "cc-a", principal="alice")
    second = account_deletion._destroy_owner_box(tmp_path, "cc-a", principal="alice")
    assert first == second
    assert calls[0] == ("bind", "cc-a", "alice")
    assert calls[1][0] == "destroy"
    assert calls[1][1] == calls[3][1]


def test_destroy_then_stage_residual_host_bytes(tmp_path, monkeypatch):
    (tmp_path / "cc-a").mkdir()
    (tmp_path / "cc-a" / "residual").write_text("bytes")

    class Provider:
        def bind_for_destroy(self, center, *, account_id):
            return object()

        def destroy(self, handle, op_id):
            return DestroyReceipt("cc-a", op_id, 1, 2)

    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: (Provider(), None))
    account_deletion._destroy_owner_box(tmp_path, "cc-a", principal="alice")
    staged = account_deletion._stage_home(tmp_path, "cc-a")
    assert staged is not None
    assert (staged / "legacy" / "residual").read_text() == "bytes"
    assert not (tmp_path / "cc-a").exists()


def test_destroy_refuses_receipt_for_another_center(tmp_path, monkeypatch):
    class Provider:
        def bind_for_destroy(self, center, *, account_id):
            return object()

        def destroy(self, handle, op_id):
            return DestroyReceipt("cc-b", op_id, 1, 2)

    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: (Provider(), None))
    with pytest.raises(account_deletion.AccountDeletionError, match="invalid durable"):
        account_deletion._destroy_owner_box(tmp_path, "cc-a", principal="alice")
