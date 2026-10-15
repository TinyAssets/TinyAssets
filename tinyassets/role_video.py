"""Data-only video parser admission; no provider credential enters the cell."""
from __future__ import annotations

from pathlib import Path

from tinyassets.boxes.media import (
    MAX_RESULT_BYTES,
    MAX_SOURCE_BYTES,
    parse_result,
)


def frames(data, universe_dir):
    from tinyassets.control_plane.binding import file_binding

    if universe_dir is None:
        raise RuntimeError('video requires an admitted command center')
    binding = file_binding(Path(universe_dir))
    if binding is not None:
        from tinyassets.boxes.media import frames as box_frames

        return box_frames(data, universe_dir)

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
