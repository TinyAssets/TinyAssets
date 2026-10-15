"""Movement through the live HTTP handlers, and the screen push contract.

Account setup in this file runs in-process against a temporary data dir.
The timed movement and the fan-out run in another process that serves
``POST /app/api/live`` and ``GET /app/api/live/stream``. Those are the handlers
the app mounts. A container runtime is used when this machine has one.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from tinyassets.daemon_server import (
    grant_universe_access,
    initialize_author_server,
    set_founder_home,
)
from tinyassets.onboarding.ui_frame import FRAME_CSP
from tinyassets.shared_state import (
    definition_bytes,
    definition_count,
    read_record,
    runs_for,
    save_app,
    sync,
    write_record,
)
from tinyassets.storage import _connect
from tinyassets.universe_owner import record_creation
from tinyassets.universe_seats import account_pool, ledger_path, occupancy

WORLD = {
    "schema": "app/v1",
    "records": {
        "character": {
            "ownership": "creator", "read": "members", "write": "owner",
            "public": ["name", "where"], "secret": [],
        },
        "ship": {
            "ownership": "creator", "read": "public", "write": "owner",
            "public": ["where", "heading"], "secret": [],
        },
        "presence": {
            "ownership": "creator", "read": "public", "write": "owner",
            "public": ["online"], "secret": [],
        },
    },
    "sync": ["character", "ship", "presence"],
}


def _account(base: Path, actor: str, home: str) -> None:
    initialize_author_server(base)
    (base / home).mkdir(parents=True, exist_ok=True)
    grant_universe_access(
        base, universe_id=home, actor_id=actor, permission="admin", granted_by=actor,
    )
    set_founder_home(base, founder_sub=actor, universe_id=home)
    with _connect(base) as conn:
        record_creation(conn, universe_id=home, owner_id=actor)


def _app(base: Path, owner: str, universe: str, document: dict) -> None:
    _account(base, owner, f"home-{owner}")
    initialize_author_server(base)
    (base / universe).mkdir(parents=True, exist_ok=True)
    grant_universe_access(
        base, universe_id=universe, actor_id=owner, permission="admin", granted_by=owner,
    )
    with _connect(base) as conn:
        record_creation(conn, universe_id=universe, owner_id=owner)
    save_app(base, actor=owner, universe_id=universe, document=document)


def _join(base: Path, owner: str, universe: str, actor: str) -> None:
    from tinyassets.shared_state import mint_invite, redeem_invite

    _account(base, actor, f"home-{actor}")
    token = mint_invite(base, actor=owner, universe_id=universe)["token"]
    redeem_invite(base, token=token, actor=actor)


def _pct(samples: list[float], percent: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int(round((percent / 100) * (len(ordered) - 1))))
    return ordered[index]


def _runtime() -> str:
    for name in ("docker", "podman", "nerdctl"):
        if shutil.which(name):
            return name
    return ""


def test_frame_receives_live_events_without_a_socket():
    frame = Path("tinyassets/onboarding/ui_frame.py").read_text(encoding="utf-8")
    shell = Path("tinyassets/onboarding/app_ui.js").read_text(encoding="utf-8")
    routes = Path("tinyassets/onboarding/__init__.py").read_text(encoding="utf-8")
    assert 'message.type === "live"' in frame
    assert 'return call("subscribe"' in frame
    assert "subscribe:\"subscribe\"" in shell
    assert "/app/api/live/stream" in shell
    assert "this.home" in shell
    assert '"/app/api/live"' in routes
    assert '"/app/api/live/stream"' in routes
    assert "connect-src data: blob:" in FRAME_CSP
    assert "http:" not in FRAME_CSP.split("connect-src", 1)[1].split(";", 1)[0]
    assert "ws:" not in FRAME_CSP


def test_interest_cursor_moves_past_other_kinds(tmp_path: Path):
    universe = "app-echoes"
    _app(tmp_path, "owner", universe, WORLD)
    _join(tmp_path, "owner", universe, "ada")
    write_record(
        tmp_path, actor="ada", universe_id=universe, kind="presence", key="presence",
        body={"online": True}, expect_etag=0,
    )
    write_record(
        tmp_path, actor="ada", universe_id=universe, kind="ship", key="ship",
        body={"where": [1, 0], "heading": 0}, expect_etag=0,
    )
    page = sync(tmp_path, actor="ada", universe_id=universe, since=0, kinds=["ship"])
    assert page["events"]
    assert {event["kind"] for event in page["events"]} == {"ship"}
    again = sync(
        tmp_path, actor="ada", universe_id=universe, since=page["cursor"], kinds=["ship"],
    )
    assert again["events"] == []
    assert again["cursor"] == page["cursor"]


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _start_server(base: Path) -> tuple[subprocess.Popen, int, str]:
    """Serve the live handlers. A container when a runtime exists, else a process."""
    runtime = _runtime()
    port = _free_port()
    env = os.environ.copy()
    env["TINYASSETS_DATA_DIR"] = str(base)
    env["TINYASSETS_LIVE_TEST_AUTH"] = "1"
    env["PORT"] = str(port)
    env["PYTHONUNBUFFERED"] = "1"
    if runtime:
        proc = subprocess.Popen(
            [
                runtime, "run", "--rm", "--network", "host",
                "-e", "TINYASSETS_DATA_DIR=/data",
                "-e", "TINYASSETS_LIVE_TEST_AUTH=1",
                "-e", f"PORT={port}",
                "-v", f"{base}:/data",
                "-v", f"{Path.cwd()}:/src",
                "-w", "/src",
                "python:3.12-slim",
                "python", "-m", "tinyassets.shared_live",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        source = "http_container"
    else:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tinyassets.shared_live"],
            env=env, cwd=str(Path.cwd()),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        source = "http_process"
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            raise RuntimeError(err[-2000:])
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            conn.request(
                "POST", "/app/api/live", body=b"{}",
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            resp.read()
            conn.close()
            if resp.status in {400, 401, 403}:
                return proc, port, source
        except OSError:
            time.sleep(0.05)
    proc.kill()
    raise RuntimeError("live server did not accept a connection")


def _watch(port: int, actor: str, universe: str, since: int, gate, stop, seen, order, errors):
    entered = False
    while not stop.is_set():
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        try:
            path = (
                f"/app/api/live/stream?universe_id={universe}&since={since}"
                "&kinds=ship"
            )
            conn.request("GET", path, headers={"Authorization": f"Bearer acct:{actor}"})
            resp = conn.getresponse()
            if resp.status != 200:
                errors.append(f"stream {resp.status}")
                return
            if conn.sock is not None:
                conn.sock.settimeout(5.0)
            if not entered:
                entered = True
                try:
                    gate.wait(timeout=30)
                except threading.BrokenBarrierError:
                    return
            buf = b""
            while not stop.is_set():
                try:
                    chunk = resp.read1(8192)
                except (TimeoutError, socket.timeout, ValueError):
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n\n" in buf:
                    block, buf = buf.split(b"\n\n", 1)
                    for line in block.split(b"\n"):
                        if not line.startswith(b"data:"):
                            continue
                        page = json.loads(line[5:])
                        now = time.perf_counter()
                        for event in page.get("events") or []:
                            seq = int(event["seq"])
                            seen.setdefault(seq, now)
                            order.append(seq)
                            if event.get("kind") != "ship":
                                errors.append("unexpected kind")
                            if isinstance(page.get("cursor"), int):
                                since = max(since, int(page["cursor"]))
        except (TimeoutError, socket.timeout, OSError) as exc:
            if stop.is_set():
                return
            errors.append(str(exc))
            return
        finally:
            conn.close()


def _move(port: int, universe: str, player: str, etag: int, updates: int, writes, errors):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        for step in range(updates):
            body = json.dumps({
                "op": "write",
                "universe_id": universe,
                "kind": "ship",
                "key": "ship",
                "expect_etag": etag,
                "body": {"where": [step + 1, 0], "heading": step, "account_id": "owner"},
            }).encode()
            started = time.perf_counter()
            conn.request(
                "POST", "/app/api/live", body=body,
                headers={
                    "Authorization": f"Bearer acct:{player}",
                    "Content-Type": "application/json",
                },
            )
            resp = conn.getresponse()
            raw = resp.read()
            finished = time.perf_counter()
            if resp.status == 409:
                errors.append("conflict")
                return etag
            if resp.status != 200:
                errors.append(f"write {resp.status} {raw[:200]!r}")
                return etag
            doc = json.loads(raw)
            if doc.get("owner") != player:
                errors.append("owner spoofed")
            writes.append({
                "seq": int(doc["seq"]),
                "start": started,
                "done": finished,
                "write_ms": (finished - started) * 1000,
            })
            etag = int(doc["etag"])
    except OSError as exc:
        errors.append(str(exc))
    finally:
        conn.close()
    return etag


def test_http_movement_ramp(tmp_path: Path, capsys):
    open("/tmp/http-measured.jsonl", "w", encoding="utf-8").close()
    runtime = _runtime()
    why = "" if runtime else (
        "no docker, podman, or nerdctl; this machine is already a container "
        "without a nested runtime, so the numbers are from a separate process "
        "serving the same handlers"
    )
    universe = "app-echoes"
    _app(tmp_path, "owner", universe, WORLD)
    players = [f"p{index}" for index in range(256)]
    for player in players:
        _join(tmp_path, "owner", universe, player)
        write_record(
            tmp_path, actor=player, universe_id=universe, kind="presence", key="presence",
            body={"online": True}, expect_etag=0,
        )
        write_record(
            tmp_path, actor=player, universe_id=universe, kind="ship", key="ship",
            body={"where": [0, 0], "heading": 0}, expect_etag=0,
        )
    cursor = sync(tmp_path, actor="owner", universe_id=universe, since=0)["cursor"]
    etags = {player: 1 for player in players}
    proc, port, source = _start_server(tmp_path)
    levels = []
    try:
        for count in (16, 64, 128, 256):
            group = players[:count]
            gate = threading.Barrier(count + 1)
            stop = threading.Event()
            seen = [dict() for _ in group]
            order = [[] for _ in group]
            watch_errors: list[str] = []
            write_errors: list[str] = []
            writes: list[dict] = []
            watchers = []
            for index, player in enumerate(group):
                thread = threading.Thread(
                    target=_watch,
                    args=(port, player, universe, cursor, gate, stop, seen[index], order[index],
                          watch_errors),
                    daemon=True,
                )
                thread.start()
                watchers.append(thread)
            gate.wait(timeout=30)
            movers = []
            for player in group:
                thread = threading.Thread(
                    target=lambda player=player: etags.__setitem__(
                        player,
                        _move(port, universe, player, etags[player], 4, writes, write_errors),
                    ),
                    daemon=True,
                )
                thread.start()
                movers.append(thread)
            for thread in movers:
                thread.join(timeout=60)
            deadline = time.time() + 20
            wanted = {row["seq"] for row in writes}
            while time.time() < deadline:
                if wanted and all(wanted <= set(page) for page in seen):
                    break
                time.sleep(0.05)
            stop.set()
            for thread in watchers:
                thread.join(timeout=5)
            write_ms = [row["write_ms"] for row in writes]
            deliver = []
            inversions = 0
            gaps = 0
            for page, seqs in zip(seen, order):
                missing = wanted - set(page)
                gaps += len(missing)
                previous = 0
                for seq in seqs:
                    if seq < previous:
                        inversions += 1
                    previous = seq
                for row in writes:
                    arrived = page.get(row["seq"])
                    if arrived is not None:
                        deliver.append((arrived - row["start"]) * 1000)
            stats = {
                "source": source,
                "container_runtime": runtime or None,
                "why_not_container": why,
                "setup": "in_process",
                "players": count,
                "updates": 4,
                "write_p50_ms": round(_pct(write_ms, 50), 3),
                "write_p99_ms": round(_pct(write_ms, 99), 3),
                "deliver_p50_ms": round(_pct(deliver, 50), 3),
                "deliver_p99_ms": round(_pct(deliver, 99), 3),
                "writes": len(writes),
                "conflicts": write_errors.count("conflict"),
                "errors": write_errors[:8],
                "watch_errors": watch_errors[:8],
                "drops": gaps,
                "inversions": inversions,
                "lost": count * 4 - len(writes),
            }
            levels.append(stats)
            cursor = max(wanted) if wanted else cursor
            line = "HTTP_MEASURED " + json.dumps(stats)
            print(line, flush=True)
            with open("/tmp/http-measured.jsonl", "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            assert stats["lost"] == 0
            assert stats["drops"] == 0
            assert stats["inversions"] == 0
            assert stats["conflicts"] == 0
            assert not write_errors
            assert not watch_errors
    finally:
        proc.kill()
        proc.wait(timeout=10)
    held = occupancy(account_pool("owner"), db=ledger_path(tmp_path))
    cost = {
        "source": "in_process_read_of_http_data",
        "owner_runs": len(runs_for(tmp_path, "owner")),
        "owner_definition_bytes": definition_bytes(tmp_path, "owner"),
        "player_record_bytes": read_record(
            tmp_path, actor="p0", universe_id=universe, key="p0/ship",
        )["etag"],
        "owner_seats_running": int(held["running"]),
        "definitions": definition_count(tmp_path, universe),
    }
    cost_line = "HTTP_COST " + json.dumps(cost)
    print(cost_line, flush=True)
    with open("/tmp/http-measured.jsonl", "a", encoding="utf-8") as handle:
        handle.write(cost_line + "\n")
    assert cost["owner_runs"] == 0
    assert cost["owner_seats_running"] == 0
    assert cost["definitions"] == 1
    assert cost["player_record_bytes"] > 1
    assert levels and [row["players"] for row in levels] == [16, 64, 128, 256]
    captured = capsys.readouterr()
    assert "HTTP_MEASURED" in captured.out
