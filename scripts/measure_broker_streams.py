#!/usr/bin/env python3
"""Measure the broker's memory per open stream (S6, I14 "Measurement owed").

A broker server runs in its own process with an injected upstream that holds
every stream open (a model still thinking). The parent opens N streams over
ONE multiplexed connection, waits until all N have their HEAD, samples the
broker process's resident set, then releases them:

    (RSS with N streams open - RSS before any stream) / N

What a stream costs here is what it costs in production minus the real TLS
socket: the server's stream state, its thread (v1 runs one per live upstream
stream: the hardened driver is synchronous), its window, the op record and
the fence's read lock. Linux only (peer credentials, /proc).

    python scripts/measure_broker_streams.py --streams 500
"""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_MiB = 1024 * 1024


def _rss(pid: int) -> int:
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("no VmRSS")


def _threads(pid: int) -> int:
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("Threads:"):
            return int(line.split()[1])
    return -1


def _broker(path: str, state: str, release_file: str) -> None:
    from tinyassets.broker.fence import Fence
    from tinyassets.broker.ops import OpStore
    from tinyassets.broker.server import OWNER, BrokerServer

    class Held:
        status, reason, headers, redirect_count = 200, "OK", {}, 0

        def read(self, max_bytes):
            if Path(release_file).exists():
                return None
            time.sleep(0.5)
            return b": ping\n\n"

        def close(self):
            pass

    class Ledger:
        def authorize_exact(self, **_):
            return object(), object()

    def dispatch_for(*_):
        def dispatch(grant, verb, request, *, stream, idle_s=None, guard=None,
                     on_connect=None, checkpoint=None, deadline_at=None):
            with guard():
                return Held()

        return dispatch

    fence = Fence(Path(state) / "fence.json", verify_lease_proof=lambda g, p: p == "proof")
    fence.barrier(1, "proof")
    server = BrokerServer(ledger_for=lambda p: Ledger(), dispatch_for=dispatch_for,
                          ops=OpStore(Path(state) / "ops.db"), fence=fence,
                          roles={os.getuid(): OWNER})

    async def main():
        listener = await server.serve(Path(path))
        async with listener:
            await asyncio.Event().wait()

    asyncio.run(main())


def _ulid(i: int) -> str:
    from tinyassets.broker.ops import new_op_id

    return new_op_id()


def main(argv: list[str] | None = None) -> int:
    from tinyassets import rpc_frames as rf

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--streams", type=int, default=500)
    args = parser.parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="broker-measure-"))
    sock_path, release = str(work / "b.sock"), str(work / "release")
    child = multiprocessing.get_context("spawn").Process(
        target=_broker, args=(sock_path, str(work), release), daemon=True)
    child.start()
    while not Path(sock_path).exists():
        time.sleep(0.05)
    fence = json.loads((work / "fence.json").read_text())
    time.sleep(0.5)
    baseline, baseline_threads = _rss(child.pid), _threads(child.pid)
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.connect(sock_path)
    for i in range(1, args.streams + 1):
        conn.sendall(rf.control(i, {
            "op": "OPEN", "op_id": _ulid(i), "principal": "p", "command_center": "cc",
            "grant_id": "g", "connection_id": "c", "verb": "POST",
            "request": {"url": "u", "body": {}}, "credit": 65536,
            "generation": fence["generation"], "token": fence["token"],
        }))
    heads = 0
    decoder = rf.Decoder()
    stop_draining = threading.Event()
    conn.settimeout(120)
    while heads < args.streams:
        for frame in decoder.feed(conn.recv(65536)):
            if frame.kind == rf.CONTROL and frame.control()["op"] == "HEAD":
                heads += 1
    time.sleep(1.0)
    peak, peak_threads = _rss(child.pid), _threads(child.pid)

    def drain():
        conn.settimeout(1)
        while not stop_draining.is_set():
            try:
                conn.recv(65536)
            except (TimeoutError, OSError):
                pass

    threading.Thread(target=drain, daemon=True).start()
    Path(release).touch()
    time.sleep(1.5)
    stop_draining.set()
    child.terminate()
    print(json.dumps({
        "streams": args.streams,
        "rss_baseline_mib": round(baseline / _MiB, 1),
        "rss_open_mib": round(peak / _MiB, 1),
        "per_open_stream_kib": round((peak - baseline) / args.streams / 1024, 1),
        "threads_baseline": baseline_threads,
        "threads_open": peak_threads,
        "python": sys.version.split()[0],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
