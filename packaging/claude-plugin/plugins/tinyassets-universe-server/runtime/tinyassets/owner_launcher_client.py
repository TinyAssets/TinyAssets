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
import struct
import threading
from types import SimpleNamespace

from tinyassets.broker.owner_identities import OWNER_ID_FIRST, OWNER_ID_LAST, OwnerIdentity


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

    def _reply(self, *, terminal=False):
        # Terminal ack can be queued immediately before child exit. The daemon
        # must retain (not reap) its launcher child until stop returns.
        if not terminal:
            self._check()
        payload, ancillary, flags, _ = self._channel.recvmsg(
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

        if (not isinstance(data, bytes) or len(data) > MAX_IMAGE_SOURCE_BYTES
                or type(identity) is not OwnerIdentity or identity.uid != identity.gid
                or not OWNER_ID_FIRST <= identity.uid <= OWNER_ID_LAST):
            raise ValueError('invalid admitted decoder input')
        document = dict(op='SPAWN', kind='image-decoder', principal=principal,
                        command_center=command_center, mime=mime)
        with self._lock:
            self._check()
            self._channel.settimeout(DECODE_WALL_SECONDS + 10)
            try:
                parent, child = socket.socketpair()
                with parent, child:
                    parent.settimeout(DECODE_WALL_SECONDS + 10)
                    self._channel.sendmsg([json.dumps(document).encode()], [(
                        socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [child.fileno()]))])
                    child.close()
                    output = bytearray()
                    data_failure = False
                    try:
                        parent.sendall(data)
                        parent.shutdown(socket.SHUT_WR)
                        while part := parent.recv(65536):
                            output.extend(part)
                            if len(output) > MAX_IMAGE_BYTES + 16384:
                                raise RuntimeError('decoder output exceeds its bound')
                    except (BrokenPipeError, ConnectionResetError):
                        # Refusal closes the received input endpoint without
                        # consuming its bytes. Still authenticate/drain control.
                        data_failure = True
                answer = self._reply()
                if answer == {'op': 'REFUSED'}:
                    raise OwnerLaunchRefused('owner launcher refused decoder scope')
                if (set(answer) != {'op', 'returncode', 'uid', 'gid'}
                        or answer['op'] != 'SPAWN_DONE' or type(answer['returncode']) is not int
                        or type(answer['uid']) is not int or type(answer['gid']) is not int):
                    raise RuntimeError('invalid owner launcher completion')
                if (answer['uid'], answer['gid']) != (identity.uid, identity.gid):
                    raise OwnerLaunchRefused('owner launcher refused decoder identity')
                if data_failure or (not output and answer['returncode'] != 0):
                    raise OwnerLaunchRefused('decoder ended before returning its cell proof')
                header, _, payload = bytes(output).partition(b'\n')
                cell = json.loads(header)['cell']
                inner = identity.uid - 300000
                if (cell.get('uid') != inner or cell.get('gid') != inner
                        or cell.get('fds') != [0, 1, 2] or cell.get('groups') != []
                        or cell.get('caps') != 'zero' or cell.get('nnp') != 1
                        or cell.get('profile') != 'cell-deny'):
                    raise RuntimeError('dedicated decoder cell proof is absent')
                return SimpleNamespace(returncode=answer['returncode'], stdout=payload, cell=cell)
            except OwnerLaunchRefused:
                raise
            except BaseException:
                self._close()
                raise

    def stop(self):
        with self._lock:
            self._check()
            self._channel.settimeout(5)
            try:
                self._channel.sendall(b'{"op":"STOP"}')
                if self._reply(terminal=True) != {'op': 'STOPPED'}:
                    raise RuntimeError('owner launcher did not stop')
            finally:
                self._close()
