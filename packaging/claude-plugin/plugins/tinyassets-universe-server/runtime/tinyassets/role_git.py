"""Admission and descriptor handoff for the bounded workspace-git cell."""
from __future__ import annotations

import os
from pathlib import Path


def run(argv, *, cwd, options, timeout_s):
    from tinyassets import role_decoder, workspace_fs
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.role_scope import owner_principal
    from tinyassets.storage import data_dir

    client = role_decoder._bounded_client
    if client is None:
        raise RuntimeError('bounded git launcher is unavailable')
    root = data_dir().resolve()
    directory = Path(cwd)
    # Refuse magic descriptor paths and symlinks; caller-held resource/lease
    # descriptors need their own explicit integration, never a silent fallback.
    if not directory.is_absolute() or '..' in directory.parts:
        raise PermissionError('git requires a canonical owner directory')
    relative = directory.relative_to(root)
    if len(relative.parts) < 2:
        raise PermissionError('git requires an owner work subtree')
    center = relative.parts[0]
    principal = owner_principal(root / center)
    identity = owner_identity(root, principal=principal)
    fd = workspace_fs.open_dir_nofollow(directory)
    try:
        return client.git(argv, options=options, timeout_s=timeout_s, directory_fd=fd,
                          principal=principal, command_center=center, identity=identity)
    finally:
        os.close(fd)
