"""Run on the Linux oracle: preservation and path refusal, never production."""
import errno
import os
from pathlib import Path

import pytest

from scripts.cleanup_provider_child import cleanup


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "universes"
    source = root / "alice" / ".runtime" / "provider-child"
    source.mkdir(parents=True)
    (source / "session").write_bytes(b"old session")
    (source.parent / "native-session").write_bytes(b"keep")
    archive = tmp_path / "archive"
    archive.mkdir(mode=0o700)
    return root, source, archive


def test_dry_run_apply_and_recreated_empty_rerun(world):
    root, source, archive = world
    report = cleanup(root, "alice", archive)
    assert report["status"] == "dry-run"
    assert report["bytes"] == len(b"old session")
    assert (source / "session").read_bytes() == b"old session"
    assert list(archive.iterdir()) == []
    report = cleanup(root, "alice", archive, apply=True, offline=True)
    saved = Path(report["archive"])
    assert report["status"] == "archived"
    assert (saved / "session").read_bytes() == b"old session"
    assert (source.parent / "native-session").read_bytes() == b"keep"
    assert cleanup(root, "alice", archive, apply=True, offline=True)["status"] == "absent"
    source.mkdir()
    assert cleanup(root, "alice", archive, apply=True, offline=True)["status"] == "empty"
    assert (saved / "session").read_bytes() == b"old session"


def test_offline_and_collision_refuse_without_changes(world):
    root, source, archive = world
    with pytest.raises(ValueError, match="offline"):
        cleanup(root, "alice", archive, apply=True)
    destination = Path(cleanup(root, "alice", archive)["archive"])
    destination.mkdir()
    with pytest.raises(ValueError, match="already exists"):
        cleanup(root, "alice", archive, apply=True, offline=True)
    assert (source / "session").read_bytes() == b"old session"


@pytest.mark.parametrize("name", ["", ".", "..", "../bob", "/tmp"])
def test_universe_boundary(world, name):
    root, source, archive = world
    with pytest.raises(ValueError):
        cleanup(root, name, archive, apply=True, offline=True)
    assert source.exists()


@pytest.mark.parametrize("boundary", ["root", "home", "runtime", "source", "archive", "file"])
def test_symlinks_refused(world, boundary):
    root, source, archive = world
    path = {"root": root, "home": root / "alice", "runtime": source.parent,
            "source": source, "archive": archive, "file": source / "session"}[boundary]
    moved = path.with_name(path.name + "-real")
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=moved.is_dir())
    with pytest.raises(ValueError):
        cleanup(root, "alice", archive, apply=True, offline=True)
    assert moved.exists()
    assert (source / "session").read_bytes() == b"old session"


@pytest.mark.parametrize("kind", ["overlap", "permissions", "fifo", "mount", "rename"])
def test_other_refusals_preserve_source(world, monkeypatch, kind):
    root, source, archive = world
    if kind == "overlap":
        archive = root / "archive"
        archive.mkdir(mode=0o700)
    elif kind == "permissions":
        archive.chmod(0o755)
    elif kind == "fifo":
        os.mkfifo(source / "pipe")
    elif kind == "mount":
        monkeypatch.setattr(os.path, "ismount", lambda p: Path(p).name == "session")
    elif kind == "rename":
        def fail(*args):
            raise OSError(errno.EXDEV, "different filesystem")
        monkeypatch.setattr(os, "rename", fail)
    with pytest.raises((ValueError, OSError)):
        cleanup(root, "alice", archive, apply=True, offline=True)
    assert (source / "session").read_bytes() == b"old session"
