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


def cell_config(argv, env, view_env, snapshot, data_root, cell_view=None):
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
    config = {'argv': [rewrite(item) for item in argv], 'env': safe_environment(out_env)}
    if cell_view is not None:
        config['view'] = cell_view
    document = json.dumps(config, separators=(',', ':')).encode()
    if len(document) >= 64 * 1024:
        raise ValueError('provider config exceeds its bound')
    return document + b'\n'


def check_proof(cell, identity, source, *, sockets=None, workspace=False):
    """``workspace``: False for discovery, else the pinned workspace identity or None."""
    inner = identity.uid - 300000
    if workspace is not False:
        cell = dict(cell) if type(cell) is dict else cell
        if type(cell) is not dict or cell.pop('workspace', False) != workspace:
            raise RuntimeError('provider execution workspace proof is absent')
    if (type(cell) is not dict or cell.get('uid') != inner or cell.get('gid') != inner
            or cell.get('fds') != [0, 1, 2] or cell.get('groups') != []
            or cell.get('caps') != 'zero' or cell.get('nnp') != 1
            or cell.get('profile') != 'cell-deny' or cell.get('nested_userns') is not False
            or cell.get('source') != source or cell.get('sockets') != (sockets or {})):
        raise RuntimeError('provider discovery cell proof is absent')


def _open_provider_workspace(center, identity):
    """Pin the owner's own provider workspace; only the owner cell creates it."""
    from tinyassets import role_tools
    from tinyassets.providers.provider_jail import PROVIDER_WORKSPACE_DIR
    from tinyassets.workspace_fs import open_dir_nofollow

    path = center / PROVIDER_WORKSPACE_DIR
    if not os.path.lexists(path):
        role_tools.prepare(center, agent_id='workspace-preparation')
    descriptor = open_dir_nofollow(path)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISDIR(info.st_mode)
                or (info.st_uid, info.st_gid) != (identity.uid, identity.gid)
                or os.readlink(f'/proc/self/fd/{descriptor}') != str(path)):
            raise PermissionError("provider workspace is not the owner's own directory")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


async def aspawn_cell(argv, *, env, view, universe_dir, snapshot_dir, limit, execution=False,
                      cell_view=None):
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
    persistent = bool(execution and cell_view and cell_view['persistent'])
    workspace_fd = _open_provider_workspace(center, identity) if persistent else None
    try:
        descriptor = open_dir_nofollow(snapshot)
    except BaseException:
        if workspace_fd is not None:
            os.close(workspace_fd)
        raise
    relay_fd = None
    sockets = {}
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or os.readlink(f'/proc/self/fd/{descriptor}') != str(snapshot)):
            raise PermissionError('provider snapshot is not the sealed daemon inode')
        source = [info.st_dev, info.st_ino]
        config = cell_config(argv, env, view.setenv, snapshot, root,
                             cell_view if execution else None)
        workspace = None
        if workspace_fd is not None:
            info = os.fstat(workspace_fd)
            workspace = [info.st_dev, info.st_ino]
        if execution:
            from tinyassets import role_relays, universe_egress

            relay = universe_egress.ensure_proxy(center)
            if relay is None:
                raise PermissionError('provider execution requires pinned egress')
            relay_fd = role_relays.pin_for_owner(relay, center, identity.uid, kind='egress')
            relay_info = os.fstat(relay_fd)
            sockets['e'] = [relay_info.st_dev, relay_info.st_ino]
        # START only waits for the mapper's admission acknowledgement, never
        # payload execution. Keep descriptor ownership synchronous so caller
        # cancellation cannot close a descriptor while a launch thread uses it.
        extra = {'egress': execution}
        if execution:
            extra['workspace'] = persistent
        cell = client.start_cell(kind='provider-exec' if execution else 'provider-discovery',
            principal=principal, command_center=center.name, identity=identity,
            extra=extra, directory_fd=descriptor,
            socket_fds=() if relay_fd is None else (relay_fd,), workspace_fd=workspace_fd)
    finally:
        os.close(descriptor)
        for opened in (relay_fd, workspace_fd):
            if opened is not None:
                os.close(opened)
    writer = error_writer = None
    data_socket = error_socket = None
    try:
        cell.stream.setblocking(False)
        data_socket = cell.stream.dup() if execution else cell.stream
        reader, writer = await asyncio.open_connection(sock=data_socket, limit=limit)
        if execution:
            from tinyassets.role_provider_execution import ExecutionProcess

            cell.stderr.setblocking(False)
            error_socket = cell.stderr.dup()
            error_reader, error_writer = await asyncio.open_connection(
                sock=error_socket, limit=limit)
            proc = ExecutionProcess(cell, reader, writer, error_reader, error_writer)
        else:
            proc = OwnerCellProcess(cell, reader, writer)
    except BaseException:
        for opened in (writer, error_writer):
            if opened is not None:
                opened.close()
        for opened in (data_socket, error_socket):
            if opened is not None:
                opened.close()
        cell.close()
        raise
    try:
        header = await reader.readline()
        if not header.endswith(b'\n') or len(header) > MAX_PROOF_BYTES:
            raise RuntimeError('provider discovery cell ended before its proof')
        check_proof(json.loads(header).get('cell'), identity, source, sockets=sockets,
                    workspace=workspace if execution else False)
        writer.write(config)
        await writer.drain()
    except BaseException:
        writer.close()
        if error_writer is not None:
            error_writer.close()
        proc.revoke()
        await proc.wait()
        raise
    return proc
