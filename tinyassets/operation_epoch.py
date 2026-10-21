"""Opaque owner operation identities are scoped to their durable creation epoch."""
from __future__ import annotations


def new_identity(value):
    """Call only at identity creation, never while replaying an existing identity."""
    from tinyassets.owner_runtime import current

    runtime = current()
    if runtime is None:
        return value
    return runtime.tools.new_operation(value)


def check(database, identity):
    """Receipt-store epoch check; opaque IDs are never timestamp-compared."""
    from tinyassets.boxes.provider import BoxOperationRefused
    from tinyassets.owner_runtime import current

    runtime = current()
    if runtime is not None:
        try:
            runtime.tools.check_operation(identity)
        except BoxOperationRefused as error:
            raise ValueError(str(error)) from None
