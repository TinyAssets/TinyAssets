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
from scripts import box_preflight, box_preflight_summary, box_store_durability

ROOT = Path(__file__).resolve().parents[1]


def test_g4_durability_stops_mount_and_reattaches_loop(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(box_store_durability, "STORE", tmp_path)
    if sys.platform == "win32":
        # Linux opens and fsyncs the containing directory; model that syscall
        # boundary on Windows while checking the mount command sequence.
        monkeypatch.setattr(box_store_durability.os, "O_DIRECTORY", 0, raising=False)
        monkeypatch.setattr(box_store_durability.os, "open", lambda *_a: 42)
        monkeypatch.setattr(box_store_durability.os, "fsync", lambda *_a: None)
        monkeypatch.setattr(box_store_durability.os, "close", lambda *_a: None)
    calls = []
    monkeypatch.setattr(box_store_durability, "command", lambda *args: calls.append(args))
    result = box_store_durability.check()
    assert result == {"pass": True, "fsync_remount": True}
    assert calls[:3] == [
        ("systemctl", "stop", box_store_durability.MOUNT),
        ("systemctl", "stop", box_store_durability.DEVICE),
        ("systemctl", "start", box_store_durability.MOUNT),
    ]
    assert calls[-1] == ("systemctl", "start", box_store_durability.MOUNT)
    assert list(tmp_path.iterdir()) == []


def test_g4_durability_refuses_nonempty_store(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(box_store_durability, "STORE", tmp_path)
    (tmp_path / "1000").mkdir()
    result = box_store_durability.check()
    assert result["pass"] is False


def test_boxhostd_publishes_durability_in_g4(monkeypatch, tmp_path: Path) -> None:
    result = tmp_path / "preflight.json"

    def harness(path: Path) -> None:
        path.write_text(json.dumps({"pass": True, "gates": {"G4": {"pass": True}}}))

    monkeypatch.setattr(boxhostd_idle, "run_preflight", harness)
    monkeypatch.setattr(
        boxhostd_idle.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess(
            [], 0, stdout=json.dumps({"pass": False, "reason": "remount_failed"})
        ),
    )
    boxhostd_idle.publish_preflight(result)
    report = json.loads(result.read_text())
    assert report["pass"] is False
    assert report["gates"]["G4"] == {
        "pass": False,
        "fsync_remount": False,
        "durability_reason": "remount_failed",
    }
    assert not result.with_suffix(".harness.json").exists()


def test_boxhostd_publishes_passing_g4_after_durability(monkeypatch, tmp_path: Path) -> None:
    result = tmp_path / "preflight.json"

    def harness(path: Path) -> None:
        assert not result.exists()
        path.write_text(json.dumps({"pass": True, "gates": {"G4": {"pass": True}}}))

    def durability(*_args, **_kwargs):
        assert not result.exists()
        return subprocess.CompletedProcess(
            [], 0, stdout=json.dumps({"pass": True, "fsync_remount": True})
        )

    monkeypatch.setattr(boxhostd_idle, "run_preflight", harness)
    monkeypatch.setattr(boxhostd_idle.subprocess, "run", durability)
    boxhostd_idle.publish_preflight(result)
    report = json.loads(result.read_text())
    assert report["pass"] is True
    assert report["gates"]["G4"] == {"pass": True, "fsync_remount": True}


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


def test_g4_distinguishes_project_limit_from_store_full() -> None:
    mib = box_preflight.MIB
    result = {
        "quota_errno": errno.ENOSPC,
        "quota_written_bytes": 8 * mib,
        "sibling_write_ok": True,
        "store_errno": errno.ENOSPC,
        "store_written_bytes": 21_200_000_000,
        "store_size_bytes": 21_400_000_000,
        "root_baseline_drop_bytes": 200 * mib,
        "root_fill_drop_bytes": 214_716_416,
        "root_excess_drop_bytes": 214_716_416 - 200 * mib,
        "runsc_state_growth_bytes": 0,
        "image_blocks_unchanged": True,
    }
    assert box_preflight.quota_limits_pass(result)
    for change in ({"quota_written_bytes": 1024 * mib},
                   {"sibling_write_ok": False},
                   {"store_written_bytes": 8 * mib},
                   {"quota_errno": errno.EDQUOT},
                   {"root_excess_drop_bytes": 65 * mib},
                   {"runsc_state_growth_bytes": 9 * mib}):
        assert not box_preflight.quota_limits_pass({**result, **change})


def test_root_baseline_matches_fill_duration_and_rejects_short_trace() -> None:
    mib = box_preflight.MIB
    samples = [(0.0, 1000 * mib), (60.0, 990 * mib), (120.0, 970 * mib)]
    assert box_preflight.root_drop_for_duration(samples, 90.0) == 25 * mib
    with pytest.raises(RuntimeError, match="outlasted"):
        box_preflight.root_drop_for_duration(samples, 121.0)


def test_all_detached_box_starts_discard_standard_streams(monkeypatch, tmp_path: Path) -> None:
    boxes = box_preflight.Boxes.__new__(box_preflight.Boxes)
    boxes.work = tmp_path
    boxes.rootfs = tmp_path / "rootfs"
    boxes.state = tmp_path / "runsc-state"
    boxes.state.mkdir()
    boxes.parent_cgroup = tmp_path / "cgroups"
    boxes.parent_cgroup.mkdir()
    boxes.names = []
    boxes.sandbox_pids = {}
    starts = []
    state_pid = 123
    monkeypatch.setattr(box_preflight, "CGROUP_ROOT", tmp_path)

    def spec(*args, **_kwargs):
        if args[0] == "state":
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"pid": state_pid}))
        bundle = Path(args[args.index("--bundle") + 1])
        (bundle / "config.json").write_text(
            json.dumps({"process": {}, "mounts": [], "linux": {}})
        )
        return subprocess.CompletedProcess(args, 0)

    def detached_run(args, **kwargs):
        assert "--systemd-cgroup=false" in args
        assert f"--overlay2={box_preflight.ROOTFS_OVERLAY}" in args
        assert args[args.index("run") + 1] == "--detach"
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs.get("capture_output") is not True
        starts.append(args[-1])
        cgroup = boxes.parent_cgroup / args[-1]
        cgroup.mkdir()
        (cgroup / "cgroup.procs").write_text("123\n456\n")
        (cgroup / "memory.current").write_text("1048576\n")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(boxes, "command", spec)
    monkeypatch.setattr(box_preflight.subprocess, "run", detached_run)
    for name in ("g1", "cap0", "cap1", "cap2", "cap3"):
        boxes.start(name, memory=box_preflight.GIB)
        config = json.loads((tmp_path / name / "config.json").read_text())
        assert config["linux"]["cgroupsPath"] == f"/cgroups/{name}"
        assert config["linux"]["resources"]["memory"] == {
            "limit": box_preflight.GIB,
            "swap": box_preflight.GIB,
        }
    assert starts == ["g1", "cap0", "cap1", "cap2", "cap3"]
    state_pid = 999
    with pytest.raises(RuntimeError, match="outside its limited box cgroup"):
        boxes.start("outside", memory=box_preflight.GIB)
    (boxes.parent_cgroup / "stale").mkdir()
    with pytest.raises(RuntimeError, match="already exists"):
        boxes.start("stale", memory=box_preflight.GIB)


def test_host_cgroup_membership_and_rss_use_fake_sys_and_proc(tmp_path, monkeypatch) -> None:
    cgroup = tmp_path / "sys/fs/cgroup/boxhostd/g1"
    child = cgroup / "nested"
    child.mkdir(parents=True)
    (cgroup / "cgroup.procs").write_text("123\n")
    (child / "cgroup.procs").write_text("456\n")
    proc = tmp_path / "proc"
    for pid, kib in ((123, 1024), (456, 2048)):
        status = proc / str(pid) / "status"
        status.parent.mkdir(parents=True)
        status.write_text(f"Name:\trunsc\nVmRSS:\t{kib} kB\n")
        (proc / str(pid) / "cgroup").write_text("0::/\n")
    monkeypatch.setattr(box_preflight, "PROC_ROOT", proc)
    assert box_preflight.cgroup_pids(cgroup) == {123, 456}
    assert box_preflight.rss_bytes(cgroup) == 3 * box_preflight.MIB
    (cgroup / "cgroup.procs").write_text("")
    (child / "cgroup.procs").write_text("")
    with pytest.raises(RuntimeError, match="no processes"):
        box_preflight.rss_bytes(cgroup)
    assert box_preflight.rss_bytes(cgroup, allow_empty=True) == 0


def test_workload_error_preserves_fixed_step_category() -> None:
    def fail():
        raise box_preflight.WorkloadTrialError("cli_job")

    with pytest.raises(box_preflight.WorkloadTrialError) as raised:
        box_preflight.gate(fail)
    assert raised.value.step == "cli_job"


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


@pytest.mark.parametrize(
    ("cli_rc", "browser_rc", "failed_steps"),
    [
        (0, 0, []),
        (1, 0, ["cli_job"]),
        (0, 1, ["chromium_render"]),
        (1, 1, ["cli_job", "chromium_render"]),
    ],
)
def test_g3_fails_when_daemon_cap_reservation_consumes_host_headroom(
    monkeypatch, tmp_path: Path, cli_rc: int, browser_rc: int, failed_steps: list[str]
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
            return subprocess.CompletedProcess([], cli_rc if "--bundled" in _args else 0)

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
    class Browser(Process):
        returncode = browser_rc

    monkeypatch.setattr(box_preflight, "chromium", lambda *_a: Browser())
    monkeypatch.setattr(box_preflight, "canary_p95", lambda: 10)
    monkeypatch.setattr(box_preflight, "pss_mib", lambda _c: 100)
    monkeypatch.setattr(box_preflight, "daemon_current_bytes", lambda: box_preflight.GIB)
    monkeypatch.setattr(box_preflight, "mem_available_mib", lambda: 3500)
    g3, g6 = box_preflight.g3_g6(Boxes(), 10, 4 * box_preflight.GIB)
    assert g3["mem_available_mib"] == 3500
    assert g3["mem_available_at_daemon_cap_mib"] < 1024
    assert g3["pass"] is False
    assert g6["pass"] is (not failed_steps)
    assert g6.get("failed_steps", []) == failed_steps


def test_g1_accepts_box_oom_with_or_without_surviving_sentry(monkeypatch, tmp_path: Path) -> None:
    class Boxes:
        state = tmp_path
        sandbox_pids = {"g1": 123}

        def start(self, _name, *, memory):
            assert memory == box_preflight.GIB
            return 0.1, tmp_path, tmp_path

        def exec(self, *_args, **_kwargs):
            return subprocess.CompletedProcess([], sandbox_rc)

    class Allocation:
        def wait(self, **_kwargs):
            return 137

    monkeypatch.setattr(box_preflight.subprocess, "Popen", lambda *_a, **_k: Allocation())
    monkeypatch.setattr(box_preflight, "CGROUP_ROOT", tmp_path.parent)
    monkeypatch.setattr(box_preflight, "canary_p95", lambda: 10)
    monkeypatch.setattr(
        box_preflight, "rss_bytes", lambda _c, **_k: 64 * box_preflight.MIB
    )
    monkeypatch.setattr(box_preflight, "cgroup_pids", lambda _c: remaining_pids)
    oom = iter((0, 1))
    events = iter((0, 1))
    monkeypatch.setattr(box_preflight, "host_oom_kills", lambda: next(oom))
    monkeypatch.setattr(box_preflight, "cgroup_event", lambda *_a: next(events))
    monkeypatch.setattr(
        box_preflight,
        "cgroup_value",
        lambda _p, key: {
            "memory.max": str(box_preflight.GIB),
            "memory.swap.max": "0",
            "memory.current": str(64 * box_preflight.MIB),
            "memory.peak": str(box_preflight.GIB),
            "pids.max": "1024",
            "cpu.weight": "100",
        }[key],
    )
    sandbox_rc = 1
    remaining_pids = {123}
    assert box_preflight.g1(Boxes(), 10)["pass"] is False
    oom = iter((0, 1))
    events = iter((0, 1))
    sandbox_rc = 0
    result = box_preflight.g1(Boxes(), 10)
    assert result["pass"] is True
    assert result["sandbox_pid_in_cgroup_after_allocation"] is True
    assert result["box_memory_peak_bytes"] == box_preflight.GIB
    assert result["box_rss_after_bytes"] == 64 * box_preflight.MIB
    oom = iter((0, 1))
    events = iter((0, 1))
    remaining_pids = set()
    result = box_preflight.g1(Boxes(), 10)
    assert result["pass"] is True
    assert result["sandbox_pid_in_cgroup_after_allocation"] is False
    assert result["sandbox_exec_after_oom_rc"] is None


def test_g1_allocation_touches_every_page() -> None:
    size = 4 * box_preflight.MIB
    result = subprocess.run(
        [sys.executable, "-c", box_preflight.allocation_code(size)],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert result.stdout == str(size // 4096)


def test_g4_xfs_quota_and_store_exhaustion_are_distinct(monkeypatch, tmp_path: Path) -> None:
    commands = []

    def fake_run(args, **_kwargs):
        commands.append(args)
        output = (
            "xfs rw,noatime,attr2,inode64,logbufs=8,logbsize=32k,prjquota"
            if args[0] == "findmnt" else ""
        )
        return subprocess.CompletedProcess(args, 0, stdout=output)

    class Boxes:
        state = tmp_path / "work" / "runsc-state"

        def start(self, _name, **_kwargs):
            return None

        def exec(self, name, *_args, **_kwargs):
            if "write_ok" in _args[-1]:
                payload = {"write_ok": True}
            elif name == "g4quota":
                payload = {"errno": errno.ENOSPC, "bytes": 8 * box_preflight.MIB}
            else:
                payload = {"errno": errno.ENOSPC, "bytes": 8 * box_preflight.GIB}
            return subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(payload)
            )

        def stop(self, _name):
            return None

    store = tmp_path / "owners"
    store.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (work / "runsc-state").mkdir()
    image = tmp_path / "boxes.img"
    image.write_bytes(b"image")
    monkeypatch.setattr(box_preflight, "BOX_IMAGE", image)
    monkeypatch.setattr(box_preflight, "run", fake_run)
    monkeypatch.setattr(box_preflight.shutil, "which", lambda _tool: "/usr/bin/xfs_quota")
    usage = shutil.disk_usage(tmp_path)._replace(
        total=8 * box_preflight.GIB, free=8 * box_preflight.GIB
    )
    monkeypatch.setattr(box_preflight.shutil, "disk_usage", lambda _path: usage)
    monkeypatch.setattr(
        box_preflight,
        "sample_root_free",
        lambda _seconds: [(0.0, usage.free), (1000.0, usage.free)],
    )
    result = box_preflight.g4(store, Boxes(), work)
    assert result["pass"] is True
    assert result["quota_errno"] == errno.ENOSPC
    assert result["quota_written_bytes"] == 8 * box_preflight.MIB
    assert result["sibling_write_ok"]
    assert result["store_errno"] == errno.ENOSPC
    assert ["fstrim", "-a"] in commands
    assert (work / "quota-state.json").exists()


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
    assert box_preflight.g4(tmp_path, None, tmp_path) == {
        "filesystem": "ext4",
        "project_quota_mount": False,
        "nodiscard_mount": True,
        "rootfs_overlay_medium": "memory",
        "rootfs_overlay_limit_bytes": 256 * box_preflight.MIB,
        "tmp_mount_medium": "tmpfs",
        "tmp_mount_limit_bytes": 256 * box_preflight.MIB,
        "runsc_state_location": str(tmp_path / "runsc-state"),
        "runsc_state_growth_limit_bytes": 8 * box_preflight.MIB,
        "root_excess_drop_limit_bytes": 64 * box_preflight.MIB,
        "pass": False,
        "reason": "box store requires XFS pquota,nodiscard",
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
    assert "box_store_durability.py" in installer
    assert 'g4["fsync_remount"]' in (ROOT / "deploy/boxhostd_idle.py").read_text()
    assert "Delegate=yes" in unit
    assert "boxhostd_idle.py" in unit
    assert "f3ed9131bc252259df150e270154180188f9df56b73a2312940325e1f522a2d6" in installer
    assert "88a87d5d6d06160d4c144f51f95ec6dd6fe0fc6b02697eb877876683c5b57a91" in installer
