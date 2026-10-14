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
        pytest.skip("requires linux_oracle.py --as-root")
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
