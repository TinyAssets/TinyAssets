"""Provider-neutral package admission and raw stdio, with broker-only credentials.

A revision whose manifest opts in also reaches the center's checked egress
relay, pinned by descriptor; it carries no credential of its own.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
from pathlib import Path

from tinyassets.role_package_manifest import MANIFEST_BYTES, PACKAGE_BYTES, parse


def _slot_dispatch(capabilities, slots):
    """A package cannot expand the canonical daemon capability selection."""
    names = capabilities.connections()
    if any(name not in names for name in slots.values()):
        raise PermissionError('package connection slot is unavailable')
    # Revalidate these exact grant/view/verb tuples on every request. The
    # canonical handler and broker still enforce live consent and authorization.
    pinned = {label: names[name] for label, name in slots.items()}

    def dispatch(message):
        if (type(message) is not dict or set(message) != {'slot', 'request'}
                or type(message['slot']) is not str or message['slot'] not in slots
                or type(message['request']) is not dict):
            return {'error': 'undeclared package connection slot'}
        label = message['slot']
        name = slots[label]
        if capabilities.connections().get(name) != pinned[label]:
            return {'error': 'package connection authority changed'}
        return asyncio.run(capabilities.dispatch(
            {'op': 'call', 'name': name, 'arguments': {'request': message['request']}}))

    return dispatch


def _proof(channel):
    # Do not buffer: bytes after this newline belong to package stdout.
    raw = bytearray()
    while len(raw) <= 16384:
        byte = channel.recv(1)
        if not byte:
            raise RuntimeError('package ended before its cell proof')
        if byte == b'\n':
            return json.loads(raw)['cell']
        raw.extend(byte)
    raise RuntimeError('package cell proof exceeds its bound')


@contextlib.contextmanager
def start(universe_dir, revision, *, slots=None, capabilities=None, egress=False):
    """Yield an authenticated OwnerCell with raw duplex stdio for this revision.

    The consumer supplies an already-provisioned daemon-sealed tree and its
    canonical Capabilities instance. No install, secret deposit or unconfined
    executable fallback occurs at this execution boundary. ``egress`` must equal
    the pinned manifest's opt-in. Lifetime is the caller's: the cell runs until
    it exits, the consumer revokes it or a resource guard ends it.
    """
    from tinyassets import role_decoder, role_relays, universe_egress, workspace_fs
    from tinyassets.auth.middleware import current_identity
    from tinyassets.broker import supervisor
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.daemon_server import get_founder_home, universe_access_permission
    from tinyassets.storage import data_dir
    from tinyassets.ta_capabilities import Capabilities, JailBridge

    if type(revision) is not str or not re.fullmatch('[a-f0-9]{64}', revision):
        raise ValueError('invalid package revision')
    client = role_decoder._bounded_client
    if client is None or not supervisor.broker_selected():
        raise PermissionError('package requires its bounded owner launcher and broker')
    supervisor._protect_daemon()
    root, center = data_dir().resolve(), Path(universe_dir)
    principal = current_identity().user_id
    if (center.parent != root or center.resolve() != center
            or not (get_founder_home(root, principal) == center.name or universe_access_permission(
                root, universe_id=center.name, actor_id=principal) == 'admin')):
        raise PermissionError('package owner scope is not admitted')
    identity = owner_identity(root, principal=principal)
    source = center / '.runtime' / 'package-cells' / revision
    descriptor = workspace_fs.open_dir_nofollow(source)
    with contextlib.ExitStack() as lifetime:
        lifetime.callback(os.close, descriptor)
        info = os.fstat(descriptor)
        if info.st_uid != 1001 or info.st_mode & 0o022:
            raise PermissionError('package is not sealed daemon content')
        raw = workspace_fs.read_package_manifest(
            descriptor, owner_uid=1001, max_bytes=MANIFEST_BYTES)
        doc = parse(raw, revision)
        workspace_fs.verify_package_tree(descriptor, doc['files'], owner_uid=1001,
                                         max_bytes=PACKAGE_BYTES)
        slots = dict(slots or {})
        if (set(slots) != set(doc['slots'])
                or any(type(value) is not str or not value.startswith('connection:')
                       for value in slots.values())):
            raise PermissionError('package slots do not match the pinned manifest')
        if type(egress) is not bool or egress is not doc['egress']:
            raise PermissionError('package egress does not match the pinned manifest')
        socket_fds, socket_sources = [], {}
        if egress:
            # Fixed slot order: egress before ta, as the mapper assigns them.
            relay = universe_egress.ensure_proxy(center)
            if relay is None:
                raise PermissionError('package egress requires the pinned relay')
            fd = role_relays.pin_for_owner(relay, center, identity.uid, kind='egress')
            lifetime.callback(os.close, fd)
            socket_fds.append(fd)
            si = os.fstat(fd)
            socket_sources['e'] = [si.st_dev, si.st_ino]
        if slots:
            if (type(capabilities) is not Capabilities or capabilities.root != center
                    or capabilities.context.owner != principal
                    or capabilities.context.universe != center.name):
                raise PermissionError('package capabilities do not match admitted owner')
            bridge = lifetime.enter_context(JailBridge(
                _slot_dispatch(capabilities, slots), universe_dir=center))
            fd = role_relays.pin_for_owner(bridge.path, center, identity.uid, kind='ta')
            lifetime.callback(os.close, fd)
            socket_fds.append(fd)
            si = os.fstat(fd)
            socket_sources['t'] = [si.st_dev, si.st_ino]
        cell = lifetime.enter_context(client.start_cell(kind='package', principal=principal,
            command_center=center.name, identity=identity, directory_fd=descriptor,
            socket_fds=socket_fds,
            extra={'revision': revision, 'ta': bool(slots), 'egress': egress}))
        cell.stream.settimeout(35)
        proof = _proof(cell.stream)
        inner = identity.uid - 300000
        if (proof.get('uid') != inner or proof.get('gid') != inner
                or proof.get('source') != [info.st_dev, info.st_ino]
                or proof.get('revision') != revision or proof.get('sockets') != socket_sources
                or proof.get('fds') != [0, 1, 2] or proof.get('groups') != []
                or proof.get('caps') != 'zero' or proof.get('nnp') != 1
                or proof.get('profile') != 'cell-links'):
            raise RuntimeError('package cell proof is absent')
        cell.stream.sendall(b'{"start":true}\n')
        cell.stream.settimeout(None)
        yield cell
