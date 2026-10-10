"""D62 bounded owner launcher used by the production bootstrap.

Loaded by the verified startup chain before namespace entry. The startup parent
installs the fixed maps and becomes the daemon; no privileged mapper survives.
Bindings are broker-resolved startup admissions, never numeric request fields.
"""
from __future__ import annotations

import array
import ctypes
import json
import os
import re
import runpy
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
_diagnostic_path = Path(__file__).resolve().parent.parent / 'tinyassets/cell_diagnostics.py'
_diagnostics = runpy.run_path(str(_diagnostic_path if _diagnostic_path.is_file()
                                else Path('/app/tinyassets/cell_diagnostics.py')))


class BootstrapRefused(RuntimeError):
    """Only fixed bootstrap diagnostics and validated filesystem paths."""


def _bootstrap_failure(exc, operation):
    # Never stringify arbitrary exceptions: provider/config/library errors may
    # carry secrets. A code location and errno still identify their operation.
    trace = exc.__traceback__
    while trace is not None and trace.tb_next is not None:
        trace = trace.tb_next
    where = (f'{trace.tb_frame.f_code.co_name}:{trace.tb_lineno}' if trace else operation)
    reason = str(exc) if isinstance(exc, BootstrapRefused) else type(exc).__name__
    number = exc.errno if isinstance(exc, OSError) else None
    message = f'bounded {operation} failed at {where}: {reason}; errno={number}'
    try:
        os.write(2, (ascii(message)[1:-1][:900] + '; exit=78\n').encode('ascii'))
    finally:
        os._exit(78)


def package_usage(pid, proc='/proc'):
    """Count/RSS of the cell from OUTSIDE its PID namespace and owner identity.

    Orphans stay below the cell's namespace init, so double-fork/setsid cannot
    leave this tree. Only kernel stat records are read; no command lines/env.
    """
    records = {}
    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        try:
            raw = Path(proc, name, 'stat').read_text()
            fields = raw[raw.rfind(')') + 2:].split()
            records[int(name)] = (int(fields[1]), int(fields[21]))
        except (FileNotFoundError, ProcessLookupError):
            # procfs can return ESRCH after open but before read when a task
            # exits. Like ENOENT, that is a vanished process, not failed
            # accounting. Unrelated daemon children race this scan too.
            continue  # A process that exited holds no resident memory.
    if pid not in records:
        raise RuntimeError('package root is unmeasurable')
    children = {}
    for child, (parent, _) in records.items():
        children.setdefault(parent, []).append(child)
    seen, pending, pages = set(), [pid], 0
    while pending:
        child = pending.pop()
        if child in seen:
            continue
        seen.add(child)
        pages += records[child][1]
        pending.extend(children.get(child, ()))
    return len(seen), pages * os.sysconf('SC_PAGE_SIZE')


def bootstrap_services(data_root, run_root, bindings, launch, *, generation):
    """Production PID1 bootstrap for the broker and bounded owner mapper.

    Migration/owner bindings must already be verified with all writers stopped.
    ``generation`` is the admission-log high-water mark that startup reconciled
    (DA5); the mapper binds only rows above it at runtime.
    Fork both services in the existing privileged startup window, then turn
    PID1 into the capability-free daemon. No host-privileged process survives.
    A service death exits PID1: the container tears down every descendant.
    """
    if os.getpid() != 1:
        raise RuntimeError('service bootstrap requires container PID1')
    try:
        return _bootstrap_services(data_root, run_root, bindings, launch, generation)
    except BaseException as exc:
        # Especially before daemon retirement, returning an exception to a
        # caller would let a caught startup failure retain host authority.
        _bootstrap_failure(exc, 'bootstrap')


def _bootstrap_services(data_root, run_root, bindings, launch, generation):
    if type(generation) is not int or generation < 0:
        raise RuntimeError('invalid bootstrap admission generation')
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
                raise BootstrapRefused(f'bootstrap path contains an alias: {ancestor}')
    for ancestor in (run_root, *run_root.parents):
        info = ancestor.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise BootstrapRefused(
                f'bootstrap IPC parent is writable or not root-owned: {ancestor}')
    broker_dir = run_root / 'broker'
    info = broker_dir.lstat()
    if (not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid,
            stat.S_IMODE(info.st_mode)) != (1002, 1101, 0o2750)):
        raise BootstrapRefused(f'invalid bootstrap broker socket directory: {broker_dir}')
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
            raise BootstrapRefused(
                f'owner root does not match bootstrap binding: {data_root / center}')

    proof_parent, proof_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    # DA2: the mapper's read-only broker pair exists before the broker fork;
    # the broker keeps its end across exec and the mapper inherits the other.
    admission_broker, admission_mapper = socket.socketpair(
        socket.AF_UNIX, socket.SOCK_SEQPACKET)
    broker_pid = os.fork()
    if broker_pid == 0:
        try:
            proof_parent.close()
            admission_mapper.close()
            launch['retire_child']('broker')
            launch['close_descriptors']((proof_child.fileno(), admission_broker.fileno()))
            proof_child.settimeout(30)
            digest, _, mapper = proof_child.recv(128).decode('ascii').partition(':')
            proof_child.close()
            if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise RuntimeError('invalid startup proof hash')
            if not re.fullmatch('[1-9][0-9]{0,9}', mapper):
                raise RuntimeError('invalid startup mapper pid')
            os.set_inheritable(admission_broker.fileno(), True)
            argv = ['/opt/venv/bin/python', '-I', '-B', '/app/broker_main.py',
                    '--socket', str(broker_dir / 'broker.sock'),
                    '--state', str(data_root / '.broker' / 'state'),
                    '--data-root', str(data_root), '--owner-uid', '1001',
                    '--proof-sha256', digest,
                    '--mapper-channel', str(admission_broker.fileno()),
                    '--mapper-pid', mapper]
            os.chdir('/')
            os.execve(argv[0], argv, launch['broker_environment'](data_root))
        except BaseException as exc:
            _bootstrap_failure(exc, 'bootstrap broker')
    proof_child.close()
    admission_broker.close()
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    ready_parent, ready_child = socket.socketpair()
    daemon_pidfd = os.pidfd_open(1)
    mapper_pid = os.fork()
    if mapper_pid == 0:
        try:
            parent.close()
            ready_parent.close()
            launch['close_descriptors']((child.fileno(), ready_child.fileno(), daemon_pidfd,
                                         admission_mapper.fileno()))
            enter_namespace(ready_child, launch)
            server = OwnerLauncher(child, 1, daemon_pidfd, bindings, data_root, launch,
                                   broker=admission_mapper, broker_pid=broker_pid,
                                   generation=generation)
            ready_child.sendall(b'S')
            ready_child.close()
            while server.serve_one():
                pass
            os._exit(0)
        except BaseException as exc:
            _bootstrap_failure(exc, 'bootstrap mapper')
    child.close()
    ready_child.close()
    admission_mapper.close()
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
    # DA2: the mapper PID travels with the proof hash; until it arrives the
    # broker has not exec'd, so it serves nothing on the mapper pair.
    proof_parent.sendall((sha256(proof.encode()).hexdigest() + ':'
                          + str(mapper_pid)).encode('ascii'))
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
    protected = {broker_pid, mapper_pid}

    def reap():
        seen = {}
        while True:
            time.sleep(REAP_INTERVAL)
            try:
                reap_orphans(protected, seen)
            except Exception as exc:  # never die silently; zombies would grow again
                os.write(2, f'orphan reaper failed: {exc!r}\n'.encode())

    threading.Thread(target=reap, name='orphan-reaper', daemon=True).start()
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
                raise BootstrapRefused(f'broker readiness timeout: {broker_dir}') from None
            time.sleep(0.02)
    supervisor.start()
    install_bounded_client(client)
    return supervisor, client


REAP_INTERVAL, REAP_GRACE = 10, 60


def reap_orphans(protected, seen, *, grace=REAP_GRACE, proc='/proc', now=time.monotonic):
    """Reap adopted orphans of this PID1; never a status the daemon waits on.

    PID1 is the daemon (D60), so the kernel reparents every orphan here; each
    provider turn leaves one ``bwrap``. ``waitpid(-1)`` would also reap the
    held broker/mapper and race subprocess/asyncio waiters, which CPython turns
    into a silent returncode 0. Owners wait within milliseconds of exit, so only
    a child seen as a zombie for ``grace`` seconds (same start time) is unowned.
    """
    me, current, reaped = os.getpid(), {}, []
    for name in os.listdir(proc):
        if not name.isdigit() or int(name) in protected:
            continue
        try:
            raw = Path(proc, name, 'stat').read_text()
        except OSError:
            continue
        fields = raw[raw.rfind(')') + 2:].split()
        if fields[0] == 'Z' and int(fields[1]) == me:
            key = (int(name), fields[19])
            current[key] = seen.get(key, now())
    seen.clear()
    for key, first in current.items():
        if now() - first < grace:
            seen[key] = first
            continue
        # The pidfd pins the identity the start time then confirms: no reuse.
        try:
            pidfd = os.pidfd_open(key[0])
        except ProcessLookupError:
            continue  # its owner reaped it first
        try:
            raw = Path(proc, str(key[0]), 'stat').read_text()
            if raw[raw.rfind(')') + 2:].split()[19] == key[1]:
                os.waitid(os.P_PIDFD, pidfd, os.WEXITED | os.WNOHANG)
                reaped.append(key[0])
        except (ChildProcessError, FileNotFoundError, ProcessLookupError):
            pass  # reaped (or gone) between the scan and here; anything else is loud
        finally:
            os.close(pidfd)
    return reaped


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

    def __init__(self, channel, daemon_pid, daemon_pidfd, bindings, data_root, launch, *,
                 broker=None, broker_pid=None, generation=0):
        assert_mapper(launch)
        if channel.family != socket.AF_UNIX or channel.type != socket.SOCK_SEQPACKET:
            raise RuntimeError('launcher needs its private seqpacket channel')
        if channel.getsockname() or channel.getpeername():
            raise RuntimeError('launcher channel must be unnamed')
        if type(generation) is not int or generation < 0:
            raise RuntimeError('invalid bootstrap admission generation')
        if broker is not None and (
                broker.family != socket.AF_UNIX or broker.type != socket.SOCK_SEQPACKET
                or broker.getsockname() or broker.getpeername()
                or type(broker_pid) is not int or broker_pid <= 1):
            raise RuntimeError('invalid admission broker channel')
        # DA2/DA5: the inherited read-only broker pair, its one peer, and the
        # log high-water mark startup reconciled. Absent: runtime admission refuses.
        self.broker, self.broker_pid, self.generation = broker, broker_pid, generation
        if broker is not None:
            broker.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            broker.settimeout(5)
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
        self.diagnostics = {}
        self.package_jobs = set()
        self.browser_jobs = set()
        self.delete_fences = {}

    def _alive(self):
        return not select.select([self.daemon_pidfd], [], [], 0)[0]

    def _broker_read(self, document):
        """DA2: one read-only broker answer, authenticated as the exact broker PID.

        Any protocol failure poisons the pair: a late reply can never be read
        as the answer to a later question.
        """
        if self.broker is None:
            raise ValueError('admission broker channel is unavailable')
        try:
            self.broker.sendall(json.dumps(document).encode())
            packet, ancillary, flags, _ = self.broker.recvmsg(
                4096, socket.CMSG_SPACE(12) + socket.CMSG_SPACE(32), socket.MSG_CMSG_CLOEXEC)
            credentials, unknown = [], False
            for level, kind, payload in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS:
                    credentials.append(struct.unpack('3i', payload))
                else:
                    unknown = True
                    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                        fds = array.array('i')
                        fds.frombytes(payload[:len(payload) - len(payload) % fds.itemsize])
                        for fd in fds:
                            os.close(fd)
            if (unknown or not packet or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
                    or credentials != [(self.broker_pid, self.overflow_uid, self.overflow_gid)]):
                raise ValueError('unauthenticated broker answer')
            answer = json.loads(packet)
            if not isinstance(answer, dict) or answer.get('op') == 'REFUSED':
                raise ValueError('broker refused the admission read')
            return answer
        except BaseException:
            self.broker.close()
            self.broker = None
            raise

    def _owner_machine(self, principal):
        answer = self._broker_read({'op': 'OWNER_MACHINE', 'principal': principal})
        if answer == {'op': 'ABSENT'}:
            return None
        machine = answer.get('machine')
        if (set(answer) != {'op', 'machine'} or answer['op'] != 'OWNER_MACHINE_IS'
                or type(machine) is not int or not FIRST < machine < FIRST + COUNT):
            raise ValueError('invalid broker reservation')
        return machine

    def _center_state(self, center):
        answer = self._broker_read({'op': 'CENTER_STATE', 'center': center})
        if (set(answer) != {'op', 'state'} or answer['op'] != 'CENTER_STATE_IS'
                or answer['state'] not in ('unadmitted', 'admitted', 'retired')):
            raise ValueError('invalid broker center state')
        return answer['state']

    def _admission_row(self, generation):
        answer = self._broker_read({'op': 'ADMISSION_ROW', 'generation': generation})
        if answer == {'op': 'ABSENT'}:
            return None
        if (set(answer) != {'op', 'generation', 'event', 'principal', 'center', 'machine'}
                or answer['op'] != 'ADMISSION_ROW_IS' or answer['generation'] != generation
                or answer['event'] not in ('admit', 'retire')
                or type(answer['machine']) is not int
                or not FIRST < answer['machine'] < FIRST + COUNT):
            raise ValueError('invalid broker admission row')
        return answer

    def serve_one(self):
        if not self._alive():
            raise RuntimeError('daemon exited')
        self._service_jobs()
        self.channel.settimeout(0.05 if self.jobs else 1)
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
        except (ValueError, TypeError, KeyError, OSError) as exc:
            reason = _diagnostics['failure_reason'](exc, 'mapper')
            os.write(2, ('owner cell refused: ' + reason + '\n').encode())
            self.channel.sendall(json.dumps(dict(op='REFUSED', reason=reason)).encode())
        finally:
            for fd in received:
                os.close(fd)
        return True

    def _decoder(self, request, received):
        if isinstance(request, dict) and request.get('op') == 'DELETE_DONE':
            self._finish_delete(request, received)
            return
        if isinstance(request, dict) and request.get('op') in ('ADMIT', 'RETIRE'):
            (self._admit if request['op'] == 'ADMIT' else self._retire)(request, received)
            return
        kind = request.get('kind') if isinstance(request, dict) else None
        streaming = isinstance(request, dict) and request.get('op') == 'START'
        fields = {'op', 'kind', 'principal', 'command_center'}
        if kind == 'image-decoder':
            fields.add('mime')
        if kind == 'preview-write':
            fields.add('ui_id')
        if kind == 'node-sandbox':
            fields.add('workspace')
        if kind == 'tool-jail':
            fields.update(('egress', 'ta', 'extensions'))
        if kind in ('provider-discovery', 'workspace-remote'):
            fields.add('egress')
        if kind == 'provider-exec':
            fields.update(('egress', 'engine'))
        if kind == 'package':
            fields.update(('revision', 'ta', 'egress'))
        if kind in ('owner-delete', 'owner-delete-subtree'):
            fields.add('delete_token')
        socket_count = (1 if kind == 'workspace-provision' else
                        sum(request.get(key) is True for key in ('egress', 'ta'))
                        if kind in ('tool-jail', 'package', 'workspace-remote') else
                        sum(request.get(key) is True for key in ('egress', 'engine'))
                        if kind in ('provider-discovery', 'provider-exec') else 0)
        extension_count = int(kind == 'tool-jail' and request.get('extensions') is True)
        mounted = kind in {'workspace-git', 'workspace-remote', 'workspace-provision',
                           'preview-write', 'tool-jail',
                           'tool-files', 'provider-discovery', 'provider-exec', 'package',
                           'owner-delete', 'owner-delete-subtree',
                           'center-root', 'owner-content', 'owner-measure'} or (
            kind == 'node-sandbox' and request.get('workspace') is True)
        if (not isinstance(request, dict)
                or set(request) != fields or request['op'] not in {'SPAWN', 'START'}
                or kind not in {'image-decoder', 'workspace-git', 'workspace-remote',
                                'workspace-provision', 'ui-preview', 'browser', 'preview-write',
                                'node-sandbox', 'tool-jail', 'ingestion-video',
                                'provider-discovery', 'provider-exec', 'tool-files',
                                'package', 'owner-delete', 'owner-delete-subtree',
                                'center-root', 'owner-content', 'owner-measure'}
                or (kind in ('center-root', 'owner-content', 'owner-measure',
                             'owner-delete-subtree', 'workspace-provision')
                    and not streaming)
                # A workspace operation reaches a remote only through the
                # center's own checking proxy. Making an empty workspace
                # reaches nothing, and gets no socket at all.
                or (kind == 'workspace-remote' and (not streaming
                    or type(request['egress']) is not bool))
                or (kind in ('owner-delete', 'owner-delete-subtree') and (not streaming
                    or type(request['delete_token']) is not str
                    or not re.fullmatch('[a-f0-9]{32}', request['delete_token'])))
                or (kind == 'package' and (not streaming or type(request['ta']) is not bool
                    or type(request['egress']) is not bool
                    or type(request['revision']) is not str
                    or not re.fullmatch('[a-f0-9]{64}', request['revision'])))
                or (kind == 'node-sandbox' and type(request['workspace']) is not bool)
                or (kind in ('provider-discovery', 'provider-exec') and (
                    not streaming or type(request['egress']) is not bool))
                # K2: the engine relay rides only beside egress, on provider-exec.
                or (kind == 'provider-exec' and (type(request['engine']) is not bool
                    or (request['engine'] and not request['egress'])))
                or (kind == 'tool-jail' and any(
                    type(request[key]) is not bool for key in ('egress', 'ta', 'extensions')))
                or (kind == 'image-decoder' and (
                    not isinstance(request['mime'], str)
                    or request['mime'] not in {
                        'image/png', 'image/jpeg', 'image/webp', 'image/gif'}))
                or not isinstance(request['principal'], str)
                or not isinstance(request['command_center'], str)
                or len(received) != (2 if mounted else 1)
                    + int(streaming) + socket_count + extension_count
                    + 2 * int(kind == 'provider-exec')):
            raise ValueError('unsupported owner engine')
        if not streaming and self.jobs:
            raise ValueError('blocking spawn cannot suspend active cell supervision')
        if kind == 'center-root':
            machine = self._center_root_machine(request, received[1])
        else:
            machine = self.bindings[(request['principal'], request['command_center'])]
        self._check_delete_fence(machine, request)
        inner = machine - FIRST
        if streaming:
            if (len(self.jobs) >= MAX_CELLS or sum(
                    job[1] == machine for job in self.jobs.values()) >= MAX_OWNER_CELLS):
                raise ValueError('owner cell concurrency is exhausted')
            self._daemon_endpoint(received[-1], socket.SOCK_SEQPACKET)
        if kind == 'provider-exec':
            # Ordinary pipes, as a local subprocess would get: stdin's read end
            # in the data slot, stdout's and stderr's write ends before status.
            self._daemon_pipe(received[0], os.O_RDONLY)
            self._daemon_pipe(received[-3], os.O_WRONLY)
            self._daemon_pipe(received[-2], os.O_WRONLY)
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
        if kind in ('tool-jail', 'tool-files', 'package', 'owner-delete',
                    'workspace-remote', 'owner-measure'):
            info = os.fstat(received[1])
            expected = self.data_root + '/' + request['command_center']
            if kind == 'package':
                expected += '/.runtime/package-cells/' + request['revision']
                valid_owner = info.st_uid == self.overflow_uid and not info.st_mode & 0o022
            else:
                valid_owner = info.st_gid == inner and info.st_uid in (inner, self.overflow_uid)
            if (not stat.S_ISDIR(info.st_mode) or not valid_owner
                    or os.readlink(f'/proc/self/fd/{received[1]}') != expected):
                raise ValueError('tool root does not match admitted center')
            index = 2
            prefix = (self.data_root + '/.universe-sidecars/'
                      + request['command_center'] + '/')
            for name in ('egress', 'ta'):
                if not request.get(name):
                    continue
                info = os.fstat(received[index])
                source = os.readlink(f'/proc/self/fd/{received[index]}')
                pattern = (rf'egress-{self.daemon_pid}\.sock' if name == 'egress'
                           else r'ta-[a-f0-9]{32}\.sock')
                if (not stat.S_ISSOCK(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != self.overflow_uid
                        or not source.startswith(prefix)
                        or not re.fullmatch(pattern, source[len(prefix):])):
                    raise ValueError('tool relay does not match admitted center')
                index += 1
        if kind == 'owner-content':
            info = os.fstat(received[1])
            source = os.readlink(f'/proc/self/fd/{received[1]}')
            prefix = self.data_root + '/.role-admission/'
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != self.overflow_uid
                    or info.st_gid != self.overflow_gid or info.st_mode & 0o007
                    or not source.startswith(prefix)
                    or not re.fullmatch('[a-f0-9]{32}', source[len(prefix):])):
                raise ValueError('owner content staging is not daemon-private')
        if extension_count:
            info = os.fstat(received[2 + socket_count])
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != self.overflow_uid
                    or info.st_mode & 0o022):
                raise ValueError('tool extension tree is not daemon-owned sealed content')
        if kind in ('provider-discovery', 'provider-exec'):
            # D82: exactly one daemon-sealed launch snapshot of this admitted
            # center; the caller names no path, identity or executable.
            info = os.fstat(received[1])
            source = os.readlink(f'/proc/self/fd/{received[1]}')
            prefix = (self.data_root + '/' + request['command_center']
                      + '/.runtime/provider-launch-credentials/')
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != self.overflow_uid
                    or not source.startswith(prefix)
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',
                                        source[len(prefix):])):
                raise ValueError('provider snapshot does not match admitted center')
            if request['egress']:
                info = os.fstat(received[2])
                source = os.readlink(f'/proc/self/fd/{received[2]}')
                prefix = (self.data_root + '/.universe-sidecars/'
                          + request['command_center'] + '/')
                if (not stat.S_ISSOCK(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != self.overflow_uid
                        or source != prefix + f'egress-{self.daemon_pid}.sock'):
                    raise ValueError('provider egress does not match admitted center')
            if request.get('engine'):
                info = os.fstat(received[3])
                source = os.readlink(f'/proc/self/fd/{received[3]}')
                if (not stat.S_ISSOCK(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != self.overflow_uid
                        or not source.startswith(prefix)
                        or not re.fullmatch(rf'engine-{self.daemon_pid}-[a-f0-9]{{12}}\.sock',
                                            source[len(prefix):])):
                    raise ValueError('provider engine relay does not match admitted center')
        if kind == 'owner-delete-subtree':
            # Pool reclamation: one owner subtree strictly below the center root,
            # removed under the same owner-wide fence as account deletion.
            info = os.fstat(received[1])
            source = os.readlink(f'/proc/self/fd/{received[1]}')
            prefix = self.data_root + '/' + request['command_center'] + '/'
            if (not stat.S_ISDIR(info.st_mode) or info.st_gid != inner
                    or info.st_uid not in (inner, self.overflow_uid)
                    or not source.startswith(prefix) or source.endswith(' (deleted)')):
                raise ValueError('subtree is not an owner directory inside the admitted center')
        if kind in ('workspace-git', 'workspace-provision') or (kind == 'node-sandbox' and mounted):
            info = os.fstat(received[1])
            if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (inner, inner):
                raise ValueError('git directory is not owned by the admitted owner')
            source = os.readlink(f'/proc/self/fd/{received[1]}')
            prefix = self.data_root + '/' + request['command_center'] + '/'
            if not source.startswith(prefix) or source.endswith(' (deleted)'):
                raise ValueError('git directory is outside the admitted command center')
        if kind == 'workspace-provision':
            info = os.fstat(received[2])
            source = os.readlink(f'/proc/self/fd/{received[2]}')
            prefix = self.data_root + '/.universe-sidecars/' + request['command_center'] + '/'
            if (not stat.S_ISSOCK(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != self.overflow_uid or not source.startswith(prefix)
                    or not re.fullmatch(r'registry-[a-f0-9]{32}\.sock', source[len(prefix):])):
                raise ValueError('registry relay is outside the admitted center')
        fd = received[0]
        if kind != 'provider-exec':
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
        error_read, error_write = os.pipe2(os.O_CLOEXEC)
        os.set_blocking(error_read, False)
        try:
            if kind in ('owner-delete', 'owner-delete-subtree'):
                self.delete_fences[machine] = (request['principal'],
                    request['command_center'], request['delete_token'])
            pid = os.fork()
        except BaseException:
            os.close(error_read)
            os.close(error_write)
            if status_channel is not None:
                status_channel.close()
            raise
        if pid == 0:
            try:
                import fcntl

                stderr_copy = fcntl.fcntl(error_write, fcntl.F_DUPFD_CLOEXEC, 20)
                os.dup2(stderr_copy, 2)
                stdout_copy = None
                if kind == 'provider-exec':
                    stdout_copy = fcntl.fcntl(received[-3], fcntl.F_DUPFD_CLOEXEC, 20)
                retained = (3,) if mounted else ()
                if kind in ('tool-jail', 'provider-discovery', 'provider-exec',
                             'package', 'workspace-remote', 'workspace-provision') and (
                                 socket_count or extension_count):
                    import fcntl

                    # Copy before assigning fixed slots, so a destination
                    # cannot overwrite another received source descriptor.
                    sources = [fcntl.fcntl(value, fcntl.F_DUPFD_CLOEXEC, 20)
                               for value in received[:2 + socket_count + extension_count]]
                    fd = sources[0]
                    os.dup2(sources[1], 3)
                    index = 2
                    retained = [3]
                    if kind == 'workspace-provision':
                        os.dup2(sources[2], 4)
                        retained.append(4)
                    slots = ((('egress', 4), ('engine', 5)) if kind.startswith('provider-')
                             else (('egress', 4), ('ta', 5)))
                    for name, target in slots:
                        if request.get(name):
                            os.dup2(sources[index], target)
                            retained.append(target)
                            index += 1
                    if extension_count:
                        os.dup2(sources[index], 6)
                        retained.append(6)
                os.dup2(fd, 0)
                os.dup2(fd if stdout_copy is None else stdout_copy, 1)
                os.dup2(stderr_copy, 2)
                if mounted and not (socket_count or extension_count):
                    os.dup2(received[1], 3)
                self.launch['close_descriptors'](retained)
                os.setgroups([])
                os.setresgid(inner, inner, inner)
                os.setresuid(inner, inner, inner)
                self.launch['_capset'](0)
                self.launch['_assert_caps'](0)
                os.umask(0o007)
                os.chdir('/')
                if kind == 'package':
                    command = ['/usr/local/libexec/ta-decoder.py', 'enter-package',
                               request['revision'] + (('e' if request['egress'] else '')
                                                      + ('t' if request['ta'] else '') or '-'),
                               self.data_root, str(inner)]
                elif kind in ('provider-discovery', 'provider-exec'):
                    mode = 'enter-provider-exec' if kind == 'provider-exec' else 'enter-provider'
                    command = ['/usr/local/libexec/ta-decoder.py',
                               mode,
                               ('e' if request['egress'] else '')
                               + ('g' if request.get('engine') else '') or '-',
                               self.data_root, str(inner)]
                elif kind == 'ingestion-video':
                    command = ['/usr/local/libexec/ta-decoder.py', 'enter-video',
                               'video', self.data_root, str(inner)]
                elif kind in ('owner-delete', 'owner-delete-subtree'):
                    command = ['/usr/local/libexec/ta-decoder.py', 'enter-owner-delete',
                               'delete', self.data_root, str(inner)]
                elif kind == 'center-root':
                    command = ['/usr/local/libexec/ta-decoder.py', 'enter-center-root',
                               'root', self.data_root, str(inner)]
                elif kind == 'owner-content':
                    command = ['/usr/local/libexec/ta-decoder.py', 'enter-content',
                               'content', self.data_root, str(inner)]
                elif kind == 'owner-measure':
                    command = ['/usr/local/libexec/ta-decoder.py', 'enter-measure',
                               'measure', self.data_root, str(inner)]
                elif kind == 'tool-files':
                    command = ['/usr/local/libexec/ta-decoder.py', 'enter-tool-files',
                               'files', self.data_root, str(inner)]
                elif kind == 'tool-jail':
                    command = ['/usr/local/libexec/ta-decoder.py', 'enter-tool',
                               ('e' if request['egress'] else '')
                               + ('t' if request['ta'] else '')
                               + ('x' if request['extensions'] else '') or '-',
                               self.data_root, str(inner)]
                elif kind == 'node-sandbox':
                    command = ['/usr/local/libexec/ta-decoder.py', 'enter-node',
                               'workspace' if mounted else 'data', self.data_root, str(inner)]
                elif kind == 'workspace-git':
                    command = ['/usr/local/libexec/ta-git.py', 'enter', str(inner), self.data_root]
                elif kind == 'workspace-provision':
                    command = ['/usr/local/libexec/ta-provision.py', 'enter', str(inner),
                               self.data_root]
                elif kind == 'workspace-remote':
                    command = ['/usr/local/libexec/ta-git.py', 'enter-remote', str(inner),
                               self.data_root, 'e' if request['egress'] else '-']
                elif kind == 'ui-preview':
                    command = ['/usr/local/libexec/ta-decoder.py', 'enter-preview',
                               self.data_root, str(inner)]
                elif kind == 'browser':
                    command = ['/usr/local/libexec/ta-decoder.py', 'enter-browser',
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
            except BaseException as exc:
                _diagnostics['report_failure'](exc, 'launcher')
                os._exit(126)
        os.close(error_write)
        error_sink = None
        if kind == 'provider-exec':
            # The daemon's stderr pipe. The cell's fd 2 is the mapper's own
            # pipe above, so a bootstrap failure is parsed here first and
            # every byte is then forwarded without ever blocking the mapper.
            error_sink = os.dup(received[-2])
            os.set_blocking(error_sink, False)
        # At most 256 KiB of pending stderr per cell; capture never blocks the
        # mapper. A slow consumer receives an explicit truncation completion.
        self.diagnostics[pid] = [error_read, error_sink, b'', None, bytearray(), False, None]
        # A provider turn or package server runs until it finishes: lifetime is
        # the daemon's revocation (EOF), daemon death or the RSS/process guard,
        # never a clock.
        deadline = float('inf') if kind in ('provider-exec', 'package') else time.monotonic() + (
            155 if kind == 'ingestion-video' else
            1810 if kind in ('node-sandbox', 'workspace-remote', 'workspace-provision') else
            660 if kind == 'tool-jail' else
            610 if kind == 'browser' else 75 if kind == 'ui-preview' else
            65 if kind == 'workspace-git' else 35)
        if streaming:
            self.jobs[pid] = (inner, machine, deadline, status_channel)
            if kind in ('package', 'provider-exec', 'browser'):
                self.package_jobs.add(pid)
            if kind == 'browser':
                self.browser_jobs.add(pid)
            try:
                self.channel.sendall(json.dumps(
                    dict(op='STARTED', uid=machine, gid=machine)).encode())
            except BaseException:
                self.jobs[pid] = (inner, machine, 0, status_channel)
                self._service_jobs()
                raise
            return
        while True:
            self._drain_diagnostics(pid)
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
        reason = self._finish_diagnostics(pid, os.waitstatus_to_exitcode(status))
        self.channel.sendall(json.dumps({'op': 'SPAWN_DONE',
            'returncode': os.waitstatus_to_exitcode(status), 'uid': machine,
            'gid': machine, 'stop_reason': reason}).encode())

    @staticmethod
    def _scope(principal, center):
        """The broker's own principal and center grammar, checked before asking it."""
        if (not isinstance(principal, str) or not principal.strip()
                or principal != principal.strip() or len(principal) > 512
                or not principal.isprintable() or not isinstance(center, str)
                or not re.fullmatch('[A-Za-z0-9_-]{1,128}', center)):
            raise ValueError('invalid admission scope')

    def _center_root_machine(self, request, staging):
        """DA3 step 2: machine from the broker, never the daemon; a fresh name only."""
        principal, center = request['principal'], request['command_center']
        self._scope(principal, center)
        if any(bound == center for _, bound in self.bindings):
            raise ValueError('center is already bound')
        machine = self._owner_machine(principal)
        if machine is None or self._center_state(center) != 'unadmitted':
            raise ValueError('center root is not admissible')
        info = os.fstat(staging)
        source = os.readlink(f'/proc/self/fd/{staging}')
        prefix = self.data_root + '/.role-admission/'
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != self.overflow_uid
                or info.st_gid != self.overflow_gid or info.st_mode & 0o007
                or not source.startswith(prefix)
                or not re.fullmatch('[a-f0-9]{32}', source[len(prefix):])):
            raise ValueError('center-root staging is not daemon-private')
        return machine

    def _check_delete_fence(self, machine, request):
        fence = self.delete_fences.get(machine)
        if request['kind'] in ('owner-delete', 'owner-delete-subtree'):
            expected = (request['principal'], request['command_center'], request['delete_token'])
            if ((fence is not None and fence != expected)
                    or any(job[1] == machine for job in self.jobs.values())):
                raise ValueError('owner deletion is not quiescent or token differs')
        elif fence is not None:
            raise ValueError('owner deletion fence is active')

    def _admission_request(self, request, received, event, descriptors):
        """DA4/DA6: verify the daemon's {principal, center, generation} against the log."""
        if (set(request) != {'op', 'principal', 'command_center', 'generation'}
                or len(received) != descriptors or type(request['generation']) is not int
                or request['generation'] < 1):
            raise ValueError('unsupported admission request')
        principal, center = request['principal'], request['command_center']
        self._scope(principal, center)
        row = self._admission_row(request['generation'])
        if (row is None or row['event'] != event or row['principal'] != principal
                or row['center'] != center):
            raise ValueError('admission row does not match the request')
        return principal, center, row['machine']

    def _admit(self, request, received):
        """DA4 step 5: bind a published, labelled, logged center; idempotent."""
        principal, center, machine = self._admission_request(request, received, 'admit', 1)
        # The daemon asserted host uid 1001 on this descriptor (D85 trust);
        # the mapper sees 1001 only as overflow and checks the rest itself,
        # on a retry of a bound center too.
        attached = os.fstat(received[0])
        fd = os.open(self.data_root + '/' + center,
                     os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
        finally:
            os.close(fd)
        if ((info.st_dev, info.st_ino) != (attached.st_dev, attached.st_ino)
                or not stat.S_ISDIR(info.st_mode) or info.st_uid != self.overflow_uid
                or info.st_gid != machine - FIRST or stat.S_IMODE(info.st_mode) != 0o750):
            raise ValueError('center root does not carry the admitted label')
        if self.bindings.get((principal, center)) == machine:
            self.channel.sendall(b'{"op":"ADMITTED"}')
            return
        if (request['generation'] <= self.generation
                or any(bound == center for _, bound in self.bindings)
                or any(owner != principal and value == machine
                       for (owner, _), value in self.bindings.items())
                or self._center_state(center) != 'admitted'):
            raise ValueError('center is not admissible at this generation')
        self.bindings[(principal, center)] = machine
        self.channel.sendall(b'{"op":"ADMITTED"}')

    def _retire(self, request, received):
        """DA6: drop a binding only under its deletion fence with no running cell."""
        principal, center, machine = self._admission_request(request, received, 'retire', 0)
        bound = self.bindings.get((principal, center))
        if bound is not None:
            fence = self.delete_fences.get(bound)
            if (bound != machine or fence is None or fence[:2] != (principal, center)
                    or any(job[1] == bound for job in self.jobs.values())):
                raise ValueError('retire requires a quiescent deletion fence')
            del self.bindings[(principal, center)]
        self.channel.sendall(b'{"op":"RETIRED"}')

    def _finish_delete(self, request, received):
        if (received or set(request) != {'op', 'principal', 'command_center', 'delete_token'}
                or any(type(request[key]) is not str for key in
                       ('principal', 'command_center', 'delete_token'))):
            raise ValueError('invalid deletion finish')
        expected = (request['principal'], request['command_center'], request['delete_token'])
        # DA6 retires the binding before finish; the fence alone names the owner.
        machine = self.bindings.get(expected[:2]) or next(
            (key for key, fence in self.delete_fences.items() if fence == expected), None)
        if machine is None:
            raise ValueError('deletion finish does not match quiescent fence')
        if (self.delete_fences.get(machine) != expected
                or any(job[1] == machine for job in self.jobs.values())):
            raise ValueError('deletion finish does not match quiescent fence')
        del self.delete_fences[machine]
        self.channel.sendall(b'{"op":"DELETE_FINISHED"}')

    def _daemon_pipe(self, fd, direction):
        """One end of an anonymous pipe the daemon made, open only ``direction``.

        A pipe has no peer credentials; the authenticated request already
        proves the daemon sent it. What is checked is that it is an unnamed
        pipe (never a FIFO on a filesystem, a socket or a file), daemon-created
        and open only in the direction the cell's stdio slot needs.
        """
        import fcntl

        info = os.fstat(fd)
        if (not stat.S_ISFIFO(info.st_mode) or info.st_uid != self.overflow_uid
                or not os.readlink(f'/proc/self/fd/{fd}').startswith('pipe:[')
                or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != direction):
            raise ValueError('owner cell needs daemon pipes')

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

    def _drain_diagnostics(self, pid):
        state = self.diagnostics[pid]
        for _ in range(16):
            try:
                chunk = os.read(state[0], 4096)
            except BlockingIOError:
                break
            if not chunk:
                break
            data = state[2] + chunk
            matches = _diagnostics['PATTERN'].findall(data)
            for match in matches:
                reason = match.decode('ascii')
                if reason.startswith('relay:') and not reason.startswith('relay:bootstrap:'):
                    state[6] = reason  # One bounded notice; never a terminal verdict.
                else:
                    state[3] = reason
            if not matches and b'bwrap:' in data and state[3] is None:
                number = next((code for message, code in (
                    (b'Permission denied', 13), (b'Operation not permitted', 1),
                    (b'No such file or directory', 2), (b'Invalid argument', 22),
                    (b'No space left on device', 28)) if message in data), None)
                state[3] = f'bwrap:bootstrap-refused:errno={number}'
            state[2] = data[-256:]
            if state[1] is not None:
                room = 256 * 1024 - len(state[4])
                state[4].extend(chunk[:room])
                state[5] |= len(chunk) > room
                self._flush_stderr(state)
        self._flush_stderr(state)

    @staticmethod
    def _flush_stderr(state):
        if state[1] is not None and state[4]:
            try:
                sent = os.write(state[1], state[4])
                del state[4][:sent]
            except BlockingIOError:
                pass  # Retry on the next mapper iteration, retaining every byte.
            except BrokenPipeError:
                state[5] = True  # The daemon stopped reading; nothing to deliver to.

    def _finish_diagnostics(self, pid, code, stop_reason=None):
        self._drain_diagnostics(pid)
        fd, sink, _, reason, pending, truncated, relay = self.diagnostics.pop(pid)
        os.close(fd)
        if sink is not None:
            os.close(sink)
        if relay:
            os.write(2, ('owner cell relay notice: ' + relay + '\n').encode())
        reason = stop_reason or reason or (
            f'cell:signal={-code}' if code < 0 else f'cell:exit={code}')
        if truncated or pending:
            reason += ';stderr_truncated'
            os.write(2, b'owner cell stderr truncated: consumer did not drain bounded buffer\n')
        if code or reason.startswith(('decoder:', 'launcher:', 'bwrap:')):
            os.write(2, ('owner cell ended: ' + reason + '\n').encode())
        return reason

    def _service_jobs(self):
        for pid, (inner, machine, deadline, channel) in list(self.jobs.items()):
            self._drain_diagnostics(pid)
            waited, status = os.waitpid(pid, os.WNOHANG)
            cancel_sent = False
            reason = 'deadline' if time.monotonic() >= deadline else None
            if not waited and pid in self.package_jobs:
                try:
                    count, rss = package_usage(pid)
                    if count > 68:
                        reason = f'process_limit: count={count} limit=68'
                    else:
                        limit = (1536 if pid in self.browser_jobs else 512) * 1024 * 1024
                        if rss > limit:
                            reason = f'rss_limit: bytes={rss} limit={limit}'
                except (OSError, ValueError, IndexError, RuntimeError) as exc:
                    # No arbitrary exception text: it can contain owner data.
                    reason = (f'usage_unavailable: {type(exc).__name__} '
                              f'errno={getattr(exc, "errno", None)}')
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
                reason = reason or 'revoked'
            if not waited and reason:
                try:
                    os.kill(pid, signal.SIGKILL)
                    cancel_sent = True
                except PermissionError:
                    os.seteuid(inner)
                    try:
                        os.kill(pid, signal.SIGKILL)
                        cancel_sent = True
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
                    receipt['stop_reason'] = self._finish_diagnostics(
                        pid, receipt['returncode'], reason if cancel_sent else None)
                    channel.sendall(json.dumps(receipt).encode())
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    channel.close()
                    del self.jobs[pid]
                    self.package_jobs.discard(pid)
                    self.browser_jobs.discard(pid)
        assert_mapper(self.launch)
