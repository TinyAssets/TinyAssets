"""D61 read-only inode census on real Linux directory descriptors."""
import os
import stat

import pytest

from scripts.role_legacy_alias_scan import scan

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux descriptor inventory")


def test_cross_owner_and_missing_names_never_become_sole_owner(tmp_path):
    for name in ("alice", "bob"):
        (tmp_path / name).mkdir()
    source = tmp_path / "bob" / "record"
    source.write_bytes(b"private fixture")
    os.link(source, tmp_path / "alice" / "alias")
    sole = tmp_path / "alice" / "sole"
    sole.write_bytes(b"one owner")
    os.link(sole, tmp_path / "alice" / "sole-alias")
    external = tmp_path / "outside"
    external.write_bytes(b"outside")
    os.link(external, tmp_path / "alice" / "unknown")
    identities = frozenset({(os.getuid(), os.getgid())})
    before = {p: p.stat() for p in (source, sole, external, tmp_path / "alice")}
    report = scan(tmp_path, ["alice", "bob"], identities)
    assert report["finding_counts"] == {"cross_owner_inode": 1, "unseen_inode_names": 1}
    assert report["counts"]["sole_owner_inodes"] == 1
    assert report["assignment_ready"] is False
    for path, old in before.items():
        now = path.stat()
        assert (now.st_ino, now.st_mode, now.st_uid, now.st_gid, now.st_atime_ns,
                now.st_mtime_ns, now.st_ctime_ns) == (
            old.st_ino, old.st_mode, old.st_uid, old.st_gid, old.st_atime_ns,
            old.st_mtime_ns, old.st_ctime_ns)
    source.unlink()
    report = scan(tmp_path, ["alice", "bob"], identities)
    assert report["finding_counts"] == {"unseen_inode_names": 1}
    assert report["counts"]["sole_owner_inodes"] == 2


def test_specials_and_foreign_metadata_reported_without_following_or_opening(tmp_path):
    owner = tmp_path / "u-alice"
    owner.mkdir()
    (owner / "escape").symlink_to("/proc/1/root", target_is_directory=True)
    os.mkfifo(owner / "pipe")
    report = scan(tmp_path, legacy_ids=frozenset({(400001, 400001)}))
    assert report["owners"] == ["u-alice"]
    assert report["counts"]["entries"] == 2
    assert report["finding_counts"] == {"foreign_identity": 3, "special": 2}
    assert {r["file_type"] for r in report["findings"] if r["kind"] == "special"} == {
        stat.S_IFLNK, stat.S_IFIFO}
    with pytest.raises(OSError):
        scan(owner / "escape", [])


def test_discovery_includes_legacy_marker_and_refuses_linked_owner_root(tmp_path):
    (tmp_path / "old-name").mkdir()
    (tmp_path / "old-name" / "universe.json").write_text("payload never parsed")
    (tmp_path / "u-alias").symlink_to(tmp_path / "old-name", target_is_directory=True)
    report = scan(tmp_path, legacy_ids=frozenset({(os.getuid(), os.getgid())}))
    assert report["owners"] == ["old-name"]
    assert report["finding_counts"] == {"scan_error": 1}
    assert report["counts"]["regular_names"] == 1
    with pytest.raises(ValueError, match="direct child"):
        scan(tmp_path, ["../escape"])
