"""NodeBid executor (Phase G).

Executes a single approved node against user-supplied inputs, with
no universe state piped in. Writes a JSON output artifact under
``<output_dir>/bid_outputs/<node_bid_id>/output.json`` and reports
an ``evidence_url`` pointing at that file.

Safety model:
- Node must be ``approved=True`` (host-reviewed).
- Source is scanned for an expanded ``_BID_DANGEROUS_PATTERNS`` list
  on top of the compile-time check in ``graph_compiler``.
- The ``node_lookup_fn`` is injected by the caller — this module has
  no dependency on the universe_server or branch registry.

v1 scope: prompt_template nodes are NOT executed here (that would
need the provider stack). Only source_code nodes with a ``run(state)``
entry point are supported; prompt_template-only nodes return
``status="failed"`` with a clear error.
"""

from __future__ import annotations

import hashlib
import json
import logging
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from tinyassets.bid.node_bid import NodeBid

logger = logging.getLogger(__name__)


# Preflight §4.1 #5d single source of truth: the stricter bid-market
# pattern list lives at ``tinyassets.graph_compiler._BID_DANGEROUS_PATTERNS``.
# Both the producer and the executor re-validate against it (invariant 1
# requires both boundaries). Use the full name throughout this module so
# readers don't mistake the reference for the narrower
# ``_DANGEROUS_PATTERNS`` used by ``_build_source_code_node``.
from tinyassets.graph_compiler import _BID_DANGEROUS_PATTERNS  # noqa: E402


@dataclass
class NodeBidResult:
    node_bid_id: str
    status: str  # "succeeded" | "failed"
    output: dict = field(default_factory=dict)
    evidence_url: str = ""
    error: str = ""


def _strip_producer_keys(inputs: dict) -> dict:
    """Remove ``__``-prefixed producer-internal keys before passing
    inputs to the node body."""
    return {k: v for k, v in inputs.items() if not str(k).startswith("__")}


def _scan_dangerous_patterns(source: str) -> str:
    """Return the offending pattern or empty string."""
    for pattern in _BID_DANGEROUS_PATTERNS:
        if pattern in source:
            return pattern
    return ""


def execute_node_bid(
    bid: NodeBid,
    *,
    node_lookup_fn: Callable[[str], object | None],
    output_dir: Path,
) -> NodeBidResult:
    """Execute a single NodeBid. Never raises — all failures return a
    ``NodeBidResult`` with ``status="failed"``.

    Parameters
    ----------
    bid:
        The NodeBid to execute.
    node_lookup_fn:
        Callable ``(node_def_id: str) -> NodeDefinition | None``. The
        caller controls how nodes are resolved (registry, branch,
        discovery) — keeps this module dependency-free.
    output_dir:
        Universe output directory. The result artifact is written
        under ``<output_dir>/bid_outputs/<bid_id>/output.json``.
    """
    node_bid_id = bid.node_bid_id
    artifact_dir = Path(output_dir) / "bid_outputs" / node_bid_id
    try:
        node = node_lookup_fn(bid.node_def_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("node_bid: lookup raised for %s: %s", bid.node_def_id, exc)
        return NodeBidResult(
            node_bid_id=node_bid_id,
            status="failed",
            error=f"node_lookup_error: {exc}",
        )

    if node is None:
        return NodeBidResult(
            node_bid_id=node_bid_id,
            status="failed",
            error=f"node_not_found: {bid.node_def_id}",
        )

    # Approval gate — required.
    approved = bool(getattr(node, "approved", False))
    if not approved:
        return NodeBidResult(
            node_bid_id=node_bid_id,
            status="failed",
            error=f"unapproved_node: {bid.node_def_id}",
        )

    source = getattr(node, "source_code", "") or ""
    prompt_template = getattr(node, "prompt_template", "") or ""

    # SECURITY (PR #1349, Codex final residual): FAIL-CLOSED hash provenance.
    # This is a second runtime code-execution gate (it ``exec()``s the source
    # directly, bypassing graph_compiler._validate_source_code). The same
    # invariant must hold: a source_code node executes only when
    # ``approved=True`` AND ``approved_source_hash`` is present AND equals
    # sha256(effective source). A bare ``approved=True`` with an empty/stale
    # hash is forged, carried-from-elsewhere, or pre-provenance and must be
    # refused. Checked here (before the dangerous-pattern scan and exec) so a
    # carried snapshot can never reach exec() on the bid path either.
    if source:
        approved_hash = (getattr(node, "approved_source_hash", "") or "").strip()
        actual_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        if not approved_hash or approved_hash != actual_hash:
            return NodeBidResult(
                node_bid_id=node_bid_id,
                status="failed",
                error=(
                    f"approval_provenance_invalid: {bid.node_def_id} is marked "
                    "approved but its approved_source_hash is missing or does "
                    "not match the current source (approval is forged, stale, "
                    "or pre-provenance). Re-approve against the current source."
                ),
            )

    if not source and prompt_template:
        return NodeBidResult(
            node_bid_id=node_bid_id,
            status="failed",
            error=(
                "prompt_template_nodes_not_supported: NodeBid execution "
                "v1 only supports source_code nodes with a run(state) entry"
            ),
        )
    if not source:
        return NodeBidResult(
            node_bid_id=node_bid_id,
            status="failed",
            error="node_has_no_source_code",
        )

    offending = _scan_dangerous_patterns(source)
    if offending:
        return NodeBidResult(
            node_bid_id=node_bid_id,
            status="failed",
            error=f"dangerous_pattern:{offending}",
        )

    # Strip producer-internal keys before exposing to node body.
    user_inputs = _strip_producer_keys(dict(bid.inputs or {}))

    # Execute source — define runner, call run(state). BUG-112: a SINGLE
    # namespace (globals == locals) so top-level helpers are visible to run();
    # split globals/locals puts defs in locals but their __globals__ is the
    # globals dict, so run() can't see sibling helpers -> NameError.
    namespace: dict = {"__builtins__": __builtins__}
    try:
        exec(source, namespace)  # noqa: S102 — approved source, pattern-scanned
    except Exception as exc:  # noqa: BLE001
        return NodeBidResult(
            node_bid_id=node_bid_id,
            status="failed",
            error=f"source_load_error: {exc}",
        )

    runner = namespace.get("run")
    if not callable(runner):
        return NodeBidResult(
            node_bid_id=node_bid_id,
            status="failed",
            error="source_missing_run_callable",
        )

    try:
        result = runner(dict(user_inputs))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "node_bid: run raised for %s: %s\n%s",
            node_bid_id, exc, traceback.format_exc(),
        )
        return NodeBidResult(
            node_bid_id=node_bid_id,
            status="failed",
            error=f"run_error: {exc}",
        )

    if not isinstance(result, dict):
        return NodeBidResult(
            node_bid_id=node_bid_id,
            status="failed",
            error=f"run_returned_non_dict: {type(result).__name__}",
        )

    # Write artifact.
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        out_file = artifact_dir / "output.json"
        out_file.write_text(
            json.dumps(result, indent=2, default=str),
            encoding="utf-8",
        )
        evidence_url = f"file://{out_file.resolve().as_posix()}"
    except Exception as exc:  # noqa: BLE001
        return NodeBidResult(
            node_bid_id=node_bid_id,
            status="failed",
            error=f"artifact_write_error: {exc}",
            output=result,
        )

    return NodeBidResult(
        node_bid_id=node_bid_id,
        status="succeeded",
        output=result,
        evidence_url=evidence_url,
    )
