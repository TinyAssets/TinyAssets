"""Daemon-only client of D68's inherited bounded owner launcher channel.

Constructed by startup, never from environment or a user-supplied socket path.
A protocol failure poisons this instance; no legacy subprocess fallback exists.
"""
from __future__ import annotations

import array
import json
import os
import select
import socket
import stat
import struct
import threading
import weakref
from types import SimpleNamespace

from tinyassets.broker.owner_identities import OWNER_ID_FIRST, OWNER_ID_LAST, OwnerIdentity

_live_cells = weakref.WeakSet()


def _close_cells_after_fork():
    for cell in list(_live_cells):
        cell._after_fork()


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_close_cells_after_fork)


class OwnerCell:
    """One mapper-owned process lifetime; caller owns its bidirectional stream."""

    def __init__(self, client, stream, status, identity, *, stderr=None):
        self.stream, self._status = stream, status
        self.stderr = stderr
        self._client, self._identity = client, identity
        self._pid = os.getpid()
        self._result = None
        self._closed = False
        _live_cells.add(self)

    def _after_fork(self):
        self.stream.close()
        if self.stderr is not None:
            self.stderr.close()
        self._status.close()
        self._closed = True

    def wait(self, timeout=5):
        if os.getpid() != self._pid or self._closed:
            raise RuntimeError('owner cell handle is unavailable')
        if self._result is not None:
            return self._result
        self._status.settimeout(timeout)
        answer = self._client._reply(channel=self._status)
        if (set(answer) != {'op', 'returncode', 'uid', 'gid'}
                or answer['op'] != 'SPAWN_DONE' or type(answer['returncode']) is not int
                or type(answer['uid']) is not int or type(answer['gid']) is not int
                or (answer['uid'], answer['gid']) != (self._identity.uid, self._identity.gid)):
            raise RuntimeError('invalid owner cell completion')
        self._result = answer['returncode']
        return self._result

    def cancel(self):
        self.revoke()
        return self.wait()

    def revoke(self):
        """Write-side EOF revokes without racing queued completion with unread data."""
        if os.getpid() != self._pid or self._closed:
            raise RuntimeError('owner cell handle is unavailable')
        if self._result is None:
            try:
                self._status.shutdown(socket.SHUT_WR)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The authenticated receipt still proves reaping.

    def close(self):
        if not self._closed:
            try:
                self.cancel()
            finally:
                self.stream.close()
                if self.stderr is not None:
                    self.stderr.close()
                self._status.close()
                self._closed = True
                _live_cells.discard(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class OwnerLaunchRefused(RuntimeError):
    """One completed authenticated exchange was refused; channel remains usable."""


class OwnerLauncherClient:
    def __init__(self, channel: socket.socket, launcher_pid: int):
        if (os.getuid() != 1001 or channel.family != socket.AF_UNIX
                or channel.type != socket.SOCK_SEQPACKET
                or channel.getsockname() or channel.getpeername()
                or type(launcher_pid) is not int or launcher_pid <= 0):
            raise PermissionError('invalid daemon launcher bootstrap')
        self._daemon_pid = os.getpid()
        self._launcher_pid = launcher_pid
        self._channel = channel
        self._pidfd = os.pidfd_open(launcher_pid)
        self._lock = threading.Lock()
        self._closed = False
        channel.set_inheritable(False)
        channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        # FD_CLOEXEC alone does not protect a fork child before exec.
        os.register_at_fork(after_in_child=self._after_fork)

    def _after_fork(self):
        self._closed = True
        self._lock = threading.Lock()
        self._channel.close()
        if self._pidfd >= 0:
            os.close(self._pidfd)
            self._pidfd = -1

    def _check(self):
        if (self._closed or os.getpid() != self._daemon_pid
                or select.select([self._pidfd], [], [], 0)[0]):
            raise RuntimeError('owner launcher is unavailable')

    def _reply(self, *, terminal=False, channel=None):
        # Terminal ack can be queued immediately before child exit. The daemon
        # must retain (not reap) its launcher child until stop returns.
        if not terminal:
            self._check()
        payload, ancillary, flags, _ = (self._channel if channel is None else channel).recvmsg(
            4096, socket.CMSG_SPACE(12) + socket.CMSG_SPACE(32), socket.MSG_CMSG_CLOEXEC)
        credentials, received, unknown = [], [], False
        try:
            for level, kind, data in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS:
                    credentials.append(struct.unpack('3i', data))
                elif level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    fds = array.array('i')
                    fds.frombytes(data[:len(data) - len(data) % fds.itemsize])
                    received.extend(fds)
                else:
                    unknown = True
            if (received or unknown or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
                    or credentials != [(self._launcher_pid, 300000, 300000)]):
                raise RuntimeError('unauthenticated owner launcher reply')
            if not terminal:
                self._check()
            reply = json.loads(payload)
            if not isinstance(reply, dict):
                raise RuntimeError('invalid owner launcher reply')
            return reply
        finally:
            for fd in received:
                os.close(fd)

    def _close(self):
        self._closed = True
        self._channel.close()
        if self._pidfd >= 0:
            os.close(self._pidfd)
            self._pidfd = -1

    def decode(self, data: bytes, mime: str, *, principal: str, command_center: str,
               identity: OwnerIdentity):
        """Scope was admitted by role_decoder; identity comes from broker IPC."""
        from tinyassets.tool_images import (
            DECODE_WALL_SECONDS,
            MAX_IMAGE_BYTES,
            MAX_IMAGE_SOURCE_BYTES,
        )

        return self._data_cell(data, principal=principal, command_center=command_center,
            identity=identity, kind='image-decoder', extra={'mime': mime}, profile='cell-deny',
            input_bound=MAX_IMAGE_SOURCE_BYTES, output_bound=MAX_IMAGE_BYTES + 16384,
            timeout=DECODE_WALL_SECONDS + 10)

    def start_cell(self, *, kind, principal, command_center, identity, extra=None,
                   directory_fd=None, socket_fds=()):
        """Start an admitted static class with independent data and lifetime pipes.

        No numeric identity or executable is sent to the mapper. The cell's
        status socket is a revocation handle: closing it kills only that cell.
        The existing per-kind mapper deadline still bounds its lifetime.
        """
        if (type(identity) is not OwnerIdentity or identity.uid != identity.gid
                or not OWNER_ID_FIRST <= identity.uid <= OWNER_ID_LAST):
            raise ValueError('invalid admitted cell identity')
        document = dict(extra or {})
        if set(document) - {'mime', 'ui_id', 'workspace', 'egress', 'ta',
                            'revision', 'delete_token'}:
            raise ValueError('unsupported cell parameters')
        if socket_fds and (kind not in ('tool-jail', 'package', 'provider-exec')
                           or len(socket_fds) > 2):
            raise ValueError('unsupported cell sockets')
        document.update(op='START', kind=kind, principal=principal, command_center=command_center)
        data, child = socket.socketpair()
        status, child_status = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        stderr, child_stderr = socket.socketpair() if kind == 'provider-exec' else (None, None)
        status.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        try:
            with self._lock:
                self._check()
                self._channel.settimeout(5)
                handles = [child.fileno()]
                if directory_fd is not None:
                    handles.append(directory_fd)
                handles.extend(socket_fds)
                if child_stderr is not None:
                    handles.append(child_stderr.fileno())
                handles.append(child_status.fileno())
                try:
                    self._channel.sendmsg([json.dumps(document).encode()], [(
                        socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', handles))])
                    reply = self._reply()
                    if reply == {'op': 'REFUSED'}:
                        raise OwnerLaunchRefused('owner launcher refused cell scope')
                    if (set(reply) != {'op', 'uid', 'gid'} or reply['op'] != 'STARTED'
                            or type(reply['uid']) is not int or type(reply['gid']) is not int
                            or reply['uid'] != reply['gid']
                            or not OWNER_ID_FIRST <= reply['uid'] <= OWNER_ID_LAST):
                        raise RuntimeError('invalid owner launcher start receipt')
                    if reply['uid'] != identity.uid:
                        actual = OwnerIdentity(reply['uid'], reply['gid'])
                        with OwnerCell(self, data, status, actual, stderr=stderr) as refused:
                            refused.cancel()  # Reap before returning a reusable refusal.
                        raise OwnerLaunchRefused('owner launcher refused cell identity')
                except OwnerLaunchRefused:
                    raise
                except BaseException:
                    self._close()
                    raise
            return OwnerCell(self, data, status, identity, stderr=stderr)
        except BaseException:
            data.close()
            status.close()
            if stderr is not None:
                stderr.close()
            raise
        finally:
            child.close()
            child_status.close()
            if child_stderr is not None:
                child_stderr.close()

    def preview(self, spec, wall_seconds, *, principal, command_center, identity):
        from tinyassets.ui_preview import MAX_CHILD_OUTPUT

        if type(wall_seconds) not in (int, float) or not 0 < wall_seconds <= 60:
            raise ValueError('invalid preview deadline')
        data = json.dumps(dict(spec=spec, wall_seconds=wall_seconds)).encode()
        return self._data_cell(data, principal=principal, command_center=command_center,
            identity=identity, kind='ui-preview', extra={}, profile='cell-nested',
            input_bound=MAX_CHILD_OUTPUT, output_bound=MAX_CHILD_OUTPUT + 16384, timeout=85)

    def _data_cell(self, data, *, principal, command_center, identity, kind, extra,
                   profile, input_bound, output_bound, timeout, directory_fd=None):
        if (not isinstance(data, bytes) or len(data) > input_bound
                or type(identity) is not OwnerIdentity or identity.uid != identity.gid
                or not OWNER_ID_FIRST <= identity.uid <= OWNER_ID_LAST):
            raise ValueError('invalid admitted decoder input')
        source = None
        if directory_fd is not None:
            info = os.fstat(directory_fd)
            if (not stat.S_ISDIR(info.st_mode) or info.st_gid != identity.gid
                    or info.st_uid not in (1001, identity.uid)):
                raise ValueError('invalid preview output root')
            source = [info.st_dev, info.st_ino]
        with self.start_cell(kind=kind, principal=principal, command_center=command_center,
                             identity=identity, extra=extra, directory_fd=directory_fd) as job:
            job.stream.settimeout(timeout)
            output = bytearray()
            data_failure = False
            try:
                job.stream.sendall(data)
                job.stream.shutdown(socket.SHUT_WR)
                while part := job.stream.recv(65536):
                    output.extend(part)
                    if len(output) > output_bound:
                        raise RuntimeError('decoder output exceeds its bound')
            except (BrokenPipeError, ConnectionResetError):
                data_failure = True
            code = job.wait(timeout)
            if data_failure or (not output and code != 0):
                raise OwnerLaunchRefused('decoder ended before returning its cell proof')
            header, _, payload = bytes(output).partition(b'\n')
            cell = json.loads(header)['cell']
            inner = identity.uid - 300000
            if (cell.get('uid') != inner or cell.get('gid') != inner
                    or cell.get('fds') != [0, 1, 2] or cell.get('groups') != []
                    or cell.get('caps') != 'zero' or cell.get('nnp') != 1
                    or cell.get('profile') != profile
                    or (source is not None and cell.get('source') != source)):
                raise RuntimeError('dedicated decoder cell proof is absent')
            return SimpleNamespace(returncode=code, stdout=payload, cell=cell)

    def write_preview(self, data, ui_id, *, directory_fd, principal, command_center, identity):
        from tinyassets.ui_preview import MAX_CHILD_OUTPUT

        return self._data_cell(data, principal=principal, command_center=command_center,
            identity=identity, kind='preview-write', extra={'ui_id': ui_id}, profile='cell-deny',
            input_bound=MAX_CHILD_OUTPUT, output_bound=16384, timeout=45,
            directory_fd=directory_fd)

    def finish_delete(self, *, principal, command_center, token):
        """Release an exact authenticated two-pass fence, never implicitly."""
        with self._lock:
            self._check()
            self._channel.settimeout(5)
            try:
                self._channel.sendall(json.dumps(dict(op='DELETE_DONE', principal=principal,
                    command_center=command_center, delete_token=token)).encode())
                answer = self._reply()
                if answer == {'op': 'REFUSED'}:
                    raise OwnerLaunchRefused('owner deletion finish refused')
                if answer != {'op': 'DELETE_FINISHED'}:
                    raise RuntimeError('invalid owner deletion finish receipt')
            except OwnerLaunchRefused:
                raise
            except BaseException:
                self._close()
                raise

    def _admission(self, document, handles, receipt):
        with self._lock:
            self._check()
            self._channel.settimeout(10)
            try:
                ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                              array.array('i', handles))] if handles else []
                self._channel.sendmsg([json.dumps(document).encode()], ancillary)
                answer = self._reply()
                if answer == {'op': 'REFUSED'}:
                    raise OwnerLaunchRefused('owner launcher refused the admission')
                if answer != {'op': receipt}:
                    raise RuntimeError('invalid owner admission receipt')
            except OwnerLaunchRefused:
                raise
            except BaseException:
                self._close()
                raise

    def admit(self, *, principal, command_center, generation, root_fd):
        """DA4 step 5: the mapper fetches the log row itself; no number is sent.

        ``root_fd`` is an O_PATH descriptor of the published root the caller
        has just checked is host uid 1001 with the canonical label.
        """
        if type(generation) is not int or generation < 1:
            raise ValueError('invalid admission generation')
        self._admission(dict(op='ADMIT', principal=principal, command_center=command_center,
                             generation=generation), [root_fd], 'ADMITTED')

    def retire(self, *, principal, command_center, generation):
        """DA6: drop the binding under its deletion fence; unbound is a verified no-op."""
        if type(generation) is not int or generation < 1:
            raise ValueError('invalid admission generation')
        self._admission(dict(op='RETIRE', principal=principal, command_center=command_center,
                             generation=generation), [], 'RETIRED')

    def stop(self):
        with self._lock:
            self._check()
            self._channel.settimeout(5)
            try:
                self._channel.sendall(b'{"op":"STOP"}')
                answer = self._reply(terminal=True)
                if answer == {'op': 'REFUSED'}:
                    raise OwnerLaunchRefused('owner cells are still running')
                if answer != {'op': 'STOPPED'}:
                    raise RuntimeError('owner launcher did not stop')
            except OwnerLaunchRefused:
                raise
            except BaseException:
                self._close()
                raise
            self._close()

    def git(self, argv, *, options, timeout_s, directory_fd, principal, command_center,
            identity: OwnerIdentity):
        """Execute the actual git runner in one pinned, admitted owner directory."""
        info = os.fstat(directory_fd)
        if (type(identity) is not OwnerIdentity or identity.uid != identity.gid
                or not OWNER_ID_FIRST <= identity.uid <= OWNER_ID_LAST
                or not stat.S_ISDIR(info.st_mode)
                or (info.st_uid, info.st_gid) != (identity.uid, identity.gid)
                or type(timeout_s) not in (int, float) or not 0 < timeout_s <= 60):
            raise ValueError('invalid admitted git directory or deadline')
        data = json.dumps(dict(argv=list(argv), options=list(options),
                               timeout_s=timeout_s)).encode()
        if len(data) > 65536:
            raise ValueError('git request exceeds its bound')
        done = self._data_cell(data, principal=principal, command_center=command_center,
            identity=identity, kind='workspace-git', extra={}, profile='cell-links',
            input_bound=65536, output_bound=1024 * 1024, timeout=75, directory_fd=directory_fd)
        if done.returncode != 0:
            raise OwnerLaunchRefused('git cell did not complete')
        result = json.loads(done.stdout)
        if (set(result) != {'returncode', 'stdout', 'stderr'}
                or type(result['returncode']) is not int
                or not isinstance(result['stdout'], str)
                or not isinstance(result['stderr'], str)):
            raise RuntimeError('invalid git result')
        return SimpleNamespace(**result, cell=done.cell)
