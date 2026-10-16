"""Bound image and video decoding in the admitted owner's box."""

from __future__ import annotations

import json
import math
from pathlib import Path

from tinyassets.box_process import run_bound

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_FRAME_BYTES = 3 * 1024 * 1024
MAX_FRAMES = 10
MAX_RESULT_BYTES = MAX_FRAME_BYTES * MAX_FRAMES + 16384


def _binding(universe_dir):
    if universe_dir is None:
        raise PermissionError("media decoding requires an admitted command center")
    from tinyassets.control_plane.binding import file_binding

    binding = file_binding(Path(universe_dir))
    if binding is None:
        raise PermissionError("media decoding requires an admitted owner box")
    return binding


def decode_image(data: bytes, mime: str, universe_dir: Path):
    from tinyassets.tool_images import (
        DECODE_CPU_SECONDS,
        DECODE_MEMORY_BYTES,
        DECODE_WALL_SECONDS,
        MAX_IMAGE_BYTES,
        MAX_IMAGE_SOURCE_BYTES,
    )

    binding = _binding(universe_dir)
    if not isinstance(data, bytes) or len(data) > MAX_IMAGE_SOURCE_BYTES:
        raise PermissionError("decoder scope is not admitted")
    return run_bound(
        binding, "image-decode", ["python", "-m", "tinyassets.tool_images",
                                  mime, str(DECODE_MEMORY_BYTES), str(DECODE_CPU_SECONDS)],
        stdin=data, wall_seconds=DECODE_WALL_SECONDS,
        output_bytes=MAX_IMAGE_BYTES + 4096,
    )


def frames(data: bytes, universe_dir: Path):
    binding = _binding(universe_dir)
    if type(data) is not bytes or not 0 < len(data) <= MAX_SOURCE_BYTES:
        raise ValueError("video input exceeds its bound or is empty")
    done = run_bound(binding, "video-frames",
                     ["python", "-m", "tinyassets.boxes.video_codec"], stdin=data,
                     wall_seconds=160, output_bytes=MAX_RESULT_BYTES)
    if done.returncode:
        raise RuntimeError("video box execution did not complete")
    return parse_result(done.stdout)


def parse_result(payload: bytes):
    header, separator, content = payload.partition(b"\n")
    if not separator or len(header) > 4096 or len(payload) > MAX_RESULT_BYTES:
        raise ValueError("invalid video result")
    meta = json.loads(header)
    if (type(meta) is not dict or set(meta) != {"duration", "sizes"}
            or type(meta["duration"]) not in (int, float)
            or not math.isfinite(meta["duration"]) or meta["duration"] < 0
            or type(meta["sizes"]) is not list or not 0 < len(meta["sizes"]) <= MAX_FRAMES
            or any(type(size) is not int or not 0 < size <= MAX_FRAME_BYTES
                   for size in meta["sizes"]) or sum(meta["sizes"]) != len(content)):
        raise ValueError("invalid video result")
    output = []
    for size in meta["sizes"]:
        frame, content = content[:size], content[size:]
        if not frame.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("invalid video frame encoding")
        output.append(frame)
    return meta["duration"], output
