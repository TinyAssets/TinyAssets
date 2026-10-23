"""Box harness retention: unreferenced rootfs copies are reclaimable."""

from __future__ import annotations

import json
import os
import stat
from collections import namedtuple
from contextlib import contextmanager
from pathlib import Path

import pytest

from scripts.daemon_image_retention import (
    Refusal,
    check_pull_headroom,
    image_refs,
    main,
    prune_box_harnesses,
    read_receipt,
    repair_host_symlink_modes,
    retain,
)

pytestmark = pytest.mark.verifies("owner-daemon-in-box:C4")

IMAGE = "sha256:" + "ab" * 32
OTHER = "sha256:" + "cd" * 32
CONFIGURED = "sha256:" + "ef" * 32
REF = "ghcr.io/tinyassets/tinyassets-daemon@" + IMAGE
OTHER_REF = "ghcr.io/tinyassets/tinyassets-daemon@" + OTHER
CONFIGURED_REF = "ghcr.io/tinyassets/tinyassets-daemon@" + CONFIGURED


def _seal(path: Path) -> None:
    payload = path / "app" / "payload"
    payload.parent.mkdir(parents=True)
    payload.write_text("sealed\n", encoding="utf-8")
    payload.chmod(0o444)
    payload.parent.chmod(0o555)
    path.chmod(0o555)
    assert stat.S_IMODE(path.stat().st_mode) == 0o555


def _name(digest: str) -> str:
    return "harness-" + digest.removeprefix("sha256:")


def test_missing_harness_root_removes_nothing(tmp_path: Path):
    missing = tmp_path / "absent"
    assert prune_box_harnesses({"Image": IMAGE}, REF, {REF: IMAGE}, root=missing) == []
    assert not missing.exists()


def test_prune_removes_readonly_copies_and_keeps_live_ones(tmp_path: Path):
    root = tmp_path / "boxhostd"
    root.mkdir()
    current = _name(IMAGE)
    running = _name(OTHER)
    configured = _name(CONFIGURED)
    leaked = "harness-" + "12" * 32
    for name in (current, running, configured, leaked):
        _seal(root / name)
    stage = root / ".harness-stage.deadbeef"
    _seal(stage)
    (root / "notes").mkdir()
    (root / "notes" / "keep").write_text("keep\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "safe").write_text("safe\n", encoding="utf-8")
    link_name = "harness-" + "34" * 32
    (root / link_name).symlink_to(outside, target_is_directory=True)
    (root / "harness-current").symlink_to(current)

    removed = prune_box_harnesses(
        {"Image": OTHER},
        CONFIGURED_REF,
        {REF: IMAGE, OTHER_REF: OTHER, CONFIGURED_REF: CONFIGURED},
        root=root,
    )
    assert set(removed) == {leaked, stage.name}
    for name in (current, running, configured):
        assert (root / name / "app" / "payload").read_text(encoding="utf-8") == "sealed\n"
        assert stat.S_IMODE((root / name).stat().st_mode) == 0o555
    assert (root / "harness-current").readlink() == Path(current)
    assert (root / "notes" / "keep").read_text(encoding="utf-8") == "keep\n"
    assert (root / link_name).is_symlink()
    assert (outside / "safe").read_text(encoding="utf-8") == "safe\n"


def test_prune_deletes_a_readonly_rootfs_without_following_symlinks(tmp_path: Path):
    """Symlinks in a container rootfs are not chmod targets and are not followed."""
    root = tmp_path / "boxhostd"
    root.mkdir()
    leaked = root / ("harness-" + "78" * 32)
    nested = leaked / "usr" / "bin"
    nested.mkdir(parents=True)
    payload = nested / "tool"
    payload.write_text("bin\n", encoding="utf-8")
    outside = tmp_path / "host-file"
    outside.write_text("host\n", encoding="utf-8")
    outside.chmod(0o444)
    outside_mode = stat.S_IMODE(outside.stat().st_mode)
    (nested / "broken").symlink_to("/nonexistent/path")
    (nested / "loop").symlink_to("loop")
    (nested / "etc-host").symlink_to(outside)
    payload.chmod(0o444)
    nested.chmod(0o555)
    (leaked / "usr").chmod(0o555)
    leaked.chmod(0o555)

    removed = prune_box_harnesses({"Image": IMAGE}, REF, {REF: IMAGE}, root=root)

    assert removed == [leaked.name]
    assert not leaked.exists()
    assert outside.is_file()
    assert stat.S_IMODE(outside.stat().st_mode) == outside_mode
    assert outside.read_text(encoding="utf-8") == "host\n"


def test_restarting_daemon_still_names_the_current_image(tmp_path: Path):
    mount = tmp_path / "data"
    mount.mkdir()
    (mount / "release-state.json").write_text(
        json.dumps({"rollback_target": REF}), encoding="utf-8",
    )

    class Docker:
        def json(self, *args):
            assert args == ("volume", "inspect", "tinyassets-data")
            return [{"Mountpoint": str(mount)}]

    containers = [{
        "Name": "/tinyassets-daemon",
        "State": {"Running": False, "Restarting": True},
        "Config": {"Env": []},
        "Mounts": [{
            "Destination": "/data", "Type": "volume", "Name": "tinyassets-data",
            "Source": str(mount),
        }],
        "Image": IMAGE,
    }]
    daemon, receipt = read_receipt(Docker(), containers)
    assert daemon["Image"] == IMAGE
    assert receipt["rollback_target"] == REF

    stopped = json.loads(json.dumps(containers))
    stopped[0]["State"] = {"Running": False, "Restarting": False}
    with pytest.raises(Refusal, match="current_daemon_unavailable"):
        read_receipt(Docker(), stopped)


def test_retain_reclaims_harnesses_before_it_returns(tmp_path: Path, monkeypatch):
    root = tmp_path / "boxhostd"
    leaked = root / ("harness-" + "56" * 32)
    _seal(leaked)
    (root / "harness-current").symlink_to(_name(IMAGE))
    live = root / _name(IMAGE)
    _seal(live)
    monkeypatch.setenv("TINYASSETS_BOX_HARNESS_ROOT", str(root))
    row = {
        "Id": IMAGE,
        "RepoDigests": [REF],
        "RepoTags": [REF],
        "Created": "2026-10-01T00:00:00+00:00",
    }

    class Docker:
        deadline = 0

        def json(self, *args):
            assert args[0] == "info"
            return {
                "Driver": "overlay2",
                "DockerRootDir": "/var/lib/docker",
                "OSType": "linux",
                "Architecture": "x86_64",
            }

        def inventory(self):
            return [row], [{"Image": IMAGE, "Name": "/tinyassets-daemon"}]

        def command(self, *args):
            raise AssertionError(args)

    @contextmanager
    def locker():
        yield

    report = retain(
        dry_run=False,
        mode="count",
        docker=Docker(),
        locker=locker,
        receipt_reader=lambda _docker, _containers: (
            {"Image": IMAGE}, {"rollback_target": REF},
        ),
        config_reader=lambda: REF,
        measure=lambda _path: 10.0,
    )
    assert report["status"] == "retained"
    assert report["harnesses_removed"] == [leaked.name]
    assert not leaked.exists()
    assert live.is_dir()
    assert stat.S_IMODE(live.stat().st_mode) == 0o555
    assert image_refs([row]) == {REF: IMAGE}


def _guard_chmod(monkeypatch, host: Path) -> None:
    """Refuse a repair that resolves a fixture link onto the machine running it."""
    root = os.path.realpath(host)
    real_chmod = os.chmod

    def guarded(path, mode):
        resolved = os.path.realpath(path)
        if resolved != root and not resolved.startswith(root + os.sep):
            raise AssertionError(f"chmod escaped the fixture: {path}")
        real_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", guarded)


def _damaged_host(tmp_path: Path) -> tuple[Path, Path, dict[str, Path]]:
    host = tmp_path / "host"
    mawk = host / "usr/bin/mawk"
    mawk.parent.mkdir(parents=True)
    mawk.write_bytes(b"\x7fELF" + b"\x00" * 12)
    mawk.chmod(0o600)
    nawk = host / "usr/bin/nawk"
    nawk.write_bytes(b"\x7fELF" + b"\x00" * 12)
    nawk.chmod(0o700)
    script = host / "usr/bin/pager"
    script.write_bytes(b"#!/bin/sh\ntrue\n")
    script.chmod(0o600)
    cert = host / "etc/ssl/certs/ca-certificates.crt"
    cert.parent.mkdir(parents=True)
    cert.write_bytes(b"not a program\n")
    cert.chmod(0o600)
    lib = host / "usr/lib/ssl"
    lib.mkdir(parents=True)
    lib.chmod(0o700)
    secret = host / "etc/ssh/ssh_host_key"
    secret.parent.mkdir(parents=True)
    secret.write_bytes(b"secret")
    secret.chmod(0o600)
    note = host / "usr/share/doc-note"
    note.parent.mkdir(parents=True)
    note.write_bytes(b"not a program\n")
    note.chmod(0o700)
    harness_root = host / "opt/tinyassets/boxhostd"
    leaked = harness_root / ("harness-" + "12" * 32) / "usr/bin"
    leaked.mkdir(parents=True)
    (leaked / "awk").symlink_to("/usr/bin/mawk")
    (leaked / "pager").symlink_to("/usr/bin/pager")
    (leaked / "certs").symlink_to("/etc/ssl/certs/ca-certificates.crt")
    (leaked / "ssl").symlink_to("/usr/lib/ssl")
    (leaked / "sshkey").symlink_to("/etc/ssh/ssh_host_key")
    return host, harness_root, {
        "mawk": mawk, "nawk": nawk, "script": script, "cert": cert, "lib": lib,
        "secret": secret, "note": note,
    }


def test_repair_host_symlink_modes_restores_damaged_targets(tmp_path: Path, monkeypatch):
    host, harness_root, paths = _damaged_host(tmp_path)
    _guard_chmod(monkeypatch, host)

    repaired = repair_host_symlink_modes(harness_root, host_root=host)

    assert stat.S_IMODE(paths["mawk"].stat().st_mode) == 0o755
    assert stat.S_IMODE(paths["nawk"].stat().st_mode) == 0o755
    assert stat.S_IMODE(paths["script"].stat().st_mode) == 0o755
    assert stat.S_IMODE(paths["note"].stat().st_mode) == 0o700
    assert stat.S_IMODE(paths["cert"].stat().st_mode) == 0o644
    assert stat.S_IMODE(paths["lib"].stat().st_mode) == 0o755
    assert stat.S_IMODE(paths["secret"].stat().st_mode) == 0o600
    assert "/usr/bin/mawk" in repaired
    assert "/etc/ssl/certs/ca-certificates.crt" in repaired
    assert "/usr/lib/ssl" in repaired
    assert "/etc/ssh/ssh_host_key" not in repaired


def test_prune_repairs_host_modes_before_deleting_the_harness(tmp_path: Path, monkeypatch):
    host, harness_root, paths = _damaged_host(tmp_path)
    _guard_chmod(monkeypatch, host)

    removed = prune_box_harnesses(
        {"Image": IMAGE}, REF, {REF: IMAGE}, root=harness_root,
        repair_host=True, host_root=host,
    )

    assert removed == ["harness-" + "12" * 32]
    assert not (harness_root / ("harness-" + "12" * 32)).exists()
    assert stat.S_IMODE(paths["mawk"].stat().st_mode) == 0o755
    assert stat.S_IMODE(paths["nawk"].stat().st_mode) == 0o755
    assert stat.S_IMODE(paths["cert"].stat().st_mode) == 0o644
    assert stat.S_IMODE(paths["lib"].stat().st_mode) == 0o755
    assert stat.S_IMODE(paths["secret"].stat().st_mode) == 0o600


def test_check_pull_headroom_repairs_when_asked(tmp_path: Path, monkeypatch):
    host, _harness_root, paths = _damaged_host(tmp_path)
    _guard_chmod(monkeypatch, host)
    usage = namedtuple("Usage", "total used free")(100 * 1024**3, 10 * 1024**3, 90 * 1024**3)

    class Docker:
        def json(self, *args):
            return {
                "Driver": "overlay2",
                "DockerRootDir": "/var/lib/docker",
                "OSType": "linux",
                "Architecture": "x86_64",
            }

    check_pull_headroom(docker=Docker(), disk_fn=lambda _path: usage, repair_host_root=host)

    assert stat.S_IMODE(paths["mawk"].stat().st_mode) == 0o755
    assert stat.S_IMODE(paths["nawk"].stat().st_mode) == 0o755
    assert stat.S_IMODE(paths["cert"].stat().st_mode) == 0o644
    assert stat.S_IMODE(paths["lib"].stat().st_mode) == 0o755


def test_apply_repairs_host_modes_before_retain_can_refuse(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        "scripts.daemon_image_retention.repair_host_symlink_modes",
        lambda *args, **kwargs: calls.append("repair") or [],
    )

    def retain(**kwargs):
        assert calls == ["repair"]
        raise Refusal("current_daemon_unavailable")

    monkeypatch.setattr("scripts.daemon_image_retention.retain", retain)
    monkeypatch.setenv("TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY", "1")
    assert main(["--apply", "--prepare-pull"]) == 2
    assert calls == ["repair"]
