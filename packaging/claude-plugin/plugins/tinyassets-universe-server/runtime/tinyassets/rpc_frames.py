"""One wire framing for the platform's local RPCs: the credential broker and boxhostd.

Target architecture D2 fixes the box RPC's framing and I14
(``openspec/changes/broker-streaming-contract``) reuses it for the broker:
length-prefixed JSON control frames and raw byte frames, multiplexed by stream
id over one connection (a Unix socket now, mTLS when remote). Both services
import THIS module, so there is one framing, not two lookalikes.

A frame on the wire::

    kind (1 byte) | stream id (4 bytes, big-endian) | length (4 bytes, big-endian) | payload

* ``CONTROL`` (``0x01``): the payload is one UTF-8 JSON object carrying an
  ``"op"`` string. At most :data:`MAX_CONTROL_FRAME` bytes (16 MiB: the bound
  the broker's request documents already have).
* ``DATA`` (``0x02``): raw bytes, at most :data:`MAX_DATA_FRAME` (64 KiB).

Stream id 0 is the connection itself (barriers, status queries); a caller
picks every other id, unique per connection.

Anything else is a protocol error, raised as :class:`FrameError` before a byte
of payload is interpreted. A peer that sends one is dropped: there is no
resynchronizing a length-prefixed stream after a bad header. The same holds
for a read that times out inside a frame: :func:`read_frame_blocking` raises
``FrameError`` rather than leave a half-read header for the next call.

Conventions both services keep (op names are upper case, case-sensitive):

* **Cancel** is a CONTROL frame ``{"op": "CANCEL"}`` on the target stream. A
  server dispatches frames independently of a stream's handler work, so a
  cancel is seen while that handler is blocked.
* **A stream ends** with one CONTROL frame ``{"op": "END", ...}`` carrying
  ``outcome`` (``completed``, ``cancelled``, ``refused``, ``failed``),
  ``error_class`` (a fixed class name, never a peer's words) and
  ``side_effect_state``: ``none`` only when the operation provably never ran
  (a box's ``BoxOperationRefused`` / ``BoxDeadlineBeforeStart``), else
  ``unknown``.
* **A deadline** rides on the request's CONTROL frame as ``deadline_ms``,
  absolute epoch milliseconds. Past it the server stops waiting and ends the
  stream ``failed`` with the deadline's class (``side_effect_state`` as above).
"""

from __future__ import annotations

import asyncio
import json
import struct
from dataclasses import dataclass
from typing import Any

CONTROL = 0x01
DATA = 0x02
MAX_CONTROL_FRAME = 16 * 1024 * 1024
MAX_DATA_FRAME = 64 * 1024
MAX_STREAM_ID = 2**32 - 1
CONNECTION = 0

_HEADER = struct.Struct(">BII")
HEADER_BYTES = _HEADER.size


class FrameError(ValueError):
    """The peer broke the framing; the connection cannot continue."""


@dataclass(frozen=True, slots=True)
class Frame:
    kind: int
    stream: int
    payload: bytes

    def control(self) -> dict[str, Any]:
        """The control document (every reader has already validated it)."""
        if self.kind != CONTROL:
            raise FrameError("not a control frame")
        return _document(self.payload)


def _document(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        # RecursionError: a few KiB of nested arrays exhaust the parser's stack.
        raise FrameError("control frame is not plain UTF-8 JSON") from None
    if not isinstance(value, dict) or not isinstance(value.get("op"), str) or not value["op"]:
        raise FrameError("control frame must be a JSON object with an op")
    return value


def _check(kind: int, stream: int, length: int) -> None:
    if kind == CONTROL:
        limit = MAX_CONTROL_FRAME
    elif kind == DATA:
        limit = MAX_DATA_FRAME
    else:
        raise FrameError(f"unknown frame kind {kind:#x}")
    if not 0 <= stream <= MAX_STREAM_ID:
        raise FrameError("stream id out of range")
    if kind == DATA and stream == CONNECTION:
        raise FrameError("data frames belong to a stream, not the connection")
    if length > limit:
        raise FrameError(f"frame of {length} bytes exceeds the {limit}-byte limit")


def control(stream: int, document: dict[str, Any]) -> bytes:
    """Encode one control frame."""
    if not isinstance(document, dict) or not isinstance(document.get("op"), str) \
            or not document["op"]:
        raise FrameError("control frame must be a JSON object with an op")
    try:
        payload = json.dumps(document, ensure_ascii=False, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise FrameError("control document is not plain JSON") from None
    _check(CONTROL, stream, len(payload))
    return _HEADER.pack(CONTROL, stream, len(payload)) + payload


def data(stream: int, payload: bytes) -> list[bytes]:
    """Encode bytes as one or more data frames, each within :data:`MAX_DATA_FRAME`."""
    payload = bytes(payload)
    frames = []
    for start in range(0, max(len(payload), 1), MAX_DATA_FRAME):
        chunk = payload[start:start + MAX_DATA_FRAME]
        _check(DATA, stream, len(chunk))
        frames.append(_HEADER.pack(DATA, stream, len(chunk)) + chunk)
    return frames


def decode_header(header: bytes) -> tuple[int, int, int]:
    if len(header) != HEADER_BYTES:
        raise FrameError("short frame header")
    kind, stream, length = _HEADER.unpack(header)
    _check(kind, stream, length)
    return kind, stream, length


class Decoder:
    """Incremental decoder: feed it bytes as they arrive, get whole frames back.

    Holds at most one partial frame. A header is validated the moment it is
    complete, so an oversized or unknown frame is refused before its payload
    is buffered.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._pending: tuple[int, int, int] | None = None

    def feed(self, chunk: bytes) -> list[Frame]:
        self._buffer += chunk
        frames: list[Frame] = []
        while True:
            if self._pending is None:
                if len(self._buffer) < HEADER_BYTES:
                    return frames
                self._pending = decode_header(bytes(self._buffer[:HEADER_BYTES]))
                del self._buffer[:HEADER_BYTES]
            kind, stream, length = self._pending
            if len(self._buffer) < length:
                return frames
            payload = bytes(self._buffer[:length])
            del self._buffer[:length]
            self._pending = None
            if kind == CONTROL:
                _document(payload)
            frames.append(Frame(kind, stream, payload))

    @property
    def idle(self) -> bool:
        """No partial frame is buffered: the peer may close here cleanly."""
        return self._pending is None and not self._buffer


async def read_frame(reader: asyncio.StreamReader) -> Frame | None:
    """The next frame, or ``None`` at a clean end of stream (between frames)."""
    try:
        header = await reader.readexactly(HEADER_BYTES)
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return None
        raise FrameError("connection closed inside a frame header") from None
    kind, stream, length = decode_header(header)
    try:
        payload = await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        raise FrameError("connection closed inside a frame") from None
    if kind == CONTROL:
        _document(payload)
    return Frame(kind, stream, payload)


def read_frame_blocking(sock: Any) -> Frame | None:
    """:func:`read_frame` for a blocking socket (the synchronous client).

    A socket timeout before any byte of a frame propagates as the timeout (the
    caller may simply read again); a timeout INSIDE a frame raises
    :class:`FrameError`, because the half-read frame cannot be resumed.
    """
    header = _recv_exactly(sock, HEADER_BYTES, allow_eof=True)
    if header is None:
        return None
    kind, stream, length = decode_header(header)
    payload = _recv_exactly(sock, length, allow_eof=False, started=True) if length else b""
    if kind == CONTROL:
        _document(payload)
    return Frame(kind, stream, payload)


def _recv_exactly(sock: Any, count: int, *, allow_eof: bool,
                  started: bool = False) -> bytes | None:
    parts = bytearray()
    while len(parts) < count:
        try:
            chunk = sock.recv(count - len(parts))
        except TimeoutError:
            if started or parts:
                raise FrameError("timed out inside a frame") from None
            raise
        if not chunk:
            if allow_eof and not parts:
                return None
            raise FrameError("connection closed inside a frame")
        parts += chunk
    return bytes(parts)
