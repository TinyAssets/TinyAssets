"""Attribution chain schema — DDL and dataclasses for remix provenance.

Spec: project_designer_royalties_and_bounties (attribution chain section).

Two tables:
  attribution_edge   — parent/child relationship between branch/node artifacts
  attribution_credit — per-artifact credit share for each contributor in the chain

Design invariant: credit_share values for a given artifact_id sum to <= 1.0.
Shares record contribution credit, without monetary distribution.

All shares are stored as REAL in [0.0, 1.0]. Generation depth starts at 0
for the original artifact and increments for each remix step.

Contribution kinds:
  "original"   — created the artifact from scratch
  "remix"      — forked + substantially modified an ancestor
  "patch"      — minor edit / metadata-only change to an ancestor
  "template"   — used artifact as a template (structural, not content)
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── Status / kind enums ───────────────────────────────────────────────────────

ContributionKind = str  # "original" | "remix" | "patch" | "template"

# ── DDL ───────────────────────────────────────────────────────────────────────

ATTRIBUTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS attribution_edge (
    edge_id          TEXT PRIMARY KEY,
    parent_id        TEXT NOT NULL,
    child_id         TEXT NOT NULL,
    parent_kind      TEXT NOT NULL DEFAULT 'branch'
                         CHECK (parent_kind IN ('branch', 'node')),
    child_kind       TEXT NOT NULL DEFAULT 'branch'
                         CHECK (child_kind IN ('branch', 'node')),
    generation_depth INTEGER NOT NULL DEFAULT 1 CHECK (generation_depth >= 1),
    contribution_kind TEXT NOT NULL DEFAULT 'remix'
                         CHECK (contribution_kind IN (
                             'original', 'remix', 'patch', 'template'
                         )),
    created_at       TEXT NOT NULL,
    UNIQUE (parent_id, child_id)
);

CREATE INDEX IF NOT EXISTS idx_edge_parent
    ON attribution_edge(parent_id);

CREATE INDEX IF NOT EXISTS idx_edge_child
    ON attribution_edge(child_id);

CREATE TABLE IF NOT EXISTS attribution_credit (
    credit_id       TEXT PRIMARY KEY,
    artifact_id     TEXT NOT NULL,
    artifact_kind   TEXT NOT NULL DEFAULT 'branch'
                        CHECK (artifact_kind IN ('branch', 'node')),
    actor_id        TEXT NOT NULL,
    owner_user_id   TEXT NOT NULL DEFAULT '',
    daemon_id       TEXT NOT NULL DEFAULT '',
    runtime_instance_id TEXT NOT NULL DEFAULT '',
    worker_id       TEXT NOT NULL DEFAULT '',
    credit_share    REAL NOT NULL CHECK (credit_share >= 0.0 AND credit_share <= 1.0),
    royalty_share   REAL NOT NULL DEFAULT 0.0
                        CHECK (royalty_share >= 0.0 AND royalty_share <= 1.0),
    generation_depth INTEGER NOT NULL DEFAULT 0 CHECK (generation_depth >= 0),
    contribution_kind TEXT NOT NULL DEFAULT 'original'
                        CHECK (contribution_kind IN (
                            'original', 'remix', 'patch', 'template'
                        )),
    recorded_at     TEXT NOT NULL,
    UNIQUE (artifact_id, actor_id)
);

CREATE INDEX IF NOT EXISTS idx_credit_artifact
    ON attribution_credit(artifact_id);

CREATE INDEX IF NOT EXISTS idx_credit_actor
    ON attribution_credit(actor_id);
"""

# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class AttributionEdge:
    """One parent → child remix relationship in the attribution graph."""

    edge_id: str
    parent_id: str
    child_id: str
    parent_kind: str
    child_kind: str
    generation_depth: int
    contribution_kind: ContributionKind
    created_at: str

    def __post_init__(self) -> None:
        if self.generation_depth < 1:
            raise ValueError(
                f"generation_depth must be >= 1 for an edge, got {self.generation_depth!r}"
            )
        if self.parent_kind not in ("branch", "node"):
            raise ValueError(f"parent_kind must be 'branch' or 'node', got {self.parent_kind!r}")
        if self.child_kind not in ("branch", "node"):
            raise ValueError(f"child_kind must be 'branch' or 'node', got {self.child_kind!r}")
        if self.contribution_kind not in ("original", "remix", "patch", "template"):
            raise ValueError(
                f"contribution_kind must be one of original/remix/patch/template, "
                f"got {self.contribution_kind!r}"
            )

    @classmethod
    def from_row(cls, row: dict) -> AttributionEdge:
        return cls(
            edge_id=row["edge_id"],
            parent_id=row["parent_id"],
            child_id=row["child_id"],
            parent_kind=row.get("parent_kind") or "branch",
            child_kind=row.get("child_kind") or "branch",
            generation_depth=int(row.get("generation_depth") or 1),
            contribution_kind=row.get("contribution_kind") or "remix",
            created_at=row["created_at"],
        )


@dataclass
class AttributionCredit:
    """One actor's credit share on a specific artifact."""

    credit_id: str
    artifact_id: str
    artifact_kind: str
    actor_id: str
    credit_share: float
    royalty_share: float
    generation_depth: int
    contribution_kind: ContributionKind
    recorded_at: str
    owner_user_id: str = ""
    daemon_id: str = ""
    runtime_instance_id: str = ""
    worker_id: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.credit_share <= 1.0:
            raise ValueError(
                f"credit_share must be in [0.0, 1.0], got {self.credit_share!r}"
            )
        if not 0.0 <= self.royalty_share <= 1.0:
            raise ValueError(
                f"royalty_share must be in [0.0, 1.0], got {self.royalty_share!r}"
            )
        if self.generation_depth < 0:
            raise ValueError(
                f"generation_depth must be >= 0, got {self.generation_depth!r}"
            )
        if self.artifact_kind not in ("branch", "node"):
            raise ValueError(
                f"artifact_kind must be 'branch' or 'node', got {self.artifact_kind!r}"
            )

    @property
    def is_original_author(self) -> bool:
        return self.generation_depth == 0

    @classmethod
    def from_row(cls, row: dict) -> AttributionCredit:
        return cls(
            credit_id=row["credit_id"],
            artifact_id=row["artifact_id"],
            artifact_kind=row.get("artifact_kind") or "branch",
            actor_id=row["actor_id"],
            credit_share=float(row.get("credit_share") or 0.0),
            royalty_share=float(row.get("royalty_share") or 0.0),
            generation_depth=int(row.get("generation_depth") or 0),
            contribution_kind=row.get("contribution_kind") or "original",
            recorded_at=row["recorded_at"],
            owner_user_id=row.get("owner_user_id") or "",
            daemon_id=row.get("daemon_id") or "",
            runtime_instance_id=row.get("runtime_instance_id") or "",
            worker_id=row.get("worker_id") or "",
        )


@dataclass
class RemixProvenance:
    """Full attribution lineage for a single artifact.

    Aggregates edges + credits into one queryable view. Built by callers
    querying both tables; not persisted directly.
    """

    artifact_id: str
    artifact_kind: str
    edges: list[AttributionEdge] = field(default_factory=list)
    credits: list[AttributionCredit] = field(default_factory=list)

    @property
    def total_credit_share(self) -> float:
        """Sum of all credit shares for this artifact (should be <= 1.0)."""
        return sum(c.credit_share for c in self.credits)

    @property
    def is_credit_valid(self) -> bool:
        """True when total credit shares do not exceed 1.0."""
        return self.total_credit_share <= 1.0 + 1e-9

    @property
    def max_generation_depth(self) -> int:
        """Deepest generation in the lineage (0 if original, N for N remixes)."""
        if not self.edges:
            return 0
        return max(e.generation_depth for e in self.edges)

    def credits_for_actor(self, actor_id: str) -> list[AttributionCredit]:
        return [c for c in self.credits if c.actor_id == actor_id]


# ── Migration ─────────────────────────────────────────────────────────────────

def migrate_attribution_schema(conn) -> None:  # type: ignore[no-untyped-def]
    """Create attribution tables if absent. Idempotent."""
    conn.executescript(ATTRIBUTION_SCHEMA)
    existing_credit_cols = {
        row["name"] if hasattr(row, "keys") else row[1]
        for row in conn.execute("PRAGMA table_info(attribution_credit)")
    }
    for col, ddl in (
        ("owner_user_id", "TEXT NOT NULL DEFAULT ''"),
        ("daemon_id", "TEXT NOT NULL DEFAULT ''"),
        ("runtime_instance_id", "TEXT NOT NULL DEFAULT ''"),
        ("worker_id", "TEXT NOT NULL DEFAULT ''"),
    ):
        if col not in existing_credit_cols:
            conn.execute(f"ALTER TABLE attribution_credit ADD COLUMN {col} {ddl}")
