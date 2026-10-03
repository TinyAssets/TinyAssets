"""Branch authoring + node CRUD subsystem — extracted from
``tinyassets/universe_server.py`` (Task #15 — decomp Step 8).

The largest single submodule extracted from the monolith: 18 ``_ext_branch_*``
handlers, the ``_action_fork_tree`` action
handlers, the build/patch composite engine (``_ext_branch_build`` /
``_ext_branch_patch`` / ``_ext_branch_update_node`` / ``_ext_branch_patch_nodes``),
the node-spec resolver + apply machinery (``_resolve_node_spec``,
``_apply_node_spec``, ``_apply_edge_spec``, ``_apply_conditional_edge_spec``,
``_apply_state_field_spec``, ``_apply_patch_op``, ``_lookup_node_body``,
``_staged_branch_from_spec``), the wiki-cross-reference helper group
(``_related_wiki_pages``, ``_related_summary``, ``_RELATED_WIKI_CAP``,
``_RELATED_SUMMARY_MAX``), the mermaid renderer (``_branch_mermaid``,
``_mermaid_node_id``, ``_mermaid_label``), the ``_BRANCH_ACTIONS`` /
``_BRANCH_WRITE_ACTIONS`` dispatch surface, the ``_dispatch_branch_action``
ledger-aware dispatcher, the ``_resolve_branch_id`` / ``_resolve_udir``
resolvers, the bulk-patch coercer (``_coerce_patch_nodes_value``,
``_PATCH_NODES_FIELDS``), the build-summary text composer
(``_build_branch_text``, ``_suggest_entry_point``, ``_closest_state_type``,
``_errors_to_suggestions``, ``_VALID_STATE_TYPES``), and the branch-design
guide markdown body (``_BRANCH_DESIGN_GUIDE``,
``_branch_design_guide_prompt``).

The ``@mcp.prompt("Branch Design Guide")`` decoration stays in
``tinyassets/universe_server.py`` (Pattern A2) so FastMCP introspection
sees the chatbot-facing signature exactly as before. The
``branch_design_guide()`` wrapper there delegates to
``_branch_design_guide_prompt()`` from this module.

Public surface (back-compat re-exported via ``tinyassets.universe_server``):
    _BRANCH_ACTIONS                : dispatch table (18 handlers)
    _BRANCH_WRITE_ACTIONS          : frozenset of write actions for ledger gating
    _RELATED_WIKI_CAP              : cap on related-wiki page list
    _dispatch_branch_action        : ledger-aware dispatcher
    _ext_branch_*                  : 15 individual handlers
    _action_fork_tree              : ancestor + descendant lineage walk
    _resolve_branch_id             : branch-name → branch_def_id resolver
    _resolve_node_spec             : node-spec resolver (node_ref / inline)
    _resolve_udir                  : universe-dir resolver
    _related_summary               : first-paragraph summary helper
    _related_wiki_pages            : wiki cross-reference scan
    _branch_mermaid                : flowchart renderer
    _mermaid_node_id, _mermaid_label : mermaid escape helpers
    _build_branch_text             : composite build text composer
    _suggest_entry_point           : entry-point inference helper
    _closest_state_type            : state-type fuzzy match
    _errors_to_suggestions         : validation-error → fix-hint mapper
    _staged_branch_from_spec       : spec → staging-BranchDefinition
    _apply_node_spec, _apply_edge_spec, _apply_conditional_edge_spec,
    _apply_state_field_spec, _apply_patch_op : per-spec applicators
    _lookup_node_body              : node_ref body lookup (standalone or branch)
    _coerce_patch_nodes_value      : bulk-patch type coercer
    _PATCH_NODES_FIELDS            : whitelisted bulk-patch field map
    _split_csv, _coerce_node_keys  : input shape helpers
    _append_global_ledger          : branch-attribution ledger writer
    _ensure_workflow_db            : lazy SQLite schema bootstrap
    _BRANCH_DESIGN_GUIDE           : prompt body markdown
    _branch_design_guide_prompt    : prompt-body accessor for the
                                      universe_server.py @mcp.prompt wrapper

Cross-module note: ``_current_actor``, ``_truncate``, ``_append_ledger``,
``_storage_backend``, ``_format_dirty_file_conflict``, ``_format_commit_failed``,
``_load_nodes``, ``VALID_PHASES``, ``logger`` all live in ``tinyassets.universe_server``
(universe-engine territory) and are lazy-imported inside the functions that use
them. This avoids the load-time cycle (universe_server back-compat-imports
symbols from this module). ``_gates_enabled`` is also lazy-imported, but from
``tinyassets.api.market`` (its real home post-Step-7).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tinyassets.api.helpers import (
    _base_path,
    _find_all_pages,
    _read_text,
    _universe_dir,
    _wiki_drafts_dir,
    _wiki_pages_dir,
)
from tinyassets.api.wiki import (
    _page_rel_path,
    _parse_frontmatter,
)
from tinyassets.catalog import CommitFailedError, DirtyFileError

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────
# Community Branches: author/edit BranchDefinition over MCP
# ───────────────────────────────────────────────────────────────────────────
# Branches are domain-agnostic graph topologies that live in the same SQLite
# backing store as the rest of the multiplayer substrate (base_path /
# .tinyassets.db, table branch_definitions). Each write action appends to
# the global ledger at base_path / "ledger.json" for public attribution —
# branches are not scoped to a universe, so the ledger target is the global
# base_path rather than a per-universe directory.


def _split_csv(text: str) -> list[str]:
    return [p.strip() for p in text.split(",") if p.strip()]


def _coerce_node_keys(
    value: Any, field_name: str,
) -> tuple[list[str], str]:
    """Coerce input_keys / output_keys to list[str], or return an error.

    Accepts list[str], JSON-encoded list strings (e.g. '["a","b"]'),
    CSV strings ("a, b, c"), and bare single tokens ("a"). Rejects
    anything else — in particular, naked iteration over an un-parsed
    string like "node.output" was silently yielding a per-character
    list, which then validated as a node spec but was unrunnable.

    Returns (keys, error). On success error is "". On failure keys is
    [] and error is a human-readable reason.
    """
    if value is None:
        return [], ""
    if isinstance(value, list):
        out: list[str] = []
        for idx, item in enumerate(value):
            if not isinstance(item, str):
                return [], (
                    f"{field_name}[{idx}] must be a string, got "
                    f"{type(item).__name__}"
                )
            trimmed = item.strip()
            if not trimmed:
                return [], f"{field_name}[{idx}] is empty"
            out.append(trimmed)
        return out, ""
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return [], ""
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                return [], (
                    f"{field_name} looks like JSON but did not parse: {exc}"
                )
            if not isinstance(parsed, list):
                return [], (
                    f"{field_name} JSON must decode to a list, got "
                    f"{type(parsed).__name__}"
                )
            return _coerce_node_keys(parsed, field_name)
        # CSV path — also handles the bare single-token case.
        return [p.strip() for p in raw.split(",") if p.strip()], ""
    return [], (
        f"{field_name} must be a list or string, got "
        f"{type(value).__name__}"
    )


def _append_global_ledger(
    action: str,
    *,
    actor: str,
    target: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append a branch-authoring ledger entry at base_path/ledger.json.

    Branch definitions are global artifacts (not scoped to a universe), so the
    ledger target is the base_path rather than a universe directory. Never
    raises: failures are logged but don't roll back the mutation.
    """
    from tinyassets.api.engine_helpers import _append_ledger

    _append_ledger(
        _base_path(), action,
        actor=actor, target=target, summary=summary, payload=payload,
    )


def _source_code_hash(source_code: str) -> str:
    return hashlib.sha256(source_code.encode("utf-8")).hexdigest()


def _clear_source_code_approval(node: Any) -> None:
    node.approved = False
    node.approved_by = ""
    node.approved_at = ""
    node.approved_source_hash = ""
    node.approval_reason = ""


def _approval_provenance_valid(
    approved: Any, source_code: str, approved_source_hash: str,
) -> bool:
    """True only when an ``approved`` flag is backed by hash provenance.

    A source_code node is genuinely approved iff the recorded
    ``approved_source_hash`` equals the hash of the *current* source_code.
    A bare ``approved=True`` with no/stale hash is forged or stale and is
    reported as unapproved. Approval is provenance -- it never authorizes
    or blocks execution (the OS sandbox is the boundary). Prompt-template
    (non-source) nodes carry no source to attest, so an empty source_code is
    treated as matching an empty hash only when no hash was recorded.
    """
    if not approved:
        return False
    if not source_code:
        # No executable content to gate. Approval is meaningless here but
        # also harmless — the compiler only gates source_code nodes.
        return True
    return bool(approved_source_hash) and (
        approved_source_hash == _source_code_hash(source_code)
    )


def _reconcile_copied_approval(merged: dict[str, Any]) -> None:
    """Strip approval metadata from a copied/merged node body unless the
    effective source hash still matches the recorded approved hash.

    Used by the ``node_ref`` copy path: a caller can inherit an approved
    body and then override ``source_code``/other executable content. The
    inherited ``approved=True`` must not survive a content change the
    approver never reviewed. See Codex ADAPT review on PR #1349.
    """
    if not _approval_provenance_valid(
        merged.get("approved"),
        merged.get("source_code") or "",
        merged.get("approved_source_hash") or "",
    ):
        merged["approved"] = False
        merged["approved_by"] = ""
        merged["approved_at"] = ""
        merged["approved_source_hash"] = ""
        merged["approval_reason"] = ""


def _node_source_code_unapproved(nd: dict[str, Any]) -> bool:
    """True when a node dict has source_code with no hash-backed approval.

    PROVENANCE ONLY. Approval stopped gating execution with change
    ``sandboxed-code-node`` (the OS sandbox is the authority boundary), so this
    predicate feeds the ``unapproved_source_code_nodes`` list a reader may use
    to see whose code they are about to run -- and nothing derives
    ``runnable`` from it. That derivation is :func:`_source_code_problems`.
    """
    if not nd.get("source_code"):
        return False
    return not _approval_provenance_valid(
        nd.get("approved", False),
        nd.get("source_code") or "",
        nd.get("approved_source_hash") or "",
    )


def _source_code_problems(node_defs: Any) -> list[dict[str, Any]]:
    """What the compiler will refuse, per code node: the ONE fact ``runnable``
    reports. Reuses :func:`tinyassets.graph_compiler.source_code_problems`, so
    a receipt cannot say ``runnable`` about a branch the compiler then rejects,
    nor ``runnable: false`` about one it accepts (concern 2026-09-01: the
    receipt still encoded the retired approval gate, and the universe told the
    founder its PR-building branch needed an approval flow that does not
    exist).

    Scope: the owner's own branches through ``run_graph``. The bid-market
    executor (``executors/node_bid.py``) keeps its own approval gate because
    a bid-referenced node is another universe's code -- that boundary is
    cross-user and is not what this receipt describes."""
    from tinyassets.graph_compiler import source_code_problems

    found: list[dict[str, Any]] = []
    for nd in node_defs or []:
        if isinstance(nd, dict):
            source = nd.get("source_code") or ""
            node_id = str(nd.get("node_id") or "")
            display = str(nd.get("display_name") or "")
        else:
            source = getattr(nd, "source_code", "") or ""
            node_id = str(getattr(nd, "node_id", "") or "")
            display = str(getattr(nd, "display_name", "") or "")
        if not source:
            continue
        problems = source_code_problems(source, node_id)
        if problems:
            found.append({
                "node_id": node_id,
                "display_name": display,
                "problems": problems,
            })
    return found


def _reconcile_node_approval(node: Any) -> Any:
    """Object-level twin of :func:`_reconcile_copied_approval`.

    Re-validates a carried/restored ``NodeDefinition`` object's approval
    against its current source hash and clears the approval metadata when the
    provenance does not match. Used by paths that carry node bodies forward as
    NodeDefinition objects rather than dicts: ``build_branch`` fork-copy
    (inherits the parent's ``node_defs`` wholesale) and ``rollback_node``
    (restores a raw audit body). A trusted/legacy snapshot carrying
    ``source_code`` + ``approved=True`` + empty/stale ``approved_source_hash``
    must not survive the copy as still-approved. Closes the Codex final
    residual on PR #1349 (carried-snapshot bypass).

    Returns the node for chaining.
    """
    if not _approval_provenance_valid(
        getattr(node, "approved", False),
        getattr(node, "source_code", "") or "",
        getattr(node, "approved_source_hash", "") or "",
    ):
        _clear_source_code_approval(node)
    return node


def _ensure_workflow_db() -> None:
    """Ensure the shared SQLite schema exists before any branch action runs.

    Branch handlers read/write ``base_path/.tinyassets.db``. Calling this
    lazily keeps tests and first-use paths from needing a separate init step.
    """
    from tinyassets.daemon_server import initialize_author_server

    initialize_author_server(_base_path())


def _dispatch_branch_action(
    action: str,
    handler: Any,
    kwargs: dict[str, Any],
) -> str:
    """Run a branch handler and append to the global ledger on success.

    Read-only branch actions (get/list/validate/describe) bypass the ledger.
    Write actions (create/add/connect/set/delete) are funneled here so no
    handler can silently skip attribution.
    """
    from tinyassets.api.engine_helpers import _format_dirty_file_conflict, _truncate

    _ensure_workflow_db()
    actor = _request_branch_actor()
    if action in _BRANCH_WRITE_ACTIONS and actor is None:
        if action in {"create_branch", "build_branch"}:
            return json.dumps({"error": "Authenticated branch subject required."})
        return _branch_authority_denied()
    try:
        result_str = handler(kwargs)
    except DirtyFileError as exc:
        # Phase 7.3: surface local-edit conflicts as a structured MCP
        # response so the client can render actionable options. Ledger
        # is intentionally skipped — no write landed.
        return json.dumps(_format_dirty_file_conflict(exc))

    if action not in _BRANCH_WRITE_ACTIONS:
        return result_str

    try:
        result = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return result_str

    if not isinstance(result, dict):
        return result_str
    # Skip ledger on any error-shaped response. Composite actions signal
    # failure via status="rejected" + errors[]; atomic actions use "error"
    # (singular string). Treat both as "don't attribute a write that
    # didn't land".
    if "error" in result:
        return result_str
    if result.get("status") == "rejected":
        return result_str

    try:
        target = result.get("branch_def_id", "") or kwargs.get("branch_def_id", "")
        summary_bits: list[str] = [action]
        if kwargs.get("name"):
            summary_bits.append(kwargs["name"])
        if kwargs.get("node_id"):
            summary_bits.append(f"node={kwargs['node_id']}")
        if kwargs.get("from_node") and kwargs.get("to_node"):
            summary_bits.append(f"{kwargs['from_node']}->{kwargs['to_node']}")
        if kwargs.get("field_name"):
            summary_bits.append(f"field={kwargs['field_name']}")
        # Composite summary hints — one ledger entry per call, not per op.
        if action == "build_branch":
            summary_bits.append(
                f"nodes={result.get('node_count', '?')}"
            )
        if action == "patch_branch":
            summary_bits.append(
                f"ops={result.get('ops_applied', '?')}"
            )
        summary = _truncate(" ".join(summary_bits))
        _append_global_ledger(
            action,
            actor=actor,
            target=str(target),
            summary=summary,
            payload=None,
        )
    except Exception as exc:
        logger.warning("Ledger write failed for branch action %s: %s", action, exc)

    return result_str


def _ext_branch_create(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.engine_helpers import (
        _format_commit_failed,
        _storage_backend,
    )
    from tinyassets.branches import BranchDefinition
    from tinyassets.identity import git_author

    name = kwargs.get("name", "").strip()
    if not name:
        return json.dumps({"error": "name is required for create_branch."})
    actor = _request_branch_actor()
    if actor is None:
        return json.dumps({"error": "Authenticated branch subject required."})

    # PRIVATE by default, and private for anything that is not an explicit
    # "public" (founder 2026-09-26: nothing in a user's universe defaults to
    # visible). Both halves mattered: the default decided an omitted value, and
    # the fallback turned every unrecognized value into `public`, so a typo
    # published a branch. This path is the deprecated `extensions` fat tool --
    # the canonical `write_graph target=branch` already does
    # `setdefault("visibility", "private")` -- and a deprecated path is still a
    # path.
    visibility_in = (kwargs.get("visibility") or "private").strip().lower()
    visibility = "public" if visibility_in == "public" else "private"
    branch = BranchDefinition(
        name=name,
        description=kwargs.get("description", ""),
        domain_id=kwargs.get("domain_id") or "workflow",
        author=actor,
        visibility=visibility,
    )
    try:
        saved, _commit = _storage_backend().save_branch_and_commit(
            branch,
            author=git_author(actor),
            message=f"branches.create_branch: {name}",
            force=bool(kwargs.get("force", False)),
        )
    except CommitFailedError as exc:
        return json.dumps(_format_commit_failed(exc))
    return json.dumps({
        "branch_def_id": saved["branch_def_id"],
        "name": saved["name"],
        "visibility": saved.get("visibility", "private"),
        "status": "created",
    })


def _request_branch_actor() -> str | None:
    """Return the credential-validated request subject, never an env actor."""
    from tinyassets.api.permissions import current_request_actor_id
    from tinyassets.principals import named_principal

    return named_principal(current_request_actor_id()) or None


#: Recorded when a ledgered action ran without a credential-validated subject.
#: A literal, NOT the empty string: `_append_ledger` resolves a falsy actor via
#: `actor or _current_actor()`, and `_current_actor()` reads the ambient
#: `UNIVERSE_SERVER_USER`. So passing "" would attribute an unauthenticated
#: write to whoever the environment happens to name — the env forging identity,
#: which is the thing `_request_branch_actor` exists to prevent.
LEDGER_ACTOR_UNBOUND = ""


def ledger_actor() -> str:
    """The actor to record in the global ledger for this request.

    Always truthy, so it can never fall through to the ambient env actor.
    Callers that must REFUSE an unauthenticated write should check
    :func:`_request_branch_actor` themselves and reject before mutating; this
    helper is only about attributing a write that is already permitted.
    """
    actor = _request_branch_actor()
    if actor is None:
        raise PermissionError("authentication required for branch ledger writes")
    return actor


def _branch_not_found_message(selector: str) -> str:
    return f"Branch '{selector}' not found."


def _branch_not_found(selector: str) -> str:
    return json.dumps({"error": _branch_not_found_message(selector)})


def _branch_version_not_found_message(selector: str) -> str:
    return f"Branch version '{selector}' not found."


def _branch_version_not_found(selector: str) -> str:
    return json.dumps({"error": _branch_version_not_found_message(selector)})


def _branch_authorized(branch: dict[str, Any]) -> bool:
    actor = _request_branch_actor()
    return actor is not None and actor == (branch.get("author") or "").strip()


def _branch_authority_denied() -> str:
    return json.dumps({"error": "Authenticated branch author required."})


def _resolve_readable_branch(
    selector: str,
    base_path: str,
) -> tuple[str, dict[str, Any]] | None:
    """Resolve an ID/name only when the request subject may read the branch."""
    from tinyassets.daemon_server import (
        get_branch_definition,
        list_branch_definitions,
    )

    selector = (selector or "").strip()
    if not selector:
        return None
    actor = _request_branch_actor()
    try:
        branch = get_branch_definition(base_path, branch_def_id=selector)
    except KeyError:
        branch = None
    if branch is not None:
        # A row with NO visibility field is PRIVATE, not public. This is the
        # read side of the same rule: a legacy branch definition written before
        # the field existed must not be readable by everyone because the field is
        # absent. Fail closed (founder 2026-09-26); its author still reads it via
        # the author check below.
        from tinyassets.branch_versions import branch_readable_by

        if branch_readable_by(actor, author=branch.get("author"),
                              visibility=branch.get("visibility")):
            return selector, branch
        return None

    needle = selector.lower()
    for candidate in list_branch_definitions(
        base_path,
        viewer=actor or "",
    ):
        if (candidate.get("name") or "").lower() == needle:
            return candidate["branch_def_id"], candidate
    return None


def _resolve_readable_version(
    version_id: str,
    base_path: str,
) -> tuple[str, dict[str, Any]] | None:
    """Resolve a version only when the caller may read THAT version.

    Its author reads every version of their branch, history included. Anyone
    else needs both a readable branch AND the version's publication mark:
    patch_branch snapshots every edit, so a public branch's versions are mostly
    private edit history, and publishing a branch must expose only the version
    its owner confirmed (founder 2026-09-30, astra round 3 on #4107).
    """
    from tinyassets.branch_versions import get_branch_version, version_readable_by

    version_id = (version_id or "").strip()
    if not version_id:
        return None
    version = get_branch_version(base_path, version_id)
    if version is None:
        return None
    readable = _resolve_readable_branch(version.branch_def_id, base_path)
    # Exact id only: the resolver's NAME fallback would let an orphaned
    # version borrow the author and visibility of an unrelated branch that
    # happens to be named like its missing parent id.
    if readable is None or readable[0] != version.branch_def_id:
        return None
    if not version_readable_by(
        _request_branch_actor(), author=readable[1].get("author"),
        visibility=readable[1].get("visibility"), public=version.public,
    ):
        return None
    return version_id, version.to_dict()


def resolve_branch_id_for_read(bid_or_name: str, base_path: str) -> str | None:
    """The branch id, or None when the subject may not read it.

    `_resolve_branch_id` deliberately passes an unresolvable selector THROUGH, so the
    caller's KeyError handler can report "not found". That is right for a branch that
    does not exist and wrong for one that does but is private: the raw load then
    succeeds and the caller proceeds on someone else's content.

    Callers that are about to LOAD the branch -- rather than merely name it -- must use
    this and refuse on None. "Unreadable" and "absent" are different answers and only
    one of them is safe to paper over.
    """
    resolved = _resolve_readable_branch(bid_or_name, base_path)
    return resolved[0] if resolved is not None else None


def _resolve_branch_id(bid_or_name: str, base_path: str) -> str:
    """Return branch_def_id for either a branch_def_id or a branch name.

    Tries exact ID match first (fast path via get_branch_definition).
    Falls back to case-insensitive name search via list_branch_definitions.
    Returns the original string unchanged if no match is found — the caller's
    KeyError handler will surface the "not found" error as usual.
    """
    resolved = _resolve_readable_branch(bid_or_name, base_path)
    return resolved[0] if resolved is not None else bid_or_name


def _ext_branch_get(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.market import _gates_enabled
    from tinyassets.daemon_server import list_gate_claims

    selector = kwargs.get("branch_def_id", "").strip()
    if not selector:
        return json.dumps({"error": "branch_def_id is required."})
    resolved = _resolve_readable_branch(selector, str(_base_path()))
    if resolved is None:
        return _branch_not_found(selector)
    bid, branch = resolved
    branch = dict(branch)
    fork_from = branch.get("fork_from")
    if fork_from and _resolve_readable_version(
        fork_from,
        str(_base_path()),
    ) is None:
        branch.pop("fork_from", None)
    parent_def_id = (branch.get("parent_def_id") or "").strip()
    if parent_def_id and _resolve_readable_branch(
        parent_def_id,
        str(_base_path()),
    ) is None:
        branch.pop("parent_def_id", None)
    # Phase 6.4: non-retracted claims for this Branch across all
    # Goals. Flag-gated placeholder when GATES_ENABLED=0 so UIs
    # render "gates off" distinct from "no claims yet."
    if _gates_enabled():
        branch["gate_claims"] = list_gate_claims(
            _base_path(),
            branch_def_id=bid,
            include_retracted=False,
            public_only=True,
        )
    else:
        branch["gate_claims"] = []
        branch["gate_status"] = "gates_disabled"
    related = _related_wiki_pages(branch)
    branch["related_wiki_pages"] = related["items"]
    branch["related_wiki_pages_truncated"] = related["truncated_count"]
    unapproved_sc = [
        {"node_id": nd.get("node_id", ""), "display_name": nd.get("display_name", "")}
        for nd in branch.get("node_defs", [])
        if _node_source_code_unapproved(nd)
    ]
    branch["unapproved_source_code_nodes"] = unapproved_sc
    problems = _source_code_problems(branch.get("node_defs", []))
    branch["source_code_problems"] = problems
    branch["runnable"] = not problems
    return json.dumps(branch, default=str)


def _ext_branch_approve_source_code(kwargs: dict[str, Any]) -> str:
    from tinyassets.branches import BranchDefinition
    from tinyassets.daemon_server import save_branch_definition

    selector = (kwargs.get("branch_def_id") or "").strip()
    nid = (kwargs.get("node_id") or "").strip()
    if not selector or not nid:
        return json.dumps({
            "status": "rejected",
            "error": "branch_def_id and node_id are required.",
        })
    resolved = _resolve_readable_branch(selector, str(_base_path()))
    if resolved is None:
        return _branch_not_found(selector)
    bid, source = resolved
    if not _branch_authorized(source):
        return _branch_authority_denied()
    actor = _request_branch_actor()
    if actor is None:
        return _branch_authority_denied()

    staging = BranchDefinition.from_dict(source)
    target_node = next(
        (n for n in staging.node_defs if n.node_id == nid), None,
    )
    if target_node is None:
        return json.dumps({
            "status": "rejected",
            "error": f"Node '{nid}' not found on branch '{bid}'.",
        })
    if not target_node.source_code:
        return json.dumps({
            "status": "rejected",
            "error": f"Node '{nid}' has no source_code to approve.",
        })

    source_hash = _source_code_hash(target_node.source_code)
    target_node.approved = True
    target_node.approved_by = actor
    target_node.approved_at = datetime.now(timezone.utc).isoformat()
    target_node.approved_source_hash = source_hash
    target_node.approval_reason = (kwargs.get("reason") or "").strip()

    saved = save_branch_definition(_base_path(), branch_def=staging.to_dict())
    persisted = BranchDefinition.from_dict(saved)
    approved_node = next(
        (n for n in persisted.node_defs if n.node_id == nid), target_node,
    )
    warning = ""
    return json.dumps({
        "status": "approved",
        "branch_def_id": bid,
        "node_id": nid,
        "approved": approved_node.approved,
        "approved_by": approved_node.approved_by,
        "approved_at": approved_node.approved_at,
        "approved_source_hash": approved_node.approved_source_hash,
        "approval_reason": approved_node.approval_reason,
        "approval_warning": warning,
    }, default=str)


_VALID_BRANCH_LIST_SCOPES = {"published", "all", "mine"}


def _ext_branch_list(kwargs: dict[str, Any]) -> str:
    from tinyassets.daemon_server import list_branch_definitions

    scope = (kwargs.get("scope") or "published").strip().lower()
    if scope not in _VALID_BRANCH_LIST_SCOPES:
        return json.dumps({
            "error": (
                f"unknown scope '{scope}'. "
                f"Valid scopes: {sorted(_VALID_BRANCH_LIST_SCOPES)}."
            ),
        })

    actor = _request_branch_actor()
    if scope == "mine" and actor is None:
        return json.dumps({"branches": [], "count": 0})

    # Phase 6.2.2 — visibility-aware listing. Viewer sees public
    # Branches and any private Branches they authored.
    rows = list_branch_definitions(
        _base_path(),
        domain_id=kwargs.get("domain_id", ""),
        author=kwargs.get("author", ""),
        goal_id=kwargs.get("goal_id", ""),
        viewer=actor or "",
    )

    # requires_sandbox filter: "none" = design-only branches only (no node
    # has requires_sandbox=True); "any" = branches that have at least one
    # sandbox-requiring node. Omit / empty = no filter.
    rs_filter = (kwargs.get("requires_sandbox") or "").strip().lower()

    summaries = []
    for r in rows:
        published_version_id = None
        if scope == "published":
            from tinyassets.branch_versions import list_branch_versions

            # Published = a version its owner published, newest first; an
            # unmarked edit snapshot is history, not a published shape.
            versions = [
                v for v in list_branch_versions(
                    _base_path(), r.get("branch_def_id", ""), limit=50)
                if v.public
            ]
            if not versions:
                continue
            published_version_id = versions[0].branch_version_id
        elif scope == "mine":
            if (r.get("author") or "") != actor:
                continue
        node_defs = r.get("node_defs", [])
        has_sandbox_nodes = any(nd.get("requires_sandbox") for nd in node_defs)
        if rs_filter == "none" and has_sandbox_nodes:
            continue
        if rs_filter == "any" and not has_sandbox_nodes:
            continue

        # node_count MUST match describe_branch's count
        # (``len(branch.node_defs)`` at line ~4924) — that's the
        # source of truth. The old formula added ``graph.nodes +
        # node_defs`` which double-counted because graph.nodes is a
        # compiled-topology view that overlaps with node_defs.
        node_count = len(node_defs)
        summary = {
            "branch_def_id": r.get("branch_def_id"),
            "name": r.get("name"),
            "author": r.get("author"),
            "domain_id": r.get("domain_id"),
            "goal_id": r.get("goal_id"),
            "node_count": node_count,
            "skill_count": len(r.get("skills", []) or []),
            "published": True if scope == "published" else r.get("published", False),
            "visibility": r.get("visibility") or "private",
            "has_sandbox_nodes": has_sandbox_nodes,
        }
        if published_version_id is not None:
            summary["branch_version_id"] = published_version_id
        summaries.append(summary)
    return json.dumps({"branches": summaries, "count": len(summaries)})


def _ext_branch_delete(kwargs: dict[str, Any]) -> str:
    from tinyassets.daemon_server import delete_branch_definition

    selector = kwargs.get("branch_def_id", "").strip()
    if not selector:
        return json.dumps({"error": "branch_def_id is required."})
    resolved = _resolve_readable_branch(selector, str(_base_path()))
    if resolved is None:
        return _branch_not_found(selector)
    bid, branch = resolved
    if not _branch_authorized(branch):
        return _branch_authority_denied()
    removed = delete_branch_definition(_base_path(), branch_def_id=bid)
    if not removed:
        return json.dumps({"error": f"Branch '{bid}' not found."})
    return json.dumps({"branch_def_id": bid, "status": "deleted"})


def _branch_dependents(
    base: str, *, branch_def_id: str, actor: str,
) -> dict[str, list[str]]:
    """Everything that would break if this branch definition vanished, by
    reader (Codex rounds 1 and 2 on the branch-delete change):

    * automations bound to it, in any universe (registration promises not to
      store one that cannot fire; deleting the branch would create exactly
      that, and the user would watch it degrade asynchronously);
    * active webhooks whose token resolves to it (each delivery would fail);
    * active schedules and event subscriptions that fire it;
    * goals whose canonical binding -- default, personal, or the legacy
      column -- points at ANY of its versions (`invoke_branch_version` maps a
      version back to its definition);
    * other branches of the same author that invoke it: their CURRENT
      definitions, and their published SNAPSHOTS, which are executable on
      their own and reload the child live (a FOREIGN snapshot from the
      branch's public days was already cut off when it went private and is
      not the owner's to fix, so it does not block);
    * universes whose soul declares it as their loop branch (request
      admission queues that branch for every incoming request).

    Internal patch snapshots of THIS branch are NOT dependents: every patch
    mints one, so counting them would make any edited branch undeletable.
    Version ids are read uncapped.
    """
    import sqlite3

    from tinyassets import branch_versions, scheduler
    from tinyassets.automations import AutomationStore
    from tinyassets.daemon_server import list_branch_definitions
    from tinyassets.storage import db_path, webhook_hooks

    versions = branch_versions.list_version_ids(base, branch_def_id)
    out: dict[str, list[str]] = {
        "automations": [], "webhooks": [], "schedules": [], "subscriptions": [],
        "goals": [], "branches": [], "universes": [],
    }

    out["automations"] = [
        a.automation_id for a in AutomationStore(base).list_for_branch(branch_def_id)
    ]
    out["webhooks"] = [
        (h.get("source_id") or f"hook:{h['token_prefix']}")
        for h in webhook_hooks.list_active_for_branch(base, branch_def_id=branch_def_id)
    ]
    bound = scheduler.list_bound_to_branch(base, branch_def_id=branch_def_id)
    out["schedules"] = bound["schedules"]
    out["subscriptions"] = bound["subscriptions"]

    if versions:
        goal_ids: set[str] = set()
        db = db_path(base)
        if db.exists():
            conn = sqlite3.connect(db)
            try:
                tables = {
                    r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                placeholders = ",".join("?" for _ in versions)
                params = tuple(versions)
                for table in ("canonical_bindings", "goal_canonicals"):
                    if table in tables:
                        for row in conn.execute(
                            f"SELECT goal_id FROM {table} "
                            f"WHERE branch_version_id IN ({placeholders})",
                            params,
                        ):
                            goal_ids.add(str(row[0]))
                if "goals" in tables:
                    # The legacy column exists on every production database
                    # (daemon_server adds it at init); probe rather than swallow
                    # an OperationalError, which would hide a real fault.
                    columns = {r[1] for r in conn.execute("PRAGMA table_info(goals)")}
                    if "canonical_branch_version_id" in columns:
                        for row in conn.execute(
                            f"SELECT goal_id FROM goals "
                            f"WHERE canonical_branch_version_id IN ({placeholders})",
                            params,
                        ):
                            goal_ids.add(str(row[0]))
            finally:
                conn.close()
        out["goals"] = sorted(goal_ids)

    invokers: set[str] = set()
    for row in list_branch_definitions(base, author=actor, include_private=True):
        other = str(row.get("branch_def_id") or "")
        if not other or other == branch_def_id:
            continue
        nodes = row.get("node_defs") or ((row.get("graph") or {}).get("nodes")) or []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            by_id = (node.get("invoke_branch_spec") or {}).get("branch_def_id")
            by_version = (node.get("invoke_branch_version_spec") or {}).get("branch_version_id")
            if by_id == branch_def_id or (by_version and by_version in versions):
                invokers.add(other)
                break
    invokers |= branch_versions.versions_invoking(
        base, branch_def_id=branch_def_id, version_ids=versions, author=actor,
    )
    out["branches"] = sorted(invokers)

    # A universe queues its declared loop branch on every request it admits.
    from tinyassets.universe_soul import read_universe_soul

    loops: list[str] = []
    # Owned only. This names universe ids back to the caller, and an archived
    # or restored directory still carries the `soul.md` it was archived with --
    # so an unowned directory used to be reported as a live dependent, which is
    # both a wrong refusal and an id it should never have seen (2026-09-02: a
    # universe exists because an ownership row says so).
    from tinyassets.api.universe import _is_listable_universe_dir
    from tinyassets.daemon_server import owned_universe_ids

    try:
        owned = owned_universe_ids(base)
        universe_dirs = [
            d for d in Path(base).iterdir() if _is_listable_universe_dir(d, owned)
        ]
    except OSError:
        universe_dirs = []
    for udir in sorted(universe_dirs):
        if not (udir / "soul.md").is_file():
            continue
        try:
            soul = read_universe_soul(udir)
        except Exception:  # noqa: BLE001 - one unreadable soul must not hide the rest
            logger.exception("could not read %s while checking branch dependents", udir / "soul.md")
            loops.append(f"{udir.name}?")
            continue
        if soul is not None and (soul.loop_branch_def_id or "").strip() == branch_def_id:
            loops.append(udir.name)
    out["universes"] = loops
    return out


def _ext_branch_delete_own(kwargs: dict[str, Any]) -> str:
    """Delete one of the caller's OWN branches that nothing of theirs depends on.

    The served surfaces (`write_graph target=branch operation=delete`) route
    here rather than to `delete_branch`. Tiny, 2026-09-02: "I do not have a
    branch delete operation exposed right now ... 106 branches". Inside your
    universe you are god; the only invariant is not affecting other users. A
    PUBLIC branch is a shape others copy or remix into their own universe and
    runs nothing for anyone else (founder, 2026-09-02), so it deletes like any
    other. A branch the owner's own things still depend on is refused with
    every dependent named so they can delete or re-point them first. Internal
    patch snapshots are not dependents.
    """
    from tinyassets.daemon_server import delete_branch_definition

    selector = kwargs.get("branch_def_id", "").strip()
    if not selector:
        return json.dumps({"error": "branch_def_id is required."})
    base = str(_base_path())
    resolved = _resolve_readable_branch(selector, base)
    if resolved is None:
        return _branch_not_found(selector)
    bid, branch = resolved
    if not _branch_authorized(branch):
        # Same envelope as a private read by a non-author: existence is not
        # confirmed either way, even for a public branch.
        return _branch_not_found(selector)
    actor = _request_branch_actor() or ""
    dependents = _branch_dependents(base, branch_def_id=bid, actor=actor)
    if any(dependents.values()):
        return json.dumps({
            "error": "branch_has_dependents",
            "branch_def_id": bid,
            "dependents": dependents,
            "detail": (
                "Something still uses this branch: delete or re-point the "
                "automations, revoke the webhooks, unregister the schedules and "
                "subscriptions, unset the goals' canonical binding, edit the "
                "branches (and their published versions) that invoke it, or "
                "declare a different loop branch in the command centers that run it, "
                "then delete."
            ),
        })
    removed = delete_branch_definition(base, branch_def_id=bid)
    if not removed:
        return _branch_not_found(selector)
    return json.dumps({"branch_def_id": bid, "status": "deleted"})


def _ext_branch_add_node(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.engine_helpers import (
        _format_commit_failed,
        _storage_backend,
    )
    from tinyassets.branches import BranchDefinition
    from tinyassets.identity import git_author

    verbose = str(kwargs.get("verbose") or "").strip().lower() in ("true", "1", "yes")
    selector = kwargs.get("branch_def_id", "").strip()
    nid = kwargs.get("node_id", "").strip()
    if not selector or not nid:
        return json.dumps({
            "error": "branch_def_id and node_id are required.",
        })
    resolved = _resolve_readable_branch(selector, str(_base_path()))
    if resolved is None:
        return _branch_not_found(selector)
    bid, source_dict = resolved
    if not _branch_authorized(source_dict):
        return _branch_authority_denied()
    actor = _request_branch_actor()
    if actor is None:
        return _branch_authority_denied()

    # Normalize kwargs into a node spec dict so we can share the
    # build_branch resolver (which checks node_ref / intent and
    # refuses to silently shadow an existing standalone node — #66).
    raw: dict[str, Any] = {
        "node_id": nid,
        "display_name": kwargs.get("display_name", "").strip(),
        "description": kwargs.get("description", ""),
        "phase": kwargs.get("phase", "") or "custom",
        "input_keys": kwargs.get("input_keys", ""),
        "output_keys": kwargs.get("output_keys", ""),
        "source_code": kwargs.get("source_code", ""),
        "prompt_template": kwargs.get("prompt_template", ""),
    }
    if "node_ref" in kwargs:
        raw["node_ref"] = kwargs["node_ref"]
    if "intent" in kwargs:
        raw["intent"] = kwargs["intent"]

    branch = BranchDefinition.from_dict(source_dict)
    err = _apply_node_spec(branch, raw)
    if err:
        return json.dumps({"error": err})

    # The resolved node may have been renamed; capture the final id
    # from the mutated branch BEFORE persisting.
    final_nid = branch.node_defs[-1].node_id
    try:
        _storage_backend().save_branch_and_commit(
            branch,
            author=git_author(actor),
            message=f"branches.add_node: {bid}.{final_nid}",
            force=bool(kwargs.get("force", False)),
        )
    except CommitFailedError as exc:
        return json.dumps(_format_commit_failed(exc))
    add_node_payload: dict[str, Any] = {
        "branch_def_id": bid,
        "node_id": final_nid,
        "status": "added",
    }
    if verbose:
        added = next(
            (n for n in branch.node_defs if n.node_id == final_nid), None
        )
        if added is not None:
            add_node_payload["node_def"] = added.to_dict()
    return json.dumps(add_node_payload, default=str)


def _ext_branch_connect_nodes(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.engine_helpers import (
        _format_commit_failed,
        _storage_backend,
    )
    from tinyassets.branches import BranchDefinition, EdgeDefinition
    from tinyassets.identity import git_author

    verbose = str(kwargs.get("verbose") or "").strip().lower() in ("true", "1", "yes")
    selector = kwargs.get("branch_def_id", "").strip()
    src = kwargs.get("from_node", "").strip()
    dst = kwargs.get("to_node", "").strip()
    if not (selector and src and dst):
        return json.dumps({
            "error": "branch_def_id, from_node, and to_node are required.",
        })

    resolved = _resolve_readable_branch(selector, str(_base_path()))
    if resolved is None:
        return _branch_not_found(selector)
    bid, source_dict = resolved
    if not _branch_authorized(source_dict):
        return _branch_authority_denied()
    actor = _request_branch_actor()
    if actor is None:
        return _branch_authority_denied()

    branch = BranchDefinition.from_dict(source_dict)
    branch.edges.append(EdgeDefinition(from_node=src, to_node=dst))

    try:
        _storage_backend().save_branch_and_commit(
            branch,
            author=git_author(actor),
            message=f"branches.connect_nodes: {bid} {src}->{dst}",
            force=bool(kwargs.get("force", False)),
        )
    except CommitFailedError as exc:
        return json.dumps(_format_commit_failed(exc))
    connect_payload: dict[str, Any] = {
        "branch_def_id": bid,
        "from_node": src,
        "to_node": dst,
        "status": "connected",
    }
    if verbose:
        connect_payload["edge_count"] = len(branch.edges)
    return json.dumps(connect_payload, default=str)


def _ext_branch_set_entry_point(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.engine_helpers import (
        _format_commit_failed,
        _storage_backend,
    )
    from tinyassets.branches import BranchDefinition
    from tinyassets.identity import git_author

    verbose = str(kwargs.get("verbose") or "").strip().lower() in ("true", "1", "yes")
    selector = kwargs.get("branch_def_id", "").strip()
    nid = kwargs.get("node_id", "").strip()
    if not (selector and nid):
        return json.dumps({
            "error": "branch_def_id and node_id are required.",
        })

    resolved = _resolve_readable_branch(selector, str(_base_path()))
    if resolved is None:
        return _branch_not_found(selector)
    bid, source_dict = resolved
    if not _branch_authorized(source_dict):
        return _branch_authority_denied()
    actor = _request_branch_actor()
    if actor is None:
        return _branch_authority_denied()

    branch = BranchDefinition.from_dict(source_dict)
    branch.entry_point = nid

    try:
        _storage_backend().save_branch_and_commit(
            branch,
            author=git_author(actor),
            message=f"branches.set_entry_point: {bid}.{nid}",
            force=bool(kwargs.get("force", False)),
        )
    except CommitFailedError as exc:
        return json.dumps(_format_commit_failed(exc))
    entry_payload: dict[str, Any] = {
        "branch_def_id": bid,
        "entry_point": nid,
        "status": "set",
    }
    if verbose:
        entry_payload["node_count"] = len(branch.node_defs)
    return json.dumps(entry_payload, default=str)


def _ext_branch_add_state_field(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.engine_helpers import (
        _format_commit_failed,
        _storage_backend,
    )
    from tinyassets.branches import BranchDefinition
    from tinyassets.identity import git_author

    verbose = str(kwargs.get("verbose") or "").strip().lower() in ("true", "1", "yes")
    selector = kwargs.get("branch_def_id", "").strip()
    fname = kwargs.get("field_name", "").strip()
    ftype = kwargs.get("field_type", "").strip() or "str"
    if not (selector and fname):
        return json.dumps({
            "error": "branch_def_id and field_name are required.",
        })

    resolved = _resolve_readable_branch(selector, str(_base_path()))
    if resolved is None:
        return _branch_not_found(selector)
    bid, source_dict = resolved
    if not _branch_authorized(source_dict):
        return _branch_authority_denied()
    actor = _request_branch_actor()
    if actor is None:
        return _branch_authority_denied()

    branch = BranchDefinition.from_dict(source_dict)
    if any(f.get("name") == fname for f in branch.state_schema):
        return json.dumps({
            "error": f"State field '{fname}' already exists on this branch.",
        })

    field_entry: dict[str, Any] = {
        "name": fname,
        "type": ftype,
        "description": kwargs.get("description", ""),
    }
    reducer = kwargs.get("reducer", "").strip()
    if reducer:
        field_entry["reducer"] = reducer
    # BUG-094: also accept canonical ``default_value`` (StateFieldDecl) and
    # write both keys so PR #932's ``_state_schema_defaults`` seeding finds it.
    default = kwargs.get(
        "default_value", kwargs.get("field_default", ""),
    )
    if default != "":
        field_entry["default_value"] = default
        field_entry["default"] = default

    branch.state_schema.append(field_entry)
    try:
        _storage_backend().save_branch_and_commit(
            branch,
            author=git_author(actor),
            message=f"branches.add_state_field: {bid}.{fname}",
            force=bool(kwargs.get("force", False)),
        )
    except CommitFailedError as exc:
        return json.dumps(_format_commit_failed(exc))
    state_payload: dict[str, Any] = {
        "branch_def_id": bid,
        "field_name": fname,
        "status": "added",
    }
    if verbose:
        state_payload["field_count"] = len(branch.state_schema)
    return json.dumps(state_payload, default=str)


def _ext_branch_validate(kwargs: dict[str, Any]) -> str:
    from tinyassets.branches import BranchDefinition

    selector = kwargs.get("branch_def_id", "").strip()
    if not selector:
        return json.dumps({"error": "branch_def_id is required."})
    resolved = _resolve_readable_branch(selector, str(_base_path()))
    if resolved is None:
        return _branch_not_found(selector)
    bid, source_dict = resolved

    branch = BranchDefinition.from_dict(source_dict)
    errors = branch.validate()

    # Provenance (who approved which code), never runnability: code nodes run
    # in the OS sandbox. What can stop a run is a structural error or a code
    # node the compiler refuses, and both are reported here as such.
    unapproved_sc = [
        {"node_id": nd.get("node_id", ""), "display_name": nd.get("display_name", "")}
        for nd in source_dict.get("node_defs", [])
        if _node_source_code_unapproved(nd)
    ]
    problems = _source_code_problems(source_dict.get("node_defs", []))

    # sandbox-compat warning: list any requires_sandbox=True nodes when
    # the host's bwrap probe says sandbox is unavailable. Non-fatal.
    sandbox_warnings: list[str] = []
    try:
        from tinyassets.providers.base import get_sandbox_status
        sb = get_sandbox_status()
        if not sb.get("bwrap_available"):
            sandbox_nodes = [
                nd.node_id
                for nd in branch.node_defs
                if getattr(nd, "requires_sandbox", False)
            ]
            if sandbox_nodes:
                reason = sb.get("reason") or "bwrap unavailable"
                sandbox_warnings.append(
                    f"This branch contains {len(sandbox_nodes)} node(s) that "
                    f"require a sandbox ({', '.join(sorted(sandbox_nodes))}) but "
                    f"the host sandbox probe returned: {reason}. "
                    f"These nodes will fail at runtime. Options: enable bwrap "
                    f"on the host, or use a branch variant without "
                    f"requires_sandbox=true nodes (design-only branch)."
                )
    except Exception:  # noqa: BLE001 — best-effort non-blocking warning
        pass

    return json.dumps({
        "branch_def_id": bid,
        "valid": not errors,
        "errors": errors,
        "runnable": not errors and not problems,
        "source_code_problems": problems,
        "unapproved_source_code_nodes": unapproved_sc,
        "sandbox_warnings": sandbox_warnings,
    })


_MERMAID_ID_SAFE = re.compile(r"[^A-Za-z0-9_]")


def _mermaid_node_id(raw: str) -> str:
    """Return a Mermaid-safe node identifier.

    Mermaid IDs must be alphanumeric/underscore. Node IDs in our branches
    are usually snake_case so this is a noop for well-formed inputs.
    """
    cleaned = _MERMAID_ID_SAFE.sub("_", raw)
    if cleaned and cleaned[0].isdigit():
        cleaned = "n_" + cleaned
    return cleaned or "node"


def _mermaid_label(text: str) -> str:
    """Escape label text for use inside Mermaid's ``["..."]`` node form."""
    return text.replace('"', "'").replace("\n", " ")


def _branch_mermaid(branch: Any) -> str:
    """Render a BranchDefinition as a Mermaid ``flowchart LR`` block.

    Claude.ai and many markdown clients auto-render fenced ``mermaid``
    code blocks. The returned string includes the fence so callers can
    embed it directly in prose. START/END are rendered as stadium shapes;
    everything else uses the default rectangle with its display_name.
    """
    lines: list[str] = ["```mermaid", "flowchart LR"]

    # START/END get stadium shape so they read as terminals.
    lines.append('    START(["START"])')
    lines.append('    END(["END"])')

    for node in branch.node_defs:
        nid = _mermaid_node_id(node.node_id)
        label = _mermaid_label(node.display_name or node.node_id)
        lines.append(f'    {nid}["{label}"]')

    # Include graph_nodes that weren't also declared as node_defs.
    defined_ids = {_mermaid_node_id(n.node_id) for n in branch.node_defs}
    for gn in branch.graph_nodes:
        nid = _mermaid_node_id(gn.id)
        if nid not in defined_ids and nid not in ("START", "END"):
            lines.append(f'    {nid}["{gn.id}"]')
            defined_ids.add(nid)

    for edge in branch.edges:
        src = _mermaid_node_id(edge.from_node)
        dst = _mermaid_node_id(edge.to_node)
        lines.append(f"    {src} --> {dst}")

    for cedge in branch.conditional_edges:
        src = _mermaid_node_id(cedge.from_node)
        for label, target in cedge.conditions.items():
            dst = _mermaid_node_id(target)
            lines.append(f"    {src} -.{_mermaid_label(label)}.-> {dst}")

    if branch.entry_point:
        entry_id = _mermaid_node_id(branch.entry_point)
        if entry_id not in ("START", "END"):
            lines.append(f"    class {entry_id} entry")
            lines.append(
                "    classDef entry stroke:#4a90e2,stroke-width:3px"
            )

    lines.append("```")
    return "\n".join(lines)


# STATUS.md Approved-bugs 2026-04-22 reshape of BUG-018 (maintainer-notes).
# The wiki already carries the cross-reference surface this feature needs —
# instead of adding a per-node `related_notes` field to NodeDefinition,
# surface wiki pages whose text mentions the branch_def_id or any of its
# node_ids. Always-on (no flag); always-bounded (top 20, summary ≤140 chars).
_RELATED_WIKI_CAP = 20
_RELATED_SUMMARY_MAX = 140


def _related_summary(body: str, meta: dict[str, str]) -> str:
    """First prose paragraph of ``body`` clipped to ``_RELATED_SUMMARY_MAX``.

    Skips heading-only lines (``#`` prefix) when picking the first
    paragraph. Falls back to the frontmatter ``description`` field if
    no prose is found; empty string if neither exists.
    """
    paragraph: list[str] = []
    for raw_line in body.split("\n"):
        line = raw_line.strip()
        if not line:
            if paragraph:
                break
            continue
        if line.startswith("#"):
            if paragraph:
                break
            continue
        paragraph.append(line)
    text = " ".join(paragraph).strip()
    if not text:
        text = (meta.get("description", "") or "").strip()
    if len(text) > _RELATED_SUMMARY_MAX:
        # Reserve one char for the ellipsis so total stays ≤ cap.
        return text[: _RELATED_SUMMARY_MAX - 1].rstrip() + "…"
    return text


def _related_wiki_pages(branch: dict[str, Any]) -> dict[str, Any]:
    """Find wiki pages that mention this branch's id or any node id.

    Returns ``{"items": [...], "truncated_count": int}``. Each item has
    ``path``, ``title``, ``summary``, ``matched_via``. Sorted by
    (matched_via count desc, title asc). Capped at ``_RELATED_WIKI_CAP``.
    """
    bid = (branch.get("branch_def_id") or "").strip()
    node_ids: list[str] = []
    for n in branch.get("node_defs", []) or []:
        nid = (n.get("node_id") or "").strip() if isinstance(n, dict) else ""
        if nid and nid not in node_ids:
            node_ids.append(nid)

    terms: list[tuple[str, str]] = []
    if bid:
        terms.append(("branch_def_id", bid.lower()))
    for nid in node_ids:
        terms.append((f"node:{nid}", nid.lower()))
    if not terms:
        return {"items": [], "truncated_count": 0}

    from tinyassets.api import visibility

    pages = (
        _find_all_pages(_wiki_pages_dir()) + _find_all_pages(_wiki_drafts_dir())
    )
    scored: list[dict[str, Any]] = []
    for p in pages:
        raw = _read_text(p)
        if not raw:
            continue
        meta, body = _parse_frontmatter(raw)
        if not visibility.page_visible_in_listing(meta, universe_id=""):
            continue
        title = meta.get("title", p.stem)
        haystack = (title + "\n" + body).lower()
        matched_via: list[str] = []
        for label, needle in terms:
            if needle and needle in haystack:
                matched_via.append(label)
        if not matched_via:
            continue
        scored.append({
            "path": _page_rel_path(p),
            "title": title,
            "summary": _related_summary(body, meta),
            "matched_via": matched_via,
        })

    scored.sort(key=lambda x: (-len(x["matched_via"]), x["title"].lower()))
    total = len(scored)
    top = scored[:_RELATED_WIKI_CAP]
    truncated = total - len(top) if total > _RELATED_WIKI_CAP else 0
    return {"items": top, "truncated_count": truncated}


def _ext_branch_describe(kwargs: dict[str, Any]) -> str:
    from tinyassets.branches import BranchDefinition

    selector = kwargs.get("branch_def_id", "").strip()
    if not selector:
        return json.dumps({"error": "branch_def_id is required."})
    resolved = _resolve_readable_branch(selector, str(_base_path()))
    if resolved is None:
        return _branch_not_found(selector)
    bid, source_dict = resolved

    branch = BranchDefinition.from_dict(source_dict)
    errors = branch.validate()

    unapproved_sc = [
        {"node_id": nd.get("node_id", ""), "display_name": nd.get("display_name", "")}
        for nd in source_dict.get("node_defs", [])
        if _node_source_code_unapproved(nd)
    ]
    problems = _source_code_problems(source_dict.get("node_defs", []))

    node_lines = [
        f"  - {n.node_id}: {n.display_name}"
        + (f" ({n.phase})" if n.phase != "custom" else "")
        for n in branch.node_defs
    ] or ["  (no nodes yet)"]

    edge_lines = [
        f"  - {e.from_node} -> {e.to_node}" for e in branch.edges
    ] or ["  (no edges yet)"]

    state_lines = [
        f"  - {f.get('name')}: {f.get('type', 'str')}"
        + (f" [{f.get('reducer')}]" if f.get("reducer") else "")
        for f in branch.state_schema
    ] or ["  (no state fields yet)"]

    code_problem_lines = [
        f"  - CODE NODE '{n['node_id']}' ({n['display_name']}) will not compile: "
        + "; ".join(n["problems"])
        for n in problems
    ]

    problem_lines = (
        [f"  - {err}" for err in errors]
        if errors
        else ["  (none — structure is valid)"]
    )

    mermaid = _branch_mermaid(branch)

    summary_parts = [
        f"Branch: {branch.name or '(unnamed)'}  [{branch.branch_def_id}]",
        f"Author: {branch.author}   Domain: {branch.domain_id}",
        f"Entry point: {branch.entry_point or '(not set)'}",
        "",
        f"Nodes ({len(branch.node_defs)}):",
        *node_lines,
        "",
        f"Edges ({len(branch.edges)}):",
        *edge_lines,
        "",
        f"State schema ({len(branch.state_schema)}):",
        *state_lines,
        "",
        "Open problems:",
        *problem_lines,
    ]
    if code_problem_lines:
        summary_parts += ["", "Code problems (branch NOT runnable):"]
        summary_parts += code_problem_lines
    run_note = (
        "Note: a code node above will not compile, so this branch cannot run "
        "until it is fixed."
        if problems
        else (
            f'Note: run this branch with run_graph branch_def_id="{bid}" '
            'inputs_json="<state JSON>" once validated.'
        )
    )
    summary_parts += ["", "Graph:", mermaid, "", run_note]
    summary = "\n".join(summary_parts)
    related = _related_wiki_pages(source_dict)

    # Lineage: expose fork_from + compute fork_descendants.
    fork_from = source_dict.get("fork_from")
    if fork_from and _resolve_readable_version(
        fork_from,
        str(_base_path()),
    ) is None:
        fork_from = None
    from tinyassets.branch_versions import list_branch_versions
    from tinyassets.daemon_server import list_branch_definitions

    my_versions = list_branch_versions(_base_path(), bid, limit=500)
    my_version_ids = {v.branch_version_id for v in my_versions
                      if _resolve_readable_version(v.branch_version_id, str(_base_path()))}
    fork_descendants: list[dict[str, Any]] = []
    for b in list_branch_definitions(
        _base_path(),
        viewer=_request_branch_actor() or "",
    ):
        ff = b.get("fork_from")
        if ff and ff in my_version_ids:
            fork_descendants.append({
                "branch_def_id": b["branch_def_id"],
                "author": b.get("author", ""),
                "published_versions_count": len(
                    [v for v in list_branch_versions(_base_path(), b["branch_def_id"], limit=500)
                     if v.public]
                ),
            })

    response = {
        "branch_def_id": bid,
        "summary": summary,
        "mermaid": mermaid,
        "valid": not errors,
        "error_count": len(errors),
        "runnable": not errors and not problems,
        "source_code_problems": problems,
        "unapproved_source_code_nodes": unapproved_sc,
        "fork_descendants": fork_descendants,
        "related_wiki_pages": related["items"],
        "related_wiki_pages_truncated": related["truncated_count"],
    }
    if fork_from is not None:
        response["fork_from"] = fork_from
    return json.dumps(response)


# ── Composite: build_branch / patch_branch ────────────────────────────────
# Per docs/specs/composite_branch_actions.md: Claude.ai's per-turn tool-call
# budget tops out around 15–20 atomic actions, below a full workflow build.
# Composite actions let a client ship one spec / one batch and get back a
# validated branch. build_branch is strict-with-suggestions (reject
# ambiguous, propose concrete fixes). patch_branch is transactional (all
# ops land or none).


_VALID_STATE_TYPES = {"str", "int", "float", "bool", "list", "dict", "any"}


def _branch_authoring_batch_receipt(
    branch: Any,
    *,
    action: str,
    operation_count: int,
    request_id: str = "",
) -> dict[str, Any]:
    """Return structured evidence for one composite Branch authoring call."""
    node_defs = list(getattr(branch, "node_defs", []) or [])
    source_code_node_count = 0
    approved_source_code_node_count = 0
    unapproved_nodes: list[dict[str, str]] = []
    for node in node_defs:
        if not getattr(node, "source_code", ""):
            continue
        source_code_node_count += 1
        # Provenance: an approval counts only when backed by the matching
        # source hash. It does NOT gate execution (see gates_execution below).
        if _approval_provenance_valid(
            getattr(node, "approved", False),
            getattr(node, "source_code", "") or "",
            getattr(node, "approved_source_hash", "") or "",
        ):
            approved_source_code_node_count += 1
        else:
            unapproved_nodes.append({
                "node_id": getattr(node, "node_id", ""),
                "display_name": getattr(node, "display_name", ""),
            })

    code_problems = _source_code_problems(node_defs)
    receipt: dict[str, Any] = {
        "receipt_type": "branch_authoring_batch",
        "action": action,
        "actor": _request_branch_actor(),
        "branch_def_id": getattr(branch, "branch_def_id", ""),
        "branch_name": getattr(branch, "name", ""),
        "operation_count": operation_count,
        "node_count": len(node_defs),
        "edge_count": len(getattr(branch, "edges", []) or []),
        "skill_count": len(getattr(branch, "skills", []) or []),
        "state_field_count": len(getattr(branch, "state_schema", []) or []),
        # The execution choices that are actually stored after this call.
        # This is a REPORT, not a check. `_validate_llm_policy_shape`
        # deliberately tolerates unknown policy keys for forward-compat, and
        # this echo returns the stored policy dict verbatim — so a key
        # misspelled INSIDE that dict is echoed back looking applied, because
        # it was in fact stored. Detecting that would need a policy-key
        # allowlist, which this change explicitly does not add.
        # What the echo does expose is the FIELD-level miss: a misspelled
        # top-level spec key (`default_llm_polcy`) leaves the choice `null`
        # here while the call still reports "built"/"patched", so an author
        # can see that nothing was applied instead of assuming it was. Both
        # are `null` when unset — clearing and never-setting are the same fact.
        "execution_choices": {
            "default_llm_policy": getattr(branch, "default_llm_policy", None),
            "concurrency_budget": getattr(branch, "concurrency_budget", None),
        },
        "validation": {
            "status": "ok",
            "valid": True,
            "error_count": 0,
        },
        "source_code_approval": {
            "source_code_node_count": source_code_node_count,
            "approved_count": approved_source_code_node_count,
            "unapproved_count": len(unapproved_nodes),
            "unapproved_nodes": unapproved_nodes,
            # Approval is provenance. A code node runs in the OS sandbox whether
            # or not anyone approved it; what stops a run is a code node the
            # compiler refuses, listed under `problems`.
            "gates_execution": False,
            "problems": code_problems,
            "runnable": not code_problems,
        },
        "authorization_effect": {
            "grants_authorization": False,
            "grants_scoped_trust_session": False,
            "bypasses_client_approval_prompts": False,
            "approved_action_scope": [],
            "revocation_handle": None,
            "note": (
                "Evidence-only receipt: clients may display or audit it, but must "
                "not treat it as permission to execute future writes."
            ),
        },
        "caveats": [
            "This receipt records what landed; it is not an authorization grant.",
            (
                "It does not bypass source_code approval, host-owned gates, "
                "or client approval prompts."
            ),
        ],
    }

    normalized_request_id = str(request_id or "").strip()
    if normalized_request_id:
        receipt["plan_context"] = {
            "request_id": normalized_request_id,
            "authoritative": False,
            "note": (
                "Caller-supplied context for correlating this batch; "
                "not an approval token."
            ),
        }
    return receipt


def _suggest_entry_point(branch: Any) -> str:
    """The node nothing points at — the head of the graph — else the first one.

    Also the DEFAULT applied when a spec omits ``entry_point`` entirely
    (``_staged_branch_from_spec``), so a conditional edge's targets count as
    incoming too: a router whose branches feed back would otherwise be picked
    as the head over the node that actually starts the flow.
    """
    if not branch.graph_nodes:
        return ""
    incoming: set[str] = set()
    for e in branch.edges:
        # An edge FROM START does not make its target "pointed at" -- START is
        # where the run begins, so its target is the head, not a successor.
        # Codex refute, PR #4108: with node order [second, first] and an
        # explicit START -> first edge, `first` counted as having an incoming
        # edge, every node did, and the fallback picked graph_nodes[0] --
        # `second`, which the runtime then ran twice.
        if e.from_node == "START":
            continue
        if e.to_node and e.to_node != "START":
            incoming.add(e.to_node)
    for ce in getattr(branch, "conditional_edges", None) or ():
        for target in (ce.conditions or {}).values():
            if target and target != "START":
                incoming.add(target)
    for gn in branch.graph_nodes:
        if gn.id not in incoming:
            return gn.id
    return branch.graph_nodes[0].id


#: Prefix on a staging string that is ADVISORY: the spec was accepted and this
#: says what was adjusted. Stripped before the author sees it. A sentinel rather
#: than a second return value because `_apply_*_spec` is a family of functions
#: all returning `str`, and a one-off tuple in the middle of them is how the
#: next caller forgets to look at it.
_STATE_COERCION_NOTICE = "\x00notice\x00"

#: JSON Schema's type names, which are what a model writes when it has been
#: asked for a schema. These are EXACT synonyms, not guesses, so accepting them
#: is silent: live 2026-09-30 (turn f3617ca3 round 3) a spec sent
#: ``{"focus_note": "string"}`` and the build was REFUSED for it, with the
#: reason reported as a coercion of a type it had already resolved correctly.
_STATE_TYPE_SYNONYMS = {
    "string": "str", "text": "str",
    "integer": "int", "number": "float", "double": "float",
    "boolean": "bool",
    "array": "list", "object": "dict",
    "null": "any", "none": "any",
}


def _closest_state_type(raw: str) -> str:
    lower = (raw or "").lower()
    if lower in _VALID_STATE_TYPES:
        return lower
    if lower in _STATE_TYPE_SYNONYMS:
        return _STATE_TYPE_SYNONYMS[lower]
    for valid in sorted(_VALID_STATE_TYPES):
        if valid.startswith(lower) or lower.startswith(valid):
            return valid
    return "any"


def _state_type_is_exact(raw: str) -> bool:
    """Was this type name understood outright, rather than guessed at?

    An exact name or a known synonym is not worth telling the author about; a
    guess (``"strang"`` -> ``str``) is.
    """
    lower = (raw or "").lower()
    return lower in _VALID_STATE_TYPES or lower in _STATE_TYPE_SYNONYMS


def _spec_offered_nodes(spec: Any) -> bool:
    """Did the caller supply at least one node entry, in any accepted container?"""
    if not isinstance(spec, dict):
        return False
    graph = spec.get("graph")
    containers = [spec.get("node_defs"), spec.get("nodes")]
    if isinstance(graph, dict):
        containers += [graph.get("node_defs"), graph.get("nodes")]
    return any(isinstance(c, list) and c for c in containers)


def _without_cascades(
    spec: Any, staging_errors: list[str], validation_errors: list[str],
) -> list[str]:
    """Drop validation errors that are artefacts of a node that failed staging.

    A node whose spec is invalid is never appended to the branch, so `validate()`
    then sees an EMPTY branch and reports "Branch must have at least one node."
    beside the real error. Live 2026-09-30 round 13: the spec supplied a node and
    was told it had none, and the model concluded its `node_defs` key was
    unrecognized — round 17 went looking for a different container shape. Two
    errors for one defect is worse than one, because the second is false.

    Only the "no nodes" family is dropped, and only when the caller actually
    offered nodes AND staging rejected one. An empty `node_defs: []` still gets
    the honest answer (round 11 got it, and it was right).
    """
    if not staging_errors or not _spec_offered_nodes(spec):
        return validation_errors
    if not any(err.startswith("node[") for err in staging_errors):
        return validation_errors
    return [
        err for err in validation_errors
        if "at least one node" not in err.lower()
    ]


def _errors_to_suggestions(
    branch: Any, errors: list[str],
) -> list[dict[str, str]]:
    suggestions: list[dict[str, str]] = []
    for err in errors:
        low = err.lower()
        if "entry point is required" in low:
            suggestions.append({
                "issue": err,
                "proposed_fix": (
                    f"Set entry_point to '{_suggest_entry_point(branch)}'."
                    if _suggest_entry_point(branch)
                    else "Add at least one node before setting entry_point."
                ),
            })
        elif "not a defined node" in low or "is not defined" in low:
            suggestions.append({
                "issue": err,
                "proposed_fix": (
                    "Either add the missing node via node_defs, or remove "
                    "the edge / entry_point that references it."
                ),
            })
        elif "not reachable from" in low:
            suggestions.append({
                "issue": err,
                "proposed_fix": (
                    "Add an incoming edge from a reachable node, or remove "
                    "the orphan node."
                ),
            })
        elif "cycle without exit" in low:
            suggestions.append({
                "issue": err,
                "proposed_fix": (
                    "Add an edge from a node inside the cycle to END, or "
                    "convert one edge to a conditional edge with an END "
                    "target."
                ),
            })
        elif "collides with a graph node id" in low:
            state_field = ""
            match = re.search(r"State field name '([^']+)'", err)
            if match:
                state_field = match.group(1)
            suggestions.append({
                "issue": err,
                "proposed_fix": (
                    f"Rename state_schema field '{state_field}' or the "
                    f"graph node ID '{state_field}' so they are distinct "
                    "before running this branch."
                    if state_field
                    else "Rename the colliding state_schema field or graph "
                    "node ID so they are distinct before running this branch."
                ),
            })
        elif "at least one node" in low:
            # Names ONLY the container staging reads. The previous wording
            # ("Add at least one node_def + graph_node entry") advertised a
            # `graph_nodes` key that `_staged_branch_from_spec` has no reader
            # for — it synthesizes the graph node from each node_def itself —
            # and live 2026-09-30 round 17 followed it into that shape.
            suggestions.append({
                "issue": err,
                "proposed_fix": (
                    'Add one entry to node_defs, e.g. {"node_id": "n1", '
                    '"prompt_template": "..."}. The graph node is derived from '
                    "it; you do not pass graph_nodes."
                ),
            })
        elif "branch name is required" in low:
            suggestions.append({
                "issue": err,
                "proposed_fix": "Pass a non-empty 'name' in the spec.",
            })
        elif "duplicate" in low:
            suggestions.append({
                "issue": err,
                "proposed_fix": "Rename the duplicate id to a unique value.",
            })
        else:
            suggestions.append({"issue": err, "proposed_fix": _concrete_fix(err)})
    return suggestions


#: Spec keys a validation error can name, longest first so a match on
#: ``display_name`` is never also reported as ``name``.
_SPEC_FIELD_VOCABULARY = tuple(sorted((
    "node_id", "display_name", "prompt_template", "source_code", "input_keys",
    "output_keys", "tools_allowed", "strict_input_isolation", "timeout_seconds",
    "model_hint", "reasoning_effort", "llm_policy", "effects", "workspace",
    "phase", "entry_point", "state_schema", "node_defs", "conditional_edges",
    "edges", "io_manifest", "description", "skills", "visibility", "name",
    "concurrency_budget", "default_llm_policy", "checkpoints", "intent",
), key=len, reverse=True))

#: The top-level keys a create spec accepts. The last-resort fix names them,
#: because a caller who cannot tell which key is at fault can at least tell
#: which keys exist.
_SPEC_TOP_LEVEL_KEYS = (
    "name, description, node_defs, edges, conditional_edges, entry_point, "
    "state_schema, io_manifest, skills"
)


def _concrete_fix(err: str) -> str:
    """A fix naming the spec keys the error itself names.

    Replaces "Review this error and reshape the spec." — which told the caller
    nothing it did not already know, and was the ONLY guidance the live
    2026-09-30 loop got for its round-13 rejection (turn
    ``c7d6279d4af74d798375d3f13780140e``). Every validator error already names
    the field or id at fault; this says which key in the SUBMITTED spec that is,
    so the caller edits one key instead of reshaping the whole spec.
    """
    fields: list[str] = []
    for field in _SPEC_FIELD_VOCABULARY:
        if field in err and not any(field in seen for seen in fields):
            fields.append(field)
    if not fields:
        return (
            "Apply the change this error names. A create spec's keys are: "
            f"{_SPEC_TOP_LEVEL_KEYS}."
        )
    named = ", ".join(sorted(fields))
    where = ""
    match = re.search(r"node '([^']+)'", err)
    if match:
        where = f" on node_defs entry '{match.group(1)}'"
    return f"Change {named}{where}, as the error states: {err}"


def _resolve_node_spec(
    raw: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """Resolve a raw node spec that may contain ``node_ref`` or just a
    ``node_id`` that collides with an existing standalone/branch node.

    Returns ``(resolved_spec, error)``. On success ``error`` is empty
    and ``resolved_spec`` is a fully-populated dict ready to build a
    ``NodeDefinition`` from. On failure ``resolved_spec`` is ``None``
    and ``error`` explains what the caller must do.

    The shape changes we accept are:

    - ``node_ref={"source": "standalone", "node_id": "X"}`` — copy the
      canonical standalone registration X into this branch.
    - ``node_ref={"source": "<branch_def_id>", "node_id": "X"}`` —
      copy node X from another branch.
    - Plain inline spec (``node_id``/``display_name``/...): used as-is,
      EXCEPT we refuse to silently shadow an existing standalone
      registration (#66). The caller must either pick a different
      ``node_id`` or pass ``intent="copy"`` to opt into the copy.

    ``intent="reference"`` is reserved for a future live-reference
    mode. v1 only supports ``intent="copy"``; other values error.
    """
    from tinyassets.api.extensions import _load_nodes

    nid = (raw.get("node_id") or "").strip()
    intent = (raw.get("intent") or "").strip().lower()
    if intent and intent not in ("copy", "reference"):
        return None, (
            f"intent='{raw.get('intent')}' is unknown. "
            "Use 'copy' to snapshot an existing node into this "
            "branch, or omit intent and pass inline fields."
        )
    if intent == "reference":
        return None, (
            "intent='reference' (live shared node) is not supported "
            "yet. Use intent='copy' to snapshot a standalone node "
            "into this branch."
        )

    node_ref = raw.get("node_ref")
    if node_ref:
        if not isinstance(node_ref, dict):
            return None, "node_ref must be an object with 'source' and 'node_id'."
        ref_source = (node_ref.get("source") or "").strip()
        ref_nid = (node_ref.get("node_id") or nid).strip()
        if not ref_source or not ref_nid:
            return None, "node_ref requires 'source' and 'node_id'."
        if ref_source != "standalone":
            resolved_source = _resolve_readable_branch(
                ref_source,
                str(_base_path()),
            )
            if resolved_source is None:
                return None, _branch_not_found_message(ref_source)
            ref_source = resolved_source[0]
        resolved, err = _lookup_node_body(ref_source, ref_nid)
        if err:
            return None, err
        # Start from the resolved body, then overlay any caller-supplied
        # fields so the client can, e.g., rename the copy.
        merged: dict[str, Any] = dict(resolved)
        merged["node_id"] = nid or ref_nid
        for field_key in (
            "display_name", "description", "phase", "input_keys",
            "output_keys", "strict_input_isolation", "source_code",
            "prompt_template", "tools_allowed", "timeout_seconds",
        ):
            if field_key in raw and raw[field_key] not in (None, ""):
                merged[field_key] = raw[field_key]
        # SECURITY (Codex ADAPT, PR #1349): approval provenance must follow
        # the *executable content*, never the inherited boolean. A caller can
        # node_ref an approved node and then override ``source_code`` — that
        # forges/staleifies approval for code the approver never saw. Approval
        # only survives when the effective source hash still matches the
        # approved hash; otherwise strip every approval field so this copy is
        # treated as unapproved and re-runs the approve gate.
        _reconcile_copied_approval(merged)
        return merged, ""

    # No explicit ref — fall back to raw. If the node_id shadows a
    # standalone registration, demand explicit intent so the caller
    # cannot silently create a hollow clone.
    if nid and intent != "copy":
        try:
            standalone = _load_nodes()
        except Exception:
            standalone = []
        hit = next(
            (n for n in standalone if n.get("node_id") == nid), None,
        )
        if hit:
            return None, (
                f"node_id '{nid}' matches an existing standalone "
                "registered node. Pass node_ref="
                f"{{'source': 'standalone', 'node_id': '{nid}'}} to "
                "copy its body into this branch, or pass intent='copy' "
                "on this spec if you intentionally want the existing "
                "body, or rename this node to avoid collision."
            )
    raw = dict(raw)
    raw.pop("approved", None)
    raw.pop("author", None)
    return raw, ""


def _lookup_node_body(
    source: str, node_id: str,
) -> tuple[dict[str, Any], str]:
    """Return the canonical node body for a ``node_ref`` lookup.

    ``source`` is either the literal string ``'standalone'`` (look in
    the standalone node registry) or a branch_def_id (look in that
    branch's ``node_defs``).
    """
    from tinyassets.api.extensions import _load_nodes

    if source == "standalone":
        try:
            nodes = _load_nodes()
        except Exception as exc:
            return {}, f"could not load standalone node registry: {exc}"
        hit = next(
            (n for n in nodes if n.get("node_id") == node_id), None,
        )
        if not hit:
            return {}, (
                f"standalone node '{node_id}' not found. "
                "Global registered-node discovery is not exposed by the "
                "advertised handles."
            )
        return {
            "node_id": hit.get("node_id", node_id),
            "display_name": hit.get("display_name", node_id),
            "description": hit.get("description", ""),
            "phase": hit.get("phase", "custom"),
            "input_keys": list(hit.get("input_keys") or []),
            "output_keys": list(hit.get("output_keys") or []),
            "tools_allowed": list(hit.get("tools_allowed") or []),
            "strict_input_isolation": bool(
                hit.get("strict_input_isolation", True),
            ),
            "source_code": hit.get("source_code", ""),
            "prompt_template": hit.get("prompt_template", ""),
            "author": hit.get("author", ""),
            "approved": bool(hit.get("approved", False)),
            "approved_by": hit.get("approved_by", ""),
            "approved_at": hit.get("approved_at", ""),
            "approved_source_hash": hit.get("approved_source_hash", ""),
            "approval_reason": hit.get("approval_reason", ""),
        }, ""

    # Otherwise treat `source` as a branch_def_id.
    from tinyassets.daemon_server import get_branch_definition

    try:
        source_branch = get_branch_definition(
            _base_path(), branch_def_id=source,
        )
    except KeyError:
        return {}, (
            f"node_ref source '{source}' is neither 'standalone' nor a "
            "known branch_def_id."
        )
    for nd in source_branch.get("node_defs") or []:
        if nd.get("node_id") == node_id:
            return {
                "node_id": nd.get("node_id", node_id),
                "display_name": nd.get("display_name", node_id),
                "description": nd.get("description", ""),
                "phase": nd.get("phase", "custom"),
                "input_keys": list(nd.get("input_keys") or []),
                "output_keys": list(nd.get("output_keys") or []),
                "tools_allowed": list(nd.get("tools_allowed") or []),
                "strict_input_isolation": bool(
                    nd.get("strict_input_isolation", True),
                ),
                "source_code": nd.get("source_code", ""),
                "prompt_template": nd.get("prompt_template", ""),
                "author": nd.get("author", ""),
                "approved": bool(nd.get("approved", False)),
                "approved_by": nd.get("approved_by", ""),
                "approved_at": nd.get("approved_at", ""),
                "approved_source_hash": nd.get("approved_source_hash", ""),
                "approval_reason": nd.get("approval_reason", ""),
            }, ""
    # This string flows into both build_branch and patch_branch(add_node)
    # rejection `text` (task #58's text-channel rule) via `_apply_node_spec`
    # -> `staging_errors`/`_apply_patch_op`'s per-op error, so it must not
    # carry `source` (the resolved branch_def_id) -- the node id alone, and
    # a generic "referenced branch" wording, are enough to act on.
    return {}, f"node '{node_id}' not found on the referenced branch."


#: JSON's own words for a Python type, so an error names what the CALLER sent
#: rather than a Python class. "got str" is actionable where "AttributeError" is
#: not (live 2026-09-30, turn f3617ca3 round 3).
_JSON_TYPE_NAMES = {
    dict: "an object", list: "an array", str: "a string", bool: "a boolean",
    int: "a number", float: "a number", type(None): "null",
}


def _json_type_name(value: Any) -> str:
    return _JSON_TYPE_NAMES.get(type(value), "an unsupported value")


#: How much caller text an error may quote back. Long enough to recognise the
#: value, short enough that a 50kB field name is not the error message.
_ECHO_MAX = 80


def _echo(value: Any) -> str:
    """Quote caller text back SAFELY: escaped, bounded, one line.

    An error names the value the caller sent, which means caller-controlled
    bytes land in a served tool result. Escaped because a raw control character
    is unreadable there and moves a terminal cursor (the same reason
    ``_payload_json_error`` escapes its excerpt), and bounded because the field
    name is as unbounded as the payload. Found while testing whether the notice
    sentinel could be forged: it cannot, but the caller's ``\\x00`` was being
    echoed verbatim.
    """
    text = value if isinstance(value, str) else str(value)
    clipped = text[:_ECHO_MAX]
    escaped = repr(clipped)[1:-1]
    return escaped + ("..." if len(text) > _ECHO_MAX else "")


def _apply_node_spec(branch: Any, raw: Any) -> str:
    from tinyassets.branches import GraphNodeRef, NodeDefinition

    if not isinstance(raw, dict):
        return (
            'node spec must be an object, e.g. {"node_id": "n1", '
            '"prompt_template": "..."} '
            f"(got {_json_type_name(raw)})"
        )
    resolved, err = _resolve_node_spec(raw)
    if err:
        return err
    raw = resolved  # resolved may be the same dict, or a merged copy

    nid = (raw.get("node_id") or "").strip()
    # `display_name` is a LABEL, and a node that has an id already has a usable
    # one. Live 2026-09-30: this pair was reported as one error, so a spec whose
    # node_id was present read as "node_id missing" and the model kept resending
    # the id it had already supplied. Defaulting removes the round entirely, and
    # the remaining error names the ONE field that has no default.
    display = (raw.get("display_name") or "").strip() or nid
    if not nid:
        return (
            "node spec missing 'node_id' (a short id for this node, e.g. "
            "\"n1\"). 'display_name' is optional and defaults to node_id"
        )

    source_code = raw.get("source_code") or ""
    prompt_template = raw.get("prompt_template") or ""
    if source_code and prompt_template:
        return (
            f"node '{nid}' has both source_code and prompt_template — "
            "pick one."
        )
    strict_input_isolation = raw.get("strict_input_isolation", True)
    if not isinstance(strict_input_isolation, bool):
        return (
            f"node '{nid}' strict_input_isolation must be a JSON boolean "
            "(true or false)."
        )

    phase = (raw.get("phase") or "").strip() or "custom"
    in_keys, err = _coerce_node_keys(raw.get("input_keys"), "input_keys")
    if err:
        return err
    out_keys, err = _coerce_node_keys(raw.get("output_keys"), "output_keys")
    if err:
        return err
    tools_allowed, err = _coerce_node_keys(
        raw.get("tools_allowed"), "tools_allowed",
    )
    if err:
        return err
    model_hint, err = _coerce_model_hint_update(
        raw.get("model_hint", ""), "model_hint",
    )
    if err:
        return err
    reasoning_effort = str(raw.get("reasoning_effort", "") or "").strip().lower()
    if reasoning_effort and reasoning_effort not in _VALID_REASONING_EFFORTS:
        return (
            f"node '{nid}' reasoning_effort must be empty or one of: "
            f"{', '.join(sorted(_VALID_REASONING_EFFORTS))}"
        )
    llm_policy, err = _coerce_llm_policy_update(
        raw.get("llm_policy"), f"node '{nid}' llm_policy",
    )
    if err:
        return err
    timeout_seconds_raw = raw.get("timeout_seconds", 300.0)
    if timeout_seconds_raw in (None, ""):
        timeout_seconds = 300.0
    else:
        try:
            timeout_seconds = float(timeout_seconds_raw)
        except (TypeError, ValueError):
            return f"node '{nid}' timeout_seconds must be a number."
    # BUG-045: thread the three sub-branch / sibling-run spec fields. The
    # compiler reads them (tinyassets/graph_compiler.py:_build_invoke_branch /
    # invoke_branch_version / await_run callables) and NodeDefinition
    # declares them (tinyassets/branches.py:267/285/294), but this authoring
    # plumbing was silently dropping the keys — callers got a node that
    # validated fine but ran as a no-op prompt-template. Mutual exclusivity
    # is enforced in BranchDefinition.validate(); we accept whatever the
    # caller provided and let validate() catch invalid combinations.
    invoke_branch = raw.get("invoke_branch_spec")
    invoke_branch_version = raw.get("invoke_branch_version_spec")
    await_run = raw.get("await_run_spec")
    invoke_branch_arg = invoke_branch if isinstance(invoke_branch, dict) else None
    invoke_branch_version_arg = (
        invoke_branch_version if isinstance(invoke_branch_version, dict) else None
    )
    await_run_arg = await_run if isinstance(await_run, dict) else None
    # PR-122 Phase 1: ``effects`` declares external-write sinks the node's
    # outputs should be routed to after the run completes. Validated by
    # NodeDefinition.__post_init__ (list of strings) — same partition
    # pattern as input_keys/output_keys plumbing.
    effects_raw = raw.get("effects", [])
    if effects_raw is None:
        effects_arg: list[str] = []
    elif isinstance(effects_raw, list):
        effects_arg = list(effects_raw)
    else:
        return (
            f"node '{nid}' effects must be a JSON array of strings"
        )
    # ``workspace`` names the ancestor checkout node whose workspace is bound
    # at /workspace for this node (workspace-node D2). It was reaching
    # NodeDefinition from nowhere: the served builder never passed it, so a
    # spec that declared it built a node WITHOUT the binding and the ``ws``
    # object simply did not exist at run time. Found live 2026-08-31 - the
    # universe reported "the build tool exposed to me here refuses workspace
    # nodes" while every unit test constructed NodeDefinition directly and so
    # could not see it.
    workspace_raw = raw.get("workspace", "")
    if workspace_raw is None:
        workspace_arg = ""
    elif isinstance(workspace_raw, str):
        workspace_arg = workspace_raw.strip()
    else:
        return (
            f"node '{nid}' workspace must be the node id of an ancestor "
            "checkout, as a string"
        )
    # SECURITY (Codex ADAPT, PR #1349): a node is only approved when the
    # recorded approval hash matches the *effective* source_code being stored.
    # This is the authoring-time half of the provenance gate; the compiler
    # enforces the same check at run time (_validate_source_code). Without
    # this, a caller could pass a bare ``approved=True`` (or inherit one via
    # an inline override) for code no approver ever reviewed. We only carry
    # the approval boolean + provenance forward when the hash matches; any
    # mismatch demotes the node to unapproved with blank provenance.
    approved_source_hash = (raw.get("approved_source_hash") or "").strip()
    if _approval_provenance_valid(
        raw.get("approved"), source_code, approved_source_hash,
    ):
        approved_arg = bool(raw.get("approved"))
        approved_by_arg = raw.get("approved_by") or ""
        approved_at_arg = raw.get("approved_at") or ""
        approved_hash_arg = approved_source_hash if source_code else ""
        approval_reason_arg = raw.get("approval_reason") or ""
    else:
        approved_arg = False
        approved_by_arg = ""
        approved_at_arg = ""
        approved_hash_arg = ""
        approval_reason_arg = ""
    try:
        node = NodeDefinition(
            node_id=nid,
            display_name=display,
            description=raw.get("description", ""),
            phase=phase,
            input_keys=in_keys,
            output_keys=out_keys,
            tools_allowed=tools_allowed,
            strict_input_isolation=strict_input_isolation,
            source_code=source_code,
            prompt_template=prompt_template,
            model_hint=model_hint,
            reasoning_effort=reasoning_effort,
            llm_policy=llm_policy,
            timeout_seconds=timeout_seconds,
            author=raw.get("author") or ledger_actor(),
            approved=approved_arg,
            approved_by=approved_by_arg,
            approved_at=approved_at_arg,
            approved_source_hash=approved_hash_arg,
            approval_reason=approval_reason_arg,
            invoke_branch_spec=invoke_branch_arg,
            invoke_branch_version_spec=invoke_branch_version_arg,
            await_run_spec=await_run_arg,
            effects=effects_arg,
            workspace=workspace_arg,
        )
    except ValueError as exc:
        return str(exc)

    if any(n.node_id == nid for n in branch.node_defs):
        return f"node '{nid}' already exists on the branch"

    branch.node_defs.append(node)
    branch.graph_nodes.append(GraphNodeRef(
        id=nid, node_def_id=nid, position=len(branch.graph_nodes),
    ))
    return ""


#: Every spelling accepted for an edge's origin, in lookup order. ``source`` /
#: ``target`` are LangGraph's own vocabulary (``add_edge`` in its docs and in
#: every serialized graph it emits), so a model that knows LangGraph writes them
#: — live 2026-09-30, round 20 of turn c7d6279d: a spec with
#: ``{"source": "n1", "target": "END"}`` was told the edge was "missing 'from'
#: or 'to'", which reads as a missing VALUE rather than a different key name.
_EDGE_FROM_KEYS = ("from", "from_node", "source")
_EDGE_TO_KEYS = ("to", "to_node", "target")


def _edge_endpoint(raw: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _edge_keys_phrase(keys: tuple[str, ...]) -> str:
    return " / ".join(f"'{key}'" for key in keys)


def _apply_edge_spec(branch: Any, raw: Any) -> str:
    from tinyassets.branches import EdgeDefinition

    if not isinstance(raw, dict):
        return (
            'edge spec must be an object, e.g. {"from": "n1", "to": "END"} '
            f"(got {_json_type_name(raw)})"
        )
    src = _edge_endpoint(raw, _EDGE_FROM_KEYS)
    dst = _edge_endpoint(raw, _EDGE_TO_KEYS)
    if not src or not dst:
        missing = []
        if not src:
            missing.append(f"an origin ({_edge_keys_phrase(_EDGE_FROM_KEYS)})")
        if not dst:
            missing.append(f"a destination ({_edge_keys_phrase(_EDGE_TO_KEYS)})")
        return (
            "edge spec needs " + " and ".join(missing)
            + '; e.g. {"from": "n1", "to": "END"}'
        )
    branch.edges.append(EdgeDefinition(from_node=src, to_node=dst))
    return ""


def _apply_conditional_edge_spec(branch: Any, raw: Any) -> str:
    from tinyassets.branches import ConditionalEdge

    if not isinstance(raw, dict):
        return (
            'conditional edge spec must be an object, e.g. {"from": "route", '
            '"conditions": {"yes": "n2", "no": "END"}} '
            f"(got {_json_type_name(raw)})"
        )
    src = _edge_endpoint(raw, _EDGE_FROM_KEYS)
    if not src:
        return (
            "conditional edge spec needs an origin "
            f"({_edge_keys_phrase(_EDGE_FROM_KEYS)})"
        )
    conditions_raw = raw.get("conditions")
    if not isinstance(conditions_raw, dict) or not conditions_raw:
        return (
            "conditional edge spec requires a non-empty 'conditions' "
            "object mapping outcome strings to target node ids"
        )
    conditions: dict[str, str] = {}
    for outcome, target in conditions_raw.items():
        outcome_str = str(outcome).strip()
        target_str = str(target).strip()
        if not outcome_str or not target_str:
            return (
                "conditional edge outcome/target must be non-empty strings"
            )
        conditions[outcome_str] = target_str
    # Merge onto any existing edge from the same source so callers can
    # add one outcome at a time without wiping siblings.
    for existing in branch.conditional_edges:
        if existing.from_node == src:
            existing.conditions.update(conditions)
            return ""
    branch.conditional_edges.append(
        ConditionalEdge(from_node=src, conditions=conditions)
    )
    return ""


def _normalized_state_schema(raw: Any) -> tuple[list[Any], str]:
    """Every reasonable way to write a state schema -> the canonical field list.

    Live 2026-09-30, turn ``f3617ca3a91d4acab30eea8dbbeb2663`` round 3: a spec
    sent ``"state_schema": {"focus_note": "string"}`` -- a MAPPING of name to
    type, which is the obvious way to write it and the shape a JSON-schema
    habit produces. Staging iterated it, got the KEY string ``"focus_note"``,
    called ``.get("name")`` on a ``str``, and the served handler's backstop
    turned that into ``branch build rejected (AttributeError).`` -- an exception
    class name, which the model cannot act on.

    Four accepted input shapes, all unambiguous:

    * ``[{"name": ..., "type": ...}, ...]`` -- canonical, returned as-is.
    * ``{"fields": [...]}`` -- the response shape; already tolerated by
      ``_sanitize_served_branch_spec``, so staging must agree or the sanitizer
      is validating a shape the builder rejects.
    * ``{"focus_note": "string"}`` -- name -> type. A dict VALUE may also be
      the field object itself (``{"focus_note": {"type": "str"}}``), in which
      case the key supplies the name.
    * ``["focus_note", ...]`` -- bare names; a name with no type is still a
      name, and ``type`` already defaults.

    Returns ``(entries, error)``. A non-empty ``error`` names ``state_schema``
    and says what it needs -- never a class name.
    """
    if raw is None or raw == "" or raw == [] or raw == {}:
        return [], ""
    if isinstance(raw, dict):
        fields = raw.get("fields")
        if isinstance(fields, list):
            return fields, ""
        if "fields" in raw:
            return [], (
                "state_schema 'fields' must be a JSON array of field objects, "
                'e.g. {"fields": [{"name": "focus_note", "type": "str"}]}'
            )
        entries: list[Any] = []
        for name, value in raw.items():
            key = str(name).strip()
            if not key:
                return [], "state_schema has a field with an empty name"
            if isinstance(value, dict):
                entries.append({**value, "name": value.get("name") or key})
            elif isinstance(value, str):
                entries.append({"name": key, "type": value})
            else:
                return [], (
                    f"state_schema field '{key}' must map to a type name "
                    'like "str", or to an object like {"type": "str"}'
                )
        return entries, ""
    if isinstance(raw, list):
        entries = []
        for item in raw:
            entries.append({"name": item.strip()} if isinstance(item, str) else item)
        return entries, ""
    return [], (
        "state_schema must be a JSON array of field objects, or an object "
        'mapping each field name to its type, e.g. {"focus_note": "str"}'
    )


def _apply_state_field_spec(branch: Any, raw: Any) -> str:
    if not isinstance(raw, dict):
        return (
            "state field spec must be an object with a 'name', e.g. "
            '{"name": "focus_note", "type": "str"}'
        )
    # Defence in depth: the served path normalizes and type-checks these before
    # they arrive, but `build_branch` is reachable from the browser flow too, and
    # a non-string here used to reach `.strip()` and raise AttributeError.
    raw_name = raw.get("name") or raw.get("field_name") or ""
    if not isinstance(raw_name, str):
        return (
            f"state field 'name' must be a string (got {_json_type_name(raw_name)})"
        )
    raw_type = raw.get("type", raw.get("field_type", "str"))
    if raw_type is not None and not isinstance(raw_type, str):
        return (
            f"state field 'type' must be a string (got {_json_type_name(raw_type)})"
        )
    fname = raw_name.strip()
    if not fname:
        return "state field spec missing 'name'"
    if any(f.get("name") == fname for f in branch.state_schema):
        return f"state field '{fname}' already exists on the branch"
    ftype_raw = (raw.get("type") or raw.get("field_type") or "str").strip()
    ftype = _closest_state_type(ftype_raw)
    entry: dict[str, Any] = {
        "name": fname,
        "type": ftype,
        "description": raw.get("description", ""),
    }
    if raw.get("reducer"):
        entry["reducer"] = raw["reducer"]
    # BUG-094: ``default_value`` is the canonical StateFieldDecl key
    # (tinyassets/branches.py:224). Read it first, fall back to the legacy
    # ``default`` / ``field_default`` spec shapes. Write to ``default_value``
    # so PR #932's ``_state_schema_defaults`` finds the seed value at runtime;
    # also dual-write ``default`` for back-compat with any reader that still
    # uses the legacy storage key.
    default = raw.get(
        "default_value",
        raw.get("default", raw.get("field_default", "")),
    )
    if default != "":
        entry["default_value"] = default
        entry["default"] = default
    branch.state_schema.append(entry)
    if not _state_type_is_exact(ftype_raw):
        # A NOTICE, not an error: the field is already stored and the branch is
        # valid. Returning this as an error refused the whole build over a type
        # name that had been resolved correctly -- live 2026-09-30, turn
        # f3617ca3 round 3, on `"string"`. The author still needs telling, so
        # the caller separates the two channels (`_STATE_COERCION_NOTICE`).
        return (
            f"{_STATE_COERCION_NOTICE}state field '{_echo(fname)}' type "
            f"'{_echo(ftype_raw)}' unknown; coerced to '{ftype}'."
        )
    return ""


def _coerce_model_hint_update(raw: Any, field: str) -> tuple[str, str]:
    if raw is None:
        return "", ""
    if not isinstance(raw, str):
        return "", f"{field} must be a string."
    return raw, ""


def _coerce_llm_policy_update(
    raw: Any, field: str,
) -> tuple[dict[str, Any] | None, str]:
    if raw is None:
        return None, ""
    if isinstance(raw, dict):
        policy = raw
    elif isinstance(raw, str):
        value = raw.strip()
        if not value or value == "null":
            return None, ""
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            return None, f"{field} is not valid JSON: {exc}"
        if decoded is None:
            return None, ""
        if isinstance(decoded, dict):
            policy = decoded
        else:
            return None, f"{field} must be a JSON object or null."
    else:
        return None, f"{field} must be a JSON object or null."

    from tinyassets.branches import _validate_llm_policy_shape

    errors = _validate_llm_policy_shape(policy, context=field)
    if errors:
        return None, "; ".join(errors)
    return policy, ""


_NODE_UPDATE_PATCH_META_FIELDS = frozenset({"op", "node_id"})
_VALID_REASONING_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh"})
_NODE_UPDATE_FIELDS = frozenset({
    "display_name",
    "description",
    "phase",
    "prompt_template",
    "source_code",
    "model_hint",
    "reasoning_effort",
    "llm_policy",
    "input_keys",
    "output_keys",
    "tools_allowed",
    "timeout_seconds",
    "retry_policy",
    "enabled",
    "invoke_branch_spec",
    "invoke_branch_version_spec",
    "await_run_spec",
    # A node's effects and its workspace binding are the two things that make
    # it DO anything beyond a prompt. Neither was editable, so a universe that
    # wanted to add a workspace to an existing node had to rebuild the whole
    # branch - and a spec that named one was dropped on the floor.
    "effects",
    "workspace",
})
_NODE_UPDATE_SPEC_FIELDS = (
    "invoke_branch_spec",
    "invoke_branch_version_spec",
    "await_run_spec",
)


def _coerce_node_update_bool(raw: Any, field: str) -> tuple[bool | None, str]:
    if isinstance(raw, bool):
        return raw, ""
    value = str(raw).strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return True, ""
    if value in {"false", "0", "no", "off"}:
        return False, ""
    return None, f"{field} must be a boolean."


def _coerce_timeout_seconds_update(raw: Any, field: str) -> tuple[float, str]:
    """Coerce a node timeout to a FINITE, POSITIVE number of seconds.

    A bare ``float()`` admitted four values no runtime can honour, and this is
    the only coercer between an edit and the stored column:

    * ``0`` and negatives — the compiler reads ``float(node.timeout_seconds or
      300.0)``, so a stored ``0`` silently becomes the 300s default (an edit
      that reports success and changes nothing) and a negative is a deadline
      that expired before the node started.
    * ``nan`` / ``inf`` — unusable deadlines admitted by the old coercer.
      Workspace read-side validation rejects them, but ran only after an
      edit had persisted; non-workspace nodes lacked that validation.
    * an integer too large to be a float — ``float()`` raises ``OverflowError``,
      which is not in the caught tuple, so it escaped the updater as an
      exception instead of a refusal.

    A bool is a type confusion rather than a one-second timeout, so it is
    refused by name too. Numeric STRINGS stay accepted: the kwargs update
    surface cannot send anything else.
    """
    if isinstance(raw, bool):
        return 0.0, f"{field} must be a number."
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return 0.0, f"{field} must be a number."
    if not math.isfinite(value):
        return 0.0, f"{field} must be a finite number of seconds."
    if value <= 0:
        return 0.0, f"{field} must be greater than 0 seconds, got {value}."
    return value, ""


def _coerce_retry_policy_update(raw: Any, field: str) -> tuple[dict[str, Any], str]:
    if isinstance(raw, dict):
        return dict(raw), ""
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return {}, f"{field} must be a JSON object."
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            return {}, f"{field} is not valid JSON: {exc}"
        if isinstance(decoded, dict):
            return dict(decoded), ""
    return {}, f"{field} must be a JSON object."


def _coerce_node_spec_update(
    raw: Any, field: str,
) -> tuple[dict[str, Any] | None, str]:
    if raw in (None, ""):
        return None, ""
    if isinstance(raw, dict):
        return raw, ""
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, f"{field} is not valid JSON: {exc}"
        if isinstance(decoded, dict):
            return decoded, ""
    return None, f"{field} must be a JSON object or null."


def _apply_node_updates(
    node: Any,
    updates: dict[str, Any],
    *,
    ignored_fields: frozenset[str] = frozenset(),
) -> str:
    """Apply validated node update fields shared by both update surfaces."""
    from tinyassets.api.extensions import VALID_PHASES
    from tinyassets.phase_vocab import normalize_phase

    editable_updates = {
        key: value
        for key, value in updates.items()
        if key not in ignored_fields
    }
    unknown = sorted(set(editable_updates) - _NODE_UPDATE_FIELDS)
    if unknown:
        return (
            "update_node unsupported field(s): "
            f"{', '.join(unknown)}. Supported: "
            f"{', '.join(sorted(_NODE_UPDATE_FIELDS))}"
        )
    if not editable_updates:
        return "update_node requires at least one field to update"

    incoming_template = editable_updates.get("prompt_template", "")
    incoming_source = editable_updates.get("source_code", "")
    if incoming_template and incoming_source:
        return "Pass prompt_template OR source_code, not both."

    if "display_name" in editable_updates:
        node.display_name = editable_updates["display_name"]
    if "description" in editable_updates:
        node.description = editable_updates["description"]
    if "phase" in editable_updates:
        new_phase = editable_updates["phase"] or "custom"
        if new_phase not in VALID_PHASES:
            return (
                f"Invalid phase '{new_phase}'. Must be one of: "
                f"{', '.join(sorted(VALID_PHASES))}"
            )
        node.phase = normalize_phase(new_phase)
    if "prompt_template" in editable_updates:
        node.prompt_template = editable_updates["prompt_template"]
        if node.prompt_template:
            node.source_code = ""
            _clear_source_code_approval(node)
    if "source_code" in editable_updates:
        next_source = editable_updates["source_code"]
        if next_source != node.source_code:
            _clear_source_code_approval(node)
        node.source_code = next_source
        if node.source_code:
            node.prompt_template = ""
    if "model_hint" in editable_updates:
        model_hint, err = _coerce_model_hint_update(
            editable_updates["model_hint"], "model_hint",
        )
        if err:
            return err
        node.model_hint = model_hint
    if "reasoning_effort" in editable_updates:
        effort = str(editable_updates["reasoning_effort"] or "").strip().lower()
        if effort and effort not in _VALID_REASONING_EFFORTS:
            return (
                f"Invalid reasoning_effort '{effort}'. Must be empty (provider "
                f"default) or one of: {', '.join(sorted(_VALID_REASONING_EFFORTS))}"
            )
        node.reasoning_effort = effort
    if "llm_policy" in editable_updates:
        llm_policy, err = _coerce_llm_policy_update(
            editable_updates["llm_policy"],
            f"node '{node.node_id}' llm_policy",
        )
        if err:
            return err
        node.llm_policy = llm_policy
    if "input_keys" in editable_updates:
        keys, err = _coerce_node_keys(
            editable_updates["input_keys"], "input_keys",
        )
        if err:
            return err
        node.input_keys = keys
    if "output_keys" in editable_updates:
        keys, err = _coerce_node_keys(
            editable_updates["output_keys"], "output_keys",
        )
        if err:
            return err
        node.output_keys = keys
    if "tools_allowed" in editable_updates:
        tools_allowed, err = _coerce_node_keys(
            editable_updates["tools_allowed"], "tools_allowed",
        )
        if err:
            return err
        node.tools_allowed = tools_allowed
    if "effects" in editable_updates:
        effects, err = _coerce_node_keys(
            editable_updates["effects"], "effects",
        )
        if err:
            return err
        node.effects = effects
    if "workspace" in editable_updates:
        # The ancestor checkout node whose workspace this node binds. Cleared
        # with "" - the compiler refuses a name that is not an ancestor in the
        # run, so an id that does not exist yet fails there rather than here,
        # where the rest of the branch may not be built.
        raw_workspace = editable_updates["workspace"]
        if raw_workspace is None:
            raw_workspace = ""
        if not isinstance(raw_workspace, str):
            return (
                f"node '{node.node_id}' workspace must be the node id of an "
                "ancestor checkout, as a string"
            )
        node.workspace = raw_workspace.strip()
    if "timeout_seconds" in editable_updates:
        timeout_seconds, err = _coerce_timeout_seconds_update(
            editable_updates["timeout_seconds"],
            f"node '{node.node_id}' timeout_seconds",
        )
        if err:
            return err
        node.timeout_seconds = timeout_seconds
    if "retry_policy" in editable_updates:
        retry_policy, err = _coerce_retry_policy_update(
            editable_updates["retry_policy"],
            f"node '{node.node_id}' retry_policy",
        )
        if err:
            return err
        node.retry_policy = retry_policy
    if "enabled" in editable_updates:
        enabled, err = _coerce_node_update_bool(
            editable_updates["enabled"], "enabled",
        )
        if err:
            return err
        node.enabled = bool(enabled)
    for spec_field in _NODE_UPDATE_SPEC_FIELDS:
        if spec_field in editable_updates:
            val, err = _coerce_node_spec_update(
                editable_updates[spec_field], spec_field,
            )
            if err:
                return err
            setattr(node, spec_field, val)
    # workspace and timeout_seconds are valid APART and invalid TOGETHER: a
    # workspace-bound node's timeout must be within 0 < t <= 1800. Each field
    # is coerced in isolation above, so the pair can only be judged once both
    # assignments have landed -- and it is judged on the STAGED copy, before
    # any caller persists. Without this the pair was checked only in
    # ``NodeDefinition.__post_init__``, i.e. on the way back IN: the row saved
    # first and the very next read of the branch raised, leaving an edited
    # branch that could not be loaded at all. Returned as an error string, not
    # raised, so the caller refuses the whole batch the same way it refuses any
    # other bad field.
    try:
        node._validate_workspace_timeout()
    except ValueError as exc:
        return str(exc)
    return ""


def _branch_file_contract_errors(branch: Any) -> list[str]:
    """Validate the final authored model, never grant custody or execution."""
    from tinyassets.authoring.io import parse_manifest
    from tinyassets.authoring.models import AuthoringValidationError

    if branch.io_manifest is None:
        return []
    try:
        manifest = parse_manifest(branch.to_dict(), strict=True,
                                  max_file_bytes=2**63 - 1, max_files=32)
    except (AuthoringValidationError, ValueError) as exc:
        return [str(exc)]
    fields = {item.get("name"): item.get("type") for item in branch.state_schema}
    return [
        f"io_manifest.inputs.{item.name} requires {kind} state"
        for item in manifest.inputs if item.is_file
        for kind in ["list" if item.io_type == "file_bundle" else "dict"]
        if fields.get(item.name) != kind
    ]


def _staged_branch_from_spec(
    spec: dict[str, Any],
    *,
    fork_version: dict[str, Any] | None = None,
) -> tuple[Any, list[str], list[str]]:
    """Stage a BranchDefinition from a spec.

    Returns ``(branch, errors, notices)``. ``errors`` refuse the build;
    ``notices`` are adjustments the author should know about but which do NOT
    refuse it -- a type name resolved by guess, for instance. Splitting them is
    the fix for a build refused because a value had been accepted (live
    2026-09-30, turn ``f3617ca3a91d4acab30eea8dbbeb2663`` round 3).
    """
    from tinyassets.branches import (
        BranchDefinition,
        normalize_branch_io_manifest,
        normalize_branch_skill_snapshots,
    )

    errors: list[str] = []
    notices: list[str] = []
    # Private unless the spec says "public" (founder 2026-09-26). An omitted
    # visibility is not a request to publish.
    raw_visibility = spec.get("visibility", "private")
    if not isinstance(raw_visibility, str) or raw_visibility.strip().lower() not in {
        "public",
        "private",
    }:
        errors.append("visibility must be 'public' or 'private'")
        visibility = "private"
    else:
        visibility = raw_visibility.strip().lower()
    branch = BranchDefinition(
        name=(spec.get("name") or "").strip(),
        description=spec.get("description") or "",
        domain_id=(spec.get("domain_id") or "").strip() or "workflow",
        goal_id=(spec.get("goal_id") or "").strip(),
        author=ledger_actor(),
        visibility=visibility,
        tags=list(spec.get("tags") or []),
        skills=[],
        fork_from=spec.get("fork_from") or None,
    )

    try:
        branch.skills = normalize_branch_skill_snapshots(spec.get("skills") or [])
    except ValueError as exc:
        errors.append(str(exc))

    # PR-037: accept the nested `graph` shape that `get_branch` RETURNS.
    # Without this, a user trying to fork by mirroring a live branch's
    # response shape (`{"graph": {"edges": [...], "conditional_edges":
    # [...], "entry_point": "..."}}`) has their edges silently dropped
    # during staging. The validator then reports "node not reachable
    # from entry point" — diagnostics that contradict what the submitted
    # spec literally contains. This mirrors what
    # `BranchDefinition.from_dict` already does for the DB-row path.
    graph_blob = spec.get("graph") if isinstance(spec.get("graph"), dict) else None
    manifest_present = "io_manifest" in spec or (
        graph_blob is not None and "io_manifest" in graph_blob
    )
    manifest_raw = spec["io_manifest"] if "io_manifest" in spec else (
        graph_blob.get("io_manifest") if graph_blob is not None else None
    )
    try:
        branch.io_manifest = normalize_branch_io_manifest(manifest_raw)
    except ValueError as exc:
        errors.append(str(exc))

    def _spec_get(key: str, default=None):
        """Top-level key wins; otherwise fall back to graph_blob[key]."""
        top = spec.get(key)
        if top is not None:
            return top
        if graph_blob is not None and graph_blob.get(key) is not None:
            return graph_blob.get(key)
        return default

    def _spec_has_graph_key(key: str) -> bool:
        return key in spec or (
            graph_blob is not None and graph_blob.get(key) is not None
        )

    # Branch-level execution choices. `_spec_get` above falls through on an
    # explicit null, which is right for topology (a null `edges` should not
    # shadow the nested `graph.edges`) and wrong here: an author clearing a
    # choice would silently re-inherit the nested value or the fork parent's.
    # So these two keys resolve PRESENCE, not truthiness — the top-level key
    # wins even when null, then the nested `graph` key wins even when null,
    # and only a wholly absent key falls through to fork inheritance.
    # `_spec_get`'s semantics are deliberately left unchanged for every other
    # key.
    def _choice_present(key: str) -> bool:
        return key in spec or (graph_blob is not None and key in graph_blob)

    def _choice_value(key: str) -> Any:
        if key in spec:
            return spec.get(key)
        if graph_blob is not None:
            return graph_blob.get(key)
        return None

    if _choice_present("default_llm_policy"):
        branch.default_llm_policy = _choice_value("default_llm_policy")
    if _choice_present("concurrency_budget"):
        branch.concurrency_budget = _choice_value("concurrency_budget")

    if branch.fork_from:
        if fork_version is not None:
            parent = BranchDefinition.from_dict(fork_version["snapshot"])
            parent_copy = BranchDefinition.from_dict(parent.to_dict())
            parent_skills = parent_copy.skills
            # The live-source fallback is for the author's OWN older snapshots.
            # Another author's live branch is not what they published: skills
            # added after the snapshot (and maybe made private since) must
            # never reach a copier (gpt-6-astra, command-center-packages D5).
            if (not parent_skills and parent.branch_def_id
                    and parent.author == _request_branch_actor()):
                from tinyassets.daemon_server import get_branch_definition

                try:
                    parent_def = get_branch_definition(
                        _base_path(), branch_def_id=parent.branch_def_id,
                    )
                except KeyError:
                    parent_def = {}
                if parent_def:
                    parent_skills = BranchDefinition.from_dict(parent_def).skills
            branch.parent_def_id = parent.branch_def_id
            if "skills" not in spec:
                branch.skills = parent_skills
            if "node_defs" not in spec and "nodes" not in spec:
                # SECURITY (Codex final residual, PR #1349): the parent's
                # node_defs are inherited wholesale. A carried node whose
                # source no longer matches its recorded approval hash — or
                # which carries approved=True with an empty/legacy hash — must
                # not survive the fork as still-approved. Re-validate each
                # carried node against its source hash; the fail-closed runtime
                # gate is the backstop, this keeps the persisted snapshot
                # honest at authoring time.
                #
                # SECURITY (Codex ADAPT 2026-08-22 #2): the approval hash is
                # SELF-COMPUTABLE, so a matching hash proves only that the source
                # is unchanged — NOT that a party the FORKER trusts approved it. A
                # malicious commons author can publish source_code with a forged
                # approved=True + matching hash; a naive fork would carry it and
                # a naive fork would carry that forged provenance. On a CROSS-AUTHOR
                # fork, strip ALL executable approval so inherited code lands
                # un-approved: the forker sees whose code it is, and it runs in the
                # OS sandbox like any code node (approval never gates a run).
                # Attribution (node ``author``) is preserved; only the approval
                # provenance is dropped.
                _forker = _request_branch_actor()
                _cross_author = bool(parent.author) and parent.author != _forker
                _carried = []
                for _n in parent_copy.node_defs:
                    if _cross_author:
                        _clear_source_code_approval(_n)
                        _carried.append(_n)
                    else:
                        _carried.append(_reconcile_node_approval(_n))
                branch.node_defs = _carried
                branch.graph_nodes = parent_copy.graph_nodes
            if not _spec_has_graph_key("edges"):
                branch.edges = parent_copy.edges
            if not _spec_has_graph_key("conditional_edges"):
                branch.conditional_edges = parent_copy.conditional_edges
            if not _spec_has_graph_key("entry_point"):
                branch.entry_point = parent_copy.entry_point
            if "state_schema" not in spec:
                branch.state_schema = list(parent_copy.state_schema)
            if not manifest_present:
                branch.io_manifest = parent_copy.io_manifest
            # Execution choices travel with the fork like state_schema and
            # io_manifest do. An explicit null in the fork's own spec is a
            # clear, not an absence, so it must not re-inherit here.
            if not _choice_present("default_llm_policy"):
                branch.default_llm_policy = parent_copy.default_llm_policy
            if not _choice_present("concurrency_budget"):
                branch.concurrency_budget = parent_copy.concurrency_budget

    # Each container is checked to BE a list before it is iterated. Iterating a
    # dict yields its keys, so a mapping where a list belonged used to reach the
    # per-entry applier as a bare `str` and raise AttributeError inside it --
    # which the served handler could only report as its class name (live
    # 2026-09-30, turn f3617ca3 round 3). `label` is the key the caller actually
    # typed, so the error points at their text and not at an internal name.
    def _entries(value: Any, label: str) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        errors.append(
            f"{label} must be a JSON array (got {_json_type_name(value)})"
        )
        return []

    node_container = "node_defs" if spec.get("node_defs") is not None else "nodes"
    for idx, raw in enumerate(
        _entries(spec.get("node_defs") if spec.get("node_defs") is not None
                 else spec.get("nodes"), node_container)
    ):
        err = _apply_node_spec(branch, raw)
        if err:
            errors.append(f"node[{idx}]: {err}")

    for idx, raw in enumerate(_entries(_spec_get("edges"), "edges")):
        err = _apply_edge_spec(branch, raw)
        if err:
            errors.append(f"edge[{idx}]: {err}")

    for idx, raw in enumerate(
        _entries(_spec_get("conditional_edges"), "conditional_edges")
    ):
        err = _apply_conditional_edge_spec(branch, raw)
        if err:
            errors.append(f"conditional_edge[{idx}]: {err}")

    state_entries, state_error = _normalized_state_schema(spec.get("state_schema"))
    if state_error:
        errors.append(state_error)
    for idx, raw in enumerate(state_entries):
        err = _apply_state_field_spec(branch, raw)
        if not err:
            continue
        if err.startswith(_STATE_COERCION_NOTICE):
            # Advisory: say it, do not fail on it.
            notices.append(
                f"state_schema[{idx}]: {err[len(_STATE_COERCION_NOTICE):]}"
            )
        else:
            errors.append(f"state_schema[{idx}]: {err}")

    entry = (spec.get("entry_point") or "").strip()
    if not entry and graph_blob is not None:
        entry = (graph_blob.get("entry_point") or "").strip()
    order = [gn.id for gn in branch.graph_nodes]
    if (
        len(order) > 1
        and not branch.edges
        and not branch.conditional_edges
        and entry in ("", order[0])
    ):
        # A LIST of nodes with no wiring at all reads one way: in the order
        # written. Refusing it ("node 'b' is not reachable from entry point
        # 'a'") answered a question the spec had already answered, and cost a
        # small model a round to restate it as edges (live 2026-09-30, free
        # account). Only an ABSENCE is filled: any edge, conditional edge, or an
        # entry point other than the first node means the author is wiring the
        # graph themselves, and that is validated exactly as written.
        for src, dst in zip(order, order[1:]):
            _apply_edge_spec(branch, {"from": src, "to": dst})
        notices.append(
            "no edges were given, so the nodes run in the order listed: "
            + " -> ".join(order)
            + ". Pass edges to wire them any other way."
        )
    if entry:
        branch.entry_point = entry
    elif not branch.entry_point and branch.graph_nodes:
        # DEFAULT, not a guess: the graph's head is derivable from the edges the
        # caller already gave (the node nothing points at), and for a single node
        # there is only one answer. Live 2026-09-30 round 18: a one-node spec was
        # refused with "Entry point is required when branch has nodes" — a round
        # spent restating a fact the spec fully determined.
        #
        # Only fills an ABSENCE. An explicit entry_point is honoured above even
        # when it names no node, so a typo is still an error rather than being
        # silently replaced by a working one.
        branch.entry_point = _suggest_entry_point(branch)

    return branch, errors, notices


def _build_branch_text(branch: Any, *, truncated: bool) -> str:
    node_count = len(branch.node_defs)
    edge_count = len(branch.edges)
    head = (
        f"**Built branch '{branch.name or 'unnamed'}'**: "
        f"{node_count} nodes, {edge_count} edges, "
        f"{len(getattr(branch, 'skills', []) or [])} skills, "
        f"entry=`{branch.entry_point}`."
    )
    if truncated:
        return "\n".join([
            head,
            "",
            "_(Branch exceeds 12-node phone-legibility limit; "
            "full topology in structuredContent. Mermaid summary:)_",
            "",
            "```mermaid",
            "flowchart LR",
            f'    START(["START"]) --> entry["{_mermaid_label(branch.entry_point)}"]',
            f"    entry --> more[\"... {node_count - 1} more nodes\"]",
            '    more --> END(["END"])',
            "```",
            *_execution_choice_lines(branch),
        ])
    mermaid = _branch_mermaid(branch)
    state_lines = [f"State schema: {len(branch.state_schema)} field(s)."]
    state_lines += _execution_choice_lines(branch)
    return "\n".join([head, "", mermaid, "", *state_lines])


def _execution_choice_lines(branch: Any) -> list[str]:
    """One prose line per branch-wide execution choice that is set.

    Silent when neither is set, so an unset branch reads exactly as before;
    present when either is, so an author can DISCOVER controls that are
    otherwise invisible on the authoring surface.
    """
    lines: list[str] = []
    policy = getattr(branch, "default_llm_policy", None)
    if policy is not None:
        preferred = ""
        if isinstance(policy, dict) and isinstance(policy.get("preferred"), dict):
            preferred = str(policy["preferred"].get("provider") or "")
        lines.append(
            "Default model policy: set"
            + (f" (preferred provider `{preferred}`)." if preferred else ".")
            + " Nodes without their own llm_policy use it."
        )
    budget = getattr(branch, "concurrency_budget", None)
    if budget is not None:
        lines.append(
            f"Concurrency budget: {budget} node(s) at a time per run "
            "(unset means unbounded)."
        )
    return lines


def _ext_branch_build(kwargs: dict[str, Any]) -> str:
    from tinyassets.daemon_server import (
        create_branch_definition_once,
        save_branch_definition,
    )

    if _request_branch_actor() is None:
        return json.dumps({"error": "Authenticated branch subject required."})
    verbose = str(kwargs.get("verbose") or "").strip().lower() in ("true", "1", "yes")
    raw = (kwargs.get("spec_json") or "").strip()
    if not raw:
        return json.dumps({
            "status": "rejected",
            "error": "spec_json is required for build_branch.",
            "suggestions": [{
                "issue": "Empty spec.",
                "proposed_fix": (
                    "Pass a JSON object with at minimum `name` and a "
                    "non-empty `node_defs` list. See branch_design_guide."
                ),
            }],
        })
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as exc:
        return json.dumps({
            "status": "rejected",
            "error": f"spec_json is not valid JSON: {exc}",
            "suggestions": [{
                "issue": "spec_json did not parse.",
                "proposed_fix": "Validate JSON shape before sending.",
            }],
        })
    if not isinstance(spec, dict):
        return json.dumps({
            "status": "rejected",
            "error": "spec_json must decode to a JSON object.",
            "suggestions": [{
                "issue": "Top-level spec is not an object.",
                "proposed_fix": "Wrap the spec in { ... }.",
            }],
        })

    fork_version: dict[str, Any] | None = None
    fork_selector = (spec.get("fork_from") or "").strip()
    if fork_selector:
        resolved_fork = _resolve_readable_version(
            fork_selector,
            str(_base_path()),
        )
        if resolved_fork is None:
            return _branch_version_not_found(fork_selector)
        _, fork_version = resolved_fork

    top_level_goal_id = (kwargs.get("goal_id") or "").strip()
    if top_level_goal_id:
        spec = {**spec, "goal_id": top_level_goal_id}

    branch, staging_errors, staging_notices = _staged_branch_from_spec(
        spec,
        fork_version=fork_version,
    )
    request_id = str(kwargs.get("request_id") or "").strip()
    if request_id and not 16 <= len(request_id) <= 128:
        staging_errors.append("request_id must contain 16 to 128 characters")
    if request_id:
        actor = _request_branch_actor()
        assert actor is not None
        branch.branch_def_id = hashlib.sha256(
            f"branch-create-v1\0{actor}\0{request_id}".encode("utf-8")
        ).hexdigest()[:12]
    validation_errors = branch.validate() + _branch_file_contract_errors(branch)
    errors = staging_errors + _without_cascades(spec, staging_errors, validation_errors)

    # Validate fork_from points to a real branch_version_id. This error
    # string is what the rejection path below joins verbatim into `text`,
    # so it must not echo the submitted `fork_from` back (task #58's
    # text-channel rule covers ids the caller supplied, not just stored
    # ones -- `text` is a phone/chat surface, not a debug log).
    if branch.fork_from:
        from tinyassets.branch_versions import get_branch_version
        if get_branch_version(_base_path(), branch.fork_from) is None:
            errors.append(
                "fork_from is not a known branch_version_id. "
                "Pass a published branch_version_id, not a branch_def_id."
            )

    if errors:
        suggestions = _errors_to_suggestions(branch, errors)
        text_lines = [
            f"**Build failed.** {len(errors)} problem(s) in spec:",
            "",
            *[f"- {err}" for err in errors],
        ]
        if suggestions:
            text_lines += [
                "",
                "Suggested fixes:",
                *[f"- {s['proposed_fix']}" for s in suggestions],
            ]
        if staging_notices:
            text_lines += ["", "Also adjusted:", *[f"- {n}" for n in staging_notices]]
        return json.dumps({
            "text": "\n".join(text_lines),
            "status": "rejected",
            "errors": errors,
            "notices": staging_notices,
            "suggestions": suggestions,
            "attempted_spec": spec,
        })

    from tinyassets.branches import BranchDefinition as _BD

    idempotent_replay = False
    existing = None
    if request_id:
        saved, created = create_branch_definition_once(
            _base_path(),
            branch_def=branch.to_dict(),
        )
        if not created:
            existing = saved
    if existing is not None:
        existing = _BD.from_dict(existing).to_dict()
        immutable_fields = (
            "branch_def_id",
            "name",
            "description",
            "author",
            "domain_id",
            "goal_id",
            "tags",
            "skills",
            "parent_def_id",
            "fork_from",
            "entry_point",
            "visibility",
            "graph_nodes",
            "edges",
            "conditional_edges",
            "node_defs",
            "state_schema",
            "io_manifest",
            "default_llm_policy",
            "concurrency_budget",
        )
        candidate = branch.to_dict()

        def comparable(value: dict[str, Any], field: str) -> Any:
            if field in {"goal_id", "parent_def_id", "fork_from"}:
                return value.get(field) or ""
            return value.get(field)

        conflicting_fields = [
            field
            for field in immutable_fields
            if comparable(existing, field) != comparable(candidate, field)
        ]
        if conflicting_fields:
            return json.dumps(
                {
                    "error": "branch_idempotency_conflict",
                    "request_id": request_id,
                    "conflicting_fields": conflicting_fields,
                }
            )
        saved = existing
        idempotent_replay = True
    elif not request_id:
        saved = save_branch_definition(_base_path(), branch_def=branch.to_dict())
    persisted = _BD.from_dict(saved)
    truncated = len(persisted.node_defs) > 12
    text = _build_branch_text(persisted, truncated=truncated)
    payload: dict[str, Any] = {
        "text": text,
        "status": "built",
        "branch_def_id": persisted.branch_def_id,
        "name": persisted.name,
        "node_count": len(persisted.node_defs),
        "edge_count": len(persisted.edges),
        "skill_count": len(persisted.skills),
        "entry_point": persisted.entry_point,
        "validation_summary": "ok",
        # What was adjusted on the way in. The build SUCCEEDED, so this is not a
        # rejection -- but an author who wrote a type name we had to guess at
        # should be told which one, and what it became.
        "notices": staging_notices,
        "batch_receipt": _branch_authoring_batch_receipt(
            persisted,
            action="build_branch",
            operation_count=1,
            request_id=kwargs.get("request_id", ""),
        ),
    }
    payload["batch_receipt"]["idempotent_replay"] = idempotent_replay
    if staging_notices:
        payload["text"] = "\n".join([
            text, "", "Adjusted on the way in:",
            *[f"- {n}" for n in staging_notices],
        ])
    if verbose:
        payload["branch"] = saved
    return json.dumps(payload, default=str)


def _apply_patch_op(branch: Any, op: dict[str, Any]) -> str:
    name = (op.get("op") or "").strip().lower()
    if name == "add_node":
        return _apply_node_spec(branch, op)
    if name == "add_edge":
        return _apply_edge_spec(branch, op)
    if name == "add_state_field":
        return _apply_state_field_spec(branch, op)
    if name == "set_entry_point":
        nid = (op.get("node_id") or "").strip()
        if not nid:
            return "set_entry_point requires node_id"
        branch.entry_point = nid
        return ""
    if name == "set_goal":
        gid = (op.get("goal_id") or "").strip()
        if not gid:
            return "set_goal requires goal_id"
        branch.goal_id = gid
        return ""
    if name == "unset_goal":
        branch.goal_id = ""
        return ""
    if name == "remove_node":
        nid = (op.get("node_id") or "").strip()
        if not nid:
            return "remove_node requires node_id"
        before_n = len(branch.node_defs)
        branch.node_defs = [n for n in branch.node_defs if n.node_id != nid]
        branch.graph_nodes = [g for g in branch.graph_nodes if g.id != nid]
        branch.edges = [
            e for e in branch.edges
            if e.from_node != nid and e.to_node != nid
        ]
        if branch.entry_point == nid:
            branch.entry_point = ""
        if len(branch.node_defs) == before_n:
            return f"remove_node: node '{nid}' not found"
        return ""
    if name == "remove_edge":
        src = (op.get("from") or op.get("from_node") or "").strip()
        dst = (op.get("to") or op.get("to_node") or "").strip()
        if not src or not dst:
            return "remove_edge requires from and to"
        before = len(branch.edges)
        branch.edges = [
            e for e in branch.edges
            if not (e.from_node == src and e.to_node == dst)
        ]
        if len(branch.edges) == before:
            return f"remove_edge: {src}->{dst} not found"
        return ""
    if name == "add_conditional_edge":
        return _apply_conditional_edge_spec(branch, op)
    if name == "remove_conditional_edge":
        src = (op.get("from") or op.get("from_node") or "").strip()
        if not src:
            return "remove_conditional_edge requires 'from'"
        outcome = (op.get("outcome") or "").strip()
        for i, ce in enumerate(branch.conditional_edges):
            if ce.from_node != src:
                continue
            if not outcome:
                del branch.conditional_edges[i]
                return ""
            if outcome not in ce.conditions:
                return (
                    f"remove_conditional_edge: outcome '{outcome}' not "
                    f"found on edge from '{src}'"
                )
            del ce.conditions[outcome]
            if not ce.conditions:
                del branch.conditional_edges[i]
            return ""
        return f"remove_conditional_edge: no conditional edge from '{src}'"
    if name == "remove_state_field":
        fname = (op.get("name") or op.get("field_name") or "").strip()
        if not fname:
            return "remove_state_field requires name"
        before = len(branch.state_schema)
        branch.state_schema = [
            f for f in branch.state_schema if f.get("name") != fname
        ]
        if len(branch.state_schema) == before:
            return f"remove_state_field: '{fname}' not found"
        return ""
    if name == "update_node":
        nid = (op.get("node_id") or "").strip()
        if not nid:
            return "update_node requires node_id"
        for n in branch.node_defs:
            if n.node_id == nid:
                return _apply_node_updates(
                    n,
                    op,
                    ignored_fields=_NODE_UPDATE_PATCH_META_FIELDS,
                )
        return f"update_node: node '{nid}' not found"
    if name == "add_skill":
        from tinyassets.branches import normalize_branch_skill_snapshot

        raw_skill = op.get("skill") if isinstance(op.get("skill"), dict) else op
        try:
            skill = normalize_branch_skill_snapshot(raw_skill)
        except ValueError as exc:
            return str(exc)
        if any(s.get("skill_id") == skill["skill_id"] for s in branch.skills):
            return f"skill '{skill['skill_id']}' already exists"
        branch.skills.append(skill)
        return ""
    if name == "update_skill":
        from tinyassets.branches import normalize_branch_skill_snapshot

        skill_id = (op.get("skill_id") or op.get("id") or "").strip()
        if not skill_id:
            return "update_skill requires skill_id"
        for idx, existing in enumerate(branch.skills):
            if existing.get("skill_id") != skill_id:
                continue
            merged = dict(existing)
            update_payload = (
                op.get("skill") if isinstance(op.get("skill"), dict) else op
            )
            for key, value in update_payload.items():
                if key != "op":
                    merged[key] = value
            merged["skill_id"] = skill_id
            try:
                branch.skills[idx] = normalize_branch_skill_snapshot(merged)
            except ValueError as exc:
                return str(exc)
            return ""
        return f"update_skill: skill '{skill_id}' not found"
    if name == "remove_skill":
        skill_id = (op.get("skill_id") or op.get("id") or "").strip()
        if not skill_id:
            return "remove_skill requires skill_id"
        before = len(branch.skills)
        branch.skills = [
            skill for skill in branch.skills
            if skill.get("skill_id") != skill_id
        ]
        if len(branch.skills) == before:
            return f"remove_skill: skill '{skill_id}' not found"
        return ""
    if name == "set_skills":
        from tinyassets.branches import normalize_branch_skill_snapshots

        if "skills" not in op:
            return "set_skills requires a skills list"
        try:
            branch.skills = normalize_branch_skill_snapshots(op.get("skills"))
        except ValueError as exc:
            return str(exc)
        return ""
    if name == "set_io_manifest":
        from tinyassets.branches import normalize_branch_io_manifest

        if "io_manifest" not in op:
            return "set_io_manifest requires an io_manifest field (null clears)"
        try:
            branch.io_manifest = normalize_branch_io_manifest(op["io_manifest"])
        except ValueError as exc:
            return str(exc)
        return ""
    # Branch-level metadata ops (#67). These let patch_branch rename /
    # retag / redescribe / publish a branch atomically, without the
    # previous delete-and-rebuild workaround that lost run history and
    # judgments.
    if name == "set_default_llm_policy":
        # Branch-wide model policy. Mirrors `set_io_manifest`: the field is
        # required (so a missing key is an explicit error, never a silent
        # clear) and an explicit `null` clears. Shape errors come from
        # `validate()` below so build, patch and compile share one check.
        if "default_llm_policy" not in op:
            return (
                "set_default_llm_policy requires a default_llm_policy field "
                "(null clears the branch default)"
            )
        policy = op["default_llm_policy"]
        if policy is not None and not isinstance(policy, dict):
            return (
                "set_default_llm_policy 'default_llm_policy' must be an "
                f"object or null, got {type(policy).__name__}"
            )
        branch.default_llm_policy = policy
        return ""
    if name == "set_concurrency_budget":
        if "concurrency_budget" not in op:
            return (
                "set_concurrency_budget requires a concurrency_budget field "
                "(null clears the branch budget)"
            )
        branch.concurrency_budget = op["concurrency_budget"]
        return ""
    if name == "set_name":
        new_name = (op.get("name") or "").strip()
        if not new_name:
            return "set_name requires a non-empty name"
        branch.name = new_name
        return ""
    if name == "set_description":
        if "description" not in op:
            return "set_description requires a description field"
        branch.description = op.get("description") or ""
        return ""
    if name == "set_tags":
        if "tags" not in op:
            return "set_tags requires a tags list"
        raw_tags = op.get("tags")
        if raw_tags is None:
            raw_tags = []
        if isinstance(raw_tags, str):
            # Accept CSV too for parity with other surfaces.
            raw_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        if not isinstance(raw_tags, list):
            return "set_tags 'tags' must be a list (or CSV string)"
        branch.tags = [str(t).strip() for t in raw_tags if str(t).strip()]
        return ""
    if name == "set_published":
        if "published" not in op:
            return "set_published requires a 'published' boolean"
        val = op.get("published")
        if not isinstance(val, bool):
            return "set_published 'published' must be true or false"
        branch.published = val
        return ""
    if name == "set_visibility":
        # Phase 6.2.2 — private hides Branch + its gate claims from
        # non-owner callers.
        if "visibility" not in op:
            return "set_visibility requires a 'visibility' string"
        raw = op.get("visibility")
        if not isinstance(raw, str):
            return "set_visibility 'visibility' must be 'public' or 'private'"
        normalized = raw.strip().lower()
        if normalized not in ("public", "private"):
            return (
                "set_visibility 'visibility' must be 'public' or 'private'"
            )
        branch.visibility = normalized
        return ""
    if name == "set_fork_from":
        bvid = (op.get("branch_version_id") or "").strip()
        if not bvid:
            return "set_fork_from requires branch_version_id"
        if branch.fork_from is not None:
            # This op-error string is what the rejected-patch path copies
            # verbatim into `text` (`_ext_branch_patch`, "Op errors:"
            # section below), so it must not carry the existing
            # `fork_from`'s branch_version_id (task #58's text-channel rule).
            return (
                "set_fork_from: fork_from is already set "
                "and is immutable after set."
            )
        if _resolve_readable_version(bvid, str(_base_path())) is None:
            return _branch_version_not_found_message(bvid)
        branch.fork_from = bvid
        return ""
    return f"unknown op '{name}'"


def _ext_branch_patch(kwargs: dict[str, Any]) -> str:
    import copy

    from tinyassets.branch_versions import publish_branch_version
    from tinyassets.branches import BranchDefinition
    from tinyassets.daemon_server import save_branch_definition

    verbose = str(kwargs.get("verbose") or "").strip().lower() in ("true", "1", "yes")
    selector = (kwargs.get("branch_def_id") or "").strip()
    if not selector:
        return json.dumps({
            "status": "rejected",
            "error": "branch_def_id is required.",
        })
    raw = (kwargs.get("changes_json") or "").strip()
    if not raw:
        return json.dumps({
            "status": "rejected",
            "error": "changes_json is required (ordered list of ops).",
        })

    try:
        changes = json.loads(raw)
    except json.JSONDecodeError as exc:
        return json.dumps({
            "status": "rejected",
            "error": f"changes_json is not valid JSON: {exc}",
        })
    if not isinstance(changes, list):
        return json.dumps({
            "status": "rejected",
            "error": "changes_json must decode to a JSON list.",
        })
    if not changes:
        # A patch with no ops used to return `status: "patched"` with
        # `ops_applied: 0` -- a success receipt for an untouched branch. Fail
        # loudly instead (Hard Rule 8); live 2026-09-03, a rename reported as
        # applied twice and never landed.
        return json.dumps({
            "status": "rejected",
            "error": (
                "changes_json is an empty list; a patch needs at least one op. "
                "To rename: [{\"op\": \"set_name\", \"name\": \"New name\"}]"
            ),
        })

    resolved = _resolve_readable_branch(selector, str(_base_path()))
    if resolved is None:
        return _branch_not_found(selector)
    bid, source = resolved
    if not _branch_authorized(source):
        return _branch_authority_denied()

    old_name = source.get("name", "")
    staging = BranchDefinition.from_dict(copy.deepcopy(source))

    per_op_errors: list[dict[str, Any]] = []
    per_op_notices: list[dict[str, Any]] = []
    for idx, op in enumerate(changes):
        if not isinstance(op, dict):
            per_op_errors.append({
                "op_index": idx, "op": op,
                "error": "op must be an object with an 'op' key",
            })
            continue
        err = _apply_patch_op(staging, op)
        # The SAME two channels as the build path. Codex refute, PR #4123: this
        # caller was missed when `_apply_state_field_spec` gained the notice
        # sentinel, so a patch that coerced a type both leaked the raw
        # `\x00notice\x00` marker into the author's text AND was rejected for
        # what the build path treats as advisory. One applicator, one contract:
        # every caller of it has to read the prefix.
        if err and err.startswith(_STATE_COERCION_NOTICE):
            per_op_notices.append({
                "op_index": idx, "op": op,
                "notice": err[len(_STATE_COERCION_NOTICE):],
            })
            continue
        if err:
            if (
                (op.get("op") or "").strip().lower() == "set_fork_from"
                and err == _branch_version_not_found_message(
                    (op.get("branch_version_id") or "").strip(),
                )
            ):
                return json.dumps({"error": err})
            per_op_errors.append({
                "op_index": idx, "op": op, "error": err,
            })

    validation_errors: list[str] = []
    if not per_op_errors:
        validation_errors = staging.validate() + _branch_file_contract_errors(staging)

    if per_op_errors or validation_errors:
        suggestions = _errors_to_suggestions(staging, validation_errors)
        text_lines = [
            f"**Patch rejected.** {len(per_op_errors)} op error(s), "
            f"{len(validation_errors)} validation error(s). No changes "
            "were applied.",
        ]
        if per_op_errors:
            text_lines += ["", "Op errors:"]
            for pe in per_op_errors:
                op_name = (
                    pe['op'].get('op', '?')
                    if isinstance(pe['op'], dict) else str(pe['op'])
                )
                text_lines.append(
                    f"- op[{pe['op_index']}] {op_name}: {pe['error']}"
                )
        if validation_errors:
            text_lines += ["", "Validation:"]
            for err in validation_errors:
                text_lines.append(f"- {err}")
        if suggestions:
            text_lines += ["", "Suggested fixes:"]
            for s in suggestions:
                text_lines.append(f"- {s['proposed_fix']}")
        if per_op_notices:
            text_lines += ["", "Also adjusted:"]
            for pn in per_op_notices:
                text_lines.append(f"- op[{pn['op_index']}]: {pn['notice']}")
        return json.dumps({
            "text": "\n".join(text_lines),
            "status": "rejected",
            "errors": per_op_errors,
            "notices": per_op_notices,
            "validation_errors": validation_errors,
            "suggestions": suggestions,
        })

    actor = _request_branch_actor()
    if actor is None:
        return _branch_authority_denied()
    try:
        parent_version = publish_branch_version(
            _base_path(),
            source,
            publisher=actor,
            notes="patch_branch pre-patch snapshot",
        )
    except (KeyError, ValueError) as exc:
        return json.dumps({
            "status": "rejected",
            "error": f"Could not snapshot pre-patch branch: {exc}",
        })

    saved = save_branch_definition(_base_path(), branch_def=staging.to_dict())
    persisted = BranchDefinition.from_dict(saved)
    try:
        branch_version = publish_branch_version(
            _base_path(),
            saved,
            publisher=actor,
            notes="patch_branch post-patch snapshot",
            parent_version_id=parent_version.branch_version_id,
        )
    except (KeyError, ValueError) as exc:
        return json.dumps({
            "status": "rejected",
            "error": f"Patch saved but post-patch version snapshot failed: {exc}",
            "branch_def_id": persisted.branch_def_id,
            "parent_version_id": parent_version.branch_version_id,
        })

    _SKIP_DIFF = {"updated_at", "created_at", "node_defs", "edges",
                  "conditional_edges", "graph_nodes", "state_schema", "stats"}
    patched_fields = [
        k for k in source
        if k not in _SKIP_DIFF and source.get(k) != saved.get(k)
    ]

    post_patch = {
        "branch_def_id": persisted.branch_def_id,
        "name": persisted.name,
        "entry_point": persisted.entry_point,
        "node_count": len(persisted.node_defs),
        "edge_count": len(persisted.edges),
        "skill_count": len(persisted.skills),
        "visibility": persisted.visibility,
    }

    truncated = len(persisted.node_defs) > 12
    # `text` is the phone/chat channel and must never carry a raw id (task
    # #58) -- `branch_version_id` is `<branch_def_id>@<hash>`, so it embeds
    # the id this function must not leak. `content_hash` is also the stable
    # discriminator that mints `branch_version_id`, so even the short form
    # is an id, not human content -- Codex ADAPT round on 256efe7b. `text`
    # gets a human description only; the full `branch_version_id` and
    # `content_hash` both stay in the structured fields below.
    text_lines = [
        f"**Patched branch '{persisted.name}'**: applied {len(changes)} op(s). "
        f"{len(persisted.node_defs)} nodes, {len(persisted.edges)} edges, "
        f"{len(persisted.skills)} skills, entry=`{persisted.entry_point}`.",
        "Version updated.",
    ]
    if patched_fields:
        text_lines += ["", f"Changed fields: {', '.join(patched_fields)}."]
    choice_lines = _execution_choice_lines(persisted)
    if choice_lines:
        text_lines += ["", *choice_lines]
    if truncated:
        text_lines += [
            "",
            "_(Branch exceeds 12 nodes; full topology in structuredContent.)_",
        ]
    else:
        text_lines += ["", _branch_mermaid(persisted)]
    name_updated = persisted.name != old_name
    patch_payload: dict[str, Any] = {
        "text": "\n".join(text_lines),
        "status": "patched",
        "branch_def_id": persisted.branch_def_id,
        "branch_version_id": branch_version.branch_version_id,
        "content_hash": branch_version.content_hash,
        "published_at": branch_version.published_at,
        "parent_version_id": branch_version.parent_version_id,
        "ops_applied": len(changes),
        "node_count": len(persisted.node_defs),
        "edge_count": len(persisted.edges),
        "skill_count": len(persisted.skills),
        "patched_fields": patched_fields,
        "name_updated": name_updated,
        "new_name": persisted.name,
        "post_patch": post_patch,
        # Same channel as the build path: what was adjusted, without rejecting.
        "notices": per_op_notices,
        "batch_receipt": _branch_authoring_batch_receipt(
            persisted,
            action="patch_branch",
            operation_count=len(changes),
            request_id=kwargs.get("request_id", ""),
        ),
    }
    if per_op_notices:
        patch_payload["text"] = "\n".join([
            patch_payload["text"], "", "Adjusted on the way in:",
            *[f"- op[{pn['op_index']}]: {pn['notice']}" for pn in per_op_notices],
        ])
    if verbose:
        patch_payload["branch"] = saved
    return json.dumps(patch_payload, default=str)


def _ext_branch_update_node(kwargs: dict[str, Any]) -> str:
    """Update a single node in-place, keeping ``node_id`` stable.

    Phase 4 lineage + judgments are keyed on node_id, so edits must
    preserve identity. Same update semantics as the patch op of the same
    name; this standalone action bumps BranchDefinition.version (+1)
    so downstream lineage can distinguish pre/post-edit runs.
    """
    from tinyassets.branches import BranchDefinition
    from tinyassets.daemon_server import save_branch_definition

    selector = (kwargs.get("branch_def_id") or "").strip()
    nid = (kwargs.get("node_id") or "").strip()
    if not selector or not nid:
        return json.dumps({
            "status": "rejected",
            "error": "branch_def_id and node_id are required.",
        })
    resolved = _resolve_readable_branch(selector, str(_base_path()))
    if resolved is None:
        return _branch_not_found(selector)
    bid, source = resolved
    if not _branch_authorized(source):
        return _branch_authority_denied()

    # Accept updates as a JSON blob (changes_json) OR as individual
    # kwargs. Individual kwargs are the phone-friendly shape;
    # changes_json is for scripts batching.
    changes_raw = (kwargs.get("changes_json") or "").strip()
    updates: dict[str, Any] = {}
    if changes_raw:
        try:
            parsed = json.loads(changes_raw)
        except json.JSONDecodeError as exc:
            return json.dumps({
                "status": "rejected",
                "error": f"changes_json is not valid JSON: {exc}",
            })
        if not isinstance(parsed, dict):
            return json.dumps({
                "status": "rejected",
                "error": "changes_json must decode to an object.",
            })
        updates = parsed
    else:
        # Pull supported fields from the top-level kwargs.
        for field in (
            "display_name", "description", "phase",
            "prompt_template", "source_code", "model_hint",
            "input_keys", "output_keys", "tools_allowed",
            "timeout_seconds", "retry_policy", "enabled",
        ):
            if field in kwargs and kwargs.get(field) is not None and kwargs.get(field) != "":
                updates[field] = kwargs[field]
        if "llm_policy" in kwargs and kwargs.get("llm_policy") is not None:
            updates["llm_policy"] = kwargs["llm_policy"]
        # BUG-045: same plumbing fix as _apply_node_spec for the
        # update_node write path. update_node has its own kwargs-merge
        # logic and writes through save_branch_definition without
        # routing through _apply_node_spec. Each spec-bearing field can
        # arrive as a JSON-encoded string (kwargs-only callers can't
        # send raw dicts) or as a dict (changes_json path).
        for field in (
            "invoke_branch_spec",
            "invoke_branch_version_spec",
            "await_run_spec",
        ):
            raw_val = kwargs.get(field)
            if raw_val is not None and raw_val != "":
                updates[field] = raw_val

    if not updates:
        return json.dumps({
            "status": "rejected",
            "error": (
                "No fields to update. Pass one or more of "
                "display_name / description / phase / prompt_template / "
                "source_code / model_hint / llm_policy / retry_policy / "
                "timeout_seconds / input_keys / output_keys / tools_allowed, or a "
                "changes_json object."
            ),
        })

    staging = BranchDefinition.from_dict(source)
    target_node = next(
        (n for n in staging.node_defs if n.node_id == nid), None,
    )
    if target_node is None:
        return json.dumps({
            "status": "rejected",
            "error": f"Node '{nid}' not found on branch '{bid}'.",
        })

    try:
        update_error = _apply_node_updates(target_node, updates)
    except Exception as exc:
        return json.dumps({
            "status": "rejected",
            "error": f"Failed to apply update: {exc}",
        })
    if update_error:
        return json.dumps({"status": "rejected", "error": update_error})

    # Snapshot the previous node body BEFORE we mutate further, so the
    # audit row captures rollback-capable state.
    before_branch = BranchDefinition.from_dict(source)
    before_node = next(
        (n for n in before_branch.node_defs if n.node_id == nid), None,
    )
    node_before_body = before_node.to_dict() if before_node else {}

    # Bump version so Phase 4 lineage can distinguish pre/post-edit runs.
    old_version = int(source.get("version") or 1)
    new_version = old_version + 1
    staging_dict = staging.to_dict()
    staging_dict["version"] = new_version
    saved = save_branch_definition(_base_path(), branch_def=staging_dict)

    # Re-hydrate to produce a clean NodeDefinition dict for the response.
    persisted = BranchDefinition.from_dict(saved)
    updated_node = next(
        (n for n in persisted.node_defs if n.node_id == nid), target_node,
    )

    # #50: emit a node_edit_audit row capturing full pre/post node
    # bodies so `rollback_node` can restore the exact prior state.
    # ``triggered_by_judgment_id`` is optional — callers applying a
    # judgment-driven edit can pass it.
    try:
        from tinyassets.runs import record_node_edit_audit

        triggered = (
            kwargs.get("triggered_by_judgment_id") or ""
        ).strip() or None
        record_node_edit_audit(
            _base_path(),
            branch_def_id=bid,
            version_before=old_version,
            version_after=new_version,
            nodes_changed=[nid],
            triggered_by_judgment_id=triggered,
            node_before=node_before_body,
            node_after=updated_node.to_dict() if updated_node else {},
            edit_kind="update",
        )
    except Exception:
        logger.exception("node_edit_audit failed for %s/%s", bid, nid)

    changed_fields = sorted(updates.keys())
    branch_label = persisted.name or "unnamed"
    text_lines = [
        f"**Updated node `{nid}`** on workflow '{branch_label}' "
        f"(version {old_version} → {new_version}). "
        f"Fields changed: {', '.join(changed_fields) or '(none)'}.",
    ]
    # Summarize the node briefly so Claude.ai sees the new shape.
    body_kind = "prompt_template" if updated_node.prompt_template else (
        "source_code" if updated_node.source_code else "passthrough"
    )
    text_lines += [
        "",
        f"- display_name: {updated_node.display_name}",
        f"- phase: {updated_node.phase}",
        f"- body: {body_kind}",
    ]
    if body_kind == "prompt_template":
        preview = updated_node.prompt_template
        if len(preview) > 240:
            preview = preview[:240].rstrip() + "…"
        text_lines += ["", f"Template preview:\n\n```\n{preview}\n```"]

    return json.dumps({
        "text": "\n".join(text_lines),
        "status": "updated",
        "branch_def_id": bid,
        "node_id": nid,
        "version_before": old_version,
        "version_after": new_version,
        "changed_fields": changed_fields,
        "node": updated_node.to_dict(),
    }, default=str)


def _ext_branch_search_nodes(kwargs: dict[str, Any]) -> str:
    """Search NodeDefinitions across every Branch for reuse candidates.

    #62 Part B. The bot's reuse-vs-invent decision depends on being
    able to ask "what nodes already exist that might fit the role I
    need?". This action returns phone-card-sized hits ranked by
    substring match + reuse_count across Branches.

    Combined with #66's ``node_ref`` primitive, the flow is:
    search_nodes → pick a hit → build_branch / add_node with
    ``node_ref={source, node_id}``.
    """
    from tinyassets.daemon_server import search_nodes

    query = (kwargs.get("query") or "").strip()
    role = (kwargs.get("role") or kwargs.get("phase") or "").strip()
    limit = int(kwargs.get("limit", 20) or 20)

    entries = search_nodes(
        _base_path(),
        query=query,
        role=role,
        limit=limit,
        viewer=_request_branch_actor(),
        public_goals_only=True,
    )

    header = "**Reusable nodes**"
    if query:
        header += f" matching '{query}'"
    if role:
        header += f" (phase={role})"
    lines = [header, ""]
    if entries:
        for e in entries[:12]:
            reuse_tag = (
                f" · used by {e['reuse_count']} branch"
                f"{'es' if e['reuse_count'] != 1 else ''}"
            )
            phase_tag = f" · phase={e['phase']}" if e.get("phase") else ""
            lines.append(
                f"- `{e['node_id']}` · **{e['display_name']}**"
                f"{phase_tag}{reuse_tag}"
            )
            desc = (e.get("description") or "").strip()
            if desc:
                lines.append(f"  {desc[:120]}")
            preview = (e.get("prompt_template_preview") or "").strip()
            if preview:
                lines.append(f"  _prompt:_ `{preview}`")
        if len(entries) > 12:
            lines.append(f"- … and {len(entries) - 12} more.")
        lines.append("")
        lines.append(
            "_To reuse: send an `add_node` operation containing "
            "`node_ref={\"source\": \"<branch_def_id>\", "
            "\"node_id\": \"<node_id>\"}` through "
            '`write_graph target="branch" branch_id="<target id>" '
            'changes_json="[...]". See #66._'
        )
    else:
        if query or role:
            lines.append(
                "_No existing nodes match. If you invent one, "
                "consider a node_id future callers would search for "
                "(e.g. `citation_audit` rather than `node_7`)._"
            )
        else:
            lines.append(
                "_No nodes registered yet. Standalone node and new-workflow "
                "creation are not exposed by the advertised handles._"
            )

    return json.dumps({
        "text": "\n".join(lines),
        "query": query,
        "role": role,
        "count": len(entries),
        "entries": entries,
    }, default=str)


# #64: whitelisted fields for bulk `patch_nodes`. Type coercion per
# field so phone-entered strings land as the right Python type.
_PATCH_NODES_FIELDS: dict[str, Any] = {
    "display_name": str,
    "description": str,
    "phase": str,
    "prompt_template": str,
    "source_code": str,
    "model_hint": str,
    "timeout_seconds": float,
    "enabled": bool,
}


def _coerce_patch_nodes_value(
    field: str, raw: Any,
) -> tuple[Any, str | None]:
    """Coerce a bulk-patch value into the right Python type.

    Returns ``(coerced, error)``. ``error`` non-None → reject without
    mutating any node; atomic.
    """
    kind = _PATCH_NODES_FIELDS[field]
    if kind is bool:
        if isinstance(raw, bool):
            return raw, None
        s = str(raw).strip().lower()
        if s in {"true", "1", "yes", "on"}:
            return True, None
        if s in {"false", "0", "no", "off"}:
            return False, None
        return None, f"Cannot coerce {raw!r} to bool."
    if kind is float:
        try:
            return float(raw), None
        except (TypeError, ValueError):
            return None, f"Cannot coerce {raw!r} to float."
    return str(raw), None


def _ext_branch_patch_nodes(kwargs: dict[str, Any]) -> str:
    """Bulk-set one field across N nodes in one call (#64).

    Different from ``patch_branch`` (heterogeneous batches of ops).
    ``patch_nodes`` is homogeneous: same field, same value, filtered by
    ``node_ids`` (default: all nodes on the branch). Atomic — if any
    node rejects, nothing is written.
    """
    from tinyassets.api.extensions import VALID_PHASES
    from tinyassets.branches import BranchDefinition
    from tinyassets.daemon_server import save_branch_definition
    from tinyassets.phase_vocab import normalize_phase

    selector = (kwargs.get("branch_def_id") or "").strip()
    if not selector:
        return json.dumps({
            "status": "rejected",
            "error": "branch_def_id is required for patch_nodes.",
        })
    field = (kwargs.get("field") or "").strip()
    if field not in _PATCH_NODES_FIELDS:
        return json.dumps({
            "status": "rejected",
            "error": (
                f"Unknown field '{field}'. patch_nodes supports: "
                f"{', '.join(sorted(_PATCH_NODES_FIELDS))}"
            ),
        })
    raw_value = kwargs.get("value")
    if raw_value is None or raw_value == "":
        return json.dumps({
            "status": "rejected",
            "error": "value is required.",
        })

    value, err = _coerce_patch_nodes_value(field, raw_value)
    if err is not None:
        return json.dumps({
            "status": "rejected",
            "error": f"Field '{field}': {err}",
        })

    if field == "phase" and value not in VALID_PHASES:
        return json.dumps({
            "status": "rejected",
            "error": (
                f"Invalid phase '{value}'. Must be one of: "
                f"{', '.join(sorted(VALID_PHASES))}"
            ),
        })
    if field == "phase":
        value = normalize_phase(str(value))

    _ensure_workflow_db()
    resolved = _resolve_readable_branch(selector, str(_base_path()))
    if resolved is None:
        return _branch_not_found(selector)
    bid, source = resolved
    if not _branch_authorized(source):
        return _branch_authority_denied()

    staging = BranchDefinition.from_dict(source)

    # Resolve target node set. Empty `node_ids` means "every node".
    target_ids_raw = kwargs.get("node_ids") or ""
    if isinstance(target_ids_raw, list):
        target_ids = [
            str(n).strip() for n in target_ids_raw if str(n).strip()
        ]
    else:
        target_ids = _split_csv(target_ids_raw)
    all_node_ids = [n.node_id for n in staging.node_defs]
    if not target_ids:
        target_ids = all_node_ids

    unknown = [nid for nid in target_ids if nid not in all_node_ids]
    if unknown:
        return json.dumps({
            "status": "rejected",
            "error": (
                f"Unknown node_ids on branch '{staging.name}': "
                f"{', '.join(unknown)}. Atomic — no node was patched."
            ),
        })

    if not target_ids:
        return json.dumps({
            "status": "rejected",
            "error": "Branch has no nodes to patch.",
        })

    # Apply the field. prompt_template / source_code are mutually
    # exclusive — clear the other when setting one.
    #
    # SECURITY (Codex round-2, PR #1349): patch_nodes is an MCP-reachable
    # node-mutation path. Changing executable content (source_code /
    # prompt_template) must not leave a node ``approved=True`` for code the
    # approver never saw. Mirror the update_node surface: reconcile approval
    # against the *new* effective source via the round-1 helper, which
    # clears every approval field unless the recorded hash still matches the
    # post-patch source. This upholds the authoring-layer invariant the
    # runtime carve-out relies on (no persisted node ever carries
    # approved=True with an empty/stale approved_source_hash).
    for node in staging.node_defs:
        if node.node_id not in target_ids:
            continue
        setattr(node, field, value)
        if field == "prompt_template" and value:
            node.source_code = ""
            # Switched to a prompt node: no executable surface to gate, and
            # any prior source approval no longer describes the body.
            _clear_source_code_approval(node)
        elif field == "source_code":
            node.prompt_template = ""
            # Approval only survives when the recorded hash still matches the
            # new source; otherwise demote to unapproved (blank provenance).
            if not _approval_provenance_valid(
                node.approved, node.source_code or "",
                node.approved_source_hash or "",
            ):
                _clear_source_code_approval(node)

    old_version = int(source.get("version") or 1)
    new_version = old_version + 1
    staging_dict = staging.to_dict()
    staging_dict["version"] = new_version
    saved = save_branch_definition(_base_path(), branch_def=staging_dict)
    persisted = BranchDefinition.from_dict(saved)

    branch_label = persisted.name or "(unnamed workflow)"
    text = (
        f"**Updated `{field}` on {len(target_ids)} node(s)** of "
        f"workflow '{branch_label}'. New value: `{value}`. "
        f"(version {old_version} → {new_version})"
    )
    per_node = [
        {"node_id": nid, "status": "updated"} for nid in target_ids
    ]
    return json.dumps({
        "text": text,
        "status": "patched",
        "field": field,
        "value": value,
        "patched_count": len(target_ids),
        "version_before": old_version,
        "version_after": new_version,
        "node_results": per_node,
    }, default=str)


# ───────────────────────────────────────────────────────────────────────────
# Branch lineage helpers
# ───────────────────────────────────────────────────────────────────────────


def _resolve_udir() -> Path:
    """Return the active universe directory (best-effort; never raises).

    Currently has no callers. Routed through ownership anyway: an unrouted
    default resolver sitting in the tree is a trap for whoever wires it up next,
    and "a directory that sorts first" is exactly the resolution that handed out
    operational stores as universes (2026-09-02).
    """
    try:
        uid = os.environ.get("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "")
        if not uid:
            from tinyassets.api.helpers import _owned_universe_dir_name

            base = _base_path()
            if base.is_dir():
                # Dot-dirs and unowned directories are both skipped: the data
                # root also holds operational state such as account deletion's
                # transient `.deleting/` staging dir.
                subdirs = sorted(
                    d for d in base.iterdir()
                    if d.is_dir()
                    and not d.name.startswith(".")
                    and _owned_universe_dir_name(base, d.name)
                )
                if subdirs:
                    uid = subdirs[0].name
        if uid:
            return _universe_dir(uid)
    except Exception:  # noqa: BLE001
        pass
    return _base_path()


def _action_fork_tree(kwargs: dict[str, Any]) -> str:
    from tinyassets.branch_versions import list_branch_versions
    from tinyassets.daemon_server import list_branch_definitions

    selector = (kwargs.get("branch_def_id") or "").strip()
    if not selector:
        return json.dumps({"error": "branch_def_id is required."})

    resolved_root = _resolve_readable_branch(selector, str(_base_path()))
    if resolved_root is None:
        return _branch_not_found(selector)
    bid, root = resolved_root

    # Walk ancestor chain via fork_from (branch_version_id → branch_def_id).
    ancestors: list[dict[str, Any]] = []
    seen_bids: set[str] = {bid}
    current_bvid = root.get("fork_from")
    if current_bvid and _resolve_readable_version(
        current_bvid,
        str(_base_path()),
    ) is None:
        current_bvid = None
    visible_fork_from = current_bvid
    while current_bvid:
        resolved_version = _resolve_readable_version(
            current_bvid,
            str(_base_path()),
        )
        if resolved_version is None:
            break
        _, version = resolved_version
        anc_bid = version["branch_def_id"]
        if anc_bid in seen_bids:
            break  # cycle guard
        seen_bids.add(anc_bid)
        resolved_ancestor = _resolve_readable_branch(
            anc_bid,
            str(_base_path()),
        )
        if resolved_ancestor is None:
            break
        _, anc = resolved_ancestor
        ancestors.append({
            "branch_def_id": anc_bid,
            "name": anc.get("name", ""),
            "author": anc.get("author", ""),
            "fork_from_version": current_bvid,
        })
        current_bvid = anc.get("fork_from")

    # Find descendants: branches whose fork_from matches any version of this branch.
    versions = list_branch_versions(_base_path(), bid, limit=200)
    version_ids = {v.branch_version_id for v in versions
                   if _resolve_readable_version(v.branch_version_id, str(_base_path()))}
    descendants: list[dict[str, Any]] = []
    all_branches = list_branch_definitions(
        _base_path(),
        viewer=_request_branch_actor() or "",
    )
    for b in all_branches:
        ff = b.get("fork_from")
        if ff and ff in version_ids:
            descendants.append({
                "branch_def_id": b["branch_def_id"],
                "name": b.get("name", ""),
                "author": b.get("author", ""),
                "fork_from_version": ff,
                "published_versions_count": len(
                    [v for v in list_branch_versions(_base_path(), b["branch_def_id"], limit=500)
                     if v.public]
                ),
            })

    return json.dumps({
        "branch_def_id": bid,
        "name": root.get("name", ""),
        "fork_from": visible_fork_from,
        "ancestors": ancestors,
        "descendant_count": len(descendants),
        "descendants": descendants[:50],
    }, default=str)


_BRANCH_ACTIONS: dict[str, Any] = {
    "create_branch": _ext_branch_create,
    "approve_source_code": _ext_branch_approve_source_code,
    "get_branch": _ext_branch_get,
    "list_branches": _ext_branch_list,
    "delete_branch": _ext_branch_delete,
    "delete_own_branch": _ext_branch_delete_own,
    "add_node": _ext_branch_add_node,
    "connect_nodes": _ext_branch_connect_nodes,
    "set_entry_point": _ext_branch_set_entry_point,
    "add_state_field": _ext_branch_add_state_field,
    "validate_branch": _ext_branch_validate,
    "describe_branch": _ext_branch_describe,
    "build_branch": _ext_branch_build,
    "patch_branch": _ext_branch_patch,
    "patch_nodes": _ext_branch_patch_nodes,
    "update_node": _ext_branch_update_node,
    "search_nodes": _ext_branch_search_nodes,
    "fork_tree": _action_fork_tree,
}

_BRANCH_WRITE_ACTIONS: frozenset[str] = frozenset({
    "create_branch", "add_node", "connect_nodes",
    "set_entry_point", "add_state_field", "delete_branch",
    "delete_own_branch",
    "build_branch", "patch_branch", "patch_nodes", "update_node",
    "approve_source_code",
})


# ───────────────────────────────────────────────────────────────────────────
# Branch Design Guide — chatbot-facing prompt body
# ───────────────────────────────────────────────────────────────────────────
# The @mcp.prompt("Branch Design Guide") decoration stays in
# ``tinyassets/universe_server.py`` (Pattern A2) so FastMCP introspection sees
# the chatbot-facing signature exactly as before. The wrapper there delegates
# to ``_branch_design_guide_prompt()`` below.


def _branch_design_guide_prompt() -> str:
    """Return the Branch Design Guide markdown body.

    Wraps the module-level ``_BRANCH_DESIGN_GUIDE`` constant so the
    universe_server-side ``@mcp.prompt`` wrapper has a single delegation
    target. Plain function (no decoration) — the FastMCP registration
    lives in ``tinyassets.universe_server.branch_design_guide``.
    """
    return _BRANCH_DESIGN_GUIDE


_BRANCH_DESIGN_GUIDE = """\
You help users inspect, edit, and run community-designed graph branches through
the advertised canonical handles. A branch is a LangGraph topology (nodes +
edges + state schema) the user can fork, share, and run.

## Before you invent — search for reusable nodes

Before you design any node, inspect known candidate branches with
`read_graph target="branch" branch_id=...`. Every node already on the server was written
once and validated; reusing it preserves lineage and lets comparative
evaluation work across branches. Global node search and cross-Goal common-node
aggregation are not exposed by the advertised handles, so do not claim an
exhaustive search.

For each relevant hit, point the user at it and ask whether to reuse.
If yes, include a `node_ref` inside the `node_defs` entry rather than
restating source_code / prompt_template:

```
{"node_id": "citation_audit",
 "node_ref": {"source": "<branch_def_id_from_search>",
              "node_id": "citation_audit"}}
```

Copy semantics are the default and usually what the user wants — the
canonical body is snapshotted into the new Branch and diverges from
there. If the user later edits it on either side, the other stays
unchanged. (v1; live shared nodes may come later.)

Bare `node_id` that collides with an existing standalone registered node is
rejected by the server; in an existing branch patch, pass `node_ref` /
`intent="copy"` or rename. This is intentional — silent shadowing was a bug
(#66).

## New-workflow authoring

Create a Branch through `write_graph target="branch" operation="create"
payload_json=...` with the complete spec below in `payload_json`.
To remix, use operation `remix` and
include a published `fork_from` version in the same spec. After validation,
freeze it through operation `publish`; cloud automation binds only immutable
published versions. Standalone node registration remains unavailable.

```
payload_json='{
  "name": "Recipe tracker",
  "description": "Capture, categorize, archive recipes",
  "entry_point": "capture",
  "skills": [
    {
      "name": "Kitchen-note style",
      "body": "Keep notes terse, ingredient-focused, and reversible.",
      "source_url": "https://example.com/skill.md",
      "source_note": "User asked to copy this from a public post."
    }
  ],
  "node_defs": [
    {"node_id": "capture", "display_name": "Capture raw recipe",
     "prompt_template": "Read the user's message and extract recipe name."},
    {"node_id": "categorize", "display_name": "Categorize recipe",
     "prompt_template": "Classify by cuisine and meal type."},
    {"node_id": "archive", "display_name": "Archive to library",
     "prompt_template": "Format as a wiki entry and file it."}
  ],
  "edges": [
    {"from": "START", "to": "capture"},
    {"from": "capture", "to": "categorize"},
    {"from": "categorize", "to": "archive"},
    {"from": "archive", "to": "END"}
  ],
  "state_schema": [
    {"name": "raw_recipe", "type": "str"},
    {"name": "category", "type": "str"},
    {"name": "archived", "type": "bool", "default": false}
  ]
}'
```

Do not fabricate validation suggestions or a batch receipt. Receipts exist
only after a real supported write completes.

## File input contracts

For exact owned binary inputs, include `io_manifest` in the create spec. A
`file` input needs a `dict` state field; `file_bundle` needs a `list` field.
For example, a list state field named `files` can declare:
`{"inputs":[{"name":"files","io_type":"file_bundle","max_count":4,"max_bytes":4194304}]}`.
Declarations are preserved exactly; invalid counts, bounds or state types refuse.
Read `read_graph target="run_file_limits"` for available capture capacity. Capture
owned authoring handles using `write_graph target="run_file" operation="capture"`
with `payload_json={"label":"<stable label>","sources":[{"session_id":"...","handle_id":"..."}]}`.
Pass the returned opaque references in `run_graph inputs_json`, never paths or
whole file bytes. Only nodes declaring that incoming field in `input_keys` can
use `read_run_file(file_id=..., offset=..., count=...)` for bounded exact reads;
downstream nodes need explicit forwarded input data, not merely the same state.

To edit the contract, use existing
`write_graph target="branch" operation="patch" branch_id=... changes_json=[...]`
with the op `{"op":"set_io_manifest","io_manifest":{...}}`; served agents put
that same ordered op list in `payload_json`. This replaces the complete
manifest. Explicit null clears it; a missing member rejects. Matching state-field
changes can be in the same atomic patch. Existing published versions/admitted
runs keep their old contract; publish again to create a new immutable version.
Canonical remix inherits the parent's contract when omitted; explicit null clears.
This does not change publication permissions or enable cross-owner file transfer.

## Branch skills

When the user wants to create a skill, remix one, or copy one they found
elsewhere for an existing Branch, attach it as a `skills` snapshot through
`write_graph target="branch" branch_id=... changes_json=...`. A skill is Branch context, not
executable code. It must include `name` and `body`; include `source_url`,
`source_note`, `parent_skill_id`, `license`, `version`, `tags`, or `metadata`
when the user gives that provenance. Do not write skill text to a shared page
as a workaround when the user wants the Branch to carry it.

## Editing an existing workflow (PREFERRED)

First call `read_graph target="branch" branch_id=...` so the edit is informed. Then call
`write_graph target="branch" branch_id=... changes_json=...` with one batch.
Transactional — all land or none:

```
write_graph target="branch" branch_id=... changes_json='[
  {"op": "add_node", "node_id": "novelty_check",
   "display_name": "Novelty assessor",
   "prompt_template": "Rate novelty of: {claim}"},
  {"op": "add_edge", "from": "categorize", "to": "novelty_check"},
  {"op": "add_edge", "from": "novelty_check", "to": "archive"},
  {"op": "remove_edge", "from": "categorize", "to": "archive"},
  {"op": "add_state_field", "name": "novelty_score", "type": "float"},
  {"op": "add_skill",
   "skill": {"name": "Review checklist",
             "body": "Check tests, code shape, and live proof."}}
]'
```

Successful branch writes include `batch_receipt`. Rejected patches do not.
The receipt lets the chatbot summarize the batch and point to remaining
blockers, but it is not an authorization grant and never overrides
`source_code` approval or host-owned gates.
Check `batch_receipt.authorization_effect` for the machine-readable
non-grant/non-bypass flags before narrating approval scope.

## Single-item surgery

Even for one small change, use one changes_json op through
`write_graph target="branch" branch_id=... changes_json=...`. Use the create
operation above only when you want a distinct new workflow. For
inspection or validation evidence, use
`read_graph target="branch" branch_id=...` and report
only what it returns.

## Hard rule

After `read_graph target="branch" branch_id=...`, check `runnable` before
telling the user their branch is ready to run. If `runnable=false`, surface
the validation `errors` or `source_code_problems` (a code node the compiler
refuses: a disallowed pattern, over the size cap, or a syntax error) and
stop. `unapproved_source_code_nodes` is provenance only -- code nodes run in
the OS sandbox and no approval gates them, so never tell the user a branch
needs approval before it can run. If `runnable=true`, use `run_graph` with a
JSON `inputs_json` that fills the state_schema fields. The runner returns a
`run_id`, final status, and per-node trace.

## Power users

Pass `source_code="def run(state): ..."` instead of `prompt_template`
for code nodes in changes_json. Pass `reducer="append"` on an
`add_state_field` op for accumulating list fields. The difference for power
users is how much you abstract on their behalf, not which handle you call.

## Running a branch

Once validated, execute with:

- `run_graph branch_def_id=... inputs_json='{"raw_recipe": "pasta"}'`
- `read_graph target="runs"` to find runs.
- `read_graph target="run" run_id=...` for a full result snapshot.

Wait, incremental stream, field-only output, and cancellation controls are not
exposed by the advertised handles. Say so plainly rather than inventing them.

The never-simulate rule lives in `control_station` (hard rule 5):
if `run_graph` fails, the branch isn't validated, or a code node will not
compile (`source_code_problems`), state the reason and stop. Approval is
provenance, never a gate.
"""
