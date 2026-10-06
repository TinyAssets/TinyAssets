"""D62 bounded launcher, staged until the complete class matrix is admitted.

Loaded by the verified startup chain before namespace entry. The startup parent
installs the fixed maps and becomes the daemon; no privileged mapper survives.
Bindings are broker-resolved startup admissions, never numeric request fields.
"""
from __future__ import annotations

import array
import ctypes
import json
import os
import select
import signal
import socket
import stat
import struct
import time
from pathlib import Path

FIRST, COUNT = 300000, 100000
MAPPING = f"0 {FIRST} {COUNT}\n"
CAPS = (1 << 6) | (1 << 7)
MAX_CELLS, MAX_OWNER_CELLS = 32, 4


def bootstrap_services(data_root, run_root, bindings, launch):
    """Staged PID1 bootstrap; never called by the production entrypoint yet.

    Migration/owner bindings must already be verified with all writers stopped.
    Fork both services in the existing privileged startup window, then turn
    PID1 into the capability-free daemon. No host-privileged process survives.
    A service death exits PID1: the container tears down every descendant.
    """
    if os.getpid() != 1:
        raise RuntimeError('service bootstrap requires container PID1')
    try:
        return _bootstrap_services(data_root, run_root, bindings, launch)
    except BaseException:
        # Especially before daemon retirement, returning an exception to a
        # caller would let a caught startup failure retain host authority.
        try:
            os.write(2, b'bounded bootstrap failed; container restart required\n')
        finally:
            os._exit(78)


def _bootstrap_services(data_root, run_root, bindings, launch):
    if os.getresuid() != (0, 0, 0) or os.getresgid() != (0, 0, 0):
        raise RuntimeError('service bootstrap requires root entry identity')
    launch['_assert_caps'](launch['ENTRY_CAPS'])
    launch['verify_chain']()
    data_root, run_root = Path(data_root), Path(run_root)
    for path in (data_root, run_root):
        if not path.is_absolute():
            raise RuntimeError('bootstrap paths must be absolute')
        for ancestor in (path, *path.parents):
            if not stat.S_ISDIR(ancestor.lstat().st_mode):
                raise RuntimeError('bootstrap path contains an alias')
    for ancestor in (run_root, *run_root.parents):
        info = ancestor.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError('bootstrap IPC parent is writable or not root-owned')
    broker_dir = run_root / 'broker'
    info = broker_dir.lstat()
    if (not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid,
            stat.S_IMODE(info.st_mode)) != (1002, 1101, 0o2750)):
        raise RuntimeError('invalid bootstrap broker socket directory')
    # These are startup-resolved principal bindings, not client request data.
    for (_, center), machine in bindings.items():
        if (not isinstance(center, str) or not center or center in {'.', '..'}
                or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_'
                       for c in center)
                or type(machine) is not int or not FIRST < machine < FIRST + COUNT):
            raise RuntimeError('invalid bootstrap owner binding')
        info = (data_root / center).lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_gid != machine
                or info.st_uid not in (1001, machine)):
            raise RuntimeError('owner root does not match bootstrap binding')

    proof_parent, proof_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    broker_pid = os.fork()
    if broker_pid == 0:
        try:
            proof_parent.close()
            launch['retire_child']('broker')
            launch['close_descriptors']((proof_child.fileno(),))
            proof_child.settimeout(30)
            digest = proof_child.recv(65).decode('ascii')
            proof_child.close()
            if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise RuntimeError('invalid startup proof hash')
            argv = ['/opt/venv/bin/python', '-I', '-B', '/app/broker_main.py',
                    '--socket', str(broker_dir / 'broker.sock'),
                    '--state', str(data_root / '.broker' / 'state'),
                    '--data-root', str(data_root), '--owner-uid', '1001',
                    '--proof-sha256', digest, '--role-split']
            os.chdir('/')
            os.execve(argv[0], argv, launch['broker_environment'](data_root))
        except BaseException:
            os.write(2, b'bounded bootstrap broker failed\n')
            os._exit(78)
    proof_child.close()
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    ready_parent, ready_child = socket.socketpair()
    daemon_pidfd = os.pidfd_open(1)
    mapper_pid = os.fork()
    if mapper_pid == 0:
        try:
            parent.close()
            ready_parent.close()
            launch['close_descriptors']((child.fileno(), ready_child.fileno(), daemon_pidfd))
            enter_namespace(ready_child, launch)
            server = OwnerLauncher(child, 1, daemon_pidfd, bindings, data_root, launch)
            ready_child.sendall(b'S')
            ready_child.close()
            while server.serve_one():
                pass
            os._exit(0)
        except BaseException:
            os.write(2, b'bounded bootstrap mapper failed\n')
            os._exit(78)
    child.close()
    ready_child.close()
    os.close(daemon_pidfd)
    ready_parent.settimeout(30)
    install_maps(mapper_pid, ready_parent)
    launch['retire_child']('daemon')
    launch['close_descriptors']((parent.fileno(), ready_parent.fileno(), proof_parent.fileno()))
    if ready_parent.recv(1) != b'S':
        raise RuntimeError('bounded mapper did not start')
    ready_parent.close()

    # Application imports and owner proof generation happen only after all
    # service forks and host-capability retirement. Mapper never inherits proof.
    import secrets
    import sys
    import threading
    from hashlib import sha256

    sys.path.insert(0, '/app')
    from tinyassets.broker.supervisor import BrokerSupervisor, _protect_daemon
    from tinyassets.owner_launcher_client import OwnerLauncherClient
    from tinyassets.role_decoder import install_bounded_client

    _protect_daemon()
    proof = secrets.token_urlsafe(32)
    proof_parent.sendall(sha256(proof.encode()).hexdigest().encode('ascii'))
    proof_parent.close()
    client = OwnerLauncherClient(parent, mapper_pid)
    supervisor = BrokerSupervisor.from_bootstrap(data_root, broker_pid=broker_pid,
        socket_path=broker_dir / 'broker.sock', proof=proof)
    # Hold unreaped child identities. Any role death ends the whole container;
    # never reacquire host capabilities or restart a role with a new PID.
    watched = (os.pidfd_open(broker_pid), os.pidfd_open(mapper_pid))

    def watch():
        try:
            select.select(watched, [], [])
            os.write(2, b'bounded service exited; container restart required\n')
        finally:
            os._exit(78)

    threading.Thread(target=watch, name='role-lifetime', daemon=True).start()
    deadline = time.monotonic() + 30
    while True:
        supervisor._same_process()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(0.2)
                connection.connect(str(supervisor.socket_path))
                supervisor.verify_broker(connection)
            break
        except (FileNotFoundError, ConnectionRefusedError, TimeoutError):
            if time.monotonic() >= deadline:
                raise RuntimeError('bootstrapped broker readiness timeout') from None
            time.sleep(0.02)
    supervisor.start()
    install_bounded_client(client)
    return supervisor, client


def enter_namespace(channel, launch):
    """Child side of startup; parent must install maps before acknowledging."""
    os.setgroups([])
    launch['_checked'](ctypes.CDLL(None, use_errno=True).unshare(0x10000000), 'owner namespace')
    channel.sendall(b'R')
    if channel.recv(1) != b'M':
        raise RuntimeError('owner mapping handshake failed')
    os.setresgid(0, 0, 0)
    os.setresuid(0, 0, 0)
    libc = ctypes.CDLL(None, use_errno=True)
    for cap in range(int(Path('/proc/sys/kernel/cap_last_cap').read_text()) + 1):
        launch['_checked'](libc.prctl(24, cap, 0, 0, 0), 'retire bounding')
    launch['_capset'](CAPS)
    assert_mapper(launch)


def install_maps(pid, channel):
    """Startup parent only; no caller-selected ranges or runtime invocation."""
    if channel.recv(1) != b'R':
        raise RuntimeError('owner mapping child failed')
    for name in ('uid_map', 'gid_map'):
        Path(f'/proc/{pid}/{name}').write_text(MAPPING)
    channel.sendall(b'M')


def assert_mapper(launch):
    fields = launch['status']()
    if (os.getresuid() != (0, 0, 0) or os.getresgid() != (0, 0, 0)
            or os.getgroups() or int(fields['NoNewPrivs']) != 1
            or any(int(fields[k], 16) for k in ('CapBnd', 'CapAmb', 'CapInh'))
            or any(int(fields[k], 16) != CAPS for k in ('CapEff', 'CapPrm'))
            or any(Path('/proc/self/' + name).read_text().split() != MAPPING.split()
                   for name in ('uid_map', 'gid_map'))):
        raise RuntimeError('bounded mapper identity mismatch')


class OwnerLauncher:
    """Private inherited seqpacket channel with per-message kernel credentials.

    SO_PEERCRED on a pre-fork pair caches the creator's identity. SCM_CREDENTIALS
    instead authenticates the exact live daemon PID on every message, including
    when its uid/gid translate to overflow IDs in this owner-only namespace.
    The pinned pidfd prevents PID reuse; descendants sharing the pair fail.
    """

    def __init__(self, channel, daemon_pid, daemon_pidfd, bindings, data_root, launch):
        assert_mapper(launch)
        if channel.family != socket.AF_UNIX or channel.type != socket.SOCK_SEQPACKET:
            raise RuntimeError('launcher needs its private seqpacket channel')
        if channel.getsockname() or channel.getpeername():
            raise RuntimeError('launcher channel must be unnamed')
        self.channel, self.daemon_pid, self.daemon_pidfd = channel, daemon_pid, daemon_pidfd
        self.bindings = dict(bindings)
        for (principal, center), machine in self.bindings.items():
            if (not isinstance(principal, str) or not principal.strip()
                    or not isinstance(center, str) or not center
                    or any(c not in 'abcdefghijklmnopqrstuvwxyz'
                           'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_'
                           for c in center)
                    or type(machine) is not int or not FIRST < machine < FIRST + COUNT):
                raise RuntimeError('invalid broker owner admission')
        self.data_root, self.launch = str(data_root), launch
        self.channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        self.channel.settimeout(1)
        self.overflow_uid = int(Path('/proc/sys/kernel/overflowuid').read_text())
        self.overflow_gid = int(Path('/proc/sys/kernel/overflowgid').read_text())
        self.jobs = {}

    def _alive(self):
        return not select.select([self.daemon_pidfd], [], [], 0)[0]

    def serve_one(self):
        if not self._alive():
            raise RuntimeError('daemon exited')
        self._service_jobs()
        try:
            packet, ancillary, flags, _ = self.channel.recvmsg(
                4096, socket.CMSG_SPACE(12) + socket.CMSG_SPACE(32), socket.MSG_CMSG_CLOEXEC)
        except TimeoutError:
            return True
        received, credentials, unknown = [], [], False
        try:
            for level, kind, payload in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    fds = array.array('i')
                    fds.frombytes(payload[:len(payload) - len(payload) % fds.itemsize])
                    received.extend(fds)
                elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS:
                    credentials.append(struct.unpack('3i', payload))
                else:
                    unknown = True
            if (unknown or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
                    or credentials != [(self.daemon_pid, self.overflow_uid, self.overflow_gid)]
                    or not self._alive()):
                raise ValueError('unauthenticated daemon request')
            request = json.loads(packet)
            if request == {'op': 'STOP'} and not received:
                if self.jobs:
                    raise ValueError('owner cells are still running')
                self.channel.sendall(b'{"op":"STOPPED"}')
                return False
            self._decoder(request, received)
        except (ValueError, TypeError, KeyError, OSError):
            self.channel.sendall(b'{"op":"REFUSED"}')
        finally:
            for fd in received:
                os.close(fd)
        return True

    def _decoder(self, request, received):
        kind = request.get('kind') if isinstance(request, dict) else None
        streaming = isinstance(request, dict) and request.get('op') == 'START'
        fields = {'op', 'kind', 'principal', 'command_center'}
        if kind == 'image-decoder':
            fields.add('mime')
        if kind == 'preview-write':
            fields.add('ui_id')
        if (not isinstance(request, dict)
                or set(request) != fields or request['op'] not in {'SPAWN', 'START'}
                or kind not in {'image-decoder', 'workspace-git', 'ui-preview', 'preview-write'}
                or (kind == 'image-decoder' and (
                    not isinstance(request['mime'], str)
                    or request['mime'] not in {
                        'image/png', 'image/jpeg', 'image/webp', 'image/gif'}))
                or not isinstance(request['principal'], str)
                or not isinstance(request['command_center'], str)
                or len(received) != (2 if kind in {'workspace-git', 'preview-write'} else 1)
                    + int(streaming)):
            raise ValueError('unsupported owner engine')
        if not streaming and self.jobs:
            raise ValueError('blocking spawn cannot suspend active cell supervision')
        machine = self.bindings[(request['principal'], request['command_center'])]
        inner = machine - FIRST
        if streaming:
            if (len(self.jobs) >= MAX_CELLS or sum(
                    job[1] == machine for job in self.jobs.values()) >= MAX_OWNER_CELLS):
                raise ValueError('owner cell concurrency is exhausted')
            self._daemon_endpoint(received[-1], socket.SOCK_SEQPACKET)
        if kind == 'preview-write':
            ui_id = request['ui_id']
            if (not isinstance(ui_id, str) or not 1 <= len(ui_id) <= 64
                    or ui_id[0] not in 'abcdefghijklmnopqrstuvwxyz0123456789'
                    or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in ui_id)):
                raise ValueError('invalid preview output name')
            info = os.fstat(received[1])
            if (not stat.S_ISDIR(info.st_mode) or info.st_gid != inner
                    or info.st_uid not in (inner, self.overflow_uid)
                    or os.readlink(f'/proc/self/fd/{received[1]}') !=
                    self.data_root + '/' + request['command_center']):
                raise ValueError('preview output root does not match admitted center')
        if kind == 'workspace-git':
            info = os.fstat(received[1])
            if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (inner, inner):
                raise ValueError('git directory is not owned by the admitted owner')
            source = os.readlink(f'/proc/self/fd/{received[1]}')
            prefix = self.data_root + '/' + request['command_center'] + '/'
            if not source.startswith(prefix) or source.endswith(' (deleted)'):
                raise ValueError('git directory is outside the admitted command center')
        fd = received[0]
        self._daemon_endpoint(fd, socket.SOCK_STREAM)
        status_channel = None
        if streaming:
            status_channel = socket.socket(fileno=os.dup(received[-1]))
            try:
                status_channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
                status_channel.setblocking(False)
            except BaseException:
                status_channel.close()
                raise
        try:
            pid = os.fork()
        except BaseException:
            if status_channel is not None:
                status_channel.close()
            raise
        if pid == 0:
            try:
                os.dup2(fd, 0)
                os.dup2(fd, 1)
                null = os.open('/dev/null', os.O_WRONLY)
                os.dup2(null, 2)
                if kind in {'workspace-git', 'preview-write'}:
                    os.dup2(received[1], 3)
                self.launch['close_descriptors'](
                    (3,) if kind in {'workspace-git', 'preview-write'} else ())
                os.setgroups([])
                os.setresgid(inner, inner, inner)
                os.setresuid(inner, inner, inner)
                self.launch['_capset'](0)
                self.launch['_assert_caps'](0)
                os.umask(0o007)
                os.chdir('/')
                if kind == 'workspace-git':
                    command = ['/usr/local/libexec/ta-git.py', 'enter', str(inner), self.data_root]
                elif kind == 'ui-preview':
                    command = ['/usr/local/libexec/ta-decoder.py', 'enter-preview',
                               self.data_root, str(inner)]
                elif kind == 'preview-write':
                    command = ['/usr/local/libexec/ta-decoder.py', 'enter-preview-write',
                               request['ui_id'], self.data_root, str(inner)]
                else:
                    command = ['/usr/local/libexec/ta-decoder.py', 'enter-owner',
                               request['mime'], self.data_root, str(inner)]
                os.execve('/opt/venv/bin/python', ['/opt/venv/bin/python', '-I', '-B', *command],
                    {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': '/tmp',
                     'PYTHONDONTWRITEBYTECODE': '1'})
            except BaseException:
                os._exit(126)
        deadline = time.monotonic() + (75 if kind == 'ui-preview' else
                                     65 if kind == 'workspace-git' else 35)
        if streaming:
            self.jobs[pid] = (inner, machine, deadline, status_channel)
            try:
                self.channel.sendall(json.dumps(
                    dict(op='STARTED', uid=machine, gid=machine)).encode())
            except BaseException:
                self.jobs[pid] = (inner, machine, 0, status_channel)
                self._service_jobs()
                raise
            return
        while True:
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited:
                break
            if time.monotonic() >= deadline or not self._alive():
                # Before setresuid the child still belongs to mapper UID 0.
                # Try that identity first, then its final owner identity; the
                # one-way transition cannot race past both matching attempts.
                try:
                    os.kill(pid, signal.SIGKILL)
                except PermissionError:
                    os.seteuid(inner)
                    try:
                        os.kill(pid, signal.SIGKILL)
                    finally:
                        os.seteuid(0)
                except ProcessLookupError:
                    pass
                _, status = os.waitpid(pid, 0)
                break
            time.sleep(0.01)
        assert_mapper(self.launch)
        self.channel.sendall(json.dumps({'op': 'SPAWN_DONE',
            'returncode': os.waitstatus_to_exitcode(status), 'uid': machine,
            'gid': machine}).encode())

    def _daemon_endpoint(self, fd, kind):
        if not stat.S_ISSOCK(os.fstat(fd).st_mode):
            raise ValueError('owner cell needs daemon socketpair')
        with socket.socket(fileno=os.dup(fd)) as endpoint:
            if (endpoint.family != socket.AF_UNIX or endpoint.type != kind
                    or endpoint.getsockname() or endpoint.getpeername()
                    or struct.unpack('3i', endpoint.getsockopt(socket.SOL_SOCKET,
                        socket.SO_PEERCRED, 12)) !=
                    (self.daemon_pid, self.overflow_uid, self.overflow_gid)):
                raise ValueError('owner endpoint is not daemon-owned')

    def _service_jobs(self):
        for pid, (inner, machine, deadline, channel) in list(self.jobs.items()):
            waited, status = os.waitpid(pid, os.WNOHANG)
            cancel = time.monotonic() >= deadline
            if not waited and select.select([channel], [], [], 0)[0]:
                # EOF revokes this launch. Any malformed or forged control
                # also cancels only this cell; it can never select another PID.
                _, ancillary, _, _ = channel.recvmsg(
                    32, socket.CMSG_SPACE(12) + socket.CMSG_SPACE(32), socket.MSG_CMSG_CLOEXEC)
                for level, kind, payload in ancillary:
                    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                        handles = array.array('i')
                        handles.frombytes(payload[:len(payload) - len(payload) % handles.itemsize])
                        for handle in handles:
                            os.close(handle)
                cancel = True
            if not waited and cancel:
                try:
                    os.kill(pid, signal.SIGKILL)
                except PermissionError:
                    os.seteuid(inner)
                    try:
                        os.kill(pid, signal.SIGKILL)
                    finally:
                        os.seteuid(0)
                except ProcessLookupError:
                    pass
                _, status = os.waitpid(pid, 0)
                waited = pid
            if waited:
                try:
                    receipt = dict(op='SPAWN_DONE', returncode=os.waitstatus_to_exitcode(status),
                                   uid=machine, gid=machine)
                    channel.sendall(json.dumps(receipt).encode())
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    channel.close()
                    del self.jobs[pid]
        assert_mapper(self.launch)
