#!/usr/bin/env python3
"""gVisor (runsc, systrap, rootful) measurements for target-architecture slice S0.

Same host and rootfs as fc_bench.py, so the two drivers compare like for like:
start latency, exec latency, idle memory, checkpoint/restore, and packing.
Prints one JSON object on stdout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

WORK = Path(sys.argv[1])
ROOTFS = WORK / "rootfs"
PACK = int(sys.argv[2]) if len(sys.argv) > 2 else 10
STATE = WORK / "runsc-state"
RUNSC = ["runsc", "--root", str(STATE), "--network=none", "--platform=systrap"]


def run(*args: str, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run([*RUNSC, *args], capture_output=True, text=True, check=check,
                          stdin=subprocess.DEVNULL, timeout=timeout)


def bundle(name: str) -> Path:
    b = WORK / "gv" / name
    shutil.rmtree(b, ignore_errors=True)
    (b / "work").mkdir(parents=True)
    subprocess.run(["runsc", "spec", "--bundle", str(b)], check=True)
    cfg = json.loads((b / "config.json").read_text())
    cfg["root"] = {"path": str(ROOTFS), "readonly": True}
    cfg["process"].update(args=["sleep", "infinity"], terminal=False, cwd="/cc",
                          env=["PATH=/usr/local/bin:/usr/bin:/bin"])
    cfg["mounts"].append({"destination": "/cc", "type": "bind", "source": str(b / "work"),
                          "options": ["rbind", "rw"]})
    (b / "config.json").write_text(json.dumps(cfg))
    return b


NOTES: dict = {}


def start(name: str) -> float:
    b = bundle(name)
    t = time.perf_counter()
    r = subprocess.run([*RUNSC, "run", "--detach", "--bundle", str(b), name],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=open(b / "run.log", "w"), timeout=120)
    cgroup_refused = "cgroup" in (b / "run.log").read_text()
    if r.returncode != 0 and cgroup_refused and "--ignore-cgroups" not in RUNSC:
        # nested cgroup hosts (containers) refuse runsc's cgroup setup; record and retry
        RUNSC.append("--ignore-cgroups")
        NOTES["ignore_cgroups"] = True
        run("delete", "--force", name, check=False)
        return start(name)
    if r.returncode != 0:
        raise RuntimeError(f"runsc run failed: {(b / 'run.log').read_text()[-300:]}")
    return time.perf_counter() - t


def exec_ms(name: str, n: int) -> list[float]:
    out = []
    for _ in range(n):
        t = time.perf_counter()
        run("exec", name, "true")
        out.append((time.perf_counter() - t) * 1000)
    return out


def pct(values: list[float], p: float) -> float:
    values = sorted(values)
    return round(values[min(len(values) - 1, int(len(values) * p))], 2)


def sandbox_pss_mib() -> float:
    total = 0
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            comm = Path(f"/proc/{d}/comm").read_text().strip()
            if comm in ("gvisor_sentry", "runsc-sandbox", "exe", "runsc-gofer"):
                for line in Path(f"/proc/{d}/smaps_rollup").read_text().splitlines():
                    if line.startswith("Pss:"):
                        total += int(line.split()[1])
        except OSError:
            continue
    return round(total / 1024, 1)


def kill(name: str) -> None:
    run("kill", name, "KILL", check=False)
    time.sleep(0.2)
    run("delete", "--force", name, check=False)


def main() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    result: dict = {}
    base = sandbox_pss_mib()
    result["start_s"] = round(start("g0"), 3)
    lat = exec_ms("g0", 50)
    result["exec_ms"] = {"p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "n": len(lat)}
    time.sleep(5)
    result["idle_box_pss_mib"] = round(sandbox_pss_mib() - base, 1)

    img = WORK / "gv-ckpt"
    shutil.rmtree(img, ignore_errors=True)
    img.mkdir()
    t = time.perf_counter()
    ck = run("checkpoint", "--image-path", str(img), "g0", check=False)
    result["checkpoint_s"] = round(time.perf_counter() - t, 3)
    result["checkpoint_ok"] = ck.returncode == 0
    if ck.returncode == 0:
        result["checkpoint_mib"] = round(sum(f.stat().st_size for f in img.iterdir()) / 2**20, 1)
        run("delete", "--force", "g0", check=False)
        b = bundle("g0r")
        t = time.perf_counter()
        rs = subprocess.run([*RUNSC, "restore", "--detach", "--image-path", str(img),
                             "--bundle", str(b), "g0r"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=open(b / "restore.log", "w"),
                            timeout=120)
        result["restore_s"] = round(time.perf_counter() - t, 3)
        result["restore_ok"] = rs.returncode == 0
        if rs.returncode == 0:
            t = time.perf_counter()
            run("exec", "g0r", "true")
            result["restore_to_first_exec_s"] = round(result["restore_s"]
                                                      + time.perf_counter() - t, 3)
        kill("g0r")
    else:
        result["checkpoint_error"] = ck.stderr[-300:]
        kill("g0")

    base = sandbox_pss_mib()
    starts = [start(f"p{i}") for i in range(PACK)]
    time.sleep(5)
    lat_load = []
    for i in range(PACK):
        lat_load += exec_ms(f"p{i}", 10)
    result["packing"] = {
        "boxes": PACK,
        "start_s": {"p50": pct(starts, 0.5), "max": round(max(starts), 3)},
        "pss_mib_total": round(sandbox_pss_mib() - base, 1),
        "exec_ms_under_load": {"p50": pct(lat_load, 0.5), "p95": pct(lat_load, 0.95)},
    }
    for i in range(PACK):
        kill(f"p{i}")
    result.update(NOTES)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
