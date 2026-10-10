"""Admit preview data in the daemon; render without any owner-store mount."""
from __future__ import annotations

import base64
import os
from pathlib import Path


def render(spec, wall_seconds):
    from tinyassets import role_decoder
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.custom_agents import read_app_ui_asset
    from tinyassets.role_scope import owner_principal
    from tinyassets.storage import data_dir
    from tinyassets.ui_preview import MAX_CHILD_OUTPUT

    root = data_dir().resolve()
    center = spec['universe_id']
    principal = owner_principal(root / center)
    if Path(spec['base_path']) != root or spec['owner_user_id'] != principal:
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
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.role_scope import owner_principal

    directory = Path(universe_dir)
    principal = owner_principal(directory)
    center = directory.name
    client = role_decoder._bounded_client
    if client is None:
        raise RuntimeError('bounded preview launcher is unavailable')
    identity = owner_identity(directory.parent, principal=principal)
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
