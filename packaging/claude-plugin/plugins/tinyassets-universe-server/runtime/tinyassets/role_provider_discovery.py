"""Provider discovery and execution inside a dedicated owner cell (D82).

Only the sealed launch snapshot (pinned by descriptor), the shipped CLI
install trees, the owner's filtered egress socket and, for a served turn, the
owner's engine-route relay enter the cell. No center root, shared store,
vault, platform credential or sidecar directory. Without the bounded owner
launcher the launch is refused; there is no daemon subprocess fallback.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

from tinyassets.owner_launcher_client import OwnerLaunchRefused
from tinyassets.providers.owned_process import OwnerCellProcess
from tinyassets.providers.provider_jail import ProviderConfinementError
from tinyassets.role_provider_cell import safe_environment

MAX_PROOF_BYTES = 65536
CELL_SNAPSHOT = '/snapshot'


def snapshot_values(snapshot):
    """Stripped contents of the sealed snapshot's top-level regular files.

    An env value equal to one of these is the owner's own credential material,
    which the cell can already read; only such values (and the safe settings)
    cross into the cell, so no ambient daemon secret can ride along.
    """
    from tinyassets.role_provider_cell import file_values

    return file_values(str(snapshot))


def cell_config(argv, env, view_env, snapshot, data_root, *, engine_port=None):
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
    config = {'argv': [rewrite(item) for item in argv],
              'env': safe_environment(out_env, snapshot_values(snapshot))}
    if engine_port is not None:
        config['engine_port'] = engine_port
    document = json.dumps(config, separators=(',', ':')).encode()
    if len(document) >= 64 * 1024:
        raise ValueError('provider config exceeds its bound')
    return document + b'\n'


def check_proof(cell, identity, source, *, sockets=None):
    inner = identity.uid - 300000
    if (type(cell) is not dict or cell.get('uid') != inner or cell.get('gid') != inner
            or cell.get('fds') != [0, 1, 2] or cell.get('groups') != []
            or cell.get('caps') != 'zero' or cell.get('nnp') != 1
            or cell.get('profile') != 'cell-deny' or cell.get('nested_userns') is not False
            or cell.get('source') != source or cell.get('sockets') != (sockets or {})):
        raise RuntimeError('provider discovery cell proof is absent')


async def aspawn_cell(argv, *, env, view, universe_dir, snapshot_dir, limit, execution=False,
                      engine_route=None):
    from tinyassets import role_decoder, role_relays, universe_egress
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
    if engine_route is not None and (not execution or engine_route[0] != principal
                                     or engine_route[1] != center.name):
        raise PermissionError('provider engine route is outside the admitted owner scope')
    descriptor = open_dir_nofollow(snapshot)
    relay_fds = []
    sockets = {}
    engine_port = None
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or os.readlink(f'/proc/self/fd/{descriptor}') != str(snapshot)):
            raise PermissionError('provider snapshot is not the sealed daemon inode')
        source = [info.st_dev, info.st_ino]
        # Both classes reach their vendor only through the owner's filtered
        # egress proxy: a model list can be a network call too.
        relay = universe_egress.ensure_proxy(center)
        if relay is None:
            raise PermissionError('provider cell requires pinned egress')
        relay_fds.append(role_relays.pin_for_owner(relay, center, identity.uid,
                                                   kind='egress'))
        relay_info = os.fstat(relay_fds[-1])
        sockets['e'] = [relay_info.st_dev, relay_info.st_ino]
        if engine_route is not None:
            engine = universe_egress.ensure_engine_relay(
                center, actor_id=engine_route[0], graph_id=engine_route[1])
            if engine is None:
                raise PermissionError('provider engine route is not serving')
            engine_path, engine_port = engine
            relay_fds.append(role_relays.pin_for_owner(engine_path, center, identity.uid,
                                                       kind='engine'))
            relay_info = os.fstat(relay_fds[-1])
            sockets['g'] = [relay_info.st_dev, relay_info.st_ino]
        config = cell_config(argv, env, view.setenv, snapshot, root, engine_port=engine_port)
        # START only waits for the mapper's admission acknowledgement, never
        # payload execution. Keep descriptor ownership synchronous so caller
        # cancellation cannot close a descriptor while a launch thread uses it.
        cell = client.start_cell(kind='provider-exec' if execution else 'provider-discovery',
            principal=principal, command_center=center.name, identity=identity,
            extra={'egress': True, 'engine': 'g' in sockets} if execution
            else {'egress': True},
            directory_fd=descriptor, socket_fds=tuple(relay_fds))
    except OwnerLaunchRefused as exc:
        # The client already verified this bounded reason's authenticated sender.
        raise ProviderConfinementError(str(exc)) from exc
    finally:
        os.close(descriptor)
        for relay_fd in relay_fds:
            os.close(relay_fd)
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
        header = await proc.stdout.readline()
        if not header.endswith(b'\n') or len(header) > MAX_PROOF_BYTES:
            raise RuntimeError('provider discovery cell ended before its proof')
        check_proof(json.loads(header).get('cell'), identity, source, sockets=sockets)
        writer.write(config)
        await writer.drain()
    except BaseException as exc:
        from tinyassets.exceptions import ProviderError
        from tinyassets.providers.owned_process import disk_stop_note

        proc.revoke()
        await proc.wait()
        writer.close()
        if error_writer is not None:
            error_writer.close()
        if not isinstance(exc, Exception):
            raise
        raise ProviderError('provider cell startup failed' + disk_stop_note(proc)) from exc
    return proc
