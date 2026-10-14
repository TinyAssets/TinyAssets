"""Quota setup accepts the mount options XFS actually prints."""

from __future__ import annotations

import os
import subprocess

import pytest

from tinyassets.boxes.provider import BoxError
from tinyassets.boxes.quota import OwnerQuota

pytestmark = pytest.mark.skipif(os.name != "posix", reason="XFS project quotas are Linux-only")


def test_quota_uses_permanent_owner_uid_and_real_findmnt_options(tmp_path, monkeypatch):
    if os.geteuid() != 0:
        pytest.skip(
            "requires linux_oracle.py --as-root; owner=c6-owner-box runs-in=linux_oracle.py"
        )
    store = tmp_path / "owners"
    store.mkdir()
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "findmnt":
            # XFS shows prjquota; nodiscard is its default and is not printed.
            return subprocess.CompletedProcess(argv, 0, "xfs rw,relatime,attr2,inode64,prjquota\n")
        return subprocess.CompletedProcess(argv, 0, "")

    monkeypatch.setattr("tinyassets.boxes.quota.subprocess.run", fake_run)
    tree = OwnerQuota(store).set_bound(300001, 300001, 1024 * 1024)
    assert (tree.stat().st_uid, tree.stat().st_gid, tree.stat().st_mode & 0o777) == (
        300001, 300001, 0o700,
    )
    assert calls[1] == ["xfs_quota", "-x", "-c", f"project -s -p {tree} 300001", str(store)]
    assert calls[2] == ["xfs_quota", "-x", "-c", "limit -p bhard=1024k 300001", str(store)]


def test_quota_rejects_mount_without_project_quotas(tmp_path, monkeypatch):
    store = tmp_path / "owners"
    store.mkdir()
    monkeypatch.setattr(
        "tinyassets.boxes.quota.subprocess.run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "xfs rw,relatime\n"),
    )
    with pytest.raises(BoxError, match="project quotas"):
        OwnerQuota(store).set_bound(300001, 300001, 1024 * 1024)


@pytest.mark.parametrize("project_ok", [False, True])
def test_existing_quota_admission_never_retags_tree(tmp_path, monkeypatch, project_ok):
    import fcntl
    import struct

    # This unit test substitutes only the permanent range check and kernel
    # ioctl result so it runs unprivileged. Real XFS proof is still required.
    from tinyassets.boxes.gvisor_runtime import GvisorRuntime

    uid = os.geteuid()
    tree = tmp_path / str(uid)
    tree.mkdir(mode=0o700)
    monkeypatch.setattr(GvisorRuntime, "box_id", lambda value: "unit-test")
    monkeypatch.setattr(OwnerQuota, "_mounted", lambda self: None)
    monkeypatch.setattr(fcntl, "ioctl", lambda *args: struct.pack(
        "=IIIII8s", 0x200, 0, 0, uid if project_ok else uid + 1, 0, b"\0" * 8))
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: calls.append(argv))
    if project_ok:
        OwnerQuota(tmp_path).admit(uid, 1024**3)
        assert calls == [["xfs_quota", "-x", "-c", f"limit -p bhard=1048576k {uid}",
                          str(tmp_path)]]
    else:
        with pytest.raises(BoxError, match="quota project"):
            OwnerQuota(tmp_path).admit(uid, 1024**3)
        assert not calls
