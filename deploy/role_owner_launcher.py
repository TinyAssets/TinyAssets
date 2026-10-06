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

    def _alive(self):
        return not select.select([self.daemon_pidfd], [], [], 0)[0]

    def serve_one(self):
        if not self._alive():
            raise RuntimeError('daemon exited')
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
        if (not isinstance(request, dict)
                or set(request) != {'op', 'kind', 'principal', 'command_center', 'mime'}
                or request['op'] != 'SPAWN' or request['kind'] != 'image-decoder'
                or not isinstance(request['mime'], str)
                or request['mime'] not in {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}
                or not isinstance(request['principal'], str)
                or not isinstance(request['command_center'], str) or len(received) != 1):
            raise ValueError('unsupported owner engine')
        machine = self.bindings[(request['principal'], request['command_center'])]
        inner = machine - FIRST
        fd = received[0]
        if not stat.S_ISSOCK(os.fstat(fd).st_mode):
            raise ValueError('decoder needs daemon socketpair')
        with socket.socket(fileno=os.dup(fd)) as endpoint:
            if (endpoint.family != socket.AF_UNIX or endpoint.type != socket.SOCK_STREAM
                    or endpoint.getsockname() or endpoint.getpeername()
                    or struct.unpack('3i', endpoint.getsockopt(socket.SOL_SOCKET,
                        socket.SO_PEERCRED, 12)) !=
                    (self.daemon_pid, self.overflow_uid, self.overflow_gid)):
                raise ValueError('decoder endpoint is not daemon-owned')
        pid = os.fork()
        if pid == 0:
            try:
                os.dup2(fd, 0)
                os.dup2(fd, 1)
                null = os.open('/dev/null', os.O_WRONLY)
                os.dup2(null, 2)
                self.launch['close_descriptors']()
                os.setgroups([])
                os.setresgid(inner, inner, inner)
                os.setresuid(inner, inner, inner)
                self.launch['_capset'](0)
                self.launch['_assert_caps'](0)
                os.umask(0o007)
                os.chdir('/')
                os.execve('/opt/venv/bin/python', ['/opt/venv/bin/python', '-I', '-B',
                    '/usr/local/libexec/ta-decoder.py', 'enter-owner', request['mime'],
                    self.data_root, str(inner)],
                    {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': '/tmp',
                     'PYTHONDONTWRITEBYTECODE': '1'})
            except BaseException:
                os._exit(126)
        deadline = time.monotonic() + 35
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
