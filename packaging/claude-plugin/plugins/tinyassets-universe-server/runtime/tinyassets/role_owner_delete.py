"""Fixed pass-one deletion in an authenticated, quiescent owner cell."""
from __future__ import annotations

import array
import json
import os
import re
import socket
from pathlib import Path


def _scope(universe_dir, token, *, finishing=False):
    from tinyassets import role_decoder
    from tinyassets.auth.middleware import current_identity
    from tinyassets.broker import supervisor
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.daemon_server import get_founder_home, universe_access_permission
    from tinyassets.storage import data_dir

    if type(token) is not str or not re.fullmatch('[a-f0-9]{32}', token):
        raise ValueError('invalid owner deletion token')
    client = role_decoder._bounded_client
    if client is None or not supervisor.broker_selected():
        raise PermissionError('owner deletion requires its bounded launcher and broker')
    supervisor._protect_daemon()
    root, center = data_dir().resolve(), Path(universe_dir)
    principal = current_identity().user_id
    if (center.parent != root or center.resolve() != center or not re.fullmatch(
            '[A-Za-z0-9_-]+', center.name) or (not finishing and not (
                get_founder_home(root, principal) == center.name or universe_access_permission(
                    root, universe_id=center.name, actor_id=principal) == 'admin'))):
        raise PermissionError('owner deletion scope is not admitted')
    # Finish can run after daemon removal of the center/owner metadata. The
    # mapper still requires the exact admitted principal, center and live token.
    identity = None if finishing else owner_identity(root, principal=principal)
    return client, principal, center, identity


def begin(universe_dir, *, token):
    """Run pass one; caller persists token and owns pass two before finish()."""
    from tinyassets import workspace_fs

    client, principal, center, identity = _scope(universe_dir, token)
    fd = workspace_fs.open_dir_nofollow(center)
    try:
        with client.start_cell(principal=principal, command_center=center.name,
                identity=identity, kind='owner-delete', extra={'delete_token': token},
                directory_fd=fd) as cell:
            from tinyassets.role_packages import _proof

            cell.stream.settimeout(40)
            proof = _proof(cell.stream)
            info = os.fstat(fd)
            inner = identity.uid - 300000
            if (proof.get('uid') != inner or proof.get('gid') != inner
                    or proof.get('source') != [info.st_dev, info.st_ino]
                    or proof.get('fds') != [0, 1, 2] or proof.get('groups') != []
                    or proof.get('caps') != 'zero' or proof.get('nnp') != 1
                    or proof.get('profile') != 'cell-deny'):
                raise RuntimeError('owner deletion proof is absent')
            cell.stream.sendall(b'{}\n')
            while True:
                marker, ancillary, flags, _ = cell.stream.recvmsg(
                    1, socket.CMSG_SPACE(32), socket.MSG_CMSG_CLOEXEC)
                received = []
                try:
                    for level, kind, payload in ancillary:
                        if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS:
                            raise RuntimeError('invalid owner deletion classifier')
                        handles = array.array('i')
                        handles.frombytes(payload[:len(payload) - len(payload) % handles.itemsize])
                        received.extend(handles)
                    if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                        raise RuntimeError('truncated owner deletion classifier')
                    if marker == b'?' and len(received) == 1:
                        # Metadata only: no read, chmod, path resolution or
                        # descriptor returns to the cell. Kernel host uid is
                        # authoritative despite nested namespace overflow.
                        cell.stream.sendall(b'1' if os.fstat(received[0]).st_uid == 1001 else b'0')
                        continue
                    if marker != b'!' or received:
                        raise RuntimeError('invalid owner deletion result')
                    break
                finally:
                    for descriptor in received:
                        os.close(descriptor)
            raw = bytearray()
            while len(raw) <= 4096:
                byte = cell.stream.recv(1)
                if byte == b'\n':
                    break
                if not byte:
                    raise RuntimeError('incomplete owner deletion receipt')
                raw.extend(byte)
            else:
                raise RuntimeError('oversized owner deletion receipt')
            answer = json.loads(raw)
            if cell.wait(5) != 0 or 'error' in answer:
                raise RuntimeError('owner deletion incomplete: '
                                   + str(answer.get('error', 'cell exit')))
            return answer
    finally:
        os.close(fd)


def finish(universe_dir, *, token):
    """Explicitly release only after caller has verified daemon pass completion."""
    client, principal, center, _ = _scope(universe_dir, token, finishing=True)
    client.finish_delete(principal=principal, command_center=center.name, token=token)


def abort(universe_dir, *, token):
    """Explicit caller recovery: release a quiescent failed deletion, no rollback.

    U2 must record/report partial deletion and cancel its pending daemon pass
    before calling this. Failure never calls it automatically.
    """
    client, principal, center, _ = _scope(universe_dir, token, finishing=True)
    client.finish_delete(principal=principal, command_center=center.name, token=token)
