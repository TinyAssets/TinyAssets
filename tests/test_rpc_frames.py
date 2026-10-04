"""The shared local-RPC framing (broker and boxhostd): round trips and refusals."""

from __future__ import annotations

import asyncio
import random
import socket
import struct

import pytest

from tinyassets import rpc_frames as rf


def _random_frames(rng: random.Random, count: int) -> list[tuple[int, int, bytes]]:
    frames = []
    for _ in range(count):
        stream = rng.randint(1, rf.MAX_STREAM_ID)
        if rng.random() < 0.5:
            doc = {"op": rng.choice(["OPEN", "CREDIT", "END"]), "n": rng.randint(0, 10**9),
                   "s": "".join(chr(rng.randint(32, 0x2FFF)) for _ in range(rng.randint(0, 40)))}
            frames.append((rf.CONTROL, stream, rf.control(stream, doc)))
        else:
            payload = rng.randbytes(rng.randint(1, rf.MAX_DATA_FRAME))
            frames.append((rf.DATA, stream, rf.data(stream, payload)[0]))
    return frames


@pytest.mark.parametrize("seed", range(20))
def test_any_split_of_a_frame_sequence_decodes_to_the_same_frames(seed):
    rng = random.Random(seed)
    frames = _random_frames(rng, 12)
    wire = b"".join(encoded for _, _, encoded in frames)
    decoder = rf.Decoder()
    got: list[rf.Frame] = []
    position = 0
    while position < len(wire):
        step = rng.randint(1, 9000)
        got += decoder.feed(wire[position:position + step])
        position += step
    assert decoder.idle
    assert [(f.kind, f.stream) for f in got] == [(k, s) for k, s, _ in frames]
    for frame, (kind, _, encoded) in zip(got, frames):
        assert encoded.endswith(frame.payload)


def test_large_payloads_split_into_bounded_data_frames():
    payload = bytes(range(256)) * 1000
    frames = rf.data(7, payload)
    assert all(len(f) - rf.HEADER_BYTES <= rf.MAX_DATA_FRAME for f in frames)
    decoded = rf.Decoder().feed(b"".join(frames))
    assert b"".join(f.payload for f in decoded) == payload


def test_control_documents_round_trip():
    frame = rf.Decoder().feed(rf.control(3, {"op": "OPEN", "body": "héllo", "n": 1}))[0]
    assert frame.control() == {"op": "OPEN", "body": "héllo", "n": 1}


@pytest.mark.parametrize(("kind", "stream", "length"), [
    (0x09, 1, 0),                         # unknown kind
    (rf.DATA, 1, rf.MAX_DATA_FRAME + 1),  # oversized data
    (rf.CONTROL, 1, rf.MAX_CONTROL_FRAME + 1),
    (rf.DATA, rf.CONNECTION, 1),          # data on the connection stream
])
def test_a_bad_header_is_refused_before_its_payload_is_buffered(kind, stream, length):
    decoder = rf.Decoder()
    with pytest.raises(rf.FrameError):
        decoder.feed(struct.pack(">BII", kind, stream, length))


@pytest.mark.parametrize("payload", [b"not json", b"[1,2]", b'{"no_op": 1}', b'{"op": ""}',
                                     b"\xff\xfe"])
def test_a_control_frame_must_be_an_object_with_an_op(payload):
    raw = struct.pack(">BII", rf.CONTROL, 1, len(payload)) + payload
    with pytest.raises(rf.FrameError):
        rf.Decoder().feed(raw)


@pytest.mark.parametrize("encode", [
    lambda: rf.control(1, {"no": "op"}),
    lambda: rf.control(1, {"op": "X", "v": float("nan")}),
    lambda: rf.control(1, {"op": "X", "v": object()}),
    lambda: rf.data(rf.CONNECTION, b"x"),
])
def test_encoding_refuses_what_decoding_would_refuse(encode):
    with pytest.raises(rf.FrameError):
        encode()


def test_async_reader_reports_clean_end_and_truncation():
    async def scenario(wire: bytes):
        reader = asyncio.StreamReader()
        reader.feed_data(wire)
        reader.feed_eof()
        frames = []
        while (frame := await rf.read_frame(reader)) is not None:
            frames.append(frame)
        return frames

    whole = rf.control(1, {"op": "END"}) + rf.data(1, b"abc")[0]
    assert [f.kind for f in asyncio.run(scenario(whole))] == [rf.CONTROL, rf.DATA]
    with pytest.raises(rf.FrameError):
        asyncio.run(scenario(whole[:-1]))
    with pytest.raises(rf.FrameError):
        asyncio.run(scenario(whole[:3]))


def test_blocking_reader_over_a_socket_pair():
    left, right = socket.socketpair()
    try:
        left.sendall(rf.control(5, {"op": "HEAD", "status": 200}) + rf.data(5, b"x" * 70000)[0])
        left.shutdown(socket.SHUT_WR)
        first = rf.read_frame_blocking(right)
        second = rf.read_frame_blocking(right)
        assert first.control()["status"] == 200
        assert len(second.payload) == rf.MAX_DATA_FRAME
        assert rf.read_frame_blocking(right) is None
    finally:
        left.close()
        right.close()


def test_deeply_nested_control_json_is_a_frame_error_not_a_crash():
    payload = b'{"op":"OPEN","x":' + b"[" * 100_000 + b"]" * 100_000 + b"}"
    raw = struct.pack(">BII", rf.CONTROL, 1, len(payload)) + payload
    with pytest.raises(rf.FrameError):
        rf.Decoder().feed(raw)


def test_a_timeout_inside_a_frame_is_a_frame_error_not_a_resumable_read():
    left, right = socket.socketpair()
    try:
        right.settimeout(0.3)
        whole = rf.control(1, {"op": "END"})
        left.sendall(whole[:5])  # half a header, then silence
        with pytest.raises(rf.FrameError):
            rf.read_frame_blocking(right)
        left.sendall(whole[:rf.HEADER_BYTES + 2])  # a header and part of a payload
        with pytest.raises(rf.FrameError):
            rf.read_frame_blocking(right)
    finally:
        left.close()
        right.close()


def test_a_timeout_between_frames_is_the_plain_timeout():
    left, right = socket.socketpair()
    try:
        right.settimeout(0.2)
        with pytest.raises(TimeoutError):
            rf.read_frame_blocking(right)
    finally:
        left.close()
        right.close()
