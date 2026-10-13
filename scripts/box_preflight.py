#!/usr/bin/env python3
"""P2a production-only gVisor gates. Output contains aggregate numbers only.

Run via SIGUSR1 on the idle boxhostd unit in a quiet window. No owner data,
credentials, or network access is passed to a sandbox. A failed check is a
recorded FAIL, never a skipped PASS. Scratch is removed after the run.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

GIB = 1024**3
MIB = 1024**2
ROOT = Path("/opt/tinyassets/boxhostd")
RUNSC = ROOT / "current" / "runsc"
RUNSC_RELEASE = "release-20260928.0"
RUNSC_SHA256 = "88a87d5d6d06160d4c144f51f95ec6dd6fe0fc6b02697eb877876683c5b57a91"
CANARY = "http://127.0.0.1:8001/app"
HARNESS_DEADLINE = 1800
QUOTA_SCRATCH = ".box-preflight-scratch"
QUOTA_TOOLS = {"xfs": ("xfs_quota",), "ext4": ("repquota", "chattr", "setquota")}


class CleanupError(Exception):
    def __init__(self, step: str, reason: str) -> None:
        self.step = step
        self.reason = reason
        super().__init__(f"{step}:{reason}")


def cleanup_reason(error: Exception) -> str:
    if isinstance(error, FileNotFoundError):
        return "path_missing"
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, subprocess.TimeoutExpired):
        return "timeout"
    return "operation_failed"


def run(
    args: list[str], *, timeout: int = 120, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=check)


def percentile(values: list[float], part: float) -> float:
    if not values:
        raise ValueError("empty measurement")
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * part))], 2)


def canary_p95(samples: int = 20) -> float:
    times = []
    for _ in range(samples):
        start = time.monotonic()
        with urllib.request.urlopen(CANARY, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError(f"canary HTTP {response.status}")
            response.read(4096)
        times.append((time.monotonic() - start) * 1000)
    return percentile(times, 0.95)


def mem_available_mib() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    raise RuntimeError("MemAvailable absent")


def adjusted_available_mib(observed_mib: int, daemon_current: int, daemon_cap: int) -> int:
    """Reserve the daemon's unused allowance before testing host headroom."""
    if daemon_current < 0 or daemon_current > daemon_cap:
        raise RuntimeError("daemon memory.current is outside its configured cap")
    return (observed_mib * MIB - (daemon_cap - daemon_current)) // MIB


def daemon_current_bytes() -> int:
    pid = int(run(["docker", "inspect", "-f", "{{.State.Pid}}", "tinyassets-daemon"]).stdout)
    relative = Path(f"/proc/{pid}/cgroup").read_text().splitlines()[0].split(":", 2)[2]
    return int(cgroup_value(Path("/sys/fs/cgroup") / relative.lstrip("/"), "memory.current"))


def host_oom_kills() -> int:
    for line in Path("/proc/vmstat").read_text().splitlines():
        if line.startswith("oom_kill "):
            return int(line.split()[1])
    raise RuntimeError("oom_kill absent")


def cgroup_value(path: Path, field: str) -> str:
    return (path / field).read_text().strip()


def cgroup_event(path: Path, event: str) -> int:
    values = dict(line.split() for line in cgroup_value(path, "memory.events").splitlines())
    return int(values.get(event, "0"))


def service_cgroup_parent(path: str) -> str:
    if not path.endswith("/boxhostd.service/control"):
        raise RuntimeError("preflight must run under boxhostd.service")
    return path.removesuffix("/control")


def pss_mib(cgroup: Path) -> float:
    pids: set[int] = set()
    for procs in cgroup.rglob("cgroup.procs"):
        try:
            pids.update(int(pid) for pid in procs.read_text().split())
        except OSError:
            pass
    total_kib = 0
    for pid in pids:
        try:
            for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
                if line.startswith("Pss:"):
                    total_kib += int(line.split()[1])
                    break
        except OSError:
            pass
    if not pids:
        raise RuntimeError("sandbox cgroup has no processes")
    return round(total_kib / 1024, 1)


def export_daemon_rootfs(work: Path) -> Path:
    rootfs = work / "rootfs"
    rootfs.mkdir()
    image = run(
        ["docker", "inspect", "-f", "{{.Config.Image}}", "tinyassets-daemon"]
    ).stdout.strip()
    if not image:
        raise RuntimeError("daemon image unavailable")
    container = run(["docker", "create", "--entrypoint", "/bin/true", image]).stdout.strip()
    exporter = None
    try:
        exporter = subprocess.Popen(
            ["docker", "export", container], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert exporter.stdout is not None
        extractor = subprocess.run(
            ["tar", "-xf", "-", "-C", str(rootfs)],
            stdin=exporter.stdout,
            capture_output=True,
            timeout=600,
        )
        exporter.stdout.close()
        if exporter.wait(timeout=30) or extractor.returncode:
            raise RuntimeError("daemon image rootfs export failed")
    finally:
        if exporter is not None:
            if exporter.stdout is not None:
                exporter.stdout.close()
            if exporter.poll() is None:
                exporter.kill()
                exporter.wait(timeout=10)
        run(["docker", "rm", "-f", container], check=False)
    for binary in (
        "usr/bin/node",
        "opt/venv/bin/python",
        "opt/codex-install/node_modules/.bin/codex",
    ):
        if not (rootfs / binary).exists():
            raise RuntimeError(f"harness rootfs missing {binary}")
    (rootfs / "home/owner").mkdir(parents=True, exist_ok=True)
    return rootfs


class Boxes:
    def __init__(self, work: Path, rootfs: Path) -> None:
        self.work = work
        self.rootfs = rootfs
        self.state = work / "runsc-state"
        self.state.mkdir()
        self.names: list[str] = []
        service_cgroup = Path("/proc/self/cgroup").read_text().splitlines()[0].split(":", 2)[2]
        self.parent_cgroup = Path("/sys/fs/cgroup") / service_cgroup_parent(service_cgroup).lstrip(
            "/"
        )
        if cgroup_value(Path("/sys/fs/cgroup"), "cgroup.controllers").find("memory") < 0:
            raise RuntimeError("cgroup v2 memory controller unavailable")

    def command(
        self, *args: str, check: bool = True, timeout: int = 120
    ) -> subprocess.CompletedProcess[str]:
        return run(
            [str(RUNSC), "--root", str(self.state), "--network=none", "--platform=systrap", *args],
            check=check,
            timeout=timeout,
        )

    def start(self, name: str, *, memory: int = 2 * GIB) -> tuple[float, Path, Path]:
        bundle = self.work / name
        bundle.mkdir()
        owner = bundle / "owner"
        owner.mkdir()
        self.command("spec", "--bundle", str(bundle))
        config = json.loads((bundle / "config.json").read_text())
        config["root"] = {"path": str(self.rootfs), "readonly": True}
        config["process"].update(
            args=["/bin/sleep", "infinity"],
            terminal=False,
            cwd="/home/owner",
            env=[
                "PATH=/usr/local/bin:/usr/bin:/bin",
                "PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright",
            ],
        )
        config["mounts"].append(
            {
                "destination": "/home/owner",
                "type": "bind",
                "source": str(owner),
                "options": ["rbind", "rw"],
            }
        )
        config["mounts"].append(
            {
                "destination": "/tmp",
                "type": "tmpfs",
                "source": "tmpfs",
                "options": ["nosuid", "nodev", "mode=1777", "size=268435456"],
            }
        )
        config.setdefault("linux", {})["cgroupsPath"] = (
            f"/{self.parent_cgroup.relative_to('/sys/fs/cgroup')}/{name}"
        )
        config["linux"]["resources"] = {
            "memory": {"limit": memory},
            "pids": {"limit": 1024},
            "cpu": {"shares": 1024},
        }
        (bundle / "config.json").write_text(json.dumps(config))
        start = time.monotonic()
        result = self.command("run", "--detach", "--bundle", str(bundle), name, check=False)
        if result.returncode:
            raise RuntimeError(f"runsc start failed ({result.returncode}): {result.stderr[-300:]}")
        self.names.append(name)
        cgroup = self.parent_cgroup / name
        if not cgroup.is_dir():
            raise RuntimeError("runsc did not create its delegated box cgroup")
        return time.monotonic() - start, owner, cgroup

    def exec(
        self, name: str, *args: str, timeout: int = 120, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return self.command("exec", name, *args, timeout=timeout, check=check)

    def stop_all(self) -> None:
        for name in reversed(self.names.copy()):
            self.stop(name)

    def stop(self, name: str) -> None:
        if name not in self.names:
            return
        self.command("kill", name, "KILL", check=False, timeout=10)
        self.command("delete", "--force", name, check=False, timeout=10)
        self.names.remove(name)


BROWSER_SCRIPT = """from pathlib import Path
import os
import time
from playwright.sync_api import sync_playwright
os.environ['PLAYWRIGHT_BROWSERS_PATH'] = '/opt/ms-playwright'
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
    page = browser.new_page()
    page.set_content('<h1>Box preflight</h1><script>document.body.dataset.ready=1</script>')
    assert page.locator('h1').inner_text() == 'Box preflight'
    assert page.locator('body').get_attribute('data-ready') == '1'
    page.screenshot(path='/home/owner/render.png')
    Path('/home/owner/browser-ready').write_text('1')
    while not Path('/home/owner/browser-stop').exists():
        time.sleep(0.1)
    browser.close()
"""


def chromium(boxes: Boxes, name: str, owner: Path) -> subprocess.Popen[str]:
    (owner / "browser.py").write_text(BROWSER_SCRIPT)
    process = subprocess.Popen(
        [
            str(RUNSC),
            "--root",
            str(boxes.state),
            "--network=none",
            "--platform=systrap",
            "exec",
            name,
            "/opt/venv/bin/python",
            "/home/owner/browser.py",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if (owner / "browser-ready").exists():
            return process
        if process.poll() is not None:
            raise RuntimeError(
                f"Chromium exited {process.returncode}: {process.stderr.read()[-300:]}"
            )
        time.sleep(0.1)
    process.kill()
    raise RuntimeError("Chromium render timed out")


def g1(boxes: Boxes, baseline: float) -> dict:
    start_oom = host_oom_kills()
    _, _, cgroup = boxes.start("g1", memory=GIB)
    limits = {name: cgroup_value(cgroup, name) for name in ("memory.max", "pids.max", "cpu.weight")}
    before = cgroup_event(cgroup, "oom_kill")
    # Touch every page so Linux really charges 1.5 GiB; the box should kill
    # this child while its PID 1 and the host remain alive.
    alloc_code = (
        "import mmap,time\n"
        "m=mmap.mmap(-1,1536*1024*1024)\n"
        "for i in range(0,1536*1024*1024,4096):\n"
        " m[i]=1\n"
        " if i%(64*1024*1024)==0: time.sleep(0.2)\n"
    )
    alloc = subprocess.Popen(
        [
            str(RUNSC),
            "--root",
            str(boxes.state),
            "--network=none",
            "--platform=systrap",
            "exec",
            "g1",
            "/opt/venv/bin/python",
            "-c",
            alloc_code,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    under = canary_p95()
    try:
        allocation_rc = alloc.wait(timeout=90)
    except subprocess.TimeoutExpired:
        alloc.kill()
        raise RuntimeError("1.5 GiB allocation did not terminate within 90 seconds") from None
    box_oom = cgroup_event(cgroup, "oom_kill") - before
    all_oom = host_oom_kills() - start_oom
    sandbox_exec_rc = boxes.exec("g1", "/bin/true", check=False).returncode
    result = {
        "memory_max_bytes": limits["memory.max"],
        "pids_max": limits["pids.max"],
        "cpu_weight": limits["cpu.weight"],
        "allocation_rc": allocation_rc,
        "sandbox_exec_after_oom_rc": sandbox_exec_rc,
        "box_oom_kills": box_oom,
        "host_other_oom_kills": all_oom - box_oom,
        "canary_baseline_p95_ms": baseline,
        "canary_under_p95_ms": under,
        "canary_ratio": round(under / baseline, 3),
    }
    result["pass"] = (
        limits["memory.max"] == str(GIB)
        and limits["pids.max"] == "1024"
        and int(limits["cpu.weight"]) > 0
        and allocation_rc != 0
        and sandbox_exec_rc == 0
        and box_oom >= 1
        and all_oom == box_oom
        and under <= baseline * 1.2
    )
    return result


def g3_g6(boxes: Boxes, baseline: float, daemon_memory: int) -> tuple[dict, dict]:
    if daemon_memory != 4 * GIB:
        raise RuntimeError(f"daemon cap is {daemon_memory}, expected {4 * GIB}")
    starts = []
    owners = []
    cgroups = []
    first_exec_s = 0.0
    for i in range(4):
        elapsed, owner, cgroup = boxes.start(f"cap{i}")
        starts.append(round(elapsed, 3))
        owners.append(owner)
        cgroups.append(cgroup)
        if i == 0:
            first_start = time.monotonic()
            boxes.exec("cap0", "/bin/true")
            first_exec_s = elapsed + time.monotonic() - first_start
    # One real, allocated 1 GiB owner tree, rather than a sparse file.
    with (owners[0] / "tree.bin").open("wb") as output:
        chunk = b"x" * MIB
        for _ in range(1024):
            output.write(chunk)
    cli = boxes.exec(
        "cap0",
        "/opt/codex-install/node_modules/.bin/codex",
        "debug",
        "models",
        "--bundled",
        timeout=90,
        check=False,
    )
    (owners[0] / "cli.js").write_text(
        "const fs=require('fs');const p='/home/owner/';"
        "require('/opt/codex-install/node_modules/@openai/codex/package.json');"
        "fs.writeFileSync(p+'cli-ready','1');"
        "const t=setInterval(()=>{if(fs.existsSync(p+'cli-stop')){"
        "clearInterval(t);process.exit(0)}},100);\n"
    )
    cli_process = subprocess.Popen(
        [
            str(RUNSC),
            "--root",
            str(boxes.state),
            "--network=none",
            "--platform=systrap",
            "exec",
            "cap0",
            "/usr/bin/node",
            "/home/owner/cli.js",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 20
    while not (owners[0] / "cli-ready").exists():
        if cli_process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError("Node CLI package process failed to start")
        time.sleep(0.1)
    browser = chromium(boxes, "cap0", owners[0])
    try:
        latencies = []
        for _ in range(30):
            start = time.monotonic()
            boxes.exec("cap0", "/bin/true")
            latencies.append((time.monotonic() - start) * 1000)
        with_browser = pss_mib(cgroups[0])
        packed_pss = round(sum(pss_mib(cgroup) for cgroup in cgroups), 1)
        available = mem_available_mib()
        daemon_current = daemon_current_bytes()
        adjusted_available = adjusted_available_mib(available, daemon_current, daemon_memory)
        under = canary_p95()
    finally:
        (owners[0] / "browser-stop").touch()
        browser.wait(timeout=20)
    without_browser = pss_mib(cgroups[0])
    (owners[0] / "cli-stop").touch()
    cli_process.wait(timeout=20)
    g3_result = {
        "boxes": 4,
        "owner_tree_bytes": (owners[0] / "tree.bin").stat().st_size,
        "daemon_cap_bytes": daemon_memory,
        "awake_pss_with_chromium_mib": with_browser,
        "awake_pss_without_chromium_mib": without_browser,
        "packed_pss_mib": packed_pss,
        "start_to_first_exec_s": round(first_exec_s, 3),
        "exec_p95_ms": percentile(latencies, 0.95),
        "mem_available_mib": available,
        "daemon_current_bytes": daemon_current,
        "mem_available_at_daemon_cap_mib": adjusted_available,
        "canary_baseline_p95_ms": baseline,
        "canary_under_p95_ms": under,
        "canary_ratio": round(under / baseline, 3),
    }
    g3_result["pass"] = (
        with_browser <= 700
        and without_browser <= 250
        and first_exec_s <= 1
        and g3_result["exec_p95_ms"] <= 100
        and adjusted_available >= 1024
        and under <= baseline * 1.2
    )
    g6_result = {
        "cli_exit": cli.returncode,
        "node_process_exit": cli_process.returncode,
        "chromium_exit": browser.returncode,
        "render_bytes": (owners[0] / "render.png").stat().st_size,
        "pass": cli.returncode == 0 and cli_process.returncode == 0 and browser.returncode == 0,
    }
    return g3_result, g6_result


def g4(volume: Path, work: Path | None = None) -> dict:
    fs = run(["findmnt", "-T", str(volume), "-no", "FSTYPE,OPTIONS"]).stdout.strip()
    filesystem = fs.split()[0]
    options = fs.split()[1] if len(fs.split()) > 1 else ""
    result: dict = {
        "filesystem": filesystem,
        "project_quota_mount": any(opt in options.split(",") for opt in ("prjquota", "pquota")),
    }
    if not result["project_quota_mount"]:
        return {**result, "pass": False, "reason": "project quotas are not mounted on data volume"}
    if filesystem not in QUOTA_TOOLS:
        return {**result, "pass": False, "reason": "unsupported_project_quota_filesystem"}
    for tool in QUOTA_TOOLS[filesystem]:
        if shutil.which(tool) is None:
            return {**result, "pass": False, "reason": f"missing_tool:{tool}"}
    # Only a new, disposable tree under the actual Docker data volume is
    # touched. No quota is applied to an owner directory.
    if not volume.is_dir():
        return {**result, "pass": False, "reason": "path_missing"}
    scratch_root = volume / QUOTA_SCRATCH
    scratch_root.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="quota-", dir=scratch_root) as scratch:
        tree = Path(scratch)
        mount = run(["findmnt", "-T", str(volume), "-no", "TARGET"]).stdout.strip()
        if filesystem == "xfs":
            existing = run(["xfs_quota", "-x", "-c", "report -p -n", mount]).stdout
        elif filesystem == "ext4":
            existing = run(["repquota", "-P", "-n", mount]).stdout
        # Never alter an existing project id. A fresh random id is also
        # checked against the filesystem's quota report before use.
        project = 1_000_000_000 + secrets.randbelow(1_000_000_000)
        if re.search(rf"(?<!\d){project}(?!\d)", existing):
            raise RuntimeError("scratch project id already has a quota")
        quota_state = work / "quota-state.json" if work is not None else None
        if quota_state is not None:
            quota_state.write_text(
                json.dumps(
                    {
                        "filesystem": filesystem,
                        "mount": mount,
                        "project": project,
                        "tree": str(tree),
                    }
                )
            )
        cleanup = None
        try:
            if filesystem == "xfs":
                cleanup = ["xfs_quota", "-x", "-c", f"limit -p bhard=0 {project}", mount]
                run(["xfs_quota", "-x", "-c", f"project -s -p {tree} {project}", mount])
                run(["xfs_quota", "-x", "-c", f"limit -p bhard=8m {project}", mount])
            else:
                cleanup = ["setquota", "-P", str(project), "0", "0", "0", "0", mount]
                run(["chattr", "-p", str(project), "+P", str(tree)])
                run(["setquota", "-P", str(project), "0", "8192", "0", "0", mount])
            try:
                with (tree / "limit.bin").open("wb") as output:
                    for _ in range(32):
                        output.write(b"q" * MIB)
                failure_errno = 0
            except OSError as error:
                failure_errno = error.errno
            sibling = tree.parent / f"{tree.name}-sibling"
            try:
                sibling.write_bytes(b"ok")
                sibling_ok = True
            except OSError:
                sibling_ok = False
            finally:
                sibling.unlink(missing_ok=True)
            result.update({"write_errno": failure_errno, "sibling_write_ok": sibling_ok})
        finally:
            assert cleanup is not None
            cleared = run(cleanup, check=False)
            result["quota_cleanup_ok"] = cleared.returncode == 0
        result["pass"] = failure_errno == errno.ENOSPC and sibling_ok and result["quota_cleanup_ok"]
        return result


def cleanup_scratch(work: Path) -> None:
    """Recover resources even when the measurement process was killed."""
    if not work.exists():
        return
    state = work / "runsc-state"
    if state.exists() and any(state.iterdir()):
        for name in ("cap3", "cap2", "cap1", "cap0", "g1"):
            prefix = [str(RUNSC), "--root", str(state), "--network=none", "--platform=systrap"]
            try:
                run([*prefix, "kill", name, "KILL"], timeout=10, check=False)
                deleted = run([*prefix, "delete", "--force", name], timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise CleanupError("sandbox_delete", cleanup_reason(error)) from error
            if deleted.returncode and not re.search(
                r"not found|does not exist|no such (?:sandbox|container)",
                deleted.stderr or "",
                re.IGNORECASE,
            ):
                raise CleanupError("sandbox_delete", "runsc_delete_failed")
    quota_state = work / "quota-state.json"
    if quota_state.exists():
        try:
            details = json.loads(quota_state.read_text())
            project = details["project"]
            mount = details["mount"]
            filesystem = details["filesystem"]
            tree = Path(details["tree"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise CleanupError("quota_state", "invalid_state") from error
        if tree.parent.name != QUOTA_SCRATCH or not tree.name.startswith("quota-"):
            raise CleanupError("quota_state", "invalid_state")
        if filesystem == "xfs":
            command = ["xfs_quota", "-x", "-c", f"limit -p bhard=0 {project}", mount]
        elif filesystem == "ext4":
            command = ["setquota", "-P", str(project), "0", "0", "0", "0", mount]
        else:
            raise CleanupError("quota_state", "invalid_state")
        if shutil.which(command[0]) is None:
            raise CleanupError("quota_clear", f"missing_tool:{command[0]}")
        try:
            cleared = run(command, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CleanupError("quota_clear", cleanup_reason(error)) from error
        if cleared.returncode:
            raise CleanupError("quota_clear", "quota_clear_failed")
        else:
            try:
                if tree.exists():
                    shutil.rmtree(tree)
            except OSError as error:
                raise CleanupError("quota_tree_remove", cleanup_reason(error)) from error
            try:
                tree.parent.rmdir()
            except OSError:
                pass  # Another preflight scratch tree may still be present.
            try:
                quota_state.unlink()
            except OSError as error:
                raise CleanupError("quota_state_remove", cleanup_reason(error)) from error
    try:
        shutil.rmtree(work)
    except OSError as error:
        raise CleanupError("work_remove", cleanup_reason(error)) from error


def _deadline(_signum: int, _frame: object) -> None:
    raise TimeoutError("preflight exceeded its 30-minute deadline")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--cleanup-only", action="store_true")
    args = parser.parse_args()
    if args.cleanup_only:
        if (
            args.work_dir is None
            or args.work_dir.parent != Path("/var/tmp")
            or not args.work_dir.name.startswith("box-preflight-")
        ):
            failure = CleanupError("cleanup_guard", "invalid_work_dir")
        else:
            try:
                cleanup_scratch(args.work_dir)
                return 0
            except CleanupError as error:
                failure = error
            except Exception as error:
                failure = CleanupError("cleanup_process", cleanup_reason(error))
        print(json.dumps({"step": failure.step, "reason": failure.reason}))
        return 1
    if args.result is None:
        parser.error("--result is required")
    work = args.work_dir or Path(tempfile.mkdtemp(prefix="box-preflight-", dir="/var/tmp"))
    work.mkdir(mode=0o700, exist_ok=True)
    signal.signal(signal.SIGALRM, _deadline)
    signal.alarm(HARNESS_DEADLINE)
    report: dict = {"schema": 1, "gates": {}}
    boxes = None
    step = "runsc_verify"
    try:
        report["runsc_version"] = run([str(RUNSC), "--version"]).stdout.strip().splitlines()[0]
        with RUNSC.open("rb") as binary:
            report["runsc_sha256"] = hashlib.file_digest(binary, "sha256").hexdigest()
        if RUNSC_RELEASE not in report["runsc_version"] or report["runsc_sha256"] != RUNSC_SHA256:
            raise RuntimeError("runsc release or binary checksum differs from P2a pin")
        report["kernel"] = os.uname().release
        step = "host_probe"
        baseline = canary_p95()
        daemon_memory = int(
            run(
                ["docker", "inspect", "-f", "{{.HostConfig.Memory}}", "tinyassets-daemon"]
            ).stdout.strip()
        )
        volume = Path(
            run(
                ["docker", "volume", "inspect", "tinyassets-data", "-f", "{{.Mountpoint}}"]
            ).stdout.strip()
        )
        report["gates"]["G4"] = gate(lambda: g4(volume, work))
        # Refuse the extraction if the host cannot safely hold the image and
        # one allocated tree. This is a failed G3 with a concrete headroom fact.
        free = shutil.disk_usage("/var/tmp").free
        report["scratch_free_mib"] = free // MIB
        image_bytes = int(
            run(
                [
                    "docker",
                    "image",
                    "inspect",
                    "-f",
                    "{{.Size}}",
                    run(
                        ["docker", "inspect", "-f", "{{.Image}}", "tinyassets-daemon"]
                    ).stdout.strip(),
                ]
            ).stdout.strip()
        )
        required = max(4 * GIB, image_bytes + 2 * GIB)
        report["scratch_required_mib"] = required // MIB
        if free < required:
            raise RuntimeError("insufficient scratch free for rootfs and owner tree")
        step = "rootfs_export"
        boxes = Boxes(work, export_daemon_rootfs(work))
        step = "sandbox_gates"
        report["gates"]["G1"] = gate(lambda: g1(boxes, baseline))
        boxes.stop("g1")
        trial = gate(lambda: g3_g6(boxes, baseline, daemon_memory))
        if isinstance(trial, tuple):
            report["gates"]["G3"], report["gates"]["G6"] = trial
        else:
            report["gates"]["G3"] = trial
            report["gates"]["G6"] = {"pass": False, "reason": "workload trial failed"}
    except Exception as error:  # fail closed while still writing aggregate evidence
        report["fatal"] = f"{step}:{cleanup_reason(error)}"
    finally:
        signal.alarm(0)
        if boxes is not None:
            try:
                boxes.stop_all()
            except Exception as error:
                report["fatal"] = f"sandbox_stop:{cleanup_reason(error)}"
        try:
            cleanup_scratch(work)
        except CleanupError as error:
            report["fatal"] = f"cleanup.{error.step}:{error.reason}"
        except Exception as error:
            report["fatal"] = f"cleanup.cleanup_process:{cleanup_reason(error)}"
        for name in ("G1", "G3", "G4", "G6"):
            report["gates"].setdefault(name, {"pass": False, "reason": "not run"})
        report["pass"] = "fatal" not in report and all(
            item.get("pass") is True for item in report["gates"].values()
        )
        args.result.parent.mkdir(parents=True, exist_ok=True)
        temp = args.result.with_suffix(".new")
        temp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        os.replace(temp, args.result)
    return 0 if report["pass"] else 1


def gate(fn):
    try:
        return fn()
    except TimeoutError:
        raise
    except Exception as error:
        return {"pass": False, "reason": f"{type(error).__name__}: {error}"}


if __name__ == "__main__":
    raise SystemExit(main())
