"""Metadata-only provider discovery inside a dedicated owner cell (D82).

Only the sealed launch snapshot (pinned by descriptor) and the shipped CLI
install trees enter the cell. No center root, shared store, vault, platform
credential or sidecar. A selected broker without its bounded client refuses;
there is no daemon subprocess fallback.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

from tinyassets.providers.owned_process import OwnerCellProcess
from tinyassets.role_provider_cell import safe_environment

MAX_PROOF_BYTES = 65536
CELL_SNAPSHOT = '/snapshot'


def cell_config(argv, env, view_env, snapshot, data_root):
    """Immutable argv/env with the snapshot rewritten to its fixed cell path."""
    host = str(snapshot)

    def rewrite(value):
        return CELL_SNAPSHOT + value[len(host):] if (
            value == host or value.startswith(host + '/')) else value

    merged = {**env, **dict(view_env)}
    out_env = {}
    for key, value in merged.items():
        value = rewrite(value)
        # Unreachable in-cell host paths are dropped, never translated.
        if str(data_root) not in value:
            out_env[key] = value
    document = json.dumps({'argv': [rewrite(item) for item in argv],
                          'env': safe_environment(out_env)},
                          separators=(',', ':')).encode()
    if len(document) >= 64 * 1024:
        raise ValueError('provider config exceeds its bound')
    return document + b'\n'


def check_proof(cell, identity, source):
    inner = identity.uid - 300000
    if (type(cell) is not dict or cell.get('uid') != inner or cell.get('gid') != inner
            or cell.get('fds') != [0, 1, 2] or cell.get('groups') != []
            or cell.get('caps') != 'zero' or cell.get('nnp') != 1
            or cell.get('profile') != 'cell-deny' or cell.get('nested_userns') is not False
            or cell.get('source') != source or cell.get('sockets') != {}):
        raise RuntimeError('provider discovery cell proof is absent')


async def aspawn_cell(argv, *, env, view, universe_dir, snapshot_dir, limit):
    from tinyassets import role_decoder
    from tinyassets.auth.middleware import current_identity
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.daemon_server import get_founder_home, universe_access_permission
    from tinyassets.storage import data_dir
    from tinyassets.workspace_fs import open_dir_nofollow

    client = role_decoder._bounded_client
    if client is None or universe_dir is None:
        raise PermissionError('provider discovery requires its bounded owner launcher')
    root = data_dir().resolve()
    center = Path(universe_dir)
    principal = current_identity().user_id
    if (center.parent != root or center.resolve() != center
            or not (get_founder_home(root, principal) == center.name or universe_access_permission(
                root, universe_id=center.name, actor_id=principal) == 'admin')):
        raise PermissionError('provider discovery owner scope is not admitted')
    snapshot = Path(snapshot_dir)
    expected = center / '.runtime' / 'provider-launch-credentials' / snapshot.name
    if snapshot != expected:
        raise PermissionError('provider discovery requires its exact launch snapshot')
    identity = owner_identity(root, principal=principal)
    descriptor = open_dir_nofollow(snapshot)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or os.readlink(f'/proc/self/fd/{descriptor}') != str(snapshot)):
            raise PermissionError('provider snapshot is not the sealed daemon inode')
        source = [info.st_dev, info.st_ino]
        config = cell_config(argv, env, view.setenv, snapshot, root)
        # START only waits for the mapper's admission acknowledgement, never
        # payload execution. Keep descriptor ownership synchronous so caller
        # cancellation cannot close a descriptor while a launch thread uses it.
        cell = client.start_cell(kind='provider-discovery', principal=principal,
            command_center=center.name, identity=identity, extra={'egress': False},
            directory_fd=descriptor)
    finally:
        os.close(descriptor)
    try:
        cell.stream.setblocking(False)
        reader, writer = await asyncio.open_connection(sock=cell.stream, limit=limit)
        proc = OwnerCellProcess(cell, reader, writer)
    except BaseException:
        cell.close()
        raise
    try:
        header = await reader.readline()
        if not header.endswith(b'\n') or len(header) > MAX_PROOF_BYTES:
            raise RuntimeError('provider discovery cell ended before its proof')
        check_proof(json.loads(header).get('cell'), identity, source)
        writer.write(config)
        await writer.drain()
    except BaseException:
        writer.close()
        proc.revoke()
        await proc.wait()
        raise
    return proc
