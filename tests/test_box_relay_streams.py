"""P0-8: the relay serves many streams on one loop, bounded per owner."""

from __future__ import annotations

import os
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from tinyassets import rpc_frames as rf
from tinyassets.boxes import relay as relays


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A same-uid echo broker; production's peer check is covered elsewhere."""
    monkeypatch.setattr(relays, "peer", lambda sock: (os.getpid(), 1002, 1002))
    broker_path = tmp_path / "broker.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(broker_path))
    listener.listen(512)

    def echo(conn):
        with conn:
            rf.read_frame_blocking(conn)
            conn.sendall(rf.control(rf.CONNECTION, {"op": "BOX_ADMITTED"}))
            while data := conn.recv(65536):
                conn.sendall(data)

    def accept():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=echo, args=(conn,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    relay = relays.OwnerRelays(tmp_path / "relay",
                               SimpleNamespace(broker_socket=broker_path))
    path = relay.bind("owner", os.getuid())
    try:
        yield SimpleNamespace(relay=relay, path=path)
    finally:
        relay.close()
        listener.close()


def _open(path):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect(str(path))
    sock.sendall(rf.control(rf.CONNECTION, {"op": "BOX_CONNECT"}))
    return sock


def _connected(sock):
    try:
        frame = rf.read_frame_blocking(sock)
    except (OSError, rf.FrameError):
        return False
    return frame is not None and frame.control() == {"op": "BOX_CONNECTED"}


@pytest.mark.verifies("owner-daemon-in-box:Owner egress uses the admitted broker channel")
def test_far_more_than_sixty_four_streams_stay_open_at_once(rig):
    streams = [_open(rig.path) for _ in range(120)]
    try:
        assert all(_connected(sock) for sock in streams)
        for index, sock in enumerate(streams):
            sock.sendall(b"stream-%d" % index)
        for index, sock in enumerate(streams):
            assert sock.recv(64) == b"stream-%d" % index
    finally:
        for sock in streams:
            sock.close()


@pytest.mark.verifies("owner-daemon-in-box:Owner egress uses the admitted broker channel")
def test_a_stream_past_its_owners_bound_is_refused_and_others_are_not(rig, monkeypatch):
    monkeypatch.setattr(relays, "OWNER_STREAMS", 3)
    streams = [_open(rig.path) for _ in range(3)]
    try:
        assert all(_connected(sock) for sock in streams)
        extra = _open(rig.path)
        assert not _connected(extra)  # Closed without an answer.
        extra.close()
        assert rig.relay._admit(os.getuid() + 1)  # Another owner still has room.
        rig.relay._release(os.getuid() + 1)
    finally:
        for sock in streams:
            sock.close()
    deadline = time.monotonic() + 5  # The relay frees a slot once it sees the close.
    while rig.relay._open.get(os.getuid(), 0) >= 3 and time.monotonic() < deadline:
        time.sleep(.01)
    again = _open(rig.path)  # A closed stream frees its owner's slot.
    try:
        assert _connected(again)
    finally:
        again.close()
