"""Unisolated local driver kept as the development and contract-test reference.

The descriptor-safe implementation lives in :mod:`tinyassets.boxes.boxd` and
also runs inside the isolated owner box. Production must not instantiate this
local entry point.
"""

from tinyassets.boxes.boxd import LocalBoxProvider as LocalBoxProvider
from tinyassets.boxes.boxd import _remove_beneath as _remove_beneath
from tinyassets.boxes.boxd import _Running as _Running

__all__ = ["LocalBoxProvider"]
