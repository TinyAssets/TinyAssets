"""Provider discovery and execution inside a dedicated owner cell (D82).

Only the sealed launch snapshot (pinned by descriptor), the shipped CLI
install trees, the owner's filtered egress socket and, for a served turn, the
owner's engine-route relay enter the cell. No center root, shared store,
vault, platform credential or sidecar directory. Without the bounded owner
launcher the launch is refused; there is no daemon subprocess fallback.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import stat
from pathlib import Path
from types import SimpleNamespace

from tinyassets.external_agents.writeback import AuthWriteback
from tinyassets.owner_launcher_client import OwnerLaunchRefused
from tinyassets.providers.owned_process import OwnerCellProcess
from tinyassets.providers.provider_jail import ProviderConfinementError
from tinyassets.role_provider_cell import safe_environment

MAX_PROOF_BYTES = 65536
CELL_SNAPSHOT = '/snapshot'


def _owner_home(root: Path, machine: int, *, center: Path, principal: str,
                identity, client) -> Path:
    """Publish an owner-created inode using the existing owner-content cell."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name, mode, owners in (('.platform', 0o711, {os.geteuid()}),
                                    ('owners', 0o711, {os.geteuid()}),
                                    (str(machine), 0o711, {os.geteuid()})):
            try:
                os.mkdir(name, mode, dir_fd=fd)
            except FileExistsError:
                pass
            else:
                os.chmod(name, mode, dir_fd=fd, follow_symlinks=False)
            next_fd = os.open(name, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
            info = os.fstat(next_fd)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners
                    or stat.S_IMODE(info.st_mode) != mode):
                os.close(next_fd)
                raise PermissionError('external-agent home path is not owner-private')
            os.close(fd)
            fd = next_fd
        home = root / '.platform' / 'owners' / str(machine) / 'agent-home'
        try:
            home_fd = os.open('agent-home', os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
        except FileNotFoundError:
            from tinyassets import role_center_admission, role_content

            with role_content._staging(root, machine) as staging:
                role_content._materialize(client, staging, principal=principal,
                                          center=center, identity=identity,
                                          data=None, directories=1)
                try:
                    role_center_admission._renameat2(staging, 'dir-0', fd,
                                                     'agent-home')
                except FileExistsError:
                    pass
            home_fd = os.open('agent-home', os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
        try:
            info = os.fstat(home_fd)
            if ((info.st_uid, info.st_gid) != (machine, machine)
                    or stat.S_IMODE(info.st_mode) not in (0o700, 0o770)):
                raise PermissionError('external-agent home inode differs from its owner')
        finally:
            os.close(home_fd)
        return home
    finally:
        os.close(fd)


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


async def aspawn_cell(argv, *, env, view, universe_dir, snapshot_dir=None, limit,
                      execution=False, engine_route=None, external_agent=False,
                      auth_files=None, auth=None, workdir=None, nonce=None,
                      owner_user_id=None, catalogue_rewrite=None, requested_model=None):
    from tinyassets import role_decoder, role_relays, universe_egress
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.role_scope import owner_principal
    from tinyassets.storage import data_dir
    from tinyassets.workspace_fs import open_dir_nofollow

    client = role_decoder._bounded_client
    if client is None or universe_dir is None:
        raise PermissionError('provider discovery requires its bounded owner launcher')
    root = data_dir().resolve()
    center = Path(universe_dir)
    principal = owner_principal(center, actor=owner_user_id if external_agent else None)
    identity = owner_identity(root, principal=principal)
    if external_agent:
        snapshot = _owner_home(root, identity.uid, center=center,
                               principal=principal, identity=identity,
                               client=client)
    else:
        snapshot = Path(snapshot_dir)
        expected = center / '.runtime' / 'provider-launch-credentials' / snapshot.name
        if snapshot != expected:
            raise PermissionError('provider discovery requires its exact launch snapshot')
    if engine_route is not None and (not execution or engine_route[0] != principal
                                     or engine_route[1] != center.name):
        raise PermissionError('provider engine route is outside the admitted owner scope')
    descriptor = (os.open(snapshot, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
                  if external_agent else open_dir_nofollow(snapshot))
    relay_fds = []
    sockets = {}
    engine_port = None
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISDIR(info.st_mode)
                or info.st_uid != (identity.uid if external_agent else os.geteuid())
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
        if external_agent:
            if auth is None or auth_files is None or workdir is None or nonce is None:
                raise ValueError('external-agent launch is missing its job contract')
            config = json.dumps({
                'argv': argv, 'env': env, 'home_dir': auth['home_dir'],
                'workdir': workdir,
                'auth_files': {name: base64.b64encode(data).decode('ascii')
                               for name, data in auth_files.items()},
                'writeback': True, 'nonce': nonce, 'engine_port': engine_port,
                'catalogue_rewrite': catalogue_rewrite,
                'requested_model': requested_model,
            }, separators=(',', ':')).encode() + b'\n'
            if len(config) >= 64 * 1024:
                raise ValueError('external-agent config exceeds its bound')
        else:
            config = cell_config(argv, env, view.setenv, snapshot, root,
                                 engine_port=engine_port)
        # START only waits for the mapper's admission acknowledgement, never
        # payload execution. Keep descriptor ownership synchronous so caller
        # cancellation cannot close a descriptor while a launch thread uses it.
        cell = client.start_cell(kind=('external-agent' if external_agent else
                                      'provider-exec' if execution else 'provider-discovery'),
            principal=principal, command_center=center.name, identity=identity,
            extra={'egress': True, 'engine': 'g' in sockets} if execution or external_agent
            else {'egress': True},
            directory_fd=descriptor, socket_fds=tuple(relay_fds))
    except OwnerLaunchRefused as exc:
        # The client already verified this bounded reason's authenticated sender.
        raise ProviderConfinementError(str(exc)) from exc
    finally:
        os.close(descriptor)
        for relay_fd in relay_fds:
            os.close(relay_fd)
    writer = None
    data_socket = None
    try:
        if execution or external_agent:
            from tinyassets.role_provider_execution import ExecutionProcess, pipe_streams

            # Three ordinary pipes, read and written exactly as asyncio reads
            # and writes a local subprocess. The transports own duplicates, so
            # reaping (which closes the cell's own ends) never discards bytes
            # still queued for a reader.
            reader, writer, error_reader = await pipe_streams(cell, limit)
            proc = ExecutionProcess(cell, reader, writer, error_reader)
        else:
            cell.stream.setblocking(False)
            data_socket = cell.stream
            reader, writer = await asyncio.open_connection(sock=data_socket, limit=limit)
            proc = OwnerCellProcess(cell, reader, writer)
    except BaseException:
        if writer is not None:
            writer.close()
        if data_socket is not None:
            data_socket.close()
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
        if execution or external_agent:
            proc.close_stdio()
        if not isinstance(exc, Exception):
            raise
        raise ProviderError('provider cell startup failed: ' + cell.failure(exc)
                            + disk_stop_note(proc)) from exc
    return proc


class CellLauncher:
    """Production external-agent launcher through the one owner cell admission."""

    def __init__(self, *, universe_dir: Path, owner_user_id: str):
        self.universe_dir = Path(universe_dir)
        self.owner_user_id = owner_user_id
        self._nonces = {}

    async def spawn(self, argv, env, *, home, workdir, egress, auth_files, auth,
                    catalogue_rewrite=None, requested_model=None):
        from tinyassets.role_scope import owner_principal

        if (home != '/agent-home' or egress != self.universe_dir.name
                or owner_principal(self.universe_dir, actor=self.owner_user_id)
                != self.owner_user_id):
            raise PermissionError('external-agent launch differs from owner scope')
        nonce = secrets.token_hex(16)
        proc = await aspawn_cell(
            argv, env=env, view=SimpleNamespace(setenv=()),
            universe_dir=self.universe_dir, limit=4 * 1024 * 1024,
            external_agent=True, auth_files=auth_files, auth=auth,
            workdir=workdir, nonce=nonce, owner_user_id=self.owner_user_id,
            catalogue_rewrite=catalogue_rewrite, requested_model=requested_model,
        )
        self._nonces[id(proc)] = nonce
        return proc

    async def close(self, proc):
        nonce = self._nonces.pop(id(proc))
        try:
            proc.stdin.close()

            async def bounded(reader):
                result = bytearray()
                while part := await reader.read(65536):
                    result.extend(part)
                    if len(result) > 12 * 1024 * 1024:
                        raise ValueError('external-agent output exceeds its bound')
                return bytes(result)

            stdout = asyncio.create_task(bounded(proc.stdout))
            stderr = asyncio.create_task(bounded(proc.stderr))
            try:
                await asyncio.wait_for(proc.wait(), timeout=15)
                output_bytes = await asyncio.wait_for(stdout, timeout=5)
                await asyncio.wait_for(stderr, timeout=5)
            except BaseException:
                proc.revoke()
                await proc.wait()
                raise
            lines = output_bytes.splitlines()
            if not lines:
                raise RuntimeError('external-agent writeback trailer is absent')
            trailer = json.loads(lines[-1])
            if trailer.get('nonce') != nonce or trailer.get('exit') != proc.returncode:
                raise RuntimeError('external-agent writeback trailer differs from launch')
            return AuthWriteback({
                name: base64.b64decode(encoded, validate=True)
                for name, encoded in trailer['external_agent_writeback'].items()
            }, proc.returncode)
        finally:
            proc.close_stdio()
