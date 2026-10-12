"""Tiered scope handling for multi-universe, multi-goal memory.

The scope layer answers: *for a given row in a memory table, who can see
it, and under what predicate?* Stage 2b reshapes ``MemoryScope`` from the
2026-04-14 nested-narrowing model to the 5-tier orthogonal-composition
model described in ``openspec/specs/knowledge-retrieval-and-memory/spec.md``.

Five tiers, each acting as an independent filter:

1. **Universe.** Hard invariant: no cross-universe read or write.
2. **Goal.** A coherent body of work within a universe.
3. **Branch.** An alternate-history fork of a goal's canon.
4. **User.** Private per-user overlay.
5. **Node.** Per-execution breadth + external-sources declaration
   (see :class:`NodeScope`).

Every tier except universe is optional. ``None`` on any tier means the
caller is not scoped to a specific value for that tier, i.e. they can
see rows at any value of that tier (subject to authorization). Stage 2b
defines the shape + write-side tagging; Stage 2c flips the read-side
predicate on via ``TINYASSETS_TIERED_SCOPE``.

Provides:
  MemoryScope         -- a caller's (universe, goal, branch, user, node) position
  NodeScope           -- node-level breadth + external sources
  SliceSpec           -- narrow-slice retrieval constraints
  ExternalSource      -- declared non-universe knowledge source
  ScopedQuery         -- query parameters scoped by a MemoryScope
  ScopeResolver       -- visibility + write permission logic
  ScopedMemoryRouter  -- MemoryManager wrapper (placeholder; Stage 2c flips)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scope primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SliceSpec:
    """A narrow-slice retrieval constraint used by ``NodeScope``.

    All fields are ANDed when multiple are set. ``None`` on a field
    means "no constraint on this dimension". Stage 2b keeps the three
    fields the design note §9.3 Q4 resolved to; extend on domain demand.
    """

    entity_ids: list[str] | None = None
    relation_types: list[str] | None = None
    document_ids: list[str] | None = None


@dataclass(frozen=True)
class ExternalSource:
    """A declared outside-knowledge source for an out-of-universe node.

    ``kind`` enumerates the four source categories from the design
    note §9.3 Q5 resolution. ``identifier`` is opaque to the scope layer
    (e.g. another ``universe_id`` when ``kind='universe'``; an API name
    like ``"arxiv"`` when ``kind='external_api'``).
    """

    kind: Literal[
        "universe", "external_api", "system_tool", "cross_universe_join"
    ]
    identifier: str


@dataclass(frozen=True)
class NodeScope:
    """A node's declared membership + breadth + external sources.

    ``universe_member`` is binary: TRUE if the node reads only from
    its caller's universe; FALSE if it pulls from outside sources.
    ``breadth`` applies only when ``universe_member=True``. The
    ``"narrow_slice"`` breadth requires a ``slice_spec``. The
    ``external_sources`` list is required when ``universe_member=False``
    — undeclared external reads are a bug the scope layer rejects at
    2c flag-flip time.
    """

    universe_member: bool = True
    breadth: Literal["full_canon", "narrow_slice"] = "full_canon"
    slice_spec: SliceSpec | None = None
    external_sources: list[ExternalSource] | None = None

    def __post_init__(self) -> None:
        # Cheap structural invariants — violations are caller bugs, not
        # data errors. Raise rather than log so they surface in dev.
        if self.breadth == "narrow_slice" and self.slice_spec is None:
            raise ValueError(
                "NodeScope: breadth='narrow_slice' requires a slice_spec"
            )
        if not self.universe_member and not self.external_sources:
            raise ValueError(
                "NodeScope: universe_member=False requires external_sources"
            )


@dataclass(frozen=True)
class MemoryScope:
    """The caller's position in the 5-tier scope hierarchy.

    Exactly one field is required: ``universe_id``. All other tiers
    default to ``None`` meaning "not scoped to a specific value; reads
    across all values subject to authorization".

    Author/session from the 2026-04-14 shape are removed per design
    §3: author collapses into ``user_id`` (roles tracked separately,
    not via scope); session collapses into node execution lifetime
    (``NodeScope`` carries that boundary now).
    """

    universe_id: str
    goal_id: str | None = None
    branch_id: str | None = None
    user_id: str | None = None
    node_scope: NodeScope | None = None

    # -----------------------------------------------------------------
    # Orthogonal-composition API (design §3)
    # -----------------------------------------------------------------

    def compose_predicate(self) -> dict[str, Any]:
        """Return the per-tier filter dict for WHERE-clause composition.

        Each set tier becomes a ``{"<tier>": value}`` entry; ``None``
        tiers are omitted. Readers combine this with table-side NULL
        semantics (``scope.col IS NULL OR scope.col = :value``) — see
        the design note §4 "Read-site behavior". The scope layer just
        emits the values; query builders own the NULL handling.
        """
        predicate: dict[str, Any] = {"universe_id": self.universe_id}
        if self.goal_id is not None:
            predicate["goal_id"] = self.goal_id
        if self.branch_id is not None:
            predicate["branch_id"] = self.branch_id
        if self.user_id is not None:
            predicate["user_id"] = self.user_id
        return predicate

    def to_filter_dict(self) -> dict[str, Any]:
        """Alias for :meth:`compose_predicate`.

        Preserved as a method name because a small number of callers
        still reference it. New code should prefer ``compose_predicate``
        — the name advertises the semantic (conjunction of tier
        predicates), not the shape (a dict).
        """
        return self.compose_predicate()

    def with_overrides(self, **overrides: Any) -> MemoryScope:
        """Return a new scope with specific tiers replaced.

        Unlike the old ``narrow``/``broaden`` pair, this is pure
        field-replacement with no path-containment validation —
        orthogonal composition doesn't have a "narrower than" ordering
        between arbitrary scopes (a goal-only scope and a branch-only
        scope are incomparable). Callers that need authorization
        checks should use :class:`ScopeResolver`.

        ``universe_id`` is not allowed in overrides; it's a hard
        invariant per the design note. Pass through
        :meth:`ScopeResolver.can_write` first if a universe-swap is
        intentional.
        """
        if "universe_id" in overrides:
            raise ValueError(
                "MemoryScope.with_overrides: universe_id is invariant; "
                "construct a new MemoryScope if a cross-universe switch "
                "is intended (and gate it through ScopeResolver)."
            )
        return MemoryScope(
            universe_id=self.universe_id,
            goal_id=overrides.get("goal_id", self.goal_id),
            branch_id=overrides.get("branch_id", self.branch_id),
            user_id=overrides.get("user_id", self.user_id),
            node_scope=overrides.get("node_scope", self.node_scope),
        )


# ---------------------------------------------------------------------------
# Query wrapper
# ---------------------------------------------------------------------------


@dataclass
class ScopedQuery:
    """Query parameters bound to a :class:`MemoryScope`.

    Attributes
    ----------
    scope : MemoryScope
        The caller's scope position. Reads resolved through
        :class:`ScopedMemoryRouter` or :class:`ScopeResolver` return
        rows visible at this scope.
    query_text : str | None
        Free-form text query.
    entity : str | None
        Filter facts to this entity.
    attribute : str | None
        Filter facts to this attribute.
    time_range : tuple[str | None, str | None]
        ``(from, to)`` ISO-8601 bounds. ``None`` on either side means
        unbounded.
    include_superseded : bool
        Include facts marked as superseded. Default False.
    max_results : int
        Upper bound on returned rows. Default 50.
    tiers : list[str] | None
        Which memory tiers to search: ``"core"``, ``"episodic"``,
        ``"archival"``. ``None`` = all tiers.
    """

    scope: MemoryScope
    query_text: str | None = None
    entity: str | None = None
    attribute: str | None = None
    time_range: tuple[str | None, str | None] = (None, None)
    include_superseded: bool = False
    max_results: int = 50
    tiers: list[str] | None = None


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class ScopeResolver:
    """Validates scope visibility and write permissions.

    In Stage 2b the resolver runs in advisory mode: ``can_write`` is
    the enforcement primitive, ``compose_read_predicate`` is the
    read-path primitive, and neither is wired into the query layer
    yet (2c flips that on via ``TINYASSETS_TIERED_SCOPE``).
    """

    def compose_read_predicate(
        self,
        caller_scope: MemoryScope,
    ) -> dict[str, Any]:
        """Return the read-time predicate a query builder should AND in.

        Shape matches :meth:`MemoryScope.compose_predicate`; see the
        design note §4 "Read-site behavior" for the WHERE clause that
        wraps it (each tier tolerates ``IS NULL`` to admit rows written
        before the caller's sub-scope existed).
        """
        return caller_scope.compose_predicate()

    def can_write(
        self,
        scope: MemoryScope,
        caller_scope: MemoryScope,
    ) -> bool:
        """Can a caller at ``caller_scope`` write to ``scope``?

        Writes are allowed only when every tier the caller is pinned
        at matches the target scope's tier value. A caller pinned to
        ``branch_id='main'`` cannot write a row tagged
        ``branch_id='dev'`` or ``branch_id=None``. Absence of a pin
        on the caller side is permissive — a universe-only caller can
        write to any sub-scope of its universe.

        Universe IDs must match exactly; cross-universe writes are
        never permitted.
        """
        if scope.universe_id != caller_scope.universe_id:
            return False

        pinned_tiers = (
            (caller_scope.goal_id, scope.goal_id),
            (caller_scope.branch_id, scope.branch_id),
            (caller_scope.user_id, scope.user_id),
        )
        for caller_val, target_val in pinned_tiers:
            if caller_val is None:
                continue
            if target_val != caller_val:
                return False
        return True

    def visible_branches(
        self,
        caller_scope: MemoryScope,
        all_branches: list[str],
    ) -> list[str]:
        """Which branches can this caller see?

        A caller pinned to a branch sees only that branch (if it
        exists in ``all_branches``). An unpinned caller sees every
        branch in the universe.
        """
        if caller_scope.branch_id is not None:
            return (
                [caller_scope.branch_id]
                if caller_scope.branch_id in all_branches
                else []
            )
        return all_branches


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class ScopedMemoryRouter:
    """Wraps MemoryManager with scope awareness.

    Stage 2b keeps this as a scaffolded delegation point. Stage 2c
    wires the read path through :meth:`ScopeResolver.compose_read_predicate`
    once ``TINYASSETS_TIERED_SCOPE`` flips on.
    """

    def __init__(self, memory_manager: Any) -> None:
        self._manager = memory_manager
        self._resolver = ScopeResolver()

    def query(self, scoped_query: ScopedQuery) -> list[dict[str, Any]]:
        """Run a scoped query through the memory tiers.

        Stage 2b placeholder: returns an empty list. Stage 2c wires
        the resolver's read predicate into the tier calls.
        """
        logger.debug(
            "ScopedMemoryRouter.query: scope=%s, entity=%s, text=%s",
            scoped_query.scope,
            scoped_query.entity,
            (scoped_query.query_text or "")[:50],
        )
        results: list[dict[str, Any]] = []
        return results[: scoped_query.max_results]

    def store(self, data: dict[str, Any], scope: MemoryScope) -> None:
        """Attach scope metadata to a row before delegating to the manager.

        Stage 2b placeholder — the actual archival write sites are
        threaded in 2b.2. This path exists for callers that still
        route through the router rather than the direct write sites.
        """
        enriched = {**data, "scope": scope.compose_predicate()}
        logger.debug(
            "ScopedMemoryRouter.store: scope=%s, keys=%s",
            scope,
            list(enriched.keys()),
        )
