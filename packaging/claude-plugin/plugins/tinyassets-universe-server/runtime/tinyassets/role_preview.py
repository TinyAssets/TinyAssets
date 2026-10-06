"""Admit preview data in the daemon; render without any owner-store mount."""
from __future__ import annotations

import base64
import os
from pathlib import Path


def render(spec, wall_seconds):
    from tinyassets import role_decoder
    from tinyassets.auth.middleware import current_identity
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.custom_agents import read_app_ui_asset
    from tinyassets.daemon_server import get_founder_home, universe_access_permission
    from tinyassets.storage import data_dir
    from tinyassets.ui_preview import MAX_CHILD_OUTPUT

    root = data_dir().resolve()
    principal = current_identity().user_id
    center = spec['universe_id']
    if (Path(spec['base_path']) != root or spec['owner_user_id'] != principal
            or not (get_founder_home(root, principal) == center or universe_access_permission(
                root, universe_id=center, actor_id=principal) == 'admin')):
        raise PermissionError('preview owner scope is not admitted')
    client = role_decoder._bounded_client
    if client is None:
        raise RuntimeError('bounded preview launcher is unavailable')
    identity = owner_identity(root, principal=principal)
    assets = {}
    total = 0
    for path, sha in spec['hashes'].items():
        data = read_app_ui_asset(root, owner_user_id=principal, sha256=sha)
        if data is not None:
            total += len(data)
            if total > MAX_CHILD_OUTPUT // 2:
                raise ValueError('preview assets exceed their bound')
            assets[path] = base64.b64encode(data).decode('ascii')
    prepared = dict(spec, base_path='/absent', asset_bytes=assets)
    return client.preview(prepared, wall_seconds, principal=principal,
                          command_center=center, identity=identity)


def write(universe_dir, ui_id, data):
    from tinyassets import role_decoder, workspace_fs
    from tinyassets.auth.middleware import current_identity
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.daemon_server import get_founder_home, universe_access_permission
    from tinyassets.storage import data_dir

    root = data_dir().resolve()
    directory = Path(universe_dir)
    principal = current_identity().user_id
    center = directory.name
    if (directory.parent != root or not (get_founder_home(root, principal) == center
            or universe_access_permission(root, universe_id=center,
                                          actor_id=principal) == 'admin')):
        raise PermissionError('preview output scope is not admitted')
    client = role_decoder._bounded_client
    if client is None:
        raise RuntimeError('bounded preview launcher is unavailable')
    identity = owner_identity(root, principal=principal)
    fd = workspace_fs.open_dir_nofollow(directory)
    try:
        result = client.write_preview(data, ui_id, directory_fd=fd, principal=principal,
                                      command_center=center, identity=identity)
        expected = f'/u/previews/{ui_id}.png'
        if result.returncode or result.stdout.decode() != expected:
            raise RuntimeError('owner preview output did not complete')
        return expected
    finally:
        os.close(fd)
