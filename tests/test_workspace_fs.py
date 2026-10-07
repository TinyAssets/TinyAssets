"""The no-follow directory handles the workspace sink holds.

Every guard here is a link-swap defence, so the tests are about paths that are
NOT what they look like: a symlinked component, a symlinked leaf, a FIFO, a
traversal, a file whose stat under-reports its size. POSIX only - the sink runs
on Linux, and on Windows the helpers refuse rather than imitate.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from tinyassets import workspace_fs as wfs

#: A lease name the hardened create_lease_dir accepts: 16 hex characters,
#: which is what secrets.token_hex(8) produces.
GOOD_NAME = "0123456789abcdef"

posix_only = pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX openat semantics (O_NOFOLLOW + dir_fd); the workspace sink runs on Linux",
)
windows_only = pytest.mark.skipif(os.name == "posix", reason="the off-POSIX refusal")


def a_proc_dir_fd() -> int | None:
    """A handle on ``/proc/<pid>``, whose files report ``st_size == 0`` and then
    read out real bytes - the only honest way to test that the read bound does
    not trust the stat. None when there is no procfs."""
    candidate = f"/proc/{os.getpid()}"
    if not os.path.isdir(candidate):
        return None
    try:
        return wfs.open_dir_nofollow(candidate)
    except OSError:
        return None


# --------------------------------------------------------------------------
# open_dir_nofollow
# --------------------------------------------------------------------------


@posix_only
def test_open_dir_nofollow_hands_back_the_directory_it_named(tmp_path: Path) -> None:
    target = tmp_path / "pool" / "lease"
    target.mkdir(parents=True)
    fd = wfs.open_dir_nofollow(target)
    try:
        opened = os.fstat(fd)
        on_disk = os.stat(target)
        assert (opened.st_dev, opened.st_ino) == (on_disk.st_dev, on_disk.st_ino)
    finally:
        os.close(fd)


@posix_only
def test_open_dir_nofollow_refuses_a_symlinked_component(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "inside").mkdir(parents=True)
    link = tmp_path / "link"
    os.symlink(real, link, target_is_directory=True)
    # The LAST component is a real directory; the middle one is the swap.
    with pytest.raises(wfs.UnsafePoolPath):
        wfs.open_dir_nofollow(link / "inside")


@posix_only
def test_open_dir_nofollow_refuses_a_symlinked_final_component(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(real, link, target_is_directory=True)
    with pytest.raises(wfs.UnsafePoolPath):
        wfs.open_dir_nofollow(link)


@posix_only
def test_open_dir_nofollow_refuses_a_relative_path(tmp_path: Path) -> None:
    with pytest.raises(wfs.UnsafePoolPath, match="absolute"):
        wfs.open_dir_nofollow("pool/lease")


@posix_only
def test_open_dir_nofollow_refuses_a_traversal_component(tmp_path: Path) -> None:
    with pytest.raises(wfs.UnsafePoolPath, match="traversal"):
        wfs.open_dir_nofollow(str(tmp_path) + "/../etc")


# --------------------------------------------------------------------------
# create_lease_dir
# --------------------------------------------------------------------------


@posix_only
def test_create_lease_dir_returns_a_handle_on_the_inode_it_created(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    parent = wfs.open_dir_nofollow(tmp_path)
    try:
        fd = wfs.create_lease_dir(parent, GOOD_NAME)
        try:
            opened = os.fstat(fd)
            on_disk = os.stat(tmp_path / GOOD_NAME)
            assert (opened.st_dev, opened.st_ino) == (on_disk.st_dev, on_disk.st_ino)
            assert stat.S_ISDIR(opened.st_mode)
            # Nothing for group or other: a lease is the daemon's alone.
            assert stat.S_IMODE(opened.st_mode) & 0o077 == 0
        finally:
            os.close(fd)
    finally:
        os.close(parent)


@posix_only
def test_create_lease_dir_refuses_a_name_that_is_not_one_component(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    parent = wfs.open_dir_nofollow(tmp_path)
    try:
        with pytest.raises(wfs.UnsafePoolPath, match="separator"):
            wfs.create_lease_dir(parent, "nested/lease")
        with pytest.raises(wfs.UnsafePoolPath, match="traversal"):
            wfs.create_lease_dir(parent, "..")
        with pytest.raises(wfs.UnsafePoolPath):
            wfs.create_lease_dir(parent, "")
    finally:
        os.close(parent)


@posix_only
def test_create_lease_dir_refuses_a_directory_swapped_in_after_the_create(
    tmp_path: Path,
) -> None:
    """The race the inode compare exists for: between the mkdir and the open,
    a DIFFERENT real directory is renamed over the name. O_NOFOLLOW does not
    see it - it is not a link - so only the inode compare catches it."""
    os.chmod(tmp_path, 0o700)
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    parent = wfs.open_dir_nofollow(tmp_path)
    original = wfs._open_child_dir

    def swap_then_open(parent_fd: int, name: str) -> int:
        os.rename(decoy, tmp_path / name)
        return original(parent_fd, name)

    # Patched only AFTER the parent handle is open: this seam is also how
    # open_dir_nofollow walks, so patching first would swap during resolution.
    wfs._open_child_dir = swap_then_open
    try:
        with pytest.raises(wfs.UnsafePoolPath, match="was replaced"):
            wfs.create_lease_dir(parent, GOOD_NAME)
    finally:
        wfs._open_child_dir = original
        os.close(parent)


@posix_only
def test_create_lease_dir_refuses_an_existing_name(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    (tmp_path / GOOD_NAME).mkdir()
    parent = wfs.open_dir_nofollow(tmp_path)
    try:
        with pytest.raises(FileExistsError):
            wfs.create_lease_dir(parent, GOOD_NAME)
    finally:
        os.close(parent)


# --------------------------------------------------------------------------
# read_regular_file_beneath
# --------------------------------------------------------------------------


@pytest.fixture
def isolation_on(monkeypatch):
    from tinyassets.broker.supervisor import ENV_SWITCH, PROCESS

    monkeypatch.setenv(ENV_SWITCH, PROCESS)


@posix_only
@pytest.mark.parametrize("operation", ["read", "copy"])
def test_switch_off_reads_a_same_owner_hardlink_without_walking_ancestry(
    tmp_path, operation, monkeypatch,
):
    # R2: production holds same-owner multi-link files; unselected reads keep
    # main's behavior, with no ``..`` identity walk.
    from tinyassets.broker.supervisor import ENV_SWITCH

    monkeypatch.delenv(ENV_SWITCH, raising=False)

    def no_walk(*_args):
        raise AssertionError("identity walk ran with the switch OFF")

    monkeypatch.setattr(wfs, "_read_owner_identity", no_walk)
    monkeypatch.setattr(wfs, "_directory_owner_identity", no_walk)
    (tmp_path / "owner" / "nested").mkdir(parents=True)
    record = tmp_path / "owner" / "nested" / "record"
    record.write_bytes(b"OWNER-BYTES")
    os.link(record, tmp_path / "owner" / "twin")
    fd = wfs.open_dir_nofollow(tmp_path / "owner")
    try:
        if operation == "read":
            assert wfs.read_regular_file_beneath(
                fd, "nested/record", max_bytes=1024) == b"OWNER-BYTES"
        else:
            assert wfs.copy_regular_file_beneath(
                fd, "nested/record", tmp_path / "copy", max_bytes=1024) == 11
            assert (tmp_path / "copy").read_bytes() == b"OWNER-BYTES"
    finally:
        os.close(fd)


@posix_only
@pytest.mark.parametrize("operation", ["read", "copy"])
def test_regular_file_refuses_preplanted_hardlink(tmp_path, operation, isolation_on):
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"FOREIGN-PRIVATE-BYTES")
    owner = tmp_path / "owner"
    owner.mkdir()
    alias = owner / "record"
    os.link(foreign, alias)
    before = foreign.stat()
    fd = wfs.open_dir_nofollow(owner)
    try:
        with pytest.raises(wfs.UnsafePoolPath, match="links"):
            if operation == "read":
                wfs.read_regular_file_beneath(fd, "record", max_bytes=1024)
            else:
                wfs.copy_regular_file_beneath(fd, "record", owner / "copy", max_bytes=1024)
        assert not (owner / "copy").exists()
        alias.unlink()
        alias.write_bytes(b"OWNER-CONTROL")
        assert wfs.read_regular_file_beneath(fd, "record", max_bytes=1024) == b"OWNER-CONTROL"
    finally:
        os.close(fd)
    assert foreign.read_bytes() == b"FOREIGN-PRIVATE-BYTES"
    after = foreign.stat()
    assert (before.st_uid, before.st_gid, before.st_mode, before.st_mtime_ns) == (
        after.st_uid, after.st_gid, after.st_mode, after.st_mtime_ns)


@posix_only
def test_hardlink_validation_uses_open_descriptor_after_name_replacement(
    tmp_path, monkeypatch, isolation_on,
):
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"FOREIGN-PRIVATE-BYTES")
    alias = tmp_path / "alias"
    os.link(foreign, alias)
    original_open = wfs._open_leaf

    def replace_after_open(parent, name):
        fd = original_open(parent, name)
        # Keep both foreign links alive while substituting a benign pathname.
        alias.rename(tmp_path / "retained-alias")
        alias.write_bytes(b"BENIGN-REPLACEMENT")
        return fd

    monkeypatch.setattr(wfs, "_open_leaf", replace_after_open)
    fd = wfs.open_dir_nofollow(tmp_path)
    try:
        with pytest.raises(wfs.UnsafePoolPath, match="links"):
            wfs.read_regular_file_beneath(fd, "alias", max_bytes=1024)
    finally:
        os.close(fd)
    assert alias.read_bytes() == b"BENIGN-REPLACEMENT"
    assert foreign.read_bytes() == b"FOREIGN-PRIVATE-BYTES"


@posix_only
def test_read_returns_the_bytes_of_a_regular_file(tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "manifest.json").write_bytes(b'{"ok": true}')
    fd = wfs.open_dir_nofollow(tmp_path)
    try:
        assert (
            wfs.read_regular_file_beneath(fd, "repo/manifest.json", max_bytes=1024)
            == b'{"ok": true}'
        )
    finally:
        os.close(fd)


@posix_only
def test_read_refuses_a_symlinked_directory_component(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"the host's key")
    lease = tmp_path / "lease"
    lease.mkdir()
    os.symlink(outside, lease / "escape", target_is_directory=True)
    fd = wfs.open_dir_nofollow(lease)
    try:
        with pytest.raises(wfs.UnsafePoolPath):
            wfs.read_regular_file_beneath(fd, "escape/secret.txt", max_bytes=1024)
    finally:
        os.close(fd)


@posix_only
def test_read_refuses_a_symlinked_leaf(tmp_path: Path) -> None:
    """The file was replaced by a link to somewhere else: the open refuses, and
    the type check happens on the OPEN descriptor, never on a stat of the name."""
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"the host's key")
    lease = tmp_path / "lease"
    lease.mkdir()
    os.symlink(outside, lease / "manifest.json")
    fd = wfs.open_dir_nofollow(lease)
    try:
        with pytest.raises(wfs.UnsafePoolPath, match="symlink"):
            wfs.read_regular_file_beneath(fd, "manifest.json", max_bytes=1024)
    finally:
        os.close(fd)


@posix_only
def test_read_refuses_a_fifo_leaf(tmp_path: Path) -> None:
    """A FIFO would block the daemon forever on a normal open; O_NONBLOCK opens
    it and the S_ISREG check on the descriptor refuses it."""
    lease = tmp_path / "lease"
    lease.mkdir()
    os.mkfifo(lease / "manifest.json")
    fd = wfs.open_dir_nofollow(lease)
    try:
        with pytest.raises(wfs.UnsafePoolPath, match="not a regular file"):
            wfs.read_regular_file_beneath(fd, "manifest.json", max_bytes=1024)
    finally:
        os.close(fd)


@posix_only
def test_read_refuses_a_directory_leaf(tmp_path: Path) -> None:
    lease = tmp_path / "lease"
    (lease / "subdir").mkdir(parents=True)
    fd = wfs.open_dir_nofollow(lease)
    try:
        with pytest.raises(wfs.UnsafePoolPath, match="not a regular file"):
            wfs.read_regular_file_beneath(fd, "subdir", max_bytes=1024)
    finally:
        os.close(fd)


@posix_only
def test_read_refuses_traversals_absolutes_and_empty_components(tmp_path: Path) -> None:
    (tmp_path / "lease").mkdir()
    (tmp_path / "outside.txt").write_bytes(b"x")
    fd = wfs.open_dir_nofollow(tmp_path / "lease")
    try:
        with pytest.raises(wfs.UnsafePoolPath, match="traversal"):
            wfs.read_regular_file_beneath(fd, "../outside.txt", max_bytes=1024)
        with pytest.raises(wfs.UnsafePoolPath, match="absolute"):
            wfs.read_regular_file_beneath(fd, "/etc/passwd", max_bytes=1024)
        with pytest.raises(wfs.UnsafePoolPath, match="empty component"):
            wfs.read_regular_file_beneath(fd, "repo//manifest.json", max_bytes=1024)
        with pytest.raises(wfs.UnsafePoolPath):
            wfs.read_regular_file_beneath(fd, "", max_bytes=1024)
    finally:
        os.close(fd)


@posix_only
def test_read_refuses_an_oversize_file(tmp_path: Path) -> None:
    (tmp_path / "big.bin").write_bytes(b"0" * 4096)
    fd = wfs.open_dir_nofollow(tmp_path)
    try:
        with pytest.raises(wfs.UnsafePoolPath, match="over the 1024 bound"):
            wfs.read_regular_file_beneath(fd, "big.bin", max_bytes=1024)
        assert len(wfs.read_regular_file_beneath(fd, "big.bin", max_bytes=4096)) == 4096
    finally:
        os.close(fd)


@posix_only
def test_read_does_not_trust_the_size_the_stat_reported(tmp_path: Path) -> None:
    """A procfs file stats as zero bytes and then reads out hundreds: exactly
    the shape of a file that grows between the stat and the read."""
    fd = a_proc_dir_fd()
    if fd is None:
        pytest.skip("no procfs on this host")
    try:
        assert os.stat(f"/proc/{os.getpid()}/status").st_size == 0
        with pytest.raises(wfs.UnsafePoolPath, match="grew past"):
            wfs.read_regular_file_beneath(fd, "status", max_bytes=8)
    finally:
        os.close(fd)


# --------------------------------------------------------------------------
# copy_regular_file_beneath
# --------------------------------------------------------------------------


@posix_only
def test_copy_is_byte_exact_and_private(tmp_path: Path) -> None:
    payload = bytes(range(256)) * 8
    (tmp_path / "lease").mkdir()
    (tmp_path / "lease" / "bundle.pack").write_bytes(payload)
    dest = tmp_path / "staged.pack"
    fd = wfs.open_dir_nofollow(tmp_path / "lease")
    try:
        copied = wfs.copy_regular_file_beneath(fd, "bundle.pack", dest, max_bytes=1 << 20)
    finally:
        os.close(fd)
    assert copied == len(payload)
    assert dest.read_bytes() == payload
    assert stat.S_IMODE(os.stat(dest).st_mode) & 0o077 == 0


@posix_only
def test_copy_refuses_an_oversize_source_without_creating_the_destination(
    tmp_path: Path,
) -> None:
    (tmp_path / "bundle.pack").write_bytes(b"0" * 4096)
    dest = tmp_path / "staged.pack"
    fd = wfs.open_dir_nofollow(tmp_path)
    try:
        with pytest.raises(wfs.UnsafePoolPath, match="over the 1024 bound"):
            wfs.copy_regular_file_beneath(fd, "bundle.pack", dest, max_bytes=1024)
    finally:
        os.close(fd)
    assert not dest.exists()


@posix_only
def test_copy_removes_the_partial_destination_when_the_bound_is_hit(tmp_path: Path) -> None:
    """The source under-reports its size, so the bound is only hit MID-copy:
    the destination has already been created and must not be left behind."""
    fd = a_proc_dir_fd()
    if fd is None:
        pytest.skip("no procfs on this host")
    dest = tmp_path / "staged.txt"
    try:
        with pytest.raises(wfs.UnsafePoolPath, match="grew past"):
            wfs.copy_regular_file_beneath(fd, "status", dest, max_bytes=8)
    finally:
        os.close(fd)
    assert not dest.exists()


@posix_only
def test_copy_refuses_an_existing_destination(tmp_path: Path) -> None:
    (tmp_path / "bundle.pack").write_bytes(b"payload")
    dest = tmp_path / "staged.pack"
    dest.write_bytes(b"do not overwrite me")
    fd = wfs.open_dir_nofollow(tmp_path)
    try:
        with pytest.raises(FileExistsError):
            wfs.copy_regular_file_beneath(fd, "bundle.pack", dest, max_bytes=1 << 20)
    finally:
        os.close(fd)
    assert dest.read_bytes() == b"do not overwrite me"


@posix_only
def test_copy_refuses_a_symlink_planted_at_the_destination(tmp_path: Path) -> None:
    (tmp_path / "bundle.pack").write_bytes(b"payload")
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"the host's file")
    dest = tmp_path / "staged.pack"
    os.symlink(victim, dest)
    fd = wfs.open_dir_nofollow(tmp_path)
    try:
        with pytest.raises(OSError):
            wfs.copy_regular_file_beneath(fd, "bundle.pack", dest, max_bytes=1 << 20)
    finally:
        os.close(fd)
    assert victim.read_bytes() == b"the host's file"
    # And the link itself survives: a destination this call did not create is
    # not this call's to clean up.
    assert os.path.islink(dest)


@posix_only
def test_copy_refuses_a_symlinked_source_leaf(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"the host's key")
    lease = tmp_path / "lease"
    lease.mkdir()
    os.symlink(outside, lease / "bundle.pack")
    dest = tmp_path / "staged.pack"
    fd = wfs.open_dir_nofollow(lease)
    try:
        with pytest.raises(wfs.UnsafePoolPath, match="symlink"):
            wfs.copy_regular_file_beneath(fd, "bundle.pack", dest, max_bytes=1 << 20)
    finally:
        os.close(fd)
    assert not dest.exists()


# --------------------------------------------------------------------------
# bind_target_for
# --------------------------------------------------------------------------


@posix_only
def test_bind_target_names_the_descriptor_and_resolves_to_its_inode(tmp_path: Path) -> None:
    lease = tmp_path / "lease"
    lease.mkdir()
    fd = wfs.open_dir_nofollow(lease)
    try:
        target = wfs.bind_target_for(fd)
        assert target == f"/proc/self/fd/{fd}"
        if not os.path.isdir("/proc/self/fd"):
            pytest.skip("no procfs on this host")
        through_fd = os.stat(target)
        on_disk = os.stat(lease)
        assert (through_fd.st_dev, through_fd.st_ino) == (on_disk.st_dev, on_disk.st_ino)
        # The rename the bind must survive: the NAME now points elsewhere, the
        # descriptor still names the directory that was checked.
        moved = tmp_path / "moved"
        os.rename(lease, moved)
        decoy = tmp_path / "lease"
        decoy.mkdir()
        still = os.stat(target)
        assert (still.st_dev, still.st_ino) == (on_disk.st_dev, on_disk.st_ino)
        assert os.stat(decoy).st_ino != still.st_ino
    finally:
        os.close(fd)


@posix_only
def test_bind_target_refuses_a_non_descriptor() -> None:
    with pytest.raises(ValueError):
        wfs.bind_target_for(-1)


# --------------------------------------------------------------------------
# off-POSIX, and the module's own discipline
# --------------------------------------------------------------------------


@windows_only
def test_the_descriptor_helpers_refuse_loudly_off_posix(tmp_path: Path) -> None:
    """No imitation: a path-based stand-in would look like the guarantee and
    not be it."""
    for call in (
        lambda: wfs.open_dir_nofollow(tmp_path),
        lambda: wfs.create_lease_dir(0, "lease1"),
        lambda: wfs.read_regular_file_beneath(0, "manifest.json", max_bytes=16),
        lambda: wfs.copy_regular_file_beneath(0, "a", tmp_path / "b", max_bytes=16),
        lambda: wfs.bind_target_for(0),
    ):
        with pytest.raises(NotImplementedError, match="POSIX"):
            call()


def test_the_module_reads_no_env_vars() -> None:
    source = Path(wfs.__file__).read_text(encoding="utf-8")
    for forbidden in ("environ", "getenv"):
        assert forbidden not in source, forbidden


# --------------------------------------------------------------------------
# the POSIX branch acts only through descriptors (Codex P1 #5)
# --------------------------------------------------------------------------


def _boom(name):
    def _raise(*args, **kwargs):
        raise AssertionError(f"os.{name} is a path-based call: TOCTOU by construction")

    return _raise


def _needs_dir_fd(original, name):
    def _checked(*args, **kwargs):
        if "dir_fd" not in kwargs and not any(
            key in kwargs for key in ("src_dir_fd", "dst_dir_fd")
        ):
            raise AssertionError(f"os.{name} was called by PATH, not through a handle")
        return original(*args, **kwargs)

    return _checked


def _forbid_path_based_calls(monkeypatch) -> None:
    """Make every path-based filesystem call an error.

    The POSIX branch resolves once and then acts through descriptors; a
    path-based call AFTER a check is the race this module exists to close, so
    the honest test is that those calls simply do not happen.
    """
    for name in ("makedirs", "replace", "scandir", "walk", "removedirs", "rmtree"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _boom(name))
    for name in ("mkdir", "rmdir", "unlink", "rename", "lstat"):
        monkeypatch.setattr(os, name, _needs_dir_fd(getattr(os, name), name))
    original_listdir = os.listdir

    def _listdir(target=None):
        if not isinstance(target, int):
            raise AssertionError("os.listdir was called by PATH, not on a handle")
        return original_listdir(target)

    monkeypatch.setattr(os, "listdir", _listdir)


@posix_only
def test_the_posix_branch_never_touches_a_path_after_it_resolves_one(
    tmp_path: Path, monkeypatch
) -> None:
    pool = tmp_path / "scratch"
    lease = pool / "lease1"
    (lease / "repo" / ".git").mkdir(parents=True)
    (lease / "repo" / ".git" / "HEAD").write_text("ref: main", encoding="utf-8")
    (lease / "repo" / "file.txt").write_text("work", encoding="utf-8")
    quarantine = pool / ".quarantine" / "lease1.1"
    fs = wfs.RealPoolFilesystem()

    _forbid_path_based_calls(monkeypatch)

    assert fs.exists(lease) is True
    assert fs.exists(quarantine) is False
    fs.rename(lease, quarantine)          # creates .quarantine through a handle
    assert fs.exists(lease) is False
    assert fs.exists(quarantine) is True
    fs.remove_tree_no_follow(quarantine)
    assert fs.exists(quarantine) is False

    monkeypatch.undo()
    assert not lease.exists()
    assert not quarantine.exists()
    assert (pool / ".quarantine").is_dir()


@posix_only
def test_the_posix_branch_unlinks_a_link_and_leaves_its_target(tmp_path: Path) -> None:
    lease = tmp_path / "scratch" / "lease1"
    (lease / "repo").mkdir(parents=True)
    (lease / "repo" / "keep.txt").write_text("x", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("the host's data", encoding="utf-8")
    os.symlink(outside, lease / "repo" / "escape", target_is_directory=True)

    wfs.RealPoolFilesystem().remove_tree_no_follow(lease)

    assert not lease.exists()
    assert (outside / "precious.txt").read_text(encoding="utf-8") == "the host's data"


@posix_only
def test_removing_something_that_is_already_gone_is_a_no_op(tmp_path: Path) -> None:
    fs = wfs.RealPoolFilesystem()
    fs.remove_tree_no_follow(tmp_path / "never" / "existed")
    fs.remove_tree_no_follow(tmp_path / "gone")


@posix_only
def test_a_tree_deeper_than_the_cap_is_refused_rather_than_recursed(
    tmp_path: Path
) -> None:
    """Unbounded recursion through descriptors is a stack overflow waiting for
    a fixture; the cap makes it a refusal the lease can be LOST on."""
    deep = tmp_path / "scratch" / "lease1"
    current = deep
    for index in range(wfs._MAX_TREE_DEPTH + 3):
        current = current / f"d{index}"
    current.mkdir(parents=True)
    with pytest.raises(wfs.UnsafePoolPath, match="deeper than"):
        wfs.RealPoolFilesystem().remove_tree_no_follow(deep)


def test_the_windows_branch_is_selectable_and_still_deletes(tmp_path: Path) -> None:
    """The platform branch is injectable so both halves are testable on either
    host - a branch nobody can run is a branch nobody has checked."""
    lease = tmp_path / "lease1"
    (lease / "sub").mkdir(parents=True)
    (lease / "sub" / "file.txt").write_text("x", encoding="utf-8")
    wfs.RealPoolFilesystem(posix=False).remove_tree_no_follow(lease)
    assert not lease.exists()


# --------------------------------------------------------------------------
# create_lease_dir states its guarantee and enforces it (Codex P1 #6)
# --------------------------------------------------------------------------


@posix_only
def test_create_lease_dir_refuses_a_parent_anyone_could_write_to(tmp_path: Path) -> None:
    """The whole swap attack needs a parent someone else can rename into."""
    parent = tmp_path / "pool"
    parent.mkdir(mode=0o777)
    os.chmod(parent, 0o777)
    fd = wfs.open_dir_nofollow(parent)
    try:
        with pytest.raises(wfs.UnsafePoolPath, match="world-writable"):
            wfs.create_lease_dir(fd, GOOD_NAME)
    finally:
        os.close(fd)
    assert not (parent / GOOD_NAME).exists(), "it must refuse BEFORE creating anything"


@posix_only
def test_create_lease_dir_refuses_a_group_writable_parent(tmp_path: Path) -> None:
    parent = tmp_path / "pool"
    parent.mkdir()
    os.chmod(parent, 0o770)
    fd = wfs.open_dir_nofollow(parent)
    try:
        with pytest.raises(wfs.UnsafePoolPath, match="writable"):
            wfs.create_lease_dir(fd, GOOD_NAME)
    finally:
        os.close(fd)


@posix_only
def test_create_lease_dir_refuses_a_guessable_name(tmp_path: Path) -> None:
    """A name an attacker can predict is a name they can create first."""
    parent = tmp_path / "pool"
    parent.mkdir(mode=0o700)
    fd = wfs.open_dir_nofollow(parent)
    try:
        for name in ("lease1", "0123456789abcde", "0123456789abcdeg", "a" * 15):
            with pytest.raises(wfs.UnsafePoolPath, match="random hex"):
                wfs.create_lease_dir(fd, name)
        assert wfs.create_lease_dir(fd, GOOD_NAME) >= 0
    finally:
        os.close(fd)


@posix_only
def test_a_directory_swapped_in_before_the_first_stat_is_still_refused(
    tmp_path: Path, monkeypatch
) -> None:
    """The inode compare alone would MISS this: a rename that lands before the
    first stat is invisible to it, because both the stat and the open then see
    the intruder. What catches it is the handle having to be a fresh, empty,
    own-uid directory with exactly the mode we asked for."""
    parent = tmp_path / "pool"
    parent.mkdir(mode=0o700)
    decoy = tmp_path / "decoy"
    decoy.mkdir(mode=0o700)
    (decoy / "planted.txt").write_text("not ours", encoding="utf-8")

    original_mkdir = os.mkdir

    def _mkdir_then_swap(name, mode=0o777, *, dir_fd=None):
        original_mkdir(name, mode, dir_fd=dir_fd)
        # The swap lands BEFORE create_lease_dir's first stat.
        os.rename(str(decoy), str(parent / name))

    monkeypatch.setattr(os, "mkdir", _mkdir_then_swap)
    fd = wfs.open_dir_nofollow(parent)
    try:
        with pytest.raises(wfs.UnsafePoolPath, match="not the empty directory"):
            wfs.create_lease_dir(fd, GOOD_NAME)
    finally:
        os.close(fd)


@posix_only
def test_the_created_lease_dir_is_private_and_empty(tmp_path: Path) -> None:
    parent = tmp_path / "pool"
    parent.mkdir(mode=0o700)
    parent_fd = wfs.open_dir_nofollow(parent)
    try:
        fd = wfs.create_lease_dir(parent_fd, GOOD_NAME)
        try:
            info = os.fstat(fd)
            assert stat.S_ISDIR(info.st_mode)
            assert stat.S_IMODE(info.st_mode) == 0o700
            assert info.st_uid == os.getuid()
            assert os.listdir(fd) == []
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


@posix_only
def test_the_copy_cleanup_never_deletes_a_file_it_did_not_create(
    tmp_path: Path
) -> None:
    """Cleanup by NAME would delete whatever now answers to it. Only the inode
    it created is this call's to remove."""
    created = tmp_path / "staged.pack"
    created.write_text("mine", encoding="utf-8")
    info = os.lstat(created)
    replacement = tmp_path / "theirs.txt"
    replacement.write_text("somebody else's file", encoding="utf-8")
    os.replace(str(replacement), str(created))

    wfs._unlink_if_same_inode(str(created), info)

    assert created.read_text(encoding="utf-8") == "somebody else's file"

    # And when it IS the same inode, it goes.
    mine = tmp_path / "mine.pack"
    mine.write_text("mine", encoding="utf-8")
    wfs._unlink_if_same_inode(str(mine), os.lstat(mine))
    assert not mine.exists()


# --------------------------------------------------------------------------
# the Windows branch retries a delete that lost to a process still exiting
# --------------------------------------------------------------------------


def _sharing_violation(winerror: int = 32) -> PermissionError:
    """What Windows raises while another handle is still open on the file."""
    error = PermissionError(13, "the process cannot access the file")
    error.winerror = winerror
    return error


def _lease_tree(tmp_path: Path) -> Path:
    lease = tmp_path / "lease1"
    (lease / "repo" / ".git" / "objects").mkdir(parents=True)
    (lease / "repo" / ".git" / "objects" / "pack.idx").write_text("idx", encoding="utf-8")
    (lease / "repo" / "README.md").write_text("hello", encoding="utf-8")
    return lease


def test_a_transient_sharing_violation_is_retried_until_the_delete_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A git child that is still exiting holds a handle under .git/objects. The
    wipe used to give up on the first one, and the lease went LOST with its
    bytes charged forever."""
    lease = _lease_tree(tmp_path)
    victim = str(lease / "repo" / ".git" / "objects" / "pack.idx")
    attempts: list[str] = []
    real_unlink = os.unlink

    def flaky_unlink(path, **kwargs):
        if str(path) == victim:
            attempts.append(str(path))
            if len(attempts) <= 2:
                raise _sharing_violation()
        return real_unlink(path, **kwargs)

    monkeypatch.setattr(os, "unlink", flaky_unlink)
    slept: list[float] = []
    wfs.RealPoolFilesystem(
        posix=False, retry_total_s=5.0, retry_step_s=0.01, sleep=slept.append
    ).remove_tree_no_follow(lease)

    assert not lease.exists()
    # Three os.unlink calls, one wait: _unlink_windows already retries ONCE
    # after clearing the read-only bit, so the first _attempt spends two calls
    # before the bounded retry waits and the third lands.
    assert len(attempts) == 3, attempts
    assert slept == [0.01], slept


def test_a_permanent_failure_still_ends_the_lease_LOST(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrying is not swallowing: a handle nobody ever closes still has to
    surface, or the pool would report bytes it never freed."""
    lease = _lease_tree(tmp_path)
    victim = str(lease / "repo" / ".git" / "objects" / "pack.idx")
    attempts: list[str] = []
    real_unlink = os.unlink

    def stuck_unlink(path, **kwargs):
        if str(path) == victim:
            attempts.append(str(path))
            raise _sharing_violation()
        return real_unlink(path, **kwargs)

    monkeypatch.setattr(os, "unlink", stuck_unlink)
    filesystem = wfs.RealPoolFilesystem(
        posix=False, retry_total_s=0.05, retry_step_s=0.01, sleep=lambda _s: None
    )
    with pytest.raises(PermissionError):
        filesystem.remove_tree_no_follow(lease)
    assert len(attempts) >= 2, "it gave up without retrying at all"
    assert lease.exists()


def test_a_failure_that_is_not_transient_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the three winerrors a still-exiting process produces are waited on.
    Anything else is a real refusal and retrying it just costs three seconds."""
    lease = _lease_tree(tmp_path)
    victim = str(lease / "repo" / "README.md")
    attempts: list[str] = []
    real_unlink = os.unlink

    def refused(path, **kwargs):
        if str(path) == victim:
            attempts.append(str(path))
            error = PermissionError(13, "denied")
            error.winerror = 1920  # ERROR_CANT_ACCESS_FILE: not transient
            raise error
        return real_unlink(path, **kwargs)

    monkeypatch.setattr(os, "unlink", refused)
    slept: list[float] = []
    with pytest.raises(PermissionError):
        wfs.RealPoolFilesystem(
            posix=False, retry_total_s=5.0, retry_step_s=0.01, sleep=slept.append
        ).remove_tree_no_follow(lease)
    # Two calls, because _unlink_windows clears the read-only bit and tries
    # once more on its own; what matters is that the bounded retry never WAITED.
    assert attempts == [victim, victim], attempts
    assert slept == []


def test_the_read_only_bit_is_cleared_before_every_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A git child that is still writing can set it again between attempts, so
    clearing it once at the start is not enough."""
    lease = _lease_tree(tmp_path)
    victim = str(lease / "repo" / ".git" / "objects" / "pack.idx")
    chmods: list[str] = []
    attempts: list[str] = []
    real_unlink = os.unlink
    real_chmod = os.chmod

    def flaky_unlink(path, **kwargs):
        if str(path) == victim:
            attempts.append(str(path))
            if len(attempts) <= 2:
                raise _sharing_violation(5)
        return real_unlink(path, **kwargs)

    def spy_chmod(path, mode, **kwargs):
        chmods.append(str(path))
        return real_chmod(path, mode, **kwargs)

    monkeypatch.setattr(os, "unlink", flaky_unlink)
    monkeypatch.setattr(os, "chmod", spy_chmod)
    wfs.RealPoolFilesystem(
        posix=False, retry_total_s=5.0, retry_step_s=0.0, sleep=lambda _s: None
    ).remove_tree_no_follow(lease)
    assert chmods.count(victim) >= 2, chmods


def test_the_posix_branch_does_not_retry(tmp_path: Path, monkeypatch) -> None:
    """POSIX unlinks an open file without complaint, so a failure there is real
    and waiting on it would only delay the LOST."""
    if os.name != "posix":
        pytest.skip("the POSIX branch needs POSIX")
    lease = _lease_tree(tmp_path)
    victim = "pack.idx"
    attempts: list[str] = []
    real_unlink = os.unlink

    def stuck_unlink(path, **kwargs):
        if str(path) == victim:
            attempts.append(str(path))
            raise _sharing_violation()
        return real_unlink(path, **kwargs)

    monkeypatch.setattr(os, "unlink", stuck_unlink)
    slept: list[float] = []
    with pytest.raises(PermissionError):
        wfs.RealPoolFilesystem(sleep=slept.append).remove_tree_no_follow(lease)
    assert slept == [], "the POSIX branch waited on something"


def test_a_lease_the_wipe_cannot_free_is_reported_LOST(tmp_path: Path, monkeypatch) -> None:
    """The pool's side of the same story, end to end: a wipe that keeps failing
    marks the lease LOST, keeps its bytes charged, and still releases the locks
    so the next run is not blocked by a directory nobody can delete."""
    from tinyassets import workspace_pool as wp

    db = tmp_path / "runs.db"
    pool_root = tmp_path / "scratch"
    lease = wp.admit(
        db,
        universe_id="u1",
        connection_id="c1",
        repo_key="repo",
        storage_class="scratch",
        run_id="run-1",
        max_bytes=1024,
        pool_root=pool_root,
        universe_root=tmp_path / "universe",
    )
    (lease.path / "repo").mkdir(parents=True)
    (lease.path / "repo" / "pack.idx").write_text("idx", encoding="utf-8")

    import sqlite3

    conn = sqlite3.connect(str(db))
    try:
        conn.execute("BEGIN IMMEDIATE")
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
        conn.commit()
    finally:
        conn.close()

    real_unlink = os.unlink

    def stuck_unlink(path, **kwargs):
        if str(path).endswith("pack.idx"):
            raise _sharing_violation()
        return real_unlink(path, **kwargs)

    monkeypatch.setattr(os, "unlink", stuck_unlink)
    entry = wp.claim_next(db, claimant="test")
    assert entry is not None
    outcome = wp.process_entry(
        db,
        entry,
        fs=wfs.RealPoolFilesystem(
            posix=False, retry_total_s=0.05, retry_step_s=0.0, sleep=lambda _s: None
        ),
    )
    assert outcome == "lost"
    stored = wp.get_lease(db, lease.lease_id)
    assert stored is not None and stored.state == "LOST"
    assert wp.pool_usage(db).lost_bytes == lease.reserved_bytes


# --------------------------------------------------------------------------
# open_subdir_nofollow: one level, from a handle (the sink's actual need)
# --------------------------------------------------------------------------


@posix_only
def test_open_subdir_nofollow_descends_exactly_one_level(tmp_path: Path) -> None:
    (tmp_path / "workspaces").mkdir()
    parent = wfs.open_dir_nofollow(tmp_path)
    try:
        child = wfs.open_subdir_nofollow(parent, "workspaces")
        try:
            opened = os.fstat(child)
            on_disk = os.stat(tmp_path / "workspaces")
            assert (opened.st_dev, opened.st_ino) == (on_disk.st_dev, on_disk.st_ino)
        finally:
            os.close(child)
    finally:
        os.close(parent)


@posix_only
def test_open_subdir_nofollow_refuses_a_symlinked_child(tmp_path: Path) -> None:
    """The whole reason it exists: a caller holding the parent must not have
    the child resolved for it through a link somebody planted."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("the host's data", encoding="utf-8")
    os.symlink(outside, tmp_path / "workspaces", target_is_directory=True)
    parent = wfs.open_dir_nofollow(tmp_path)
    try:
        with pytest.raises(wfs.UnsafePoolPath):
            wfs.open_subdir_nofollow(parent, "workspaces")
    finally:
        os.close(parent)


@posix_only
def test_open_subdir_nofollow_refuses_a_file(tmp_path: Path) -> None:
    (tmp_path / "workspaces").write_text("not a directory", encoding="utf-8")
    parent = wfs.open_dir_nofollow(tmp_path)
    try:
        with pytest.raises(wfs.UnsafePoolPath):
            wfs.open_subdir_nofollow(parent, "workspaces")
    finally:
        os.close(parent)


@posix_only
def test_open_subdir_nofollow_refuses_anything_but_one_component(
    tmp_path: Path,
) -> None:
    (tmp_path / "workspaces").mkdir()
    parent = wfs.open_dir_nofollow(tmp_path)
    try:
        for name, fragment in (
            ("..", "traversal"),
            (".", "traversal"),
            ("workspaces/repo", "separator"),
            ("", "non-empty"),
        ):
            with pytest.raises(wfs.UnsafePoolPath, match=fragment):
                wfs.open_subdir_nofollow(parent, name)
    finally:
        os.close(parent)


@posix_only
def test_the_fstat_is_what_refuses_a_file_where_O_DIRECTORY_is_absent(
    tmp_path: Path, monkeypatch
) -> None:
    """O_DIRECTORY does the work on Linux, which would make the fstat a check
    that cannot go red. It is load-bearing exactly where the flag is missing -
    the module falls back to 0 for it - so that is the condition to test under.
    """
    (tmp_path / "workspaces").write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(wfs, "_O_DIRECTORY", 0)
    parent = wfs.open_dir_nofollow(tmp_path)
    try:
        with pytest.raises(wfs.UnsafePoolPath, match="not a directory"):
            wfs.open_subdir_nofollow(parent, "workspaces")
    finally:
        os.close(parent)


@posix_only
def test_open_subdir_nofollow_reports_a_missing_child_as_itself(
    tmp_path: Path,
) -> None:
    """Absent is not unsafe: the caller decides whether to create it, and a
    FileNotFoundError is what says which case this is."""
    parent = wfs.open_dir_nofollow(tmp_path)
    try:
        with pytest.raises(FileNotFoundError):
            wfs.open_subdir_nofollow(parent, "nothing-here")
    finally:
        os.close(parent)


@windows_only
def test_open_subdir_nofollow_refuses_loudly_off_posix(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError, match="POSIX"):
        wfs.open_subdir_nofollow(0, "workspaces")


@posix_only
@pytest.mark.parametrize("selected", [False, True])
def test_universe_read_through_a_symlinked_root_follows_only_when_off(
    tmp_path, monkeypatch, selected,
):
    from tinyassets.broker.supervisor import ENV_SWITCH, PROCESS
    from tinyassets.universe_files import read_universe_file

    if selected:
        monkeypatch.setenv(ENV_SWITCH, PROCESS)
    else:
        monkeypatch.delenv(ENV_SWITCH, raising=False)
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "soul.md").write_bytes(b"SOUL")
    (tmp_path / "alias").symlink_to(tmp_path / "real", target_is_directory=True)
    if selected:
        with pytest.raises(OSError):
            read_universe_file(tmp_path / "alias", "soul.md")
    else:
        assert read_universe_file(tmp_path / "alias", "soul.md") == b"SOUL"
