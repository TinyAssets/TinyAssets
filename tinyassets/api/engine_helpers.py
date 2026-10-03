"""Preamble engine helpers — extracted from
``tinyassets/universe_server.py`` (Task #8 — decomp Step 10).

The smallest extraction by LOC (~336 moveable) but the **highest
test-monkeypatch-target burden** of the decomposition: 11 ``mock.patch``
sites + 2 ``monkeypatch.setattr`` sites = 13 test-patch interactions
across 5 test files. This module is the foundation that every Step-1-9
submodule lazy-imports from for ledger attribution, storage backend
resolution and error formatting. After
Step 10, the dependency-graph inversion is complete: engine_helpers.py
is a leaf module with no upstream dependency on universe_server.py.

Public surface (back-compat re-exported via ``tinyassets.universe_server``):
    Public action ledger trio:
      _current_actor()               - resolve the bound request identity or
                                       refuse when no named principal exists;
                                       patched 7+ times in tests
      _append_ledger(udir, action, ..) - durable per-universe ledger writer
      _truncate(text, limit=140)     - whitespace-collapsing string truncator
                                       for ledger summary lines

    Storage + error formatters:
      _storage_backend()             - memoized StorageBackend factory;
                                       patched 4 times in tests
      _format_dirty_file_conflict(exc) - shape DirtyFileError for MCP clients
      _filter_claims_by_branch_visibility(claims, *, viewer) - Phase 6.2.2
                                       private-branch filter for gate claims
      _filter_leaderboard_by_branch_visibility(entries, *, viewer) - sibling
                                       for leaderboard rows
      _format_commit_failed(exc)     - shape CommitFailedError for MCP clients

Cross-module note: `_filter_*_by_branch_visibility` lazy-import
`get_branch_definition` from `tinyassets.daemon_server` at function-body time
(daemon_server is a sibling, not an api/* submodule). All other engine
helpers depend only on `tinyassets.api.helpers._base_path` (top-of-module
import) and `tinyassets.catalog` types (DirtyFileError, CommitFailedError,
get_backend).

Pattern-A2-style leaf inversion: after this extraction, every Steps 1-9
submodule retargets its lazy `from tinyassets.universe_server import _X`
imports to `from tinyassets.api.engine_helpers import _X`. universe_server.py
becomes a pure routing/wrapper module (extensions() body + 6 Pattern A2
wrappers + cross-module shims + main).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tinyassets.api.helpers import _base_path, _read_json
from tinyassets.catalog import CommitFailedError, DirtyFileError, get_backend

logger = logging.getLogger("universe_server.engine_helpers")


# ---------------------------------------------------------------------------
# Public action ledger
# ---------------------------------------------------------------------------
# PLAN.md Design Decision: "Private chats, public actions." Every universe-
# affecting write must be publicly attributable. The ledger is the durable
# record of who did what, when.


def _current_actor() -> str:
    """Resolve the acting user's identity for ledger attribution.

    Authenticated MCP requests use the OAuth subject resolved at request
    entry. There is no environment or synthetic-identity fallback.
    """
    from tinyassets.auth.middleware import current_identity

    # No environment fallback and no stand-in (founder, 2026-09-02): the
    # attribution is the authenticated subject or the write refuses.
    from tinyassets.principals import named_principal

    subject = named_principal(getattr(current_identity(), "user_id", ""))
    if not subject:
        raise PermissionError("Authentication required")
    return subject


def _append_ledger(
    udir: Path,
    action: str,
    *,
    actor: str | None = None,
    target: str = "",
    summary: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    """Append one entry to the universe's public action ledger.

    Designed to never raise: ledger failures are logged but don't abort
    the surrounding write. The mutation has already landed on disk by the
    time this is called, so losing a ledger entry is strictly better than
    rolling back a successful user action.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor": actor or _current_actor(),
        "action": action,
        "target": target,
        "summary": summary,
    }
    if payload:
        entry["payload"] = payload

    ledger_path = udir / "ledger.json"
    try:
        udir.mkdir(parents=True, exist_ok=True)
        existing = _read_json(ledger_path)
        if not isinstance(existing, list):
            existing = []
        existing.append(entry)
        ledger_path.write_text(
            json.dumps(existing, indent=2, default=str),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Failed to append ledger entry at %s: %s", ledger_path, exc)


def _truncate(text: str, limit: int = 140) -> str:
    """Collapse whitespace and truncate for ledger summaries."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Storage backend + error formatters
# ---------------------------------------------------------------------------


def _storage_backend():
    """Resolve the memoized :class:`StorageBackend` for catalog writes.

    Goals + Branches live at repo root per spec §phase7_github_as_catalog
    (`goals/<slug>.yaml`, `branches/<slug>.yaml`). The repo root is
    derived from ``_base_path().parent`` — production points
    ``output/`` at the project root, so its parent IS the git repo
    root. Tests using ``TINYASSETS_DATA_DIR=<tmp_path>/output`` get
    ``<tmp_path>`` as the repo root, which isn't a git repo, so
    ``get_backend`` auto-probes to :class:`SqliteOnlyBackend` and
    leaves the host project repo untouched.
    """
    base = _base_path()
    return get_backend(base, repo_root=base.parent)


def _format_dirty_file_conflict(exc: DirtyFileError) -> dict[str, Any]:
    """Shape a :class:`DirtyFileError` for MCP clients.

    The structured payload lets the chat-side render the conflict as
    actionable options instead of an opaque traceback. Used by Phase 7.3
    write handlers; the formatter is wired separately from the raising
    sites so each handler can keep its existing return-shape idiom.
    """
    paths = [str(p) for p in getattr(exc, "paths", []) or []]
    primary = paths[0] if paths else ""
    return {
        "status": "local_edit_conflict",
        "conflicting_path": primary,
        "all_conflicts": paths,
        "options": [
            "pass force=True to overwrite",
            "commit or stash local edits first",
        ],
    }


def _filter_claims_by_branch_visibility(
    claims: list[dict[str, Any]],
    *,
    viewer: str,
) -> list[dict[str, Any]]:
    """Phase 6.2.2 — hide gate claims whose Branch is private.

    A private Branch's claim is visible only to its author. Public
    Branches are visible to everyone. The Goal's visibility is NOT
    consulted here; private Branch on public Goal is a supported
    product state.

    Branches that have been deleted (no row) are treated as "orphan
    claims" and left in the list — the caller's orphan tagging
    handles that surface separately.
    """
    if not claims:
        return claims
    from tinyassets.daemon_server import get_branch_definition

    visibility_cache: dict[str, tuple[str, str]] = {}
    filtered: list[dict[str, Any]] = []
    for claim in claims:
        bid = claim.get("branch_def_id", "")
        if not bid:
            filtered.append(claim)
            continue
        if bid not in visibility_cache:
            try:
                branch = get_branch_definition(
                    _base_path(), branch_def_id=bid,
                )
                visibility_cache[bid] = (
                    branch.get("visibility", "public") or "public",
                    branch.get("author", "") or "",
                )
            except KeyError:
                # Orphan claim — branch row gone. Keep the claim.
                visibility_cache[bid] = ("public", "")
        branch_visibility, branch_author = visibility_cache[bid]
        if branch_visibility == "private" and branch_author != viewer:
            continue
        filtered.append(claim)
    return filtered


def _filter_leaderboard_by_branch_visibility(
    entries: list[dict[str, Any]],
    *,
    viewer: str,
) -> list[dict[str, Any]]:
    """Phase 6.2.2 — hide leaderboard entries whose Branch is private.

    Same contract as :func:`_filter_claims_by_branch_visibility` but
    operates on leaderboard shape (``branch_def_id`` key present).
    """
    if not entries:
        return entries
    from tinyassets.daemon_server import get_branch_definition

    filtered: list[dict[str, Any]] = []
    for entry in entries:
        bid = entry.get("branch_def_id", "")
        if not bid:
            filtered.append(entry)
            continue
        try:
            branch = get_branch_definition(
                _base_path(), branch_def_id=bid,
            )
        except KeyError:
            filtered.append(entry)
            continue
        visibility = branch.get("visibility", "public") or "public"
        author = branch.get("author", "") or ""
        if visibility == "private" and author != viewer:
            continue
        filtered.append(entry)
    return filtered


def _format_commit_failed(exc: CommitFailedError) -> dict[str, Any]:
    """Shape a :class:`CommitFailedError` for MCP clients.

    SQLite row is retained (Path A — SQLite is the accepted-write
    boundary); YAML is rolled back; the write is queued in
    ``unreconciled_writes`` for a future ``sync_commit`` replay.
    """
    paths = [str(p) for p in getattr(exc, "paths", []) or []]
    return {
        "status": "git_commit_failed",
        "error": "git_commit_failed",
        "helper": exc.helper,
        "git_error": exc.git_error,
        "paths": paths,
        "row_ref": exc.row_ref,
        "note": (
            "SQLite write accepted; git commit failed and was rolled "
            "back. Entry queued in unreconciled_writes for later "
            "sync_commit replay."
        ),
    }
