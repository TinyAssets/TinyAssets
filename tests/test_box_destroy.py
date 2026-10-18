"""Stopped owner trees are erased without depth limits or following links."""
import os

import pytest

from tinyassets.boxes.destroy import tree
from tinyassets.boxes.provider import BoxError


def test_destroy_200_deep_tree_and_leave_symlink_target(tmp_path):
    store = tmp_path / "owners"
    root = store / "300001"
    root.mkdir(parents=True, mode=0o700)
    os.chown(root, 300001, 300001)
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    nested = root
    for _ in range(200):
        nested = nested / "d"
        nested.mkdir()
    (nested / "note").write_text("deep bytes")
    (nested / "link").symlink_to(victim)
    assert tree(store, 300001, erase=False) == 203
    assert tree(store, 300001, erase=True) == 203
    assert not root.exists()
    assert victim.read_text() == "untouched"


def test_destroy_refuses_foreign_mount_before_erasure(tmp_path, monkeypatch):
    import tinyassets.boxes.destroy as destroy

    root = tmp_path / "300001"
    root.mkdir(mode=0o700)
    os.chown(root, 300001, 300001)
    (root / "note").write_text("keep")
    mount = destroy._mount
    monkeypatch.setattr(destroy, "_mount", lambda fd:
                        "foreign" if os.readlink(f"/proc/self/fd/{fd}").endswith("note")
                        else mount(fd))
    with pytest.raises(BoxError, match="foreign mount"):
        tree(tmp_path, 300001, erase=True)
    assert (root / "note").read_text() == "keep"
