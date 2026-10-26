"""P0-8: the relay serves many streams on one loop, bounded per owner."""

from __future__ import annotations

import os
import resource
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from tinyassets import rpc_frames as rf
from tinyassets.boxes import relay as relays

_UID = 300001


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A same-uid broker rig; production's peer check is covered elsewhere."""
    monkeypatch.setattr(relays, "peer", lambda sock: (os.getpid(), 1002, 1002))
    monkeypatch.setattr(os, "chown", lambda *args: None)  # Any owner uid without root.
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    wanted = 8192 if hard == resource.RLIM_INFINITY else min(hard, 8192)
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, wanted), hard))
    flood = threading.Event()
    broker_path = tmp_path / "broker.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(broker_path))
    listener.listen(1024)

    def serve(conn):
        with conn:
            rf.read_frame_blocking(conn)
            conn.sendall(rf.control(rf.CONNECTION, {"op": "BOX_ADMITTED"}))
            try:
                if flood.is_set():
                    while True:
                        conn.sendall(b"x" * 65536)
                while data := conn.recv(65536):
                    conn.sendall(data)
            except OSError:
                pass

    def accept():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=serve, args=(conn,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    relay = relays.OwnerRelays(tmp_path / "relay", SimpleNamespace(broker_socket=broker_path))
    try:
        yield SimpleNamespace(relay=relay, flood=flood)
    finally:
        relay.close()
        listener.close()
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def _open(path):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10)
    for _ in range(500):
        try:
            sock.connect(str(path))
            break
        except BlockingIOError:  # A full accept queue is backpressure, not refusal.
            time.sleep(.01)
    try:
        sock.sendall(rf.control(rf.CONNECTION, {"op": "BOX_CONNECT"}))
    except OSError:
        pass  # Refused before it read the request.
    return sock


def _connected(sock):
    try:
        frame = rf.read_frame_blocking(sock)
    except (OSError, rf.FrameError):
        return False
    return frame is not None and frame.control() == {"op": "BOX_CONNECTED"}


def _whoami(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(str(path))
        sock.sendall(rf.control(rf.CONNECTION, {"op": "WHOAMI"}))
        return rf.read_frame_blocking(sock).control()


def _wait_open(relay, uid, below):
    deadline = time.monotonic() + 5  # The relay frees a slot once it sees the close.
    while relay._open.get(uid, 0) >= below and time.monotonic() < deadline:
        time.sleep(.01)


@pytest.mark.verifies("owner-daemon-in-box:Owner egress uses the admitted broker channel")
def test_five_hundred_streams_across_fifty_owners_while_one_owner_floods(rig):
    paths = [rig.relay.bind(f"owner{n}", _UID + n) for n in range(51)]
    greedy = [_open(paths[50]) for _ in range(400)]
    streams = [_open(path) for path in paths[:50] for _ in range(10)]
    try:
        assert all(_connected(sock) for sock in streams)  # None of the 500 refused.
        admitted = sum(_connected(sock) for sock in greedy)
        assert admitted == relays.OWNER_STREAMS  # Only the flooding owner is bounded.
        for index, sock in enumerate(streams):
            sock.sendall(b"stream-%d" % index)
        for index, sock in enumerate(streams):
            assert sock.recv(64) == b"stream-%d" % index
    finally:
        for sock in streams + greedy:
            sock.close()


@pytest.mark.verifies("owner-daemon-in-box:Owner egress uses the admitted broker channel")
def test_the_fleet_bound_refuses_and_a_closed_stream_frees_its_slot(rig, monkeypatch):
    monkeypatch.setattr(relays, "FLEET_STREAMS", 3)
    first, second = rig.relay.bind("one", _UID), rig.relay.bind("two", _UID + 1)
    streams = [_open(first), _open(first), _open(second)]
    try:
        assert all(_connected(sock) for sock in streams)
        extra = _open(second)
        assert not _connected(extra)  # Closed without an answer.
        extra.close()
    finally:
        for sock in streams:
            sock.close()
    _wait_open(rig.relay, _UID, 1)
    again = _open(second)
    try:
        assert _connected(again)
    finally:
        again.close()


@pytest.mark.verifies("owner-daemon-in-box:Owner egress uses the admitted broker channel")
def test_a_retired_endpoint_frees_its_listener_for_the_next_bind(rig):
    for round_ in range(3):
        path = rig.relay.bind("owner", _UID)
        assert _whoami(path) == {"op": "OWNER_IS", "principal": "owner", "uid": _UID}
        rig.relay.retire(_UID)
        assert not path.exists()
        other = rig.relay.bind(f"next{round_}", _UID + 1 + round_)  # Reuses the descriptor.
        assert _whoami(other)["principal"] == f"next{round_}"


@pytest.mark.verifies("owner-daemon-in-box:Owner egress uses the admitted broker channel")
def test_binding_while_streams_churn_never_stalls_the_loop(rig):
    busy = rig.relay.bind("busy", _UID)
    stop = threading.Event()

    def churn():
        while not stop.is_set():
            _whoami(busy)

    with ThreadPoolExecutor(8) as pool:
        for _ in range(8):
            pool.submit(churn)
        try:
            started = time.monotonic()
            paths = [rig.relay.bind(f"owner{n}", _UID + 1 + n) for n in range(40)]
            assert time.monotonic() - started < 5
            assert _whoami(paths[-1])["principal"] == "owner39"
        finally:
            stop.set()


@pytest.mark.verifies("owner-daemon-in-box:Owner egress uses the admitted broker channel")
def test_a_guest_that_stops_reading_cannot_hold_its_slot(rig, monkeypatch):
    monkeypatch.setattr(relays, "_IDLE", .5)
    rig.flood.set()
    path = rig.relay.bind("owner", _UID)
    stalled = _open(path)
    try:
        assert _connected(stalled)
        stalled.shutdown(socket.SHUT_WR)  # Half-closed, and never reads again.
        deadline = time.monotonic() + 5
        while rig.relay._open.get(_UID) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not rig.relay._open.get(_UID)
    finally:
        stalled.close()
