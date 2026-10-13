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
BOX_STORE = Path("/var/lib/boxhostd/owners")
BOX_IMAGE = Path("/var/lib/boxhostd/boxes.img")
MOUNTINFO = Path("/proc/self/mountinfo")


class CleanupError(Exception):
    def __init__(self, step: str, reason: str) -> None:
        self.step = step
        self.reason = reason
        super().__init__(f"{step}:{reason}")


class WorkloadTrialError(Exception):
    def __init__(self, step: str) -> None:
        self.step = step
        super().__init__(step)


def cleanup_reason(error: Exception) -> str:
    if isinstance(error, FileNotFoundError):
        return "path_missing"
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, subprocess.TimeoutExpired):
        return "timeout"
    if isinstance(error, OSError) and error.errno in errno.errorcode:
        return f"operation_failed:{errno.errorcode[error.errno]}"
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

    def start(
        self, name: str, *, memory: int = 2 * GIB, owner_root: Path | None = None
    ) -> tuple[float, Path, Path]:
        bundle = self.work / name
        bundle.mkdir()
        owner = owner_root or bundle / "owner"
        if owner_root is None:
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
        result = subprocess.run(
            [
                str(RUNSC), "--root", str(self.state), "--network=none",
                "--platform=systrap", "run", "--detach", "--bundle", str(bundle), name,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f"runsc start failed ({result.returncode})")
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
    try:
        cli = boxes.exec(
            "cap0",
            "/opt/codex-install/node_modules/.bin/codex",
            "debug",
            "models",
            "--bundled",
            timeout=90,
            check=False,
        )
    except Exception as error:
        raise WorkloadTrialError("cli_job") from error
    (owners[0] / "cli.js").write_text(
        "const fs=require('fs');const p='/home/owner/';"
        "require('/opt/codex-install/node_modules/@openai/codex/package.json');"
        "fs.writeFileSync(p+'cli-ready','1');"
        "const t=setInterval(()=>{if(fs.existsSync(p+'cli-stop')){"
        "clearInterval(t);process.exit(0)}},100);\n"
    )
    try:
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
    except Exception as error:
        raise WorkloadTrialError("cli_job") from error
    try:
        browser = chromium(boxes, "cap0", owners[0])
    except Exception as error:
        raise WorkloadTrialError("chromium_render") from error
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
        try:
            browser.wait(timeout=20)
        except Exception as error:
            raise WorkloadTrialError("chromium_render") from error
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
    try:
        render_bytes = (owners[0] / "render.png").stat().st_size
    except OSError as error:
        raise WorkloadTrialError("chromium_render") from error
    failed_steps = []
    if cli.returncode or cli_process.returncode:
        failed_steps.append("cli_job")
    if browser.returncode or not render_bytes:
        failed_steps.append("chromium_render")
    g6_result = {
        "cli_exit": cli.returncode,
        "node_process_exit": cli_process.returncode,
        "chromium_exit": browser.returncode,
        "render_bytes": render_bytes,
        "pass": not failed_steps,
    }
    if failed_steps:
        g6_result["failed_steps"] = failed_steps
    return g3_result, g6_result


def g4(store: Path, boxes: Boxes, work: Path) -> dict:
    fs = run(["findmnt", "--mountpoint", str(store), "-no", "FSTYPE,OPTIONS"]).stdout.strip()
    fields = fs.split()
    options = fields[1].split(",") if len(fields) > 1 else []
    result: dict = {
        "filesystem": fields[0] if fields else "",
        "project_quota_mount": "pquota" in options or "prjquota" in options,
        "nodiscard_mount": "nodiscard" in options,
    }
    if (
        result["filesystem"] != "xfs"
        or not result["project_quota_mount"]
        or not result["nodiscard_mount"]
    ):
        return {**result, "pass": False, "reason": "box store requires XFS pquota,nodiscard"}
    if not store.is_dir() or shutil.which("xfs_quota") is None:
        return {**result, "pass": False, "reason": "box store or xfs_quota missing"}
    if any(entry.name != QUOTA_SCRATCH for entry in store.iterdir()):
        return {**result, "pass": False, "reason": "G4 requires an empty P2a store"}
    scratch_root = store / QUOTA_SCRATCH
    scratch_root.mkdir(mode=0o700, exist_ok=True)
    tree = Path(tempfile.mkdtemp(prefix="quota-", dir=scratch_root))
    project = 1_000_000_000 + secrets.randbelow(1_000_000_000)
    existing = run(["xfs_quota", "-x", "-c", "report -p -n", str(store)]).stdout
    if re.search(rf"(?<!\d){project}(?!\d)", existing):
        raise RuntimeError("scratch project id already has a quota")
    (work / "quota-state.json").write_text(json.dumps({
        "filesystem": "xfs", "mount": str(store), "project": project, "tree": str(tree)
    }))
    run(["xfs_quota", "-x", "-c", f"project -s -p {tree} {project}", str(store)])
    run(["xfs_quota", "-x", "-c", f"limit -p bhard=8m {project}", str(store)])
    boxes.start("g4quota", owner_root=tree)
    quota_code = (
        "import errno,json,os; p='/home/owner/limit.bin'; e=0; "
        "\ntry:\n with open(p,'wb') as f:\n  for _ in range(32): f.write(b'x'*1048576)\n"
        "except OSError as x: e=x.errno\nprint(json.dumps({'errno':e}))"
    )
    quota = json.loads(boxes.exec("g4quota", "/opt/venv/bin/python", "-c", quota_code).stdout)
    result["quota_errno"] = quota["errno"]
    boxes.stop("g4quota")
    sibling = scratch_root / "sibling"
    sibling.mkdir()
    boxes.start("g4full", owner_root=sibling)
    # Fill only the disposable store, never the host root. The sandbox sees
    # ENOSPC and remains alive; the allocated backing image cannot grow.
    root_free = shutil.disk_usage("/").free
    image_blocks = getattr(BOX_IMAGE.stat(), "st_blocks", None) if BOX_IMAGE.exists() else None
    full_code = (
        "import errno,json,os; e=0; n=0; p='/home/owner/full.bin'\n"
        "try:\n with open(p,'wb') as f:\n  while True:\n"
        "   f.write(b'x'*16777216); n+=16777216\n"
        "except OSError as x: e=x.errno\nprint(json.dumps({'errno':e,'bytes':n}))"
    )
    full = json.loads(boxes.exec(
        "g4full", "/opt/venv/bin/python", "-c", full_code, timeout=600
    ).stdout)
    result["store_errno"] = full["errno"]
    result["store_written_bytes"] = full["bytes"]
    result["root_free_delta_bytes"] = shutil.disk_usage("/").free - root_free
    boxes.stop("g4full")
    (sibling / "full.bin").unlink(missing_ok=True)
    sibling.rmdir()
    run(["fstrim", "-a"], timeout=120)
    result["image_blocks_unchanged"] = (
        image_blocks is None or getattr(BOX_IMAGE.stat(), "st_blocks", None) == image_blocks
    )
    result["pass"] = (
        result["quota_errno"] == errno.EDQUOT
        and result["store_errno"] == errno.ENOSPC
        and result["root_free_delta_bytes"] >= -MIB
        and result["image_blocks_unchanged"]
    )
    return result


def valid_work_dir(work: Path) -> bool:
    return (
        work.parent == Path("/var/tmp")
        and work.name.startswith("box-preflight-")
        and not work.is_symlink()
    )


def unmount_beneath(tree: Path) -> None:
    try:
        mountinfo = MOUNTINFO.read_text()
    except FileNotFoundError:
        return
    mounts = set()
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        target = Path(re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[4]))
        if target == tree or tree in target.parents:
            mounts.add(target)
    for target in sorted(mounts, key=lambda path: len(path.parts), reverse=True):
        try:
            result = run(["umount", str(target)], timeout=20, check=False)
            if result.returncode:
                result = run(["umount", "-l", str(target)], timeout=20, check=False)
            if result.returncode:
                raise CleanupError("work_unmount", "umount_failed")
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CleanupError("work_unmount", cleanup_reason(error)) from error


def remove_cleanup_tree(tree: Path, step: str) -> None:
    if not tree.exists():
        return
    unmount_beneath(tree)
    try:
        run(["chattr", "-R", "-ia", str(tree)], timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass  # Attributes may be unsupported; rmtree reports any real failure.
    try:
        shutil.rmtree(tree)
    except OSError as error:
        raise CleanupError(step, cleanup_reason(error)) from error


def cleanup_scratch(work: Path) -> None:
    """Recover resources even when the measurement process was killed."""
    if not work.exists():
        return
    state = work / "runsc-state"
    if state.exists() and any(state.iterdir()):
        names = {"cap3", "cap2", "cap1", "cap0", "g1"}
        names.update(child.name for child in state.iterdir() if child.is_dir())
        for name in sorted(names):
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
        if (
            tree.parent.name != QUOTA_SCRATCH
            or not tree.name.startswith("quota-")
            or tree.is_symlink()
        ):
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
            remove_cleanup_tree(tree, "quota_tree_remove")
            remove_cleanup_tree(tree.parent / "sibling", "store_fill_remove")
            try:
                tree.parent.rmdir()
            except OSError:
                pass  # Another preflight scratch tree may still be present.
            try:
                quota_state.unlink()
            except OSError as error:
                raise CleanupError("quota_state_remove", cleanup_reason(error)) from error
    remove_cleanup_tree(work, "work_remove")


def _deadline(_signum: int, _frame: object) -> None:
    raise TimeoutError("preflight exceeded its 30-minute deadline")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--cleanup-only", action="store_true")
    args = parser.parse_args()
    if args.cleanup_only:
        if args.work_dir is None or not valid_work_dir(args.work_dir):
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
    if not valid_work_dir(work):
        parser.error("--work-dir must be a /var/tmp/box-preflight-* directory")
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
        report["gates"]["G4"] = gate(lambda: g4(BOX_STORE, boxes, work))
        report["gates"]["G1"] = gate(lambda: g1(boxes, baseline))
        boxes.stop("g1")
        try:
            trial = gate(lambda: g3_g6(boxes, baseline, daemon_memory))
        except WorkloadTrialError as error:
            report["gates"]["G3"] = {"pass": False, "reason": "workload_incomplete"}
            report["gates"]["G6"] = {"pass": False, "failed_steps": [error.step]}
        else:
            if isinstance(trial, tuple):
                report["gates"]["G3"], report["gates"]["G6"] = trial
            else:
                report["gates"]["G3"] = trial
                report["gates"]["G6"] = {"pass": False, "reason": "not_run"}
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
    except WorkloadTrialError:
        raise
    except TimeoutError:
        raise
    except Exception as error:
        return {"pass": False, "reason": f"{type(error).__name__}: {error}"}


if __name__ == "__main__":
    raise SystemExit(main())
