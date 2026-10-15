"""Movement through the live HTTP handlers, and the screen push contract.

Account setup in this file runs in-process against a temporary data dir.
The timed movement and the fan-out run in another process that serves
``POST /app/api/live`` and ``GET /app/api/live/stream``. Those are the handlers
the app mounts. A container runtime is used when this machine has one.
"""

from __future__ import annotations

import http.client
import json
import multiprocessing as mp
import os
import shutil
import socket
import struct
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
    StateConflict,
    checkpoint,
    definition_bytes,
    definition_count,
    read_record,
    record_key_bytes,
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
            "durability": "transient",
        },
        "presence": {
            "ownership": "creator", "read": "public", "write": "owner",
            "public": ["online"], "secret": [],
        },
    },
    "sync": ["character", "ship", "presence"],
    "checkpoint_s": 60,
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


def test_transient_checkpoint_is_one_log_row(tmp_path: Path):
    """Ticks stay in memory. A checkpoint stores the latest body once."""
    universe = "app-motion"
    _app(tmp_path, "owner", universe, WORLD)
    _join(tmp_path, "owner", universe, "ada")
    created = write_record(
        tmp_path, actor="ada", universe_id=universe, kind="ship", key="ship",
        body={"where": [0, 0], "heading": 0}, expect_etag=0,
    )
    assert created["etag"] == 1
    etag = 1
    for step in range(30):
        wrote = write_record(
            tmp_path, actor="ada", universe_id=universe, kind="ship", key="ship",
            body={"where": [step + 1, 0], "heading": step}, expect_etag=etag,
        )
        etag = int(wrote["etag"])
    assert checkpoint(tmp_path, universe) == 1
    stored = read_record(tmp_path, actor="ada", universe_id=universe, key="ada/ship")
    assert stored["body"]["where"] == [30, 0]
    assert stored["etag"] == 31
    with _connect(tmp_path) as conn:
        row = conn.execute(
            "SELECT private_json, etag FROM app_records"
            " WHERE universe_id = ? AND record_key = ?",
            (universe, "ada/ship"),
        ).fetchone()
        logged = conn.execute(
            "SELECT COUNT(*) FROM app_log WHERE universe_id = ? AND record_key = ?",
            (universe, "ada/ship"),
        ).fetchone()[0]
    assert json.loads(row["private_json"])["where"] == [30, 0]
    assert int(row["etag"]) == 31
    assert record_key_bytes(tmp_path, universe, "ada/ship") > int(row["etag"])
    assert int(logged) == 2
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def again() -> None:
        barrier.wait(timeout=5)
        try:
            write_record(
                tmp_path, actor="ada", universe_id=universe, kind="ship", key="ship",
                body={"where": [99, 0], "heading": 1}, expect_etag=etag,
            )
        except StateConflict:
            outcomes.append("conflict")
        else:
            outcomes.append("ok")

    threads = [threading.Thread(target=again) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert sorted(outcomes) == ["conflict", "ok"]
    final = read_record(tmp_path, actor="ada", universe_id=universe, key="ada/ship")
    assert final["etag"] == etag + 1


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
            stdout=subprocess.DEVNULL, stderr=open("/tmp/live-server.err", "w"),
        )
        source = "http_process"
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            err = ""
            if proc.stderr is not None:
                err = proc.stderr.read().decode("utf-8", "replace")
            else:
                try:
                    err = open("/tmp/live-server.err", encoding="utf-8", errors="replace").read()
                except OSError:
                    err = ""
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


# Linux SO_TIMESTAMPNS. The kernel records when the bytes arrived, so a
# reader that wakes late does not move the delivery clock.
_SO_TIMESTAMPNS = 35


def _arrived(ancillary) -> float:
    """CLOCK_MONOTONIC time of the packet the kernel just handed up.

    Writer clocks in other processes use the same clock, so the two subtract.
    """
    for _level, kind, raw in ancillary:
        if kind == _SO_TIMESTAMPNS and len(raw) >= 16:
            sec, nsec = struct.unpack_from("qq", raw)
            return time.monotonic() - time.time() + sec + nsec / 1e9
    return time.monotonic()


def _tcp_nodelay(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        return


def _watch(port: int, actor: str, universe: str, since: int, gate, stop, seen, order, errors):
    """One player, one stream. Delivery is the kernel time of the recv."""
    entered = False
    while not stop.is_set():
        sock = socket.socket()
        sock.settimeout(30)
        try:
            sock.connect(("127.0.0.1", port))
            _tcp_nodelay(sock)
            sock.setsockopt(socket.SOL_SOCKET, _SO_TIMESTAMPNS, 1)
            path = (
                f"/app/api/live/stream?universe_id={universe}&since={since}&kinds=ship"
            )
            raw = (
                f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                f"Authorization: Bearer acct:{actor}\r\nConnection: keep-alive\r\n\r\n"
            ).encode()
            sock.sendall(raw)
            buf = b""
            while b"\r\n\r\n" not in buf:
                data, _anc, _flags, _addr = sock.recvmsg(65536, 256)
                if not data:
                    errors.append("stream closed")
                    return
                buf += data
            head, buf = buf.split(b"\r\n\r\n", 1)
            status = int(head.split(b"\r\n", 1)[0].split()[1])
            if status != 200:
                errors.append(f"stream {status}")
                return
            if not entered:
                entered = True
                try:
                    gate.wait(timeout=60)
                except threading.BrokenBarrierError:
                    return
            sock.settimeout(5.0)
            pending = buf
            payload = b""
            while not stop.is_set():
                try:
                    data, anc, _flags, _addr = sock.recvmsg(65536, 256)
                except (TimeoutError, socket.timeout):
                    break
                if not data:
                    break
                arrived = _arrived(anc)
                pending += data
                while True:
                    line, sep, rest = pending.partition(b"\r\n")
                    if not sep:
                        break
                    try:
                        size = int(line.split(b";", 1)[0], 16)
                    except ValueError:
                        errors.append("chunk")
                        return
                    if len(rest) < size + 2:
                        break
                    payload += rest[:size]
                    pending = rest[size + 2:]
                    while b"\n\n" in payload:
                        block, _, payload = payload.partition(b"\n\n")
                        for row in block.split(b"\n"):
                            if not row.startswith(b"data:"):
                                continue
                            page = json.loads(row[5:])
                            for event in page.get("events") or []:
                                seq = int(event["seq"])
                                seen.setdefault(seq, arrived)
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
            sock.close()


def _checkpoint(port: int, universe: str) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        body = json.dumps({"op": "checkpoint", "universe_id": universe}).encode()
        conn.request(
            "POST", "/app/api/live", body=body,
            headers={
                "Authorization": "Bearer acct:p0",
                "Content-Type": "application/json",
            },
        )
        resp = conn.getresponse()
        raw = resp.read()
        if resp.status != 200:
            raise AssertionError(f"checkpoint {resp.status} {raw[:200]!r}")
    finally:
        conn.close()


def _post_move(conn, universe: str, player: str, etag: int, step: int, record):
    """One position write. ``record`` is None for the untimed warmup."""
    along = 0 if step < 0 else step + 1
    body = json.dumps({
        "op": "write",
        "universe_id": universe,
        "kind": "ship",
        "key": "ship",
        "expect_etag": etag,
        "body": {
            "where": [along, 0],
            "heading": 0 if step < 0 else step,
            "account_id": "owner",
        },
    }).encode()
    started = time.monotonic()
    conn.request(
        "POST", "/app/api/live", body=body,
        headers={
            "Authorization": f"Bearer acct:{player}",
            "Content-Type": "application/json",
        },
    )
    resp = conn.getresponse()
    raw = resp.read()
    finished = time.monotonic()
    if resp.status == 409:
        return etag, "conflict"
    if resp.status != 200:
        return etag, f"write {resp.status} {raw[:200]!r}"
    doc = json.loads(raw)
    if doc.get("owner") != player:
        return etag, "owner spoofed"
    if record is not None:
        record.append({
            "seq": int(doc["seq"]),
            "start": started,
            "write_ms": (finished - started) * 1000,
        })
    return int(doc["etag"]), ""


def _move(port: int, universe: str, player: str, etag: int, updates: int, writes, errors):
    """Warm the connection, then time ``updates`` moves on it.

    Opening hundreds of connections at once makes the first request on each
    one wait behind the others. A player who is already connected does not
    pay that on the next move, so the sample starts at the second request.
    """
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.connect()
        if conn.sock is not None:
            _tcp_nodelay(conn.sock)
        etag, err = _post_move(conn, universe, player, etag, -1, None)
        if err:
            errors.append(err)
            return etag
        for step in range(updates):
            etag, err = _post_move(conn, universe, player, etag, step, writes)
            if err:
                errors.append(err)
                return etag
    except OSError as exc:
        errors.append(str(exc))
    finally:
        conn.close()
    return etag


def _play_slice(port, universe, players, since, etags, ready, out_path):
    """One process owns a slice of players: their stream and their moves."""
    stop = threading.Event()
    gate = threading.Barrier(len(players) + 1)
    watch_errors: list[str] = []
    buckets = [{"seen": {}, "order": []} for _ in players]
    threads = []
    for actor, bucket in zip(players, buckets):
        thread = threading.Thread(
            target=_watch,
            args=(port, actor, universe, since, gate, stop, bucket["seen"], bucket["order"],
                  watch_errors),
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    try:
        gate.wait(timeout=90)
    except threading.BrokenBarrierError:
        watch_errors.append("local barrier")
    ready.wait()
    writes: list[dict] = []
    write_errors: list[str] = []
    final = dict(etags)
    lock = threading.Lock()

    def one(player: str) -> None:
        etag = _move(port, universe, player, etags[player], 4, writes, write_errors)
        with lock:
            final[player] = etag

    movers = [threading.Thread(target=one, args=(player,), daemon=True) for player in players]
    for thread in movers:
        thread.start()
    for thread in movers:
        thread.join(timeout=120)
    time.sleep(1.0)
    stop.set()
    for thread in threads:
        thread.join(timeout=8)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump({
            "watch_errors": watch_errors[:8],
            "write_errors": write_errors[:8],
            "writes": writes,
            "etags": final,
            "buckets": buckets,
        }, handle)


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
    players = [f"p{index}" for index in range(512)]
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
    cursor = 0
    while True:
        page = sync(
            tmp_path, actor="owner", universe_id=universe, since=cursor, limit=500,
        )
        nxt = int(page["cursor"])
        if not page["events"] or nxt == cursor:
            cursor = nxt
            break
        cursor = nxt
    etags = {player: 1 for player in players}
    proc, port, source = _start_server(tmp_path)
    # One process reading every stream is the measuring client, not a player.
    # Spread each player's stream and moves across this machine's cores.
    client_processes = min(4, os.cpu_count() or 1)
    levels = []
    try:
        for count in (16, 64, 128, 256, 512):
            group = players[:count]
            procs_n = min(client_processes, count)
            ctx = mp.get_context("spawn")
            ready = ctx.Barrier(procs_n)
            slices = [group[index::procs_n] for index in range(procs_n)]
            children = []
            paths = []
            for index, sl in enumerate(slices):
                path = tmp_path / f"level-{count}-{index}.json"
                paths.append(path)
                if path.exists():
                    path.unlink()
                part = {player: etags[player] for player in sl}
                child = ctx.Process(
                    target=_play_slice,
                    args=(port, universe, sl, cursor, part, ready, str(path)),
                )
                child.start()
                children.append(child)
            for child in children:
                child.join(timeout=180)
                if child.is_alive():
                    child.kill()
                    child.join(timeout=5)
            packed = []
            for path in paths:
                if not path.exists():
                    packed.append({
                        "watch_errors": [f"missing {path.name}"],
                        "write_errors": [],
                        "writes": [],
                        "etags": {},
                        "buckets": [],
                    })
                    continue
                packed.append(json.loads(path.read_text(encoding="utf-8")))
            writes = []
            write_errors = []
            watch_errors = []
            seen = []
            order = []
            for item in packed:
                writes.extend(item["writes"])
                write_errors.extend(item["write_errors"])
                watch_errors.extend(item["watch_errors"])
                etags.update({player: int(etag) for player, etag in item["etags"].items()})
                for bucket in item["buckets"]:
                    seen.append({int(seq): float(at) for seq, at in bucket["seen"].items()})
                    order.append(bucket["order"])
            write_ms = [row["write_ms"] for row in writes]
            wanted = {row["seq"] for row in writes}
            by_seq = {row["seq"]: row for row in writes}
            deliver = []
            inversions = 0
            gaps = 0
            for page, seqs in zip(seen, order):
                gaps += len(wanted - set(page))
                previous = 0
                for seq in seqs:
                    if seq < previous:
                        inversions += 1
                    previous = seq
                for seq, row in by_seq.items():
                    arrived = page.get(seq)
                    if arrived is not None:
                        deliver.append((arrived - row["start"]) * 1000)
            stats = {
                "source": source,
                "container_runtime": runtime or None,
                "why_not_container": why,
                "setup": "in_process",
                "client_processes": procs_n,
                "connection": "warm",
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
        _checkpoint(port, universe)
    finally:
        proc.kill()
        proc.wait(timeout=10)
    held = occupancy(account_pool("owner"), db=ledger_path(tmp_path))
    cost = {
        "source": "in_process_read_of_http_data",
        "owner_runs": len(runs_for(tmp_path, "owner")),
        "owner_definition_bytes": definition_bytes(tmp_path, "owner"),
        "player_record_bytes": record_key_bytes(tmp_path, universe, "p0/ship"),
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
    stored = read_record(tmp_path, actor="p0", universe_id=universe, key="p0/ship")
    assert stored["body"]["where"] == [4, 0]
    assert cost["player_record_bytes"] == record_key_bytes(tmp_path, universe, "p0/ship")
    assert cost["player_record_bytes"] > stored["etag"]
    with _connect(tmp_path) as conn:
        logged = conn.execute(
            "SELECT COUNT(*) FROM app_log WHERE universe_id = ? AND record_key = ?",
            (universe, "p0/ship"),
        ).fetchone()[0]
    assert 1 < int(logged) < 20
    row_256 = next(row for row in levels if row["players"] == 256)
    assert row_256["deliver_p50_ms"] < 50, row_256
    assert row_256["deliver_p99_ms"] < 150, row_256
    assert levels and [row["players"] for row in levels] == [16, 64, 128, 256, 512]
    captured = capsys.readouterr()
    assert "HTTP_MEASURED" in captured.out
