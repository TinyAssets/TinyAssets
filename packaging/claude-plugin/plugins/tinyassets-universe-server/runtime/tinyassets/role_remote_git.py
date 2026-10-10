"""Admission and descriptor handoff for the workspace-remote owner cell.

The daemon's whole part in a workspace git operation: admit the owner scope,
prepare (and label) the pool directory the lease is created in, pin the command
center's egress relay for that owner, start the cell, hand it ONE bounded
request and read ONE bounded answer.

No credential and no host path crosses this boundary. The route the cell uses
is already open on the daemon's checking proxy (``git_egress``), and the lease
is named relative to the command center the launcher mounted.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

#: Both directions are a single JSON line; the cell asserts the same bound.
MAX_FRAME_BYTES = 16384
_PROOF_BYTES = 16384


def _frame(value) -> bytes:
    raw = json.dumps(value, separators=(',', ':')).encode() + b'\n'
    if len(raw) > MAX_FRAME_BYTES:
        raise ValueError('workspace cell message exceeds its bound')
    return raw


def _read(reader, bound):
    raw = reader.readline(bound + 1)
    if len(raw) > bound or not raw.endswith(b'\n'):
        raise RuntimeError('invalid workspace cell frame')
    return json.loads(raw)


def _scope(universe_dir, principal):
    """The admitted owner identity for ``principal`` in this command center.

    ``principal`` is explicit rather than read from the request context: the
    startup push reconciler acts for a finished run's owner and has no request
    identity at all (the same reason ``role_owner_delete.remove_subtree`` takes
    one).
    """
    from tinyassets import role_decoder
    from tinyassets.broker import supervisor
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.daemon_server import get_founder_home, universe_access_permission
    from tinyassets.storage import data_dir

    client = role_decoder._bounded_client
    if client is None:
        raise RuntimeError('workspace git requires its bounded owner launcher')
    supervisor._protect_daemon()
    root, center = data_dir().resolve(), Path(universe_dir)
    if not principal or center.parent != root or center.resolve() != center:
        raise PermissionError('workspace git scope is not an admitted command center')
    if not (get_founder_home(root, principal) == center.name or universe_access_permission(
            root, universe_id=center.name, actor_id=principal) == 'admin'):
        raise PermissionError('workspace git owner scope is not admitted')
    return client, center, owner_identity(root, principal=principal)


def run(request, *, universe_dir, principal, egress_socket):
    """Run one workspace operation in this owner's cell; return its answer.

    ``egress_socket`` is the command center's own checking proxy socket, or
    ``None`` for an operation that reaches no remote (making an empty
    workspace), which then gets no socket bound at all.
    """
    from tinyassets import workspace_fs, workspace_owner_pool
    from tinyassets.role_relays import pin_for_owner

    client, center, identity = _scope(universe_dir, principal)
    parts = tuple(request.get('lease_parent') or ())
    if parts:
        workspace_owner_pool.prepare(center, parts, machine=identity.gid)
    payload = _frame(request)
    fd = workspace_fs.open_dir_nofollow(center)
    socket_fds = []
    try:
        info = os.fstat(fd)
        if (not stat.S_ISDIR(info.st_mode) or info.st_gid != identity.gid
                or info.st_uid not in (1001, identity.uid)):
            raise PermissionError('workspace center does not match the owner identity')
        relay = None
        if egress_socket is not None:
            descriptor = pin_for_owner(Path(egress_socket), center, identity.uid, kind='egress')
            socket_fds.append(descriptor)
            relay_info = os.fstat(descriptor)
            relay = [relay_info.st_dev, relay_info.st_ino]
        return _exchange(client, payload, principal=principal, center=center,
                         identity=identity, fd=fd, info=info, relay=relay,
                         socket_fds=socket_fds)
    finally:
        for descriptor in socket_fds:
            os.close(descriptor)
        os.close(fd)


def _exchange(client, payload, *, principal, center, identity, fd, info, relay, socket_fds):
    with client.start_cell(kind='workspace-remote', principal=principal,
                           command_center=center.name, identity=identity,
                           extra={'egress': relay is not None}, directory_fd=fd,
                           socket_fds=socket_fds) as cell:
        cell.stream.settimeout(60)
        with cell.stream.makefile('rb') as reader:
            proof = _read(reader, _PROOF_BYTES)['cell']
            inner = identity.uid - 300000
            if (proof.get('uid') != inner or proof.get('gid') != inner
                    or proof.get('source') != [info.st_dev, info.st_ino]
                    or proof.get('relay') != relay
                    or proof.get('fds') != [0, 1, 2] or proof.get('groups') != []
                    or proof.get('caps') != 'zero' or proof.get('nnp') != 1
                    or proof.get('profile') != 'cell-links'):
                raise RuntimeError('workspace cell proof is absent')
            # The mapper's own deadline bounds the cell; this is the read that
            # must outlast a large clone, not a second policy.
            cell.stream.settimeout(1800)
            cell.stream.sendall(payload)
            answer = _read(reader, MAX_FRAME_BYTES)
            if cell.wait(30) != 0:
                raise RuntimeError('workspace cell did not complete')
            return answer
