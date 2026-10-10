"""Resolve a bound execution actor to the mapper's admitted center owner."""
from pathlib import Path


def owner_principal(universe_dir, *, actor=None):
    """Only the exact center actor or its admitted owner may enter a cell.

    Explicit actors are for trusted daemon jobs such as workspace reconciliation;
    request paths use the authenticated identity context. Never bind a request's
    actor parameter here. The mapper independently checks the returned pair.
    """
    from tinyassets.auth.middleware import current_identity
    from tinyassets.broker.owner_identities import admitted_owner, validate_center
    from tinyassets.storage import data_dir

    root, center = data_dir().resolve(), Path(universe_dir)
    if center.parent != root or center.resolve() != center:
        raise PermissionError('cell owner scope is not an admitted command center')
    validate_center(center.name)
    if actor is None:
        actor = current_identity().user_id
    try:
        owner = admitted_owner(root, center=center.name)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PermissionError('cell owner scope is not admitted: broker lookup failed') from exc
    if actor not in (owner, f'universe:{center.name}', f'command_center:{center.name}'):
        raise PermissionError('cell owner scope is not admitted')
    return owner
