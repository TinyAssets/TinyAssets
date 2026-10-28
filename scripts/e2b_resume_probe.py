"""Measure E2B pause/resume latency for a 1 GiB sandbox (openspec clean-sheet-platform §1).

Builds a throwaway 2 vCPU / 1024 MiB template, seeds one sandbox with ~200 MB of
files and a background process holding memory, then runs pause -> wait -> resume
-> `echo ok` cycles and 10 cold creates. Everything it creates is killed or
deleted on exit, and the list API is checked for leftovers.

    pip install e2b==2.61.0
    E2B_API_KEY=... python scripts/e2b_resume_probe.py --out results.json

Raw timings land in --out as JSON; stats print at the end.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from importlib.metadata import version

import httpx
from e2b import Sandbox, SandboxQuery, SandboxState, ServiceBusyException, Template

TEMPLATE_NAME = "tinyassets-resume-probe-1g"
META = {"purpose": "tinyassets-e2b-resume-probe"}
SANDBOX_TIMEOUT = 900  # seconds of running time; paused time doesn't count

SEED = r"""
set -e
mkdir -p /home/user/data
for i in $(seq 1 20); do dd if=/dev/urandom of=/home/user/data/blob$i bs=1M count=10 status=none; done
sha256sum /home/user/data/blob1 | cut -d' ' -f1 > /home/user/data/blob1.sha
cat > /home/user/holder.py <<'EOF'
import time
ballast = bytearray(150 * 1024 * 1024)
for i in range(0, len(ballast), 4096):
    ballast[i] = 1
n = 0
while True:
    n += 1
    with open("/home/user/tick", "w") as f:
        f.write(str(n))
    time.sleep(1)
EOF
"""

CHECK = (
    "pgrep -f holder.py >/dev/null && echo alive || echo dead; cat /home/user/tick; echo; "
    "test \"$(sha256sum /home/user/data/blob1 | cut -d' ' -f1)\" = \"$(cat /home/user/data/blob1.sha)\" "
    "&& echo sha-ok || echo sha-bad; du -sm /home/user/data | cut -f1"
)


def pct(xs: list[float], p: float) -> float:
    """Linear-interpolated percentile (numpy's default method)."""
    s = sorted(xs)
    k = (len(s) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def stats(xs: list[float]) -> dict:
    return {
        "n": len(xs),
        "p50": pct(xs, 50),
        "p95": pct(xs, 95),
        "p99": pct(xs, 99),
        "max": max(xs),
        "mean": statistics.fmean(xs),
    }


def first_output(sbx: Sandbox, t_start: float) -> float:
    """Run `echo ok`; return seconds from t_start to the first stdout byte."""
    seen: list[float] = []
    res = sbx.commands.run("echo ok", on_stdout=lambda _: seen or seen.append(time.perf_counter()))
    assert res.stdout.strip() == "ok", res
    return seen[0] - t_start


def build_template() -> str:
    t = time.perf_counter()
    info = Template.build(
        Template().from_base_image(), TEMPLATE_NAME, cpu_count=2, memory_mb=1024
    )
    print(f"template built in {time.perf_counter() - t:.1f}s: {info}", flush=True)
    return info.template_id


def delete_template(template_id: str) -> None:
    r = httpx.delete(
        f"https://api.e2b.app/templates/{template_id}",
        headers={"X-API-Key": os.environ["E2B_API_KEY"]},
        timeout=30,
    )
    print(f"delete template {template_id}: HTTP {r.status_code}", flush=True)


def pause(sbx: Sandbox) -> tuple[float, int]:
    busy = 0
    t = time.perf_counter()
    while True:
        try:
            sbx.pause()
            return time.perf_counter() - t, busy
        except ServiceBusyException:
            busy += 1
            time.sleep(1)


def cycles(template: str, waits: list[int], out: dict, save) -> None:
    sbx = Sandbox.create(template, timeout=SANDBOX_TIMEOUT, metadata=META)
    out["sandbox_id"] = sbx.sandbox_id
    out["created"].append(sbx.sandbox_id)
    info = sbx.get_info()
    out["sandbox"] = {"cpu_count": info.cpu_count, "memory_mb": info.memory_mb}
    sbx.commands.run(SEED, timeout=300)
    sbx.files.write("/home/user/data/sdk-written.bin", os.urandom(5 * 1024 * 1024))
    sbx.commands.run("python3 /home/user/holder.py", background=True)
    time.sleep(3)
    out["seed_check"] = sbx.commands.run(
        CHECK + "; grep -E 'MemTotal|MemAvailable' /proc/meminfo"
    ).stdout
    print("seeded:", out["seed_check"].replace("\n", " "), flush=True)

    sid = sbx.sandbox_id
    for i, wait in enumerate(waits):
        pause_s, busy = pause(sbx)
        state = str(Sandbox.get_info(sid).state)  # untimed: confirm it is really paused
        time.sleep(wait)
        t0 = time.perf_counter()
        sbx = Sandbox.connect(sid, timeout=SANDBOX_TIMEOUT)
        resume_s = time.perf_counter() - t0
        first_s = first_output(sbx, t0)
        check = sbx.commands.run(CHECK).stdout.split()
        row = {
            "cycle": i + 1,
            "wait_s": wait,
            "pause_s": pause_s,
            "pause_busy_retries": busy,
            "state_after_pause": state,
            "resume_s": resume_s,
            "resume_to_first_output_s": first_s,
            "process": check[0],
            "tick": int(check[1]),
            "sha": check[2],
            "data_mb": int(check[3]),
        }
        out["cycles"].append(row)
        save()
        print(json.dumps(row), flush=True)


def cold(template: str, n: int, out: dict, save) -> None:
    for i in range(n):
        t0 = time.perf_counter()
        sbx = Sandbox.create(template, timeout=120, metadata=META)
        create_s = time.perf_counter() - t0
        out["created"].append(sbx.sandbox_id)
        try:
            first_s = first_output(sbx, t0)
        finally:
            sbx.kill()
        row = {"create": i + 1, "create_s": create_s, "create_to_first_output_s": first_s}
        out["cold"].append(row)
        save()
        print(json.dumps(row), flush=True)


def ours() -> list[str]:
    """IDs of this probe's sandboxes (by metadata tag), running or paused."""
    query = SandboxQuery(metadata=META, state=[SandboxState.RUNNING, SandboxState.PAUSED])
    pager, ids = Sandbox.list(query=query), []
    while pager.has_next:
        ids += [s.sandbox_id for s in pager.next_items()]
    return ids


def cleanup(created: list[str]) -> list[str]:
    """Kill only sandboxes this probe created; return any of ours still listed."""
    for sid in sorted(set(created) | set(ours())):
        try:
            Sandbox.kill(sid)
        except Exception as e:  # already gone is fine; report anything else
            print(f"kill {sid}: {e!r}", flush=True)
    return ours()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--cycles-10s", type=int, default=16)
    ap.add_argument("--cycles-60s", type=int, default=20)
    ap.add_argument("--cycles-300s", type=int, default=4)
    ap.add_argument("--cold", type=int, default=10)
    args = ap.parse_args()

    waits = [10] * args.cycles_10s + [60] * args.cycles_60s + [300] * args.cycles_300s
    random.Random(28).shuffle(waits)  # spread long waits across the run

    out = {
        "sdk": f"e2b=={version('e2b')}",
        "python": sys.version.split()[0],
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cycles": [],
        "cold": [],
        "created": [],
    }

    def save() -> None:
        with open(args.out, "w") as f:
            json.dump(out, f, indent=1)

    template = build_template()
    out["template_id"] = template
    try:
        cycles(template, waits, out, save)
        cold(template, args.cold, out, save)
    finally:
        out["remaining_after_cleanup"] = cleanup(out["created"])
        delete_template(template)
        out["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save()

    c, k = out["cycles"], out["cold"]
    out["stats"] = {
        "resume_to_first_output_s": stats([r["resume_to_first_output_s"] for r in c]),
        "resume_s": stats([r["resume_s"] for r in c]),
        "pause_s": stats([r["pause_s"] for r in c]),
        "cold_create_s": stats([r["create_s"] for r in k]),
        "cold_create_to_first_output_s": stats([r["create_to_first_output_s"] for r in k]),
    }
    for wait in sorted({r["wait_s"] for r in c}):
        sub = [r["resume_to_first_output_s"] for r in c if r["wait_s"] == wait]
        out["stats"][f"resume_to_first_output_s@wait{wait}"] = stats(sub)
    save()
    print(json.dumps(out["stats"], indent=1))
    print("remaining sandboxes:", out["remaining_after_cleanup"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
