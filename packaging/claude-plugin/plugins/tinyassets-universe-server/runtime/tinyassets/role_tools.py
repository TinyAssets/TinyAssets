"""Bounded tool execution with daemon-owned queueing and storage accounting.

The owner cell keeps the existing nested tool jail and its resource supervisor.
A fixed budget poll carries no action or credential authority. Exact pinned
relay sockets retain their existing per-center and per-invocation authority.
"""
from __future__ import annotations

import base64
import json
import math
import os
import stat
import sys
import threading
from dataclasses import asdict, replace
from pathlib import Path

from tinyassets import universe_tools as tools

INPUT_BOUND = 24 * 1024 * 1024
OUTPUT_BOUND = 4 * ((tools.MAX_IMAGE_SOURCE_BYTES + 2) // 3) + 65536
_center_locks = {}
_center_locks_guard = threading.Lock()


def _center_lock(center):
    # One authenticated daemon owns this launcher. Keep each lock for its
    # lifetime: eviction could allow overlapping maintenance for the same root.
    with _center_locks_guard:
        return _center_locks.setdefault(center, threading.Lock())


def _files(client, *, principal, center, identity, fd, agent_id):
    from tinyassets import storage_accounting

    try:
        return _files_exchange(client, principal=principal, center=center,
                               identity=identity, fd=fd, agent_id=agent_id)
    finally:
        # Preparation can publish bytes before open_budget succeeds. Keep
        # those stores dirty even on a failed admission or lost receipt.
        for store in ('universe_files', 'workspaces'):
            storage_accounting.touch(center.parent, center.name, store)


def _files_exchange(client, *, principal, center, identity, fd, agent_id):
    info = os.fstat(fd)
    with client.start_cell(kind='tool-files', principal=principal,
            command_center=center.name, identity=identity, directory_fd=fd) as cell:
        cell.stream.settimeout(40)
        with cell.stream.makefile('rb') as reader:
            proof = _read(reader, 16384)['cell']
            inner = identity.uid - 300000
            if (proof.get('uid') != inner or proof.get('gid') != inner
                    or proof.get('source') != [info.st_dev, info.st_ino]
                    or proof.get('fds') != [0, 1, 2] or proof.get('groups') != []
                    or proof.get('caps') != 'zero' or proof.get('nnp') != 1
                    or proof.get('profile') != 'cell-deny'):
                raise RuntimeError('tool maintenance proof is absent')
            cell.stream.sendall(_frame({'agent_id': agent_id}, 4096))
            answer = _read(reader, 16384)
            if set(answer) != {'files'} or cell.wait(5) != 0:
                raise RuntimeError('tool maintenance did not complete')
            return answer['files']


def prepare(universe_dir, *, agent_id='main'):
    """Only the owner cell creates selected-path tool workspace directories."""
    from tinyassets import role_decoder
    from tinyassets.auth.middleware import current_identity
    from tinyassets.broker import supervisor
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.daemon_server import get_founder_home, universe_access_permission
    from tinyassets.storage import data_dir
    from tinyassets.workspace_fs import open_dir_nofollow

    client = role_decoder._bounded_client
    if client is None:
        raise tools.UniverseToolError('tool preparation requires its bounded owner launcher')
    supervisor._protect_daemon()
    root, center = data_dir().resolve(), Path(universe_dir)
    principal = current_identity().user_id
    if (center.parent != root or center.resolve() != center
            or not (get_founder_home(root, principal) == center.name or universe_access_permission(
                root, universe_id=center.name, actor_id=principal) == 'admin')):
        raise PermissionError('tool preparation scope is not admitted')
    identity = owner_identity(root, principal=principal)
    fd = open_dir_nofollow(center)
    try:
        info = os.fstat(fd)
        if info.st_gid != identity.gid or info.st_uid not in (1001, identity.uid):
            raise PermissionError('tool center does not match owner identity')
        with _center_lock(center):
            return _files(client, principal=principal, center=center, identity=identity,
                          fd=fd, agent_id=agent_id)
    finally:
        os.close(fd)


def _frame(value, bound):
    raw = json.dumps(value, separators=(',', ':')).encode() + b'\n'
    if len(raw) > bound:
        raise ValueError('tool cell message exceeds its bound')
    return raw


def _read(stream, bound):
    raw = stream.readline(bound + 1)
    if len(raw) > bound or not raw.endswith(b'\n'):
        raise RuntimeError('invalid tool cell frame')
    return json.loads(raw)


def _validate(request):
    if set(request) != {'inner', 'agent_id', 'stdin', 'limits', 'wall', 'cap'}:
        raise ValueError('invalid tool request')
    if (type(request['inner']) is not list or not request['inner']
            or any(type(arg) is not str or '\0' in arg for arg in request['inner'])
            or type(request['agent_id']) is not str or not request['agent_id'].strip()
            or type(request['wall']) not in (float, int)
            or not math.isfinite(request['wall']) or not 0 < request['wall'] <= 600
            or type(request['cap']) is not int
            or not 0 < request['cap'] <= tools.MAX_IMAGE_SOURCE_BYTES):
        raise ValueError('invalid tool cell limits or command')
    limits = tools.ToolLimits(**request['limits'])
    for key, value in asdict(limits).items():
        if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
            raise ValueError('invalid tool resource limit')
        default = (tools.MAX_IMAGE_SOURCE_BYTES if key == 'output_bytes'
                   else getattr(tools.DEFAULT_LIMITS, key))
        if key.startswith('min_free_'):
            if value < default:
                raise ValueError('tool disk floor cannot decrease')
        elif value > default:
            raise ValueError('tool resource ceiling cannot increase')
    stdin = request['stdin']
    if stdin is not None:
        stdin = base64.b64decode(stdin, validate=True)
    return limits, stdin


def run(universe_dir, inner, *, agent_id, stdin, limits, wall_seconds, output_bytes,
        on_wait, egress_socket, ta_socket):
    from tinyassets import role_decoder
    from tinyassets.auth.middleware import current_identity
    from tinyassets.broker import supervisor
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.daemon_server import get_founder_home, universe_access_permission
    from tinyassets.storage import data_dir
    from tinyassets.workspace_fs import open_dir_nofollow

    client = role_decoder._bounded_client
    if client is None:
        raise tools.UniverseToolError('tool requires its bounded owner launcher')
    from tinyassets.providers.provider_jail import UNIVERSE_SIDECARS_DIR

    center = Path(universe_dir)
    for path in (egress_socket, ta_socket):
        if path is not None and Path(path).parent != (
                center.parent / UNIVERSE_SIDECARS_DIR / center.name):
            raise tools.UniverseToolError('tool cell socket forwarding requires a scoped relay')
    supervisor._protect_daemon()
    root = data_dir().resolve()
    principal = current_identity().user_id
    if (center.parent != root or center.resolve() != center
            or not (get_founder_home(root, principal) == center.name or universe_access_permission(
                root, universe_id=center.name, actor_id=principal) == 'admin')):
        raise PermissionError('tool owner scope is not admitted')
    request = dict(inner=list(inner), agent_id=agent_id,
        stdin=None if stdin is None else base64.b64encode(stdin).decode(),
        limits=asdict(limits), wall=limits.wall_seconds if wall_seconds is None else wall_seconds,
        cap=limits.output_bytes if output_bytes is None else output_bytes)
    _validate(request)
    payload = _frame(request, INPUT_BOUND)
    identity = owner_identity(root, principal=principal)
    fd = open_dir_nofollow(center)
    socket_fds = []
    socket_sources = {}
    queued = []
    try:
        info = os.fstat(fd)
        if (not stat.S_ISDIR(info.st_mode) or info.st_gid != identity.gid
                or info.st_uid not in (1001, identity.uid)):
            raise PermissionError('tool center does not match owner identity')
        from tinyassets.role_relays import pin_for_owner

        for key, name, path in (('e', 'egress', egress_socket), ('t', 'ta', ta_socket)):
            if path is not None:
                descriptor = pin_for_owner(Path(path), center, identity.uid, kind=name)
                socket_fds.append(descriptor)
                socket_info = os.fstat(descriptor)
                socket_sources[key] = [socket_info.st_dev, socket_info.st_ino]
        with _center_lock(center), tools._slot(center, on_wait=on_wait, waited=queued):
            maintenance = dict(principal=principal, center=center, identity=identity,
                               fd=fd, agent_id=agent_id)
            maintenance_warnings = set()

            def maintain_files():
                result = _files(client, **maintenance)
                if result['truncated']:
                    maintenance_warnings.add('Tool file recovery reached its bounded walk limit.')
                if result['skipped']:
                    maintenance_warnings.add('Brain publication skipped: '
                                             + ', '.join(result['skipped']) + '.')

            maintain_files()
            try:
                budget = tools.jail_disk.open_budget(center,
                    min_free_bytes=limits.min_free_disk_bytes,
                    min_free_inodes=limits.min_free_inodes)
            except tools.jail_disk.DiskFloorRefused as below:
                raise tools.UniverseToolError(
                    f'{below}, so the tool jail will not start; nothing ran') from None
            try:
                with client.start_cell(kind='tool-jail', principal=principal,
                        command_center=center.name, identity=identity, directory_fd=fd,
                        extra={'egress': egress_socket is not None, 'ta': ta_socket is not None},
                        socket_fds=socket_fds) as cell:
                    cell.stream.settimeout(40)
                    with cell.stream.makefile('rb') as reader:
                        proof = _read(reader, 16384)['cell']
                        inner_uid = identity.uid - 300000
                        if (proof.get('uid') != inner_uid or proof.get('gid') != inner_uid
                                or proof.get('source') != [info.st_dev, info.st_ino]
                                or proof.get('sockets') != socket_sources
                                or proof.get('fds') != [0, 1, 2] or proof.get('groups') != []
                                or proof.get('caps') != 'zero' or proof.get('nnp') != 1
                                or proof.get('profile') != 'cell-nested'):
                            raise RuntimeError('tool cell proof is absent')
                        cell.stream.settimeout(request['wall'] + 25)
                        cell.stream.sendall(payload)
                        while True:
                            answer = _read(reader, OUTPUT_BOUND)
                            if (set(answer) == {'budget', 'force'} and answer['budget'] is True
                                    and type(answer['force']) is bool):
                                if answer['force']:
                                    # The trusted supervisor has reaped its payload.
                                    # Restore ACL masks before the final daemon walk.
                                    maintain_files()
                                cell.stream.sendall(_frame(
                                    {'breach': budget.breach(force=answer['force'])}, 1024))
                                continue
                            if set(answer) != {'result'}:
                                raise RuntimeError('invalid tool result')
                            result = answer['result']
                            result['output'] = base64.b64decode(result['output'], validate=True)
                            if len(result['output']) > request['cap']:
                                raise RuntimeError('tool output exceeds its bound')
                            run = tools.ToolRun(**result)
                            if cell.wait(5) != 0:
                                raise RuntimeError('tool cell did not exit successfully')
                            return replace(run, waited=queued[0] if queued else 0.0,
                                           notice=' '.join(filter(None, (
                                               budget.notice, *sorted(maintenance_warnings)))),
                                           disk_bound=budget.bound)
            finally:
                try:
                    maintain_files()
                finally:
                    budget.settle()
    finally:
        for descriptor in socket_fds:
            os.close(descriptor)
        os.close(fd)


def cell_main(*, egress=False, ta=False):
    """Trusted supervisor inside the owner cell; no host paths or stores."""
    request = _read(sys.stdin.buffer, INPUT_BOUND)
    limits, stdin = _validate(request)

    class Budget:
        def breach(self, *, force=False):
            sys.stdout.buffer.write(_frame({'budget': True, 'force': force}, 1024))
            sys.stdout.buffer.flush()
            answer = _read(sys.stdin.buffer, 1024)
            if (set(answer) != {'breach'}
                    or answer['breach'] not in (None, 'disk_limit', 'storage_limit')):
                raise RuntimeError('invalid tool budget answer')
            return answer['breach']

    root = Path('/center')
    # The virtual center cannot publish a new root entry. Existing brain files
    # are writable mounts; new names stay in the persistent agent workspace.
    # Promotion is a separate owner-file operation, not an implicit root write.
    filter_fd = tools._seccomp_fd()
    try:
        cpu = min(int(limits.cpu_seconds), int(request['wall']) + 1)
        close_fds = (
            'import os,sys\n'
            'for name in os.listdir("/proc/self/fd"):\n'
            ' fd=int(name)\n'
            ' if fd>2:\n'
            '  try: os.close(fd)\n'
            '  except OSError: pass\n'
            'os.execv(sys.argv[1],sys.argv[1:])\n'
        )
        inner = ['/usr/local/bin/python', '-I', '-S', '-c', close_fds,
                 *tools._limited(request['inner'], limits, cpu_seconds=cpu)]
        argv = tools.tool_jail_argv(root,
            inner,
            agent_id=request['agent_id'], seccomp_fd=filter_fd, promote_brain_files=False,
            egress_socket=Path('/tool-egress.sock') if egress else None,
            ta_socket=Path('/tool-ta.sock') if ta else None)
        result = tools._supervise(argv, root, filter_fd, stdin=stdin, limits=limits,
            wall=request['wall'], cap=request['cap'], process_cap=int(limits.processes) + 3,
            budget=Budget())
        value = asdict(result)
        value['output'] = base64.b64encode(result.output).decode()
        sys.stdout.buffer.write(_frame({'result': value}, OUTPUT_BOUND))
        sys.stdout.buffer.flush()
        return 0
    finally:
        os.close(filter_fd)
