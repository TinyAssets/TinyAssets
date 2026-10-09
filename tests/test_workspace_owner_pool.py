"""The pool directory a lease is created in (``workspace_owner_pool``).

This is the one place the owner split's two hard facts are reconciled: the
daemon cannot make a directory the owner owns (no CAP_CHOWN, and a command
center root admits the owner r-x), and a node sandbox will mount nothing that
is not the owner's. So the DAEMON owns the directory above the lease and
labels it -- this owner rwx and nobody else, with the daemon granted rwx on
everything created below -- and the owner's CELL creates the lease inside it.

Both ACLs are asserted by their bytes. A label that silently does not apply
is the failure mode that matters: without the access entry the cell cannot
create its lease, and without the default entry the daemon cannot open,
measure or reclaim what the cell made.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from tinyassets import role_modes, workspace_owner_pool
from tinyassets.workspace_fs import UnsafePoolPath, create_cell_lease_dir, open_dir_nofollow

ACCESS, DEFAULT = "system.posix_acl_access", "system.posix_acl_default"
MACHINE = 300001

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX ACLs and openat semantics")


@pytest.fixture()
def center(tmp_path: Path) -> Path:
    value = tmp_path / "data" / "cc-alice"
    value.mkdir(parents=True)
    return value


def _acl(owner, named, *, mask):
    from tinyassets.role_center_admission import _acl as pack

    return pack(owner, named, mask=mask)


def test_prepare_labels_every_component_for_exactly_this_owner(center: Path) -> None:
    parent = workspace_owner_pool.prepare(center, ("workspaces", "scratch"), machine=MACHINE)

    assert parent == center / "workspaces" / "scratch"
    for path in (center / "workspaces", parent):
        fd = open_dir_nofollow(path)
        try:
            assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o770
            assert os.getxattr(fd, ACCESS) == _acl(7, {MACHINE: 7}, mask=7)
            assert os.getxattr(fd, DEFAULT) == _acl(
                7, {role_modes.DAEMON_UID: 7}, mask=7)
        finally:
            os.close(fd)


def test_prepare_is_idempotent_and_relabels_what_it_finds(center: Path) -> None:
    """A second operation must not refuse, and must not leave a loosened label.

    Forward migration resets these ACLs (the entry is platform-labelled while
    it is the daemon's), so the preparation has to be able to put them back.
    """
    workspace_owner_pool.prepare(center, ("workspaces", "scratch"), machine=MACHINE)
    fd = open_dir_nofollow(center / "workspaces" / "scratch")
    try:
        os.fchmod(fd, 0o777)
        os.removexattr(fd, ACCESS)
    finally:
        os.close(fd)

    workspace_owner_pool.prepare(center, ("workspaces", "scratch"), machine=MACHINE)

    fd = open_dir_nofollow(center / "workspaces" / "scratch")
    try:
        assert os.getxattr(fd, ACCESS) == _acl(7, {MACHINE: 7}, mask=7)
        assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o770
    finally:
        os.close(fd)


def test_the_label_lets_the_cell_create_a_lease_the_daemon_can_still_open(
    center: Path,
) -> None:
    """The whole point, end to end on one filesystem.

    In production the creator is the owner's cell and the reader is the
    daemon; here they are the same uid, so what this proves is the mechanical
    half -- the inherited default ACL survives onto the lease, keeping a named
    rwx entry for the daemon with a mask that does not cancel it.
    """
    parent = workspace_owner_pool.prepare(
        center, ("workspaces", "scratch"), machine=MACHINE)
    fd = open_dir_nofollow(parent)
    try:
        lease_fd = create_cell_lease_dir(fd, "a" * 32)
    finally:
        os.close(fd)
    try:
        assert stat.S_IMODE(os.fstat(lease_fd).st_mode) == 0o770, "the mask would be zero"
        inherited = os.getxattr(lease_fd, ACCESS)
        assert _acl(7, {role_modes.DAEMON_UID: 7}, mask=7) == inherited
        assert os.getxattr(lease_fd, DEFAULT), "descendants keep the daemon's access too"
    finally:
        os.close(lease_fd)


@pytest.mark.parametrize("parts", [(), ("",), ("..",), ("workspaces", "a/b"), ("a\\b",)])
def test_a_pool_parent_is_one_or_more_plain_names(center: Path, parts) -> None:
    with pytest.raises(ValueError):
        workspace_owner_pool.prepare(center, parts, machine=MACHINE)


def test_a_component_that_is_not_a_directory_refuses(center: Path) -> None:
    (center / "workspaces").write_text("not a directory")
    with pytest.raises((UnsafePoolPath, OSError)):
        workspace_owner_pool.prepare(center, ("workspaces", "scratch"), machine=MACHINE)


def test_a_missing_command_center_is_never_conjured(tmp_path: Path) -> None:
    """Only admission creates a command center root."""
    with pytest.raises((FileNotFoundError, UnsafePoolPath)):
        workspace_owner_pool.prepare(
            tmp_path / "data" / "absent", ("workspaces",), machine=MACHINE)


def test_the_pool_parts_follow_the_storage_class() -> None:
    assert workspace_owner_pool.pool_parts("scratch", "ignored") == ("workspaces", "scratch")
    assert workspace_owner_pool.pool_parts("universe", "h--o--n") == ("workspaces", "h--o--n")
    with pytest.raises(ValueError):
        workspace_owner_pool.pool_parts("elsewhere", "h--o--n")
