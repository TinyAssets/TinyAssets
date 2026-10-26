"""Ephemeral UID-authenticated host bootstrap. No proof file or environment key."""

from __future__ import annotations

import asyncio
import logging
import os
import select
import socket
import struct
from pathlib import Path

from tinyassets.boxes.control_channel import receive, send
from tinyassets.boxes.provider import BoxAuthError

RUNTIME = Path("/run/tinyassets-boxhostd")


def peer(sock: socket.socket) -> tuple[int, int, int]:
    return struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))


def connect(path: Path, *, timeout=30) -> tuple[socket.socket, tuple[int, int, int]]:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        sock.settimeout(timeout)
        sock.connect(str(path))
        identity = peer(sock)
        if identity[1:] != (0, 0):
            raise BoxAuthError("box bootstrap peer is not the root host")
        return sock, identity
    except BaseException:
        sock.close()
        raise


def register_broker(proof: str):
    if os.geteuid() != 1002:
        raise BoxAuthError("only the broker may register its ephemeral lease")
    sock, identity = connect(RUNTIME / "broker-registration.sock")
    try:
        send(sock, {"register": proof})
        if receive(sock, identity) != {"registered": True}:
            raise BoxAuthError("broker registration refused")
    except BaseException:
        sock.close()
        raise
    return sock, identity


class BrokerRegistration:
    """Keep the running broker's ephemeral proof registered across host restarts."""

    def __init__(self, proof):
        self.proof = proof
        self.channel = register_broker(proof)

    def ready(self):
        sock, identity = self.channel
        try:
            send(sock, {"ready": True})
            if receive(sock, identity) != {"ready": True}:
                raise BoxAuthError("host did not accept broker readiness")
        except BaseException:
            self.close()
            raise

    def refresh(self):
        if self.channel is not None:
            if not select.select([self.channel[0]], [], [], 0)[0]:
                return
            self.close()
        self.channel = register_broker(self.proof)
        self.ready()

    async def maintain(self, stop, *, poll_seconds=1):
        from tinyassets.broker.admission import thread_work

        try:
            while not stop.is_set():
                try:
                    await thread_work(self.refresh)
                except Exception:
                    logging.getLogger(__name__).exception("box host registration retry pending")
                try:
                    await asyncio.wait_for(stop.wait(), poll_seconds)
                except TimeoutError:
                    pass
        finally:
            self.close()

    def close(self):
        if self.channel is not None:
            self.channel[0].close()
            self.channel = None
