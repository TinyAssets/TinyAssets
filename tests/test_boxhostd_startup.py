"""The entry point delegates before it reads layout or starts either service."""

import os
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from deploy import boxhostd


@pytest.fixture
def cgroup(tmp_path, monkeypatch):
    parent = tmp_path / "boxhostd.service"
    parent.mkdir()
    (parent / "cgroup.controllers").write_text("cpu memory pids io")
    (parent / "cgroup.procs").write_text(str(os.getpid()))
    (parent / "cgroup.subtree_control").write_text("")
    membership = tmp_path / "membership"
    membership.write_text("0::/system.slice/boxhostd.service\n")
    monkeypatch.setattr(boxhostd, "Path", lambda value: {
        "/proc/self/cgroup": membership, "/sys/fs/cgroup": tmp_path,
        "/system.slice/boxhostd.service": Path("boxhostd.service"),
    }.get(value, Path(value)))
    # The production join is relative to the cgroup mount.
    (tmp_path / "system.slice").mkdir()
    parent.rename(tmp_path / "system.slice/boxhostd.service")
    parent = tmp_path / "system.slice/boxhostd.service"
    original = Path.write_text
    writes = []

    def kernel_write(path, value, *args, **kwargs):
        writes.append((path, value))
        if path == parent / "supervisor/cgroup.procs":
            original(parent / "cgroup.procs", "")
        if path.name == "cgroup.subtree_control":
            assert not (parent / "cgroup.procs").read_text().strip()
            value = value.replace("+", "")
        return original(path, value, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", kernel_write)
    return parent, membership, writes


def test_delegation_moves_self_before_enabling_controllers(cgroup):
    parent, _, writes = cgroup
    assert boxhostd.prepare_delegation() == parent
    assert writes == [(parent / "supervisor/cgroup.procs", str(os.getpid())),
                      (parent / "cgroup.subtree_control", "+cpu +memory +pids")]
    assert not (parent / "cgroup.procs").read_text()


@pytest.mark.parametrize("failure", ["wrong-unit", "v1", "controllers", "permission"])
def test_delegation_failure_is_fatal(cgroup, monkeypatch, failure):
    parent, membership, _ = cgroup
    if failure == "wrong-unit":
        membership.write_text("0::/system.slice/other.service\n")
    elif failure == "v1":
        membership.write_text("1:cpu:/system.slice/boxhostd.service\n")
    elif failure == "controllers":
        (parent / "cgroup.controllers").write_text("cpu pids")
    else:
        def refuse(*args, **kwargs):
            raise PermissionError("delegation denied")
        monkeypatch.setattr(Path, "mkdir", refuse)
    with pytest.raises((RuntimeError, PermissionError)):
        boxhostd.prepare_delegation()
    assert not (parent / "cgroup.subtree_control").read_text()


def test_failed_delegation_prevents_even_layout_discovery(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    def refuse():
        raise RuntimeError("delegation denied")
    monkeypatch.setattr(boxhostd, "prepare_delegation", refuse)
    monkeypatch.setattr(boxhostd, "data_root", lambda: pytest.fail("layout reached"))
    with pytest.raises(RuntimeError, match="delegation denied"):
        boxhostd.main()


@pytest.mark.parametrize("failure", ["populated", "ignored-controllers"])
def test_delegation_refuses_incomplete_kernel_transition(cgroup, monkeypatch, failure):
    parent, _, _ = cgroup
    original = Path.write_text

    def incomplete(path, value, *args, **kwargs):
        if failure == "ignored-controllers" and path.name == "cgroup.subtree_control":
            return len(value)
        result = original(path, value, *args, **kwargs)
        if failure == "populated" and path == parent / "supervisor/cgroup.procs":
            original(parent / "cgroup.procs", "999")
        return result

    monkeypatch.setattr(Path, "write_text", incomplete)
    with pytest.raises(RuntimeError, match=("still populated" if failure == "populated"
                                           else "did not take effect")):
        boxhostd.prepare_delegation()
    assert not (parent / "cgroup.subtree_control").read_text()


@pytest.mark.parametrize("committed", [False, True])
def test_both_layouts_use_the_same_delegated_entry(monkeypatch, tmp_path, committed):
    calls = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["boxhostd", "--data-root", str(tmp_path)])
    parent = Path("/sys/fs/cgroup/system.slice/boxhostd.service")
    monkeypatch.setattr(boxhostd, "prepare_delegation",
                        lambda: calls.append("delegate") or parent)
    monkeypatch.setattr(boxhostd, "owner_box_committed",
                        lambda root: calls.append("layout") or committed)
    monkeypatch.setitem(sys.modules, "boxhostd_idle", SimpleNamespace(
        main=lambda: calls.append("idle")))
    rootfs = tmp_path / "harness"
    (rootfs / "app").mkdir(parents=True)
    monkeypatch.setattr(boxhostd, "HARNESS", rootfs)
    def serve(**kwargs):
        assert kwargs == {"rootfs": rootfs, "data_root": tmp_path, "cgroup_parent": parent}
        calls.append("serve")
    monkeypatch.setitem(sys.modules, "tinyassets.boxes.host_service", SimpleNamespace(main=serve))
    old = sys.path[:]
    try:
        boxhostd.main()
    finally:
        sys.path[:] = old
    assert calls == ["delegate", "layout", "serve" if committed else "idle"]


def test_idle_script_enters_the_production_entry(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "boxhostd", SimpleNamespace(main=lambda: calls.append(1)))
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "deploy/boxhostd_idle.py"),
                   run_name="__main__")
    assert calls == [1]


def test_import_path_follows_the_harness_current_symlink(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["boxhostd", "--data-root", str(tmp_path)])
    parent = Path("/sys/fs/cgroup/system.slice/boxhostd.service")
    monkeypatch.setattr(boxhostd, "prepare_delegation", lambda: parent)
    monkeypatch.setattr(boxhostd, "owner_box_committed", lambda root: True)
    monkeypatch.setitem(sys.modules, "boxhostd_idle", SimpleNamespace(main=lambda: None))
    target = tmp_path / ("harness-" + "ab" * 32)
    (target / "app").mkdir(parents=True)
    link = tmp_path / "harness-current"
    link.symlink_to(target.name)
    monkeypatch.setattr(boxhostd, "HARNESS", link)
    seen = []

    def serve(**kwargs):
        assert kwargs == {
            "rootfs": link.resolve(strict=True),
            "data_root": tmp_path,
            "cgroup_parent": parent,
        }
        seen.append(sys.path[0])

    monkeypatch.setitem(
        sys.modules, "tinyassets.boxes.host_service", SimpleNamespace(main=serve))
    old = sys.path[:]
    try:
        boxhostd.main()
    finally:
        sys.path[:] = old
    assert seen == [str(link / "app")]
    assert Path(seen[0]).resolve() == target / "app"
    assert seen[0] != str(target / "app")


def test_systemd_allows_delegation_and_does_not_move_main_to_a_leaf():
    unit = (Path(__file__).resolve().parents[1] / "deploy/boxhostd.service").read_text()
    assert "Delegate=yes" in unit
    assert "DelegateSubgroup=" not in unit  # entry point owns the only evacuation path
    assert "ProtectControlGroups=yes" not in unit
    assert "ExecStart=/usr/bin/python3 /opt/tinyassets/boxhostd/boxhostd.py" in unit


pytestmark = pytest.mark.verifies('owner-daemon-in-box:T4')
