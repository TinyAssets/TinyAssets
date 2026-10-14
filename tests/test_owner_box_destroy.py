"""Deletion fences survive host loss; descriptor erasure never follows owner links."""

import os
from dataclasses import replace

import pytest

from tinyassets.boxes import destroy
from tinyassets.boxes.handles import OwnerGrant
from tinyassets.boxes.owner_files import OwnerFileHost
from tinyassets.boxes.provider import BoxAuthError, BoxError, OpIdReuse, StaleHandle

A = OwnerGrant("alice", 300001, "delete-a")
B = OwnerGrant("bob", 300002, "delete-b")


class Eraser:
    def __init__(self):
        self.steps = []
        self.fail = None

    def recover(self):
        pass

    def close(self):
        pass

    def stop(self, uid):
        self.steps.append(("stop", uid))
        if self.fail == "stop":
            raise BoxError("stop not proven")

    def destroy_count(self, uid):
        self.steps.append(("count", uid))
        return 12

    def destroy(self, uid):
        self.steps.append(("erase", uid))
        if self.fail == "erase":
            raise BoxError("partial erase")


def make_host(tmp_path, backend, tombstones):
    def admitted(grant, center):
        return (grant.owner_id, grant.uid, center) in {
            ("alice", 300001, "a"), ("alice", 300001, "a2"), ("bob", 300002, "b")}
    return OwnerFileHost(
        state_dir=tmp_path / "state", backend=backend,
        authorize=lambda grant, center: admitted(grant, center)
        and grant.owner_id not in tombstones,
        authorize_destroy=lambda grant, center: admitted(grant, center)
        and grant.owner_id in tombstones)


@pytest.mark.parametrize("failure", ["stop", "erase"])
def test_failed_destroy_is_fenced_and_resumes_after_restart(tmp_path, failure):
    backend, tombstones = Eraser(), set()
    host = make_host(tmp_path, backend, tombstones)
    token = host.bind(A, "a")["token"]
    before = host.state.epoch("alice")
    with pytest.raises(BoxAuthError):
        host.destroy(A, token, "delete-account")
    assert backend.steps == []
    tombstones.add("alice")
    backend.fail = failure
    with pytest.raises(BoxError):
        host.destroy(A, token, "delete-account")
    record = host.state.owner_destroy("alice")
    assert record["epoch"] == before + 1 and record["state"] == "deleting"
    if failure == "stop":
        assert backend.steps == [("stop", 300001)]
    else:
        assert record["entries"] == 12
    with pytest.raises(StaleHandle):
        host.destroy(A, token, "different")
    host.close()
    backend.fail = None
    host = make_host(tmp_path, backend, tombstones)
    try:
        # Even if the daemon accidentally clears its tombstone, admission stays
        # fenced by the root host's durable deletion record.
        tombstones.clear()
        with pytest.raises(BoxAuthError):
            host.bind(A, "a2")
        tombstones.add("alice")
        token = host.bind(A, "a", destroying=True)["token"]
        receipt = host.destroy(A, token, "delete-account")
        steps = list(backend.steps)
        assert host.destroy(A, token, "delete-account") == receipt
        assert backend.steps == steps
        assert receipt["result"]["new_epoch"] == before + 1
        assert receipt["result"]["files_removed"] == 12
        assert sum(step == "count" for step, _ in steps) == 1
        assert host.bind(B, "b")["epoch"] == 1
    finally:
        host.close()


def test_delete_handle_cannot_cross_owner_session_or_center(tmp_path):
    backend = Eraser()
    host = make_host(tmp_path, backend, {"alice", "bob"})
    try:
        token = host.bind(A, "a", destroying=True)["token"]
        with pytest.raises(BoxAuthError):
            host.destroy(B, token, "d")
        with pytest.raises(BoxAuthError):
            host.destroy(replace(A, session_id="other"), token, "d")
        receipt = host.destroy(A, token, "d")
        other = host.bind(A, "a2", destroying=True)["token"]
        with pytest.raises(OpIdReuse):
            host.destroy(A, other, "d")
        assert receipt["result"]["command_center_id"] == "a"
        assert all(uid == 300001 for _, uid in backend.steps)
    finally:
        host.close()


@pytest.mark.skipif(os.geteuid() != 0, reason="permanent UID tree requires Linux oracle root")
def test_descriptor_erasure_preserves_link_targets_and_other_owner(tmp_path):
    root = tmp_path / "300001"
    root.mkdir(mode=0o700)
    os.chown(root, 300001, 300001)
    sibling = tmp_path / "300002"
    sibling.mkdir()
    target = sibling / "private"
    target.write_bytes(b"keep")
    (root / "directory").mkdir()
    (root / "directory/file").write_bytes(b"erase")
    (root / "link").symlink_to(target)
    os.link(target, root / "hardlink")
    assert destroy.tree(tmp_path, 300001, erase=False) == 5
    assert destroy.tree(tmp_path, 300001, erase=True) == 5
    assert not root.exists() and target.read_bytes() == b"keep"
    assert destroy.tree(tmp_path, 300001, erase=True) == 0


@pytest.mark.skipif(os.geteuid() != 0, reason="permanent UID tree requires Linux oracle root")
def test_mount_identity_refuses_same_device_bind_before_unlink(tmp_path, monkeypatch):
    root = tmp_path / "300001"
    root.mkdir(mode=0o700)
    os.chown(root, 300001, 300001)
    (root / "protected").write_bytes(b"keep")
    original = destroy._mount

    def mounted_entry(fd):
        if os.readlink(f"/proc/self/fd/{fd}").endswith("/protected"):
            return "foreign-bind-mount"
        return original(fd)

    monkeypatch.setattr(destroy, "_mount", mounted_entry)
    with pytest.raises(BoxError, match="foreign mount"):
        destroy.tree(tmp_path, 300001, erase=True)
    assert (root / "protected").read_bytes() == b"keep"
