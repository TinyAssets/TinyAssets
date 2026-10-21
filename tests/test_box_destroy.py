"""C1 deletes opaque images; only runtime socket exports have a tree cleanup."""

import os
import subprocess
from types import SimpleNamespace

import pytest

from tinyassets.boxes.destroy import socket_tree
from tinyassets.boxes.volumes import OwnerVolumes


@pytest.fixture
def volumes(tmp_path, monkeypatch):
    monkeypatch.setattr(OwnerVolumes, "_run", staticmethod(
        lambda *args: SimpleNamespace(stdout="reflink=1" if args[0] == "xfs_info" else "")))
    monkeypatch.setattr(OwnerVolumes, "_mounts", staticmethod(lambda: []))
    result = OwnerVolumes(tmp_path / "images", tmp_path / "mounts", "proof:synthetic")
    result.create(300001, 2 * 1024**3)
    result.create(300002, 2 * 1024**3)
    return result


def test_destroy_uses_only_opaque_image_names_and_preserves_other_owner(volumes, monkeypatch):
    for suffix in ("create", "prototype", "clone", "gz", "readback", "download", "restore"):
        (volumes.store / f".300001.{suffix}").write_bytes(b"interrupted stage")
    def guard(original):
        def checked(path, *args, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)):
                value = os.fsdecode(path)
                assert not value.startswith(str(volumes.mount_root))
            return original(path, *args, **kwargs)
        return checked
    with monkeypatch.context() as patch:
        for name in ("open", "stat", "lstat", "listdir", "scandir", "walk"):
            patch.setattr(os, name, guard(getattr(os, name)))
        volumes.delete(300001)
        volumes.delete(300001)
    assert not (volumes.store / "300001.img").exists()
    assert not list(volumes.store.glob(".300001.*"))
    assert volumes.size(300002) == 2 * 1024**3


def test_busy_detach_preserves_image_and_never_uses_lazy_unmount(volumes, monkeypatch):
    target = str(volumes.mount_root / "300001")
    monkeypatch.setattr(volumes, "_loops", lambda uid: ["/dev/loop7"])
    monkeypatch.setattr(volumes, "_mounts", lambda: [(target, "/dev/loop7")])
    def busy(*args):
        assert args == ("umount", "--no-canonicalize", target)
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(volumes, "_run", busy)
    with pytest.raises(subprocess.CalledProcessError):
        volumes.delete(300001)
    assert (volumes.store / "300001.img").exists()


def test_runtime_socket_cleanup_preserves_symlink_target(tmp_path):
    sockets = tmp_path / "runtime"
    root = sockets / "300001"
    root.mkdir(parents=True)
    victim = tmp_path / "other"
    victim.write_text("untouched")
    nested = root
    for _ in range(200):
        nested = nested / "d"
        nested.mkdir()
    (nested / "link").symlink_to(victim)
    socket_tree(sockets, 300001)
    assert not root.exists()
    assert victim.read_text() == "untouched"


pytestmark = pytest.mark.verifies('owner-daemon-in-box:C1')
