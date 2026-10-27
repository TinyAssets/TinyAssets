"""`docker stop` reaches the daemon entry, which runs as PID 1.

Linux ignores any signal PID 1 has no handler for, so before
`box_daemon_entry.exit_on_sigterm` every stop of the daemon container ran to
docker's SIGKILL. These tests run the entry as PID 1 of a fresh PID namespace
and send SIGTERM from the parent namespace, the way dockerd does.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "deploy" / "box_daemon_entry.py"


def _constant(path: Path, pattern: str) -> float:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, (path, pattern)
    return float(match.group(1))


DRAIN_BOUND_S = _constant(
    ROOT / "deploy" / "deploy_fail_safe.sh", r"^MAX_DAEMON_STOP_GRACE_S=(\d+)$"
)
GRACEFUL_SHUTDOWN_S = _constant(
    ROOT / "tinyassets" / "universe_server.py", r"^GRACEFUL_SHUTDOWN_S = ([0-9.]+)$"
)

# argv: entry path, mode ("bare" | "startup" | "serve"), graceful shutdown seconds.
CHILD = """
import os, runpy, socket, sys, threading
entry = runpy.run_path(sys.argv[1])
mode = sys.argv[2]
print("pid", os.getpid(), flush=True)
if mode != "bare":
    entry["exit_on_sigterm"]()
if mode in ("bare", "startup"):
    print("ready", flush=True)
    threading.Event().wait(120)
    sys.exit(0)

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import PlainTextResponse
from starlette.routing import Route

def blocked():
    print("blocked", flush=True)
    threading.Event().wait(120)

async def slow(request):
    await run_in_threadpool(blocked)
    return PlainTextResponse("done")

listener = socket.socket()
listener.bind(("127.0.0.1", 0))
listener.listen()
print("port", listener.getsockname()[1], flush=True)
config = uvicorn.Config(Starlette(routes=[Route("/slow", slow)]),
                        timeout_graceful_shutdown=float(sys.argv[3]), log_level="warning")
uvicorn.Server(config).run(sockets=[listener])
"""


def _namespaces_available() -> bool:
    if sys.platform != "linux" or shutil.which("unshare") is None:
        return False
    probe = subprocess.run(["unshare", "-Urpf", "true"], capture_output=True)
    return probe.returncode == 0


pytestmark = [
    pytest.mark.skipif(
        not _namespaces_available(),
        reason="needs Linux unshare with unprivileged user and PID namespaces, "
        "unavailable on this host",
    ),
    pytest.mark.verifies(
        "uptime-and-alarms:A Deploy Waits For In-Flight Work Before It Swaps The Daemon"
    ),
]


class Pid1:
    def __init__(self, mode: str):
        self.proc = subprocess.Popen(
            ["unshare", "-Urpf", "--kill-child", sys.executable, "-c", CHILD,
             str(ENTRY), mode, str(GRACEFUL_SHUTDOWN_S)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        assert self.line() == "pid 1"
        self.pid = self._namespace_init()

    def line(self) -> str:
        return self.proc.stdout.readline().strip()

    def _namespace_init(self) -> int:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for task in Path(f"/proc/{self.proc.pid}/task").iterdir():
                children = (task / "children").read_text().split()
                if children:
                    return int(children[0])
        raise AssertionError("unshare started no child")

    def terminate(self, within: float) -> tuple[int | None, float]:
        started = time.monotonic()
        subprocess.run(["kill", "-TERM", str(self.pid)], check=True)
        try:
            self.proc.wait(timeout=within)
        except subprocess.TimeoutExpired:
            return None, time.monotonic() - started
        finally:
            self.proc.kill()
            self.proc.wait()
            self.proc.stdout.close()
        return self.proc.returncode, time.monotonic() - started


def test_pid1_without_a_handler_ignores_sigterm():
    """The control: the namespace really gives the child PID 1 semantics."""
    child = Pid1("bare")
    assert child.line() == "ready"
    code, _ = child.terminate(within=3)
    assert code is None


def test_entry_exits_on_sigterm_during_startup():
    """Before uvicorn installs a handler, e.g. while the broker is awaited."""
    child = Pid1("startup")
    assert child.line() == "ready"
    code, elapsed = child.terminate(within=DRAIN_BOUND_S)
    assert code == 143
    assert elapsed < 2


def test_entry_exits_after_the_graceful_drain_with_a_request_still_running():
    """A request blocked in a worker thread no longer holds the process to SIGKILL."""
    child = Pid1("serve")
    port = int(child.line().split()[1])
    loopback = ("127.0.0.1", port)
    client = socket.create_connection(loopback, timeout=10)  # hermetic-ok: the child under test
    try:
        client.sendall(b"GET /slow HTTP/1.1\r\nHost: daemon\r\n\r\n")
        assert child.line() == "blocked"
        code, elapsed = child.terminate(within=DRAIN_BOUND_S)
    finally:
        client.close()
    assert code == 143
    assert GRACEFUL_SHUTDOWN_S - 1 <= elapsed < DRAIN_BOUND_S
