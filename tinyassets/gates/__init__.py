"""Historical gate bonus schema; no runtime stake or settlement transport."""

from tinyassets.gates.schema import (
    BONUS_COLUMNS,
    AttachmentScope,
    GateBonusClaim,
    migrate_gate_bonus_columns,
)

__all__ = [
    "AttachmentScope",
    "BONUS_COLUMNS",
    "GateBonusClaim",
    "migrate_gate_bonus_columns",
]
