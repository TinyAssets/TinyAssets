"""Community Branches — data models for community-designed LangGraph topologies.

Defines the core models that let users design, share, and fork
complete graph topologies through MCP tools:

- **BranchDefinition**: A complete graph topology (nodes + edges + state).
- **NodeDefinition**: A single node within a branch graph, extending
  the existing NodeRegistration contract.
- **StateFieldDecl**: A single field declaration within a branch's
  state schema, supporting the TypedDict + Annotated reducer pattern.
  Deferred to Phase 3 — state_schema is stored as an unvalidated JSON
  blob for now. StateFieldDecl is available for future formal validation.

All models serialize to/from JSON for SQLite storage. BranchDefinition
stores its graph topology (nodes and edges) and state schema as embedded
JSON rather than separate normalized tables — this keeps fork/clone
operations atomic and avoids cross-table consistency issues.

Graph topology JSON follows LangGraph's native API shape:
- nodes: list of {id, node_def_id, position}
- edges: list of {from, to} for simple directed edges
- conditional_edges: list of {from, conditions: {outcome: target}} for branching
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from tinyassets.phase_vocab import VALID_PHASES, normalize_phase

# Placeholder-extraction regex pair — kept in sync with
# ``tinyassets/graph_compiler.py`` (_PLACEHOLDER_RE + _DOUBLE_PLACEHOLDER_RE
# + _ESCAPED_PLACEHOLDER_RE). Duplicated here so ``validate()`` can run
# the build-time missing-key check without creating a circular import
# (graph_compiler already imports BranchDefinition at module load).
# If either copy changes, update both.
_VALIDATE_PLACEHOLDER_RE = re.compile(
    r"(?<!\\){([a-zA-Z_][a-zA-Z0-9_]*)}"
)
_VALIDATE_DOUBLE_PLACEHOLDER_RE = re.compile(
    r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}"
)


def _template_placeholders(template: str) -> list[str]:
    """Return deduped list of placeholder identifiers in a template.

    Handles Jinja ``{{ident}}`` by normalizing to ``{ident}`` before
    scanning. Escaped ``\\{ident\\}`` forms are excluded via the
    lookbehind in ``_VALIDATE_PLACEHOLDER_RE``.
    """
    if not template:
        return []
    normalized = _VALIDATE_DOUBLE_PLACEHOLDER_RE.sub(r"{\1}", template)
    seen: set[str] = set()
    out: list[str] = []
    for k in _VALIDATE_PLACEHOLDER_RE.findall(normalized):
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


_BRANCH_SKILL_ID_RE = re.compile(r"[^a-z0-9._-]+")
_BRANCH_SKILL_BODY_MAX_CHARS = 200_000


def _slugify_branch_skill_id(value: str) -> str:
    """Return a stable identifier for a branch-carried skill snapshot."""
    slug = _BRANCH_SKILL_ID_RE.sub("-", value.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-._")
    return slug[:80]


def normalize_branch_skill_snapshot(raw: Any) -> dict[str, Any]:
    """Normalize one user-authored/copied Branch skill snapshot.

    Branch skills are data carried by a Branch, not executable plugins.
    The body is required so the skill remains useful if an internet
    source link changes or disappears.
    """
    if not isinstance(raw, dict):
        raise ValueError("skill must be an object")

    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("skill name is required")

    body = str(raw.get("body") or raw.get("instructions") or "").strip()
    if not body:
        raise ValueError("skill body is required")
    if len(body) > _BRANCH_SKILL_BODY_MAX_CHARS:
        raise ValueError(
            f"skill body exceeds {_BRANCH_SKILL_BODY_MAX_CHARS} characters"
        )

    skill_id_raw = str(raw.get("skill_id") or raw.get("id") or name).strip()
    skill_id = _slugify_branch_skill_id(skill_id_raw)
    if not skill_id:
        raise ValueError("skill_id is required")

    tags_raw = raw.get("tags", [])
    if tags_raw is None:
        tags_raw = []
    if isinstance(tags_raw, str):
        tags_raw = [t.strip() for t in tags_raw.split(",") if t.strip()]
    if not isinstance(tags_raw, list):
        raise ValueError("skill tags must be a list or CSV string")

    metadata = raw.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("skill metadata must be an object")

    normalized: dict[str, Any] = {
        "skill_id": skill_id,
        "name": name,
        "body": body,
    }
    optional_string_fields = (
        "description",
        "source_url",
        "source_note",
        "parent_skill_id",
        "license",
        "version",
    )
    for field_name in optional_string_fields:
        value = str(raw.get(field_name) or "").strip()
        if value:
            normalized[field_name] = value
    tags = [str(t).strip() for t in tags_raw if str(t).strip()]
    if tags:
        normalized["tags"] = tags
    if metadata:
        normalized["metadata"] = metadata
    return normalized


def normalize_branch_skill_snapshots(raw: Any) -> list[dict[str, Any]]:
    """Normalize and dedupe a list of branch skill snapshots."""
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValueError("skills must be a list")

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for entry in raw:
        skill = normalize_branch_skill_snapshot(entry)
        skill_id = skill["skill_id"]
        if skill_id in seen:
            raise ValueError(f"duplicate skill_id '{skill_id}'")
        seen.add(skill_id)
        out.append(skill)
    return out

# ═══════════════════════════════════════════════════════════════════════════
# Validation errors
# ═══════════════════════════════════════════════════════════════════════════


class NodeDefinitionValidationError(ValueError):
    """Raised when a NodeDefinition load/construct hits a type-level shape error.

    Carries `field` + `message` attrs so MCP-layer handlers can render the
    structured envelope ``{"error": "validation", "field": ..., "message": ...}``
    that BUG-029-style chatbot contracts expect; falls back to plain
    ``str(exc)`` for callers that just want a human-readable message.

    Subclass of ``ValueError`` so existing ``except ValueError`` catch
    sites keep working — the structured shape is opt-in.

    Use case (Mara 2026-04-26): a chatbot writes a branch row with
    ``output_keys: "framed_question"`` (str instead of list) via a path
    that bypasses the lenient write-side ``_coerce_node_keys`` helper,
    and the row later round-trips through ``NodeDefinition.from_dict``
    with the string intact. Without strict read-side validation, the
    string would be character-iterated into ``['f','r','a',...]`` and
    silently corrupt downstream sandbox/state handling. With this
    exception raised in ``__post_init__``, the load fails loudly per
    Hard Rule #8.
    """

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


# ═══════════════════════════════════════════════════════════════════════════
# State Schema (Phase 3 — formal validation deferred)
# ═══════════════════════════════════════════════════════════════════════════

# Valid type strings for state field declarations.
VALID_FIELD_TYPES = {"string", "number", "boolean", "list", "dict", "any"}


def normalize_branch_io_manifest(value: Any) -> dict[str, Any] | None:
    """Preserve optional declarations without granting file/runtime authority."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("io_manifest must be an object")
    for direction in ("inputs", "outputs"):
        entries = value.get(direction, [])
        if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
            raise ValueError(f"io_manifest.{direction} must be a list of objects")

    def exact(item: Any) -> None:
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("io_manifest requires string object keys")
            for child in item.values():
                exact(child)
        elif isinstance(item, list):
            for child in item:
                exact(child)
        elif item is not None and type(item) not in {str, bool, int, float}:
            raise ValueError("io_manifest requires exact JSON values")

    try:
        # Serializing first detects cycles before the recursive exact-type check.
        encoded = json.dumps(value, allow_nan=False, ensure_ascii=False)
        exact(value)
        return json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"io_manifest requires exact JSON: {exc}") from exc

# Valid reducer strategies — maps to how LangGraph merges parallel updates.
VALID_REDUCERS = {"overwrite", "append", "merge"}


@dataclass
class StateFieldDecl:
    """A single field declaration in a branch's state schema.

    Maps to one key in the LangGraph TypedDict. The reducer determines
    how parallel node outputs merge into the state:

    - ``overwrite``: last-write-wins (default for scalar fields).
    - ``append``: ``Annotated[list, operator.add]`` — accumulates.
    - ``merge``: ``dict.update`` semantics for dict fields.

    NOTE: This model is available for future Phase 3 formal validation.
    Currently, state_schema in BranchDefinition is stored as an
    unvalidated JSON blob (list of dicts).
    """

    name: str
    type: str = "any"
    default_value: Any = None
    reducer: str = "overwrite"
    description: str = ""

    def __post_init__(self) -> None:
        if self.type not in VALID_FIELD_TYPES:
            raise ValueError(
                f"Invalid field type '{self.type}'. "
                f"Must be one of: {', '.join(sorted(VALID_FIELD_TYPES))}"
            )
        if self.reducer not in VALID_REDUCERS:
            raise ValueError(
                f"Invalid reducer '{self.reducer}'. "
                f"Must be one of: {', '.join(sorted(VALID_REDUCERS))}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateFieldDecl:
        return cls(**{
            k: v for k, v in data.items()
            if k in cls.__dataclass_fields__
        })


# ═══════════════════════════════════════════════════════════════════════════
# Node Definition
# ═══════════════════════════════════════════════════════════════════════════

# Valid phases — same set as the existing node registration system.
# ``worldbuild`` is a deprecated same-arc alias for ``enrich``.


@dataclass
class NodeDefinition:
    """A single node within a branch graph topology.

    Extends the existing NodeRegistration contract with fields needed
    for graph topology (dependencies, evaluation criteria, model hints)
    and execution policy (timeout, retry).

    Nodes come in two flavors:
    - **Code nodes**: have ``source_code`` — executed in the sandbox.
    - **Prompt-template nodes**: have ``prompt_template`` — LLM calls
      with the template filled from state. ``model_hint`` selects the
      provider role.

    ``input_keys`` and ``output_keys`` declare which state fields the node
    reads and writes — enforced by the sandbox at runtime for code nodes.
    """

    node_id: str
    display_name: str
    description: str = ""
    phase: str = "custom"

    # State contract
    input_keys: list[str] = field(default_factory=list)
    output_keys: list[str] = field(default_factory=list)
    # When true, prompt_template rendering sees ONLY the state keys
    # declared in ``input_keys`` — references to any other state key
    # raise CompilerError at runtime. This restores symmetry with the
    # code-node sandbox (node_sandbox.py:279-282 already filters
    # code-node state views). Branch authors can explicitly set false
    # as a legacy escape hatch for prompt templates that intentionally
    # read beyond their declared input_keys.
    strict_input_isolation: bool = True

    # Source and execution — one of source_code or prompt_template
    source_code: str = ""
    # A code node may name ONE ancestor checkout node whose workspace is
    # bound read-write at /workspace for the run (design D2). Empty = no
    # workspace and no ``ws`` object. Resolved through the run's effect
    # chain at run time; naming a non-ancestor fails at compile time.
    workspace: str = ""
    prompt_template: str = ""
    model_hint: str = ""
    # Per-node reasoning/effort level — a REAL provider setting (e.g. Codex
    # ``model_reasoning_effort``), not a prompt hint. Empty = provider default.
    # Lets a branch run a light node (localize) at ``minimal``/``low`` for
    # speed+cost and a hard node (propose_changes) at ``high``. Like model_hint,
    # this is a per-node knob the branch builder controls.
    reasoning_effort: str = ""
    tools_allowed: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    # #61: dense LLM calls (legal research, long summaries) regularly
    # take 90s+ on local models. 300s default matches
    # tinyassets.providers.base.ProviderConfig.timeout so a node timeout
    # only fires after the provider's own subprocess/HTTP limit.
    timeout_seconds: float = 300.0
    retry_policy: dict[str, Any] = field(default_factory=lambda: {
        "max_retries": 0,
        "backoff_seconds": 1.0,
    })

    # Per-node LLM policy override. Shape:
    # {
    #   "preferred": {"provider": str, "model": str, "reasoning_effort"?: str},
    #   "fallback_chain": [{"provider": str, "model"?: str,
    #                       "trigger": "unavailable"|"rate_limited"|
    #                                  "cost_exceeded"|"empty_response"}],
    #   "difficulty_override": [{"if_difficulty": str,
    #                            "use": {"provider": str, "model"?: str}}],
    # }
    # When None the branch-level default_llm_policy applies; when that is
    # also None the existing role-based routing is used (backward-compat).
    llm_policy: dict[str, Any] | None = None

    # When True the node shells out to a bwrap-sandboxed CLI (dev, checker,
    # tester variants). validate_branch warns when this is True and the
    # host's sandbox probe fails. Default False preserves back-compat.
    requires_sandbox: bool = False

    # Partial-credit checkpoints authored into node_def.
    # Each entry: {
    #   "checkpoint_id": str (unique within node),
    #   "earns_fraction": float (0.0-1.0),
    #   "reached_when": {
    #       "state_key": str,
    #       "value": <any>  # optional — if omitted, fires on key presence
    #       OR "exists": true  # fires when key is non-empty/non-None
    #   }
    # }
    # Cumulative earns_fraction across all checkpoints must not exceed 1.0.
    # Default (empty list) = all-or-nothing = current behavior.
    checkpoints: list[dict[str, Any]] = field(default_factory=list)

    # Quality
    evaluation_criteria: list[dict[str, str]] = field(default_factory=list)

    # Sub-branch invocation (invoke_branch node kind).
    # When set this node spawns a child branch run rather than executing an
    # LLM template or source-code snippet.
    # Shape: {
    #   "branch_def_id": str,
    #   "inputs_mapping": {parent_state_key: child_input_key},
    #   "output_mapping": {parent_state_key: child_output_key},
    #   "wait_mode": "blocking" | "async",
    # }
    # "blocking": spawns the child, waits for completion, writes output_mapping.
    # "async": spawns the child, writes run_id to the declared
    #   output_mapping[0] target key, returns immediately.
    invoke_branch_spec: dict[str, Any] | None = None

    # Sub-branch invocation against a frozen branch_version snapshot
    # (Task #76a, Phase A item 5). Sibling to invoke_branch_spec; uses an
    # immutable branch_version_id instead of a live branch_def_id. The
    # version is content-addressed via branch_versions; the run binds to
    # that exact snapshot, immune to live-def edits.
    # Shape: {
    #   "branch_version_id": str,                         # required, "<def_id>@<sha8>"
    #   "inputs_mapping": {parent_state_key: child_input_key},
    #   "output_mapping": {parent_state_key: child_output_key},
    #   "wait_mode": "blocking" | "async",
    #   "on_child_fail": "propagate" | "default" | "retry",  # default "propagate"
    #   "default_outputs": dict | None,        # used when on_child_fail="default"
    #   "retry_budget": int | None,            # used when on_child_fail="retry"
    #   "child_actor": str | None,             # actor override; default = parent
    # }
    # Mutually exclusive with invoke_branch_spec; validate() enforces.
    invoke_branch_version_spec: dict[str, Any] | None = None

    # await_branch_run node kind. Reads a run_id from parent state, polls
    # until the child run ends, writes output_mapping into parent state.
    # Shape: {
    #   "run_id_field": str,       # state key that holds the child run_id
    #   "output_mapping": {parent_state_key: child_output_key},
    #   "timeout_seconds": float,  # default 300
    # }
    await_run_spec: dict[str, Any] | None = None

    # PR-122 Phase 1 — external-write effect declarations. Each string names
    # a sink the node's outputs should be routed to after the run completes.
    # The platform is channel-agnostic: the general sink is
    # "authenticated_external_call" (a credential-blind call over a
    # user-configured connection — see
    # tinyassets.effectors.authenticated_external_call); "wiki_write_back" is
    # the internal write-back. There is no per-channel sink — channels are
    # user-built graph nodes over the generic call. The node's output_keys must
    # contain at least one value parseable as an ``external_write_packet`` shape
    # (sink, payload, idempotency_hint?, expected_evidence_keys?).
    # Convention only — this is NOT a new substrate primitive; the
    # effector reads the packet shape out of run_state at completion time.
    # See draft spec drafts/concepts/external-write-packet-shape.md and
    # the canonical 6+5 vocab page for context.
    effects: list[str] = field(default_factory=list)

    # Real-world handoff declarations (capability
    # ``real-world-handoffs-and-outcomes``). Each entry names a *typed* external
    # effect this node's output may become: output_field, adapter,
    # adapter_action, destination, effect_class, outcome_kind, and optionally
    # credential_class + evidence_contract. Validated by
    # ``tinyassets.handoffs.models.parse_declaration``.
    #
    # This is richer than ``effects`` above on purpose, and does not replace it:
    # ``effects`` names a sink for the legacy packet shape, whereas a handoff
    # binds an exact declared output to an exact destination so that neither the
    # output nor the destination can be substituted at invocation time. Because
    # the declaration lives on the node, it is carried into every published
    # branch version's snapshot (``node_defs``) and is therefore immutable for
    # that version — which is what lets a confirmation be bound to a source
    # version/hash.
    handoffs: list[dict[str, Any]] = field(default_factory=list)

    # Legacy compat fields from NodeRegistration
    author: str = ""
    registered_at: str = ""
    enabled: bool = True
    approved: bool = False
    approved_by: str = ""
    approved_at: str = ""
    approved_source_hash: str = ""
    approval_reason: str = ""

    def __post_init__(self) -> None:
        if self.phase not in VALID_PHASES:
            raise ValueError(
                f"Invalid phase '{self.phase}'. "
                f"Must be one of: {', '.join(sorted(VALID_PHASES))}"
            )
        self.phase = normalize_phase(self.phase)
        # Read-side strict validation for input_keys / output_keys
        # (Task #12, Option B). Fail loudly when a persisted branch row
        # holds a non-list value — typically a bare string from a
        # pre-fix write or a non-funnel write path that bypassed the
        # lenient `_coerce_node_keys` helper. Without this guard,
        # downstream `for k in node.input_keys:` iterates the string
        # character-by-character, silently corrupting sandbox/state
        # handling. Per Hard Rule #8, we'd rather fail to load than
        # accept malformed data.
        self._validate_workspace_timeout()
        for field_name in (
            "input_keys",
            "output_keys",
            "tools_allowed",
            "effects",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, list):
                raise NodeDefinitionValidationError(
                    field_name,
                    f"must be a list of strings, got "
                    f"{type(value).__name__}",
                )
            for idx, item in enumerate(value):
                if not isinstance(item, str):
                    raise NodeDefinitionValidationError(
                        field_name,
                        f"[{idx}] must be a string, got "
                        f"{type(item).__name__}",
                    )

    def _validate_workspace_timeout(self) -> None:
        """A workspace node's timeout is bounded; see graph_compiler.

        Checked on the read side too, so a persisted row that predates the
        bound fails to load rather than compiling into a node that holds the
        host-wide slot for as long as it likes.
        """
        if not self.workspace:
            return
        timeout = float(self.timeout_seconds or 0.0)
        if not 0 < timeout <= 1800.0:
            raise NodeDefinitionValidationError(
                "timeout_seconds",
                f"must be within 0 < t <= 1800 for a node declaring "
                f"workspace: '{self.workspace}', got {timeout}",
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NodeDefinition:
        return cls(**{
            k: v for k, v in data.items()
            if k in cls.__dataclass_fields__
        })

    def to_node_registration(self) -> dict[str, Any]:
        """Convert to the legacy NodeRegistration dict shape.

        Used for backward compatibility with the existing sandbox
        ``execute_registered()`` method and the extensions MCP tool.
        """
        return {
            "node_id": self.node_id,
            "display_name": self.display_name,
            "description": self.description,
            "phase": self.phase,
            "input_keys": self.input_keys,
            "output_keys": self.output_keys,
            "source_code": self.source_code,
            "dependencies": self.dependencies,
            "author": self.author,
            "registered_at": self.registered_at,
            "enabled": self.enabled,
            "approved": self.approved,
        }

    @classmethod
    def from_node_registration(cls, reg: dict[str, Any]) -> NodeDefinition:
        """Create a NodeDefinition from a legacy NodeRegistration dict.

        Used during migration from .node_registry.json.
        """
        return cls.from_dict(reg)

    def mark_approved(
        self,
        *,
        approved_by: str = "",
        reason: str = "",
        at: str = "",
    ) -> NodeDefinition:
        """Record genuine approval together with its source-hash provenance.

        PROVENANCE, not a gate (change sandboxed-code-node; receipts fixed
        2026-09-02): a code node runs in the OS sandbox whether or not anyone
        approved it. What this records is WHO approved WHICH source: an
        approval is genuine only when ``approved_source_hash`` equals
        ``sha256(source_code)``; a bare ``approved=True`` with an empty or
        stale hash is forged or carried from elsewhere and is reported as
        unapproved by the receipts (PR #1349 kept the hash check for that).

        This is the ONLY sanctioned in-process approval helper. Trusted
        construction (host code, fixtures) must call it instead of setting
        ``approved=True`` directly, so the hash is always bound to the source
        actually being approved. Do NOT use this to sprinkle hashes onto code
        you have not genuinely approved — it binds the hash to whatever
        ``source_code`` is set at call time.

        Returns ``self`` so it can be chained at construction sites.
        """
        self.approved = True
        self.approved_by = approved_by
        self.approved_at = at or datetime.now(timezone.utc).isoformat()
        self.approved_source_hash = hashlib.sha256(
            (self.source_code or "").encode("utf-8")
        ).hexdigest()
        self.approval_reason = reason
        return self


# ═══════════════════════════════════════════════════════════════════════════
# Graph Topology Types
# ═══════════════════════════════════════════════════════════════════════════

# Graph topology JSON follows LangGraph's native API shape:
#
# {
#   "nodes": [{"id": "orient", "node_def_id": "...", "position": 0}],
#   "edges": [{"from": "START", "to": "orient"}, ...],
#   "conditional_edges": [{"from": "commit", "conditions": {"accept": "END", "revise": "draft"}}]
# }
#
# Special node IDs: "START" and "END" are reserved for graph entry/exit.

RESERVED_NODE_IDS = {"START", "END"}


@dataclass
class GraphNodeRef:
    """A reference to a node definition within a graph topology.

    This is the graph-level placement of a node — linking a position
    in the topology to a NodeDefinition via ``node_def_id``.
    """

    id: str
    node_def_id: str = ""
    position: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "node_def_id": self.node_def_id, "position": self.position}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphNodeRef:
        return cls(**{
            k: v for k, v in data.items()
            if k in cls.__dataclass_fields__
        })


@dataclass
class EdgeDefinition:
    """A simple directed edge in the branch graph topology.

    Uses ``from_node`` and ``to_node`` internally to avoid shadowing
    Python's ``from`` keyword. Serializes as ``{"from": ..., "to": ...}``
    to match LangGraph's native format.
    """

    from_node: str
    to_node: str

    def to_dict(self) -> dict[str, Any]:
        return {"from": self.from_node, "to": self.to_node}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EdgeDefinition:
        return cls(
            from_node=data.get("from", data.get("from_node", "")),
            to_node=data.get("to", data.get("to_node", "")),
        )


@dataclass
class ConditionalEdge:
    """A conditional branching edge in the graph topology.

    Maps outcomes to target nodes. The routing function is determined
    by the source node's evaluation at runtime.
    """

    from_node: str
    conditions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"from": self.from_node, "conditions": self.conditions}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConditionalEdge:
        return cls(
            from_node=data.get("from", data.get("from_node", "")),
            conditions=data.get("conditions", {}),
        )


# ═══════════════════════════════════════════════════════════════════════════
# Branch Definition
# ═══════════════════════════════════════════════════════════════════════════


def _reachable_from(
    start: str,
    adjacency: dict[str, set[str]],
) -> set[str]:
    """Return all nodes reachable from ``start`` via BFS."""
    visited: set[str] = set()
    queue = [start]
    while queue:
        node = queue.pop(0)
        if node in visited:
            continue
        visited.add(node)
        for neighbor in adjacency.get(node, ()):
            if neighbor not in visited:
                queue.append(neighbor)
    return visited


def _nodes_that_cannot_terminate(
    graph_nodes: set[str],
    simple: dict[str, set[str]],
    conditional: dict[str, set[str]],
) -> set[str]:
    """Graph nodes whose run cannot finish -- a cycle with no way out.

    The rule follows LangGraph's own edge semantics, which differ between the
    two edge kinds and are the whole reason a single merged "can it reach END"
    walk gets this wrong:

    * A **simple** edge is unconditional, and a node with several of them fans
      out to **all** of them every superstep. So a node terminates only if
      **every** simple successor terminates. One successor reaching END does
      not save it -- the others still fire.
    * A **conditional** edge is a router: exactly **one** target is taken. So
      **any** conditional target that terminates is enough.
    * A node with **no outgoing edge at all terminates**: END is implicit and
      LangGraph halts the run after it.

    Computed as a least fixpoint upward from the terminals, so a cycle -- which
    can never be justified by anything outside itself -- is exactly what is left
    over.

    **Verified against the installed langgraph**, six shapes, `invoke` with
    `recursion_limit=12` as the oracle::

        single node, no edges         terminates      -> accepted
        a -> b, b has no out-edge     terminates      -> accepted
        a -> a, a -> tail             InvalidUpdate   -> a flagged
        a <-> b, a -> tail            InvalidUpdate   -> a, b flagged
        a <-> b, b -> END (simple)    GraphRecursion  -> a, b flagged
        a -> b -> c -> b              GraphRecursion  -> flagged

    History, and why both halves matter. The first two were REFUSED before
    2026-09-30 ("Nodes in cycle without exit condition: n1" for a single node
    with no edges -- live, round 19 of turn
    ``c7d6279d4af74d798375d3f13780140e``), because a reverse walk from END alone
    cannot see an implicit terminal. Crediting implicit terminals fixed that and,
    on its own, admitted rows 3 and 4 (Codex refute, PR #4108): once ``tail``
    terminates, a reverse walk credits everything that can reach it, including a
    node that also loops on itself. Row 5 was accepted by the ORIGINAL code too
    -- a pre-existing false accept, since two simple edges out of ``b`` both fire
    and the loop never ends. Only the per-kind rule above matches the oracle on
    all six.
    """
    terminating = {"END"} | {n for n in graph_nodes if not simple.get(n) and not conditional.get(n)}
    changed = True
    while changed:
        changed = False
        for node in graph_nodes - terminating:
            simple_out = simple.get(node) or set()
            cond_out = conditional.get(node) or set()
            # Unconditional fan-out: every one of them fires.
            if not simple_out <= terminating:
                continue
            # Router: one target is chosen, so one good exit suffices.
            if cond_out and not (cond_out & terminating):
                continue
            terminating.add(node)
            changed = True
    return graph_nodes - terminating


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_VALID_POLICY_TRIGGERS = frozenset({
    "unavailable", "rate_limited", "cost_exceeded", "empty_response",
})


def _validate_checkpoints(
    checkpoints: list[dict[str, Any]], *, node_id: str,
) -> list[str]:
    """Return validation errors for a node's checkpoints list."""
    errors: list[str] = []
    if not checkpoints:
        return errors

    seen_ids: set[str] = set()
    cumulative: float = 0.0
    for i, ckpt in enumerate(checkpoints):
        if not isinstance(ckpt, dict):
            errors.append(
                f"Node '{node_id}' checkpoint[{i}] must be a dict."
            )
            continue
        ckpt_id = ckpt.get("checkpoint_id", "")
        if not ckpt_id:
            errors.append(
                f"Node '{node_id}' checkpoint[{i}] missing 'checkpoint_id'."
            )
        elif ckpt_id in seen_ids:
            errors.append(
                f"Node '{node_id}' duplicate checkpoint_id '{ckpt_id}'."
            )
        else:
            seen_ids.add(ckpt_id)

        fraction = ckpt.get("earns_fraction")
        if fraction is None:
            errors.append(
                f"Node '{node_id}' checkpoint '{ckpt_id or i}' missing 'earns_fraction'."
            )
        else:
            try:
                fv = float(fraction)
            except (TypeError, ValueError):
                errors.append(
                    f"Node '{node_id}' checkpoint '{ckpt_id or i}' "
                    f"earns_fraction must be a number."
                )
                fv = 0.0
            if fv < 0.0 or fv > 1.0:
                errors.append(
                    f"Node '{node_id}' checkpoint '{ckpt_id or i}' "
                    f"earns_fraction {fv!r} must be between 0.0 and 1.0."
                )
            cumulative += fv

        rw = ckpt.get("reached_when")
        if rw is None:
            errors.append(
                f"Node '{node_id}' checkpoint '{ckpt_id or i}' missing 'reached_when'."
            )
        elif not isinstance(rw, dict):
            errors.append(
                f"Node '{node_id}' checkpoint '{ckpt_id or i}' "
                f"reached_when must be a dict."
            )
        elif "state_key" not in rw:
            errors.append(
                f"Node '{node_id}' checkpoint '{ckpt_id or i}' "
                f"reached_when must have a 'state_key'."
            )

    if cumulative > 1.0 + 1e-9:
        errors.append(
            f"Node '{node_id}' checkpoints cumulative earns_fraction "
            f"{cumulative:.4f} exceeds 1.0."
        )

    return errors


def _validate_invoke_output_mapping(
    errors: list[str],
    node_id: str,
    spec_kind: str,
    output_mapping: dict[str, str],
    parent_schema_fields: set[str],
) -> None:
    """Audit gap #7 — confirm each output_mapping target is a parent state
    field. Skips silently when parent schema is empty (no schema declared).
    Mutates ``errors`` in place; matches the surrounding validate() pattern.
    """
    if not parent_schema_fields:
        return  # legacy branches without state_schema — warn-only mode
    for parent_key in (output_mapping or {}):
        if parent_key not in parent_schema_fields:
            errors.append(
                f"Node '{node_id}' {spec_kind} output_mapping target "
                f"'{parent_key}' is not declared in parent branch's "
                f"state_schema."
            )


#: Largest value a SQLite INTEGER column can represent. A budget beyond this
#: is refused at authoring time rather than bound and silently coerced to a
#: float (or crashed on later) — a storage-representation bound, NOT a
#: provider or admission concurrency ceiling.
SQLITE_MAX_INT64 = 2**63 - 1


def validate_concurrency_budget(
    budget: Any, *, context: str = "branch concurrency_budget",
) -> list[str]:
    """Return validation errors for a branch-level concurrency budget.

    The contract is copied verbatim from the per-run override
    (``tinyassets/run_input_runtime.py``): ``type(value) is int and value > 0``.
    ``type(...) is int`` rather than ``isinstance`` is the point — it refuses
    ``bool`` instead of coercing it.

    Why every other value has to be refused here: ``ConcurrencyTracker`` does
    ``threading.Semaphore(budget) if budget else None``, so ``0`` silently
    means *unbounded* while reporting a budget of 0, ``True`` silently means
    ``Semaphore(1)``, and ``-1`` / ``"4"`` raise inside compile long after the
    author has left. There is deliberately **no ceiling**: usage limits belong
    at admission, not in a field validator.
    """
    if type(budget) is not int:  # noqa: E721 — bool must be refused, not coerced
        return [
            f"{context}: must be a positive integer, got "
            f"{type(budget).__name__}. Booleans, floats and numeric strings "
            "are refused rather than coerced. Omit the field entirely to "
            "leave the branch unbounded."
        ]
    if budget <= 0:
        return [
            f"{context}: must be a positive integer greater than 0, got "
            f"{budget}. A budget of 0 or less would run unbounded or crash at "
            "compile time rather than limiting anything. Omit the field "
            "entirely to leave the branch unbounded."
        ]
    if budget > SQLITE_MAX_INT64:
        return [
            f"{context}: {budget} is larger than the maximum integer this "
            f"platform can store ({SQLITE_MAX_INT64}). This is a storage "
            "representation limit, not a concurrency cap — ordinary positive "
            "budgets are not limited by any platform constant."
        ]
    return []


def _validate_llm_policy_shape(
    policy: dict[str, Any], *, context: str,
) -> list[str]:
    """Return validation errors for an llm_policy dict.

    Called from ``BranchDefinition.validate()`` for both node-level
    and branch-level policy dicts. Validates known keys without being
    over-strict about unknown keys (forward-compat).
    """
    errors: list[str] = []
    if not isinstance(policy, dict):
        return [f"{context}: llm_policy must be a dict, got {type(policy).__name__}."]

    # `preferred_provider` is NOT forward-compat slack: the runtime reads only
    # policy["preferred"]["provider"], so this key is silently ignored and the run
    # dies later with provider_not_bound. It reached branches because two of our own
    # tool descriptions told agents to write it (live 2026-08-26: the founder's X
    # post failed twice this way). Catch it here, where the fix is obvious.
    if "preferred_provider" in policy:
        errors.append(
            f"{context}: 'preferred_provider' is not a policy key and is ignored at "
            "run time (the run then fails with provider_not_bound). Either omit the "
            "policy entirely so the run uses whatever provider your command center serves "
            "- the usual choice - or pin one by NAME as "
            "{'preferred': {'provider': 'codex'}} (a provdef_... definition id is "
            "not a provider name)."
        )

    preferred = policy.get("preferred")
    if preferred is not None:
        if not isinstance(preferred, dict):
            errors.append(f"{context}: 'preferred' must be a dict.")
        elif "provider" not in preferred:
            errors.append(f"{context}: 'preferred' must have a 'provider' key.")

    fallback_chain = policy.get("fallback_chain")
    if fallback_chain is not None:
        if not isinstance(fallback_chain, list):
            errors.append(f"{context}: 'fallback_chain' must be a list.")
        else:
            for i, entry in enumerate(fallback_chain):
                if not isinstance(entry, dict):
                    errors.append(
                        f"{context}: fallback_chain[{i}] must be a dict."
                    )
                    continue
                if "provider" not in entry:
                    errors.append(
                        f"{context}: fallback_chain[{i}] must have a 'provider' key."
                    )
                trigger = entry.get("trigger")
                if trigger is not None and trigger not in _VALID_POLICY_TRIGGERS:
                    errors.append(
                        f"{context}: fallback_chain[{i}] trigger {trigger!r} "
                        f"must be one of {sorted(_VALID_POLICY_TRIGGERS)}."
                    )

    difficulty_override = policy.get("difficulty_override")
    if difficulty_override is not None:
        if not isinstance(difficulty_override, list):
            errors.append(f"{context}: 'difficulty_override' must be a list.")
        else:
            for i, entry in enumerate(difficulty_override):
                if not isinstance(entry, dict):
                    errors.append(
                        f"{context}: difficulty_override[{i}] must be a dict."
                    )
                    continue
                if "if_difficulty" not in entry:
                    errors.append(
                        f"{context}: difficulty_override[{i}] must have 'if_difficulty'."
                    )
                use = entry.get("use")
                if use is None or not isinstance(use, dict) or "provider" not in use:
                    errors.append(
                        f"{context}: difficulty_override[{i}] 'use' must be a "
                        f"dict with a 'provider' key."
                    )

    return errors


@dataclass
class BranchDefinition:
    """A complete community-designed graph topology.

    A branch definition is the unit of sharing and forking. It contains:

    - Identity and metadata (name, author, domain_id, tags, version).
    - Branch-carried skill snapshots users create, remix, or copy.
    - A full graph topology as embedded JSON (nodes + edges).
    - A state schema as an unvalidated JSON blob (formal validation
      deferred to Phase 3).
    - Fork lineage (parent_def_id) for tracking provenance.
    - Publication state and aggregate stats.

    The graph JSON is stored as a single blob in SQLite rather than
    normalized across tables. This makes fork/clone/export atomic —
    one row = one complete topology.
    """

    branch_def_id: str = field(default_factory=_new_id)
    name: str = ""
    description: str = ""
    author: str = ""
    domain_id: str = "workflow"
    # Phase 5: optional Goal binding. Empty string means no Goal. A
    # Goal captures the intent a Branch serves; many Branches can bind
    # to one Goal for discovery and leaderboards.
    goal_id: str = ""
    tags: list[str] = field(default_factory=list)
    version: int = 1
    skills: list[dict[str, Any]] = field(default_factory=list)

    # Fork lineage
    parent_def_id: str | None = None
    # Publish-version lineage — points to the branch_version_id this was forked from.
    fork_from: str | None = None

    # Graph topology — stored as embedded JSON in LangGraph-native shape
    graph_nodes: list[GraphNodeRef] = field(default_factory=list)
    edges: list[EdgeDefinition] = field(default_factory=list)
    conditional_edges: list[ConditionalEdge] = field(default_factory=list)
    entry_point: str = ""

    # Node definitions — the actual node implementations
    node_defs: list[NodeDefinition] = field(default_factory=list)

    # State schema — unvalidated JSON blob for now (Phase 3 will use StateFieldDecl)
    state_schema: list[dict[str, Any]] = field(default_factory=list)

    # Optional declarations travel inside graph JSON, with no second schema.
    # Absent remains absent so legacy immutable snapshot hashes do not change.
    io_manifest: dict[str, Any] | None = None

    # Publication
    published: bool = False
    # Phase 6.2.2 — visibility mirrors the Goals visibility pattern.
    # Default is public; users opt into private explicitly. Private
    # Branches are hidden from non-owner callers in gate-claim listings
    # and leaderboards. Normalized to 'public'/'private' at the SQLite
    # layer (see ``daemon_server.save_branch_definition``).
    visibility: str = "public"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    # Aggregate stats (updated by the system, not by users)
    stats: dict[str, Any] = field(default_factory=lambda: {
        "fork_count": 0,
        "run_count": 0,
        "avg_quality_score": 0.0,
    })

    # Branch-level LLM policy default. Applied to nodes that have no
    # node-level llm_policy set. Same shape as NodeDefinition.llm_policy.
    # When None, falls back to the global role-based routing.
    default_llm_policy: dict[str, Any] | None = None

    # Max concurrent executing nodes per run. None = unbounded (current behavior).
    # When set, a semaphore limits simultaneous LLM/sandbox calls within a run.
    concurrency_budget: int | None = None

    # ── Serialization ──────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict suitable for JSON storage."""
        return {
            "branch_def_id": self.branch_def_id,
            "name": self.name,
            "description": self.description,
            "author": self.author,
            "domain_id": self.domain_id,
            "goal_id": self.goal_id,
            "tags": self.tags,
            "version": self.version,
            "skills": normalize_branch_skill_snapshots(self.skills),
            "parent_def_id": self.parent_def_id,
            "fork_from": self.fork_from,
            "entry_point": self.entry_point,
            "published": self.published,
            "visibility": self.visibility,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stats": self.stats,
            "graph_nodes": [n.to_dict() for n in self.graph_nodes],
            "edges": [e.to_dict() for e in self.edges],
            "conditional_edges": [c.to_dict() for c in self.conditional_edges],
            "node_defs": [n.to_dict() for n in self.node_defs],
            "state_schema": self.state_schema,
            "default_llm_policy": self.default_llm_policy,
            "concurrency_budget": self.concurrency_budget,
            **({"io_manifest": normalize_branch_io_manifest(self.io_manifest)}
               if self.io_manifest is not None else {}),
        }

    def to_json(self) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BranchDefinition:
        """Deserialize from a plain dict.

        Accepts three formats:
        1. **Canonical**: graph_nodes, edges, conditional_edges, node_defs
           (from ``to_dict()``).
        2. **DB row**: ``graph`` nested dict (from ``_branch_def_from_row``),
           with ``node_defs`` alongside.
        3. **Legacy**: ``nodes`` as a flat node list stored in graph_json.

        Does not mutate the input dict.
        """
        data = dict(data)  # shallow copy to avoid mutating caller's dict

        # DB row format: extract from nested "graph" dict
        graph_blob = data.pop("graph", None)
        if isinstance(graph_blob, dict):
            # Only use graph_blob values if the flat keys aren't present
            if "graph_nodes" not in data:
                data.setdefault("graph_nodes", graph_blob.get("nodes", []))
            if "edges" not in data:
                data.setdefault("edges", graph_blob.get("edges", []))
            if "conditional_edges" not in data:
                data.setdefault(
                    "conditional_edges",
                    graph_blob.get("conditional_edges", []),
                )
            if "entry_point" not in data and graph_blob.get("entry_point"):
                data["entry_point"] = graph_blob["entry_point"]
            if "io_manifest" not in data and "io_manifest" in graph_blob:
                data["io_manifest"] = graph_blob["io_manifest"]

        # Extract nested structures before filtering
        graph_nodes_raw = data.pop("graph_nodes", [])
        edges_raw = data.pop("edges", [])
        cond_edges_raw = data.pop("conditional_edges", [])
        node_defs_raw = data.pop("node_defs", [])
        state_schema_raw = data.pop("state_schema", [])
        skills_raw = data.pop("skills", [])
        manifest_raw = normalize_branch_io_manifest(data.pop("io_manifest", None))

        # Legacy compat: "nodes" key from old format becomes node_defs
        legacy_nodes = data.pop("nodes", [])
        if legacy_nodes and not node_defs_raw:
            node_defs_raw = legacy_nodes

        # Filter to known fields only
        known = cls.__dataclass_fields__
        filtered = {k: v for k, v in data.items() if k in known}

        branch = cls(**filtered)
        branch.graph_nodes = [GraphNodeRef.from_dict(n) for n in graph_nodes_raw]
        branch.edges = [EdgeDefinition.from_dict(e) for e in edges_raw]
        branch.conditional_edges = [ConditionalEdge.from_dict(c) for c in cond_edges_raw]
        branch.node_defs = [NodeDefinition.from_dict(n) for n in node_defs_raw]
        branch.state_schema = state_schema_raw
        branch.io_manifest = manifest_raw
        branch.skills = normalize_branch_skill_snapshots(skills_raw)
        return branch

    @classmethod
    def from_json(cls, raw: str) -> BranchDefinition:
        """Deserialize from a JSON string."""
        return cls.from_dict(json.loads(raw))

    # ── Graph topology helpers ─────────────────────────────────────────

    def graph_json(self) -> dict[str, Any]:
        """Return the graph topology in LangGraph-native JSON shape."""
        return {
            "nodes": [n.to_dict() for n in self.graph_nodes],
            "edges": [e.to_dict() for e in self.edges],
            "conditional_edges": [c.to_dict() for c in self.conditional_edges],
            "entry_point": self.entry_point,
            **({"io_manifest": normalize_branch_io_manifest(self.io_manifest)}
               if self.io_manifest is not None else {}),
        }

    def state_schema_json(self) -> list[dict[str, Any]]:
        """Return the state schema as a JSON-serializable list."""
        return list(self.state_schema)

    def node_def_ids(self) -> list[str]:
        """Return all node definition IDs in this branch."""
        return [n.node_id for n in self.node_defs]

    def get_node_def(self, node_id: str) -> NodeDefinition | None:
        """Look up a node definition by ID."""
        for n in self.node_defs:
            if n.node_id == node_id:
                return n
        return None

    def validate(self) -> list[str]:
        """Validate the branch definition for consistency.

        Checks:
        - Name is required.
        - At least one node exists.
        - Entry point is required and references a defined node.
        - No duplicate node IDs (graph nodes or node defs).
        - All edge/conditional_edge references point to defined nodes.
        - Graph node refs point to valid node definitions.
        - No orphaned nodes (nodes not reachable from entry point).
        - No cycles without an exit condition (cycles that never reach END).
        - State schema field names are unique.

        Returns a list of error messages (empty if valid).
        """
        errors: list[str] = []

        if not self.name:
            errors.append("Branch name is required.")

        has_nodes = bool(self.node_defs or self.graph_nodes)
        if not has_nodes:
            errors.append("Branch must have at least one node.")

        # Check for duplicate node definition IDs
        seen_defs: set[str] = set()
        for n in self.node_defs:
            if n.node_id in seen_defs:
                errors.append(f"Duplicate node definition ID: '{n.node_id}'.")
            seen_defs.add(n.node_id)

        # Check for duplicate graph node IDs
        seen_graph: set[str] = set()
        for n in self.graph_nodes:
            if n.id in seen_graph:
                errors.append(f"Duplicate graph node ID: '{n.id}'.")
            seen_graph.add(n.id)

        # Collect all valid node IDs (graph nodes + reserved)
        all_node_ids = seen_graph | RESERVED_NODE_IDS

        # Entry point is required when the branch has nodes
        if has_nodes and not self.entry_point:
            errors.append("Entry point is required when branch has nodes.")
        elif self.entry_point and self.entry_point not in all_node_ids:
            errors.append(
                f"Entry point '{self.entry_point}' is not a defined node."
            )

        # Check graph node refs point to valid node_defs
        for gn in self.graph_nodes:
            if gn.node_def_id and gn.node_def_id not in seen_defs:
                errors.append(
                    f"Graph node '{gn.id}' references unknown "
                    f"node_def_id '{gn.node_def_id}'."
                )

        # Check edge references and build adjacency for reachability.
        # ``adjacency`` merges both kinds (reachability does not care which edge
        # got you there); ``simple_out``/``cond_out`` keep them apart, because
        # termination DOES care: a simple edge always fires, a conditional edge
        # is one branch of a router. See ``_nodes_that_cannot_terminate``.
        adjacency: dict[str, set[str]] = {}
        simple_out: dict[str, set[str]] = {}
        cond_out: dict[str, set[str]] = {}
        for e in self.edges:
            if e.from_node not in all_node_ids:
                errors.append(
                    f"Edge 'from' node '{e.from_node}' is not defined."
                )
            if e.to_node not in all_node_ids:
                errors.append(
                    f"Edge 'to' node '{e.to_node}' is not defined."
                )
            adjacency.setdefault(e.from_node, set()).add(e.to_node)
            simple_out.setdefault(e.from_node, set()).add(e.to_node)

        # Check conditional edge references
        for ce in self.conditional_edges:
            if ce.from_node not in all_node_ids:
                errors.append(
                    f"Conditional edge 'from' node '{ce.from_node}' is not defined."
                )
            for target in ce.conditions.values():
                if target not in all_node_ids:
                    errors.append(
                        f"Conditional edge target '{target}' from "
                        f"'{ce.from_node}' is not defined."
                    )
                adjacency.setdefault(ce.from_node, set()).add(target)
                cond_out.setdefault(ce.from_node, set()).add(target)

        # Orphan detection: check all graph nodes are reachable from entry point
        if self.entry_point and seen_graph:
            reachable = _reachable_from(self.entry_point, adjacency)
            # Also count nodes reachable from START
            reachable |= _reachable_from("START", adjacency)
            for gn in self.graph_nodes:
                if gn.id not in reachable:
                    errors.append(
                        f"Graph node '{gn.id}' is not reachable from "
                        f"entry point '{self.entry_point}'."
                    )

        # Cycle detection: check that every node's run can actually finish
        if seen_graph and not errors:
            # Only run if no structural errors so adjacency is valid
            cycle_nodes = _nodes_that_cannot_terminate(
                seen_graph, simple_out, cond_out
            )
            if cycle_nodes:
                errors.append(
                    f"Nodes in cycle without exit condition: "
                    f"{', '.join(sorted(cycle_nodes))}. A plain edge always "
                    "fires, so a loop needs a conditional edge with an 'END' "
                    "target to leave it; a second plain edge to END does not "
                    "end the loop, it runs alongside it."
                )

        # Check state schema field names are unique (basic check on raw dicts)
        # and do not collide with LangGraph node names. StateGraph rejects
        # adding a node whose name is already present in the state schema.
        field_names: set[str] = set()
        for f in self.state_schema:
            name = f.get("name", "")
            if name:
                if name in field_names:
                    errors.append(f"Duplicate state field name: '{name}'.")
                field_names.add(name)
                if name in all_node_ids:
                    errors.append(
                        f"State field name '{name}' collides with a graph "
                        "node ID. Rename the state field or node before "
                        "running this branch."
                    )

        # LangGraph's synthesized TypedDict is built from state_schema.
        # If a branch declares a partial schema, any undeclared initial
        # inputs or node outputs can be dropped at runtime. Keep empty-schema
        # legacy branches in warn-only mode, but make partial schemas exact.
        if field_names:
            for n in self.node_defs:
                for key in n.input_keys or []:
                    if key not in field_names:
                        errors.append(
                            f"Node '{n.node_id}' input_key '{key}' is not "
                            "declared in state_schema. Add a state_schema "
                            "field so run_branch preserves this input at "
                            "runtime."
                        )
                for key in n.output_keys or []:
                    if key not in field_names:
                        errors.append(
                            f"Node '{n.node_id}' output_key '{key}' is not "
                            "declared in state_schema. Add a state_schema "
                            "field so run_branch preserves this output at "
                            "runtime."
                        )

        try:
            normalize_branch_skill_snapshots(self.skills)
        except ValueError as exc:
            errors.append(str(exc))

        # Build-time placeholder validation: every ``{ident}`` in a
        # node's prompt_template must resolve via the node's
        # input_keys OR the branch-level state_schema. Runtime
        # ``CompilerError`` is the second layer; this is the first.
        # Escaped ``\\{ident\\}`` forms are literal output and are
        # excluded by the regex lookbehind in ``_template_placeholders``.
        for n in self.node_defs:
            if not n.prompt_template:
                continue
            declared = set(n.input_keys or []) | field_names
            for placeholder in _template_placeholders(n.prompt_template):
                if placeholder not in declared:
                    errors.append(
                        f"Node '{n.node_id}' prompt_template references "
                        f"'{{{placeholder}}}' but it is not declared in "
                        f"input_keys or state_schema. Add it to one, or "
                        f"escape it as '\\{{{placeholder}\\}}' for a "
                        f"literal brace."
                    )

        # llm_policy shape validation (node-level and branch default)
        if self.default_llm_policy is not None:
            policy_errors = _validate_llm_policy_shape(
                self.default_llm_policy, context="branch default_llm_policy",
            )
            errors.extend(policy_errors)

        # Branch-level concurrency budget. One check for every authoring
        # surface: build, patch and compile all arrive through validate().
        if self.concurrency_budget is not None:
            errors.extend(validate_concurrency_budget(self.concurrency_budget))

        for n in self.node_defs:
            if n.llm_policy is not None:
                policy_errors = _validate_llm_policy_shape(
                    n.llm_policy, context=f"node '{n.node_id}' llm_policy",
                )
                errors.extend(policy_errors)

        for n in self.node_defs:
            if n.checkpoints:
                errors.extend(_validate_checkpoints(n.checkpoints, node_id=n.node_id))

        # Pre-build the parent state-schema field set for output_mapping
        # validation (Task #76b — audit gap #7). Empty schema = warn-only;
        # validate-time output_mapping check is best-effort when schema is
        # unset (some legacy branches don't declare state_schema).
        _parent_schema_fields = {
            f.get("name") for f in (self.state_schema or []) if f.get("name")
        }

        for n in self.node_defs:
            if n.invoke_branch_spec is not None:
                spec = n.invoke_branch_spec
                if not spec.get("branch_def_id"):
                    errors.append(
                        f"Node '{n.node_id}' invoke_branch_spec missing 'branch_def_id'."
                    )
                wait_mode = spec.get("wait_mode", "blocking")
                if wait_mode not in ("blocking", "async"):
                    errors.append(
                        f"Node '{n.node_id}' invoke_branch_spec wait_mode must be "
                        f"'blocking' or 'async', got '{wait_mode}'."
                    )
                if n.prompt_template or n.source_code:
                    errors.append(
                        f"Node '{n.node_id}' has invoke_branch_spec and also "
                        f"prompt_template/source_code — these are mutually exclusive."
                    )
                # Task #76b — output_mapping schema check (audit gap #7).
                _validate_invoke_output_mapping(
                    errors, n.node_id, "invoke_branch_spec",
                    spec.get("output_mapping") or {},
                    _parent_schema_fields,
                )

            # Task #76a: invoke_branch_version_spec sibling validation.
            if n.invoke_branch_version_spec is not None:
                vspec = n.invoke_branch_version_spec
                if not vspec.get("branch_version_id"):
                    errors.append(
                        f"Node '{n.node_id}' invoke_branch_version_spec missing "
                        f"'branch_version_id'."
                    )
                v_wait_mode = vspec.get("wait_mode", "blocking")
                if v_wait_mode not in ("blocking", "async"):
                    errors.append(
                        f"Node '{n.node_id}' invoke_branch_version_spec wait_mode "
                        f"must be 'blocking' or 'async', got '{v_wait_mode}'."
                    )
                v_on_fail = vspec.get("on_child_fail", "propagate")
                if v_on_fail not in ("propagate", "default", "retry"):
                    errors.append(
                        f"Node '{n.node_id}' invoke_branch_version_spec "
                        f"on_child_fail must be 'propagate' | 'default' | "
                        f"'retry', got '{v_on_fail}'."
                    )
                if n.prompt_template or n.source_code:
                    errors.append(
                        f"Node '{n.node_id}' has invoke_branch_version_spec and "
                        f"also prompt_template/source_code — these are mutually "
                        f"exclusive."
                    )
                if n.invoke_branch_spec is not None:
                    errors.append(
                        f"Node '{n.node_id}' has both invoke_branch_spec and "
                        f"invoke_branch_version_spec — these are mutually "
                        f"exclusive (use one or the other)."
                    )
                # Task #76b — output_mapping schema check (audit gap #7).
                _validate_invoke_output_mapping(
                    errors, n.node_id, "invoke_branch_version_spec",
                    vspec.get("output_mapping") or {},
                    _parent_schema_fields,
                )

            if n.await_run_spec is not None:
                spec = n.await_run_spec
                if not spec.get("run_id_field"):
                    errors.append(
                        f"Node '{n.node_id}' await_run_spec missing 'run_id_field'."
                    )
                if n.prompt_template or n.source_code:
                    errors.append(
                        f"Node '{n.node_id}' has await_run_spec and also "
                        f"prompt_template/source_code — these are mutually exclusive."
                    )

        return errors

    def fork(
        self,
        new_name: str = "",
        author: str = "",
    ) -> BranchDefinition:
        """Create a forked copy of this branch definition.

        The fork gets a new ID, resets version to 1, and records
        this branch as its parent for lineage tracking.
        """
        forked = BranchDefinition.from_dict(self.to_dict())
        forked.branch_def_id = _new_id()
        forked.name = new_name or f"{self.name} (fork)"
        forked.author = author
        forked.version = 1
        forked.parent_def_id = self.branch_def_id
        forked.published = False
        forked.created_at = _now_iso()
        forked.updated_at = _now_iso()
        forked.stats = {"fork_count": 0, "run_count": 0, "avg_quality_score": 0.0}
        return forked
