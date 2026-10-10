"""Data-only video parser admission; no provider credential enters the cell."""
from __future__ import annotations

import json
import math
from pathlib import Path

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_FRAME_BYTES = 3 * 1024 * 1024
MAX_FRAMES = 10
MAX_RESULT_BYTES = MAX_FRAME_BYTES * MAX_FRAMES + 16384


def frames(data, universe_dir):
    from tinyassets import role_decoder
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.role_scope import owner_principal
    from tinyassets.storage import data_dir

    client = role_decoder._bounded_client
    if client is None or universe_dir is None:
        raise RuntimeError('video requires its bounded owner launcher and command center')
    root = data_dir().resolve()
    center = Path(universe_dir)
    principal = owner_principal(center)
    if type(data) is not bytes or not 0 < len(data) <= MAX_SOURCE_BYTES:
        raise ValueError('video input exceeds its bound or is empty')
    identity = owner_identity(root, principal=principal)
    done = client._data_cell(data, principal=principal, command_center=center.name,
        identity=identity, kind='ingestion-video', extra={}, profile='cell-deny',
        input_bound=MAX_SOURCE_BYTES, output_bound=MAX_RESULT_BYTES, timeout=160)
    if done.returncode != 0:
        raise RuntimeError('video cell did not complete')
    return parse_result(done.stdout)


def parse_result(payload):
    header, separator, content = payload.partition(b'\n')
    if not separator or len(header) > 4096 or len(payload) > MAX_RESULT_BYTES:
        raise ValueError('invalid video result')
    meta = json.loads(header)
    if (type(meta) is not dict or set(meta) != {'duration', 'sizes'}
            or type(meta['duration']) not in (int, float)
            or not math.isfinite(meta['duration']) or meta['duration'] < 0
            or type(meta['sizes']) is not list or not 0 < len(meta['sizes']) <= MAX_FRAMES
            or any(type(size) is not int or not 0 < size <= MAX_FRAME_BYTES
                   for size in meta['sizes']) or sum(meta['sizes']) != len(content)):
        raise ValueError('invalid video result')
    output = []
    for size in meta['sizes']:
        frame, content = content[:size], content[size:]
        if not frame.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('invalid video frame encoding')
        output.append(frame)
    return meta['duration'], output
