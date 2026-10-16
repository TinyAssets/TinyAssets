"""tinyassets.attribution — Attribution chain and remix provenance primitives.

Schema layer (schema.py): attribution_edge / attribution_credit DDL and dataclasses.
Calculation layer (calc.py): compute_credit_shares.

Ship contract: when `attribution_credit` rows exist at ship time, map each
`actor_id` to a GitHub handle through `CONTRIBUTORS.md` and emit a
`Co-Authored-By:` line per contributor. An `actor_id` with no mapping is skipped
SILENTLY -- attribution must never block a commit.
"""

from __future__ import annotations

from tinyassets.attribution.calc import compute_credit_shares
from tinyassets.attribution.schema import (
    ATTRIBUTION_SCHEMA,
    AttributionCredit,
    AttributionEdge,
    ContributionKind,
    RemixProvenance,
    migrate_attribution_schema,
)

__all__ = [
    "ATTRIBUTION_SCHEMA",
    "AttributionCredit",
    "AttributionEdge",
    "ContributionKind",
    "RemixProvenance",
    "compute_credit_shares",
    "migrate_attribution_schema",
]
