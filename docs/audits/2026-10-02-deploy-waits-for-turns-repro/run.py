"""A merge lands mid-turn: does the deploy wait, and does the turn finish?

Run on a LINUX docker host as root (the droplet's shape: the probe runs on the
host against the data volume and drops to uid 1001). From Windows that is WSL:

    wsl -u root -e bash -lc 'cd <this dir> && REPRO_IMAGE=turns-repro:1 python3 run.py wait 600'
    ... python3 run.py cap 600

REPRO_IMAGE needs fastmcp + uvicorn (python:3.11-slim + `pip install fastmcp
uvicorn` is enough; the repo is mounted at /repo). Host side: Python 3 with
PyYAML, docker, sudo, setpriv-free (the probe drops privilege itself).

Starts the daemon (GEN=1), opens an MCP session and calls `converse`, a turn
holding a real interactive seat for TURN_S seconds (default 600). Five seconds
in, a "merge" arrives: this runs the REAL `Wait for in-flight turns` step
(`bash deploy/wait_for_turns.sh`, read from .github/workflows/deploy-prod.yml)
with `ssh`/`scp` replaced by stand-ins that run the same command on this host.
When the step returns it converges GEN=2 as deploy_fail_safe.sh does
(`up -d --timeout 20`).

wait  cap 2700s, as production. Expect outcome=idle, the turn's reply from
      GEN=1, the swap only after it, and GEN=2 serving.
cap   cap 20s, standing in for 45 min with a turn that will not end. Expect
      outcome=cap_reached, the swap proceeds, the turn is cut off.
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
URL = "http://127.0.0.1:18001/mcp"
HEADERS = {"Accept": "application/json, text/event-stream",
           "Content-Type": "application/json"}
VOLUME = "deploy-waits-for-turns-repro_data"

FAKE_SSH = r"""#!/usr/bin/env bash
# The droplet is this host: run the command the step would send.
exec bash -c "${@: -1}"
"""
FAKE_SCP = r"""#!/usr/bin/env bash
# Copy the one file the step ships to the path the step then verifies and reads.
args=("$@"); cp "${args[-2]}" "${args[-1]#*:}"
"""


def compose(*args: str, env: dict[str, str], check: bool = True) -> float:
    started = time.monotonic()
    subprocess.run(["docker", "compose", "-f", str(HERE / "compose.yml"), *args],
                   check=check, env={**os.environ, **env})
    return time.monotonic() - started


def answers() -> bool:
    try:
        urllib.request.urlopen(URL, timeout=1)
    except urllib.error.HTTPError:
        return True  # any HTTP status is an answer
    except (urllib.error.URLError, OSError):
        return False
    return True


def up(timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if answers():
            return
        time.sleep(0.25)
    raise SystemExit("server never came up")


def post(body: dict, session: str | None = None, timeout: float | None = None):
    headers = dict(HEADERS)
    if session:
        headers["mcp-session-id"] = session
    request = urllib.request.Request(URL, data=json.dumps(body).encode(), headers=headers,
                                     method="POST")
    return urllib.request.urlopen(request, timeout=timeout)


def session() -> str:
    with post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "repro", "version": "1"}}}) as response:
        sid = response.headers["mcp-session-id"]
        response.read()
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid).read()
    return sid


def call(sid: str, name: str) -> str:
    with post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
               "params": {"name": name, "arguments": {}}}, sid) as response:
        return response.read().decode("utf-8", "replace")


def wait_step() -> dict:
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "deploy-prod.yml").read_text(
        encoding="utf-8"))
    return next(s for s in wf["jobs"]["deploy"]["steps"]
                if s.get("name") == "Wait for in-flight turns")


def run_wait_step(cap_s: int, work: Path) -> dict[str, str]:
    bin_dir = work / "bin"
    bin_dir.mkdir()
    for name, body in (("ssh", FAKE_SSH), ("scp", FAKE_SCP)):
        (bin_dir / name).write_text(body, encoding="utf-8", newline="\n")
        (bin_dir / name).chmod(0o755)
    step = wait_step()
    out = work / "gh_output"
    out.write_text("", encoding="utf-8")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "GITHUB_OUTPUT": str(out), "DO_SSH_USER": "local", "DO_DROPLET_HOST": "localhost",
           "TARGET_REVISION": "f" * 40, "TURN_WAIT_CAP_S": str(cap_s), "TURN_POLL_S": "5",
           "RUN_URL": "https://example.invalid/repro", "DATA_VOLUME": VOLUME,
           "GITHUB_RUN_ID": f"repro{os.getpid()}", "GITHUB_RUN_ATTEMPT": "1"}
    subprocess.run(["bash", "-eo", "pipefail", "-c", step["run"]], env=env, check=True,
                   cwd=REPO)  # the checkout root, as on the runner
    return dict(line.split("=", 1)
                for line in out.read_text(encoding="utf-8").splitlines() if "=" in line)


def main(variant: str, turn_s: float) -> None:
    cap_s = {"wait": 2700, "cap": 20}[variant]
    env1 = {"GEN": "1", "TURN_S": str(turn_s)}
    compose("down", "-v", "--timeout", "0", env=env1, check=False)
    compose("run", "--rm", "--no-deps", "--user", "0:0", "--entrypoint", "chown", "daemon",
            "1001:1001", "/data", env=env1)
    compose("up", "-d", env=env1)
    up()
    mount = subprocess.run(["docker", "volume", "inspect", "-f", "{{.Mountpoint}}", VOLUME],
                           capture_output=True, text=True, check=True).stdout.strip()

    reply: dict[str, object] = {}
    sid = session()

    def turn() -> None:
        try:
            reply["text"] = call(sid, "converse")
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            reply["text"] = f"CUT OFF: {type(exc).__name__}"
        reply["at"] = time.monotonic()

    started = time.monotonic()
    worker = threading.Thread(target=turn, daemon=True)
    worker.start()
    time.sleep(5)  # the merge lands mid-turn

    marker: dict[str, object] = {}

    def watch_marker() -> None:
        time.sleep(12)
        try:
            marker["seen"] = (Path(mount) / ".deploy-pending.json").read_text(encoding="utf-8")
        except OSError as exc:
            marker["seen"] = f"<{type(exc).__name__}>"

    threading.Thread(target=watch_marker, daemon=True).start()
    with tempfile.TemporaryDirectory() as tmp:
        outcome = run_wait_step(cap_s, Path(tmp))
    swap_at = time.monotonic()

    dead: list[float] = []
    stop = threading.Event()

    def probe() -> None:
        while not stop.is_set():
            if not answers():
                dead.append(time.monotonic())
            time.sleep(0.25)

    prober = threading.Thread(target=probe, daemon=True)
    prober.start()
    compose("up", "-d", "--timeout", "20", env={"GEN": "2", "TURN_S": str(turn_s)})
    up()
    stop.set()
    prober.join()
    worker.join(timeout=60)
    new_gen = call(session(), "gen")

    finished = reply.get("at")
    text = str(reply.get("text", "<no reply>"))
    print(json.dumps({
        "variant": variant,
        "turn_s": turn_s,
        "wait_outcome": outcome.get("outcome"),
        "waited_s": outcome.get("waited_s"),
        "polls": outcome.get("polls"),
        "turn_reply": "TURN_FINISHED gen=1" if "TURN_FINISHED gen=1" in text else text[:120],
        "turn_finished_before_swap": finished is not None and finished <= swap_at,
        "turn_s_observed": round(finished - started, 1) if finished else None,
        "port_dead_window_s": round(dead[-1] - dead[0], 1) if dead else 0.0,
        "new_gen_serving": "2" if ('"2"' in new_gen or 'text":"2' in new_gen) else new_gen[:160],
        "marker_during_wait": str(marker.get("seen", "<not read>"))[:300],
    }))
    compose("down", "-v", "--timeout", "0", env={"GEN": "0", "TURN_S": "1"}, check=False)


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 600.0)
