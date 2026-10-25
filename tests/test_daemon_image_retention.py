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
    KEEP_OLDER,
    PULL_FREE_BYTES,
    Refusal,
    candidates,
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


def _headroom_docker():
    class Docker:
        def json(self, *args):
            assert args[0] == "info"
            return {
                "Driver": "overlay2",
                "DockerRootDir": "/var/lib/docker",
                "OSType": "linux",
                "Architecture": "x86_64",
            }

    return Docker()


def test_pull_headroom_uses_the_post_cutover_root_reserve():
    usage = namedtuple("Usage", "total used free")
    total = 468 * 1024**3 // 10  # 46.8 GiB root after the C6 harness extract
    free = total - (85 * total // 100)
    assert free > PULL_FREE_BYTES
    check_pull_headroom(
        docker=_headroom_docker(),
        disk_fn=lambda _path: usage(total, total - free, free),
    )
    big = 200 * 1024**3
    free_at_ninety = big // 10
    assert free_at_ninety > PULL_FREE_BYTES
    with pytest.raises(Refusal, match="deploy_disk_headroom_insufficient"):
        check_pull_headroom(
            docker=_headroom_docker(),
            disk_fn=lambda _path: usage(big, big - free_at_ninety, free_at_ninety),
        )
    modest = 40 * 1024**3
    tight = (4 * 1024**3) + (1024**3 // 2)
    assert 100 * (1 - tight / modest) < 90
    assert tight < PULL_FREE_BYTES
    with pytest.raises(Refusal, match="deploy_disk_headroom_insufficient"):
        check_pull_headroom(
            docker=_headroom_docker(),
            disk_fn=lambda _path: usage(modest, modest - tight, tight),
        )
    floor = PULL_FREE_BYTES
    assert 100 * (1 - floor / modest) < 90
    check_pull_headroom(
        docker=_headroom_docker(),
        disk_fn=lambda _path: usage(modest, modest - floor, floor),
    )
    assert PULL_FREE_BYTES == 5 * 1024**3


def test_retention_does_not_keep_an_extra_older_image():
    assert KEEP_OLDER == 0
    images = [
        {
            "Id": IMAGE,
            "RepoDigests": [REF],
            "RepoTags": [REF],
            "Created": "2026-10-02T00:00:00+00:00",
        },
        {
            "Id": OTHER,
            "RepoDigests": [OTHER_REF],
            "RepoTags": [OTHER_REF],
            "Created": "2026-10-01T00:00:00+00:00",
        },
    ]
    choices, protected = candidates(
        images, [{"Image": IMAGE}], {"Image": IMAGE}, REF, {"rollback_target": REF},
    )
    assert OTHER not in protected
    assert IMAGE in protected
    assert [item["image_id"] for item in choices] == [OTHER]


def test_threshold_default_stays_quiet_below_ninety_percent():
    class Docker:
        def json(self, *args):
            assert args[0] == "info"
            return {
                "Driver": "overlay2",
                "DockerRootDir": "/var/lib/docker",
                "OSType": "linux",
                "Architecture": "x86_64",
            }

        def inventory(self):
            raise AssertionError("88% is below the post-C6 trigger")

    report = retain(dry_run=False, mode="threshold", docker=Docker(), measure=lambda _path: 88.0)
    assert report["status"] == "below_threshold"
    assert report["removed"] == []


def test_main_watermark_defaults_are_post_cutover(monkeypatch):
    seen = {}

    def fake_retain(**kwargs):
        seen.update(kwargs)
        return {"status": "below_threshold", "after_pct": 10}

    monkeypatch.setattr("scripts.daemon_image_retention.retain", fake_retain)
    monkeypatch.delenv("DISK_AUTOPRUNE_PCT", raising=False)
    monkeypatch.delenv("DISK_AUTOPRUNE_LOW_PCT", raising=False)
    monkeypatch.delenv("TINYASSETS_DAEMON_IMAGE_RETENTION_MODE", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    assert main([]) == 0
    assert seen["high"] == 90.0
    assert seen["low"] == 80.0


def _legacy_gvisor_snapshots(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    host = tmp_path / "host"
    deploy = host / "var/lib/tinyassets-deploy"
    stamp = deploy / "bundle-snapshots" / "2026-10-01T00-00-00Z-aaaaaa" / "host" / "root"
    paths: dict[str, Path] = {}
    for name in ("current", "release-20260928.0"):
        tree = stamp / name
        (tree / "gvisor-bin").mkdir(parents=True)
        (tree / "runsc").write_text("bin\n", encoding="utf-8")
        tree.chmod(0o555)
        paths[name] = tree
    kept = stamp / "harness-current"
    kept.mkdir()
    (kept / "keep").write_text("keep\n", encoding="utf-8")
    paths["kept"] = kept
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "safe").write_text("safe\n", encoding="utf-8")
    (stamp / "release-link").symlink_to(outside, target_is_directory=True)
    paths["outside"] = outside
    cutover = deploy / "cutover" / "bundle" / "host" / "root"
    old = cutover / "release-old"
    old.mkdir(parents=True)
    (old / "runsc").write_text("old\n", encoding="utf-8")
    (cutover / "current").symlink_to("release-old")
    paths["cutover_release"] = old
    paths["cutover_link"] = cutover / "current"
    return host, paths


def test_prune_removes_legacy_gvisor_snapshot_directories(tmp_path: Path):
    host, paths = _legacy_gvisor_snapshots(tmp_path)
    root = tmp_path / "boxhostd"
    root.mkdir()
    removed = prune_box_harnesses(
        {"Image": IMAGE}, REF, {REF: IMAGE}, root=root,
        repair_host=True, host_root=host,
    )
    assert not paths["current"].exists()
    assert not paths["release-20260928.0"].exists()
    assert not paths["cutover_release"].exists()
    assert paths["cutover_link"].is_symlink()
    assert (paths["kept"] / "keep").read_text(encoding="utf-8") == "keep\n"
    assert (paths["outside"] / "safe").read_text(encoding="utf-8") == "safe\n"
    assert (host / "var/lib/tinyassets-deploy/bundle-snapshots").is_dir()
    assert set(removed) == {
        "bundle-snapshots/2026-10-01T00-00-00Z-aaaaaa/host/root/current",
        "bundle-snapshots/2026-10-01T00-00-00Z-aaaaaa/host/root/release-20260928.0",
        "cutover/bundle/host/root/release-old",
    }


def test_legacy_snapshots_stay_until_the_host_repair_pass(tmp_path: Path):
    host, paths = _legacy_gvisor_snapshots(tmp_path)
    root = tmp_path / "boxhostd"
    root.mkdir()
    assert prune_box_harnesses(
        {"Image": IMAGE}, REF, {REF: IMAGE}, root=root, host_root=host,
    ) == []
    assert paths["current"].is_dir()
    assert paths["cutover_release"].is_dir()
    assert paths["cutover_link"].is_symlink()


def test_production_harness_root_clears_legacy_snapshots(tmp_path: Path, monkeypatch):
    host, paths = _legacy_gvisor_snapshots(tmp_path)
    root = tmp_path / "boxhostd"
    root.mkdir()
    monkeypatch.setattr("scripts.daemon_image_retention.BOX_HARNESS_ROOT", root)
    removed = prune_box_harnesses(
        {"Image": IMAGE}, REF, {REF: IMAGE}, root=root, host_root=host,
    )
    assert "cutover/bundle/host/root/release-old" in removed
    assert not paths["current"].exists()
    assert not paths["release-20260928.0"].exists()


def _two_images():
    return [
        {"Id": IMAGE, "RepoDigests": [REF], "RepoTags": [REF],
         "Created": "2026-10-02T00:00:00+00:00"},
        {"Id": OTHER, "RepoDigests": [OTHER_REF], "RepoTags": [OTHER_REF],
         "Created": "2026-10-01T00:00:00+00:00"},
    ]


def test_an_already_absent_rollback_digest_does_not_block_retention(capsys):
    gone = "ghcr.io/tinyassets/tinyassets-daemon@sha256:" + "9f" * 32
    choices, protected = candidates(
        _two_images(), [{"Image": IMAGE}], {"Image": IMAGE}, REF, {"rollback_target": gone},
    )
    assert IMAGE in protected
    assert [item["image_id"] for item in choices] == [OTHER]
    assert "rollback_target_absent" in capsys.readouterr().err


@pytest.mark.parametrize("receipt,configured,slot", [
    ({"rollback_target": "ghcr.io/tinyassets/tinyassets-daemon:previous"}, REF,
     "rollback_target"),
    ({"rollback_target": REF},
     "ghcr.io/tinyassets/tinyassets-daemon@sha256:" + "ef" * 32, "configured"),
])
def test_a_missing_tag_or_configured_image_still_refuses_by_name(receipt, configured, slot):
    with pytest.raises(Refusal, match="protected_reference_unmapped:" + slot):
        candidates(_two_images(), [{"Image": IMAGE}], {"Image": IMAGE}, configured, receipt)


@pytest.mark.parametrize("bundle", ["live", "unreadable"])
def test_prune_keeps_the_image_a_running_owner_still_reads(tmp_path: Path, monkeypatch, bundle):
    root = tmp_path / "boxhostd"
    root.mkdir()
    current, deferred, stale = _name(IMAGE), "harness-" + "56" * 32, "harness-" + "78" * 32
    for name in (current, deferred, stale):
        _seal(root / name)
    (root / "harness-current").symlink_to(current)
    bundles = tmp_path / "bundles"
    (bundles / "owner-300001").mkdir(parents=True)
    config = bundles / "owner-300001" / "config.json"
    config.write_text(json.dumps({"root": {"path": str(root / deferred)}})
                      if bundle == "live" else "{not json", encoding="utf-8")
    monkeypatch.setenv("TINYASSETS_BOX_BUNDLE_ROOT", str(bundles))
    removed = prune_box_harnesses({"Image": IMAGE}, REF, {REF: IMAGE}, root=root)
    # An owner left serving through a deploy keeps its image until it sleeps;
    # an unknowable live set removes no image at all.
    assert set(removed) == ({stale} if bundle == "live" else set())
    assert (root / deferred).is_dir()
