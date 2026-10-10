"""Command center MCP tool surface — extracted from
``tinyassets/universe_server.py`` (Task #7 — decomp Step 9).

The largest single submodule extracted from the monolith: 27 universe-tool
``_action_*`` handlers (universe-CRUD, daemon control, canon, queue,
subscriptions, goal-pool, daemon overview, tier config), the ``WRITE_ACTIONS``
dispatch table + 14 ``_extract_*`` extractor closures, the ledger dispatcher
trio (``_ledger_target_dir`` / ``_scope_universe_response`` /
``_dispatch_with_ledger``), the daemon telemetry block (``_last_activity_at``
-- newest of the retired fleet loop's heartbeat files and started runs in the
runs ledger, scoped to this command center -- ``_staleness_bucket``, ``_phase_human``,
``_compute_accept_rate_from_db``, ``_compute_word_count_from_files``,
``_daemon_liveness``, ``_parse_activity_line``), and the Pattern A2 body of
the ``universe()`` MCP tool exposed as ``_universe_impl(action, **kwargs)``.

The ``@mcp.tool() def universe(...)`` decorator + 23-arg signature + chatbot-
facing docstring stays in ``tinyassets/universe_server.py`` (Pattern A2) so
FastMCP introspection sees the surface unchanged. The wrapper there delegates
to ``_universe_impl()`` from this module.

Public surface (back-compat re-exported via ``tinyassets.universe_server``):
    WRITE_ACTIONS                       : dispatch table (14 write actions)
    _extract_*                          : 14 extractor closures
    _ledger_target_dir                  : universe-dir resolver for ledger entries
    _scope_universe_response            : #15 contract — `Universe: <id>` text lead-in
    _dispatch_with_ledger               : universe-tool ledger wrapper
    _universe_impl                      : Pattern A2 body for the ``universe()`` MCP tool
    _last_activity_at, _staleness_bucket, _phase_human :
                                          daemon telemetry primitives
    _compute_accept_rate_from_db        : reads ``<udir>/story.db`` directly
    _compute_word_count_from_files      : walks ``<udir>/output/**/*.md``
    _daemon_liveness                    : composite liveness block (test-monkeypatched)
    _parse_activity_line                : activity-log line parser (single-caller helper)
    _action_*                           : 27 universe-tool handlers
    _list_output_tree, _trim_overview_for_bytes, _overview_limits,
    _tail_file_lines, _query_world_db, _normalize_escaped_text,
    _goal_pool_not_available, _paid_market_not_available :
                                          handler-scoped helpers

Cross-module note: ``_current_actor``, ``_truncate``, ``_append_ledger``,
``_storage_backend``, (and
``_format_dirty_file_conflict``, ``_format_commit_failed`` if needed by future
edits) live in ``tinyassets.universe_server`` (preamble engine helpers
territory) and are lazy-imported inside the functions that use them. This
avoids the load-time cycle (universe_server back-compat-imports symbols from
this module). Step 10 (``engine_helpers.py``) will retarget these lazy imports
to ``tinyassets.api.engine_helpers``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from tinyassets.api import permissions
from tinyassets.api.helpers import (
    _base_path,
    _read_json,
    _read_text,
    _request_universe,
    _universe_dir,
)
from tinyassets.catalog import list_unreconciled_writes
from tinyassets.ids import new_universe_id
from tinyassets.ingestion.canon_io import iter_canon_files, read_canon_bytes, safe_canon_path
from tinyassets.role_center_admission import ensure_center_dir as _ensure_center_dir
from tinyassets.storage_accounting import StorageRefused
from tinyassets.universe_bundle import seed_okf_bundle
from tinyassets.universe_files import (
    MAX_CONFIG_BYTES,
    MAX_PLATFORM_FILE_BYTES,
    load_untrusted_yaml,
    read_data_path,
    write_data_path,
)
from tinyassets.universe_soul import (
    NO_LOOP_DECLARED,
    SOUL_FILENAME,
    has_soul,
    legacy_premise_path,
    premise_from_soul,
    read_legacy_premise,
    read_universe_soul,
    write_universe_soul,
)

logger = logging.getLogger("universe_server.universe")

ENV_CAPABILITIES_VAR = "UNIVERSE_SERVER_CAPABILITIES"
ACTION_CANCEL_BRANCH_TASK = "cancel_branch_task"
ACTION_POST_PRIORITY_GOAL_POOL = "post_priority_goal_pool"
LEGACY_FANTASY_LOOP_BRANCH_DEF_ID = "fantasy_author:universe_cycle_wrapper"


def _env_actor_grants() -> tuple[str, ...]:
    raw = os.environ.get(ENV_CAPABILITIES_VAR, "")
    return tuple(part for part in re.split(r"[\s,]+", raw.strip()) if part)


def _env_actor_can(action: str, *, universe_id: str = "") -> bool:
    # The bound principal decides, not the environment. The helper keeps its
    # name for its callers; what it resolves is the authenticated subject.
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.auth.provider import PermissionScope, resolve_permission

    actor = _current_actor()
    grants = _env_actor_grants()
    return resolve_permission(
        actor_id=actor,
        action=action,
        grants=grants,
        scope=PermissionScope(universe_id=universe_id),
    ).allowed


# Daemon-scoped actions operate on a daemon's own operational memory (keyed by
# daemon_id under daemon_wikis/), NOT on a universe brain. They are authorized by
# daemon ownership / the autonomous daemon runtime, not the per-universe ACL.
# Routing them through the universe ACL would (a) misattribute them to whatever
# _default_universe() happens to resolve to and (b) break autonomous daemon
# memory writes, which run with no founder OAuth grant.
_DAEMON_SCOPED_ACTIONS: frozenset[str] = frozenset({
    "daemon_memory_capture",
    "daemon_memory_search",
    "daemon_memory_list",
    "daemon_memory_review",
    "daemon_memory_promote",
    "daemon_memory_status",
})


def _universe_acl_error(action: str, *, universe_id: str = "") -> str | None:
    """Gate a command center action against the single ACL path in
    ``tinyassets.api.permissions``.

    ``list`` and ``create_universe`` are exempt: ``list`` filters visibility
    per-universe inside ``_action_list_universes``; ``create_universe`` has no
    pre-existing command center to authorize against (it is scope-gated + founder-
    granted on create instead). Daemon-scoped actions
    (``_DAEMON_SCOPED_ACTIONS``) are exempt because they are not universe-brain
    operations — see that set's comment.
    """
    if action in {"list", "create_universe"} or action in _DAEMON_SCOPED_ACTIONS:
        return None

    uid = _request_universe(universe_id)
    write = action in WRITE_ACTIONS
    if permissions.universe_access_allows(uid, write=write):
        return None

    return json.dumps(
        permissions.universe_access_error(
            universe_id=uid,
            write=write,
            action=action,
            surface="universe",
        )
    )

# WRITE_ACTIONS is the single source of truth for which `universe` tool
# actions are writes. The dispatcher consults this table; any action
# registered here is funneled through `_dispatch_with_ledger`, which
# refuses to return a success response without first writing the ledger
# entry. To add a new write action: put its name here with extractors for
# (target, summary, payload). No handler-side ledger code is needed — and
# no handler can silently skip the ledger.
#
# Each entry maps action name -> extractor callable:
#   extractor(kwargs, result_dict) -> (target: str, summary: str, payload: dict | None)
# kwargs is the normalized handler kwargs. result_dict is the parsed JSON
# of the handler's return string (used to pick up server-generated IDs like
# request_id / note_id).


def _extract_submit_request(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    return (
        str(result.get("request_id", "")),
        _truncate(kwargs.get("text", "")),
        {
            "request_type": kwargs.get("request_type", "") or None,
            "branch_id": kwargs.get("branch_id", "") or None,
            "pickup_incentive": kwargs.get("pickup_incentive", "") or None,
            "directed_daemon_id": kwargs.get("directed_daemon_id", "") or None,
            "request_classification": result.get("request_classification"),
            "loop_dispatch": result.get("loop_dispatch"),
        },
    )


def _extract_give_direction(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    return (
        kwargs.get("target") or str(result.get("note_id", "")),
        _truncate(kwargs.get("text", "")),
        {
            "category": kwargs.get("category", "direction"),
            "note_id": result.get("note_id", ""),
            "anchor": result.get("anchor", {}),
        },
    )


def _extract_set_premise(
    kwargs: dict[str, Any], _result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    text = kwargs.get("text", "")
    return (
        SOUL_FILENAME,
        _truncate(text),
        {
            "bytes": len(text.encode("utf-8")),
            "legacy_program_mirror": "PROGRAM.md",
        },
    )


def _extract_set_visibility(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    """Ledger row for an owner's exposure decision.

    Exposing a command center to other users is an authority change, so it is ledgered
    like every other command center write — the ledger is how "the owner chose this"
    stays auditable after the fact, independent of the provenance key.
    """
    requested = str(kwargs.get("visibility", "") or "")
    return (
        "visibility",
        f"visibility -> {result.get('visibility', '') or requested}",
        {
            "visibility": result.get("visibility", ""),
            "previous_visibility": result.get("previous_visibility", ""),
            "requested": requested,
        },
    )


def _extract_add_canon(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    name = result.get("filename", "") or kwargs.get("filename", "")
    provenance = kwargs.get("provenance_tag", "")
    return (
        f"canon/{name}",
        _truncate(f"{name} ({provenance or 'untagged'})"),
        {
            "filename": name,
            "provenance": provenance,
            "bytes": len(kwargs.get("text", "").encode("utf-8")),
        },
    )


def _extract_control_daemon(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    return (
        ".pause",
        str(result.get("action", kwargs.get("text", "").strip().lower())),
        {"status": result.get("status", "")},
    )


def _extract_switch_universe(
    kwargs: dict[str, Any], _result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    uid = kwargs.get("universe_id", "")
    return (uid, f"daemon switched to {uid}", {})


def _extract_create_universe(
    kwargs: dict[str, Any], _result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    # universe_id may be server-generated (optional on create), so prefer the
    # id in the handler result over the caller kwargs — otherwise the ledger
    # row loses the generated id (target: "").
    uid = _result.get("universe_id") or kwargs.get("universe_id", "")
    text = kwargs.get("text", "")
    summary = _truncate(text) if text.strip() else f"created {uid}"
    return (uid, summary, {
        "has_premise": bool(text.strip()),
        "has_soul": True,
        "soul_path": SOUL_FILENAME,
        "loop_branch_def_id": str(kwargs.get("branch_def_id") or "").strip(),
    })


def _synthesis_first_run_checklist(has_premise: bool) -> dict[str, Any]:
    """Explain when upload synthesis signals become actionable on first run."""
    steps = [
        {
            "id": "premise",
            "label": (
                "Save a purpose with write_graph target=\"command_center\" text= "
                "when creating the command center."
            ),
            "complete": has_premise,
        },
        {
            "id": "canon_source",
            "label": (
                "Canon-source upload is not exposed by the advertised handles."
            ),
            "complete": False,
        },
        {
            "id": "synthesis_signal",
            "label": "Confirm the upload response reports synthesis_signal_emitted=true.",
            "complete": False,
        },
        {
            "id": "daemon_enrich",
            "label": "Let the daemon process the synthesize_source signal in an enrich cycle.",
            "complete": False,
        },
    ]
    if has_premise:
        next_action = (
            "Canon-source upload and synthesis waiting are not exposed by the "
            "advertised handles."
        )
    else:
        next_action = (
            "Set a premise before uploading canon; synthesis needs the premise "
            "and source material to produce useful context."
        )
    return {
        "synthesis_signal_meaning": (
            "synthesis_signal_emitted only means an uploaded source was queued; "
            "it is meaningful after a premise exists, at least one canon source "
            "has been uploaded, and the daemon has processed the synthesize_source "
            "signal."
        ),
        "steps": steps,
        "next_action": next_action,
    }


def _universe_loop_dispatch(udir: Path) -> tuple[str, dict[str, Any]]:
    soul = read_universe_soul(udir)
    if soul is None:
        if not read_legacy_premise(udir).strip():
            return NO_LOOP_DECLARED, {
                "source": "no_soul_no_loop_declared",
                "has_soul": False,
                "branch_def_id": "",
                "error": "universe_loop_not_declared",
                "note": (
                    "This command center has no soul.md and no legacy PROGRAM.md; "
                    "declare a Loop branch in soul.md before queuing work."
                ),
            }
        return LEGACY_FANTASY_LOOP_BRANCH_DEF_ID, {
            "source": "legacy_program_fantasy_compat",
            "has_soul": False,
            "branch_def_id": LEGACY_FANTASY_LOOP_BRANCH_DEF_ID,
            "caveat": (
                "Legacy command center has PROGRAM.md but no soul.md; keeping the "
                "fantasy loop as an explicit compatibility path only until "
                "the command center is migrated to a soul-declared loop. Scheduled "
                "for removal once PROGRAM.md-only command centers are migrated; "
                "tracked under the de-fantasy audit "
                "docs/audits/2026-06-24-fantasy-architecture-residue-audit.md."
            ),
        }
    branch_def_id = soul.loop_branch_def_id.strip()
    if not branch_def_id:
        return NO_LOOP_DECLARED, {
            "source": SOUL_FILENAME,
            "has_soul": True,
            "branch_def_id": "",
            "error": "universe_loop_not_declared",
            "note": (
                "This command center has a soul.md but no Loop branch declaration; "
                "new souled command centers do not silently attach the fantasy loop."
            ),
        }
    return branch_def_id, {
        "source": SOUL_FILENAME,
        "has_soul": True,
        "branch_def_id": branch_def_id,
    }


# action name -> (extractor, control_daemon_gate)
# control_daemon_gate: if set, the wrapper only logs when the daemon action
# was an actual write (pause/resume), not a read (status).
def _extract_queue_cancel(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    return (
        str(kwargs.get("branch_task_id", "")),
        _truncate(f"cancel {kwargs.get('branch_task_id', '')}"),
        {"status": result.get("status", "")},
    )


def _extract_subscribe_goal(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    g = str(kwargs.get("goal_id", ""))
    return (g, _truncate(f"subscribe {g}"), {"status": result.get("status", "")})


def _extract_unsubscribe_goal(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    g = str(kwargs.get("goal_id", ""))
    return (g, _truncate(f"unsubscribe {g}"), {"status": result.get("status", "")})


def _extract_post_to_goal_pool(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    g = str(kwargs.get("goal_id", ""))
    bd = str(kwargs.get("branch_def_id", ""))
    return (
        str(result.get("path", f"goal_pool/{g}")),
        _truncate(f"post {bd} to {g}"),
        {
            "goal_id": g,
            "branch_def_id": bd,
            "status": result.get("status", ""),
        },
    )


def _extract_submit_node_bid(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    nb = str(result.get("node_bid_id", ""))
    nd = str(kwargs.get("node_def_id", ""))
    bid = kwargs.get("bid", 0.0)
    return (
        str(result.get("path", f"bids/{nb}.yaml")),
        _truncate(f"bid {bid} for node {nd}"),
        {
            "node_bid_id": nb,
            "node_def_id": nd,
            "bid": bid,
            "status": result.get("status", ""),
        },
    )


def _extract_set_tier_config(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    tier_name = str(kwargs.get("tier", ""))
    en = bool(kwargs.get("enabled", False))
    return (
        f"tier/{tier_name}",
        _truncate(f"set_tier_config {tier_name}={en}"),
        {
            "tier": tier_name,
            "enabled": en,
            "status": result.get("status", ""),
        },
    )


def _extract_daemon_create(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    daemon = result.get("daemon", {}) if isinstance(result.get("daemon"), dict) else {}
    daemon_id = str(daemon.get("daemon_id") or "")
    name = str(daemon.get("display_name") or "")
    return (
        daemon_id,
        _truncate(f"create daemon {name}"),
        {
            "soul_mode": daemon.get("soul_mode"),
            "has_soul": daemon.get("has_soul"),
            "domain_claims": daemon.get("domain_claims", []),
        },
    )


def _extract_daemon_summon(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    runtime = result.get("runtime", {}) if isinstance(result.get("runtime"), dict) else {}
    runtime_id = str(runtime.get("runtime_instance_id") or "")
    provider = str(runtime.get("provider_name") or "")
    daemon_id = str(runtime.get("daemon_id") or "")
    return (
        runtime_id,
        _truncate(f"summon {daemon_id} on {provider}"),
        {
            "daemon_id": daemon_id,
            "provider_name": provider,
            "model_name": runtime.get("model_name"),
            "status": runtime.get("status"),
        },
    )


def _extract_daemon_banish(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    runtime = result.get("runtime", {}) if isinstance(result.get("runtime"), dict) else {}
    runtime_id = str(runtime.get("runtime_instance_id") or "")
    return (
        runtime_id,
        _truncate(f"banish runtime {runtime_id}"),
        {
            "daemon_id": runtime.get("daemon_id"),
            "status": runtime.get("status"),
        },
    )


def _extract_daemon_control(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    runtime_id = str(result.get("runtime_instance_id") or "")
    action = str(result.get("action") or kwargs.get("action", "daemon_control"))
    return (
        runtime_id or str(result.get("daemon_id", "")),
        _truncate(f"{action} {runtime_id}"),
        {
            "daemon_id": result.get("daemon_id"),
            "runtime_instance_id": result.get("runtime_instance_id"),
            "authority_scope": result.get("authority_scope"),
            "effect": result.get("effect"),
            "action_id": result.get("action_id"),
        },
    )


def _extract_daemon_update_behavior(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    daemon_id = str(result.get("daemon_id") or "")
    return (
        daemon_id,
        _truncate(f"update daemon behavior {daemon_id}"),
        {
            "daemon_id": daemon_id,
            "authority_scope": result.get("authority_scope"),
            "effect": result.get("effect"),
            "action_id": result.get("action_id"),
        },
    )


def _extract_daemon_memory_capture(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    entry = result.get("entry", {}) if isinstance(result.get("entry"), dict) else {}
    entry_id = str(entry.get("entry_id") or "")
    daemon_id = str(result.get("daemon_id") or entry.get("daemon_id") or "")
    return (
        entry_id,
        _truncate(f"capture daemon memory {daemon_id}"),
        {
            "daemon_id": daemon_id,
            "entry_id": entry_id,
            "memory_kind": entry.get("memory_kind"),
            "promotion_state": entry.get("promotion_state"),
        },
    )


def _extract_daemon_memory_review(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    entry_id = str(result.get("entry_id") or "")
    daemon_id = str(result.get("daemon_id") or "")
    decision = str(result.get("decision") or "")
    return (
        entry_id,
        _truncate(f"{decision} daemon memory {entry_id}"),
        {
            "daemon_id": daemon_id,
            "entry_id": entry_id,
            "decision": decision,
        },
    )


def _extract_daemon_memory_promote(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    promotion_id = str(result.get("promotion_id") or "")
    daemon_id = str(result.get("daemon_id") or "")
    return (
        promotion_id,
        _truncate(f"promote daemon memory {daemon_id}"),
        {
            "daemon_id": daemon_id,
            "promotion_id": promotion_id,
            "entry_ids": result.get("entry_ids", []),
            "promoted_count": result.get("promoted_count", 0),
        },
    )


def _extract_declare_universe_loop(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    declared = str(result.get("loop_dispatch", {}).get("branch_def_id", ""))
    return (
        SOUL_FILENAME,
        _truncate(
            f"loop branch declared: {declared}" if declared else "loop declaration cleared"
        ),
        {"branch_def_id": declared, "status": result.get("status")},
    )


def _extract_soul_edit(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    from tinyassets.api.engine_helpers import _truncate
    return (
        str(result.get("universe_id", "")),
        _truncate("learned: " + ", ".join(result.get("updated_files") or [])),
        {
            "files": result.get("updated_files"),
            "snapshot": result.get("snapshot"),
            "source": result.get("source"),
        },
    )


def _extract_set_engine(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    """Ledger extractor for set_engine. Reads only redacted fields from the
    handler result — never the raw api_key (which lives in kwargs.inputs_json
    and must not reach the public ledger)."""
    from tinyassets.api.engine_helpers import _truncate
    source = str(result.get("engine_source", ""))
    writer = str(result.get("preferred_writer", ""))
    return (
        str(result.get("universe_id", "")),
        _truncate(f"engine assigned: source={source} writer={writer}"),
        {
            "engine_source": source,
            "service": str(result.get("service", "")),
            "preferred_writer": writer,
            "status": result.get("status", ""),
        },
    )


def _extract_offer_engine(
    kwargs: dict[str, Any], result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    """Ledger extractor for offer_engine (founder market supply). Never logs the
    engine credential — offers carry only service/model/rate/cap metadata."""
    from tinyassets.api.engine_helpers import _truncate
    return (
        str(result.get("founder_id", "")),
        _truncate(f"market offer: {result.get('status', '')} "
                  f"{result.get('offer_key', '')}"),
        {
            "status": result.get("status", ""),
            "offer_key": str(result.get("offer_key", "")),
            "offer_count": len(result.get("offers", []) or []),
        },
    )


WRITE_ACTIONS: dict[str, Any] = {
    "submit_request": (_extract_submit_request, None),
    "give_direction": (_extract_give_direction, None),
    "set_premise": (_extract_set_premise, None),
    "set_visibility": (_extract_set_visibility, None),
    "soul.edit": (_extract_soul_edit, None),
    "declare_universe_loop": (_extract_declare_universe_loop, None),
    "set_engine": (_extract_set_engine, None),
    "offer_engine": (_extract_offer_engine, None),
    "add_canon": (_extract_add_canon, None),
    "control_daemon": (_extract_control_daemon, {"pause", "resume"}),
    "switch_universe": (_extract_switch_universe, None),
    "create_universe": (_extract_create_universe, None),
    "queue_cancel": (_extract_queue_cancel, None),
    "subscribe_goal": (_extract_subscribe_goal, None),
    "unsubscribe_goal": (_extract_unsubscribe_goal, None),
    "post_to_goal_pool": (_extract_post_to_goal_pool, None),
    "submit_node_bid": (_extract_submit_node_bid, None),
    "set_tier_config": (_extract_set_tier_config, None),
    "daemon_create": (_extract_daemon_create, None),
    "daemon_summon": (_extract_daemon_summon, None),
    "daemon_banish": (_extract_daemon_banish, None),
    "daemon_pause": (_extract_daemon_control, None),
    "daemon_resume": (_extract_daemon_control, None),
    "daemon_restart": (_extract_daemon_control, None),
    "daemon_update_behavior": (_extract_daemon_update_behavior, None),
    "daemon_memory_capture": (_extract_daemon_memory_capture, None),
    "daemon_memory_review": (_extract_daemon_memory_review, None),
    "daemon_memory_promote": (_extract_daemon_memory_promote, None),
}


def _ledger_target_dir(
    action: str, kwargs: dict[str, Any], result: dict[str, Any] | None = None,
) -> Path:
    """Resolve which command center directory owns the ledger entry for this action.

    create_universe writes to the newly-created command center's ledger. For a
    server-generated id the kwargs carry no ``universe_id``, so prefer the id in
    the handler result — otherwise the entry would wrongly land in the default
    command center's ledger. All others write to the command center whose state they affect.
    """
    if action == "create_universe":
        created = str((result or {}).get("universe_id") or "") or kwargs.get("universe_id", "")
        return _base_path() / (created or _request_universe(""))
    uid = _request_universe(
        kwargs.get("universe_id", "") or kwargs.get("graph_id", "")
    )
    return _universe_dir(uid)


def _scope_universe_response(result_str: str) -> str:
    """Ensure every universe-scoped response leads with a `Universe: <id>`
    header and puts `universe_id` as the first key.

    #15 contract: downstream reasoning must be able to ground a response to
    its command center without re-reading the full JSON. On phones the bot often
    summarizes; a phone-legible `text` lead-in survives summarization even
    when deep JSON fields don't.

    Responses that aren't a dict, aren't JSON, or have no `universe_id`
    field are returned unchanged — errors without command center context must
    not claim a command center, and multi-universe responses (e.g. list) stay
    as-is.
    """
    try:
        data = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return result_str
    if not isinstance(data, dict):
        return result_str
    uid = data.get("universe_id")
    if not isinstance(uid, str) or not uid:
        return result_str

    header = f"Command center: {uid}"
    scoped: dict[str, Any] = {"universe_id": uid}
    existing_text = data.get("text")
    if isinstance(existing_text, str) and existing_text.strip():
        scoped["text"] = f"{header}\n\n{existing_text}"
    else:
        scoped["text"] = header
    for k, v in data.items():
        if k in ("universe_id", "text"):
            continue
        scoped[k] = v
    return json.dumps(scoped, default=str)


def _dispatch_with_ledger(
    action: str,
    handler: Any,
    kwargs: dict[str, Any],
    *,
    scope_response: bool = True,
) -> str:
    """Enforce: every WRITE action lands in the public ledger before returning.

    If the handler returned an error or the action is a write gate that was
    actually a read (e.g. control_daemon text=status), no ledger entry is
    written. For all successful writes, we parse the result, derive the
    attribution fields via the declared extractor, and append the entry.

    Ledger append failures degrade gracefully (logged warning): the mutation
    has already landed on disk, so refusing to return success would be worse
    than missing one audit entry.

    Every return path passes through `_scope_universe_response` so any
    universe-scoped dict gets the `Universe: <id>` text lead-in and key
    reordering (#15).
    """
    from tinyassets.api.engine_helpers import _append_ledger

    def finish(value: str) -> str:
        return _scope_universe_response(value) if scope_response else value

    result_str = handler(**kwargs)

    spec = WRITE_ACTIONS.get(action)
    if spec is None:
        return finish(result_str)

    extractor, write_gate = spec

    try:
        result = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return finish(result_str)

    if not isinstance(result, dict) or "error" in result:
        return finish(result_str)
    if result.get("idempotent_replay") is True:
        return finish(result_str)

    # control_daemon branch — only append if actually a write
    if write_gate is not None:
        daemon_action = (kwargs.get("text") or "").strip().lower()
        if daemon_action not in write_gate:
            return finish(result_str)

    try:
        target, summary, payload = extractor(kwargs, result)
        udir = _ledger_target_dir(action, kwargs, result)
        _append_ledger(
            udir, action, target=target, summary=summary, payload=payload,
        )
    except Exception as exc:
        logger.warning("Ledger extraction failed for %s: %s", action, exc)

    return finish(result_str)


# ---------------------------------------------------------------------------
# Daemon telemetry — liveness, staleness, human-readable phase
# ---------------------------------------------------------------------------
# The daemon writes `current_phase` and `last_updated` into status.json via
# `domains.fantasy_daemon.phases._activity.update_phase`. status.json itself
# is not a heartbeat — it only moves when a phase transitions. For liveness
# we also consult `activity.log`, which is appended to on every node entry,
# `.runtime_status.json`, which is refreshed while the graph process is alive,
# and PROGRAM.md + work_targets.json to disambiguate "no premise" vs
# "starved for work" vs "actually running".
#
# The fleet daemon loop that wrote those three files was retired on
# 2026-08-29 (`user-owned-automations`); real activity since then comes
# from automation runs (`tinyassets.automations` /
# `tinyassets.runtime.assigned_queue_consumer`) and schedule runs
# (`tinyassets.scheduler`), both recorded in the runs ledger
# (`tinyassets.runs`) as rows scoped to this universe via
# ``queue_universe_id``. `_last_activity_at` takes the newest across the
# file-based signals AND actually-started runs in the ledger so a universe
# running only through those paths does not read as dormant. It deliberately
# does NOT consult the automations store: `AutomationStore.last_finished_at`
# is bumped on a REFUSED attempt too (Codex ADAPT, 2026-08-29), so treating
# it as activity could keep the canary green while every requested
# automation is refused. Only a run that actually started counts.


# Staleness buckets, in seconds. Chosen to match the lead's spec: <1h fresh,
# <24h idle, >24h dormant. "fresh" is the only bucket that should be read
# as "the daemon is alive right now".
_STALE_FRESH_SECONDS = 60 * 60
_STALE_IDLE_SECONDS = 24 * 60 * 60


def _file_based_last_activity(
    udir: Path, status: dict[str, Any] | None,
) -> datetime | None:
    """Newest on-disk heartbeat from the retired fleet daemon loop's files.

    Uses the newest of activity.log mtime (node progress),
    .runtime_status.json mtime (running-process heartbeat), status.json's
    `last_updated`, and status.json file mtime, in that precedence order
    (unchanged from before the runs-ledger and automations-store sources
    were added in `_last_activity_at`). Returns None if none of these files
    exist or parse.

    `status.json`'s `last_updated` is user/daemon-authored free text on a
    public MCP read, so it gets the same treatment as the runs-ledger epoch
    in `_safe_epoch_to_datetime`: the UTC-normalization conversion
    (`.astimezone(timezone.utc)`) can raise `OverflowError` for an
    out-of-range offset combination (an extreme year paired with a large
    UTC offset can shift the result past `datetime.min`/`datetime.max` --
    Codex ADAPT round 2 reproduced this with both a 9999 negative-offset
    and a year-1 positive-offset value) even though `datetime.fromisoformat`
    itself parsed successfully, so the conversion is caught too, not just
    the parse. A successfully parsed value more than 5 minutes in the
    future is also rejected, matching the runs-ledger hygiene -- and, per
    Codex ADAPT round 3's founder note, so is every file-mtime candidate
    below (`activity.log`, `.runtime_status.json`, and the `status.json`
    mtime fallback): a spoofed or clock-skewed future mtime shouldn't read
    as "just happened" any more than a corrupt runs-ledger timestamp should.
    """
    def _reject_future(candidate: datetime) -> datetime | None:
        if (candidate - datetime.now(timezone.utc)).total_seconds() > 300:
            return None
        return candidate

    heartbeat_candidates: list[datetime] = []

    for path in (udir / "activity.log", udir / ".runtime_status.json"):
        if not path.exists():
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        accepted = _reject_future(mtime)
        if accepted is not None:
            heartbeat_candidates.append(accepted)
    if heartbeat_candidates:
        return max(heartbeat_candidates)

    if status and isinstance(status, dict):
        last_updated = status.get("last_updated")
        if isinstance(last_updated, str) and last_updated:
            parsed: datetime | None
            try:
                parsed = datetime.fromisoformat(last_updated)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                else:
                    parsed = parsed.astimezone(timezone.utc)
            except (ValueError, OverflowError, OSError):
                parsed = None
            if parsed is not None:
                parsed = _reject_future(parsed)
            if parsed is not None:
                return parsed

    status_path = udir / "status.json"
    if status_path.exists():
        try:
            mtime = datetime.fromtimestamp(
                status_path.stat().st_mtime, tz=timezone.utc,
            )
        except OSError:
            mtime = None
        if mtime is not None:
            accepted = _reject_future(mtime)
            if accepted is not None:
                return accepted

    return None


def _safe_epoch_to_datetime(epoch: float) -> datetime | None:
    """Convert an epoch-seconds activity timestamp to a UTC datetime, or None.

    ``_last_activity_at`` feeds a public MCP read (`read_graph target=graph`),
    so a corrupt or adversarial timestamp in the runs DB must degrade to "no
    signal" rather than raising or falsely reporting freshness (Codex ADAPT,
    2026-08-29): rejects non-finite values, non-positive values, and values
    more than 5 minutes in the future (clock-skew tolerance beyond which we
    no longer trust it as "now"). Also wraps `datetime.fromtimestamp` itself
    -- a finite float can still be large enough in magnitude to raise
    `OverflowError` or `OSError` there on some platforms.
    """
    try:
        if not math.isfinite(epoch) or epoch <= 0:
            return None
        if epoch > time.time() + 300:
            return None
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _latest_run_activity_at(universe_id: str) -> datetime | None:
    """Newest actually-started run for ``universe_id`` in the runs ledger.

    Automation and schedule runs are recorded by `tinyassets.runs` with
    ``queue_universe_id`` set to the command center they ran under -- the
    authoritative execution scope every universe-run entry point populates
    (the automation attempt runner, the schedule tick's
    `enqueue_universe_branch_run`, and the interactive `run_branch` MCP
    action all pass `_enqueue_universe_id` through to it) -- but never touch
    the retired fleet daemon loop's heartbeat files, so without this a
    command center that is actively completing runs would still read as dormant.

    Delegates the actually-started / non-queued filtering and the
    read-only, short-timeout, scope-correct query to
    `tinyassets.runs.latest_run_activity_for_universe`. Read-only and fails
    soft here too: no `.runs.db` yet (never run), a locked DB, or any other
    lookup error is not a programming error -- it's logged at debug and
    treated as no signal, never raised through a status read.
    """
    if not universe_id:
        return None
    try:
        from tinyassets.runs import latest_run_activity_for_universe

        epoch = latest_run_activity_for_universe(
            _base_path(), universe_id=universe_id,
        )
    except Exception as exc:
        logger.debug(
            "runs-ledger activity lookup failed for %s: %s", universe_id, exc,
        )
        return None
    if epoch is None:
        return None
    return _safe_epoch_to_datetime(epoch)


def _last_activity_at(udir: Path, status: dict[str, Any] | None) -> str | None:
    """Return the most recent heartbeat ISO timestamp we can find.

    The retired fleet daemon loop wrote `activity.log` / `.runtime_status.json`
    / `status.json` directly, so those files went stale the moment
    `user-owned-automations` retired that loop. This now returns the newest
    across two source families: (1) `_file_based_last_activity` -- the
    original on-disk heartbeat files, kept for command centers/tests that still
    only have those; and (2) the runs ledger (`_latest_run_activity_at`) --
    automation and schedule runs recorded via `tinyassets.runs`, scoped by
    `queue_universe_id` and filtered to runs that actually started. Does NOT
    consult the automations store -- see the module comment above
    `_STALE_FRESH_SECONDS` for why. Returns None only if neither family has
    anything.
    """
    candidates: list[datetime] = []

    file_ts = _file_based_last_activity(udir, status)
    if file_ts is not None:
        candidates.append(file_ts)

    universe_id = udir.name
    run_ts = _latest_run_activity_at(universe_id)
    if run_ts is not None:
        candidates.append(run_ts)

    if not candidates:
        return None
    return max(candidates).isoformat()


def _staleness_bucket(last_activity_iso: str | None) -> str:
    """Classify liveness from a last-activity timestamp.

    Returns one of: "fresh" (<1h), "idle" (<24h), "dormant" (>=24h), or
    "never" (no timestamp recorded). Callers that previously trusted
    `daemon_state: running` from status.json should consult this instead.
    """
    if not last_activity_iso:
        return "never"
    try:
        ts = datetime.fromisoformat(last_activity_iso)
    except (TypeError, ValueError):
        return "never"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age < _STALE_FRESH_SECONDS:
        return "fresh"
    if age < _STALE_IDLE_SECONDS:
        return "idle"
    return "dormant"


def _phase_human(
    raw_phase: str | None,
    has_premise: bool,
    has_work: bool,
    is_paused: bool,
    staleness: str,
) -> str:
    """Translate raw daemon state into a legible phase for downstream readers.

    Precedence: paused > dormant-no-premise > dormant > no-premise > starved
    > raw_phase > idle. Chat clients and humans both need a single sentence
    that explains why nothing is happening; the raw phase alone ("unknown",
    "dispatch_execution") is not enough when no premise or no work exists.
    """
    if is_paused:
        return "paused"
    if staleness == "dormant":
        if not has_premise:
            return "dormant-no-premise"
        if not has_work:
            return "dormant-starved"
        return "dormant"
    if not has_premise:
        return "idle-no-premise"
    if not has_work:
        return "starved"
    if raw_phase and raw_phase not in ("", "unknown"):
        return raw_phase
    return "idle"


def _compute_accept_rate_from_db(
    udir: Path,
) -> tuple[float | None, dict[str, Any]]:
    """Derive accept_rate directly from scene_history.

    Returns (rate, sample) where rate is None when no evaluated scenes exist,
    and sample carries the raw counts so downstream readers can tell the
    difference between "0% accepted" and "nothing evaluated yet". This is
    deliberately read-time — status.json's cached `accept_rate` is never
    updated by the daemon today, so reading it is misleading.
    """
    db_path = udir / "story.db"
    sample: dict[str, Any] = {"accepted": 0, "evaluated": 0, "source": "none"}
    if not db_path.exists():
        return None, sample

    try:
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='scene_history'",
            ).fetchone()
            if not row:
                return None, sample
            # Only scenes that have been judged count toward the rate.
            # `pending` means not yet evaluated — not a reject.
            evaluated = conn.execute(
                "SELECT COUNT(*) FROM scene_history "
                "WHERE verdict IS NOT NULL AND verdict != '' "
                "AND verdict != 'pending'",
            ).fetchone()[0]
            accepted = conn.execute(
                "SELECT COUNT(*) FROM scene_history "
                "WHERE verdict IN ('accept', 'second_draft')",
            ).fetchone()[0]
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("Failed to read scene_history from %s: %s", db_path, exc)
        return None, sample

    sample = {"accepted": accepted, "evaluated": evaluated, "source": "scene_history"}
    if evaluated == 0:
        return None, sample
    return accepted / evaluated, sample


def _compute_word_count_from_files(
    udir: Path,
) -> tuple[int, dict[str, Any]]:
    """Derive word_count by reading prose files on disk.

    Returns (total_words, sample). `status.json::word_count` is a cached
    value maintained by `DashboardMetrics` in the daemon process — it's
    only flushed during runs, never corrected when output files are
    added/removed/moved, and can drift wildly across command center switches.
    Reading the files is slower but always truthful.

    The walk covers `output/**/*.md` under the command center directory, which
    matches where commit writes prose (`output/book-{N}/chapter-{NN}/
    scene-{NN}.md`). Non-scene markdown (canon/, INDEX.md, progress.md)
    lives outside `output/` so it won't be double-counted.

    Returns `(0, {"source": "none"})` when there's no output directory
    yet — callers should treat that distinctly from "0 words written".
    """
    out_dir = udir / "output"
    sample: dict[str, Any] = {"scenes": 0, "source": "none"}
    if not out_dir.is_dir():
        return 0, sample

    total = 0
    scenes = 0
    try:
        for path in out_dir.rglob("*.md"):
            if not path.is_file():
                continue
            name = path.name
            # Skip index files; only prose counts. Scene files are
            # scene-*.md; chapter-*.md is a chapter-level wrapper we don't
            # want to double-count if both exist. Count anything under
            # output/ that looks like prose.
            if name.startswith(".") or name in {"INDEX.md", "progress.md"}:
                continue
            try:
                raw_md = read_data_path(path, max_bytes=MAX_PLATFORM_FILE_BYTES)
                text = raw_md.decode("utf-8", "replace") if raw_md else ""
            except OSError:
                continue
            total += len(text.split())
            if name.startswith("scene-"):
                scenes += 1
    except OSError as exc:
        logger.debug("word_count walk failed at %s: %s", out_dir, exc)
        return 0, {"scenes": 0, "source": "error"}

    sample = {"scenes": scenes, "source": "output_files"}
    return total, sample


def _daemon_liveness(udir: Path, status: dict[str, Any] | None) -> dict[str, Any]:
    """Build the shared liveness block used by list, inspect, and status.

    Centralizing this shape is the point — every reader surface gets the
    same interpreted fields, so legibility fixes in one place land
    everywhere at once.
    """
    has_premise = bool(
        read_legacy_premise(udir).strip() or premise_from_soul(udir).strip()
    )
    targets = _read_json(udir / "work_targets.json")
    has_work = isinstance(targets, list) and any(
        t.get("lifecycle") == "active" for t in targets if isinstance(t, dict)
    )
    is_paused = (udir / ".pause").exists()
    last_activity = _last_activity_at(udir, status)
    staleness = _staleness_bucket(last_activity)

    raw_phase: str | None = None
    if status and isinstance(status, dict):
        # status.json uses `current_phase`; older callers wrote `phase`.
        # Accept both for defense in depth, prefer the canonical name.
        raw_phase = status.get("current_phase") or status.get("phase")

    accept_rate, accept_sample = _compute_accept_rate_from_db(udir)
    # word_count comes from prose on disk, NOT status.json — the cached
    # value there is a DashboardMetrics snapshot that drifts across
    # universe switches. Reading files is slower but truthful.
    word_count, word_count_sample = _compute_word_count_from_files(udir)

    return {
        "phase": raw_phase or "offline",
        "phase_human": _phase_human(
            raw_phase, has_premise, has_work, is_paused, staleness,
        ),
        "is_paused": is_paused,
        "has_premise": has_premise,
        "has_soul": has_soul(udir),
        "has_work": has_work,
        "last_activity_at": last_activity,
        "staleness": staleness,
        "worker_liveness": _worker_liveness(udir),
        "word_count": word_count,
        "word_count_sample": word_count_sample,
        "accept_rate": accept_rate,
        "accept_rate_sample": accept_sample,
    }


_WORKER_SUPERVISOR_FILENAME = ".worker_supervisor.json"
_WORKER_SUPERVISOR_PREFIX = ".worker_supervisor."
_WORKER_SUPERVISOR_SUFFIX = ".json"
_WORKER_QUEUE_DESCRIPTOR_FIELDS = (
    "queue_protocol_version",
    "capabilities",
    "boot_id",
    "build_sha",
    "config_hash",
    "universe_id",
    "expires_at",
)


def _worker_id_from_heartbeat_path(path: Path) -> str:
    name = path.name
    if (
        name.startswith(_WORKER_SUPERVISOR_PREFIX)
        and name.endswith(_WORKER_SUPERVISOR_SUFFIX)
    ):
        return name[
            len(_WORKER_SUPERVISOR_PREFIX):-len(_WORKER_SUPERVISOR_SUFFIX)
        ]
    return ""


def _read_worker_liveness_entry(
    beat_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    worker_id = _worker_id_from_heartbeat_path(beat_path)
    try:
        raw_beat = read_data_path(beat_path, max_bytes=MAX_CONFIG_BYTES) or b""
        beat = json.loads(raw_beat.decode("utf-8"))
    except (OSError, ValueError, TypeError):  # noqa: BLE001 — probe, not gate
        return {
            "present": True,
            "parse_error": True,
            "worker_id": worker_id,
            "runtime_instance_id": "",
        }

    worker_id = str(beat.get("worker_id") or worker_id)
    runtime_instance_id = str(beat.get("runtime_instance_id") or "")
    try:
        ts = datetime.strptime(
            str(beat.get("ts", "")), "%Y-%m-%dT%H:%M:%SZ",
        ).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):  # noqa: BLE001 — probe, not gate
        return {
            "present": True,
            "parse_error": True,
            "worker_id": worker_id,
            "runtime_instance_id": runtime_instance_id,
        }
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    age_s = max(
        0.0,
        (observed_at.astimezone(timezone.utc) - ts).total_seconds(),
    )
    planned_sleep = float(beat.get("planned_sleep_s") or 0.0)
    allowed = max(300.0, planned_sleep + 120.0)
    result = {
        "present": True,
        "alive": age_s <= allowed,
        "beat_age_s": round(age_s, 1),
        "phase": beat.get("phase", ""),
        "subprocess_alive": bool(beat.get("subprocess_alive", False)),
        "consec_crashes": beat.get("consec_crashes", 0),
        "total_spawns": beat.get("total_spawns", 0),
        "last_exit_rc": beat.get("last_exit_rc"),
        "worker_id": worker_id,
        "runtime_instance_id": runtime_instance_id,
    }
    for field in _WORKER_QUEUE_DESCRIPTOR_FIELDS:
        if field in beat:
            result[field] = beat[field]
    return result


def _worker_liveness(
    udir: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Supervisor-heartbeat liveness, distinct from content activity.

    ``last_activity_at`` answers "when did the daemon last DO something"
    (newest of activity.log / .runtime_status.json mtimes, or a run that
    actually started in the runs ledger) — it goes stale both when the
    worker is wedged AND when there is simply nothing to do.
    This field answers "is the worker process alive right now" from the
    ``.worker_supervisor.json`` beat the served `AssignedQueueConsumer`
    writes (docs/specs/daemon-liveness-watchdog.md). Consumers (the activity
    canary) use it to page on wedge and stay quiet on idle.
    """
    legacy_path = udir / _WORKER_SUPERVISOR_FILENAME
    worker_paths = sorted(
        path for path in udir.glob(
            f"{_WORKER_SUPERVISOR_PREFIX}*{_WORKER_SUPERVISOR_SUFFIX}"
        )
        if path.name != _WORKER_SUPERVISOR_FILENAME
    )
    if not worker_paths and legacy_path.exists():
        worker_paths = [legacy_path]
    if not worker_paths:
        return {"present": False}

    workers = [
        _read_worker_liveness_entry(path, now=now)
        for path in worker_paths
    ]
    if legacy_path.exists():
        summary = _read_worker_liveness_entry(legacy_path, now=now)
    else:
        summary = min(
            workers,
            key=lambda entry: float(entry.get("beat_age_s", float("inf"))),
        )
    out = dict(summary)
    out["workers"] = workers
    out["worker_count"] = len(workers)
    out["runtime_instance_count"] = len({
        str(worker.get("runtime_instance_id") or "")
        for worker in workers
        if worker.get("runtime_instance_id")
    })
    return out


def _classify_epoch2_workers(
    udir: Path,
    *,
    now: datetime | None = None,
    trusted_descriptors: dict[str, dict[str, Any] | None] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Return compatible workers plus why every other worker was rejected.

    The accept decision is unchanged; the second element only records which
    gate turned each worker away. Before this existed, ten live workers could
    yield ``compatible_worker_count: 0`` with no way to tell which of the
    admission gates was responsible, and production sat blocked for 22.5h on
    a cause no read-only surface could name.

    Attribution is first-failure-wins, following the original short-circuit
    order, so the counts sum to the number of rejected workers.
    """
    from tinyassets.branch_tasks_v2 import (
        WorkerClaimDescriptor,
        _descriptor_is_live,
    )

    rejected: dict[str, int] = {}
    mismatch_fields: set[str] = set()

    def reject(gate: str) -> None:
        rejected[gate] = rejected.get(gate, 0) + 1

    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    observed_at = observed_at.astimezone(timezone.utc)
    runtime_by_id: dict[str, dict[str, Any]] = {}
    if trusted_descriptors is None:
        from tinyassets.daemon_registry import list_runtime_instances

        runtimes = list_runtime_instances(
            udir.parent,
            universe_id=udir.name,
        )
        runtime_by_id = {
            str(runtime.get("runtime_instance_id") or ""): runtime
            for runtime in runtimes
            if runtime.get("status") == "provisioned"
        }
        trusted_descriptors = {
            runtime_id: (
                runtime.get("metadata", {}).get(
                    "queue_protocol_descriptor"
                )
            )
            for runtime_id, runtime in runtime_by_id.items()
        }
    else:
        runtime_by_id = {
            runtime_id: {
                "runtime_instance_id": runtime_id,
                "daemon_id": "",
                "provider_name": "",
                "model_name": "",
            }
            for runtime_id in trusted_descriptors
        }
    workers = _worker_liveness(udir, now=observed_at).get("workers", [])
    compatible: list[dict[str, str]] = []
    for worker in workers:
        capabilities = worker.get("capabilities")
        if not worker.get("alive"):
            reject("beat_not_alive")
            continue
        if not worker.get("subprocess_alive"):
            reject("subprocess_not_alive")
            continue
        if not isinstance(capabilities, (list, tuple, set, frozenset)):
            reject("capabilities_not_a_collection")
            continue
        try:
            descriptor = WorkerClaimDescriptor(
                queue_protocol_version=int(
                    worker.get("queue_protocol_version")
                ),
                capabilities=frozenset(str(item) for item in capabilities),
                worker_id=str(worker.get("worker_id") or ""),
                runtime_instance_id=str(
                    worker.get("runtime_instance_id") or ""
                ),
                boot_id=str(worker.get("boot_id") or ""),
                build_sha=str(worker.get("build_sha") or ""),
                config_hash=str(worker.get("config_hash") or ""),
                universe_id=str(worker.get("universe_id") or ""),
                expires_at=str(worker.get("expires_at") or ""),
            )
        except (TypeError, ValueError):
            reject("descriptor_malformed")
            continue
        heartbeat_descriptor = {
            "queue_protocol_version": descriptor.queue_protocol_version,
            "capabilities": sorted(descriptor.capabilities),
            "worker_id": descriptor.worker_id,
            "runtime_instance_id": descriptor.runtime_instance_id,
            "boot_id": descriptor.boot_id,
            "build_sha": descriptor.build_sha,
            "config_hash": descriptor.config_hash,
            "universe_id": descriptor.universe_id,
            "expires_at": descriptor.expires_at,
        }
        trusted = trusted_descriptors.get(descriptor.runtime_instance_id)
        runtime = runtime_by_id.get(descriptor.runtime_instance_id)
        if descriptor.universe_id != udir.name:
            reject("universe_id_mismatch")
            continue
        if runtime is None:
            reject("no_provisioned_runtime_row")
            continue
        if trusted != heartbeat_descriptor:
            if trusted is None:
                reject("descriptor_never_published")
            else:
                reject("descriptor_not_trusted")
                # Field NAMES only — never the values, which carry
                # build/config identity.
                if isinstance(trusted, dict):
                    mismatch_fields.update(
                        key
                        for key in set(trusted) | set(heartbeat_descriptor)
                        if trusted.get(key) != heartbeat_descriptor.get(key)
                    )
            continue
        if not _descriptor_is_live(
            descriptor,
            transaction_at=observed_at.isoformat(),
        ):
            reject("descriptor_lease_not_live")
            continue
        compatible.append({
            "worker_id": descriptor.worker_id,
            "runtime_instance_id": descriptor.runtime_instance_id,
            "daemon_id": str(runtime.get("daemon_id") or ""),
            "provider_name": str(runtime.get("provider_name") or ""),
            "model_name": str(runtime.get("model_name") or ""),
        })
    evidence: dict[str, Any] = {
        "observed_worker_beats": len(workers),
        "provisioned_runtime_count": len(runtime_by_id),
        "rejected": dict(sorted(rejected.items())),
    }
    if mismatch_fields:
        evidence["descriptor_mismatch_fields"] = sorted(mismatch_fields)
    return (
        sorted(
            compatible,
            key=lambda worker: (
                worker["worker_id"],
                worker["runtime_instance_id"],
            ),
        ),
        evidence,
    )


def _compatible_epoch2_workers(
    udir: Path,
    *,
    now: datetime | None = None,
    trusted_descriptors: dict[str, dict[str, Any] | None] | None = None,
) -> list[dict[str, str]]:
    """Return workers with live, complete, universe-bound v2 evidence."""
    workers, _ = _classify_epoch2_workers(
        udir,
        now=now,
        trusted_descriptors=trusted_descriptors,
    )
    return workers


def _compatible_epoch2_worker_ids(
    udir: Path,
    *,
    now: datetime | None = None,
    trusted_descriptors: dict[str, dict[str, Any] | None] | None = None,
) -> list[str]:
    """Compatibility helper for liveness tests and concise status callers."""
    try:
        workers = _compatible_epoch2_workers(
            udir,
            now=now,
            trusted_descriptors=trusted_descriptors,
        )
    except Exception:  # noqa: BLE001 — missing trust means no capacity
        return []
    return sorted({worker["worker_id"] for worker in workers})


def _unavailable_epoch2_summary(error: str) -> dict[str, Any]:
    return {
        "available": False,
        "queue_epoch": 2,
        "error": error,
        "depth": None,
        "lifecycle_counts": None,
        "lifecycle_oldest_age_s": None,
        "unknown_lifecycle_status_counts": None,
        "operational_state_counts": None,
        "operational_oldest_age_s": None,
        "operational_reason_counts": None,
        "valid_pending_count": None,
        "eligible_pending_count": None,
        "operational_counts_authoritative": False,
        "integrity_scope_complete": None,
        "unclassified_active_count": None,
        "active_scan_limit": None,
        "diagnostics": [],
        "diagnostics_truncated": False,
        "compatible_worker_count": None,
        "capacity_evidence_available": False,
        "consumer_ready": False,
    }


def _may_view_unscoped_epoch2_integrity(udir: Path) -> bool:
    """Restrict exact unscoped corruption counts to command center admins."""
    actor_id = permissions.current_actor_id()
    from tinyassets.principals import has_named_principal

    if not has_named_principal(actor_id):
        return False
    try:
        from tinyassets.daemon_server import universe_access_permission

        return universe_access_permission(
            udir.parent,
            universe_id=udir.name,
            actor_id=actor_id,
        ) == "admin"
    except Exception:  # noqa: BLE001 - observability auth fails closed
        return False


def _epoch2_operational_read(
    udir: Path,
    *,
    dispatcher_config: Any | None = None,
):
    """Read counts and safe candidates from one bounded SQLite snapshot."""
    from tinyassets.branch_tasks_v2 import (
        EPOCH2_QUEUE_CONSUMER_READY,
        Epoch2BranchTaskAdapter,
        Epoch2OperationalRead,
    )
    from tinyassets.dispatcher import (
        load_dispatcher_config,
        prefers_request_type,
    )
    from tinyassets.storage import DB_FILENAME

    base_path = udir.parent
    database = base_path / DB_FILENAME
    if not database.is_file():
        return Epoch2OperationalRead(
            summary=_unavailable_epoch2_summary(
                "epoch2_store_unavailable"
            ),
            candidates=(),
        )
    cfg = dispatcher_config or load_dispatcher_config(udir)
    capacity_error = ""
    consumer_ready = EPOCH2_QUEUE_CONSUMER_READY is True
    capacity_evidence: dict[str, Any] = {}
    if not consumer_ready:
        workers = []
        capacity_error = "epoch2_consumer_not_ready"
    else:
        try:
            workers, capacity_evidence = _classify_epoch2_workers(udir)
        except Exception as exc:  # noqa: BLE001 — surface trust-read failure
            workers = []
            capacity_error = str(exc)

    def capacity_matches(task) -> bool:
        if (
            task.required_llm_type
            and cfg.served_llm_type
            and task.required_llm_type != cfg.served_llm_type
        ):
            return False
        if not prefers_request_type(task.request_type):
            return False
        if task.directed_daemon_id:
            return any(
                worker["daemon_id"] == task.directed_daemon_id
                for worker in workers
            )
        return bool(workers)

    try:
        from tinyassets.runtime.assigned_queue_consumer import (
            assigned_queue_refusal_freshness_seconds,
        )
        from tinyassets.storage.assigned_queue_refusals import (
            AssignedQueueRefusalStore,
        )

        refusals = AssignedQueueRefusalStore(base_path).fresh_reasons(
            universe_id=udir.name,
            max_age_seconds=assigned_queue_refusal_freshness_seconds(),
        )
        result = Epoch2BranchTaskAdapter(
            base_path,
        ).operational_read(
            universe_id=udir.name,
            capacity_matcher=capacity_matches,
            policy_matcher=lambda task: cfg.tier_enabled(
                task.trigger_source
            ),
            authority_refusal_matcher=lambda task: refusals.get(
                task.branch_task_id, ""
            ),
            include_unscoped_invalid=(
                _may_view_unscoped_epoch2_integrity(udir)
            ),
        )
    except Exception as exc:  # noqa: BLE001 — preserve epoch-1 reads
        return Epoch2OperationalRead(
            summary=_unavailable_epoch2_summary(str(exc)),
            candidates=(),
        )
    result.summary["compatible_worker_count"] = len(workers)
    result.summary["consumer_ready"] = consumer_ready
    # Why the consumer is not producing work for this universe right now:
    # per-automation preconditions (provider_mismatch:..., no_prepared_continuation)
    # and per-principal pump outcomes (no_daemon_for_principal, produce_error:...).
    # Each machine reason ships with the plain-language next step, because these
    # strings are what the served agent relays to the user (cold desktop test
    # 2026-08-25: it relayed provider_mismatch and then offered to investigate).
    from tinyassets.consumer_reason_actions import consumer_next_action

    result.summary["consumer_pump"] = [
        {"key": key, "reason": reason, "next_action": consumer_next_action(reason)}
        for key, reason in sorted(refusals.items())
        if key.startswith(("automation:", "universe:")) and not reason.startswith("ok:")
    ]
    result.summary["capacity_evidence_available"] = not capacity_error
    # Name the admission gate when capacity is zero; a bare 0 is unactionable.
    if capacity_evidence and not workers:
        result.summary["capacity_rejections"] = capacity_evidence
    if capacity_error:
        result.summary["operational_counts_authoritative"] = False
        result.summary["capacity_evidence_error"] = capacity_error
    return result


def _epoch2_operational_snapshot(udir: Path) -> dict[str, Any]:
    return _epoch2_operational_read(udir).summary


def _is_listable_universe_dir(path: Path, owned: set[str]) -> bool:
    """A command center is a directory somebody OWNS (founder, 2026-09-02).

    This used to be a four-name denylist (``lance``/``output``/``runs``/``wiki``)
    standing in for a definition, so the platform's own backups and every past
    prune's archive were command centers, and each new operational directory needed
    another name in the frozenset -- ``lancedb``, daemon memory, retained inputs,
    the workspace pool and stored offers were already missing from it. Ownership
    is the definition; operational directories need no list because they were
    never command centers.

    ``owned`` comes from ``daemon_server.owned_universe_ids``. Passing it in
    rather than reading it here keeps one ownership query per enumeration
    instead of one per directory.
    """
    return (
        path.is_dir()
        and not path.name.startswith(".")
        # EXACT, not case-folded. A universe id is both a path component and an
        # authority key, and resolving those to different spellings breaks one of
        # them -- see `daemon_server.owned_universe_id` for the two ways it broke.
        and path.name in owned
    )


def _action_list_universes(**_kwargs: Any) -> str:
    base = _base_path()
    if not base.is_dir():
        return json.dumps({
            "universes": [],
            "count": 0,
            "note": f"Base directory does not exist: {base}",
        })

    from tinyassets.api import visibility
    from tinyassets.daemon_server import owned_universe_ids

    # Ownership first: reading it initializes its store in this directory, so
    # entries listed before it described a directory the call then changed --
    # the first list on a fresh data dir said "empty" and the second did not.
    try:
        owned = owned_universe_ids(base)
    except Exception as exc:  # noqa: BLE001 - fail closed, and say why
        logger.exception("ownership lookup failed while listing command centers")
        return json.dumps({
            "universes": [],
            "count": 0,
            "note": f"Ownership store unavailable: {exc}",
        })

    try:
        all_entries = list(base.iterdir())
    except OSError as exc:
        return json.dumps({
            "universes": [],
            "count": 0,
            "note": f"Base directory unreadable ({base}): {exc}",
        })

    universes = []
    hidden_by_visibility = 0
    for child in sorted(all_entries):
        if not _is_listable_universe_dir(child, owned):
            continue
        # Existence is a privileged, separately-granted capability: a universe
        # whose declared level withholds discovery (e.g. `unlisted`) is not
        # enumerated even though its content may be readable by direct id.
        if not visibility.visibility_permits(child.name, "discover_existence"):
            hidden_by_visibility += 1
            continue
        status = _read_json(child / "status.json")
        liveness = _daemon_liveness(child, status if isinstance(status, dict) else None)
        info: dict[str, Any] = {
            "id": child.name,
            "visibility": visibility.declared_level_name(child.name),
            "has_premise": liveness["has_premise"],
            "has_soul": liveness["has_soul"],
            "word_count": liveness["word_count"],
            "phase": liveness["phase"],
            "phase_human": liveness["phase_human"],
            "staleness": liveness["staleness"],
            "last_activity_at": liveness["last_activity_at"],
            "accept_rate": liveness["accept_rate"],
        }
        universes.append(info)

    result: dict[str, Any] = {"universes": universes, "count": len(universes)}
    if not universes:
        if hidden_by_visibility:
            # Some universes exist but none are visible to this caller. Do NOT
            # leak the hidden count or the base path — that is aggregate
            # disclosure about withheld universes (existence is privileged).
            result["note"] = "No command centers are visible to you."
        elif not all_entries:
            result["note"] = f"Base directory is empty: {base}"
        else:
            result["note"] = (
                f"Base directory has {len(all_entries)} entries but none "
                f"are valid command centers (all hidden or non-directories or "
                f"reserved operational data directories): {base}"
            )
    return json.dumps(result)


class _OwnershipUnavailable(RuntimeError):
    """The ownership store could not be read. NOT the same as unowned."""


def _owned_universe_id(uid: str) -> str:
    """The owned id ``uid`` names, or ``""`` when nobody owns it.

    Raises :class:`_OwnershipUnavailable` when the store cannot be read.
    Returning ``""`` there would refuse the request as "Command center not found",
    which tells the caller an existing command center does not exist -- a lie, from a
    transient SQLite lock. Fail closed AND loudly: the request is still refused,
    but for the reason that is true.
    """
    from tinyassets.daemon_server import owned_universe_id

    base = _base_path()
    if not base.is_dir():
        # No data root is not a broken store: there is nothing here, so nobody
        # owns anything. Raising here would turn every read on a fresh install
        # into "Ownership store unavailable".
        return ""
    try:
        return owned_universe_id(base, uid)
    except Exception as exc:  # noqa: BLE001
        logger.exception("ownership lookup failed for %s", uid)
        raise _OwnershipUnavailable(str(exc)) from exc


def _available_universe_ids() -> list[str]:
    """What to offer when an id is not found: the command centers SOMEBODY OWNS.

    This used to list every directory under the data root, so a "not found"
    answer published the whole graveyard -- the archives, the migration backup
    and the operational stores -- to any caller who guessed a wrong id.
    """
    from tinyassets.daemon_server import owned_universe_ids

    base = _base_path()
    if not base.is_dir():
        return []
    try:
        owned = owned_universe_ids(base)
    except Exception:  # noqa: BLE001 - fail closed
        logger.exception("ownership lookup failed while listing available ids")
        return []
    from tinyassets.api import visibility

    return sorted(
        d.name for d in base.iterdir()
        if _is_listable_universe_dir(d, owned)
        # Existence is separately granted. Without this, asking for an id that
        # does not exist answers with every owned universe, private and unlisted
        # ones included -- the enumeration gate the listing applies, skipped by
        # taking the error path.
        and visibility.visibility_permits(d.name, "discover_existence")
    )


def _action_inspect_universe(universe_id: str = "", **_kwargs: Any) -> str:
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)

    # A DIRECTORY IS NOT A UNIVERSE. Filtering the enumeration was half the fix:
    # reading one BY ID still answered with a full universe payload for
    # `cloud-automation-inputs` and for the migration backup, reproduced against
    # production on 2026-09-02. The graveyard was still browsable, which is what
    # the founder reported.
    try:
        owner_id = _owned_universe_id(uid)
    except _OwnershipUnavailable as exc:
        return json.dumps({"error": f"Ownership store unavailable: {exc}"})
    if not udir.is_dir() or not owner_id:
        return json.dumps({
            "error": f"Command center '{uid}' not found.",
            "available": _available_universe_ids(),
        })

    # Metadata gate: inspect returns describe-surface metadata (premise, daemon
    # phase/counts, notes, targets, file listings), so an EXISTING universe must
    # be gated on the `read_metadata` capability — not merely the legacy read
    # gate. A content-only (`unlisted`) universe sets public_read=True to keep
    # content readable, which the legacy preflight allows; without this gate an
    # unbound caller would read its metadata even though the level withholds it.
    from tinyassets.api import permissions, visibility

    if not visibility.visibility_permits(uid, "read_metadata"):
        return json.dumps(permissions.universe_access_error(
            universe_id=uid, write=False, action="inspect", surface="universe",
        ))

    result: dict[str, Any] = {"universe_id": uid}

    # Declared visibility is observable to a permitted reader (spec Req 4): the
    # boundary is stated, not inferred from its absence.
    result["visibility"] = visibility.declared_level_name(uid)

    # Daemon liveness block — always present, so downstream readers (humans
    # and chat clients) can always tell whether the daemon is alive, why
    # it's stuck, and whether the premise and work exist.
    status = _read_json(udir / "status.json")
    liveness = _daemon_liveness(udir, status if isinstance(status, dict) else None)
    result["daemon"] = {
        "phase": liveness["phase"],
        "phase_human": liveness["phase_human"],
        "is_paused": liveness["is_paused"],
        "has_premise": liveness["has_premise"],
        "has_soul": liveness["has_soul"],
        "has_work": liveness["has_work"],
        "last_activity_at": liveness["last_activity_at"],
        "staleness": liveness["staleness"],
        "word_count": liveness["word_count"],
        "word_count_sample": liveness["word_count_sample"],
        "accept_rate": liveness["accept_rate"],
        "accept_rate_sample": liveness["accept_rate_sample"],
    }

    soul = read_universe_soul(udir)
    if soul is not None:
        result["has_soul"] = True
        result["soul"] = soul.summary()

    # Premise remains the public compatibility field. The durable source is
    # now soul.md when PROGRAM.md is absent or empty.
    program = _normalize_escaped_text(read_legacy_premise(udir))
    premise = program.strip() or (soul.purpose if soul is not None else "")
    result["has_premise"] = bool(premise)
    if premise:
        result["premise"] = premise[:500] + ("..." if len(premise) > 500 else "")
        result["premise_source"] = (
            "PROGRAM.md" if program.strip() else SOUL_FILENAME
        )

    # Notes summary
    notes = _read_json(udir / "notes.json")
    if notes and isinstance(notes, list):
        recent = notes[-5:]
        result["recent_notes"] = [
            {
                "source": n.get("source"),
                "category": n.get("category"),
                "text": n.get("text", "")[:200],
                "timestamp": n.get("timestamp"),
            }
            for n in recent
        ]

    # Work targets
    targets = _read_json(udir / "work_targets.json")
    if targets and isinstance(targets, list):
        active = [t for t in targets if t.get("lifecycle") == "active"][:5]
        result["active_targets"] = [
            {
                "id": t.get("target_id"),
                "title": t.get("title"),
                "role": t.get("role"),
                "intent": t.get("current_intent"),
            }
            for t in active
        ]

    # Output files
    output_dir = udir / "output"
    if output_dir.is_dir():
        result["output_files"] = _list_output_tree(output_dir)

    # Activity tail
    activity = _read_text(udir / "activity.log")
    if activity:
        lines = activity.strip().splitlines()
        result["recent_activity"] = lines[-10:]

    # Pending requests
    from tinyassets.work_targets import REQUESTS_FILENAME
    requests = _read_json(udir / REQUESTS_FILENAME)
    if requests and isinstance(requests, list):
        pending = [r for r in requests if r.get("status") == "pending"]
        if pending:
            result["pending_requests"] = len(pending)

    # Cross-surface hint — helps chatbots discover cross-domain work even
    # when the active universe is themed (e.g. a particular novel or
    # standup tracker). The workspace is one container; goals, branches,
    # and wiki span all domains.
    result["cross_surface_hint"] = {
        "note": (
            "This workspace is one container; canonical graph and page reads "
            "span domains regardless of this workspace's theme. Global workflow "
            "enumeration is not exposed by the advertised handles."
        ),
        "paths": [
            {
                "action": 'read_graph target="branch" branch_id="<known id>"',
                "purpose": "Inspect a known workflow by identifier",
            },
            {
                "action": 'read_graph target="goals"',
                "purpose": (
                    "Domain-agnostic intents "
                    "(research, software, science, fantasy, etc.)"
                ),
            },
            {
                "action": 'read_page query="<terms>"',
                "purpose": "Cross-domain notes, bugs, and design plans",
            },
            {
                "action": 'read_graph target="graphs"',
                "purpose": "Other workspaces if multiple exist",
            },
        ],
    }

    return json.dumps(result, default=str)


def _list_output_tree(output_dir: Path, max_depth: int = 3) -> list[str]:
    """Walk the output directory and return relative paths."""
    files = []
    for root, dirs, filenames in os.walk(output_dir):
        depth = len(Path(root).relative_to(output_dir).parts)
        if depth >= max_depth:
            dirs.clear()
            continue
        for f in sorted(filenames):
            rel = Path(root, f).relative_to(output_dir)
            if not f.startswith("."):
                files.append(str(rel))
    return files[:50]


def _action_read_output(universe_id: str = "", path: str = "", **_kwargs: Any) -> str:
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    # Never resolve: a workflow provider jail can plant ``output -> /data/<B>/``,
    # and a resolved target would then be "inside" the resolved output dir.
    # The relative path is checked lexically and read with no link followed.
    parts = path.replace("\\", "/").split("/")
    if not path or path.startswith("/") or any(p in ("", ".", "..") for p in parts):
        return json.dumps({"error": "Path traversal not allowed."})

    from tinyassets.universe_files import read_universe_text

    try:
        content = read_universe_text(
            udir, "/".join(["output", *parts]), max_bytes=MAX_PLATFORM_FILE_BYTES,
        )
    except FileNotFoundError:
        return json.dumps({"error": f"File not found: {path}"})
    except (OSError, UnicodeDecodeError) as exc:
        return json.dumps({"error": f"Output file {path!r} was not read: {exc}"})
    if len(content) > 10000:
        return json.dumps({
            "universe_id": uid,
            "path": path,
            "content": content[:10000],
            "truncated": True,
            "total_chars": len(content),
            "note": "File truncated to 10K chars. Request specific sections if needed.",
        })
    return json.dumps({
        "universe_id": uid,
        "path": path,
        "content": content,
        "truncated": False,
    })


def _lookup_operator_request_replay(
    store: Any,
    *,
    universe_id: str,
    idempotency_key_hash: str,
    body_digest: str,
    body_digest_version: str,
) -> dict[str, Any] | None:
    """Reauthorize ordinary access before consulting idempotency state."""

    verdict = permissions.operator_request_replay_verdict(universe_id)
    if not verdict.allowed:
        # Reveal no stored identifier, digest, receipt, replay status, or
        # key-existence evidence.
        return {"error": "universe_access_denied"}
    access_check, _priority_check = (
        permissions.operator_request_transaction_checks(verdict)
    )
    return store.lookup_replay(
        tenant_id=verdict.tenant_id,
        actor_id=verdict.actor_id,
        universe_id=verdict.universe_id,
        idempotency_key_hash=idempotency_key_hash,
        body_digest=body_digest,
        body_digest_version=body_digest_version,
        access_check=access_check,
    )


_REQUEST_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_REQUEST_BODY_DIGEST_VERSION = "rfc8785-v1"
_REQUEST_BODY_SCHEMA_VERSION = "request-admission-v2"
_REQUEST_HMAC_ENV = "TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY"
_REQUEST_TYPES = frozenset({
    "scene_direction",
    "revision",
    "canon_change",
    "branch_proposal",
    "general",
})


def _request_validation_error() -> str:
    return json.dumps({"error": "request_validation_error"})


def _request_idempotency_key_hash(raw_key: str) -> str:
    # `_REQUEST_IDEMPOTENCY_KEY_RE` already restricts the key to ASCII, so
    # delegating to the shared minter is byte-identical to the old
    # ascii-encoded HMAC.
    from tinyassets.storage.request_admissions import (
        mint_idempotency_key_hash,
    )

    return mint_idempotency_key_hash(raw_key)


def _request_body_digest(
    *,
    universe_id: str,
    text: str,
    request_type: str,
    branch_id: str,
    pickup_incentive: str,
    directed_daemon_id: str,
    directed_daemon_instruction: str,
    priority_weight: int | float,
) -> str:
    import rfc8785

    canonical = rfc8785.dumps({
        "branch_id": branch_id,
        "directed_daemon_id": directed_daemon_id,
        "directed_daemon_instruction": directed_daemon_instruction,
        "pickup_incentive": pickup_incentive,
        "priority_weight": priority_weight,
        "request_type": request_type,
        "schema_version": _REQUEST_BODY_SCHEMA_VERSION,
        "text": text,
        "universe_id": universe_id,
    })
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _action_admit_request_v2(
    *,
    idempotency_key: str,
    graph_id: str = "",
    text: str = "",
    request_type: str = "general",
    branch_id: str = "",
    pickup_incentive: str = "",
    directed_daemon_id: str = "",
    directed_daemon_instruction: str = "",
    priority_weight: int | float = 0.0,
) -> str:
    """Validate and atomically admit one canonical protocol-v2 request."""

    string_fields = (
        idempotency_key,
        graph_id,
        text,
        request_type,
        branch_id,
        pickup_incentive,
        directed_daemon_id,
        directed_daemon_instruction,
    )
    if (
        not all(isinstance(value, str) for value in string_fields)
        or _REQUEST_IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key) is None
        or isinstance(priority_weight, bool)
        or not isinstance(priority_weight, (int, float))
        or not 0 <= priority_weight <= 100
        or request_type not in _REQUEST_TYPES
    ):
        return _request_validation_error()
    try:
        encoded_fields = tuple(value.encode("utf-8") for value in string_fields)
    except UnicodeEncodeError:
        return _request_validation_error()
    if len(encoded_fields[2]) > _SUBMIT_REQUEST_MAX_BYTES:
        return _request_validation_error()

    uid = _request_universe(graph_id)
    try:
        idempotency_key_hash = _request_idempotency_key_hash(
            idempotency_key
        )
    except RuntimeError:
        logger.error("request admission HMAC key is not configured")
        return json.dumps({"error": "request_admission_unavailable"})
    body_digest = _request_body_digest(
        universe_id=uid,
        text=text,
        request_type=request_type,
        branch_id=branch_id,
        pickup_incentive=pickup_incentive,
        directed_daemon_id=directed_daemon_id,
        directed_daemon_instruction=directed_daemon_instruction,
        priority_weight=priority_weight,
    )

    from tinyassets.storage.request_admissions import (
        IdempotencyKeyBodyConflict,
        RequestAdmissionStore,
    )

    store = RequestAdmissionStore(_base_path())
    try:
        replay = _lookup_operator_request_replay(
            store,
            universe_id=uid,
            idempotency_key_hash=idempotency_key_hash,
            body_digest=body_digest,
            body_digest_version=_REQUEST_BODY_DIGEST_VERSION,
        )
    except IdempotencyKeyBodyConflict:
        return json.dumps({"error": "idempotency_key_body_conflict"})
    except PermissionError:
        return json.dumps({"error": "universe_access_denied"})
    if replay is not None:
        return json.dumps(replay, default=str)

    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": "universe_not_found"})
    loop_branch_def_id, _loop_dispatch = _universe_loop_dispatch(udir)
    if not loop_branch_def_id:
        return json.dumps({
            "error": "universe_loop_not_declared",
            "universe_id": uid,
        })

    verdict = permissions.operator_request_admission_verdict(
        uid,
        requested_priority_weight=float(priority_weight),
        directed=bool(directed_daemon_id),
    )
    if not verdict.allowed:
        return json.dumps({"error": verdict.error_code})

    directed_receipt: dict[str, Any] = {}
    if directed_daemon_id:
        from tinyassets.daemon_registry import (
            build_requester_directed_daemon_assignment,
        )

        assignment = build_requester_directed_daemon_assignment(
            _base_path(),
            daemon_id=directed_daemon_id,
            requester_id=verdict.actor_id,
            patch_request_id="pending-request-admission",
            instruction=directed_daemon_instruction,
        )
        if assignment.get("effect") == "refused":
            return json.dumps({"error": "directed_daemon_not_authorized"})
        directed_receipt = {
            "daemon_id": str(assignment.get("daemon_id") or ""),
            "daemon_soul_hash": str(
                assignment.get("daemon_soul_hash") or ""
            ),
            "authority_scope": str(
                assignment.get("authority_scope") or ""
            ),
        }

    access_check, priority_check = (
        permissions.operator_request_transaction_checks(verdict)
    )
    receipt = {
        "authority": "request-local",
        "grant_generation": int(verdict.grant_generation or 0),
        "priority_policy_version": verdict.priority_policy_version,
        "directed_assignment": directed_receipt,
    }
    try:
        result = store.commit_admission(
            tenant_id=verdict.tenant_id,
            actor_id=verdict.actor_id,
            universe_id=uid,
            idempotency_key_hash=idempotency_key_hash,
            body_digest=body_digest,
            body_digest_version=_REQUEST_BODY_DIGEST_VERSION,
            request_type=request_type,
            text=text,
            branch_id=branch_id,
            branch_def_id=loop_branch_def_id,
            trigger_source=verdict.trigger_source,
            accepted_priority_weight=verdict.accepted_priority_weight,
            policy_version=verdict.priority_policy_version,
            grant_generation=int(verdict.grant_generation or 0),
            receipt=receipt,
            directed_daemon_id=directed_daemon_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            pickup_incentive=pickup_incentive,
            directed_daemon_instruction=directed_daemon_instruction,
            access_check=access_check,
            authority_check=priority_check,
        )
    except IdempotencyKeyBodyConflict:
        return json.dumps({"error": "idempotency_key_body_conflict"})
    except PermissionError:
        return json.dumps({"error": "universe_access_denied"})
    except Exception as exc:
        from tinyassets.storage.accounts import (
            CapabilityGrantAuthorizationError,
        )

        if isinstance(exc, CapabilityGrantAuthorizationError):
            return json.dumps({"error": "priority_authorization_required"})
        logger.exception("request admission transaction failed")
        return json.dumps({"error": "request_admission_failed"})
    return json.dumps(result, default=str)


def admit_request_v2(
    *,
    idempotency_key: str,
    graph_id: str = "",
    text: str = "",
    request_type: str = "general",
    branch_id: str = "",
    pickup_incentive: str = "",
    directed_daemon_id: str = "",
    directed_daemon_instruction: str = "",
    priority_weight: int | float = 0.0,
) -> str:
    """Public request writer with one mutation-ledger entry per commit."""

    kwargs = {
        "idempotency_key": idempotency_key,
        "graph_id": graph_id,
        "text": text,
        "request_type": request_type,
        "branch_id": branch_id,
        "pickup_incentive": pickup_incentive,
        "directed_daemon_id": directed_daemon_id,
        "directed_daemon_instruction": directed_daemon_instruction,
        "priority_weight": priority_weight,
    }
    return _dispatch_with_ledger(
        "submit_request",
        _action_admit_request_v2,
        kwargs,
        scope_response=False,
    )


_SUBMIT_REQUEST_MAX_BYTES = 8192


def _action_submit_request(
    universe_id: str = "",
    text: str = "",
    request_type: str = "scene_direction",
    branch_id: str = "",
    priority_weight: float = 0.0,
    pickup_incentive: str = "",
    directed_daemon_id: str = "",
    directed_daemon_instruction: str = "",
    **_kwargs: Any,
) -> str:
    from tinyassets.api.market import (
        PATCH_REQUEST_AUTHORITY_BOUNDARY,
        classify_patch_request,
        normalize_patch_request_incentive,
    )
    from tinyassets.branch_tasks import BranchTask, append_task, new_task_id
    from tinyassets.work_targets import REQUESTS_FILENAME

    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})

    loop_branch_def_id, loop_dispatch = _universe_loop_dispatch(udir)
    if not loop_branch_def_id:
        return json.dumps({
            "error": "universe_loop_not_declared",
            "universe_id": uid,
            "loop_dispatch": loop_dispatch,
        })

    # 8 KiB cap keeps requests.json bounded and discourages pasting
    # entire drafts into the request channel (add_canon is the right
    # tool for that). UTF-8 byte length, not char count.
    text_bytes = len(text.encode("utf-8"))
    if text_bytes > _SUBMIT_REQUEST_MAX_BYTES:
        return json.dumps({
            "error": (
                f"Request text exceeds {_SUBMIT_REQUEST_MAX_BYTES} bytes "
                f"({text_bytes} submitted). Summarize or split into "
                "multiple requests. For private long-form material, relay it "
                "to the command center through converse instead."
            ),
        })

    valid_types = {
        "scene_direction", "revision", "canon_change",
        "branch_proposal", "general",
    }
    if request_type not in valid_types:
        request_type = "general"

    # The canonical public surface adds exact JSON-number and [0, 100]
    # validation in its own lane. This legacy writer still rejects negatives,
    # then delegates all identity/ACL/elevation decisions to one request-local
    # verdict. It must never silently demote positive priority.
    try:
        pw = float(priority_weight)
    except (TypeError, ValueError):
        pw = 0.0
    if pw < 0:
        return json.dumps({
            "error": "priority_weight must be >= 0.",
        })
    admission_verdict = permissions.operator_request_admission_verdict(
        uid,
        requested_priority_weight=pw,
        directed=bool(directed_daemon_id.strip()),
    )
    if not admission_verdict.allowed:
        return json.dumps({
            "error": admission_verdict.error_code,
            "universe_id": uid,
        })
    if pw > 0:
        # Positive priority is valid only through the not-yet-enabled epoch-2
        # transactional writer. The legacy split writer cannot safely persist
        # it, so fail without mutation instead of stranding or demoting work.
        return json.dumps({
            "error": "operator_priority_unavailable",
            "universe_id": uid,
        })
    source = admission_verdict.actor_id

    request_id = f"req_{int(time.time())}_{os.urandom(4).hex()}"
    incentive = normalize_patch_request_incentive(
        str(pickup_incentive or ""),
        requester_id=source,
    )
    authority_boundary = dict(PATCH_REQUEST_AUTHORITY_BOUNDARY)
    requester_directed_daemon: dict[str, Any] | None = None
    if directed_daemon_id.strip():
        from tinyassets.daemon_registry import (
            build_requester_directed_daemon_assignment,
        )

        requester_directed_daemon = build_requester_directed_daemon_assignment(
            _base_path(),
            daemon_id=directed_daemon_id.strip(),
            requester_id=source,
            patch_request_id=request_id,
            instruction=directed_daemon_instruction or text,
        )
        if requester_directed_daemon.get("effect") == "refused":
            return json.dumps({
                "error": "directed_daemon_not_authorized",
                "requester_directed_daemon": requester_directed_daemon,
            })
    request_classification = classify_patch_request(
        text=text,
        request_type=request_type,
        requester_id=source,
        priority_authorized=False,
        directed_daemon=requester_directed_daemon is not None,
    )
    request_obj = {
        "id": request_id,
        "type": request_type,
        "text": text,
        "branch_id": branch_id or None,
        "status": "pending",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "pickup_incentive": incentive,
        "authority_boundary": authority_boundary,
        "request_classification": request_classification,
        "loop_dispatch": loop_dispatch,
    }
    if requester_directed_daemon is not None:
        request_obj["requester_directed_daemon"] = requester_directed_daemon

    requests_path = udir / REQUESTS_FILENAME
    existing = _read_json(requests_path)
    if not isinstance(existing, list):
        existing = []
    existing.append(request_obj)

    try:
        _ensure_center_dir(udir)
        write_data_path(requests_path, json.dumps(existing, indent=2, default=str))
    except OSError as exc:
        return json.dumps({"error": f"Failed to write request: {exc}"})

    # Phase E: also queue a BranchTask so the dispatcher can score +
    # schedule. host submissions land as host_request tier; anyone
    # else lands as user_request. The WorkTarget still gets
    # materialized by UserRequestProducer from requests.json on the
    # next producer cycle — BranchTask wraps the execution intent.
    branch_task_id = ""
    try:
        task = BranchTask(
            branch_task_id=new_task_id(),
            branch_def_id=loop_branch_def_id,
            universe_id=uid,
            inputs={
                "work_target_ref": None,
                "request_id": request_id,
                "request_type": request_type,
                "branch_id": branch_id or "",
                "pickup_incentive": incentive,
                "authority_boundary": authority_boundary,
                "request_classification": request_classification,
                "requester_directed_daemon": requester_directed_daemon,
                "loop_dispatch": loop_dispatch,
            },
            trigger_source=(
                "owner_queued"
                if requester_directed_daemon is not None
                else admission_verdict.trigger_source
            ),
            priority_weight=admission_verdict.accepted_priority_weight,
            pickup_signal_weight=float(incentive.get("pickup_signal_weight") or 0.0),
            directed_daemon_id=(
                str(requester_directed_daemon.get("daemon_id", ""))
                if requester_directed_daemon is not None else ""
            ),
        )
        append_task(udir, task)
        branch_task_id = task.branch_task_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to queue BranchTask for %s: %s", request_id, exc)

    pending_count = sum(
        1 for r in existing
        if isinstance(r, dict) and r.get("status") == "pending"
    )
    ahead = max(0, pending_count - 1)
    if ahead == 0:
        position_note = "yours is next in the daemon's queue"
    elif ahead == 1:
        position_note = "1 other request is ahead of yours"
    else:
        position_note = f"{ahead} other requests are ahead of yours"

    return json.dumps({
        "universe_id": uid,
        "request_id": request_id,
        "branch_task_id": branch_task_id,
        "status": "pending",
        "priority_weight": pw,
        "pickup_incentive": incentive,
        "authority_boundary": authority_boundary,
        "request_classification": request_classification,
        "requester_directed_daemon": requester_directed_daemon,
        "loop_dispatch": loop_dispatch,
        "queue_position": pending_count,
        "ahead_of_yours": ahead,
        "what_happens_next": (
            f"The daemon will see your request on its next review cycle; "
            f'{position_note}. Use `read_graph target="graph" graph_id="{uid}"` '
            "to watch the queue or check whether your request is now active work."
        ),
    })


def _safe_epoch2_queue_row(
    task: Any,
    *,
    score: float,
    tier_enabled: bool,
) -> dict[str, Any]:
    """Serialize only non-private operational identity and state."""
    return {
        "branch_task_id": task.branch_task_id,
        "admission_id": task.admission_id,
        "request_id": task.request_id,
        "status": task.status,
        "queue_epoch": task.queue_epoch,
        "protocol_version": task.protocol_version,
        "trigger_source": task.trigger_source,
        "queued_at": task.queued_at,
        "score": score,
        "tier_enabled": tier_enabled,
    }


def _action_queue_list(
    universe_id: str = "",
    **_kwargs: Any,
) -> str:
    """Read ``branch_tasks.json`` fresh (no in-memory cache) and
    return sorted+scored queue. Includes ``tier_status`` per R11.
    """
    from tinyassets.branch_tasks import read_queue
    from tinyassets.dispatcher import (
        load_dispatcher_config,
        score_task,
    )

    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})

    v1_error = ""
    try:
        queue = read_queue(udir)
    except Exception as exc:  # noqa: BLE001
        queue = []
        v1_error = str(exc)
    non_public_goal_ids = _non_public_goal_ids()
    queue = [
        task for task in queue
        if not task.goal_id or task.goal_id not in non_public_goal_ids
    ]

    cfg = load_dispatcher_config(udir)
    now_iso = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    epoch2_read = _epoch2_operational_read(
        udir,
        dispatcher_config=cfg,
    )
    epoch2 = epoch2_read.summary
    for task in queue:
        row = task.to_dict()
        row.setdefault("queue_epoch", 1)
        row["score"] = score_task(task, now_iso=now_iso, config=cfg)
        row["tier_enabled"] = cfg.tier_enabled(task.trigger_source)
        rows.append(row)
    for task in epoch2_read.candidates:
        score = score_task(task, now_iso=now_iso, config=cfg)
        rows.append(_safe_epoch2_queue_row(
            task,
            score=score,
            tier_enabled=cfg.tier_enabled(task.trigger_source),
        ))
    # Primary: status pending first, then score desc. Non-pending
    # sorted by queued_at desc.
    rows.sort(
        key=lambda r: (
            0 if r.get("status") == "pending" else 1,
            -float(r.get("score", 0.0)),
            r.get("queued_at", ""),
        ),
    )

    epoch1_lifecycle = {
        "depth": len(queue),
        "lifecycle": {
            status: sum(
                1 for task in queue if task.status == status
            )
            for status in (
                "pending",
                "running",
                "cancel_requested",
                "cancelled",
                "succeeded",
                "failed",
            )
        },
    }
    epoch_counts: dict[str, Any] = {
        "1": (
            {"available": False, "error": v1_error}
            if v1_error
            else {"available": True, **epoch1_lifecycle}
        ),
        "2": (
            {
                "available": True,
                "depth": epoch2["depth"],
                "lifecycle": epoch2["lifecycle_counts"],
                "operational": epoch2["operational_state_counts"],
            }
            if epoch2["available"]
            else {
                "available": False,
                "error": epoch2["error"],
            }
        ),
    }
    v1_pending = epoch1_lifecycle["lifecycle"]["pending"]
    v1_running = epoch1_lifecycle["lifecycle"]["running"]
    v2_lifecycle = epoch2.get("lifecycle_counts") or {}
    operational_counts_authoritative = bool(
        epoch2.get("operational_counts_authoritative", False)
    )
    return json.dumps({
        "universe_id": uid,
        "queue": rows,
        "pending_count": v1_pending + int(v2_lifecycle.get("pending", 0)),
        "running_count": v1_running + int(v2_lifecycle.get("running", 0)),
        "counts_complete": (
            not v1_error
            and epoch2["available"]
            and operational_counts_authoritative
        ),
        "epoch_counts": epoch_counts,
        "epoch_health": {
            epoch: {
                "available": data["available"],
                **(
                    {"error": data["error"]}
                    if not data["available"]
                    else {}
                ),
            }
            for epoch, data in epoch_counts.items()
        },
        "operational_state_counts": epoch2.get(
            "operational_state_counts"
        ),
        "operational_state_oldest_age_s": epoch2.get(
            "operational_oldest_age_s"
        ),
        "operational_reason_counts": epoch2.get(
            "operational_reason_counts"
        ),
        "operational_diagnostics": epoch2["diagnostics"],
        "operational_diagnostics_truncated": epoch2[
            "diagnostics_truncated"
        ],
        "operational_counts_authoritative": (
            operational_counts_authoritative
        ),
        "integrity_scope_complete": epoch2.get(
            "integrity_scope_complete"
        ),
        "unknown_epoch2_lifecycle_status_counts": epoch2.get(
            "unknown_lifecycle_status_counts"
        ),
        "unclassified_epoch2_active_count": epoch2.get(
            "unclassified_active_count"
        ),
        "epoch2_active_scan_limit": epoch2.get("active_scan_limit"),
        "capacity_evidence_available": epoch2.get(
            "capacity_evidence_available"
        ),
        "capacity_evidence_error": epoch2.get("capacity_evidence_error"),
        "consumer_ready": epoch2.get("consumer_ready"),
        "valid_epoch2_pending_count": epoch2.get("valid_pending_count"),
        "eligible_epoch2_pending_count": epoch2.get(
            "eligible_pending_count"
        ),
        "compatible_worker_count": epoch2.get("compatible_worker_count"),
        "tier_status": cfg.tier_status_map(),
        **(
            {
                "unscoped_invalid_count": int(
                    epoch2["unscoped_invalid_count"]
                ),
            }
            if "unscoped_invalid_count" in epoch2
            else {}
        ),
    })


# ---------------------------------------------------------------------------
# Daemon roster + runtime actions
# ---------------------------------------------------------------------------


def _parse_inputs_object(inputs_json: str) -> tuple[dict[str, Any], str | None]:
    if not inputs_json.strip():
        return {}, None
    try:
        parsed = json.loads(inputs_json)
    except json.JSONDecodeError as exc:
        return {}, f"inputs_json invalid JSON: {exc}"
    if not isinstance(parsed, dict):
        return {}, "inputs_json must be a JSON object."
    return parsed, None


def _action_daemon_list(
    universe_id: str = "",
    limit: Any = 30,
    **_kwargs: Any,
) -> str:
    from tinyassets.daemon_registry import list_daemons, list_runtime_instances

    uid = _request_universe(universe_id)
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 30
    if n <= 0:
        n = 30
    daemons = list_daemons(_base_path())[:n]
    runtimes = list_runtime_instances(_base_path(), universe_id=uid)
    return json.dumps({
        "universe_id": uid,
        "daemons": daemons,
        "runtimes": runtimes,
        "count": len(daemons),
        "runtime_count": len(runtimes),
    }, default=str)


def _target_daemon_id(
    data: dict[str, Any],
    *,
    daemon_id: str = "",
    node_def_id: str = "",
) -> str:
    return str(data.get("daemon_id") or daemon_id or node_def_id or "").strip()


def _latest_runtime_id_for_daemon(
    daemon_id: str,
    *,
    universe_id: str | None = None,
) -> str:
    from tinyassets.daemon_registry import get_daemon, list_runtime_instances

    get_daemon(_base_path(), daemon_id=daemon_id)
    runtimes = [
        runtime
        for runtime in list_runtime_instances(_base_path(), universe_id=universe_id)
        if runtime.get("daemon_id") == daemon_id
    ]
    active = [runtime for runtime in runtimes if runtime.get("status") != "retired"]
    candidates = active or runtimes
    if not candidates:
        return ""
    candidates.sort(
        key=lambda runtime: str(
            runtime.get("updated_at") or runtime.get("created_at") or ""
        ),
        reverse=True,
    )
    return str(candidates[0].get("runtime_instance_id") or "").strip()


def _action_daemon_get(
    inputs_json: str = "",
    node_def_id: str = "",
    daemon_id: str = "",
    **_kwargs: Any,
) -> str:
    from tinyassets.daemon_registry import get_daemon

    data, err = _parse_inputs_object(inputs_json)
    if err:
        return json.dumps({"error": err})
    resolved_daemon_id = _target_daemon_id(
        data, daemon_id=daemon_id, node_def_id=node_def_id
    )
    if not resolved_daemon_id:
        return json.dumps({"error": "daemon_id is required."})
    try:
        daemon = get_daemon(
            _base_path(),
            daemon_id=resolved_daemon_id,
            include_soul=bool(data.get("include_soul", False)),
        )
    except KeyError:
        return json.dumps({"error": f"Daemon '{resolved_daemon_id}' not found."})
    return json.dumps({"daemon": daemon}, default=str)


def _action_daemon_create(
    universe_id: str = "",
    inputs_json: str = "",
    text: str = "",
    **_kwargs: Any,
) -> str:
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.daemon_registry import create_daemon

    data, err = _parse_inputs_object(inputs_json)
    if err:
        return json.dumps({"error": err})
    display_name = str(data.get("display_name") or text or "").strip()
    if not display_name:
        return json.dumps({"error": "display_name is required."})
    try:
        daemon = create_daemon(
            _base_path(),
            display_name=display_name,
            created_by=_current_actor(),
            soul_mode=str(data.get("soul_mode") or "").strip() or None,
            soul_text=str(data.get("soul_text") or ""),
            domain_claims=data.get("domain_claims")
            if isinstance(data.get("domain_claims"), list)
            else None,
            lineage_parent_id=str(data.get("lineage_parent_id") or "").strip() or None,
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else None,
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "universe_id": _request_universe(universe_id),
        "daemon": daemon,
    }, default=str)


def _action_daemon_summon(
    universe_id: str = "",
    inputs_json: str = "",
    branch_id: str = "",
    daemon_id: str = "",
    node_def_id: str = "",
    **_kwargs: Any,
) -> str:
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.daemon_registry import summon_daemon

    data, err = _parse_inputs_object(inputs_json)
    if err:
        return json.dumps({"error": err})
    resolved_daemon_id = _target_daemon_id(
        data, daemon_id=daemon_id, node_def_id=node_def_id
    )
    provider_name = str(data.get("provider_name") or "").strip()
    model_name = str(data.get("model_name") or provider_name).strip()
    uid = _request_universe(universe_id or str(data.get("universe_id") or "").strip())
    if not resolved_daemon_id:
        return json.dumps({"error": "daemon_id is required."})
    if not provider_name:
        return json.dumps({"error": "provider_name is required."})
    try:
        runtime = summon_daemon(
            _base_path(),
            daemon_id=resolved_daemon_id,
            universe_id=uid,
            provider_name=provider_name,
            model_name=model_name,
            branch_id=branch_id or data.get("branch_id") or None,
            created_by=_current_actor(),
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else None,
        )
    except KeyError:
        return json.dumps({"error": f"Daemon '{resolved_daemon_id}' not found."})
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"universe_id": uid, "runtime": runtime}, default=str)


def _action_daemon_banish(
    universe_id: str = "",
    inputs_json: str = "",
    branch_task_id: str = "",
    **_kwargs: Any,
) -> str:
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.daemon_registry import control_runtime_instance

    data, err = _parse_inputs_object(inputs_json)
    if err:
        return json.dumps({"error": err})
    runtime_id = str(data.get("runtime_instance_id") or branch_task_id or "").strip()
    if not runtime_id:
        return json.dumps({"error": "runtime_instance_id is required."})
    try:
        result = control_runtime_instance(
            _base_path(),
            runtime_instance_id=runtime_id,
            actor_id=_current_actor(),
            action="banish",
        )
    except KeyError:
        return json.dumps({"error": f"Runtime '{runtime_id}' not found."})
    result["universe_id"] = _request_universe(universe_id)
    return json.dumps(result, default=str)


def _action_daemon_runtime_control(
    action_name: str,
    *,
    universe_id: str = "",
    inputs_json: str = "",
    branch_task_id: str = "",
    daemon_id: str = "",
    node_def_id: str = "",
) -> str:
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.daemon_registry import control_runtime_instance

    data, err = _parse_inputs_object(inputs_json)
    if err:
        return json.dumps({"error": err})
    runtime_id = str(data.get("runtime_instance_id") or branch_task_id or "").strip()
    resolved_daemon_id = _target_daemon_id(
        data, daemon_id=daemon_id, node_def_id=node_def_id
    )
    if not runtime_id and resolved_daemon_id:
        uid = universe_id or str(data.get("universe_id") or "").strip() or None
        try:
            runtime_id = _latest_runtime_id_for_daemon(
                resolved_daemon_id,
                universe_id=uid,
            )
        except KeyError:
            return json.dumps({"error": f"Daemon '{resolved_daemon_id}' not found."})
        if not runtime_id:
            return json.dumps({
                "error": f"Runtime for daemon '{resolved_daemon_id}' not found."
            })
    if not runtime_id:
        return json.dumps({"error": "runtime_instance_id is required."})
    try:
        result = control_runtime_instance(
            _base_path(),
            runtime_instance_id=runtime_id,
            actor_id=_current_actor(),
            action=action_name,
        )
    except KeyError:
        return json.dumps({"error": f"Runtime '{runtime_id}' not found."})
    result["universe_id"] = _request_universe(universe_id)
    return json.dumps(result, default=str)


def _action_daemon_pause(
    universe_id: str = "",
    inputs_json: str = "",
    branch_task_id: str = "",
    daemon_id: str = "",
    node_def_id: str = "",
    **_kwargs: Any,
) -> str:
    return _action_daemon_runtime_control(
        "pause",
        universe_id=universe_id,
        inputs_json=inputs_json,
        branch_task_id=branch_task_id,
        daemon_id=daemon_id,
        node_def_id=node_def_id,
    )


def _action_daemon_resume(
    universe_id: str = "",
    inputs_json: str = "",
    branch_task_id: str = "",
    daemon_id: str = "",
    node_def_id: str = "",
    **_kwargs: Any,
) -> str:
    return _action_daemon_runtime_control(
        "resume",
        universe_id=universe_id,
        inputs_json=inputs_json,
        branch_task_id=branch_task_id,
        daemon_id=daemon_id,
        node_def_id=node_def_id,
    )


def _action_daemon_restart(
    universe_id: str = "",
    inputs_json: str = "",
    branch_task_id: str = "",
    daemon_id: str = "",
    node_def_id: str = "",
    **_kwargs: Any,
) -> str:
    return _action_daemon_runtime_control(
        "restart",
        universe_id=universe_id,
        inputs_json=inputs_json,
        branch_task_id=branch_task_id,
        daemon_id=daemon_id,
        node_def_id=node_def_id,
    )


def _action_daemon_update_behavior(
    universe_id: str = "",
    inputs_json: str = "",
    text: str = "",
    node_def_id: str = "",
    **_kwargs: Any,
) -> str:
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.daemon_registry import update_daemon_behavior

    data, err = _parse_inputs_object(inputs_json)
    if err:
        return json.dumps({"error": err})
    daemon_id = str(data.get("daemon_id") or node_def_id or "").strip()
    if not daemon_id:
        return json.dumps({"error": "daemon_id is required."})
    behavior = data.get("behavior_update")
    if not isinstance(behavior, dict):
        behavior = {"note": text.strip()} if text.strip() else {}
    if not behavior:
        return json.dumps({"error": "behavior_update is required."})
    try:
        result = update_daemon_behavior(
            _base_path(),
            daemon_id=daemon_id,
            actor_id=_current_actor(),
            behavior_update=behavior,
            apply_now=bool(data.get("apply_now") or data.get("apply")),
        )
    except KeyError:
        return json.dumps({"error": f"Daemon '{daemon_id}' not found."})
    result["universe_id"] = _request_universe(universe_id)
    return json.dumps(result, default=str)


def _action_daemon_control_status(
    universe_id: str = "",
    inputs_json: str = "",
    node_def_id: str = "",
    branch_task_id: str = "",
    daemon_id: str = "",
    **_kwargs: Any,
) -> str:
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.daemon_registry import daemon_control_status

    data, err = _parse_inputs_object(inputs_json)
    if err:
        return json.dumps({"error": err})
    resolved_daemon_id = _target_daemon_id(
        data, daemon_id=daemon_id, node_def_id=node_def_id
    )
    result = daemon_control_status(
        _base_path(),
        actor_id=_current_actor(),
        daemon_id=resolved_daemon_id or None,
        runtime_instance_id=(
            str(data.get("runtime_instance_id") or branch_task_id or "").strip()
            or None
        ),
        universe_id=universe_id or str(data.get("universe_id") or "").strip() or None,
    )
    result["universe_id"] = _request_universe(universe_id)
    return json.dumps(result, default=str)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_string_list(value: Any) -> list[str] | None:
    if value is None or value == "":
        return None
    if isinstance(value, list):
        out = [str(item).strip() for item in value if str(item).strip()]
        return out or None
    if isinstance(value, str):
        out = [part.strip() for part in value.split(",") if part.strip()]
        return out or None
    return None


def _daemon_memory_inputs(
    inputs_json: str,
    *,
    daemon_id: str = "",
    node_def_id: str = "",
) -> tuple[dict[str, Any], str, str | None]:
    data, err = _parse_inputs_object(inputs_json)
    if err:
        return {}, "", err
    resolved_daemon_id = str(data.get("daemon_id") or daemon_id or node_def_id or "").strip()
    if not resolved_daemon_id:
        return data, "", "daemon_id is required."
    return data, resolved_daemon_id, None


def _action_daemon_memory_capture(
    universe_id: str = "",
    inputs_json: str = "",
    text: str = "",
    daemon_id: str = "",
    node_def_id: str = "",
    **_kwargs: Any,
) -> str:
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.daemon_brain import capture_daemon_memory

    uid = _request_universe(universe_id)
    data, daemon_id, err = _daemon_memory_inputs(
        inputs_json, daemon_id=daemon_id, node_def_id=node_def_id
    )
    if err:
        return json.dumps({"universe_id": uid, "error": err})
    content = str(data.get("content") or text or "").strip()
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else None
    temporal_bounds = (
        data.get("temporal_bounds") if isinstance(data.get("temporal_bounds"), dict)
        else None
    )
    try:
        entry = capture_daemon_memory(
            _base_path(),
            daemon_id=daemon_id,
            content=content,
            memory_kind=str(data.get("memory_kind") or "semantic"),
            source_type=str(data.get("source_type") or "manual"),
            source_id=str(data.get("source_id") or _current_actor() or "manual"),
            source_path=str(data.get("source_path") or ""),
            source_hash=str(data.get("source_hash") or ""),
            reliability=str(data.get("reliability") or ""),
            temporal_bounds=temporal_bounds,
            language_type=str(data.get("language_type") or ""),
            confidence=float(data.get("confidence", 0.5)),
            importance=float(data.get("importance", 0.5)),
            sensitivity_tier=str(data.get("sensitivity_tier") or "normal"),
            visibility=str(data.get("visibility") or "host_private"),
            promotion_state=str(data.get("promotion_state") or "candidate"),
            supersedes_entry_id=(
                str(data.get("supersedes_entry_id")).strip()
                if data.get("supersedes_entry_id") else None
            ),
            metadata=metadata,
        )
    except StorageRefused as refused:
        return json.dumps({"universe_id": uid, **_visible_refusal(refused)})
    except (KeyError, ValueError, TypeError) as exc:
        return json.dumps({"universe_id": uid, "error": str(exc)})
    return json.dumps({"universe_id": uid, "daemon_id": daemon_id, "entry": entry}, default=str)


def _action_daemon_memory_search(
    universe_id: str = "",
    inputs_json: str = "",
    text: str = "",
    filter_text: str = "",
    limit: Any = 5,
    daemon_id: str = "",
    node_def_id: str = "",
    **_kwargs: Any,
) -> str:
    from tinyassets.daemon_brain import search_daemon_memory

    uid = _request_universe(universe_id)
    data, daemon_id, err = _daemon_memory_inputs(
        inputs_json, daemon_id=daemon_id, node_def_id=node_def_id
    )
    if err:
        return json.dumps({"universe_id": uid, "error": err})
    query = str(data.get("query") or text or filter_text or "").strip()
    try:
        result = search_daemon_memory(
            _base_path(),
            daemon_id=daemon_id,
            query=query,
            limit=int(data.get("limit", limit)),
            min_score=float(data.get("min_score", 0.0)),
            include_superseded=_as_bool(data.get("include_superseded", False)),
            memory_kinds=_as_string_list(data.get("memory_kinds")),
            visibility=(
                str(data.get("visibility")).strip()
                if data.get("visibility") else None
            ),
        )
    except (KeyError, ValueError, TypeError) as exc:
        return json.dumps({"universe_id": uid, "error": str(exc)})
    result["universe_id"] = uid
    return json.dumps(result, default=str)


def _action_daemon_memory_list(
    universe_id: str = "",
    inputs_json: str = "",
    limit: Any = 50,
    daemon_id: str = "",
    node_def_id: str = "",
    **_kwargs: Any,
) -> str:
    from tinyassets.daemon_brain import list_daemon_memory

    uid = _request_universe(universe_id)
    data, daemon_id, err = _daemon_memory_inputs(
        inputs_json, daemon_id=daemon_id, node_def_id=node_def_id
    )
    if err:
        return json.dumps({"universe_id": uid, "error": err})
    try:
        result = list_daemon_memory(
            _base_path(),
            daemon_id=daemon_id,
            limit=int(data.get("limit", limit)),
            include_superseded=_as_bool(data.get("include_superseded", False)),
            memory_kinds=_as_string_list(data.get("memory_kinds")),
        )
    except (KeyError, ValueError, TypeError) as exc:
        return json.dumps({"universe_id": uid, "error": str(exc)})
    result["universe_id"] = uid
    return json.dumps(result, default=str)


def _action_daemon_memory_review(
    universe_id: str = "",
    inputs_json: str = "",
    text: str = "",
    branch_task_id: str = "",
    daemon_id: str = "",
    node_def_id: str = "",
    **_kwargs: Any,
) -> str:
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.daemon_brain import review_daemon_memory

    uid = _request_universe(universe_id)
    data, daemon_id, err = _daemon_memory_inputs(
        inputs_json, daemon_id=daemon_id, node_def_id=node_def_id
    )
    if err:
        return json.dumps({"universe_id": uid, "error": err})
    entry_id = str(data.get("entry_id") or branch_task_id or "").strip()
    if not entry_id:
        return json.dumps({"universe_id": uid, "error": "entry_id is required."})
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else None
    try:
        result = review_daemon_memory(
            _base_path(),
            daemon_id=daemon_id,
            entry_id=entry_id,
            decision=str(data.get("decision") or text or ""),
            reviewer_id=str(data.get("reviewer_id") or _current_actor() or "host"),
            note=str(data.get("note") or ""),
            superseded_by_entry_id=(
                str(data.get("superseded_by_entry_id")).strip()
                if data.get("superseded_by_entry_id") else None
            ),
            metadata=metadata,
        )
    except (KeyError, ValueError, TypeError) as exc:
        return json.dumps({"universe_id": uid, "error": str(exc)})
    result["universe_id"] = uid
    return json.dumps(result, default=str)


def _action_daemon_memory_promote(
    universe_id: str = "",
    inputs_json: str = "",
    text: str = "",
    branch_task_id: str = "",
    daemon_id: str = "",
    node_def_id: str = "",
    **_kwargs: Any,
) -> str:
    from tinyassets.daemon_brain import promote_daemon_memory_to_wiki

    uid = _request_universe(universe_id)
    data, daemon_id, err = _daemon_memory_inputs(
        inputs_json, daemon_id=daemon_id, node_def_id=node_def_id
    )
    if err:
        return json.dumps({"universe_id": uid, "error": err})
    entry_ids = _as_string_list(data.get("entry_ids")) or _as_string_list(branch_task_id)
    summary = str(data.get("summary") or text or "").strip()
    target_rel_path = str(data.get("target_rel_path") or "pages/brain/review.md")
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else None
    try:
        result = promote_daemon_memory_to_wiki(
            _base_path(),
            daemon_id=daemon_id,
            entry_ids=entry_ids or [],
            summary=summary,
            target_rel_path=target_rel_path,
            metadata=metadata,
        )
    except StorageRefused as refused:
        return json.dumps({"universe_id": uid, **_visible_refusal(refused)})
    except (KeyError, ValueError, TypeError) as exc:
        return json.dumps({"universe_id": uid, "error": str(exc)})
    result["universe_id"] = uid
    return json.dumps(result, default=str)


def _action_daemon_memory_status(
    universe_id: str = "",
    inputs_json: str = "",
    daemon_id: str = "",
    node_def_id: str = "",
    **_kwargs: Any,
) -> str:
    from tinyassets.daemon_brain import memory_observability_status

    uid = _request_universe(universe_id)
    data, daemon_id, err = _daemon_memory_inputs(
        inputs_json, daemon_id=daemon_id, node_def_id=node_def_id
    )
    if err:
        return json.dumps({"universe_id": uid, "error": err})
    try:
        result = memory_observability_status(_base_path(), daemon_id=daemon_id)
    except (KeyError, ValueError, TypeError) as exc:
        return json.dumps({"universe_id": uid, "error": str(exc)})
    result["universe_id"] = uid
    return json.dumps(result, default=str)


def _action_treasury_status(
    universe_id: str = "",
    limit: Any = 10,
    **_kwargs: Any,
) -> str:
    from tinyassets.treasury import treasury_status

    uid = _request_universe(universe_id)
    try:
        result = treasury_status(_base_path(), limit=int(limit))
    except (ValueError, TypeError) as exc:
        return json.dumps({"universe_id": uid, "error": str(exc)})
    result["universe_id"] = uid
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Phase H — daemon_overview + set_tier_config (aggregated MCP surface)
# ---------------------------------------------------------------------------

# 1-second TTL cache per universe for daemon_overview (R1 invariant 1).
_OVERVIEW_CACHE: dict[str, tuple[float, str, str]] = {}
_OVERVIEW_TTL_SECONDS = 1.0


def _non_public_goal_ids() -> set[str]:
    """Batch-resolve every known non-public Goal ID."""
    from tinyassets.daemon_server import non_public_goal_ids

    return non_public_goal_ids(_base_path())


def _public_subscription_refs(
    goal_refs: list[str],
    *,
    non_public_goal_ids: set[str] | None = None,
) -> list[str]:
    """Hide known non-public Goals while retaining legacy pool topic names."""
    blocked = (
        _non_public_goal_ids()
        if non_public_goal_ids is None
        else non_public_goal_ids
    )
    # Goal-pool subscriptions predate Shared Goals and may be topic slugs such
    # as ``maintenance`` rather than Goal record IDs. Unknown refs remain
    # visible; only known non-public Goal records are filtered.
    return [goal_ref for goal_ref in goal_refs if goal_ref not in blocked]


# Per-caller reasonable limits (R14 response-size). Overridable via `limit`.
_OVERVIEW_DEFAULT_LIMITS = {
    "queue_top": 20,
    "bids_top": 20,
    "settlements_recent": 10,
    "gates_recent": 10,
    "activity_tail": 30,
}
# Absolute cap even when `limit=full` — prevents pathological responses.
_OVERVIEW_ABSOLUTE_CAP = {
    "queue_top": 500,
    "bids_top": 500,
    "settlements_recent": 500,
    "gates_recent": 200,
    "activity_tail": 1000,
}

# R14 serialized-byte cap. Per-field caps above are necessary but not
# sufficient: a dense queue + long activity_tail + many bids can still
# blow past Claude.ai token limits even with each field individually
# bounded. If the serialized response exceeds this threshold, trim in
# priority order (see _OVERVIEW_TRIM_ORDER). Gates + dispatcher +
# subscriptions are NEVER trimmed (load-bearing per reviewer polish
# #5).
DAEMON_OVERVIEW_MAX_BYTES = 32_768

# Trim priority when the byte cap fires. Each entry is
# ``(key, subkey, keep_side)``:
# - ``key``: top-level response field.
# - ``subkey``: nested key (e.g. ``response["bids"]["recent"]``) or
#   None for top-level lists.
# - ``keep_side``: "head" keeps the front of the list (sorted
#   descending — top-N); "tail" keeps the back (chronological —
#   latest entries).
_OVERVIEW_TRIM_ORDER = (
    ("activity_tail", None, "tail"),
    ("settlements", "recent", "head"),
    ("bids", "recent", "head"),
    ("queue", "top", "head"),
)


def _trim_overview_for_bytes(
    response: dict[str, Any], *, cap: int = DAEMON_OVERVIEW_MAX_BYTES,
) -> str:
    """Serialize ``response`` and trim until ``<= cap`` bytes.

    Mutates ``response`` in place as fields shrink. When any trim
    lands, sets ``response["truncated"] = True`` so consumers know
    counters (``*_count`` fields) are authoritative over the trimmed
    lists. Gates + dispatcher + subscriptions are never in the trim
    order (load-bearing). Returns the final serialized JSON.
    """
    serialized = json.dumps(response, default=str)
    if len(serialized.encode("utf-8")) <= cap:
        return serialized
    response["truncated"] = True
    for key, subkey, keep_side in _OVERVIEW_TRIM_ORDER:
        container: Any = response.get(key)
        if subkey is not None:
            container = container.get(subkey) if isinstance(container, dict) else None
        if not isinstance(container, list):
            continue
        while container:
            if len(container) <= 1:
                container.clear()
            elif keep_side == "tail":
                # Halve from the front, keep the latest entries.
                del container[: len(container) // 2]
            else:
                # Halve from the back, keep top-ranked entries.
                del container[len(container) // 2:]
            serialized = json.dumps(response, default=str)
            if len(serialized.encode("utf-8")) <= cap:
                return serialized
    return serialized


def _overview_limits(limit_param: Any) -> dict[str, int]:
    """Resolve per-field limits from the `limit` param.

    `limit` int → applies that value to all top-N lists (bounded by
    absolute cap). `limit="full"` → absolute cap (not truly unbounded).
    Default / invalid → documented defaults.
    """
    if isinstance(limit_param, str) and limit_param.strip().lower() == "full":
        return dict(_OVERVIEW_ABSOLUTE_CAP)
    try:
        n = int(limit_param)
    except (TypeError, ValueError):
        return dict(_OVERVIEW_DEFAULT_LIMITS)
    if n <= 0:
        return dict(_OVERVIEW_DEFAULT_LIMITS)
    return {
        key: min(n, _OVERVIEW_ABSOLUTE_CAP[key])
        for key in _OVERVIEW_DEFAULT_LIMITS
    }


def _tail_file_lines(path: Path, n: int) -> list[str]:
    """Return the last `n` lines of `path`; empty when absent.

    Read link-free (``read_data_path``): a refused read raises rather than
    returning another universe's lines through a planted ``activity.log``.
    """
    if n <= 0:
        return []
    raw = read_data_path(path, max_bytes=MAX_PLATFORM_FILE_BYTES)
    if raw is None:
        return []
    return raw.decode("utf-8", "replace").splitlines()[-n:]


def _action_daemon_overview(
    universe_id: str = "",
    limit: Any = None,
    **_kwargs: Any,
) -> str:
    """Aggregated read-through per preflight §4.1 #1 (Phase H).

    Composes queue + subscriptions + bids + settlements + gates +
    activity tail + run state into one response. 1s TTL cache keyed
    on (universe_id, limit, integrity-visibility class) keeps hot-path
    cost bounded without replaying admin-only diagnostics to readers.

    Read-only: no mutations. Absent features gracefully degrade
    (empty lists / zero counts) rather than error.
    """
    import time as _time

    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})

    limit_key = (
        "full" if isinstance(limit, str)
        and limit.strip().lower() == "full" else str(limit)
    )
    integrity_visibility = (
        "admin"
        if _may_view_unscoped_epoch2_integrity(udir)
        else "reader"
    )
    cache_key = f"{uid}::{limit_key}::{integrity_visibility}"
    now_s = _time.time()
    cached = _OVERVIEW_CACHE.get(cache_key)
    if cached and (now_s - cached[0]) < _OVERVIEW_TTL_SECONDS:
        return cached[2]

    limits = _overview_limits(limit)
    response: dict[str, Any] = {"universe_id": uid}

    # Dispatcher config + tier_status_map.
    try:
        from tinyassets.dispatcher import load_dispatcher_config
        cfg = load_dispatcher_config(udir)
        response["dispatcher"] = {
            "tier_status_map": cfg.tier_status_map(),
            "config": {
                "accept_external_requests": cfg.accept_external_requests,
                "accept_goal_pool": cfg.accept_goal_pool,
                "accept_paid_bids": cfg.accept_paid_bids,
                "allow_opportunistic": cfg.allow_opportunistic,
                "bid_coefficient": cfg.bid_coefficient,
                "bid_term_cap": cfg.bid_term_cap,
            },
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("daemon_overview: dispatcher read failed: %s", exc)
        response["dispatcher"] = {}

    # Queue top-N.
    try:
        queue_read = json.loads(_action_queue_list(universe_id=uid))
        pending_rows = [
            row
            for row in queue_read["queue"]
            if row.get("status") == "pending"
        ]
        response["queue"] = {
            "pending_count": queue_read["pending_count"],
            "top": pending_rows[: limits["queue_top"]],
            "archived_recent_count": 0,
            **{
                key: queue_read[key]
                for key in (
                    "counts_complete",
                    "epoch_counts",
                    "epoch_health",
                    "operational_state_counts",
                    "operational_state_oldest_age_s",
                    "operational_reason_counts",
                    "operational_diagnostics",
                    "operational_diagnostics_truncated",
                    "operational_counts_authoritative",
                    "integrity_scope_complete",
                    "unknown_epoch2_lifecycle_status_counts",
                    "unclassified_epoch2_active_count",
                    "epoch2_active_scan_limit",
                    "capacity_evidence_available",
                    "capacity_evidence_error",
                    "valid_epoch2_pending_count",
                    "eligible_epoch2_pending_count",
                    "compatible_worker_count",
                )
            },
            **(
                {
                    "unscoped_invalid_count": queue_read[
                        "unscoped_invalid_count"
                    ],
                }
                if "unscoped_invalid_count" in queue_read
                else {}
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("daemon_overview: queue read failed: %s", exc)
        response["queue"] = {
            "pending_count": None,
            "top": [],
            "archived_recent_count": 0,
            "counts_complete": False,
            "epoch_health": {
                "1": {"available": False, "error": str(exc)},
                "2": {"available": False, "error": str(exc)},
            },
            "error": str(exc),
        }

    # Subscriptions + drift.
    try:
        from tinyassets.producers.goal_pool import (
            POOL_DIRNAME,
            goal_pool_enabled,
            repo_root_path,
        )
        from tinyassets.subscriptions import list_subscriptions
        goals = _public_subscription_refs(list_subscriptions(udir))
        counts: dict[str, int] = {g: 0 for g in goals}
        try:
            repo_root = repo_root_path(udir)
            pool_root = repo_root / POOL_DIRNAME
            for g in goals:
                gdir = pool_root / g
                if gdir.is_dir():
                    counts[g] = sum(1 for _ in gdir.glob("*.yaml"))
        except RuntimeError:
            pass
        if cfg.accept_goal_pool and not goals:
            drift = "pool_enabled_no_subs"
        elif goals and not cfg.accept_goal_pool:
            drift = "subs_but_pool_disabled"
        else:
            drift = "ok"
        response["subscriptions"] = {
            "goals": goals,
            "drift_flag": drift,
            "pool_status_per_goal": counts,
            "pool_flag_enabled": goal_pool_enabled(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("daemon_overview: subscriptions read failed: %s", exc)
        response["subscriptions"] = {"goals": [], "drift_flag": "ok",
                                     "pool_status_per_goal": {}}

    # Bids + daemon capabilities.
    try:
        from tinyassets.bid.node_bid import read_node_bids
        from tinyassets.producers.goal_pool import repo_root_path
        from tinyassets.producers.node_bid import paid_market_enabled
        try:
            bid_repo_root = repo_root_path(udir)
            bids = read_node_bids(bid_repo_root)
        except RuntimeError:
            bids = []
        open_bids = [b.to_dict() for b in bids if b.status == "open"]
        claimed = sum(1 for b in bids if b.status.startswith("claimed:"))
        response["bids"] = {
            "open_count": len(open_bids),
            "claimed_count": claimed,
            "top_open": open_bids[: limits["bids_top"]],
            "daemon_capabilities": {
                "serves_llm_types": sorted(
                    os.environ.get("FANTASY_DAEMON_LLM_TYPES", "").split(",")
                    if os.environ.get("FANTASY_DAEMON_LLM_TYPES")
                    else [],
                ),
                "paid_market_enabled": paid_market_enabled(),
                "bid_coefficient": cfg.bid_coefficient,
            },
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("daemon_overview: bids read failed: %s", exc)
        response["bids"] = {"open_count": 0, "claimed_count": 0,
                            "top_open": []}

    # Settlements.
    try:
        import yaml as _yaml  # noqa: F401 - soul settlements need PyYAML

        from tinyassets.bid.settlements import settlements_dir
        from tinyassets.producers.goal_pool import repo_root_path
        try:
            sroot = settlements_dir(repo_root_path(udir))
        except RuntimeError:
            sroot = None
        s_entries: list[dict] = []
        s_total = 0
        s_unsettled = 0
        if sroot and sroot.is_dir():
            for p in sorted(sroot.glob("*.yaml")):
                s_total += 1
                try:
                    raw = load_untrusted_yaml(
                        (read_data_path(p, max_bytes=MAX_CONFIG_BYTES) or b"").decode("utf-8")
                    ) or {}
                except Exception:  # noqa: BLE001
                    continue
                if not isinstance(raw, dict):
                    continue
                if not raw.get("settled"):
                    s_unsettled += 1
                s_entries.append(raw)
        s_entries.sort(
            key=lambda r: str(r.get("completed_at", "")), reverse=True,
        )
        response["settlements"] = {
            "count_total": s_total,
            "count_unsettled": s_unsettled,
            "recent": s_entries[: limits["settlements_recent"]],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "daemon_overview: settlements read failed: %s", exc,
        )
        response["settlements"] = {
            "count_total": 0, "count_unsettled": 0, "recent": [],
        }

    # Unscoped gate-claim enumeration is unsupported; scoped claims remain
    # available through the gates surface.
    response["gates"] = {"total_claims": 0, "recent_claims": []}

    # Activity tail (raw file, not a parse).
    response["activity_tail"] = _tail_file_lines(
        udir / "activity.log", limits["activity_tail"],
    )

    # Run state (status.json — best-effort).
    try:
        status = _read_json(udir / "status.json") or {}
        if isinstance(status, dict):
            response["run_state"] = {
                "current_phase": status.get("current_phase", ""),
                "status": status.get("daemon_state", ""),
                "last_verdict": status.get("last_verdict", ""),
                "total_words": status.get("total_words", 0),
                "total_chapters": status.get("total_chapters", 0),
                "last_updated": status.get("last_updated", ""),
            }
        else:
            response["run_state"] = {}
    except Exception:  # noqa: BLE001
        response["run_state"] = {}

    serialized = _trim_overview_for_bytes(response)
    _OVERVIEW_CACHE[cache_key] = (now_s, cache_key, serialized)
    # Cap cache size — prune to last 8 universes worth of keys.
    if len(_OVERVIEW_CACHE) > 16:
        oldest = sorted(_OVERVIEW_CACHE.items(), key=lambda kv: kv[1][0])[:8]
        for k, _ in oldest:
            _OVERVIEW_CACHE.pop(k, None)
    return serialized


_VALID_TIER_KEYS = frozenset({
    "external_requests", "goal_pool", "paid_bids", "opportunistic",
})
_TIER_KEY_TO_CONFIG_FIELD = {
    "external_requests": "accept_external_requests",
    "goal_pool": "accept_goal_pool",
    "paid_bids": "accept_paid_bids",
    "opportunistic": "allow_opportunistic",
}


def _action_set_tier_config(
    universe_id: str = "",
    tier: str = "",
    enabled: bool = False,
    **_kwargs: Any,
) -> str:
    """Phase H: persist a tier toggle into ``dispatcher_config.yaml``.

    Takes effect at the next dispatcher cycle (R2 invariant 3);
    in-flight tasks complete normally. Round-trips YAML so other
    config fields are preserved.
    """
    import yaml as _yaml

    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})

    tier_name = (tier or "").strip().lower()
    if tier_name not in _VALID_TIER_KEYS:
        return json.dumps({
            "status": "rejected",
            "error": "unknown_tier",
            "available_tiers": sorted(_VALID_TIER_KEYS),
        })

    field_name = _TIER_KEY_TO_CONFIG_FIELD[tier_name]
    cfg_path = udir / "dispatcher_config.yaml"
    existing: dict[str, Any] = {}
    try:
        # Link-free and alias-free: a refusal rejects rather than reading as
        # empty, which the write below would then replace the file with.
        raw_cfg = read_data_path(cfg_path, max_bytes=MAX_CONFIG_BYTES)
        if raw_cfg is not None:
            loaded = load_untrusted_yaml(raw_cfg.decode("utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
    except Exception as exc:  # noqa: BLE001
        return json.dumps({
            "status": "rejected",
            "error": f"config_corrupt: {exc}",
        })

    existing[field_name] = bool(enabled)

    try:
        _ensure_center_dir(udir)
        write_data_path(
            cfg_path,
            _yaml.safe_dump(existing, sort_keys=True, default_flow_style=False),
        )
    except OSError as exc:
        return json.dumps({
            "status": "rejected",
            "error": f"config_write_failed: {exc}",
        })

    # Invalidate the overview cache for this universe so the next
    # `daemon_overview` reflects the change immediately (tests rely
    # on this; production clients also benefit).
    for key in list(_OVERVIEW_CACHE.keys()):
        if key.startswith(f"{uid}::"):
            _OVERVIEW_CACHE.pop(key, None)

    return json.dumps({
        "universe_id": uid,
        "status": "ok",
        "tier": tier_name,
        "enabled": bool(enabled),
        "takes_effect": "next_dispatcher_cycle",
    })


def _action_queue_cancel(
    universe_id: str = "",
    branch_task_id: str = "",
    **_kwargs: Any,
) -> str:
    """Cancel a BranchTask.

    Pending: hard-marks ``cancelled`` via ``mark_status``.
    Running: cooperative cancel — sets ``cancel_requested=True`` so
    the daemon's stream loop observes the flag at the next
    inter-node event and finalizes as ``cancelled``. Authorization:
    the task's ``claimed_by`` daemon (self-cancel) OR host identity.
    Other actors get ``cancel_not_authorized``.
    """
    from tinyassets.branch_tasks import (
        mark_status,
        read_queue,
        request_task_cancel,
    )

    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})
    if not branch_task_id:
        return json.dumps({"error": "branch_task_id required."})

    try:
        queue = read_queue(udir)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"Failed to read queue: {exc}"})

    target = next(
        (t for t in queue if t.branch_task_id == branch_task_id),
        None,
    )
    if target is None:
        return json.dumps({
            "universe_id": uid,
            "status": "not_found",
            "branch_task_id": branch_task_id,
        })
    if target.status == "running":
        from tinyassets.api.engine_helpers import _current_actor

        source = _current_actor()
        is_owner = bool(target.claimed_by) and source == target.claimed_by
        can_cancel = _env_actor_can(
            ACTION_CANCEL_BRANCH_TASK,
            universe_id=uid,
        )
        if not (can_cancel or is_owner):
            return json.dumps({
                "universe_id": uid,
                "status": "rejected",
                "error": "cancel_not_authorized",
                "branch_task_id": branch_task_id,
                "hint": (
                    "Running-task cancel requires the claiming daemon "
                    f"or the {ACTION_CANCEL_BRANCH_TASK!r} capability "
                    f"in {ENV_CAPABILITIES_VAR}."
                ),
            })
        try:
            ok = request_task_cancel(udir, branch_task_id)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Failed to request cancel: {exc}"})
        if not ok:
            # Race: task reached terminal between read_queue and now.
            return json.dumps({
                "universe_id": uid,
                "status": "rejected",
                "error": "task_already_terminal",
                "branch_task_id": branch_task_id,
            })
        return json.dumps({
            "universe_id": uid,
            "status": "cancel_requested",
            "branch_task_id": branch_task_id,
            "note": (
                "cooperative cancel — observed at next inter-node "
                "event; daemon finalizes as cancelled"
            ),
        })
    if target.status != "pending":
        return json.dumps({
            "universe_id": uid,
            "status": target.status,
            "branch_task_id": branch_task_id,
            "note": "task is already in a terminal state",
        })

    try:
        mark_status(udir, branch_task_id, status="cancelled")
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"Failed to cancel: {exc}"})

    return json.dumps({
        "universe_id": uid,
        "status": "cancelled",
        "branch_task_id": branch_task_id,
    })


def _goal_pool_not_available() -> str:
    return json.dumps({
        "status": "not_available",
        "hint": "TINYASSETS_GOAL_POOL=on required",
    })


def _action_subscribe_goal(
    universe_id: str = "",
    goal_id: str = "",
    **_kwargs: Any,
) -> str:
    from tinyassets.producers.goal_pool import goal_pool_enabled
    from tinyassets.subscriptions import subscribe

    if not goal_pool_enabled():
        return _goal_pool_not_available()
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})
    if not goal_id:
        return json.dumps({"error": "goal_id required."})
    try:
        goals = subscribe(udir, goal_id)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"subscribe failed: {exc}"})
    return json.dumps({
        "universe_id": uid,
        "goal_id": goal_id,
        "status": "subscribed",
        "goals": goals,
    })


def _action_unsubscribe_goal(
    universe_id: str = "",
    goal_id: str = "",
    **_kwargs: Any,
) -> str:
    from tinyassets.producers.goal_pool import goal_pool_enabled
    from tinyassets.subscriptions import unsubscribe

    if not goal_pool_enabled():
        return _goal_pool_not_available()
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})
    if not goal_id:
        return json.dumps({"error": "goal_id required."})
    try:
        goals = unsubscribe(udir, goal_id)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"unsubscribe failed: {exc}"})
    return json.dumps({
        "universe_id": uid,
        "goal_id": goal_id,
        "status": "unsubscribed",
        "goals": goals,
    })


def _action_list_subscriptions(
    universe_id: str = "",
    **_kwargs: Any,
) -> str:
    """List subscriptions + drift detection + per-goal pool counts.

    Preflight §4.1 #4 drift flag values:
      - "ok"
      - "pool_enabled_no_subs"  (F on + accept_goal_pool=true + zero subs)
      - "subs_but_pool_disabled" (subs exist + accept_goal_pool=false)
    """
    from tinyassets.dispatcher import load_dispatcher_config
    from tinyassets.producers.goal_pool import (
        POOL_DIRNAME,
        goal_pool_enabled,
        repo_root_path,
    )
    from tinyassets.subscriptions import list_subscriptions as _list

    if not goal_pool_enabled():
        return _goal_pool_not_available()
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})

    try:
        goals = _public_subscription_refs(_list(udir))
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"list_subscriptions failed: {exc}"})

    # Per-goal pool counts.
    counts: dict[str, int] = {g: 0 for g in goals}
    try:
        repo_root = repo_root_path(udir)
        pool_root = repo_root / POOL_DIRNAME
        for g in goals:
            gdir = pool_root / g
            if gdir.is_dir():
                counts[g] = sum(1 for _ in gdir.glob("*.yaml"))
    except RuntimeError:
        # repo_root unresolvable — counts stay zero
        pass

    cfg = load_dispatcher_config(udir)
    if cfg.accept_goal_pool and not goals:
        drift = "pool_enabled_no_subs"
    elif goals and not cfg.accept_goal_pool:
        drift = "subs_but_pool_disabled"
    else:
        drift = "ok"

    return json.dumps({
        "universe_id": uid,
        "goals": goals,
        "pool_status_per_goal": counts,
        "config_vs_subscriptions_drift": drift,
    })


def _action_post_to_goal_pool(
    universe_id: str = "",
    goal_id: str = "",
    branch_def_id: str = "",
    inputs_json: str = "",
    priority_weight: float = 0.0,
    **_kwargs: Any,
) -> str:
    """Write a pool YAML to ``<repo_root>/goal_pool/<goal_id>/<id>.yaml``.

    Response includes a ``next_step`` hint for cross-host visibility
    (git add/commit/push).
    """
    from tinyassets.producers.goal_pool import (
        goal_pool_enabled,
        repo_root_path,
        validate_pool_task_inputs,
        write_pool_post,
    )

    if not goal_pool_enabled():
        return _goal_pool_not_available()
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})
    if not goal_id:
        return json.dumps({"error": "goal_id required."})
    if not branch_def_id:
        return json.dumps({"error": "branch_def_id required."})

    # Parse inputs_json. Empty string → {}.
    if inputs_json.strip():
        try:
            inputs = json.loads(inputs_json)
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"inputs_json invalid JSON: {exc}"})
    else:
        inputs = {}
    ok, reason = validate_pool_task_inputs(inputs)
    if not ok:
        return json.dumps({
            "status": "rejected",
            "error": f"invalid_inputs: {reason}",
        })

    # priority_weight clamp per Phase E invariant 9 (extended to pool posts).
    try:
        pw = float(priority_weight)
    except (TypeError, ValueError):
        pw = 0.0
    if pw < 0:
        return json.dumps({
            "status": "rejected",
            "error": "priority_weight must be >= 0.",
        })
    from tinyassets.api.engine_helpers import _current_actor

    source = _current_actor()
    if not _env_actor_can(ACTION_POST_PRIORITY_GOAL_POOL, universe_id=uid):
        pw = 0.0

    try:
        repo_root = repo_root_path(udir)
    except RuntimeError as exc:
        return json.dumps({
            "status": "rejected",
            "error": "repo_root_not_resolvable",
            "hint": (
                "Set TINYASSETS_REPO_ROOT or run the daemon from inside "
                "a git checkout. Detail: " + str(exc)
            ),
        })

    try:
        out_path = write_pool_post(
            repo_root,
            goal_id,
            branch_def_id=branch_def_id,
            inputs=inputs,
            priority_weight=pw,
            posted_by=source,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"post failed: {exc}"})

    rel_path = out_path.relative_to(repo_root) if out_path.is_relative_to(
        repo_root,
    ) else out_path
    return json.dumps({
        "universe_id": uid,
        "status": "posted",
        "goal_id": goal_id,
        "branch_def_id": branch_def_id,
        "path": str(out_path),
        "priority_weight": pw,
        "next_step": (
            f"To make this post visible to cross-host subscribers, run: "
            f"git add {rel_path} && git commit && git push"
        ),
    })


def _paid_market_not_available() -> str:
    return json.dumps({
        "status": "not_available",
        "hint": "TINYASSETS_PAID_MARKET=on required",
    })


def _action_submit_node_bid(
    universe_id: str = "",
    node_def_id: str = "",
    required_llm_type: str = "",
    inputs_json: str = "",
    bid: float = 0.0,
    **_kwargs: Any,
) -> str:
    """Phase G: write a NodeBid YAML to ``<repo_root>/bids/<id>.yaml``.

    Flag-gated on ``TINYASSETS_PAID_MARKET=on``. Flat-dict inputs only.
    Response includes a ``next_step`` git push hint, mirroring
    ``post_to_goal_pool``.
    """
    from tinyassets.bid.node_bid import (
        new_node_bid_id,
        validate_node_bid_inputs,
        write_node_bid_post,
    )
    from tinyassets.producers.goal_pool import repo_root_path
    from tinyassets.producers.node_bid import paid_market_enabled

    if not paid_market_enabled():
        return _paid_market_not_available()
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})
    if not node_def_id:
        return json.dumps({"error": "node_def_id required."})

    if inputs_json.strip():
        try:
            inputs = json.loads(inputs_json)
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"inputs_json invalid JSON: {exc}"})
    else:
        inputs = {}
    ok, reason = validate_node_bid_inputs(inputs)
    if not ok:
        return json.dumps({
            "status": "rejected",
            "error": f"invalid_inputs: {reason}",
        })

    try:
        bid_value = float(bid)
    except (TypeError, ValueError):
        return json.dumps({
            "status": "rejected",
            "error": "bid must be numeric",
        })
    if bid_value < 0:
        return json.dumps({
            "status": "rejected",
            "error": "bid must be >= 0",
        })

    try:
        repo_root = repo_root_path(udir)
    except RuntimeError as exc:
        return json.dumps({
            "status": "rejected",
            "error": "repo_root_not_resolvable",
            "hint": (
                "Set TINYASSETS_REPO_ROOT or run the daemon from inside "
                "a git checkout. Detail: " + str(exc)
            ),
        })

    from tinyassets.api.engine_helpers import _current_actor

    source = _current_actor()
    node_bid_id = new_node_bid_id()
    payload = {
        "node_bid_id": node_bid_id,
        "node_def_id": node_def_id,
        "required_llm_type": required_llm_type or "",
        "inputs": dict(inputs),
        "bid": bid_value,
        "submitted_by": source,
        "status": "open",
        "evidence_url": "",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        out_path = write_node_bid_post(repo_root, payload)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"post failed: {exc}"})

    rel_path = (
        out_path.relative_to(repo_root)
        if out_path.is_relative_to(repo_root)
        else out_path
    )
    return json.dumps({
        "universe_id": uid,
        "status": "posted",
        "node_bid_id": node_bid_id,
        "node_def_id": node_def_id,
        "path": str(out_path),
        "bid": bid_value,
        "required_llm_type": required_llm_type or "",
        "next_step": (
            f"To make this bid visible to cross-host daemons, run: "
            f"git add {rel_path} && git commit && git push"
        ),
    })


def _action_give_direction(
    universe_id: str = "",
    text: str = "",
    category: str = "direction",
    target: str = "",
    anchor_json: str = "",
    **_kwargs: Any,
) -> str:
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})

    valid_categories = {"direction", "protect", "concern", "observation", "error"}
    if category not in valid_categories:
        category = "direction"

    try:
        from tinyassets.notes import add_note as _add_note

        anchor: dict[str, Any] = {}
        if anchor_json.strip():
            parsed_anchor = json.loads(anchor_json)
            if not isinstance(parsed_anchor, dict):
                return json.dumps({"error": "anchor_json must be a JSON object."})
            anchor = parsed_anchor

        _ensure_center_dir(udir)
        note = _add_note(
            udir,
            source="user",
            text=text,
            category=category,
            target=target or None,
            anchor=anchor,
        )
        return json.dumps({
            "universe_id": uid,
            "note_id": note.id,
            "category": category,
            "target": note.target,
            "anchor": note.anchor,
            "status": "written",
            "note": "Direction delivered. The daemon reads notes at scene boundaries.",
        })
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Invalid anchor_json: {exc.msg}"})
    except Exception as exc:
        return json.dumps({"error": f"Failed to add note: {exc}"})


def _action_query_world(
    universe_id: str = "",
    query_type: str = "facts",
    filter_text: str = "",
    limit: int = 20,
    **_kwargs: Any,
) -> str:
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})

    if query_type == "characters":
        data = _read_json(udir / "characters.json")
    elif query_type == "promises":
        data = _read_json(udir / "promises.json")
    elif query_type == "timeline":
        data = _read_json(udir / "timeline.json")
    else:
        data = _read_json(udir / "facts.json")

    if data is None:
        return _query_world_db(udir, uid, query_type, filter_text, limit)

    if isinstance(data, list) and filter_text:
        lower_filter = filter_text.lower()
        data = [
            item for item in data
            if lower_filter in json.dumps(item, default=str).lower()
        ]

    if isinstance(data, list):
        data = data[:limit]

    return json.dumps({
        "universe_id": uid,
        "query_type": query_type,
        "results": data,
        "count": len(data) if isinstance(data, list) else 1,
    }, default=str)


def _query_world_db(
    udir: Path, uid: str, query_type: str, filter_text: str, limit: int,
) -> str:
    """Query the SQLite world-state stores.

    World state is split across two DBs today:
      - story.db       — extracted_facts, character_states, promises
                         (written by the commit pipeline)
      - knowledge.db   — entities, facts, edges, communities
                         (written by the KG pipeline)

    A single `query_type` may live in either DB. We probe known candidate
    (db, table) pairs in priority order and return the first one that
    contains data. This is the source-of-truth routing — previous versions
    of this function pointed `characters -> entities` in story.db, which
    never existed and surfaced to users as "entities table missing". The
    actual character data is in story.db::character_states.
    """
    # Each entry: (db_filename, table_name). Order is priority: first table
    # that exists AND has rows wins. If none have rows, the first table that
    # exists is queried (possibly returning an empty list but not "missing").
    SOURCES: dict[str, list[tuple[str, str]]] = {
        "facts": [
            # commit-pipeline facts (richest: 282 rows on sporemarch)
            ("story.db", "extracted_facts"),
            # KG-native facts (written by the knowledge pipeline)
            ("knowledge.db", "facts"),
        ],
        "characters": [
            ("story.db", "character_states"),
            ("knowledge.db", "entities"),
        ],
        "promises": [
            ("story.db", "promises"),
        ],
        "timeline": [
            # No timeline table exists in either DB today. Reserved for a
            # future world-state pipeline; surfaces as "not recorded yet"
            # rather than "missing".
        ],
    }

    candidates = SOURCES.get(query_type, SOURCES["facts"])
    if not candidates:
        return json.dumps({
            "universe_id": uid,
            "query_type": query_type,
            "results": [],
            "count": 0,
            "note": (
                f"No store for query_type='{query_type}' yet. "
                "Valid types: facts, characters, promises."
            ),
        })

    import sqlite3

    # Pick the first (db, table) pair that exists. Prefer ones with data.
    chosen: tuple[str, str] | None = None
    fallback_empty: tuple[str, str] | None = None
    checked: list[str] = []
    for dbname, table in candidates:
        db_path = udir / dbname
        checked.append(f"{dbname}::{table}")
        if not db_path.exists():
            continue
        try:
            probe = sqlite3.connect(str(db_path))
            try:
                row = probe.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if not row:
                    continue
                count_row = probe.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()
                has_rows = count_row and count_row[0] > 0
            finally:
                probe.close()
        except sqlite3.Error:
            continue
        if has_rows:
            chosen = (dbname, table)
            break
        if fallback_empty is None:
            fallback_empty = (dbname, table)

    if chosen is None and fallback_empty is None:
        return json.dumps({
            "universe_id": uid,
            "query_type": query_type,
            "results": [],
            "count": 0,
            "note": (
                f"World-state not initialized for query_type='{query_type}'. "
                f"Checked: {', '.join(checked)}."
            ),
        })

    dbname, table = chosen or fallback_empty  # type: ignore[misc]
    db_path = udir / dbname

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if filter_text:
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [row["name"] for row in cursor.fetchall()]
            text_cols = [c for c in columns if c not in ("id", "rowid")]

            where_parts = [f"{c} LIKE ?" for c in text_cols]
            where_clause = " OR ".join(where_parts) if where_parts else "1=1"
            params = [f"%{filter_text}%" for _ in text_cols]

            cursor.execute(
                f"SELECT * FROM {table} WHERE {where_clause} LIMIT ?",
                params + [limit],
            )
        else:
            cursor.execute(f"SELECT * FROM {table} LIMIT ?", (limit,))

        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()

        response: dict[str, Any] = {
            "universe_id": uid,
            "query_type": query_type,
            "results": rows,
            "count": len(rows),
            "source": f"{dbname}::{table}",
        }
        if not rows and chosen is None:
            response["note"] = (
                f"Table '{table}' exists in {dbname} but has no rows yet."
            )
        return json.dumps(response, default=str)

    except Exception as exc:
        return json.dumps({"error": f"DB query failed: {exc}"})


def _action_read_premise(universe_id: str = "", **_kwargs: Any) -> str:
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    content = _normalize_escaped_text(read_legacy_premise(udir))
    source = "PROGRAM.md"
    if not content.strip():
        soul = read_universe_soul(udir)
        content = soul.purpose if soul is not None else ""
        source = SOUL_FILENAME
    else:
        soul = read_universe_soul(udir)

    if not content.strip():
        return json.dumps({
            "universe_id": uid,
            "premise": None,
            "has_soul": soul is not None,
            "soul": soul.summary() if soul is not None else None,
            "note": (
                "No premise set. Tell the command center its premise through "
                "converse."
            ),
        })
    return json.dumps({
        "universe_id": uid,
        "premise": content,
        "source": source,
        "has_soul": soul is not None,
        "soul": soul.summary() if soul is not None else None,
    })


def _normalize_escaped_text(text: str) -> str:
    """Convert literal escape sequences (``\\n``, ``\\r``, ``\\t``) to real
    characters when the input looks accidentally double-encoded.

    Some MCP clients transmit multi-line strings as JSON string literals
    and the receiving end sees the escape sequences verbatim. Premise
    markdown is prose — writers essentially never want the 2-character
    literal ``\\n`` sequence in that prose. A text with literal ``\\n``
    sequences is therefore treated as double-encoded, even if it also has
    one or two real newlines (e.g. a trailing file-end newline). If a
    future caller legitimately needs the literal 2-char sequence, they
    can double-escape as ``\\\\n``.
    """
    if not text:
        return text
    if "\\n" not in text and "\\r" not in text and "\\t" not in text:
        return text
    return (
        text
        .replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\r", "\n")
        .replace("\\t", "\t")
    )


def _action_set_premise(universe_id: str = "", text: str = "", **_kwargs: Any) -> str:
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)

    if not text.strip():
        return json.dumps({"error": "Premise text cannot be empty."})
    text = _normalize_escaped_text(text)
    try:
        _ensure_center_dir(udir)
        soul = write_universe_soul(
            udir, purpose=text, lineage="created-from-premise",
        )
        write_data_path(legacy_premise_path(udir), text)
        return json.dumps({
            "universe_id": uid,
            "status": "updated",
            "has_soul": True,
            "soul": soul.summary(),
            "note": "Premise saved. The daemon will read it at next startup.",
        })
    except OSError as exc:
        return json.dumps({"error": f"Failed to write premise: {exc}"})


#: The levels this owner-facing verb OFFERS — deliberately narrower than
#: ``visibility.LEVELS``. A level is a promise, and a promise no reader enforces
#: is decoration: six universe read actions that return raw content
#: (``get_activity``, ``read_premise``, ``read_canon``, ``read_source``,
#: ``read_output``, ``query_world``) are gated only by the legacy ``public_read``
#: bit through ``_universe_acl_error``, and ``set_universe_visibility`` sets that
#: bit for ANY level granting a visitor a capability. So `metadata_only` (which
#: promises to withhold content) and `unlisted` (which promises to withhold
#: metadata) would both be mis-served. Offering only the two the platform enforces
#: end to end keeps this surface honest; the gap is
#: `docs/concerns/2026-09-26-content-readers-gate-on-the-legacy-bit.md`, and when
#: it closes the other two levels belong here.
#:
#: This is also exactly the binary the founder described on 2026-09-26 — private
#: unless the owner makes it accessible — rather than a refinement nobody asked for.
_OFFERED_VISIBILITY_LEVELS = frozenset({"private", "public"})


def _action_set_universe_visibility(
    universe_id: str = "", visibility: str = "", **_kwargs: Any
) -> str:
    """Change a command center's declared visibility — the owner's exposure decision.

    A command center is born `private` (founder, 2026-09-26: nothing in a user's
    command center is visible, accessible or interactable to another user unless its
    owner exposed it). This is the only way it stops being private, and it is the
    reason private-by-default is a boundary rather than a wall: before this
    action existed, `set_universe_visibility` had no production caller outside
    the creation path and the boot backfill, so an owner could not publish at
    all.

    Authority: OWNER-only, which is strictly narrower than write. Registration in
    ``WRITE_ACTIONS`` makes ``_universe_acl_error`` demand write access and makes
    the dispatcher ledger the decision — necessary, and not sufficient. That gate
    accepts ``write`` OR ``admin`` (``permissions._WRITE_PERMISSIONS``), so relying
    on it alone let a delegated *writer* publish someone else's command center and have
    it recorded as the owner's choice (Codex cross-family review of PR #4019,
    reproduced end-to-end: `status=updated`, `chosen_by=owner`, and the migration
    then classified that command center as owner-chosen and left it public).

    Exposing a command center to other users is not an editing operation, so it takes
    the canonical per-universe ownership predicate — ``universe_owner_actor``,
    the explicit ``admin`` ACL row, the same signal ``connect_llm``,
    ``source_channel`` and the pending-request rail use. This is a narrowing on
    top of the central gate, not a second copy of it: the ACL check still runs
    first and this only ever refuses more.
    """
    from tinyassets.api import visibility as _visibility
    from tinyassets.api.source_channel import universe_owner_actor
    from tinyassets.principals import named_principal

    offered = _OFFERED_VISIBILITY_LEVELS
    uid = _request_universe(universe_id)
    if not _universe_dir(uid).is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})

    actor = named_principal(permissions.current_actor_id())
    if not actor or not universe_owner_actor(_base_path(), uid, actor):
        # The SAME envelope the central ACL gate returns for a non-writer, so a
        # delegated writer learns exactly what a reader learns.
        return json.dumps(permissions.universe_access_error(
            universe_id=uid, write=True, action="set_visibility",
            surface="universe",
        ))

    requested = (visibility or "").strip()
    if not requested:
        return json.dumps({
            "error": f"visibility is required; expected one of {sorted(offered)}.",
        })
    if requested not in offered:
        known = _visibility.parse_level(requested) is not None
        return json.dumps({
            "error": (
                f"visibility {requested!r} is not offered; expected one of "
                f"{sorted(offered)}."
            ),
            "reason": "level_not_enforced" if known else "unknown_level",
            "detail": (
                f"{requested!r} is a real level, but the platform does not yet "
                "enforce its content boundary on every reader, so this surface "
                "does not offer it (docs/concerns/"
                "2026-09-26-content-readers-gate-on-the-legacy-bit.md)."
            ) if known else "",
        })
    previous = _visibility.declared_level_name(uid)
    try:
        resolved = _visibility.set_universe_visibility(
            uid, requested, source=_visibility.LEVEL_SOURCE_OWNER
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({
        "universe_id": uid,
        "status": "updated",
        "visibility": resolved.name,
        "previous_visibility": previous,
        "chosen_by": "owner",
        "capabilities": {
            cap: resolved.permits(cap) for cap in _visibility.CAPABILITIES
        },
        "note": (
            f"'{uid}' is now {resolved.name}. "
            + (
                "Other users can see it to the extent that level allows; you "
                "and anyone you granted access keep full access either way."
                if resolved is not _visibility.PRIVATE
                else "No other user can discover, inspect or read it. People "
                "you granted access to keep full access."
            )
        ),
    })


_CANON_SAME_FILENAME_BEHAVIOR = (
    "A later ingest of the same canon-source filename replaces the stored "
    "source bytes and manifest entry when the content hash changes; identical "
    "bytes are treated as unchanged."
)


def _canon_source_operation(canon_dir: Path, filename: str, data: bytes) -> str:
    from tinyassets.ingestion.core import SourceManifest

    existing = SourceManifest.load(canon_dir).get(filename)
    if existing is None:
        return "created"
    if existing.sha256 == sha256(data).hexdigest():
        return "unchanged"
    return "replaced"


def _canon_version_semantics(filename: str, routed_to: str) -> dict[str, Any]:
    identity = f"canon/{filename}"
    if routed_to == "sources":
        identity = f"canon/sources/{filename}"
    return {
        "mode": "filename_upsert",
        "identity": identity,
        "same_filename_behavior": _CANON_SAME_FILENAME_BEHAVIOR,
        "history_retained": False,
        "supersede_supported": False,
        "deprecate_supported": False,
    }


def _action_add_canon(
    universe_id: str = "",
    filename: str = "",
    text: str = "",
    provenance_tag: str = "",
    **_kwargs: Any,
) -> str:
    """Add inline canon text.

    Memory-scope Stage 2b landed the ``synthesize_source`` signal as the
    trigger for premise/canon/entity synthesis. This path now routes
    through :func:`tinyassets.ingestion.core.ingest_file` so the signal
    fires (the earlier direct-write path bypassed it, breaking MCP
    uploads). Files still land under ``canon/sources/`` on user
    uploads; the daemon's enrich phase picks up the signal and
    synthesizes canon from the source.
    """
    from tinyassets.api.engine_helpers import _current_actor
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    canon_dir = udir / "canon"

    safe_name = Path(filename).name
    if not safe_name:
        return json.dumps({"error": "Invalid filename."})

    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        return json.dumps({"error": f"Failed to encode text as UTF-8: {exc}"})

    from tinyassets.ingestion.core import ingest_file

    try:
        canon_dir.mkdir(parents=True, exist_ok=True)
        source_operation = _canon_source_operation(canon_dir, safe_name, data)
        result = ingest_file(
            canon_dir=canon_dir,
            filename=safe_name,
            data=data,
            universe_path=udir,
            user_upload=True,
        )

        if provenance_tag:
            # Resolve + contain the sidecar meta path before write so a
            # crafted ``safe_name`` cannot clobber a file outside canon_dir.
            safe_canon_path(
                canon_dir, f".{safe_name}.meta.json", kind="meta sidecar"
            )
            meta = {
                "provenance": provenance_tag,
                "added": datetime.now(timezone.utc).isoformat(),
                "source": _current_actor(),
            }
            write_data_path(canon_dir / f".{safe_name}.meta.json", json.dumps(meta))

        return json.dumps({
            "universe_id": uid,
            "filename": safe_name,
            "status": "written",
            "provenance": provenance_tag or "untagged",
            "routed_to": result.routed_to,
            "bytes_written": result.byte_count,
            "synthesis_signal_emitted": result.signal_emitted,
            "source_operation": source_operation,
            "version_semantics": _canon_version_semantics(
                safe_name, result.routed_to,
            ),
            "note": (
                "Canon file ingested via ingest_file(). The daemon will "
                "pick up the synthesize_source signal on its next cycle."
            ),
        })
    except OSError as exc:
        return json.dumps({"error": f"Failed to write canon file: {exc}"})


def _action_list_canon(
    universe_id: str = "",
    **_kwargs: Any,
) -> str:
    """List all canon documents in a command center with metadata."""
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    canon_dir = udir / "canon"

    if not canon_dir.is_dir():
        return json.dumps({"universe_id": uid, "canon_files": [], "note": "No canon directory."})

    files = []
    # Enumeration is routed through ``iter_canon_files`` so each entry is
    # resolved + contained before ``stat``; a symlinked canon file escaping
    # canon_dir is skipped. Dotfiles (markers, manifests, meta sidecars) are
    # excluded from the listing.
    for f in iter_canon_files(canon_dir, include_hidden=False):
        entry: dict[str, Any] = {
            "filename": f.name,
            "size_bytes": f.stat().st_size,
        }
        # Check for provenance metadata. ``f.name`` is a contained basename,
        # so the sidecar still resolves under canon_dir; contain it anyway.
        meta = _canon_json(canon_dir, f".{f.name}.meta.json")
        if meta:
            entry["provenance"] = meta.get("provenance", "")
            entry["added"] = meta.get("added", "")
            entry["source"] = meta.get("source", "")
        files.append(entry)

    return json.dumps({"universe_id": uid, "canon_files": files, "count": len(files)})


def _canon_json(canon_dir: Path, name: str) -> dict[str, Any]:
    """A canon dotfile (meta sidecar, manifest) as a dict; ``{}`` when absent
    or not a JSON object.

    Read by its LEXICAL path with no link followed: ``safe_canon_path``
    resolves against the resolved canon dir, which a ``canon -> /data/<other>``
    link moves, so a sidecar read through it would return another universe's
    provenance. A refused read raises rather than reading as ``{}``.
    """
    if "/" in name or "\\" in name or name in ("", ".", ".."):
        return {}
    raw = read_data_path(canon_dir / name, max_bytes=MAX_PLATFORM_FILE_BYTES)
    if raw is None:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _source_sidecar_meta(canon_dir: Path, filename: str) -> dict[str, Any]:
    return _canon_json(canon_dir, f".{filename}.meta.json")


def _manifest_data(canon_dir: Path) -> dict[str, Any]:
    return _canon_json(canon_dir, ".manifest.json")


def _source_file_entry(
    path: Path,
    canon_dir: Path,
    manifest: dict[str, Any],
    raw: bytes | None = None,
) -> dict[str, Any]:
    stat = path.stat()
    meta = _source_sidecar_meta(canon_dir, path.name)
    manifest_entry = manifest.get(path.name, {})
    if not isinstance(manifest_entry, dict):
        manifest_entry = {}
    synthesized_docs = manifest_entry.get("synthesized_docs", [])
    if not isinstance(synthesized_docs, list):
        synthesized_docs = []
    manifest_sha = manifest_entry.get("sha256", "")
    current_sha = hashlib.sha256(raw).hexdigest() if raw is not None else manifest_sha

    entry: dict[str, Any] = {
        "filename": path.name,
        "source_path": f"sources/{path.name}",
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc,
        ).isoformat(),
        "sha256": current_sha,
        "provenance": meta.get("provenance", ""),
        "added": meta.get("added", ""),
        "source": meta.get("source", ""),
        "original_source_path": meta.get("source_path", ""),
        "file_type": manifest_entry.get("file_type", "unknown"),
        "mime_type": manifest_entry.get("mime_type", ""),
        "manifest_sha256": manifest_sha,
        "ingested_at": manifest_entry.get("ingested_at", ""),
        "synthesized_docs": synthesized_docs,
        "synthesis_complete": bool(synthesized_docs),
        "synthesis_failed": bool(manifest_entry.get("synthesis_failed")),
    }
    if isinstance(manifest_entry.get("last_bite_outcomes"), dict):
        entry["last_bite_outcomes"] = manifest_entry["last_bite_outcomes"]
    return entry


def _action_list_sources(
    universe_id: str = "",
    **_kwargs: Any,
) -> str:
    """List uploaded source documents with attestation metadata."""
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    canon_dir = udir / "canon"
    sources_dir = canon_dir / "sources"

    if not sources_dir.is_dir():
        return json.dumps({
            "universe_id": uid,
            "source_files": [],
            "source_count": 0,
            "note": "No canon/sources directory.",
        })

    manifest = _manifest_data(canon_dir)
    source_files: list[dict[str, Any]] = []
    # Enumeration is routed through ``iter_canon_files`` with the canon ROOT as
    # the containment root and ``subdir="sources"`` as the listing target, so
    # the ``sources/`` dir is itself resolved + contained first. A *symlinked*
    # ``sources/`` (or a symlinked source file inside it) that escapes canon is
    # rejected; rooting containment at ``sources/`` directly would let a
    # symlinked subdir become its own trusted root.
    try:
        for path in iter_canon_files(
            canon_dir, subdir="sources", include_hidden=False
        ):
            source_files.append(_source_file_entry(path, canon_dir, manifest))
    except OSError as exc:
        return json.dumps({"error": f"Failed to list source files: {exc}"})

    return json.dumps({
        "universe_id": uid,
        "source_files": source_files,
        "source_count": len(source_files),
    })


def _action_read_source(
    universe_id: str = "",
    filename: str = "",
    limit: int = 30,
    **_kwargs: Any,
) -> str:
    """Read one uploaded source document verbatim with checksum metadata."""
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    canon_dir = udir / "canon"

    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        return json.dumps({
            "error": (
                "Filename required. Source enumeration is not exposed by the "
                "advertised handles."
            ),
        })

    # Resolve + contain against the canon ROOT (not the ``sources/`` subdir) so
    # a symlinked ``sources/`` directory cannot become its own trusted root and
    # expose files outside canon. ``sources/<name>`` round-trips through the
    # containment check against ``canon_dir``; a ``../`` traversal or a
    # symlinked ``sources/`` / source entry that escapes canon is rejected.
    try:
        target = safe_canon_path(
            canon_dir, f"sources/{safe_name}", kind="source file"
        )
    except ValueError:
        return json.dumps({"error": "Path traversal not allowed."})
    if not target.is_file():
        return json.dumps({
            "error": f"Source file '{safe_name}' not found.",
            "hint": (
                "Source enumeration is not exposed by the advertised handles."
            ),
        })

    try:
        raw = read_canon_bytes(canon_dir, f"sources/{safe_name}", kind="source file")
        content = raw.decode("utf-8")
        manifest = _manifest_data(canon_dir)
        entry = _source_file_entry(target, canon_dir, manifest, raw=raw)
    except UnicodeDecodeError as exc:
        return json.dumps({
            "error": (
                f"Source file '{safe_name}' is not valid UTF-8 "
                f"({exc.reason} at byte {exc.start})."
            ),
        })
    except OSError as exc:
        return json.dumps({"error": f"Failed to read source file: {exc}"})

    # ChatGPT's connector wrapper can stop rendering downstream writes after
    # several large source reads. Keep the default preview compact, while still
    # allowing an explicit larger preview through the existing MCP `limit` arg.
    preview_chars = 4000
    if limit and limit > 100:
        preview_chars = min(limit, 10000)

    truncated = len(content) > preview_chars
    entry.update({
        "universe_id": uid,
        "content": content[:preview_chars] if truncated else content,
        "truncated": truncated,
        "content_preview_chars": preview_chars,
        "next_action_hint": (
            "If the user requested a create or update, continue with that write "
            "action now; do not stop after reading sources."
        ),
    })
    if truncated:
        entry["total_chars"] = len(content)
        entry["note"] = (
            f"Source file preview truncated to {preview_chars} chars. "
            "Pass limit=10000 for the largest supported preview."
        )
    return json.dumps(entry)


def _action_read_canon(
    universe_id: str = "",
    filename: str = "",
    **_kwargs: Any,
) -> str:
    """Read the contents of a specific canon document."""
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    canon_dir = udir / "canon"

    safe_name = Path(filename).name
    if not safe_name:
        return json.dumps({
            "error": (
                "Filename required. Canon enumeration is not exposed by the "
                "advertised handles."
            ),
        })

    # Resolve + contain before any ``is_file`` / read so a symlinked canon
    # file whose target lives outside canon_dir is rejected, not read.
    try:
        target = safe_canon_path(canon_dir, safe_name, kind="canon file")
    except ValueError:
        return json.dumps({"error": "Path traversal not allowed."})
    if not target.is_file():
        return json.dumps({
            "error": f"Canon file '{safe_name}' not found.",
            "hint": "Canon enumeration is not exposed by the advertised handles.",
        })

    try:
        raw = read_canon_bytes(canon_dir, safe_name, kind="canon file")
        content = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        entry: dict[str, Any] = {
            "universe_id": uid,
            "filename": safe_name,
            "size_bytes": len(raw),
            "content": content,
        }
        # Attach provenance if available. ``safe_name`` is contained above, so
        # the sidecar still resolves under canon_dir; contain it anyway.
        meta = _canon_json(canon_dir, f".{safe_name}.meta.json")
        if meta:
            entry["provenance"] = meta.get("provenance", "")
        return json.dumps(entry)
    except OSError as exc:
        return json.dumps({"error": f"Failed to read canon file: {exc}"})


def _action_control_daemon(
    universe_id: str = "",
    text: str = "",
    **_kwargs: Any,
) -> str:
    action = text.strip().lower()
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})

    pause_path = udir / ".pause"

    if action == "pause":
        try:
            _ensure_center_dir(udir)
            write_data_path(pause_path, datetime.now(timezone.utc).isoformat())
            return json.dumps({
                "universe_id": uid,
                "action": "pause",
                "status": "signal_written",
                "note": "Daemon will pause at the next scene boundary.",
            })
        except OSError as exc:
            return json.dumps({"error": f"Failed to write pause signal: {exc}"})

    elif action == "resume":
        if not pause_path.exists():
            return json.dumps({
                "universe_id": uid,
                "action": "resume",
                "status": "not_paused",
                "note": "Daemon was not paused.",
            })
        try:
            pause_path.unlink()
            return json.dumps({
                "universe_id": uid,
                "action": "resume",
                "status": "resumed",
                "note": "Pause signal removed. Daemon will resume.",
            })
        except OSError as exc:
            return json.dumps({"error": f"Failed to remove pause: {exc}"})

    elif action == "status":
        status = _read_json(udir / "status.json")
        liveness = _daemon_liveness(
            udir, status if isinstance(status, dict) else None,
        )
        # Count pending unreconciled writes so host sees drift when a
        # git commit failed but SQLite accepted the write.
        try:
            pending = list_unreconciled_writes(_base_path(), limit=500)
            pending_count = len(pending)
        except Exception:
            pending_count = 0
        return json.dumps({
            "universe_id": uid,
            "action": "status",
            "phase": liveness["phase"],
            "phase_human": liveness["phase_human"],
            "is_paused": liveness["is_paused"],
            "has_premise": liveness["has_premise"],
            "has_work": liveness["has_work"],
            "last_activity_at": liveness["last_activity_at"],
            "staleness": liveness["staleness"],
            "word_count": liveness["word_count"],
            "word_count_sample": liveness["word_count_sample"],
            "accept_rate": liveness["accept_rate"],
            "accept_rate_sample": liveness["accept_rate_sample"],
            "unreconciled_writes_count": pending_count,
        })

    else:
        return json.dumps({
            "error": f"Unknown daemon action '{action}'. Use: pause, resume, status.",
        })


def _action_get_activity(
    universe_id: str = "",
    limit: int = 30,
    **_kwargs: Any,
) -> str:
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    log_path = udir / "activity.log"

    limit = min(max(limit, 1), 200)

    content = _read_text(log_path)
    if not content:
        return json.dumps({
            "universe_id": uid,
            "lines": [],
            "note": "No activity log found. The daemon may not have run yet.",
        })

    all_lines = content.strip().splitlines()
    tail = all_lines[-limit:]
    return json.dumps({
        "universe_id": uid,
        "lines": tail,
        "count": len(tail),
        "total": len(all_lines),
    })


# Pattern: "[2026-04-19 20:30:00] [dispatch_guard] message body" or
# legacy "[2026-04-19 20:30:00] untagged message". Lenient — any line
# that can't be parsed is surfaced in the 'raw' field so callers still
# see the source text when they need it.
_ACTIVITY_LINE_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\](?:\s*\[(?P<tag>[^\]]+)\])?\s*(?P<msg>.*)$"
)

# Domain caveat for dispatch_guard queries with zero matching events.
# dispatch_guard only emits when the daemon actively dispatches a scene;
# empty results could mean (a) no overshoots fired, OR (b) the daemon did
# not dispatch at all in this window (endpoint unbound, daemon paused,
# universe idle). Chatbot must not read empty-list as "no overshoots."
_DISPATCH_GUARD_ABSENCE_CAVEAT = (
    "Empty dispatch_guard list does not prove no overshoots — the daemon "
    "may not have dispatched any scenes in this window (endpoint unbound, "
    "daemon paused, or command center idle). Verify daemon ran before inferring "
    "'guard never needed to fire'."
)


def _parse_activity_line(line: str) -> dict[str, str]:
    """Split ``[TS] [TAG] MSG`` (or legacy ``[TS] MSG``) into fields.

    Returns dict with keys ``ts``, ``tag`` (empty when untagged),
    ``message``, ``raw``. Unparseable lines fall back to all-empty
    fields + ``raw`` holding the original string.
    """
    line = line.rstrip("\n")
    match = _ACTIVITY_LINE_RE.match(line)
    if not match:
        return {"ts": "", "tag": "", "message": "", "raw": line}
    return {
        "ts": match.group("ts") or "",
        "tag": match.group("tag") or "",
        "message": match.group("msg") or "",
        "raw": line,
    }


def _action_get_recent_events(
    universe_id: str = "",
    tag: str = "",
    limit: int = 30,
    **_kwargs: Any,
) -> str:
    """Tag-filterable view of activity.log for chatbot observability.

    Reads the command center's ``activity.log`` tail and returns entries as
    structured dicts (``ts`` / ``tag`` / ``message`` / ``raw``). When
    ``tag`` is non-empty, only entries whose tag starts with ``tag``
    are returned — tag prefix-match so a caller can filter ``"dispatch"``
    and get both ``dispatch_guard`` and ``dispatch_execution``.

    Evidence + caveat fields follow the self-auditing-tools pattern:
      - ``events``: matching structured entries (most recent first).
      - ``source``: ``"activity.log"`` — the audit surface backing the
        answer.
      - ``caveats``: list of strings explaining any observation caveats
        (e.g. "log file missing", "tag filter matched 0 of N entries").

    Args:
        universe_id: Target command center (falls back to default).
        tag: Optional tag prefix filter (empty = all entries).
        limit: Max entries to return (1..500, clamped).
    """
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    log_path = udir / "activity.log"

    limit = min(max(limit, 1), 500)
    caveats: list[str] = []

    content = _read_text(log_path)
    if not content:
        missing_caveats = [
            "No activity.log found. The daemon may not have run yet "
            "in this command center, or the log was cleared.",
        ]
        if tag == "dispatch_guard":
            missing_caveats.append(_DISPATCH_GUARD_ABSENCE_CAVEAT)
        return json.dumps({
            "universe_id": uid,
            "events": [],
            "source": "activity.log",
            "tag_filter": tag,
            "caveats": missing_caveats,
        })

    all_lines = content.strip().splitlines()
    parsed = [_parse_activity_line(line) for line in all_lines]

    if tag:
        matched = [p for p in parsed if p["tag"].startswith(tag)]
        if not matched:
            caveats.append(
                f"Tag filter {tag!r} matched 0 of {len(parsed)} entries. "
                f"Known tags in file: "
                f"{sorted({p['tag'] for p in parsed if p['tag']})[:10]}."
            )
            if tag == "dispatch_guard":
                caveats.append(_DISPATCH_GUARD_ABSENCE_CAVEAT)
    else:
        matched = parsed

    # Return most-recent first so chatbot readers see newest events at top.
    tail = matched[-limit:]
    events = list(reversed(tail))

    untagged_count = sum(1 for p in parsed if not p["tag"])
    if untagged_count and not tag:
        caveats.append(
            f"{untagged_count} of {len(parsed)} activity lines carry no tag "
            "(pre-tagging call sites or legacy entries)."
        )

    return json.dumps({
        "universe_id": uid,
        "events": events,
        "source": "activity.log",
        "tag_filter": tag,
        "total_lines": len(all_lines),
        "matched": len(matched),
        "returned": len(events),
        "caveats": caveats,
    })


def _action_get_ledger(universe_id: str = "", limit: int = 50, **_kwargs: Any) -> str:
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if udir == _base_path().resolve():
        return json.dumps({
            "universe_id": uid,
            "error": "Invalid universe_id.",
        })

    ledger_path = udir / "ledger.json"
    data = _read_json(ledger_path)
    if not data or not isinstance(data, list):
        return json.dumps({"universe_id": uid, "entries": [], "note": "No ledger entries yet."})

    non_public_goal_ids = _non_public_goal_ids()

    def visible_goal_record(entry: Any) -> bool:
        if not isinstance(entry, dict):
            return True
        payload = entry.get("payload")
        goal_ref = (
            str(payload.get("goal_id") or "")
            if isinstance(payload, dict)
            else ""
        )
        if not goal_ref and entry.get("action") in {
            "subscribe_goal",
            "unsubscribe_goal",
        }:
            goal_ref = str(entry.get("target") or "")
        return (
            not goal_ref
            or goal_ref not in non_public_goal_ids
        )

    visible_entries = [
        entry for entry in data
        if visible_goal_record(entry)
    ]
    entries = list(reversed(visible_entries))[:limit]
    return json.dumps({"universe_id": uid, "entries": entries, "count": len(entries)})


def _action_switch_universe(universe_id: str = "", **_kwargs: Any) -> str:
    if not universe_id:
        return json.dumps({"error": "universe_id is required."})

    uid = universe_id
    udir = _universe_dir(uid)
    # The same question the listing and `inspect` ask: selecting a directory
    # nobody owns is selecting something that is not a universe.
    try:
        owner_id = _owned_universe_id(uid)
    except _OwnershipUnavailable as exc:
        return json.dumps({"error": f"Ownership store unavailable: {exc}"})
    if not udir.is_dir() or not owner_id:
        return json.dumps({
            "error": f"Command center '{uid}' not found.",
            "available": _available_universe_ids(),
        })

    # Explicit universe selection is not global (universe-creation spec:
    # "Explicit universe selection is not global"). An authenticated founder's
    # switch applies only to the current request/session scope — they select a
    # universe by passing `universe_id` on each tool call — and must NOT mutate
    # the host-global `.active_universe` marker that other users resolve through.
    if not permissions.is_local_single_tenant():
        return json.dumps({
            "universe_id": uid,
            "status": "selected",
            "scope": "request",
            "note": (
                f"Selected '{uid}' for this session. Pass the explicit target "
                "on each advertised handle (graph_id for graph operations; "
                "universe_id for converse, page, and status operations); this "
                "does not change the daemon's global active command center."
            ),
        })

    # Local/dev single-tenant: write the active universe marker — the
    # tray app watches this file to switch the local daemon.
    marker = _base_path() / ".active_universe"
    try:
        marker.write_text(uid, encoding="utf-8")
    except OSError as exc:
        return json.dumps({"error": f"Failed to write active command center marker: {exc}"})

    return json.dumps({
        "universe_id": uid,
        "status": "switching",
        "note": f"Daemon will restart on '{uid}' within ~10 seconds.",
    })


def _action_create_universe(
    universe_id: str = "",
    text: str = "",
    branch_def_id: str = "",
    visibility: str = "",
    **_kwargs: Any,
) -> str:
    base = _base_path()
    # Creation-time visibility declaration: a new universe must be born with an
    # explicit level so undeclared rows stop being produced (undeclared fails
    # closed). The creator may choose a level; otherwise the universe is born
    # `private` (`DEFAULT_CREATE_VISIBILITY`, founder 2026-09-26). Validate up
    # front so a bad value fails the create loudly rather than silently leaving
    # the universe undeclared.
    #
    # The provenance matters as much as the level: a level this caller asked for
    # is the OWNER's choice, the fallback is the platform's. The migration that
    # closes the defaulted-public records reads exactly that distinction, so a
    # universe born private-by-default must not claim its owner chose privacy.
    from tinyassets.api import visibility as _visibility

    chosen_level = (visibility or "").strip()
    create_level = chosen_level or _visibility.DEFAULT_CREATE_VISIBILITY
    create_level_source = (
        _visibility.LEVEL_SOURCE_OWNER if chosen_level else "default"
    )
    # Birth offers the same levels the post-birth verb offers, and for the same
    # reason (`_OFFERED_VISIBILITY_LEVELS`): a level whose content boundary no
    # reader enforces must not be selectable. This became reachable when the
    # dispatcher started forwarding `visibility`, which it needs to do for
    # `set_visibility` — so the two writers are held to one list rather than
    # birth quietly accepting more than the verb.
    if create_level not in _OFFERED_VISIBILITY_LEVELS:
        return json.dumps({
            "error": (
                f"Invalid visibility {create_level!r}; expected one of "
                f"{sorted(_OFFERED_VISIBILITY_LEVELS)}."
            ),
        })
    # universe-creation D2: universe_id is optional. When absent, generate one
    # opaque immutable serial (u- + lowercase ULID). Provided ids are still
    # accepted (dev / existing-universe operations).
    supplied_id = (universe_id or "").strip()
    # Provenance (universe-creation 5.2): the id is platform-generated iff this
    # function generated it (no caller value). The public MCP boundary rejects a
    # caller-selected id upstream, so public births always land here with an
    # empty ``universe_id`` and are generated=True; a supplied id is a dev /
    # migration / already-reserved value whose provenance is the caller's.
    id_is_platform_generated = not supplied_id
    uid = supplied_id or new_universe_id()
    udir = base / uid

    # Sanitize
    if "/" in uid or "\\" in uid or uid.startswith("."):
        return json.dumps({"error": "Invalid universe_id."})
    # owner-dynamic-admission DA4: an incomplete root left by a failed create
    # resumes in place (same id); the ownership grant below still refuses
    # another account's id.
    if udir.exists() and (udir / "soul.md").is_file():
        return json.dumps({"error": f"Command center '{uid}' already exists."})

    from tinyassets.providers.provider_jail import UNIVERSE_SIDECARS_DIR

    seed_sidecar_preexisted = (udir.parent / UNIVERSE_SIDECARS_DIR / udir.name).exists()
    founder = ""
    try:
        # THE OWNER IS CLAIMED BEFORE THE DIRECTORY EXISTS. Creation used to
        # mkdir and seed here and grant ownership ~90 lines below, which left a
        # window where the directory was on disk and owned by nobody. That was
        # merely untidy while an unowned directory was still readable; now that
        # a universe nobody owns grants no capability (`visibility_permits`), the
        # window is a functional hole -- the creator's own reads inside it would
        # be refused, and `first_contact`'s seeding reads through the same gate.
        # `first_contact` already claims the home before materializing; explicit
        # creation now does the same (Codex review 2026-09-26).
        founder = permissions.current_actor_id()
        # NO UNOWNED UNIVERSE, EVER (founder rule 2026-08-28). This used to fall
        # through to `founder_id: ""` -- it created the universe, granted nobody,
        # bound nobody, and returned success. The question "whose is this?" then
        # had no answer, and that question is what every multi-tenant guarantee
        # is built on. The public MCP surface already refuses at the door
        # (`_universe_birth_refusal`) and `ensure_founder_home` needs
        # `create_universe` scope, so no production path reaches here
        # unauthenticated today -- and "no caller does that today" is precisely
        # the reasoning that has been wrong twice already in this repo.
        #
        # Refusing BEFORE the mkdir means a refusal leaves nothing behind at all,
        # rather than relying on rollback to remove a bare directory.
        if not permissions.is_authenticated_request() or not (founder or "").strip():
            raise PermissionError(
                "a command center must belong to someone: refusing to create one with no "
                "authenticated owner"
            )
        from tinyassets.daemon_server import grant_universe_ownership

        # The admin grant and the storage/seat owner in ONE transaction: a
        # universe is never granted but unowned (account-storage-quota D2).
        grant_universe_ownership(base, universe_id=uid, owner_id=founder)

        # Reserve, label, publish, log, bind; usable only once bound.
        from tinyassets.role_center_admission import admit_center

        admit_center(base, principal=founder, center=uid)
        normalized_text = _normalize_escaped_text(text) if text.strip() else ""
        loop_branch_def_id = str(branch_def_id or "").strip()
        # universe-creation D4/D5: seed the linked OKF soul bundle. Creation
        # does NOT write self/, soul/, notes.json, or activity.log.
        soul = seed_okf_bundle(
            udir,
            purpose=normalized_text,
            loop_branch_def_id=loop_branch_def_id,
            owner_id=founder,
        )
        # Write premise mirror if provided
        if normalized_text.strip():
            write_data_path(legacy_premise_path(udir), normalized_text)

        result: dict[str, Any] = {
            "universe_id": uid,
            "status": "created",
            "has_premise": bool(normalized_text.strip()),
            "has_soul": True,
            "soul": soul.summary(),
            "loop_dispatch": {
                "source": SOUL_FILENAME,
                "branch_def_id": soul.loop_branch_def_id,
                "declared": bool(soul.loop_branch_def_id),
            },
            "first_run_checklist": _synthesis_first_run_checklist(
                has_premise=bool(normalized_text.strip()),
            ),
        }

        # Auto-switch the daemon to the new universe — ONLY for local/dev
        # single-tenant (tray) creates. An authenticated founder's create is a
        # multi-tenant MCP operation: their universe is recorded as their
        # ``founder_home`` binding (below), and per the universe-creation spec
        # ("First MCP contact") the system must NOT use the host-global
        # ``.active_universe`` marker to decide which universe a chatbot speaks
        # as. Writing it on a founder create clobbers the marker to the
        # last-created home and leaks it to other founders' omitted-scope reads.
        if permissions.is_local_single_tenant():
            marker = base / ".active_universe"
            marker.write_text(uid, encoding="utf-8")
            result["note"] = (
                f"Command center '{uid}' created. "
                "Daemon will switch to it within ~10 seconds."
            )
        else:
            result["note"] = f"Command center '{uid}' created."

        # D0a founder-grant-on-create: the authenticated founder OWNS the
        # universe they create (admin grant) — the mechanism that makes the
        # per-universe write boundary real. Ownership (ACL grant) stays
        # orthogonal to visibility (the declared level). Register in the
        # universes index so a founder universe has a universes + universe_rules
        # row (not just an ACL grant) — home resolution + reset rely on a
        # consistent registry.
        try:
            from tinyassets.daemon_server import ensure_universe_registered

            ensure_universe_registered(base, universe_id=uid, universe_path=udir)
        except Exception:  # noqa: BLE001 - registry is best-effort at create
            logger.warning("ensure_universe_registered failed for %s", uid, exc_info=True)

        # Declare the universe's visibility explicitly at birth (both the explicit
        # `create_universe` action and the converse/first-contact auto-birth route
        # through here), so no universe is ever produced undeclared. This is on
        # the critical path: a failure rolls the partial create back via the
        # outer except, keeping create atomic.
        _visibility.set_universe_visibility(
            uid, create_level, source=create_level_source
        )
        result["visibility"] = create_level

        # The admin grant was written BEFORE the directory existed (top of this
        # try), because a universe nobody owns now grants no capability. What is
        # left here is the HOME binding, which needs the completed directory to
        # decide whether an existing home is living.
        # Bind this as the founder's home when they don't already have a
        # LIVING one — no binding, or a binding to a removed/incomplete dir.
        # "Living" means COMPLETE (soul.md present), not a bare/partial dir,
        # so a broken home rebinds to this fresh one (Codex 2026-07-15).
        # Explicit later creates by a founder with a living home do NOT
        # reassign home.
        from tinyassets.daemon_server import get_founder_home, set_founder_home

        _home = get_founder_home(base, founder)
        if not _home or not (base / _home / "soul.md").is_file():
            set_founder_home(
                base,
                founder_sub=founder,
                universe_id=uid,
                platform_generated=id_is_platform_generated,
            )
        result["founder_id"] = founder

        return json.dumps(result)
    except Exception as exc:  # noqa: BLE001 - roll back a partial create
        # DA4: a published root is never removed here; it keeps its root and
        # grant, and the next create with the same id resumes in place. Only
        # whole-center deletion removes it (DA6). An incomplete root has no
        # soul.md, so it never reads as a living home. Preserve prior
        # behavior: OSError → error envelope, anything else re-raises.
        if founder and not seed_sidecar_preexisted:
            from tinyassets.starter_release import abandon_new_provision

            abandon_new_provision(udir, owner_id=founder)
        published = udir.exists()
        if published:
            logger.warning("create of %s failed after its root was published; "
                           "kept for an in-place retry", uid, exc_info=True)
        # ...and the grant written before it, so a failed create leaves neither a
        # bare directory nor an ownership row for a universe that never existed.
        # An owner left holding an admin grant on a nonexistent universe has to be
        # TOLD, whatever the create failed with: it is the one piece of state this
        # rollback cannot clean up, and it silently blocks the id.
        revoke_failed = ""
        try:
            from tinyassets.daemon_server import revoke_universe_ownership

            if founder and not published:
                revoke_universe_ownership(base, universe_id=uid, owner_id=founder)
        except Exception as revoke_exc:  # noqa: BLE001 - the create already failed
            logger.exception("rollback: could not revoke the create grant for %s", uid)
            revoke_failed = str(revoke_exc)
        if revoke_failed:
            return json.dumps({"error": (
                f"Failed to create command center: {exc}. The ownership grant could NOT "
                f"be taken back ({revoke_failed}); '{uid}' is claimed but not "
                "created."
            )})
        if isinstance(exc, OSError):
            return json.dumps({"error": f"Failed to create command center: {exc}"})
        raise


# ───────────────────────────────────────────────────────────────────────────
# Pattern A2 body for the ``universe()`` MCP tool. The decorator + 23-arg
# signature + chatbot-facing docstring stays in ``tinyassets/universe_server.py``
# wrapping a delegation to this function. Same shape as ``goals()`` /
# ``gates()`` (Step 7), ``branch_design_guide`` (Step 8).
# ───────────────────────────────────────────────────────────────────────────


# ───────────────────────────────────────────────────────────────────────────
# Newborn without an engine (P0 #1582).
#
# 92dd60c5 correctly stopped a universe with no credential of its own from
# spending the host's subscription — but nothing gives a newborn a credential,
# so the founder's very first `converse` turn came back as the raw
# "All providers exhausted for role=writer". That is a true statement of the
# runtime and a dead end for the person reading it.
#
# The distinction that matters: a universe with NO attached engine credential
# can never speak until its founder attaches one (BYOC), while a universe that
# HAS one and still exhausts is a real outage (BUG-038/039) whose error must
# keep surfacing. Only the first case becomes onboarding.
# ───────────────────────────────────────────────────────────────────────────

_ENGINE_CREDENTIAL_TYPES = frozenset({"llm_subscription", "llm_api_key"})

# `UniverseConfig.engine_source` defaults to "byo_api_key" for every newborn, so
# the default value is NOT evidence the founder chose anything. Any OTHER value
# was written by `universe action=set_engine` and IS an explicit choice.
_DEFAULT_ENGINE_SOURCE = "byo_api_key"


def universe_has_assigned_engine(universe_dir: str | Path) -> bool:
    """Return True when this command center has an engine of its own by any route.

    Two routes count. A vault LLM credential is the fully-wired BYO path. An
    explicit non-default ``engine_source`` is the other: ``self_hosted_endpoint``
    / ``market_rented`` / ``host_daemon`` record the founder's choice in config
    and write NO vault record, so a vault-only test would read them as "never
    set up" and send a founder who already chose an engine back to onboarding
    while hiding that their engine is down.

    Fail-safe direction throughout: an unreadable vault or config returns True.
    We only ever claim "no engine is attached" from state we actually read —
    otherwise a corrupt file becomes a setup instruction the founder already
    followed, hiding the real fault (Hard Rule #8).
    """
    from tinyassets.credential_vault import load_credential_vault

    try:
        records = load_credential_vault(universe_dir)
    except (ValueError, OSError):
        logger.warning(
            "credential vault unreadable for %s; not treating as engine-less",
            universe_dir,
        )
        return True
    if any(
        record.get("credential_type") in _ENGINE_CREDENTIAL_TYPES
        for record in records
    ):
        return True

    config_file = Path(universe_dir) / "config.yaml"
    if config_file.exists() and not _config_yaml_is_parseable(config_file):
        logger.warning(
            "command center config unreadable for %s; not treating as engine-less",
            universe_dir,
        )
        return True

    from tinyassets.config import load_universe_config

    try:
        engine_source = load_universe_config(Path(universe_dir)).engine_source
    except Exception:  # noqa: BLE001 - unreadable config is not proof of absence
        logger.warning(
            "command center config unreadable for %s; not treating as engine-less",
            universe_dir,
        )
        return True
    # `_build_config` (config.py) assigns YAML values to fields without type
    # coercion, so `engine_source: 7` arrives as an int. Coerce before
    # comparing — an AttributeError here would escape while we are already
    # handling the founder's failed turn. A non-string value is also not the
    # default, so it lands on the fail-safe side. (Codex ADAPT round 2.)
    return str(engine_source or "").strip() != _DEFAULT_ENGINE_SOURCE


def _config_yaml_is_parseable(config_file: Path) -> bool:
    """True when ``config.yaml`` actually parses.

    Codex ADAPT round 2 (2026-07-25): ``load_universe_config`` deliberately
    degrades a corrupt, unreadable, or PyYAML-less config to a default
    ``UniverseConfig`` (config.py:137-151) rather than raising — and the
    default ``engine_source`` is exactly the value read above as "the founder
    never chose an engine". So the loader alone cannot tell "no choice" from
    "a choice we failed to read", and the try/except around it never fires.
    Probe parseability separately so the read failure stays visible.
    """
    try:
        import yaml  # noqa: F401 - availability probe
    except ImportError:
        return False
    try:
        raw = read_data_path(config_file, max_bytes=MAX_CONFIG_BYTES)
        if raw is None:
            return False
        load_untrusted_yaml(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False
    return True


def engine_setup_required_payload(
    universe_id: str, exc: BaseException,
) -> dict[str, Any] | None:
    """Return the held/setup-required envelope, or None to surface *exc*.

    Returns a payload ONLY when the turn failed because the command center has no
    engine of its own. Every other failure — a transient outage on a command center
    that HAS an engine, a policy block, anything not provider exhaustion —
    returns None so the caller reports it honestly.

    The envelope deliberately carries no ``reply`` key. ``reply`` is what the
    connector renders verbatim as the command center's own first-person voice; this
    text is platform-authored, so it travels as ``note`` like the other
    deterministic relay payloads (`write_page`, brain-write relays).
    """
    from tinyassets.exceptions import AllProvidersExhaustedError

    if not isinstance(exc, AllProvidersExhaustedError):
        return None
    # The router raises this one class for several distinct conditions. Only
    # the genuine "every provider in the chain was tried and failed" raise
    # carries `chain_state` diagnostics — which is the shape a universe with no
    # auth of its own produces. The policy hard-fails (allowlist blocks the
    # chain, pinned writer unavailable, API-key policy, no router installed)
    # raise it bare, and each of those is a real fault whose own message must
    # reach the founder rather than being retold as "you have no engine".
    if getattr(exc, "chain_state", None) is None:
        return None
    udir = _universe_dir(universe_id)
    if universe_has_assigned_engine(udir):
        return None
    return {
        "status": "held",
        "reason": "setup_required",
        "universe_id": universe_id,
        "missing": ["compute", "model_access"],
        "note": (
            "Your command center is born and listening, but it has no engine yet — "
            "no provider of its own to think with, so it can't answer you. It "
            "will never run on anyone else's account, which is why this is the "
            "one thing it needs from you first. Give it one and it starts "
            "speaking on this very next turn."
        ),
        "setup_paths": [{
            "path": "byo_api_key",
            "how": "Engine assignment is not exposed by the advertised handles.",
            "inputs_json": {
                "engine_source": "byo_api_key",
                "service": "anthropic | openai",
                "api_key": "<your key>",
            },
            "note": (
                "Your own API key would be stored in this command center's private "
                "vault and never echoed back. Ask the host to use the internal "
                "engine-assignment surface."
            ),
        }],
    }


def _action_set_engine(
    universe_id: str = "",
    inputs_json: str = "",
    **_kwargs: Any,
) -> str:
    """Founder-only: assign the command center's engine (`universe action=set_engine`).

    Deposits a BYO LLM API key into the command center's credential vault and sets the
    preferred writer, so the command center's own intelligence runs on the founder's
    engine (BYO API key → CLI-subprocess provider). Founder-only: gated by the
    ``universe:admin`` scope + the command center write ACL. The key is stored in the
    per-universe vault and injected into the CLI subprocess env at call time; it
    is never echoed back or written to the ledger.

    ``inputs_json``: ``{"service": "anthropic"|"openai", "api_key": "...",
    "preferred_writer": "claude-code"|"codex"}`` (preferred_writer inferred from
    service when omitted).
    """
    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})

    raw = (inputs_json or "").strip()
    if not raw:
        return json.dumps({
            "error": "inputs_json is required.",
            "expected": {
                "engine_source": "byo_api_key | self_hosted_endpoint | "
                                 "market_rented | host_daemon",
                "byo_api_key": {"service": "anthropic|openai", "api_key": "...",
                                "preferred_writer": "claude-code|codex (opt)"},
                "self_hosted_endpoint": {"endpoint": "https://…",
                                         "preferred_writer": "…"},
                "market_rented": {"market_model": "glm-5.2", "market_rate": 0.0,
                                  "spending_cap": 0.0},
                "host_daemon": {"provider": "claude-code|codex"},
            },
        })
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"inputs_json is not valid JSON: {exc}"})
    if not isinstance(data, dict):
        return json.dumps({"error": "inputs_json must decode to a JSON object."})

    engine_source = str(data.get("engine_source", "byo_api_key")).strip().lower()
    preferred_writer = str(data.get("preferred_writer", "")).strip()

    if engine_source == "byo_api_key":
        return _set_engine_byo_api_key(uid, udir, data, preferred_writer)
    if engine_source == "self_hosted_endpoint":
        return _set_engine_self_hosted(uid, udir, data, preferred_writer)
    if engine_source == "market_rented":
        return _set_engine_market_rented(uid, udir, data, preferred_writer)
    if engine_source == "host_daemon":
        return _set_engine_host_daemon(uid, udir, data, preferred_writer)
    if engine_source == "open_provider":
        return _set_engine_open_provider(uid, udir, data, preferred_writer)
    return json.dumps({
        "error": f"unknown engine_source {engine_source!r}.",
        "expected_engine_source": ["byo_api_key", "self_hosted_endpoint",
                                   "market_rented", "host_daemon", "open_provider"],
    })


def _set_engine_open_provider(uid, udir, data, preferred_writer) -> str:
    """Open compute provider (compute-agnostic) → set the command center's writer to a
    registered ProviderDefinition.

    The correct-shape replacement for the fixed-service ``byo_api_key`` path: the
    provider was registered out of band via ``connect_compute`` (an open descriptor
    referencing a granted connection / CLI subscription), so this sets NO credential —
    it only points the command center's ``preferred_writer`` at the definition's resolved
    executor name (``api_key_http:<def-id>`` / ``codex`` / ``claude-code``). The
    per-call registration bridge then makes that name routable. No ``allowed_providers``
    restriction is written, so other roles keep their chains."""
    from tinyassets.config import write_universe_config_fields
    from tinyassets.providers import definition as pd
    from tinyassets.providers.provider_resolver import provider_for_definition

    definition_id = str(data.get("definition_id", "")).strip()
    if not definition_id:
        return json.dumps({
            "error": "definition_id is required for open_provider.",
            "hint": "register the provider first with write_graph "
                    "target=connection operation=connect_compute.",
        })
    definition = pd.get_definition(uid, definition_id)
    if definition is None:
        return json.dumps({
            "error": f"provider definition {definition_id!r} is not registered in "
                     "this command center.",
            "hint": "register it with connect_compute first.",
        })
    try:
        provider_name = provider_for_definition(definition).name
    except (ValueError, KeyError) as exc:
        return json.dumps({
            "error": f"definition cannot resolve to an executor: {exc}",
        })
    try:
        write_universe_config_fields(
            udir, engine_source="open_provider", preferred_writer=provider_name,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"Failed to write engine config: {exc}"})
    return json.dumps({
        "status": "engine_set",
        "universe_id": uid,
        "engine_source": "open_provider",
        "definition_id": definition_id,
        "preferred_writer": provider_name,
        "note": "Writer now routes to this registered provider; no credential "
                "was set here (it lives in the connection the definition references).",
    })


def _set_engine_byo_api_key(uid, udir, data, preferred_writer) -> str:
    """BYO API key → per-universe vault + preferred_writer (fully wired)."""
    from tinyassets.config import write_universe_config_fields
    from tinyassets.credential_vault import (
        supported_llm_api_key_services,
        write_credential_vault,
    )

    service = str(data.get("service", "")).strip().lower()
    api_key = str(data.get("api_key", "")).strip()
    _writer_by_service = {"anthropic": "claude-code", "openai": "codex"}
    if not preferred_writer and service in _writer_by_service:
        preferred_writer = _writer_by_service[service]

    if not api_key:
        return json.dumps({"error": "api_key is required."})
    if service not in supported_llm_api_key_services():
        return json.dumps({
            "error": f"unsupported service {service!r} — the key would never "
                     "reach a provider.",
            "expected_services": sorted(supported_llm_api_key_services()),
        })

    import base64
    try:
        vault_summary = write_credential_vault(udir, [{
            "credential_type": "llm_api_key",
            "service": service,
            # base64 at rest (the vault's existing convention; _secret_value
            # decodes secret_b64). Envelope encryption is the deferred hardening
            # flagged in the credential-custody research.
            "secret_b64": base64.b64encode(api_key.encode("utf-8")).decode("ascii"),
        }])
    except ValueError as exc:
        return json.dumps({"error": f"Failed to store engine credential: {exc}"})

    fields = {"engine_source": "byo_api_key"}
    if preferred_writer:
        fields["preferred_writer"] = preferred_writer
    try:
        write_universe_config_fields(udir, **fields)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"Failed to write engine config: {exc}"})

    return json.dumps({
        "status": "engine_set",
        "universe_id": uid,
        "engine_source": "byo_api_key",
        "service": service,
        "preferred_writer": preferred_writer,
        "credential_types": vault_summary.get("credential_types", []),
        "note": "Engine credential stored in the per-universe vault (never "
                "echoed).",
    })


def _set_engine_self_hosted(uid, udir, data, preferred_writer) -> str:
    """Self-hosted endpoint → persist the endpoint + writer choice."""
    from tinyassets.config import write_universe_config_fields

    endpoint = str(data.get("endpoint", "")).strip()
    if not endpoint:
        return json.dumps({"error": "endpoint is required for self_hosted_endpoint."})
    fields = {"engine_source": "self_hosted_endpoint", "engine_endpoint": endpoint}
    if preferred_writer:
        fields["preferred_writer"] = preferred_writer
    try:
        write_universe_config_fields(udir, **fields)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"Failed to write engine config: {exc}"})
    return json.dumps({
        "status": "engine_set", "universe_id": uid,
        "engine_source": "self_hosted_endpoint", "engine_endpoint": endpoint,
        "preferred_writer": preferred_writer,
    })


def _set_engine_market_rented(uid, udir, data, preferred_writer) -> str:
    """Market-rented → persist model + rate + spending cap."""
    from tinyassets.config import write_universe_config_fields

    market_model = str(data.get("market_model", "")).strip()
    if not market_model:
        return json.dumps({"error": "market_model is required for market_rented."})
    try:
        market_rate = float(data.get("market_rate", 0.0) or 0.0)
        spending_cap = float(data.get("spending_cap", 0.0) or 0.0)
    except (TypeError, ValueError):
        return json.dumps({"error": "market_rate and spending_cap must be numbers."})
    fields = {
        "engine_source": "market_rented", "market_model": market_model,
        "market_rate": market_rate, "spending_cap": spending_cap,
    }
    if preferred_writer:
        fields["preferred_writer"] = preferred_writer
    try:
        write_universe_config_fields(udir, **fields)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"Failed to write engine config: {exc}"})
    return json.dumps({
        "status": "engine_set", "universe_id": uid,
        "engine_source": "market_rented", "market_model": market_model,
        "market_rate": market_rate, "spending_cap": spending_cap,
        "note": "Your command center will run on a market-rented daemon within the "
                "spending cap. Market matching runs when a market host is live "
                "(post-M1 runtime).",
    })


def _set_engine_host_daemon(uid, udir, data, preferred_writer) -> str:
    """Host-your-own daemon → persist the choice + preferred provider.

    The founder hosts a daemon bound to this command center. Recording the choice is
    the onboard step; the actual runtime instance is bound via the existing
    ``universe action=daemon_summon`` (create + summon) — post-M1 wires a live
    worker to consume it.
    """
    from tinyassets.config import write_universe_config_fields

    provider = str(data.get("provider", "")).strip() or "claude-code"
    fields = {"engine_source": "host_daemon",
              "preferred_writer": preferred_writer or provider}
    try:
        write_universe_config_fields(udir, **fields)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"Failed to write engine config: {exc}"})
    return json.dumps({
        "status": "engine_set", "universe_id": uid,
        "engine_source": "host_daemon", "provider": provider,
        "preferred_writer": fields["preferred_writer"],
        "next_step": (
            "Runtime-daemon binding is not exposed by the advertised handles; "
            "ask the host to use the internal operator surface."
        ),
    })


def _founder_offers_path(founder_id: str) -> Path:
    from tinyassets.storage import data_dir
    safe = "".join(
        c if c.isalnum() or c in "-_" else "-" for c in (founder_id or "")
    ) or "anon"
    d = data_dir() / "founder_offers"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe}.json"


def _read_founder_offers(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        offers = loaded.get("offers") if isinstance(loaded, dict) else None
        return offers if isinstance(offers, list) else []
    except Exception:  # noqa: BLE001
        return []


def _write_founder_offers(path: Path, offers: list[dict[str, Any]]) -> None:
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".offers.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"offers": offers}, fh, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _action_offer_engine(
    universe_id: str = "",
    inputs_json: str = "",
    **_kwargs: Any,
) -> str:
    """Founder-only: offer an engine to the market (`universe action=offer_engine`).

    Supply side (the inverse of set_engine): records / lists / toggles engines the
    founder offers to the market for OTHER command centers to rent when the founder is
    not running their own. Founder-scoped (keyed on the authenticated founder),
    togglable. No credential is stored here — only offer terms (service, model,
    rate, cap). Founder-only via the universe:admin scope + write ACL.

    ``inputs_json``: ``{"action": "list"|"set"|"toggle", "service": "anthropic",
    "model": "…", "rate": 0.0, "cap": 0.0, "enabled": true, "key": "…" (toggle)}``.
    """
    from tinyassets.api.permissions import current_actor_id

    founder_id = current_actor_id()
    path = _founder_offers_path(founder_id)
    offers = _read_founder_offers(path)

    raw = (inputs_json or "").strip()
    data: dict[str, Any] = {}
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"inputs_json is not valid JSON: {exc}"})
        if not isinstance(data, dict):
            return json.dumps({"error": "inputs_json must decode to a JSON object."})

    op = str(data.get("action", "list")).strip().lower()
    if op == "list":
        return json.dumps({"status": "offers", "founder_id": founder_id,
                           "offers": offers})
    if op == "set":
        service = str(data.get("service", "")).strip().lower()
        model = str(data.get("model", "")).strip()
        if not service:
            return json.dumps({"error": "service is required to set an offer."})
        try:
            rate = float(data.get("rate", 0.0) or 0.0)
            cap = float(data.get("cap", 0.0) or 0.0)
        except (TypeError, ValueError):
            return json.dumps({"error": "rate and cap must be numbers."})
        key = f"{service}:{model}"
        offers = [o for o in offers if o.get("key") != key]
        offers.append({"key": key, "service": service, "model": model,
                       "rate": rate, "cap": cap,
                       "enabled": bool(data.get("enabled", True))})
        try:
            _write_founder_offers(path, offers)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Failed to save offer: {exc}"})
        return json.dumps({"status": "offer_set", "founder_id": founder_id,
                           "offer_key": key, "offers": offers})
    if op == "toggle":
        key = str(data.get("key", "")).strip()
        found = False
        for o in offers:
            if o.get("key") == key:
                o["enabled"] = not o.get("enabled", True)
                found = True
        if not found:
            return json.dumps({"error": f"no offer with key {key!r}."})
        try:
            _write_founder_offers(path, offers)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Failed to toggle offer: {exc}"})
        return json.dumps({"status": "offer_toggled", "founder_id": founder_id,
                           "offer_key": key, "offers": offers})
    return json.dumps({
        "error": f"unknown action {op!r}; expected list | set | toggle.",
    })


def _action_declare_universe_loop(
    universe_id: str = "",
    branch_def_id: str = "",
    **_kwargs: Any,
) -> str:
    """Declare (or change) the Loop branch of an EXISTING command center.

    A command center is the owner's account and storage, not a workflow: it hosts many
    automations, and an owner must be able to declare a loop after birth. Until
    this action existed, ``loop_branch_def_id`` could only be set by
    ``_action_create_universe``, and the public ``write_graph target="universe"``
    never forwarded it — so no publicly created command center could declare a loop,
    then or ever. Every downstream consequence followed silently: no loop ->
    ``select_project_loop_daemon`` returns None -> the cloud worker skips runtime
    registration -> nothing converges an automation's activation -> scheduled
    execution never runs.

    Scoped to the caller's own command center via ``_request_universe``; the branch
    must exist. Passing an empty ``branch_def_id`` clears the declaration.
    """
    from tinyassets.api.branches import _resolve_readable_branch
    from tinyassets.storage import data_dir
    from tinyassets.universe_soul import write_universe_soul

    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})

    declared = str(branch_def_id or "").strip()
    if declared:
        # MUST use the authority-aware resolver, not raw get_branch_definition:
        # the raw query ignores author/visibility, which would both bind another
        # author's PRIVATE branch as this universe's loop and turn the
        # found/not-found answer into a cross-tenant existence oracle
        # (cross-family review 2026-08-05).
        resolved = _resolve_readable_branch(declared, str(data_dir()))
        if resolved is None:
            return json.dumps({
                "error": "branch_not_found",
                "universe_id": uid,
                "branch_def_id": declared,
                "note": (
                    "Declare a branch you can read. Build one with "
                    '`write_graph target="branch" operation="create" '
                    "payload_json=...` first."
                ),
            })
        declared = resolved[0]

    soul = write_universe_soul(
        udir,
        loop_branch_def_id=declared,
        # A blank means "leave alone" everywhere else in this writer, so
        # clearing needs an explicit flag or it silently reports success.
        clear_loop_branch=not declared,
    )
    if soul.loop_branch_def_id != declared:
        return json.dumps({
            "error": "loop_declaration_failed",
            "universe_id": uid,
            "requested": declared,
            "current": soul.loop_branch_def_id,
        })

    # A declared loop is not yet a SERVABLE loop: without a project-loop daemon
    # for this universe, the served consumer's `_serving_runtime` returns None,
    # so no runtime is registered and nothing claims the universe's work.
    #
    # This route deliberately does NOT provision that daemon. Three consecutive
    # cross-family reviews rejected doing so, and the last one showed why the
    # shape is wrong rather than the implementation: `daemon_create` accepts
    # caller-supplied metadata and `create_daemon` uses setdefault, so
    # `owner_user_id` is CALLER-SPOOFABLE. Any ownership check built on it —
    # including an owner-scoped selector — can be satisfied by an attacker who
    # planted a daemon claiming to be owned by the victim. Provisioning a loop
    # daemon needs authority derived from server state, which belongs with the
    # separately-gated daemon lifecycle, not smuggled into a config write.
    #
    # So we report the gap instead of papering over it.
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.daemon_registry import select_project_loop_daemon

    # Owner-scoped: an UNSCOPED lookup would select an attacker-owned daemon
    # whose (caller-controlled) metadata names this universe, leaking its id and
    # falsely reporting the owner's loop as served (cross-family review).
    loop_daemon = (
        select_project_loop_daemon(
            _base_path(), universe_id=uid, owner_user_id=_current_actor()
        )
        if declared
        else None
    )

    return json.dumps({
        "universe_id": uid,
        "status": "declared" if declared else "cleared",
        "loop_dispatch": {
            "source": SOUL_FILENAME,
            "branch_def_id": soul.loop_branch_def_id,
            "declared": bool(soul.loop_branch_def_id),
        },
        "loop_daemon": (
            {
                "daemon_id": loop_daemon.get("daemon_id"),
                # Registry presence only. A registered daemon is NOT proof of a
                # live runtime — `runtime_instance_count` can still be 0 — so do
                # not call this "serving" and invite a false all-clear.
                "registered": True,
                "note": (
                    "a project-loop daemon is registered for this command center; "
                    "this does not prove a worker has a live runtime for it"
                ),
            }
            if loop_daemon
            else {
                "daemon_id": None,
                "registered": False,
                "blocker": "no_project_loop_daemon",
                "note": (
                    "loop declared, but no project-loop daemon is registered for "
                    "this command center, so no worker will register a runtime for it "
                    "and queued work will not be claimed"
                ),
            }
            if declared
            else None
        ),
    }, default=str)


def _action_soul_edit(
    universe_id: str = "",
    inputs_json: str = "",
    **_kwargs: Any,
) -> str:
    """The command center's learn/write path (`universe action=soul.edit`).

    Applies a governed learning event per the command center's own soul.edit.md
    policy — see ``tinyassets.soul_edit.apply_soul_edit``. This is how a
    founder's command center REMEMBERS what it is taught: learned files feed the
    self-model, and the persona voices them from the next turn on.
    """
    from tinyassets.soul_edit import SoulEditError, apply_soul_edit
    from tinyassets.universe_self_model import read_self_model

    uid = _request_universe(universe_id)
    udir = _universe_dir(uid)
    if not udir.is_dir():
        return json.dumps({"error": f"Command center '{uid}' not found."})

    raw = (inputs_json or "").strip()
    if not raw:
        return json.dumps({
            "error": "inputs_json is required.",
            "expected": {
                "changes": {"<governed file>": "<new markdown body>"},
                "source": "who/what taught this (required)",
                "context": "why this is being learned (required)",
                "summary": "optional log line",
                "name": "optional learned self-name (identity.md)",
            },
        })
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"inputs_json is not valid JSON: {exc}"})
    if not isinstance(data, dict):
        return json.dumps({"error": "inputs_json must decode to a JSON object."})

    changes = data.get("changes") or {}
    if not isinstance(changes, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in changes.items()
    ):
        return json.dumps({
            "error": "changes must map governed filename -> new markdown body.",
        })

    try:
        result = apply_soul_edit(
            udir,
            agent_id="main",
            changes=changes,
            source=str(data.get("source", "")),
            context=str(data.get("context", "")),
            summary=str(data.get("summary", "")),
            name=str(data.get("name", "")),
        )
    except SoulEditError as exc:
        return json.dumps({"error": str(exc), "policy": "soul.edit.md"})

    model = read_self_model(udir)
    # universe-creation task 5.3: when a governed learning event accepts a new
    # self-name in identity.md, project it onto the universe index row keyed by
    # this immutable id. The projection updates only the display name — never
    # the key or runtime operation id — and is best-effort so a registry hiccup
    # never fails the learning event that already persisted to the brain.
    learned_name = str(model.get("name") or "").strip()
    if learned_name and "identity.md" in result["updated_files"]:
        try:
            from tinyassets.daemon_server import set_universe_display_name

            set_universe_display_name(
                _base_path(), universe_id=uid, display_name=learned_name
            )
        except Exception:  # noqa: BLE001 - index projection is best-effort
            logger.warning(
                "learned-name index projection failed for %s", uid, exc_info=True
            )
    return json.dumps({
        "universe_id": uid,
        "status": "learned",
        "updated_files": result["updated_files"],
        "snapshot": result["snapshot"],
        "source": result["source"],
        "persona_name": model.get("name", ""),
        "still_curious": [q["slug"] for q in model.get("open_questions", [])],
        "note": (
            "Learned and remembered — these files persist in my brain and my "
            "persona speaks them from now on."
        ),
    })


UNIVERSE_ACTIONS: dict[str, Any] = {
    "list": _action_list_universes,
    "inspect": _action_inspect_universe,
    "read_output": _action_read_output,
    "query_world": _action_query_world,
    "get_activity": _action_get_activity,
    "get_recent_events": _action_get_recent_events,
    "get_ledger": _action_get_ledger,
    "submit_request": _action_submit_request,
    "give_direction": _action_give_direction,
    "read_premise": _action_read_premise,
    "set_premise": _action_set_premise,
    "set_visibility": _action_set_universe_visibility,
    "soul.edit": _action_soul_edit,
    "set_engine": _action_set_engine,
    "offer_engine": _action_offer_engine,
    "add_canon": _action_add_canon,
    "list_canon": _action_list_canon,
    "read_canon": _action_read_canon,
    "list_sources": _action_list_sources,
    "read_source": _action_read_source,
    "control_daemon": _action_control_daemon,
    "switch_universe": _action_switch_universe,
    "create_universe": _action_create_universe,
    "declare_universe_loop": _action_declare_universe_loop,
    "queue_list": _action_queue_list,
    "queue_cancel": _action_queue_cancel,
    "subscribe_goal": _action_subscribe_goal,
    "unsubscribe_goal": _action_unsubscribe_goal,
    "list_subscriptions": _action_list_subscriptions,
    "post_to_goal_pool": _action_post_to_goal_pool,
    "submit_node_bid": _action_submit_node_bid,
    "daemon_overview": _action_daemon_overview,
    "daemon_list": _action_daemon_list,
    "daemon_get": _action_daemon_get,
    "daemon_create": _action_daemon_create,
    "daemon_summon": _action_daemon_summon,
    "daemon_pause": _action_daemon_pause,
    "daemon_resume": _action_daemon_resume,
    "daemon_restart": _action_daemon_restart,
    "daemon_banish": _action_daemon_banish,
    "daemon_update_behavior": _action_daemon_update_behavior,
    "daemon_control_status": _action_daemon_control_status,
    "daemon_memory_capture": _action_daemon_memory_capture,
    "daemon_memory_search": _action_daemon_memory_search,
    "daemon_memory_list": _action_daemon_memory_list,
    "daemon_memory_review": _action_daemon_memory_review,
    "daemon_memory_promote": _action_daemon_memory_promote,
    "daemon_memory_status": _action_daemon_memory_status,
    "treasury_status": _action_treasury_status,
    "set_tier_config": _action_set_tier_config,
}


def _dispatch_scope_error(
    tool: str,
    action: str,
    *,
    universe_id: str = "",
) -> str | None:
    # NB: daemon-scoped memory actions are NOT exempt here (Codex review
    # 2026-07-03). The autonomous daemon writes its own memory via the DIRECT
    # `daemon_brain.capture_daemon_memory` path (never this gated dispatch), so it
    # doesn't need an exemption — and exempting the MCP-reachable action would let
    # an untrusted caller poison any daemon's memory (the handlers trust a
    # caller-supplied `daemon_id`). Keeping them scope-gated means external MCP
    # callers must be an authenticated principal with the grant; the ACL/daemon
    # confinement stays defense-in-depth.
    from tinyassets.auth.middleware import require_action_scope
    from tinyassets.auth.provider import PermissionScope

    try:
        require_action_scope(
            tool,
            action,
            scope=PermissionScope(universe_id=universe_id),
        )
    except PermissionError as exc:
        return json.dumps({
            "error": str(exc),
            "auth_scope_required": True,
            "tool": tool,
            "action": action,
        })
    return None


def _universe_impl(
    action: str,
    universe_id: str = "",
    text: str = "",
    path: str = "",
    category: str = "",
    target: str = "",
    query_type: str = "",
    filter_text: str = "",
    request_type: str = "scene_direction",
    branch_id: str = "",
    filename: str = "",
    provenance_tag: str = "",
    limit: int = 30,
    priority_weight: float = 0.0,
    pickup_incentive: str = "",
    directed_daemon_id: str = "",
    directed_daemon_instruction: str = "",
    daemon_id: str = "",
    branch_task_id: str = "",
    goal_id: str = "",
    branch_def_id: str = "",
    inputs_json: str = "",
    node_def_id: str = "",
    required_llm_type: str = "",
    bid: float = 0.0,
    tier: str = "",
    enabled: bool = False,
    tag: str = "",
    anchor_json: str = "",
    visibility: str = "",
    *,
    allow_named_universe_id: bool = False,
) -> str:
    """Pattern A2 body — see ``tinyassets.universe_server.universe`` for the
    chatbot-facing docstring. Behavior is identical; the decorator wrapper
    forwards every argument unchanged.

    ``allow_named_universe_id`` is a keyword-only, internal-trust flag. The
    public MCP surface (``universe`` and ``write_graph`` tools) never sets it,
    so a public caller cannot choose a command center's id — see the public-birth
    boundary below.
    """
    dispatch = UNIVERSE_ACTIONS
    handler = dispatch.get(action)
    if handler is None:
        return json.dumps({
            "error": f"Unknown action '{action}'.",
            "available_actions": sorted(dispatch.keys()),
        })
    # universe-lifecycle-and-soul: every public universe-birth entry point
    # self-serializes. A public caller-selected id is rejected here at the
    # shared dispatch boundary — the service assigns an opaque ``u-``+ULID
    # serial (see ``tinyassets.ids.new_universe_id``). Only trusted internal
    # callers (first-contact home materialization, migration/dev tooling) may
    # supply a pre-generated serial via ``allow_named_universe_id``. Direct
    # ``_action_create_universe`` callers bypass this boundary and keep
    # accepting explicit ids for dev/test/migration use.
    if (
        action == "create_universe"
        and not allow_named_universe_id
        and universe_id.strip()
    ):
        return json.dumps({
            "error": (
                "Command center birth assigns its own opaque serial id; a "
                "caller-selected universe_id is not accepted."
            ),
            "reason": "caller_selected_id_rejected",
        })
    scope_error = _dispatch_scope_error("universe", action, universe_id=universe_id)
    if scope_error is not None:
        return scope_error
    acl_error = _universe_acl_error(action, universe_id=universe_id)
    if acl_error is not None:
        return acl_error

    # Build kwargs from all optional params
    kwargs: dict[str, Any] = {
        "universe_id": universe_id,
        "text": text,
        "path": path,
        "category": category,
        "target": target,
        "query_type": query_type,
        "filter_text": filter_text,
        "request_type": request_type,
        "branch_id": branch_id,
        "filename": filename,
        "provenance_tag": provenance_tag,
        "limit": limit,
        "priority_weight": priority_weight,
        "pickup_incentive": pickup_incentive,
        "directed_daemon_id": directed_daemon_id,
        "directed_daemon_instruction": directed_daemon_instruction,
        "daemon_id": daemon_id,
        "branch_task_id": branch_task_id,
        "goal_id": goal_id,
        "branch_def_id": branch_def_id,
        "inputs_json": inputs_json,
        "node_def_id": node_def_id,
        "required_llm_type": required_llm_type,
        "bid": bid,
        "tier": tier,
        "enabled": enabled,
        "tag": tag,
        "visibility": visibility,
        "anchor_json": anchor_json,
    }

    # All WRITE actions are funneled through the ledger wrapper. READ actions
    # pass through untouched. See WRITE_ACTIONS for the authoritative set.
    return _dispatch_with_ledger(action, handler, kwargs)

def _visible_refusal(refused):
    """The refusal the CALLER may see: the charged account's full record only
    if the caller is that account (storage_accounting.visible_record)."""
    from tinyassets.storage_accounting import visible_record

    return visible_record(refused)
