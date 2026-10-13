"""Fail-closed P2a measurement and workflow wiring."""

from __future__ import annotations

import contextlib
import errno
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from deploy import boxhostd_idle
from scripts import box_preflight, box_preflight_summary

ROOT = Path(__file__).resolve().parents[1]


def test_percentile_and_empty_sample() -> None:
    assert box_preflight.percentile([5, 1, 3, 2, 4], 0.95) == 5
    with pytest.raises(ValueError, match="empty"):
        box_preflight.percentile([], 0.95)


def test_gate_exception_is_a_failure() -> None:
    def broken() -> None:
        raise RuntimeError("no project quota")

    assert box_preflight.gate(broken) == {
        "pass": False,
        "reason": "RuntimeError: no project quota",
    }


def test_harness_deadline_escapes_gate_for_outer_cleanup() -> None:
    with pytest.raises(TimeoutError, match="30-minute"):
        box_preflight.gate(lambda: box_preflight._deadline(0, None))


def test_installer_rejects_malformed_and_wrong_checksums() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash unavailable")
    installer = (ROOT / "deploy/install-boxhostd-preflight.sh").read_text()
    assert installer.count("sha256sum --strict -c -") == 3
    assert "a0dde4d5e75d4f8e4287bc2e631b96536e83c80f574fb4ff62c58d472d4f58c5" in installer
    for digest in ("0" * 55, "0" * 64):
        result = subprocess.run(
            [bash, "-c", "sha256sum --strict -c -"],
            input=f"{digest}  /dev/null\n",
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0


def test_g3_reserves_unused_daemon_allowance() -> None:
    cap = 4 * box_preflight.GIB
    assert box_preflight.adjusted_available_mib(5120, box_preflight.GIB, cap) == 2048
    assert box_preflight.adjusted_available_mib(3500, box_preflight.GIB, cap) < 1024


def test_g3_fails_when_daemon_cap_reservation_consumes_host_headroom(
    monkeypatch, tmp_path: Path
) -> None:
    class Boxes:
        state = tmp_path

        def start(self, name):
            owner = tmp_path / name
            owner.mkdir()
            if name == "cap0":
                (owner / "cli-ready").touch()
                (owner / "render.png").write_bytes(b"image")
                (owner / "tree.bin").touch()
            return 0.01, owner, owner

        def exec(self, *_args, **_kwargs):
            return subprocess.CompletedProcess([], 0)

    class Process:
        returncode = 0

        def poll(self):
            return None

        def wait(self, **_kwargs):
            return 0

    original_open = Path.open

    class Sink:
        def write(self, data):
            return len(data)

    def fake_open(path, *args, **kwargs):
        if path.name == "tree.bin":
            return contextlib.nullcontext(Sink())
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)
    monkeypatch.setattr(box_preflight.subprocess, "Popen", lambda *_a, **_k: Process())
    monkeypatch.setattr(box_preflight, "chromium", lambda *_a: Process())
    monkeypatch.setattr(box_preflight, "canary_p95", lambda: 10)
    monkeypatch.setattr(box_preflight, "pss_mib", lambda _c: 100)
    monkeypatch.setattr(box_preflight, "daemon_current_bytes", lambda: box_preflight.GIB)
    monkeypatch.setattr(box_preflight, "mem_available_mib", lambda: 3500)
    g3, _ = box_preflight.g3_g6(Boxes(), 10, 4 * box_preflight.GIB)
    assert g3["mem_available_mib"] == 3500
    assert g3["mem_available_at_daemon_cap_mib"] < 1024
    assert g3["pass"] is False


def test_g1_checks_sandbox_survives_oom(monkeypatch, tmp_path: Path) -> None:
    class Boxes:
        state = tmp_path

        def start(self, _name, *, memory):
            assert memory == box_preflight.GIB
            return 0.1, tmp_path, tmp_path

        def exec(self, *_args, **_kwargs):
            return subprocess.CompletedProcess([], sandbox_rc)

    class Allocation:
        def wait(self, **_kwargs):
            return 137

    monkeypatch.setattr(box_preflight.subprocess, "Popen", lambda *_a, **_k: Allocation())
    monkeypatch.setattr(box_preflight, "canary_p95", lambda: 10)
    oom = iter((0, 1))
    events = iter((0, 1))
    monkeypatch.setattr(box_preflight, "host_oom_kills", lambda: next(oom))
    monkeypatch.setattr(box_preflight, "cgroup_event", lambda *_a: next(events))
    monkeypatch.setattr(
        box_preflight,
        "cgroup_value",
        lambda _p, key: {
            "memory.max": str(box_preflight.GIB),
            "pids.max": "1024",
            "cpu.weight": "100",
        }[key],
    )
    sandbox_rc = 1
    assert box_preflight.g1(Boxes(), 10)["pass"] is False
    oom = iter((0, 1))
    events = iter((0, 1))
    sandbox_rc = 0
    assert box_preflight.g1(Boxes(), 10)["pass"] is True


def test_ext4_project_inheritance_and_dedicated_scratch(monkeypatch, tmp_path: Path) -> None:
    commands = []

    def fake_run(args, **_kwargs):
        commands.append(args)
        output = (
            "ext4 rw,prjquota"
            if args[-1] == "FSTYPE,OPTIONS"
            else "/volume"
            if args[-1] == "TARGET"
            else ""
        )
        return subprocess.CompletedProcess(args, 0, stdout=output)

    monkeypatch.setattr(box_preflight, "run", fake_run)
    monkeypatch.setattr(box_preflight.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(box_preflight, "MIB", 1)
    volume = tmp_path / "volume"
    volume.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    box_preflight.g4(volume, work)
    chattr = next(command for command in commands if command[0] == "chattr")
    assert chattr[3] == "+P"
    assert Path(chattr[4]).parent == volume / box_preflight.QUOTA_SCRATCH
    assert (work / "quota-state.json").exists()
    box_preflight.cleanup_scratch(work)
    assert not work.exists()


def test_cleanup_pass_removes_killed_harness_quota_tree(monkeypatch, tmp_path: Path) -> None:
    work = tmp_path / "box-preflight-run"
    tree = tmp_path / "volume" / box_preflight.QUOTA_SCRATCH / "quota-run"
    tree.mkdir(parents=True)
    (tree / "limit.bin").write_bytes(b"x")
    work.mkdir()
    (work / "quota-state.json").write_text(
        json.dumps(
            {
                "filesystem": "ext4",
                "mount": "/volume",
                "project": 123,
                "tree": str(tree),
            }
        )
    )
    commands = []

    def fake_run(args, **_kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(box_preflight, "run", fake_run)
    monkeypatch.setattr(box_preflight.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    box_preflight.cleanup_scratch(work)
    assert ["setquota", "-P", "123", "0", "0", "0", "0", "/volume"] in commands
    assert not work.exists() and not tree.exists()


def test_boxhostd_parent_timeout_kills_group_and_runs_cleanup(monkeypatch, tmp_path: Path) -> None:
    class Process:
        pid = 123
        calls = 0

        def wait(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("harness", 2400)
            return 0

    process = Process()
    kills = []
    cleanups = []
    monkeypatch.setattr(
        boxhostd_idle.tempfile, "mkdtemp", lambda **_k: str(tmp_path / "box-preflight-run")
    )
    monkeypatch.setattr(boxhostd_idle.subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(boxhostd_idle.os, "killpg", lambda *args: kills.append(args), raising=False)
    monkeypatch.setattr(boxhostd_idle.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(
        boxhostd_idle.subprocess,
        "run",
        lambda args, **_k: cleanups.append(args) or subprocess.CompletedProcess(args, 0),
    )
    result = tmp_path / "result.json"
    boxhostd_idle.run_preflight(result)
    assert kills == [(123, boxhostd_idle.signal.SIGKILL)]
    assert "--cleanup-only" in cleanups[0]
    assert json.loads(result.read_text())["pass"] is False


def test_cleanup_fresh_host_is_noop_without_runtime(monkeypatch, tmp_path: Path) -> None:
    work = tmp_path / "box-preflight-fresh"
    def fake_run(args, **_kwargs):
        assert args[0] == "chattr"
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(box_preflight, "run", fake_run)
    box_preflight.cleanup_scratch(work)
    work.mkdir()
    (work / "runsc-state").mkdir()
    box_preflight.cleanup_scratch(work)
    assert not work.exists()


def test_cleanup_only_guard_reports_fixed_failure(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setattr(
        sys, "argv", ["box_preflight.py", "--cleanup-only", "--work-dir", str(tmp_path)]
    )
    assert box_preflight.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "step": "cleanup_guard",
        "reason": "invalid_work_dir",
    }


def test_g4_missing_optional_tool_is_gate_failure_without_quota_state(
    monkeypatch, tmp_path: Path
) -> None:
    volume = tmp_path / "volume"
    volume.mkdir()
    work = tmp_path / "work"
    work.mkdir()

    def fake_run(args, **_kwargs):
        assert args[0] in {"findmnt", "chattr"}
        return subprocess.CompletedProcess(args, 0, stdout="ext4 rw,prjquota")

    monkeypatch.setattr(box_preflight, "run", fake_run)
    monkeypatch.setattr(
        box_preflight.shutil, "which", lambda tool: None if tool == "chattr" else f"/usr/bin/{tool}"
    )
    assert box_preflight.g4(volume, work)["reason"] == "missing_tool:chattr"
    assert not (work / "quota-state.json").exists()
    box_preflight.cleanup_scratch(work)


def test_g4_creates_absent_scratch_directory(monkeypatch, tmp_path: Path) -> None:
    volume = tmp_path / "volume"
    volume.mkdir()
    work = tmp_path / "work"
    work.mkdir()

    def fake_run(args, **_kwargs):
        output = "ext4 rw,prjquota" if args[-1] == "FSTYPE,OPTIONS" else "/volume"
        return subprocess.CompletedProcess(args, 0, stdout=output)

    monkeypatch.setattr(box_preflight, "run", fake_run)
    monkeypatch.setattr(box_preflight.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(box_preflight, "MIB", 1)
    box_preflight.g4(volume, work)
    assert (volume / box_preflight.QUOTA_SCRATCH).is_dir()
    box_preflight.cleanup_scratch(work)


def test_cleanup_quota_state_missing_tool_names_step(monkeypatch, tmp_path: Path) -> None:
    work = tmp_path / "box-preflight-run"
    work.mkdir()
    (work / "quota-state.json").write_text(
        json.dumps(
            {
                "filesystem": "xfs",
                "mount": "/volume",
                "project": 123,
                "tree": str(tmp_path / "volume" / box_preflight.QUOTA_SCRATCH / "quota-run"),
            }
        )
    )
    monkeypatch.setattr(box_preflight.shutil, "which", lambda _tool: None)
    with pytest.raises(box_preflight.CleanupError, match="quota_clear:missing_tool:xfs_quota"):
        box_preflight.cleanup_scratch(work)
    assert work.exists()


def test_cleanup_nonexistent_sandboxes_are_success(monkeypatch, tmp_path: Path) -> None:
    work = tmp_path / "box-preflight-run"
    (work / "runsc-state").mkdir(parents=True)
    (work / "runsc-state" / "metadata").touch()
    commands = []

    def fake_run(args, **_kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="sandbox not found")

    monkeypatch.setattr(box_preflight, "run", fake_run)
    box_preflight.cleanup_scratch(work)
    assert sum("delete" in args for args in commands) == 5
    assert not work.exists()


def test_cleanup_scans_state_and_unmounts_busy_tree(monkeypatch, tmp_path: Path) -> None:
    work = tmp_path / "box-preflight-run"
    state = work / "runsc-state" / "unexpected-box"
    state.mkdir(parents=True)
    nested = work / "rootfs" / "nested"
    nested.mkdir(parents=True)
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        f"1 0 0:1 / {work} rw - tmpfs tmpfs rw\n"
        f"2 1 0:2 / {work / 'rootfs'} rw - overlay overlay rw\n"
        f"3 2 0:3 / {nested} rw - tmpfs tmpfs rw\n"
        "4 0 0:4 / /unrelated rw - tmpfs tmpfs rw\n"
    )
    monkeypatch.setattr(box_preflight, "MOUNTINFO", mountinfo)
    commands = []
    mounted = True
    original_rmtree = shutil.rmtree

    def fake_run(args, **_kwargs):
        nonlocal mounted
        commands.append(args)
        if args[0] == "umount" and args[-1] == str(work):
            if args[1:2] != ["-l"]:
                return subprocess.CompletedProcess(args, 1)
            mounted = False
        return subprocess.CompletedProcess(args, 0)

    def fake_rmtree(path):
        if mounted:
            raise OSError(errno.EBUSY, "busy")
        original_rmtree(path)

    monkeypatch.setattr(box_preflight, "run", fake_run)
    monkeypatch.setattr(box_preflight.shutil, "rmtree", fake_rmtree)
    box_preflight.cleanup_scratch(work)
    assert [args[-1] for args in commands if args[0] == "umount"] == [
        str(nested), str(work / "rootfs"), str(work), str(work)
    ]
    assert ["umount", "-l", str(work)] in commands
    assert ["chattr", "-R", "-ia", str(work)] in commands
    assert any("delete" in args and args[-1] == "unexpected-box" for args in commands)
    assert not work.exists()


def test_cleanup_busy_errno_is_public_token(monkeypatch, tmp_path: Path) -> None:
    work = tmp_path / "box-preflight-run"
    work.mkdir()
    monkeypatch.setattr(box_preflight, "MOUNTINFO", tmp_path / "missing-mountinfo")
    monkeypatch.setattr(
        box_preflight, "run", lambda args, **_k: subprocess.CompletedProcess(args, 0)
    )

    def busy(_path):
        raise OSError(errno.EBUSY, "private path")

    monkeypatch.setattr(box_preflight.shutil, "rmtree", busy)
    with pytest.raises(box_preflight.CleanupError, match="work_remove:operation_failed:EBUSY"):
        box_preflight.cleanup_scratch(work)


def test_cleanup_failure_names_step_and_fixed_reason(monkeypatch, tmp_path: Path) -> None:
    work = tmp_path / "box-preflight-run"
    (work / "runsc-state").mkdir(parents=True)
    (work / "runsc-state" / "metadata").touch()
    monkeypatch.setattr(
        box_preflight,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 1, stdout="", stderr="secret"),
    )
    with pytest.raises(box_preflight.CleanupError, match="sandbox_delete:runsc_delete_failed"):
        box_preflight.cleanup_scratch(work)
    assert (
        boxhostd_idle.cleanup_failure('{"step":"sandbox_delete","reason":"runsc_delete_failed"}')
        == "cleanup.sandbox_delete:runsc_delete_failed"
    )
    assert boxhostd_idle.cleanup_failure('{"step":"sandbox_delete","reason":"secret"}') == (
        "cleanup.cleanup_process:operation_failed"
    )


def test_boxhostd_publishes_cleanup_step_and_reason(monkeypatch, tmp_path: Path) -> None:
    class Process:
        def wait(self, **_kwargs):
            return 0

    monkeypatch.setattr(
        boxhostd_idle.tempfile, "mkdtemp", lambda **_k: str(tmp_path / "box-preflight-run")
    )
    monkeypatch.setattr(boxhostd_idle.subprocess, "Popen", lambda *_a, **_k: Process())
    monkeypatch.setattr(
        boxhostd_idle.subprocess,
        "run",
        lambda args, **_k: subprocess.CompletedProcess(
            args, 1, stdout='{"step":"quota_clear","reason":"missing_tool:setquota"}'
        ),
    )
    result = tmp_path / "result.json"
    boxhostd_idle.run_preflight(result)
    assert json.loads(result.read_text()) == {
        "pass": False,
        "fatal": "cleanup.quota_clear:missing_tool:setquota",
    }


def test_box_cgroups_stay_beneath_the_delegated_service() -> None:
    assert (
        box_preflight.service_cgroup_parent("/system.slice/boxhostd.service/control")
        == "/system.slice/boxhostd.service"
    )
    with pytest.raises(RuntimeError, match="boxhostd.service"):
        box_preflight.service_cgroup_parent("/system.slice/other.service")


def test_missing_quota_mount_fails_without_touching_volume(monkeypatch, tmp_path: Path) -> None:
    class Result:
        stdout = "ext4 rw,relatime\n"

    monkeypatch.setattr(box_preflight, "run", lambda _args: Result())
    assert box_preflight.g4(tmp_path) == {
        "filesystem": "ext4",
        "project_quota_mount": False,
        "pass": False,
        "reason": "project quotas are not mounted on data volume",
    }


def test_fatal_run_writes_all_four_failed_gates(monkeypatch, tmp_path: Path) -> None:
    def broken(*_args, **_kwargs) -> None:
        raise RuntimeError("runtime missing")

    path = tmp_path / "results.json"
    monkeypatch.setattr(box_preflight, "run", broken)
    monkeypatch.setattr(box_preflight, "valid_work_dir", lambda _work: True)
    monkeypatch.setattr(box_preflight.signal, "SIGALRM", 14, raising=False)
    monkeypatch.setattr(box_preflight.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(box_preflight.signal, "alarm", lambda *_args: None, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "box_preflight.py",
            "--result",
            str(path),
            "--work-dir",
            str(tmp_path / "box-preflight-work"),
        ],
    )
    assert box_preflight.main() == 1
    report = json.loads(path.read_text())
    assert report["pass"] is False
    assert set(report["gates"]) == {"G1", "G3", "G4", "G6"}
    assert all(not item["pass"] for item in report["gates"].values())


def test_summary_uses_only_reported_aggregate_values(monkeypatch, capsys, tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    path.write_text(
        json.dumps(
            {
                "pass": False,
                "gates": {
                    "G1": {"pass": True, "memory_max_bytes": 1073741824},
                    "G3": {"pass": False, "awake_pss_with_chromium_mib": 701},
                },
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["box_preflight_summary.py", str(path)])
    box_preflight_summary.main()
    output = capsys.readouterr().out
    assert "Overall: **FAIL**" in output
    assert "awake_pss_with_chromium_mib=701" in output
    assert "| G4 | FAIL |" in output


def test_preflight_workflow_and_idle_service_are_decoupled_from_daemon() -> None:
    deploy = (ROOT / ".github/workflows/deploy-prod.yml").read_text()
    gates = (ROOT / ".github/workflows/box-preflight.yml").read_text()
    unit = (ROOT / "deploy/boxhostd.service").read_text()
    installer = (ROOT / "deploy/install-boxhostd-preflight.sh").read_text()
    assert (
        "sudo flock -w 120 /var/lock/tinyassets-host-mutation.lock "
        "bash /tmp/install-boxhostd-preflight.sh" in deploy
    )
    assert "workflow_dispatch:" in gates
    assert "production-host-mutation" in gates
    assert "--kill-who=main -s USR1 boxhostd.service" in gates
    assert "Delegate=yes" in unit
    assert "boxhostd_idle.py" in unit
    assert "f3ed9131bc252259df150e270154180188f9df56b73a2312940325e1f522a2d6" in installer
    assert "88a87d5d6d06160d4c144f51f95ec6dd6fe0fc6b02697eb877876683c5b57a91" in installer
