"""Measure how long a recreate leaves the port dead while a long tool call runs.

    REPRO_IMAGE=<image with fastmcp+uvicorn> python run.py old
    REPRO_IMAGE=... python run.py first-deploy
    REPRO_IMAGE=... python run.py steady

old           the shape before the fix: the container was created with a 60s
              grace (180s in production, scaled down) and `up -d` has no --timeout.
first-deploy  the first deploy AFTER the fix lands: the running container still
              carries the old 60s StopTimeout, and the deploy passes --timeout 20.
steady        later deploys: created with 20s, recreated with --timeout 20.

Each run starts the container, opens an MCP session, calls a tool that sleeps
600s, then recreates the container (GEN changes, standing in for a new image)
while a probe hits the port every 0.25s. It prints the converge time, the
longest stretch with no answer, and whether the new container answers.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
URL = "http://127.0.0.1:18001/mcp"
HEADERS = {"Accept": "application/json, text/event-stream"}


def compose(*args: str, env: dict[str, str]) -> float:
    started = time.monotonic()
    subprocess.run(["docker", "compose", "-f", str(HERE / "compose.yml"), *args],
                   check=True, env={**os.environ, **env})
    return time.monotonic() - started


def up(timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            httpx.get(URL, timeout=1)
            return
        except httpx.HTTPError:
            time.sleep(0.25)
    raise SystemExit("server never came up")


def start_long_turn() -> None:
    client = httpx.Client(headers=HEADERS, timeout=None)
    init = client.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "repro", "version": "1"}}})
    client.headers["mcp-session-id"] = init.headers["mcp-session-id"]
    client.post(URL, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call() -> None:
        try:
            client.post(URL, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                   "params": {"name": "converse", "arguments": {}}})
        except httpx.HTTPError:
            pass

    threading.Thread(target=call, daemon=True).start()
    time.sleep(2)  # let the tool's worker thread start


def main(variant: str) -> None:
    created = {"old": ("60s", "50"), "first-deploy": ("60s", "50"),
               "steady": ("20s", "10")}[variant]
    deploy = {"old": ("60s", "50", []), "first-deploy": ("20s", "10", ["--timeout", "20"]),
              "steady": ("20s", "10", ["--timeout", "20"])}[variant]
    compose("down", "--timeout", "0", env={"GEN": "0", "GRACE": "1s", "GRACEFUL_SHUTDOWN_S": "1"})
    compose("up", "-d", env={"GEN": "1", "GRACE": created[0], "GRACEFUL_SHUTDOWN_S": created[1]})
    up()
    start_long_turn()

    dead: list[float] = []
    stop = threading.Event()

    def probe() -> None:
        while not stop.is_set():
            try:
                httpx.get(URL, timeout=1)
            except httpx.HTTPError:
                dead.append(time.monotonic())
            time.sleep(0.25)

    prober = threading.Thread(target=probe, daemon=True)
    prober.start()
    converge = compose("up", "-d", *deploy[2],
                       env={"GEN": "2", "GRACE": deploy[0], "GRACEFUL_SHUTDOWN_S": deploy[1]})
    up()
    stop.set()
    prober.join()
    window = (dead[-1] - dead[0]) if dead else 0.0
    print(f"variant={variant} converge={converge:.1f}s port_dead_window={window:.1f}s "
          f"failed_probes={len(dead)} new_container_serving=yes")
    compose("down", "--timeout", "0", env={"GEN": "0", "GRACE": "1s", "GRACEFUL_SHUTDOWN_S": "1"})


if __name__ == "__main__":
    main(sys.argv[1])
