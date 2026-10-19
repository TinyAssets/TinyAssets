"""C1 replaces project-tree quotas with the opaque image's size."""

from types import SimpleNamespace

import pytest

from tinyassets.boxes.volumes import OwnerVolumes, VolumeError


@pytest.fixture
def volumes(tmp_path, monkeypatch):
    calls = []
    def run(*args):
        calls.append(args)
        return SimpleNamespace(stdout="reflink=1" if args[0] == "xfs_info" else "")
    monkeypatch.setattr(OwnerVolumes, "_run", staticmethod(run))
    monkeypatch.setattr(OwnerVolumes, "_mounts", staticmethod(lambda: []))
    result = OwnerVolumes(tmp_path / "store", tmp_path / "mounts", "proof:synthetic")
    return result, calls


def test_plan_quota_is_image_length_and_no_project_tree_is_constructed(volumes):
    volume, calls = volumes
    volume.create(300001, 2 * 1024**3)
    assert volume.size(300001) == 2 * 1024**3
    assert not (volume.mount_root / "300001").exists()
    assert any(args[0] == "mkfs.xfs" for args in calls)
    assert not any(args[0] == "xfs_quota" for args in calls)
    with pytest.raises(FileExistsError):
        volume.create(300001, 4 * 1024**3)
    assert volume.size(300001) == 2 * 1024**3


@pytest.mark.parametrize("uid", [0, 300000, 400000, "300001", True])
def test_only_permanent_admitted_uid_can_address_an_image(volumes, uid):
    volume, _ = volumes
    with pytest.raises(VolumeError, match="UID"):
        volume.create(uid, 2 * 1024**3)


def test_growth_changes_only_image_length_and_never_shrinks(volumes, monkeypatch):
    volume, calls = volumes
    volume.create(300001, 2 * 1024**3)
    monkeypatch.setattr(volume, "_loops", lambda uid: ["/dev/loop7"])
    monkeypatch.setattr(volume, "_mounts", lambda: [
        (str(volume.mount_root / "300001"), "/dev/loop7")])
    volume.grow(300001, 3 * 1024**3)
    assert volume.size(300001) == 3 * 1024**3
    assert calls[-2:] == [("losetup", "--set-capacity", "/dev/loop7"),
                         ("xfs_growfs", str(volume.mount_root / "300001"))]
    with pytest.raises(VolumeError, match="shrink"):
        volume.grow(300001, 2 * 1024**3)
    assert volume.size(300001) == 3 * 1024**3


def test_outer_store_requires_reflink(tmp_path, monkeypatch):
    monkeypatch.setattr(OwnerVolumes, "_run", staticmethod(
        lambda *args: SimpleNamespace(stdout="reflink=0")))
    with pytest.raises(VolumeError, match="reflink"):
        OwnerVolumes(tmp_path / "store", tmp_path / "mounts", "proof:synthetic")
