"""D60 real inode labels; run Linux oracle --as-root."""
import os

import pytest

from tinyassets import workspace_fs as fs

pytestmark = pytest.mark.skipif(
    os.name != "posix" or getattr(os, "geteuid", lambda: -1)() != 0,
    reason="requires Linux root to seed dedicated inode identities",
)


def test_open_descriptor_checks_uid_and_gid_after_foreign_name_retirement(tmp_path):
    own = tmp_path / "alice"
    own.mkdir()
    os.chown(own, 1001, 300001)
    foreign = tmp_path / "bob"
    foreign.write_bytes(b"foreign")
    os.chown(foreign, 300002, 300002)
    alias = own / "alias"
    os.link(foreign, alias)
    foreign.unlink()
    fd = fs.open_dir_nofollow(own)
    try:
        for uid, gid in ((300002, 300002), (300001, 300002), (300002, 300001), (1001, 300001)):
            os.chown(alias, uid, gid)
            with pytest.raises(fs.UnsafePoolPath, match="identity"):
                fs.read_regular_file_beneath(fd, "alias", max_bytes=100)
            with pytest.raises(fs.UnsafePoolPath, match="identity"):
                fs.copy_regular_file_beneath(fd, "alias", tmp_path / "copy", max_bytes=100)
            assert not (tmp_path / "copy").exists()
        os.chown(alias, 300001, 300001)
        assert fs.read_regular_file_beneath(fd, "alias", max_bytes=100) == b"foreign"
        with pytest.raises(fs.UnsafePoolPath, match="root"):
            fs.read_regular_file_beneath(fd, "alias", max_bytes=100,
                                         expected_identity=(300002, 300002))
    finally:
        os.close(fd)


def test_nested_reader_root_preserves_owner_and_rejects_root_symlink(tmp_path):
    from tinyassets.universe_files import read_universe_file

    own = tmp_path / "alice"
    own.mkdir()
    os.chown(own, 1001, 300001)
    nested = own / "nested"
    nested.mkdir()
    target = nested / "file"
    target.write_bytes(b"foreign")
    os.chown(target, 300002, 300002)
    with pytest.raises(fs.UnsafePoolPath, match="identity"):
        read_universe_file(nested, "file")
    foreign = tmp_path / "bob"
    foreign.mkdir()
    os.chown(foreign, 1001, 300002)
    (foreign / "file").write_bytes(b"foreign")
    os.chown(foreign / "file", 300002, 300002)
    (own / "alias").symlink_to(foreign, target_is_directory=True)
    with pytest.raises(fs.UnsafePoolPath):
        read_universe_file(own / "alias", "file")


def test_data_root_read_keeps_first_owner_identity_across_descendants(tmp_path):
    own = tmp_path / "alice"
    own.mkdir()
    os.chown(own, 300001, 300001)
    child = own / "nested"
    child.mkdir()
    os.chown(child, 300002, 300002)
    target = child / "file"
    target.write_bytes(b"foreign")
    os.chown(target, 300002, 300002)
    fd = fs.open_dir_nofollow(tmp_path)
    try:
        with pytest.raises(fs.UnsafePoolPath, match="crosses"):
            fs.read_regular_file_beneath(fd, "alice/nested/file", max_bytes=100)
        os.chown(child, 1001, 1001)
        with pytest.raises(fs.UnsafePoolPath, match="identity"):
            fs.read_regular_file_beneath(fd, "alice/nested/file", max_bytes=100)
        os.chown(target, 300001, 300001)
        assert fs.read_regular_file_beneath(fd, "alice/nested/file", max_bytes=100) == b"foreign"
        os.chown(child, 300002, 300001)
        with pytest.raises(fs.UnsafePoolPath, match="invalid"):
            fs.read_regular_file_beneath(fd, "alice/nested/file", max_bytes=100)
    finally:
        os.close(fd)
