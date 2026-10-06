"""A single credited binary upload frame, with cancellation/deadline checks."""
from __future__ import annotations

import threading

from tinyassets import rpc_frames as rf


class Upload:
    def __init__(self, check, credit):
        self._check, self._credit = check, credit
        self._wake = threading.Condition()
        self._pending = b""
        self._finished = False
        self._available = True

    def put(self, data):
        with self._wake:
            if self._finished or not self._available or len(data) > rf.MAX_DATA_FRAME:
                raise rf.FrameError("uncredited git upload")
            self._available = False
            self._pending = data
            self._wake.notify_all()

    def finish(self):
        with self._wake:
            if self._finished:
                raise rf.FrameError("duplicate upload end")
            self._finished = True
            self._wake.notify_all()

    def read(self, size=8192):
        with self._wake:
            while not self._pending and not self._finished:
                self._check()
                self._wake.wait(0.1)
            self._check()
            if not self._pending:
                return b""
            piece, self._pending = self._pending[:size], self._pending[size:]
            if not self._pending:
                self._available = True
                self._credit()
            return piece
