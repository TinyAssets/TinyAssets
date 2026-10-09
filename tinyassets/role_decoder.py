"""Daemon client for the launcher's fixed, data-free decoder cell."""
from __future__ import annotations

from pathlib import Path

_bounded_client = None


def install_bounded_client(client):
    """One startup-owned client; no environment lookup or runtime replacement."""
    from tinyassets.owner_launcher_client import OwnerLauncherClient

    global _bounded_client
    if type(client) is not OwnerLauncherClient or _bounded_client is not None:
        raise RuntimeError('bounded decoder client already installed or invalid')
    _bounded_client = client


def decode(data: bytes, mime: str, universe_dir: Path):
    from tinyassets.auth.middleware import current_identity
    from tinyassets.broker import supervisor
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.daemon_server import get_founder_home, universe_access_permission
    from tinyassets.storage import data_dir
    from tinyassets.tool_images import MAX_IMAGE_SOURCE_BYTES

    # Scope first, launcher second: a foreign owner, or a caller with no
    # command center at all, is refused before the bounded channel is touched.
    supervisor._protect_daemon()
    if universe_dir is None:
        raise PermissionError("decoder requires admitted owner scope")
    universe = Path(universe_dir)
    root = data_dir().resolve()
    owner = current_identity().user_id
    if (universe.parent != root or universe.resolve() != universe
            or not (get_founder_home(root, owner) == universe.name or universe_access_permission(
                root, universe_id=universe.name, actor_id=owner) == "admin")
            or not isinstance(data, bytes) or len(data) > MAX_IMAGE_SOURCE_BYTES):
        raise PermissionError("decoder scope is not admitted")
    if _bounded_client is None:
        raise RuntimeError("bounded decoder launcher is unavailable")
    identity = owner_identity(root, principal=owner)
    return _bounded_client.decode(data, mime, principal=owner,
                                  command_center=universe.name, identity=identity)
