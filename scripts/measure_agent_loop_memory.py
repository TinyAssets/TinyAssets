#!/usr/bin/env python3
"""Measure resident memory per WAITING thin-loop turn (target architecture S7).

N turns are started against a mock server-sent-events model endpoint that
holds every response open (sending only ``: ping`` comments, the way a model
that is still thinking does) and then completes them all. While all N wait,
the client process's resident set is sampled; the per-turn figure is

    (RSS with N turns waiting - RSS before any turn) / N

Each turn is what the thin loop holds for a waiting round: one coroutine, one
open HTTP stream, its SSE line buffer, and its context buffer (system prompt
plus history, ``--context-kb``). When the stream completes the turn folds it
with the production codec (``agent_chat_codec.fold_chat_stream``), so the
measured client is doing real work, not idling on sockets.

What this does NOT include, on purpose, and what the number must be read with:

* the transport here is a direct streaming HTTP client to the mock. In
  production the stream comes from the credential broker (S6's streaming
  contract); until S6 lands the broker answers each round through a spawned
  worker process, which costs a process per in-flight round and is NOT what
  this measures;
* the journal (SQLite, opened per write) and the router's admission, which
  are identical on the old and the new path.

The mock server runs in its own process so its memory never counts.

    python scripts/measure_agent_loop_memory.py --turns 500
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import multiprocessing
import os
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_MiB = 1024 * 1024


# ── the mock model endpoint (separate process) ──────────────────────────────


def _serve(port: int, release_after: float) -> None:
    started = time.monotonic()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                head = await reader.readuntil(b"\r\n\r\n")
            except asyncio.IncompleteReadError:
                return  # the readiness probe: connected, sent nothing
            # Drain the POST body before answering, or the client may see a reset.
            for line in head.decode("latin-1").split("\r\n"):
                name, _, value = line.partition(":")
                if name.strip().lower() == "content-length":
                    await reader.readexactly(int(value.strip()))
            writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n"
                         b"cache-control: no-cache\r\nconnection: close\r\n\r\n")
            await writer.drain()
            while time.monotonic() - started < release_after:
                writer.write(b": ping\n\n")
                await writer.drain()
                await asyncio.sleep(1.0)
            for piece in ("The ", "answer ", "is ", "ready."):
                chunk = {"choices": [{"index": 0, "delta": {"content": piece}}]}
                writer.write(f"data: {json.dumps(chunk)}\n\n".encode())
            done = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            writer.write(f"data: {json.dumps(done)}\n\ndata: [DONE]\n\n".encode())
            await writer.drain()
        finally:
            writer.close()

    async def main() -> None:
        server = await asyncio.start_server(handle, "127.0.0.1", port, backlog=4096)
        async with server:
            await server.serve_forever()

    asyncio.run(main())


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ── the client: N waiting turns ──────────────────────────────────────────────


def _rss() -> int:
    status = Path("/proc/self/status")
    if status.exists():
        for line in status.read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    import psutil  # not a project dependency; only off Linux

    return psutil.Process(os.getpid()).memory_info().rss


async def _turn(client, url: str, context: str, waiting: asyncio.Semaphore,
                connected: list[int]) -> str:
    from tinyassets.providers.agent_chat_codec import fold_chat_stream

    body = {"model": "mock", "stream": True,
            "messages": [{"role": "system", "content": context[: len(context) // 4]},
                         {"role": "user", "content": context[len(context) // 4:]}]}
    lines: list[str] = []
    async with client.stream("POST", url, json=body) as response:
        response.raise_for_status()
        counted = False
        async for line in response.aiter_lines():
            if not counted:
                connected[0] += 1
                counted = True
                waiting.release()
            lines.append(line)
    folded = fold_chat_stream("\n".join(lines))
    return folded["choices"][0]["message"]["content"]


async def _measure(turns: int, context_kb: int, url: str) -> dict:
    import httpx

    # Warm everything a turn touches, then take the baseline.
    from tinyassets.providers import agent_chat_codec  # noqa: F401

    limits = httpx.Limits(max_connections=turns + 8, max_keepalive_connections=0)
    async with httpx.AsyncClient(limits=limits, timeout=httpx.Timeout(600.0),
                                 trust_env=False) as client:
        gc.collect()
        baseline = _rss()
        waiting = asyncio.Semaphore(0)
        connected = [0]
        # Every turn gets its own context buffer: no sharing flatters the number.
        tasks = [asyncio.create_task(_turn(client, url, ("x%05d" % i) * (context_kb * 1024 // 6),
                                           waiting, connected))
                 for i in range(turns)]
        for _ in range(turns):
            await asyncio.wait_for(waiting.acquire(), 120)
        await asyncio.sleep(1.0)
        gc.collect()
        peak = _rss()
        waiting_turns = connected[0]
        answers = await asyncio.gather(*tasks)
    assert all(answer == "The answer is ready." for answer in answers), "a turn misread"
    return {
        "turns": turns,
        "waiting_when_sampled": waiting_turns,
        "context_kb_per_turn": context_kb,
        "rss_baseline_mib": round(baseline / _MiB, 1),
        "rss_waiting_mib": round(peak / _MiB, 1),
        "per_waiting_turn_kib": round((peak - baseline) / turns / 1024, 1),
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--turns", type=int, default=500)
    parser.add_argument("--context-kb", type=int, default=64,
                        help="context buffer per turn (system + history), KiB")
    parser.add_argument("--hold", type=float, default=0.0,
                        help="seconds the mock holds each stream (default: scales with turns)")
    args = parser.parse_args(argv)

    port = _free_port()
    hold = args.hold or max(15.0, args.turns / 20)
    server = multiprocessing.get_context("spawn").Process(
        target=_serve, args=(port, hold), daemon=True)
    server.start()
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
                break
            except OSError:
                time.sleep(0.1)
        result = asyncio.run(_measure(args.turns, args.context_kb,
                                      f"http://127.0.0.1:{port}/v1/chat/completions"))
    finally:
        server.terminate()
        server.join(5)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
