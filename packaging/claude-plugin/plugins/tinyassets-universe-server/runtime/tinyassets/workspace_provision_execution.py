"""Admit a pinned lease to its owner's bounded provisioning cell.

Consent and reservations remain the workspace effector's responsibility.
The daemon holds no package-manager process, only the fixed registry relay.
"""
from __future__ import annotations

import json
import math
import os
import select
import stat
import time
from dataclasses import dataclass

from tinyassets import workspace_fs as fs
from tinyassets.node_sandbox import MAX_WORKSPACE_TIMEOUT_SECONDS
from tinyassets.workspace_provision_cell import FRAME_BOUND
from tinyassets.workspace_resolver import ProvisionManifests


@dataclass(frozen=True)
class ProvisionResult:
    failure: str | None
    bytes_to_charge: int


def execute_provision(manifests, *, lease_fd, repo_fd, max_transfer_bytes,
                      storage_bound, timeout_s, cancelled, universe_dir, principal):
    from tinyassets.role_relays import pin_for_owner
    from tinyassets.role_remote_git import _scope
    from tinyassets.workspace_registry_relay import RegistryRelay

    if type(manifests) is not ProvisionManifests or not (manifests.python or manifests.node):
        raise ValueError('provisioning requires admitted manifests')
    if any(type(v) is not int or v <= 0 for v in (max_transfer_bytes, storage_bound)):
        raise ValueError('provisioning requires positive resource bounds')
    if (type(timeout_s) not in (int, float) or not math.isfinite(timeout_s)
            or not 0 < timeout_s <= MAX_WORKSPACE_TIMEOUT_SECONDS or not callable(cancelled)):
        raise ValueError('invalid provisioning deadline or cancellation')
    client, center, identity = _scope(universe_dir, principal)
    lease, repo = os.fstat(lease_fd), os.fstat(repo_fd)
    for info in (lease, repo):
        if (not stat.S_ISDIR(info.st_mode)
                or (info.st_uid, info.st_gid) != (identity.uid, identity.gid)):
            raise PermissionError('provisioning lease is not owned by the admitted owner')
    held = fs.open_subdir_nofollow(lease_fd, 'repo')
    try:
        if not os.path.samestat(repo, os.fstat(held)):
            raise PermissionError('provisioning checkout differs from the pinned lease')
    finally:
        os.close(held)
    request = dict(python=manifests.python.normalized_text if manifests.python else None,
                   node=[manifests.node.normalized_package_json, manifests.node.normalized_lockfile]
                   if manifests.node else None, storage_bound=storage_bound, timeout_s=timeout_s)
    payload = json.dumps(request).encode() + b'\n'
    if len(payload) > FRAME_BOUND:
        raise ValueError('provisioning manifests exceed the cell frame bound')
    if cancelled():
        return ProvisionResult('cancelled', 0)
    relay = RegistryRelay(center, max_bytes=max_transfer_bytes, timeout_s=timeout_s)
    descriptor = None
    charge = max_transfer_bytes
    try:
        descriptor = pin_for_owner(relay.path, center, identity.uid, kind='registry')
        relay_info = os.fstat(descriptor)
        with client.start_cell(kind='workspace-provision', principal=principal,
                command_center=center.name, identity=identity, directory_fd=lease_fd,
                socket_fds=(descriptor,)) as cell:
            cell.stream.setblocking(False)
            deadline = time.monotonic() + timeout_s
            pending = bytearray()

            def read():
                while b'\n' not in pending:
                    if cancelled():
                        raise InterruptedError('cancelled')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('timeout')
                    if not select.select([cell.stream], [], [], 0.05)[0]:
                        continue
                    data = cell.stream.recv(16384)
                    if not data:
                        raise RuntimeError('provisioning cell ended without a receipt')
                    pending.extend(data)
                    if len(pending) > 16384:
                        raise RuntimeError('provisioning receipt exceeds its bound')
                line, _, rest = pending.partition(b'\n')
                pending[:] = rest
                return json.loads(line)

            def send(data):
                remaining = memoryview(data)
                while remaining:
                    if cancelled():
                        raise InterruptedError('cancelled')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('timeout')
                    if select.select([], [cell.stream], [], 0.05)[1]:
                        remaining = remaining[cell.stream.send(remaining[:65536]):]

            proof = read()['cell']
            inner = identity.uid - 300000
            if (proof.get('uid') != inner or proof.get('gid') != inner
                    or proof.get('source') != [lease.st_dev, lease.st_ino]
                    or proof.get('relay') != [relay_info.st_dev, relay_info.st_ino]
                    or proof.get('fds') != [0, 1, 2] or proof.get('groups') != []
                    or proof.get('caps') != 'zero' or proof.get('nnp') != 1
                    or proof.get('profile') != 'cell-nested'):
                raise RuntimeError('provisioning cell proof is absent')
            send(payload)
            answer = read()
            if answer == {'acquired': True}:
                relay.close()
                if relay.failure:
                    cell.cancel()
                    return ProvisionResult('registry_failed', max_transfer_bytes)
                charge = relay.charge
                send(b'{"install":true}\n')
                answer = read()
            if (set(answer) != {'failure'} or answer['failure'] is not None
                    and (type(answer['failure']) is not str or len(answer['failure']) > 80)):
                raise RuntimeError('invalid provisioning terminal receipt')
            if cell.wait(30) != 0:
                raise RuntimeError('provisioning cell did not terminate')
            return ProvisionResult(answer['failure'], charge)
    except (InterruptedError, TimeoutError) as exc:
        return ProvisionResult(str(exc), max_transfer_bytes)
    finally:
        relay.close()
        if descriptor is not None:
            os.close(descriptor)
