"""Run-execution subsystem — extracted from tinyassets/universe_server.py
(Task #11 — decomp Step 4).

Contains the run dispatcher (_RUN_ACTIONS), 16 action handlers, and the
failure-classification taxonomy. The MCP tool registration stays in
``tinyassets/universe_server.py`` (Pattern A2 from the decomp plan); this
module is plain functions consumed via the ``extensions()`` MCP tool.

Public surface (back-compat re-exported via ``tinyassets.universe_server``):
    _RUN_ACTIONS               : action dispatch table (16 entries)
    _RUN_WRITE_ACTIONS         : frozenset of write actions for ledger gating
    _dispatch_run_action       : ledger-aware action dispatcher
    _action_*                  : 16 individual handlers
    _classify_run_error        : failure-class router (also test-imported)
    _classify_run_outcome_error: outcome-error parser (also test-imported)
    _ensure_runs_recovery      : startup-recovery idempotent gate
    _build_failure_taxonomy    : taxonomy lazy-init
    _FAILURE_TAXONOMY          : module-level taxonomy state
    _run_mermaid_from_events   : mermaid renderer for run streams
    _branch_name_for_run       : human-name lookup for run records
    _compose_run_snapshot      : run-record → response-shape adapter

Cross-module note: ``_append_global_ledger``, ``_truncate``, ``_current_actor``,
``_mermaid_label``, ``_mermaid_node_id``, ``_resolve_branch_id``, ``logger`` all
live in ``tinyassets.universe_server`` (universe-engine territory) and are
lazy-imported inside the functions that use them. This avoids the load-time
cycle (universe_server back-compat-imports symbols from this module).

Source ranges extracted (current line numbers, post-#10 land):
- L7016–7982 — Phase 3 banner + helpers + 9 primary handlers
- L8053–8221 — query_runs + routing_evidence + memory_scope_status
- L8473–8757 — branch_version + rollback handlers + dispatch table + dispatcher
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # advice-only; the class itself is computed in providers.diagnostics
    from tinyassets.providers.diagnostics import HeldAttemptDiagnosis

from tinyassets.api.helpers import (
    _base_path,
    _read_text,
    _request_universe,
    _universe_dir,
)
from tinyassets.command_center_names import present_actor

logger = logging.getLogger("universe_server.runs")

ENV_CAPABILITIES_VAR = "UNIVERSE_SERVER_CAPABILITIES"


def _missing_required_inputs_response(exc: Any) -> str:
    return json.dumps(exc.to_dict())


def _bind_run_provider_call(
    provider_call: Any,
    universe_id: str,
    *,
    principal_id: str = "",
) -> Any:
    """Bind branch execution to a server-owned foreground run session.

    ``principal_id`` names the person the run acts for. A foreground request
    omits it and the request identity is used, as before. A BACKGROUND trigger
    must pass it explicitly: the scheduler tick runs on its own thread with no
    request context, so ``current_request_actor_id()`` there is empty and
    the session's founder-home check refuses every run. The principal comes from
    the schedule row, recorded at registration from the authenticated owner —
    never from an ambient or host identity.
    """
    uid = (universe_id or "").strip()
    from tinyassets.config import load_universe_config
    from tinyassets.providers.base import UniverseContext
    from tinyassets.providers.call import bind_universe_provider_call

    if not uid:
        # A legacy/unowned run has no requester authority. Keep it explicit so
        # the router raises ProviderAuthorityHeldError instead of consulting
        # the process-global provider selection.
        return bind_universe_provider_call(
            provider_call,
            UniverseContext(),
            operation="run_graph",
        )
    universe_dir = _universe_dir(uid)
    from tinyassets.api.permissions import current_request_actor_id
    from tinyassets.foreground_run_provider import new_foreground_run_provider_session

    principal = (principal_id or "").strip() or current_request_actor_id()
    session = new_foreground_run_provider_session(
        _base_path(),
        universe_id=uid,
        principal_id=principal,
        provider_call=provider_call,
    )
    return bind_universe_provider_call(
        session,
        UniverseContext(
            universe_dir=universe_dir,
            config=load_universe_config(universe_dir),
        ),
        operation="run_graph",
    )


def _current_actor_grants() -> tuple[str, ...]:
    raw = os.environ.get(ENV_CAPABILITIES_VAR, "")
    return tuple(part for part in raw.replace(",", " ").split() if part)


def _current_actor_has_capability(action: str) -> bool:
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.auth.provider import resolve_permission

    return resolve_permission(
        actor_id=_current_actor(),
        action=action,
        grants=_current_actor_grants(),
    ).allowed


def _run_actor_for_kwargs(kwargs: dict[str, Any]) -> str:
    from tinyassets.api.permissions import branch_run_actor

    return branch_run_actor(str(kwargs.get("universe_id") or ""))


def _run_owner_for_request() -> str:
    """Capture at authenticated intake, never rediscover in a worker or RPC."""
    from tinyassets.api.permissions import current_request_actor_id

    return current_request_actor_id()


#: Owner-scoped inbound-trigger ops (mint/revoke/list a webhook, create/revoke/list a
#: Source). They mutate or disclose a universe's inbound ingress secrets, so — like a
#: branch run — they are gated to a caller with WRITE access to their OWN universe
#: (Floor-1 Codex #1/#5: the ops became run_graph-reachable, so the same universe-scope
#: gate that guards run_branch must guard them, or a request could mint/list another
#: universe's tokens). Listing is gated on WRITE too because it discloses live tokens.
_WEBHOOK_OWNER_ACTIONS: frozenset[str] = frozenset({
    "mint_webhook", "revoke_webhook", "list_webhooks",
    "create_source", "revoke_source", "list_sources",
})


def _branch_run_scope_error(action: str, kwargs: dict[str, Any]) -> str | None:
    if action in _WEBHOOK_OWNER_ACTIONS:
        from tinyassets.api.permissions import (
            universe_access_allows,
            universe_access_error,
        )

        uid = _request_universe(str(kwargs.get("universe_id") or ""))
        if not uid or not universe_access_allows(uid, write=True):
            return json.dumps(universe_access_error(
                universe_id=uid,
                write=True,
                action=action,
                surface="extensions",
            ))
        return None

    if action not in {"run_branch", "run_branch_version"}:
        return None

    from tinyassets.api.permissions import (
        current_request_actor_id,
        universe_access_allows,
        universe_access_error,
    )

    uid = str(kwargs.get("universe_id") or "").strip()
    if not uid:
        if current_request_actor_id():
            return json.dumps({
                "error": "branch_run_requires_universe",
                "action": action,
                "required": "universe_id",
                "note": (
                    "Branches are run by command centers. Route the run through "
                    "the founder's own command center."
                ),
            })
        return None

    if not universe_access_allows(uid, write=True):
        return json.dumps(universe_access_error(
            universe_id=uid,
            write=True,
            action=action,
            surface="extensions",
        ))
    return None


def _run_actor_universe_id(record: dict[str, Any]) -> str:
    """The universe named by a run's ``universe:<uid>`` actor, else ``""``."""
    actor = str((record or {}).get("actor") or "")
    prefix = "universe:"
    return actor[len(prefix):].strip() if actor.startswith(prefix) else ""


def _run_universe_id(record: dict[str, Any]) -> str:
    """The universe a run is bound to: its ``universe:<uid>`` actor, else its queue.

    A background queue task records the OWNER as its actor and the universe in
    ``queue_universe_id``. Reading only the actor classed those runs as
    "not universe data" and let any signed-in caller list them and read their
    output -- which, once an owner's run reads its own private universe as the
    owner, is that private content (Codex round 2 on #4060, P1). The queue
    binding is recorded by the run's own admission, never by a caller.
    """
    return (
        _run_actor_universe_id(record)
        or str((record or {}).get("queue_universe_id") or "").strip()
    )


def _run_is_unreachable_unowned(record: dict[str, Any]) -> bool:
    """A run row recorded before there was a principal, and with no owner.

    ``create_run`` refuses the string now, so these are historical rows only.
    The actor cannot grant anything: it names nobody, so it must not put the
    row within reach of every scoped signed-in caller (Codex code review
    round 1).

    But a row that ALSO carries an ``owner_user_id`` is somebody's history,
    and the owner keeps it: refusing there would delete the founder's own past
    runs from every list without deleting anything (Codex round 2, P1). So the
    actor is ignored as authority, and ownership decides -- which is what
    ownership is for. A row with neither is unreachable, by anyone.
    """
    from tinyassets.principals import has_named_principal

    if has_named_principal((record or {}).get("actor")):
        return False
    owner = str((record or {}).get("owner_user_id") or "").strip()
    if not owner:
        return True
    from tinyassets.api.permissions import current_request_actor_id

    return owner != current_request_actor_id()


def _run_read_allowed(record: dict[str, Any]) -> bool:
    """Whether the current caller may READ a run's data.

    A universe-bound run is gated by that universe's read visibility: public
    universes stay readable, a private universe (``public_read=false``) requires
    a grant. Runs with no universe binding are not private-universe data.
    """
    if _run_is_unreachable_unowned(record):
        return False
    uid = _run_universe_id(record)
    if not uid:
        return True
    from tinyassets.api.permissions import universe_access_allows

    return universe_access_allows(uid, write=False)


def _run_matches_scope(record: dict[str, Any], kwargs: dict[str, Any]) -> bool:
    """Explicit graph selection narrows ACL access; public visibility cannot widen it."""
    expected = str(kwargs.get("universe_id") or "").strip()
    return not expected or _run_universe_id(record) == expected


def _run_read_denied_error(record: dict[str, Any], action: str) -> str:
    from tinyassets.api.permissions import universe_access_error

    return json.dumps(universe_access_error(
        universe_id=_run_universe_id(record),
        write=False,
        action=action,
        surface="extensions",
    ))


def _run_write_allowed(record: dict[str, Any]) -> bool:
    """Whether the current caller may MUTATE a run.

    A universe-bound run may only be mutated (cancel/resume/attach/receipt) by a
    caller with write access to that universe; runs with no universe binding are
    not universe-brain state.
    """
    if _run_is_unreachable_unowned(record):
        return False
    uid = _run_universe_id(record)
    if not uid:
        return True
    from tinyassets.api.permissions import universe_access_allows

    return universe_access_allows(uid, write=True)


def _run_write_denied_error(record: dict[str, Any], action: str) -> str:
    from tinyassets.api.permissions import universe_access_error

    return json.dumps(universe_access_error(
        universe_id=_run_universe_id(record),
        write=True,
        action=action,
        surface="extensions",
    ))


_EMPTY_LLM_RESPONSE_ACTION = (
    "Ask the host to check get_status provider availability/cooldowns and fix "
    "provider credentials or CLI, then rerun; only switch llm_type if get_status "
    "shows another provider available."
)


# Phase 3: Graph Runner — execute a BranchDefinition
# ---------------------------------------------------------------------------
# The runner compiles a validated branch into a LangGraph StateGraph via
# `tinyassets.graph_compiler.compile_branch`, runs it synchronously against
# user-supplied inputs, and persists run metadata + per-node events in
# `<base>/.runs.db`. Status-aware mermaid diagrams are returned so
# Claude.ai can auto-visualize the live/completed graph. True async
# execution is task #39 (Phase 3.5).


def _branch_readable_by_caller(branch_def_id: str) -> bool:
    """A run view is enriched from the CURRENT branch only when the caller may
    read that branch. A readable run of a branch that is private to someone
    else must not render the branch's name, nodes and edges as they are now
    (astra refute 2026-09-30)."""
    import sqlite3

    from tinyassets.api.branches import resolve_branch_id_for_read

    try:
        return resolve_branch_id_for_read(branch_def_id, str(_base_path())) == branch_def_id
    except (KeyError, sqlite3.OperationalError):
        # No branch store, or no such branch: nothing readable to enrich from.
        # Fail closed -- the run view still reports its own node statuses.
        return False


def _run_mermaid_from_events(
    branch_def_id: str,
    node_statuses: list[dict[str, Any]],
) -> str:
    """Render a status-colored mermaid flowchart for a run snapshot.

    Colors: ran=green, running=amber, failed=red, cancelled=blue, pending=grey. The caller
    embeds this in the `summary` markdown and as a top-level field so
    Claude.ai auto-renders.
    """
    from tinyassets.api.branches import (
        _mermaid_label,
        _mermaid_node_id,
    )
    from tinyassets.branches import BranchDefinition
    from tinyassets.daemon_server import get_branch_definition

    try:
        if not _branch_readable_by_caller(branch_def_id):
            raise KeyError(branch_def_id)
        source_dict = get_branch_definition(
            _base_path(), branch_def_id=branch_def_id,
        )
    except KeyError:
        return "```mermaid\nflowchart LR\n    missing_branch[\"(branch not found)\"]\n```"

    branch = BranchDefinition.from_dict(source_dict)
    status_by_id = {s["node_id"]: s["status"] for s in node_statuses}

    lines: list[str] = ["```mermaid", "flowchart LR"]
    lines.append('    START(["START"])')
    lines.append('    END(["END"])')

    definitions = {node.node_id: node for node in branch.node_defs}
    if branch.graph_nodes:
        diagram_nodes = [
            (ref.id, definitions.get(ref.node_def_id or ref.id))
            for ref in branch.graph_nodes
        ]
    else:
        diagram_nodes = [(node.node_id, node) for node in branch.node_defs]
    for graph_id, node in diagram_nodes:
        nid = _mermaid_node_id(graph_id)
        label = _mermaid_label((node.display_name if node else "") or graph_id)
        lines.append(f'    {nid}["{label}"]')

    for edge in branch.edges:
        src = _mermaid_node_id(edge.from_node)
        dst = _mermaid_node_id(edge.to_node)
        lines.append(f"    {src} --> {dst}")

    # Apply status classes per node.
    status_classes = {
        "ran": "ran",
        "running": "running",
        "failed": "failed",
        "cancelled": "cancelled",
        "pending": "pending",
    }
    for graph_id, _node in diagram_nodes:
        nid = _mermaid_node_id(graph_id)
        st = status_by_id.get(graph_id, "pending")
        cls = status_classes.get(st, "pending")
        lines.append(f"    class {nid} {cls}")

    lines.extend([
        "    classDef ran fill:#d4edda,stroke:#28a745,stroke-width:2px",
        "    classDef running fill:#fff3cd,stroke:#ffc107,stroke-width:2px",
        "    classDef failed fill:#f8d7da,stroke:#dc3545,stroke-width:2px",
        "    classDef cancelled fill:#dbeafe,stroke:#2563eb,stroke-width:2px",
        "    classDef pending fill:#e9ecef,stroke:#6c757d,stroke-width:1px",
    ])

    lines.append("```")
    return "\n".join(lines)


_RUNS_RECOVERY_DONE = False
#: The held recovery lock, kept for the process lifetime.
_RUNS_RECOVERY_LOCK: Any = None
_RUNS_RECOVERY_LOCK_NAME = ".run_recovery.lock"


def _ensure_runs_recovery() -> None:
    """Once per process, interrupt the runs a dead process left in flight.

    Only ONE live process sweeps: the one holding the data dir's recovery
    lock, which the server takes at boot before it starts anything that runs.
    Every engine MCP child is a separate process that also serves run tools,
    and the first run tool in one used to sweep EVERY queued/running row --
    the server's live automation runs included (live 2026-09-30: a background
    run was marked interrupted while running, and its interrupted event fired
    a second wake of the same loop). And the sweep takes only runs started
    before this process began, so it never interrupts a run of its own.
    """
    global _RUNS_RECOVERY_DONE, _RUNS_RECOVERY_LOCK
    if _RUNS_RECOVERY_DONE:
        return
    try:
        from tinyassets.runs import PROCESS_STARTED_AT, recover_in_flight_runs
        from tinyassets.singleton_lock import acquire_singleton_lock

        base = Path(_base_path())
        base.mkdir(parents=True, exist_ok=True)
        # Held already when an earlier attempt took it and then failed.
        lock = _RUNS_RECOVERY_LOCK or acquire_singleton_lock(
            base / _RUNS_RECOVERY_LOCK_NAME
        )
        if not lock.acquired:
            logger.info(
                "in-flight run recovery: another live process (pid %s) owns it",
                lock.existing_pid,
            )
        else:
            _RUNS_RECOVERY_LOCK = lock
            recover_in_flight_runs(base, started_before=PROCESS_STARTED_AT)
    except Exception:
        # Not marked done: the next run tool tries again.
        logger.exception("in-flight run recovery failed")
        return
    _RUNS_RECOVERY_DONE = True


#: How often the recovery-lock holder looks for runs whose owner died, and
#: redelivers terminal events still owed (run-owner-proof D3/D4).
RUN_OWNER_WATCH_SECONDS = 15.0


def recover_dead_owner_runs_now() -> int:
    """One recovery pass, only in the process that holds the recovery lock.

    Interrupts the runs whose owning process is provably dead, then redelivers
    every terminal event still owed. Called by the watcher and right after the
    engine supervisor respawns a dead child. Never raises.
    """
    if _RUNS_RECOVERY_LOCK is None:
        return 0
    try:
        from tinyassets.runs import (
            PROCESS_STARTED_AT,
            deliver_terminal_events,
            recover_in_flight_runs,
        )

        base = Path(_base_path())
        count = recover_in_flight_runs(base, started_before=PROCESS_STARTED_AT)
        deliver_terminal_events(base)
        return count
    except Exception:
        logger.exception("dead-owner run recovery failed")
        return 0


def start_run_owner_watcher() -> Any:
    """Start the recovery watcher when this process holds the recovery lock."""
    import threading

    if _RUNS_RECOVERY_LOCK is None:
        return None
    stop = threading.Event()

    def _watch() -> None:
        while not stop.wait(RUN_OWNER_WATCH_SECONDS):
            recover_dead_owner_runs_now()

    threading.Thread(target=_watch, name="run-owner-watcher", daemon=True).start()
    return stop


_FAILURE_TAXONOMY: list[tuple[type, str, str]] = []


# Live browser finding 2026-08-25: a branch whose node pinned a REGISTERED but
# not-serving compute provider failed with this class, and the assistant reported
# it to the user as a platform bug ("the runner is not reading serving state")
# because nothing said a pin was in play. Name the pin explicitly so a user of
# any surface can resolve it themselves.
#
# Rewritten 2026-09-20 after the live checklist. Three things in the old text
# were wrong or unreachable:
#   * it named `llm_policy.preferred_provider`, a key `branches.py` REJECTS; the
#     validated pin is `llm_policy.preferred.provider`.
#   * it said to "make the pinned provider serving". `providers/router.py` treats
#     a writer pin as CONFLICTING with the armed provider rather than replacing
#     it, so no global switch can make a pinned source pass -- and the agent read
#     that sentence as "the operator must change the universe's binding", which
#     `openspec/specs/agent-model-selection/spec.md` explicitly forbids as the
#     remedy for a work choice.
#   * it never mentioned the owner's OWN model routes, which need no operator.
_OWN_MODEL_ROUTES = (
    "The owner's own routes, needing nobody else: read_graph "
    "target=model_options lists their authorized choices, and write_graph "
    "target=model_preferences operation=save sets the default and orders the "
    "fallbacks. A node pin is llm_policy.preferred.provider (plus optional "
    "model_id) on the node, editable in the graph; provider may be the access "
    "method (api_key_http) when one source of it offers the model."
)
_PROVIDER_NOT_BOUND_ACTION = (
    "No provider authority is bound for this run - nothing was invoked. If "
    "nothing is registered yet, register one first (connect_compute). "
    "Registration is not selection. If a node pins a provider, compare "
    "read_graph target=branch (the pin) with read_graph target=compute "
    "(registered) and read_graph target=model_options (authorized), then either "
    "make that source authorized or edit the pin. " + _OWN_MODEL_ROUTES
    + " This is not a credential problem, not an exhausted quota, and not "
    "host-actionable."
)
# Capacity, not connection. The old single class told an owner whose models were
# all rate-limited or full to "connect your provider", which they already had.
_WORK_MODEL_EXHAUSTED_ACTION = (
    "Every model in this run's order was exhausted or ineligible, so no further "
    "attempt was made; the error above names each exhausted model, its capacity "
    "scope, and the classified failure and retry-after the run observed. Report "
    "that cause, not a guess: provider_refused means the source will not serve "
    "that model (retrying will not help; it is not a rate limit), \"cooling "
    "down\" means an earlier failure is being waited out, and only "
    "provider_rate_limited or provider_daily_quota is a rate limit. Retry later, or "
    "widen the order - an explicit choice with no fallbacks stays exhausted "
    "rather than silently moving to another source. " + _OWN_MODEL_ROUTES
    + " Changing what the command center serves elsewhere cannot rescue a pinned "
    "source, and nobody else needs to act."
)


# A node pin that names no single source of the universe. Nothing was invoked
# and nothing is missing: the pin is the thing to edit (live 2026-10-01, a pin
# naming the access method `api_key_http` was reported as an unavailable
# provider, and the agent then told the owner they were rate-limited).
MODEL_PIN_ACTION = (
    "A node's llm_policy pin names no single source of this universe, so the run "
    "was refused before any model was called - not a rate limit, not a missing "
    "connection. The error lists the accepted provider refs and the pin shape: "
    "edit the pin with write_graph operation=patch op=update_node, or clear it "
    "with llm_policy null so the owner's model order applies."
)


# A held ARMED attempt: the owner's bound source was admitted and invoked, and
# the attempt failed for a classified reason the run could not prove was a
# side-effect-free capacity failure, so it held rather than moving to a sibling
# model. Live 2026-09-21 (run 07c1611916cc4eb4): that evidence was erased and
# the owner was told to connect a provider they had already connected and used.
# The class itself comes from `providers.diagnostics.held_attempt_diagnosis` --
# the SHARED typed classifier `runs._classify_failure` also reads, so one stored
# row cannot get two causes on two surfaces. Only the wording lives here.
_HELD_ATTEMPT_UNEVIDENCED_ACTION = (
    "This run's work model attempt was held, and no classified attempt evidence "
    "was recorded: the cause is unknown, and how far the attempt got cannot be "
    "said from this record. It is not a missing provider connection - the source "
    "was bound and admitted. If it reached the provider, any tool or effect it "
    "started may already have happened: read this run's events and the target "
    "before running again; nothing is retried automatically."
)


def _held_attempt_action(diagnosis: "HeldAttemptDiagnosis") -> str:
    provider = diagnosis.provider or "the bound source"
    # An unrecognised cause is reported as unrecognised. The attempt still
    # happened, so the effect warning and the "not unbound" correction below
    # hold; only the diagnosis is withheld.
    named = (
        f"failed with {diagnosis.cause}"
        if diagnosis.recognised
        else "failed for a reason this daemon does not recognise"
    )
    return (
        f"The work model attempt on {provider} {named}, so the run held: "
        "that evidence does not prove a side-effect-free capacity failure, so no "
        "sibling model was tried, and any tool or effect the attempt started may "
        "already have happened. Read this run's events and the target before running "
        "again; nothing is retried automatically. error_detail.provider_chain carries "
        "the attempt. This is not a missing provider connection - the source was "
        "bound, admitted, and invoked. " + _OWN_MODEL_ROUTES
    )


def _held_attempt_annotation(
    error_text: str, provider_chain: dict[str, Any] | None,
) -> tuple[str, str] | None:
    """(failure_class, suggested_action) for a held or single-source-exhausted attempt.

    The class is the shared typed one; this adds the advice for it. Returns
    None when the shared classifier has nothing typed to say -- including a
    refusal that invoked nothing, which carries no attempt and keeps
    ``permission_denied:provider_not_bound`` -- so the existing classifiers decide.
    """
    from tinyassets.providers.diagnostics import held_attempt_diagnosis

    diagnosis = held_attempt_diagnosis(error_text, provider_chain)
    if diagnosis is None:
        return None
    if not diagnosis.invoked:
        # Held, but no FAILED attempt record: say the cause is unknown without
        # claiming an invocation the evidence does not show.
        return (diagnosis.run_class, _HELD_ATTEMPT_UNEVIDENCED_ACTION)
    return (diagnosis.run_class, _held_attempt_action(diagnosis))


def _build_failure_taxonomy() -> list[tuple[type, str, str]]:
    """Build the (exc_type, failure_class, suggested_action) table lazily."""
    rows: list[tuple[type, str, str]] = []
    try:
        from tinyassets.graph_compiler import EmptyResponseError
        rows.append((
            EmptyResponseError,
            "empty_llm_response",
            _EMPTY_LLM_RESPONSE_ACTION,
        ))
    except ImportError:
        pass
    # A held provider authority ("Connect your provider ...") is NOT an expired
    # credential. Matched by TYPE here because its own message text contains the
    # word "credentials", which the substring net below used to misread as
    # permission_denied:auth_expired / actionable_by=host (live browser finding
    # 2026-08-25: a registered-but-unbound provider told the user the HOST must
    # rotate a key).
    from tinyassets.exceptions import ProviderAuthorityHeldError, WorkModelExhaustedError

    # SUBCLASS FIRST: `_classify_run_error` takes the first isinstance match, and
    # exhausting the owner's own model order is a different action from having no
    # authority at all. Typed, not message-matched -- the substring net below is
    # what misread "credentials" in the held text as an expired host key.
    rows.append((
        WorkModelExhaustedError,
        "work_model_exhausted",
        _WORK_MODEL_EXHAUSTED_ACTION,
    ))
    from tinyassets.providers.model_pins import ModelPinError

    rows.append((ModelPinError, "permission_denied:provider_not_bound", MODEL_PIN_ACTION))
    rows.append((
        ProviderAuthorityHeldError,
        "permission_denied:provider_not_bound",
        _PROVIDER_NOT_BOUND_ACTION,
    ))
    # A stored io_manifest that the strict parser refuses (an unsupported
    # top-level key such as ``file_inputs``) must not fall through to the scalar
    # path and "complete" with zero bindings (live finding 2026-09-21). The run
    # is refused before any run row or binding exists; the owner repairs it.
    # Matched on the NARROW subclass only: a general AuthoringValidationError
    # (inputs violating a valid manifest, a bad node definition) is not an
    # invalid manifest and must not be told to rewrite one.
    from tinyassets.authoring.io import UnsupportedManifestKeyError

    rows.append((
        UnsupportedManifestKeyError,
        "compile_error",
        "This branch's stored io_manifest declares an unsupported top-level key, so "
        "the run was refused before any run or file binding existed. Repair it with write_graph "
        'operation=patch, payload [{"op": "set_io_manifest", "io_manifest": '
        '{"inputs": [{"name": <state field>, "io_type": "file_bundle", ...}]}}], '
        "then run again.",
    ))
    rows.append((
        RecursionError,
        "recursion_limit",
        "Branch loop may be too deep; raise recursion_limit_override param or simplify loop.",
    ))
    rows.append((
        TimeoutError,
        "timeout",
        "Branch run timed out; try a shorter branch or increase timeout param.",
    ))
    return rows


def _actionable_by(failure_class: str) -> str:
    """Look up `actionable_by` for a failure_class via the canonical table.

    BUG-029 surface: chatbot reads this field to know whether to retry
    via another tool call ("chatbot"), surface a host-action to the user
    ("host"), escalate the raw error to the user for human judgment
    ("user"), or accept the run as terminal-by-design with no recovery
    path ("none" — e.g. cancelled).

    Defaults to "user" — never silently drops the field; conservative
    "ask the human" beats silent absence. Use "none" only when the
    failure is genuinely unrecoverable.
    """
    from tinyassets.runs import ACTIONABLE_BY
    return ACTIONABLE_BY.get(failure_class, "user")


def _no_provider_advice() -> str:
    """Advice for "no model reachable" that matches how this platform works.

    The old text was "check ANTHROPIC/GROQ/GEMINI keys" unconditionally, which
    sent the owner to fix something the platform disregards. The platform has
    no LLM (AGENTS.md Hard Rule 15): a universe runs only on the provider its
    owner connects, so that is the one thing worth pointing at.
    """
    return (
        "No LLM provider is reachable for this command center. It runs on a provider "
        "you connect to it, not on platform API keys -- connect or reconnect "
        "one from the request in your rail."
    )


def _failure_payload(
    exc: Exception, failure_class: str, suggested_action: str,
) -> dict[str, Any]:
    """Construct the standard failure response with all 3 BUG-029 fields."""
    return {
        "status": "error",
        "error": f"Run failed: {exc}",
        "failure_class": failure_class,
        "suggested_action": suggested_action,
        "actionable_by": _actionable_by(failure_class),
    }


def _classify_run_error(exc: Exception, bid: str) -> dict[str, Any]:
    # A held ATTEMPT carries its own evidence; it precedes the held-authority
    # row, which describes a refusal that invoked nothing.
    held = _held_attempt_annotation(str(exc), getattr(exc, "chain_state", None))
    if held is not None:
        return _failure_payload(exc, *held)
    for exc_type, failure_class, suggested_action in _build_failure_taxonomy():
        if isinstance(exc, exc_type):
            return _failure_payload(exc, failure_class, suggested_action)
    msg = str(exc).lower()
    if "quota" in msg or "rate limit" in msg or "rate_limit" in msg or "ratelimit" in msg:
        return _failure_payload(
            exc, "quota_exhausted",
            "Provider quota or rate limit hit; wait before retrying OR"
            " switch providers via the llm_type param.",
        )
    if "all providers exhausted" in msg or "providers exhausted" in msg:
        return _failure_payload(
            exc, "provider_exhausted",
            "Provider chain exhausted; check provider credentials/config or"
            " rerun after cooldown.",
        )
    if "auth expir" in msg or "token expir" in msg or "credential" in msg:
        return _failure_payload(
            exc, "permission_denied:auth_expired",
            "Provider credentials have expired; re-authenticate or rotate the API key.",
        )
    if "permission denied" in msg:
        return _failure_payload(
            exc, "permission_denied:approval_required",
            "Ask the host to approve the source_code node through the internal "
            "operator surface before running; approval is not exposed by the "
            "advertised handles.",
        )
    if any(f"code runs only in the {word} that authored it" in msg
           for word in ("command center", "universe")):  # pre-rename records
        return _failure_payload(
            exc, "node_not_accepted",
            "This branch's code was authored elsewhere. Remix it into your command center "
            "(write_graph with fork_from) and run your copy.",
        )
    if "approv" in msg:
        # Legacy message shape (pre sandboxed-code-node); no compile path emits it.
        return _failure_payload(
            exc, "node_not_approved",
            "Approval no longer gates code: a source_code node runs in the OS sandbox, "
            "in the command center that authored it. If this run named an approval, the "
            "branch predates that change - re-store the node and run again.",
        )
    if "workspace not available" in msg:
        # The node declared a workspace and the checkout that owed it never
        # delivered, or a discard revoked it. Without this the taxonomy called
        # it "unknown", which tells the universe nothing it can act on (found
        # by the end-to-end chain test, 2026-08-30).
        return _failure_payload(
            exc, "code_node_failed",
            "This node declared workspace: \"<checkout node>\" and that checkout "
            "did not deliver (or was discarded). Read the run's earlier nodes for "
            "the checkout's own refusal - it names the class - then run again.",
        )
    if "source_code" in msg:
        # A compile-time refusal of the code itself (disallowed pattern, size,
        # syntax): the universe wrote it and can fix it. No approval exists.
        return _failure_payload(
            exc, "code_node_failed",
            "Your code node was refused at compile; the message says why "
            "(disallowed pattern, size, or syntax). Fix the node's source_code "
            "with write_graph (operation=patch, payload op=update_node) and run again.",
        )
    if "concurrent" in msg or "conflict" in msg or "modified" in msg or "stale" in msg:
        return _failure_payload(
            exc, "state_mutation_conflict",
            "Concurrent modification detected; re-fetch the branch state"
            f' with read_graph target="branch" branch_id="{bid}", then '
            "reapply your edit.",
        )
    if "compile failed" in msg or "already being used as a state key" in msg:
        return _failure_payload(
            exc, "compile_error",
            "Inspect the branch definition, node ids, state_schema, and graph"
            " edges; patch the branch and rerun.",
        )
    if (
        "carrier" in msg
        or "already consumed" in msg
        or "is not server-owned" in msg
        or "seal is invalid" in msg
    ):
        return _failure_payload(
            exc, "platform_fault",
            "This is a fault on our side, not a problem with your account or "
            "your provider setup. Retrying may work; the details are recorded.",
        )
    if "provider" in msg or "api key" in msg or "api_key" in msg or "auth" in msg:
        return _failure_payload(
            exc, "provider_unavailable", _no_provider_advice(),
        )
    return _failure_payload(
        exc, "unknown",
        f'Inspect the branch with read_graph target="branch" branch_id="{bid}".',
    )


def _classify_run_outcome_error(error_str: str) -> tuple[str, str] | None:
    """Map a stored run-failure error string to (failure_class, suggested_action).

    Called on RunOutcome objects whose error was recorded by the async runner,
    so exception type is gone — only the serialised string remains.  Returns
    None when the error does not match any known pattern (caller keeps raw
    error string and omits failure_class / suggested_action).
    """
    msg = error_str.lower()
    if msg.startswith("external write failed"):
        from tinyassets.runs import _classify_external_write, external_write_suggested_action

        cls = _classify_external_write(msg)
        return (cls, external_write_suggested_action(cls))
    from tinyassets.exceptions import WorkModelExhaustedError

    if WorkModelExhaustedError.MESSAGE in msg:
        # Typed at the raise; only the string survives the async runner. Its
        # evidence suffix names classified capacity classes ("rate_limited",
        # "overloaded"), so this must precede the substring nets below.
        return ("work_model_exhausted", _WORK_MODEL_EXHAUSTED_ACTION)
    # Same contract for a held or single-source attempt: its persisted
    # `[chain_state]:` evidence names the cause, and the nets below would read
    # that JSON's words ("timeout", "401") or fall through to "connect your
    # provider" for a source that was bound and invoked.
    held = _held_attempt_annotation(error_str, _provider_chain_from_error(error_str))
    if held is not None:
        return held
    from tinyassets.providers.model_pins import PIN_REFUSAL_MARKER
    from tinyassets.providers.owner_binding import (
        AUTHORITY_HELD_DETAIL,
        LEGACY_AUTHORITY_HELD_DETAIL,
    )

    if PIN_REFUSAL_MARKER in msg:
        # A pin naming no single source: its own words list the accepted refs.
        return ("permission_denied:provider_not_bound", MODEL_PIN_ACTION)
    if any(lead.lower() in msg
           for lead in (AUTHORITY_HELD_DETAIL, LEGACY_AUTHORITY_HELD_DETAIL)):
        # A held run whose universe DOES have a provider connected: the message
        # carries the refusal's own words after this lead-in. Keyed BEFORE the
        # substring nets below, because those words are arbitrary -- a wrapped
        # cause mentioning "timeout" or "credential" would otherwise be
        # classified as a timeout or an expired key instead of held authority.
        return ("permission_denied:provider_not_bound", _PROVIDER_NOT_BOUND_ACTION)
    if "empty" in msg and ("llm" in msg or "response" in msg or "provider" in msg):
        return (
            "empty_llm_response",
            _EMPTY_LLM_RESPONSE_ACTION,
        )
    if any(f"code runs only in the {word} that authored it" in msg
           for word in ("command center", "universe")):  # pre-rename records
        # A public foreign branch with code was run directly (sandboxed-code-node D2).
        return (
            "node_not_accepted",
            "This branch's code was authored elsewhere. Remix it into your command center "
            "(write_graph with fork_from) and run your copy.",
        )
    if "workspace not available" in msg:
        return (
            "code_node_failed",
            "A node declared a workspace and the checkout that owed it did not "
            "deliver (or was discarded). The checkout's own refusal names the "
            "class; read the earlier nodes of this run.",
        )
    if "code node '" in msg:
        # A source_code node's sandboxed run failed; the message carries its stderr.
        return (
            "code_node_failed",
            "Your code node raised or exited non-zero; the error carries its stderr tail. "
            "Fix run() in that node with write_graph "
            "(operation=patch, payload op=update_node) and run again.",
        )
    if "timed out" in msg or "timeout" in msg:
        return (
            "timeout",
            "Branch run timed out; try a shorter branch or increase timeout param.",
        )
    if "quota" in msg or "rate limit" in msg or "rate_limit" in msg or "ratelimit" in msg:
        return (
            "quota_exhausted",
            "Provider quota or rate limit hit; wait before retrying OR"
            " switch providers via the llm_type param.",
        )
    if "all providers exhausted" in msg or "providers exhausted" in msg:
        return (
            "provider_exhausted",
            "Provider chain exhausted; check provider credentials/config or"
            " rerun after cooldown.",
        )
    if "overload" in msg or "503" in msg or "service unavailable" in msg or "server error" in msg:
        return (
            "provider_overloaded",
            "Provider is temporarily overloaded; wait 30-60s then retry"
            " or switch llm_type.",
        )
    if (
        "maximum context length" in msg
        or "context_length_exceeded" in msg
        or "tokens exceeded" in msg
        or "too many tokens" in msg
    ):
        return (
            "context_length_exceeded",
            "Input or accumulated state is too long for this provider;"
            " try a branch with fewer nodes or a higher-context model.",
        )
    if "connect your provider" in msg or "serving provider is bound" in msg:
        return (
            "permission_denied:provider_not_bound",
            _PROVIDER_NOT_BOUND_ACTION,
        )
    if "auth expir" in msg or "token expir" in msg or "credential" in msg:
        return (
            "permission_denied:auth_expired",
            "Provider credentials have expired; re-authenticate or rotate the API key.",
        )
    if "approv" in msg:
        return (
            "node_not_approved",
            "Approval no longer gates code: a source_code node runs in the OS sandbox, "
            "in the command center that authored it. Re-store the node and run again.",
        )
    if "source_code" in msg:
        return (
            "code_node_failed",
            "Your code node was refused at compile; the message says why (disallowed "
            "pattern, size, or syntax). Fix the node's source_code with write_graph "
            "(operation=patch, payload op=update_node) and run again.",
        )
    if "permission denied" in msg:
        return (
            "permission_denied:approval_required",
            "Ask the host to approve the source_code node through the internal "
            "operator surface before running; approval is not exposed by the "
            "advertised handles.",
        )
    if "exit code" in msg or "subprocess failure" in msg or "api likely unavailable" in msg:
        return (
            "provider_subprocess_failed",
            "Provider CLI process failed; check that claude/codex binary is"
            " installed and reachable.",
        )
    if "concurrent" in msg or "conflict" in msg or "modified" in msg or "stale" in msg:
        return (
            "state_mutation_conflict",
            "Concurrent modification detected; re-fetch the branch state"
            ' with read_graph target="branch" branch_id="<branch id>", then '
            "reapply your edit.",
        )
    if "compile failed" in msg or "already being used as a state key" in msg:
        return (
            "compile_error",
            "Inspect the branch definition, node ids, state_schema, and graph"
            " edges; patch the branch and rerun.",
        )
    # ORDER MATTERS: our own invariants carry the word "provider" too, and the
    # bare substring below used to claim them as "check your API keys". The
    # founder was handed that advice for a carrier bug on a universe with
    # api_key_providers_enabled=False -- advice he could not have acted on even
    # if it had been the right diagnosis (2026-09-01).
    if (
        "carrier" in msg
        or "already consumed" in msg
        or "is not server-owned" in msg
        or "seal is invalid" in msg
        or "belongs to another process" in msg
    ):
        return (
            "platform_fault",
            "This is a fault on our side, not a problem with your account or "
            "your provider setup. Retrying may work; the details are recorded.",
        )
    if "expired" in msg or "unauthor" in msg or "forbidden" in msg or "401" in msg:
        return (
            "auth_invalid",
            "The command center's model provider reported a sign-in problem. Check "
            "the connection and reconnect the provider for this command center if "
            "needed; this is not evidence of a usage or billing limit.",
        )
    if "provider" in msg or "api key" in msg or "api_key" in msg:
        return (
            "provider_unavailable",
            _no_provider_advice(),
        )
    if "call failed" in msg or "groq" in msg or "gemini" in msg or "grok" in msg:
        return (
            "provider_error",
            "Provider returned an unexpected error; check provider logs"
            " or try a different llm_type.",
        )
    return None


def _provider_chain_from_events(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first structured provider_chain recorded on failed events."""
    for event in reversed(events):
        detail = event.get("detail")
        if not isinstance(detail, dict):
            continue
        provider_chain = detail.get("provider_chain")
        if isinstance(provider_chain, dict):
            return provider_chain
    return None


def _provider_chain_from_error(error: str) -> dict[str, Any] | None:
    """Parse graph_compiler's compact ``[chain_state]:`` diagnostic suffix.

    One parser, shared with the classifier that reads the same suffix, so the
    evidence this surface *shows* and the class both surfaces *report* can
    never be derived from two different readings of one stored row.
    """
    from tinyassets.providers.diagnostics import chain_state_from_error

    return chain_state_from_error(error)


def _run_error_detail(
    run_record: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build structured error detail for failed run snapshots."""
    detail: dict[str, Any] = {}
    provider_chain = (
        _provider_chain_from_events(events)
        or _provider_chain_from_error(run_record.get("error", ""))
    )
    if provider_chain:
        detail["provider_chain"] = provider_chain
    return detail


def _action_run_branch(kwargs: dict[str, Any]) -> str:
    """Execute a branch once.

    Durability guarantee (v1): runs are *terminal-on-restart*. If the
    daemon exits while a run is in flight, the row is marked
    ``interrupted`` on next startup (see
    ``tinyassets.runs.recover_in_flight_runs``) and ``get_run`` returns
    ``resumable=false`` with ``resumable_reason="v1 terminal-on-restart"``.
    To continue, re-invoke ``run_branch`` with the same ``branch_def_id``
    and ``inputs_json`` — a new ``run_id`` is returned. Mid-run resume
    from a SqliteSaver checkpoint is a future extension and is not
    available today; do not poll an ``interrupted`` run expecting it to
    flip back to ``running``.
    """
    from tinyassets.api.branches import resolve_branch_id_for_read
    from tinyassets.branches import BranchDefinition
    from tinyassets.daemon_server import get_branch_definition
    from tinyassets.runs import (
        RUN_STATUS_CANCELLED,
        RUN_STATUS_COMPLETED,
        RUN_STATUS_FAILED,
        RUN_STATUS_INTERRUPTED,
        MissingRequiredInputs,
        execute_branch_async,
        get_run,
        record_lineage,
    )

    _ensure_runs_recovery()
    actor = _run_actor_for_kwargs(kwargs)

    selector = kwargs.get("branch_def_id", "").strip()
    if not selector:
        return json.dumps({"error": "branch_def_id is required."})
    # The scope check above established that the caller may WRITE to the universe the
    # run is recorded under -- their own. It says nothing about the branch. Without a
    # read check here, a caller names another user's PRIVATE branch by exact id, the
    # resolver passes it through, the raw load succeeds, and the run executes it and
    # files the output under the caller's universe (Codex, 2026-08-28).
    bid = resolve_branch_id_for_read(selector, _base_path())
    if bid is None:
        # Deliberately indistinguishable from absent: telling a caller that a branch
        # exists but is not theirs is itself a disclosure.
        return json.dumps({"error": f"Branch '{selector}' not found."})

    try:
        source_dict = get_branch_definition(_base_path(), branch_def_id=bid)
    except KeyError:
        return json.dumps({"error": f"Branch '{bid}' not found."})

    branch = BranchDefinition.from_dict(source_dict)
    errors = branch.validate()
    if errors:
        return json.dumps({
            "error": "Branch is not valid. Fix these before running:",
            "validation_errors": errors,
        })

    inputs_raw = kwargs.get("inputs_json", "").strip()
    inputs: dict[str, Any] = {}
    if inputs_raw:
        try:
            parsed = json.loads(inputs_raw)
            if not isinstance(parsed, dict):
                return json.dumps({
                    "error": "inputs_json must decode to a JSON object.",
                })
            inputs = parsed
        except json.JSONDecodeError as exc:
            return json.dumps({
                "error": f"inputs_json is not valid JSON: {exc}",
            })

    resume_from = (kwargs.get("resume_from") or "").strip()
    source_run: dict[str, Any] | None = None
    if resume_from:
        if any(ch.isspace() for ch in resume_from):
            return json.dumps({
                "error": "resume_from must be a single run_id with no whitespace.",
                "failure_class": "resume_from_invalid",
                "actionable_by": "chatbot",
            })
        source_run = get_run(_base_path(), resume_from)
        if source_run is None:
            return json.dumps({
                "error": "resume_from source run was not found.",
                "failure_class": "resume_from_not_found",
                "actionable_by": "chatbot",
            })
        if source_run.get("actor") != actor:
            return json.dumps({
                "error": "resume_from source run is not visible to the current actor.",
                "failure_class": "resume_from_forbidden",
                "actionable_by": "user",
            })
        if source_run.get("branch_def_id") != bid:
            return json.dumps({
                "error": (
                    "resume_from source run belongs to a different workflow "
                    "than the requested target branch."
                ),
                "failure_class": "resume_from_branch_mismatch",
                "actionable_by": "chatbot",
            })
        terminal_statuses = {
            RUN_STATUS_COMPLETED,
            RUN_STATUS_FAILED,
            RUN_STATUS_CANCELLED,
            RUN_STATUS_INTERRUPTED,
        }
        if source_run.get("status") not in terminal_statuses:
            return json.dumps({
                "error": (
                    "resume_from source run must be terminal before it can "
                    "seed a new run."
                ),
                "failure_class": "resume_from_invalid_state",
                "source_status": source_run.get("status"),
                "actionable_by": "chatbot",
            })
        source_inputs = source_run.get("inputs")
        if isinstance(source_inputs, dict):
            inputs = {**source_inputs, **inputs}

    # Parse `recursion_limit_override`. ANY positive integer: the 10-1000 range
    # that used to be enforced here refused an author's own number for being
    # large, which is a cap on what they may build (founder, 2026-09-30). Zero and
    # negatives are still refused -- those are not a smaller ceiling, they are a
    # graph that cannot step.
    _rl_raw = kwargs.get("recursion_limit_override", "")
    recursion_limit_override: int | None = None
    if _rl_raw:
        try:
            _rl_val = int(_rl_raw)
        except (TypeError, ValueError):
            return json.dumps({"error": "recursion_limit_override must be an integer."})
        if _rl_val < 1:
            return json.dumps({
                "error": (
                    f"recursion_limit_override {_rl_val} is not a number of steps. "
                    "Use a positive integer; there is no upper bound."
                ),
            })
        recursion_limit_override = _rl_val

    try:
        from tinyassets.api.run_files import dispatch_file_branch

        outcome = dispatch_file_branch(
            branch, inputs, universe_id=_request_universe(kwargs.get("universe_id") or ""),
            run_name=kwargs.get("run_name", ""), recursion_limit_override=recursion_limit_override,
        )
        if outcome is None:
            provider_call = _legacy_request_provider(kwargs)
            outcome = execute_branch_async(
                _base_path(),
                branch=branch,
                inputs=inputs,
                run_name=kwargs.get("run_name", ""),
                actor=actor,
                provider_call=provider_call,
                recursion_limit_override=recursion_limit_override,
                _enqueue_universe_id=_request_universe(kwargs.get("universe_id") or ""),
                owner_user_id=_run_owner_for_request(),
            )
    except MissingRequiredInputs as exc:
        return _missing_required_inputs_response(exc)
    except Exception as exc:
        logger.exception("run_branch failed for %s", bid)
        return json.dumps(_classify_run_error(exc, bid))

    # Write-ack per tool_return_shapes.md §Write actions. Phase 3.5 async:
    # the graph is running in a background worker, so the MCP call returns
    # status=queued almost immediately. The text channel is phone-legible
    # (no raw IDs); the run_id lives in structuredContent for the next
    # tool call.
    error_annotation = _classify_run_outcome_error(outcome.error) if outcome.error else None
    error_lines: list[str] = []
    if outcome.error:
        error_lines.append(f"Error: {outcome.error}")
    if error_annotation:
        error_lines.append(f"Suggested action: {error_annotation[1]}")
    text = "\n".join([
        f"**Run {outcome.status}.** TinyAssets accepted this run.",
        "",
        *error_lines,
        "Use `read_graph target=\"run\" run_id=\"<run id>\"` for a "
        "snapshot. To request cancellation use `run_graph operation=\"cancel\" "
        "run_id=\"<run id>\"`, then read the run to observe its final status.",
    ]).strip()

    result: dict[str, Any] = {
        "text": text,
        "run_id": outcome.run_id,
        "status": outcome.status,
        "output": outcome.output,
        "error": outcome.error,
    }
    if source_run is not None:
        branch_version = int(getattr(branch, "version", 1) or 1)
        record_lineage(
            _base_path(),
            run_id=outcome.run_id,
            parent_run_id=resume_from,
            branch_def_id=bid,
            branch_version=branch_version,
            edits_since_parent=[],
        )
        result["resume_from"] = resume_from
        result["source_run_id"] = resume_from
    if error_annotation:
        result["failure_class"] = error_annotation[0]
        result["suggested_action"] = error_annotation[1]
        result["actionable_by"] = _actionable_by(error_annotation[0])
    return json.dumps(result)


def enqueue_universe_branch_run(
    base_path: str | Path,
    *,
    universe_id: str,
    branch_def_id: str,
    inputs: dict[str, Any],
    run_name: str = "",
    principal_id: str = "",
) -> str:
    """Enqueue a run of ``branch_def_id`` as ``universe:<universe_id>``.

    The single audited path used by trigger sources that carry their OWN authority
    (the inbound webhook token, an event-bus subscription) rather than an MCP request
    identity. It is deliberately NOT reachable with a request-derived actor: it enqueues
    ONLY as the bound universe, and FAILS CLOSED if the universe is empty — so a trigger
    can never fall back to an ambient/host identity (Floor-1 Codex #1). It mirrors
    ``_action_run_branch``'s resolve → validate → provider-bind → execute, and appends a
    global-ledger entry for parity with the MCP dispatch path (which the direct
    ``execute_branch_async`` call would otherwise skip).

    ``principal_id`` is the person the run acts for, and is REQUIRED of a trigger that
    has no request context of its own — the scheduler's tick thread. Omitting it keeps
    the pre-existing behaviour (the request identity binds the session), which is what
    the webhook path wants: its authority is the token it was called with, presented on
    a live request. See :func:`_bind_run_provider_call`.
    """
    from tinyassets.api.branches import (
        _append_global_ledger,
        _resolve_branch_id,
    )
    from tinyassets.api.permissions import branch_run_actor, owner_run_identity
    from tinyassets.branches import BranchDefinition
    from tinyassets.daemon_server import get_branch_definition
    from tinyassets.runs import execute_branch_async

    uid = (universe_id or "").strip()
    if not uid:
        # Never enqueue a triggered run under a non-universe (ambient/host) actor.
        raise ValueError("enqueue_universe_branch_run requires a non-empty universe_id")

    actor = branch_run_actor(uid)
    if not actor.startswith("universe:"):
        raise ValueError(f"refusing to enqueue as non-universe actor {actor!r}")

    bid = _resolve_branch_id(branch_def_id, str(base_path))
    branch = BranchDefinition.from_dict(
        get_branch_definition(base_path, branch_def_id=bid)
    )
    errors = branch.validate()
    if errors:
        raise ValueError(f"branch {bid} failed validation: {errors}")

    # Settlement identity only; a triggered run is never refused for usage. It
    # waits for its account's seats at its agent calls (`universe_seats`).
    from tinyassets.engine_admissions import attach_run
    from tinyassets.engine_mcp_server import _engine_run_admit

    ticket = _engine_run_admit(universe_id=uid)

    provider_call: Any = None
    try:
        from tinyassets.providers.call import call_provider

        provider_call = _bind_run_provider_call(
            call_provider, uid, principal_id=principal_id
        )
    except ImportError:
        provider_call = None

    # A schedule or Source event is the owner's own automation, fired from a
    # thread no request bound. The run's worker copies THIS context, so bind the
    # owner here or its reads of a private universe refuse their own owner.
    with owner_run_identity(base_path, uid, principal_id):
        outcome = execute_branch_async(
            base_path,
            branch=branch,
            inputs=inputs,
            run_name=run_name or "trigger",
            actor=actor,
            provider_call=provider_call,
            _enqueue_universe_id=uid,
            owner_user_id=principal_id,
        )
    attach_run(ticket, str(outcome.run_id or ""))
    try:
        _append_global_ledger(
            "run_branch",
            actor=actor,
            target=str(outcome.run_id),
            summary=f"trigger run_name={run_name or 'trigger'} branch={bid}",
            payload=None,
        )
    except Exception as exc:  # noqa: BLE001 - ledger loss must not fail the enqueue
        logger.warning("trigger enqueue ledger write failed: %s", exc)
    return outcome.run_id


def terminal_run_ids_for_universe(base_path: str | Path, universe_id: str) -> set[str]:
    """Run ids for a universe that have reached a TERMINAL state.

    The inbound back-pressure reservation counter (Codex #5, in webhook_hooks) reconciles
    against this: a reservation linked to a terminal run is released. Read straight from the
    runs DB so it survives a restart. Kept small — only non-active runs matter for release.
    """
    uid = (universe_id or "").strip()
    if not uid:
        return set()
    from tinyassets.runs import (
        RUN_STATUS_CANCELLED,
        RUN_STATUS_COMPLETED,
        RUN_STATUS_FAILED,
        RUN_STATUS_INTERRUPTED,
        _connect,
        initialize_runs_db,
    )

    terminal = (
        RUN_STATUS_COMPLETED, RUN_STATUS_FAILED,
        RUN_STATUS_CANCELLED, RUN_STATUS_INTERRUPTED,
    )
    initialize_runs_db(base_path)
    with _connect(base_path) as conn:
        rows = conn.execute(
            "SELECT run_id FROM runs WHERE queue_universe_id = ? "
            f"AND status IN ({','.join('?' * len(terminal))})",
            (uid, *terminal),
        ).fetchall()
    return {r[0] for r in rows}


def _branch_name_for_run(run_record: dict[str, Any]) -> str:
    """Fetch the human-legible branch name for a run record.

    Text channels should surface names, never raw branch_def_id strings.
    Falls back to ``(unknown workflow)`` when the branch is missing.
    """
    from tinyassets.branches import BranchDefinition
    from tinyassets.daemon_server import get_branch_definition

    try:
        source_dict = get_branch_definition(
            _base_path(),
            branch_def_id=run_record.get("branch_def_id", ""),
        )
        branch = BranchDefinition.from_dict(source_dict)
        return branch.name or "(unnamed workflow)"
    except Exception:
        return "(unknown workflow)"


def _admission_observation(run_record):
    """Read metadata only after the caller's normal run-read authorization."""
    from tinyassets import runs
    from tinyassets.api.permissions import current_request_actor_id
    from tinyassets.run_input_origin import classify_admission_observation

    if run_record.get("status") not in {"queued", "running"}:
        return {}
    owner = run_record.get("owner_user_id")
    if not owner or current_request_actor_id() != owner:
        return {}
    with runs._connect(_base_path()) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(run_input_admissions)")}
        if not columns:
            return {}
        names = [name for name in ("origin_kind", "origin_version", "origin_options_json",
                                   "execution_started_at", "claim_token") if name in columns]
        row = conn.execute(
            f"SELECT {','.join(names)} FROM run_input_admissions "
            "WHERE run_id=? AND owner_id=? AND universe_id=?",
            (run_record["run_id"], run_record.get("owner_user_id"),
             run_record.get("queue_universe_id")),
        ).fetchone()
    return classify_admission_observation(dict(row), run_record["status"]) if row else {}


def _compose_run_snapshot(
    run_record: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pack run metadata + node statuses + mermaid into a phone-legible dict."""
    from tinyassets.branches import BranchDefinition
    from tinyassets.daemon_server import get_branch_definition
    from tinyassets.runs import SYSTEM_EVENT_NODE_ID, build_node_status_map

    declared_order: list[str] = []
    branch_name = ""
    declares_effects: bool | None = None
    try:
        if not _branch_readable_by_caller(run_record["branch_def_id"]):
            raise KeyError(run_record["branch_def_id"])
        source_dict = get_branch_definition(
            _base_path(), branch_def_id=run_record["branch_def_id"],
        )
        branch = BranchDefinition.from_dict(source_dict)
        declared_order = [gn.id for gn in branch.graph_nodes]
        branch_name = branch.name or ""
        declares_effects = any(
            getattr(nd, "effects", None) for nd in (getattr(branch, "node_defs", None) or [])
        )
    except KeyError:
        pass

    node_statuses = build_node_status_map(events, declared_order)
    if run_record.get("status") == "failed" and run_record.get("last_node_id"):
        failed_node_id = run_record["last_node_id"]
        for node_status in node_statuses:
            if (
                node_status.get("node_id") == failed_node_id
                and node_status.get("status") == "running"
            ):
                node_status["status"] = "failed"
                break
    mermaid = _run_mermaid_from_events(
        run_record["branch_def_id"], node_statuses,
    )

    node_lines = (
        [f"  - {s['node_id']}: {s['status']}" for s in node_statuses]
        or ["  (no nodes reported)"]
    )
    # Phone-legible header — name first, IDs only in structuredContent.
    header_branch = branch_name or "(branch)"
    summary = "\n".join([
        f"**Run on workflow `{header_branch}`** — status "
        f"`{run_record['status']}`",
        f"Actor: {present_actor(run_record['actor'])}",
        "",
        "Nodes:",
        *node_lines,
        "",
        "Graph:",
        mermaid,
    ])

    # Surface the applied recursion limit from the system event if present. It
    # is a run FACT, reported as its own field -- never as a node status, which
    # is why `build_node_status_map` drops the row (live 2026-09-30: summaries
    # listed `__system__: recursion_limit_applied` among the nodes).
    recursion_limit: int | None = None
    for ev in events:
        if (ev.get("node_id") == SYSTEM_EVENT_NODE_ID
                and ev.get("status") == "recursion_limit_applied"):
            try:
                recursion_limit = int(ev.get("detail", {}).get("recursion_limit", 0)) or None
            except (TypeError, ValueError):
                pass
            break

    snapshot: dict[str, Any] = {
        "text": summary,
        "run_id": run_record["run_id"],
        "branch_def_id": run_record["branch_def_id"],
        "status": run_record["status"],
        "actor": run_record["actor"],
        "last_node_id": run_record.get("last_node_id", ""),
        "started_at": run_record.get("started_at"),
        "finished_at": run_record.get("finished_at"),
        "error": run_record.get("error", ""),
        "node_statuses": node_statuses,
        "mermaid": mermaid,
        "summary": summary,
        "recursion_limit": recursion_limit,
    }
    wait = run_record.get("workspace_wait")
    if isinstance(wait, dict):
        # A queued run that is waiting its turn for the universe's workspace.
        # Say so plainly: "queued" alone reads as stuck.
        snapshot["workspace_wait"] = dict(wait)
        wait_line = (
            "Waiting for the command center workspace (position "
            f"{wait.get('position')} in line). It starts by itself when the "
            "workspace is free, keeps its place across a restart, and can "
            "still be cancelled."
        )
        snapshot["text"] = f"{summary}\n\n{wait_line}"
        snapshot["summary"] = snapshot["text"]
    output = run_record.get("output")
    from tinyassets.api.run_outputs import output_catalog

    snapshot["output_catalog"] = output_catalog(output if isinstance(output, dict) else {})
    snapshot["output_read"] = (
        'Use read_graph target="run_output" with this run_id and field_name. '
        'For continued content pass next_offset as output_offset; omit field_name '
        'to page the field catalog.'
    )
    if isinstance(output, dict):
        for key in ("external_write_results", "external_write_errors"):
            if key in output:
                snapshot[key] = output[key]
    # INTERRUPTED runs are terminal in v1 (durability guarantee — see
    # ``_action_run_branch`` docstring + ``runs.recover_in_flight_runs``).
    # The client must rerun with the same ``inputs_json``; it cannot be
    # polled to recovery. Surface this explicitly so chatbots don't
    # busy-wait forever.
    if run_record["status"] == "interrupted":
        snapshot["resumable"] = False
        snapshot["resumable_reason"] = "v1 terminal-on-restart"
    # BUG-029: enrich failed snapshots so chatbots have a user-actionable hint.
    # `actionable_by` tells the chatbot WHO can fix it — chatbot/host/user —
    # so it doesn't have to guess (Mara's failure mode 2026-04-24).
    # A run whose EFFECT failed completes with `error` set (the nodes ran; the
    # write was refused or the far side answered with an error). Live
    # 2026-08-30: this shape carried no class at all, and the list view called
    # it "error / actionable_by: user", so the universe stopped and asked the
    # founder after every one. It is the universe's own to fix and rerun.
    error_text = str(run_record.get("error") or "")
    if run_record["status"] != "failed" and error_text.lower().startswith("external write failed"):
        from tinyassets.runs import _classify_failure, external_write_suggested_action

        cls = _classify_failure(run_record)
        snapshot["failure_class"] = cls
        snapshot["suggested_action"] = external_write_suggested_action(cls)
        snapshot["actionable_by"] = _actionable_by(cls)
    if run_record["status"] == "failed":
        error_annotation = _classify_run_outcome_error(run_record.get("error", ""))
        if error_annotation:
            snapshot["failure_class"] = error_annotation[0]
            snapshot["suggested_action"] = error_annotation[1]
            snapshot["actionable_by"] = _actionable_by(error_annotation[0])
        error_detail = _run_error_detail(run_record, events)
        if error_detail:
            snapshot["error_detail"] = error_detail
    # A run reads `running` for the seconds its effect takes to deliver after
    # its last node has run. Live 2026-08-30: a universe read two 20-second
    # branch-creation runs during that window, called them "hanging in the
    # external-call phase", and stopped. Say what the window is.
    if run_record["status"] in ("running", "queued"):
        finished = {"ran", "completed", "skipped"}
        # Only real nodes decide whether the graph is done (Codex: with the
        # system rows counted, "delivering" was unreachable). `node_statuses`
        # no longer carries one -- `build_node_status_map` drops them at the
        # fold, which is the single place that decides what a node status is.
        all_ran = bool(node_statuses) and all(
            s.get("status") in finished for s in node_statuses
        )
        if all_ran and declares_effects:
            snapshot["phase"] = "delivering_effects"
            snapshot["suggested_action"] = (
                "Every node has run; the run is delivering its effect - an external "
                "call takes seconds, not minutes. Read this run again in a few "
                "seconds; it is not stuck until it has read `running` for minutes."
            )
        elif all_ran:
            snapshot["phase"] = "finalizing"
            snapshot["suggested_action"] = (
                "Every node has run; the run is recording its result. Read this run "
                "again in a few seconds."
            )
        else:
            snapshot["phase"] = "running"
            snapshot["suggested_action"] = (
                "Still running. Read this run again in a few seconds before "
                "reporting an outcome."
            )
        snapshot["actionable_by"] = "chatbot"
    observation = _admission_observation(run_record)
    if observation:
        snapshot.update(observation)
        snapshot["actionable_by"] = "host"
        snapshot["text"] += "\n\n" + observation["suggested_action"]
        snapshot["summary"] = snapshot["text"]
    return snapshot


def _action_get_run(kwargs: dict[str, Any]) -> str:
    from tinyassets.runs import get_run as _get_run
    from tinyassets.runs import is_cancel_requested, list_events

    rid = kwargs.get("run_id", "").strip()
    if not rid:
        return json.dumps({"error": "run_id is required."})

    record = _get_run(_base_path(), rid)
    if record is None or not _run_matches_scope(record, kwargs):
        return json.dumps({"error": f"Run '{rid}' not found."})
    if not _run_read_allowed(record):
        return _run_read_denied_error(record, "get_run")

    events = list_events(_base_path(), rid)
    snapshot = _compose_run_snapshot(record, events)
    snapshot["cancel_requested"] = is_cancel_requested(_base_path(), rid)
    from tinyassets.api.run_activity import ACTIVITY_EVIDENCE, build_node_activity

    # Keep existing recovery/output guidance ahead of additive diagnostics for
    # text-only clients whose faithful result prefix has a bounded size.
    snapshot["activity_evidence"] = ACTIVITY_EVIDENCE
    snapshot["node_activity"] = build_node_activity(events, snapshot["node_statuses"])
    return json.dumps(snapshot, default=str)


def _action_list_runs(kwargs: dict[str, Any]) -> str:
    from tinyassets.runs import list_runs as _list_runs

    rows = _list_runs(
        _base_path(),
        branch_def_id=kwargs.get("branch_def_id", ""),
        status=kwargs.get("status", ""),
        limit=int(kwargs.get("limit", 50) or 50),
        universe_id=str(kwargs.get("universe_id") or "").strip(),
    )
    # Do not expose runs of a private universe the caller cannot read.
    rows = [r for r in rows if _run_matches_scope(r, kwargs) and _run_read_allowed(r)]
    summaries = [
        {
            "run_id": r["run_id"],
            "branch_def_id": r["branch_def_id"],
            "run_name": r["run_name"],
            "status": r["status"],
            "actor": r["actor"],
            "started_at": r.get("started_at"),
            "finished_at": r.get("finished_at"),
            "last_node_id": r.get("last_node_id", ""),
        }
        for r in rows
    ]
    # Catalog shape per tool_return_shapes.md — compact markdown list
    # for phone clients; full fidelity is in the `runs` array.
    if summaries:
        lines = [f"**{len(summaries)} run(s):**", ""]
        for s in summaries[:12]:
            name = s["run_name"] or s["run_id"]
            lines.append(
                f"- `{s['run_id']}` · {s['status']} · "
                f"branch={s['branch_def_id']}"
                + (f" · name={name}" if s['run_name'] else "")
            )
        if len(summaries) > 12:
            lines.append(
                f"- … and {len(summaries) - 12} more. Narrow with "
                "`branch_def_id=...` or `status=...`."
            )
        text = "\n".join(lines)
    else:
        text = "No runs match the filter."
    return json.dumps({
        "text": text,
        "runs": summaries,
        "count": len(summaries),
    }, default=str)


def _action_stream_run(kwargs: dict[str, Any]) -> str:
    from tinyassets.runs import get_run as _get_run
    from tinyassets.runs import list_events

    rid = kwargs.get("run_id", "").strip()
    if not rid:
        return json.dumps({"error": "run_id is required."})

    record = _get_run(_base_path(), rid)
    if record is None:
        return json.dumps({"error": f"Run '{rid}' not found."})
    if not _run_read_allowed(record):
        return _run_read_denied_error(record, "stream_run")

    since = int(kwargs.get("since_step", -1))
    events = list_events(_base_path(), rid, since_step=since)
    next_cursor = max(
        (e.get("step_index", since) for e in events), default=since,
    )

    # State-over-time shape per tool_return_shapes.md — ordered event
    # ticks, tight one-line per event for phone polling.
    if events:
        lines = [
            f"**Run {record['status']}** · {len(events)} new event(s)",
            "",
        ]
        for e in events[-12:]:
            lines.append(
                f"- step {e.get('step_index')} · "
                f"`{e.get('node_id', '?')}` · {e.get('status', '?')}"
            )
        if len(events) > 12:
            lines.insert(
                2, f"_(showing last 12 of {len(events)})_\n",
            )
        lines.append("")
        lines.append(f"Next poll: `since_step={next_cursor}`.")
        text = "\n".join(lines)
    else:
        text = (
            f"No new events since step {since}. "
            f"Run status: `{record['status']}`."
        )

    return json.dumps({
        "text": text,
        "run_id": rid,
        "status": record["status"],
        "events": events,
        "next_cursor": next_cursor,
    }, default=str)


def _action_wait_for_run(kwargs: dict[str, Any]) -> str:
    """Long-poll for new events on a run (#65).

    Holds the response for up to ``max_wait_s`` OR until new events
    land, then returns everything since ``since_step``. One tool call
    covers ~60s of run wall time — dramatically cheaper than repeated
    stream_run polls on the Claude.ai per-turn budget.
    """
    from tinyassets.runs import await_run_events
    from tinyassets.runs import get_run as _get_run

    rid = kwargs.get("run_id", "").strip()
    if not rid:
        return json.dumps({
            "error": "run_id is required for wait_for_run.",
        })
    record = _get_run(_base_path(), rid)
    if record is None:
        return json.dumps({"error": f"Run '{rid}' not found."})
    if not _run_read_allowed(record):
        return _run_read_denied_error(record, "wait_for_run")

    # Bound max_wait_s to 120s so a broken client can't tie up the
    # server thread forever. Default 60s per spec.
    raw_wait = kwargs.get("max_wait_s", 60)
    try:
        max_wait_s = max(0.5, min(120.0, float(raw_wait)))
    except (TypeError, ValueError):
        max_wait_s = 60.0
    since = int(kwargs.get("since_step", -1) or -1)

    result = await_run_events(
        _base_path(), rid,
        since_step=since,
        max_wait_s=max_wait_s,
    )
    events = result["events"]
    status = result["status"]
    next_cursor = result["next_cursor"]
    reason = result["reason"]
    waited = result["waited_s"]

    if events:
        header = (
            f"**Run status: `{status}`** · {len(events)} new event(s) "
            f"after waiting {waited}s."
        )
    elif reason == "terminal":
        header = (
            f"**Run finished** with status `{status}` "
            f"({waited}s wait)."
        )
    else:
        header = (
            f"**Still running** — no new events in {waited}s. "
            f"Status: `{status}`."
        )

    lines = [header, ""]
    for e in events[-12:]:
        lines.append(
            f"- step {e.get('step_index')} · "
            f"`{e.get('node_id', '?')}` · {e.get('status', '?')}"
        )
    if len(events) > 12:
        lines.insert(
            2, f"_(showing last 12 of {len(events)})_\n",
        )
    if events:
        lines.append("")
        lines.append(f"Next poll: `since_step={next_cursor}`.")
    text = "\n".join(lines)

    return json.dumps({
        "text": text,
        "run_id": rid,
        "status": status,
        "events": events,
        "next_cursor": next_cursor,
        "waited_s": waited,
        "reason": reason,
    }, default=str)


def _action_cancel_run(kwargs: dict[str, Any]) -> str:
    from tinyassets.runs import (
        _TERMINAL_STATUSES,
        is_cancel_requested,
        request_cancel,
    )
    from tinyassets.runs import (
        get_run as _get_run,
    )

    rid = kwargs.get("run_id", "").strip()
    if not rid:
        return json.dumps({"error": "run_id is required."})
    record = _get_run(_base_path(), rid)
    if record is None or not _run_matches_scope(record, kwargs):
        return json.dumps({"error": f"Run '{rid}' not found."})
    if not _run_write_allowed(record):
        return _run_write_denied_error(record, "cancel_run")
    if not _run_universe_id(record):
        from tinyassets.api.permissions import current_request_actor_id

        owner = str(record.get("owner_user_id") or record.get("actor") or "").strip()
        if not owner or owner != current_request_actor_id():
            return json.dumps({"error": f"Run '{rid}' not found."})

    # Storage checks current terminal state and family membership under its
    # admission fence. A completed root may still own executing children;
    # its historical output stays completed while those children are stopped.
    accepted = request_cancel(_base_path(), rid)
    record = _get_run(_base_path(), rid) or record
    terminal = record.get("status") in _TERMINAL_STATUSES
    requested = accepted or is_cancel_requested(_base_path(), rid)
    note = (
        "This run's result remains finished. Cancellation was recorded for its "
        "active execution family; already delivered effects cannot be undone."
        if terminal and accepted else
        "This run is already terminal; no further cancellation is needed."
        if terminal else
        "Cancellation is cooperative. Queued work checks before starting; "
        "running work checks at node boundaries and supported in-flight polls. "
        "An external effect already delivered cannot be undone. Read the run "
        "again to observe the actual terminal status."
    )
    return json.dumps({
        "text": ("**Run finished.** " if terminal else "**Cancel requested.** ") + note,
        "run_id": rid,
        "status": record.get("status"),
        "terminal": terminal,
        "cancel_requested": requested,
        "note": note,
    })


def _action_get_run_output(kwargs: dict[str, Any]) -> str:
    from tinyassets.runs import get_run as _get_run

    rid = kwargs.get("run_id", "").strip()
    if not rid:
        return json.dumps({"error": "run_id is required."})

    record = _get_run(_base_path(), rid)
    if record is None or not _run_matches_scope(record, kwargs):
        return json.dumps({"error": f"Run '{rid}' not found."})
    if not _run_read_allowed(record):
        return _run_read_denied_error(record, "get_run_output")

    field = kwargs.get("field_name", "").strip()
    output = record.get("output") or {}
    if kwargs.get("bounded_output"):
        from tinyassets.api.run_outputs import read_output

        return json.dumps({
            "run_id": rid, "status": record.get("status"),
            **read_output(output, field_name=kwargs.get("field_name", ""),
                          offset=kwargs.get("output_offset", 0),
                          max_chars=kwargs.get("output_max_chars", 8192)),
        }, ensure_ascii=False)
    if field:
        if field not in output:
            return json.dumps({
                "error": f"Output field '{field}' not present on run.",
                "available_fields": sorted(output.keys()),
            })
        value = output[field]
        # Scalar/single-artifact shape per tool_return_shapes.md —
        # tight one-liner + full value for scripts.
        preview = str(value)
        if len(preview) > 240:
            preview = preview[:240].rstrip() + "…"
        branch_label = _branch_name_for_run(record)
        text = (
            f"**{field}** (workflow '{branch_label}'):\n\n{preview}"
        )
        return json.dumps({
            "text": text,
            "run_id": rid,
            "field_name": field,
            "value": value,
        }, default=str)
    # Whole-output read — catalog of fields.
    branch_label = _branch_name_for_run(record)
    lines = [
        f"**Output from tinyassets '{branch_label}'** "
        f"(status: {record.get('status')})"
    ]
    if output:
        lines.append("")
        for key in sorted(output.keys()):
            val_preview = str(output[key])
            if len(val_preview) > 120:
                val_preview = val_preview[:120].rstrip() + "…"
            lines.append(f"- `{key}`: {val_preview}")
    else:
        lines.append("\n_(no output produced)_")
    return json.dumps({
        "text": "\n".join(lines),
        "run_id": rid,
        "status": record.get("status"),
        "output": output,
    }, default=str)


def _action_attach_existing_child_run(kwargs: dict[str, Any]) -> str:
    """Attach a completed child run receipt to a waiting parent run."""
    from tinyassets.runs import ChildRunAttachmentError, attach_existing_child_run

    parent_run_id = kwargs.get("run_id", "").strip()
    child_run_id = kwargs.get("child_run_id", "").strip()
    child_branch_def_id = kwargs.get("child_branch_def_id", "").strip()
    output_digest = kwargs.get("output_digest", "").strip()

    from tinyassets.runs import get_run as _get_run

    parent_record = _get_run(_base_path(), parent_run_id)
    if parent_record is not None and not _run_write_allowed(parent_record):
        return _run_write_denied_error(parent_record, "attach_existing_child_run")

    try:
        result = attach_existing_child_run(
            _base_path(),
            parent_run_id=parent_run_id,
            child_run_id=child_run_id,
            child_branch_def_id=child_branch_def_id,
            output_digest=output_digest,
            actor=_run_actor_for_kwargs(kwargs),
        )
    except ChildRunAttachmentError as exc:
        payload: dict[str, Any] = {
            "error": str(exc),
            "error_code": exc.code,
        }
        payload.update(exc.details)
        return json.dumps(payload, default=str)

    text = (
        "**Child receipt attached.** "
        f"Parent run `{result['parent_run_id']}` now references child run "
        f"`{result['child_run_id']}` via `{result['stable_evidence_handle']}`. "
        "This is a receipt validation path only."
    )
    return json.dumps({
        "text": text,
        "run_id": result["parent_run_id"],
        **result,
    }, default=str)


def _action_resume_run(kwargs: dict[str, Any]) -> str:
    """Resume an INTERRUPTED run from its SqliteSaver checkpoint.

    Auth re-check is performed at resume time — the caller must still own
    the run. If the run is already in RESUMED status, the call is
    idempotent and returns the existing run_id.
    """
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.branches import BranchDefinition
    from tinyassets.daemon_server import get_branch_definition
    from tinyassets.runs import ResumeError, resume_run

    _ensure_runs_recovery()

    run_id = kwargs.get("run_id", "").strip()
    if not run_id:
        return json.dumps({"error": "run_id is required."})

    from tinyassets.runs import get_run as _get_run

    _resume_record = _get_run(_base_path(), run_id)
    if _resume_record is not None and not _run_write_allowed(_resume_record):
        return _run_write_denied_error(_resume_record, "resume_run")

    actor = _current_actor()

    def _branch_lookup(branch_def_id: str, _version: int) -> BranchDefinition | None:
        try:
            source_dict = get_branch_definition(_base_path(), branch_def_id=branch_def_id)
            return BranchDefinition.from_dict(source_dict)
        except Exception:
            return None

    provider_call: Any = None
    try:
        from tinyassets.providers.call import (
            call_provider as provider_call,
        )
        provider_call = _bind_run_provider_call(
            provider_call,
            # Provider binding is unchanged by the read gate's queue fallback.
            _run_actor_universe_id(_resume_record or {}),
        )
    except ImportError:
        provider_call = None

    try:
        outcome = resume_run(
            _base_path(),
            run_id=run_id,
            actor=actor,
            branch_lookup=_branch_lookup,
            provider_call=provider_call,
        )
    except ResumeError as exc:
        return json.dumps({
            "error": str(exc), "reason": exc.reason, "current_status": exc.current_status,
        })
    except Exception as exc:
        logger.exception("resume_run failed for %s", run_id)
        return json.dumps({"error": f"Resume failed: {exc}"})

    text = "\n".join([
        f"**Run {outcome.status}.** Resume handed to the background executor.",
        "",
        f"Error: {outcome.error}" if outcome.error else "",
        "Use `read_graph target=\"run\" run_id=\"<run id>\"` to check "
        "progress. Cancel is not exposed by the advertised handles.",
    ]).strip()

    return json.dumps({
        "text": text,
        "run_id": outcome.run_id,
        "status": outcome.status,
        "output": outcome.output,
        "error": outcome.error,
    })


def _action_estimate_run_cost(kwargs: dict[str, Any]) -> str:
    """Estimate cost and time for running a branch before dispatch.

    Returns a structured estimate so the chatbot can narrate cost/time
    framing before the user commits to a paid-market bid or free-queue
    wait. Read-only — no provider calls, no writes.

    Confidence levels:
    - "low": branch has never been run (estimate from node declarations).
    - "medium": 1-4 prior completed runs exist (use average).
    - "high": 5+ prior completed runs exist (use median of sample).
    """
    from tinyassets.branches import BranchDefinition
    from tinyassets.daemon_server import get_branch_definition
    from tinyassets.runs import RUN_STATUS_COMPLETED, list_runs

    bid = kwargs.get("branch_def_id", "").strip()
    if not bid:
        return json.dumps({"error": "branch_def_id is required."})

    try:
        source_dict = get_branch_definition(_base_path(), branch_def_id=bid)
    except Exception:
        return json.dumps({"error": f"Branch '{bid}' not found."})

    branch = BranchDefinition.from_dict(source_dict)
    node_count = len(branch.node_defs)

    # Per-node cost heuristic: roughly 0.01 credits per node for a
    # prompt-template node (LLM call), 0.001 for a code node (exec only).
    # These are illustrative baseline defaults — real pricing depends on
    # the provider bid the user sets at dispatch time (paid_market model).
    credits_per_node: dict[str, float] = {}
    for n in branch.node_defs:
        if n.prompt_template:
            credits_per_node[n.node_id] = 0.01
        else:
            credits_per_node[n.node_id] = 0.001

    estimated_paid_market_credits = round(sum(credits_per_node.values()), 4)

    # Confidence: check prior completed run history.
    try:
        prior_runs = list_runs(
            _base_path(), branch_def_id=bid, status=RUN_STATUS_COMPLETED,
        )
    except Exception:
        prior_runs = []

    run_count = len(prior_runs)
    if run_count == 0:
        confidence = "low"
    elif run_count < 5:
        confidence = "medium"
    else:
        confidence = "high"

    # Free-queue ETA: best-effort from dispatcher queue depth.
    free_queue_eta_hours: float | None = None
    free_queue_caveat: str | None = None
    try:
        from tinyassets.dispatcher import get_queue_depth
        queue_depth = get_queue_depth()
        # Rough heuristic: ~10 min per queued run ahead of this one.
        free_queue_eta_hours = round((queue_depth * 10) / 60, 2)
    except Exception:
        free_queue_caveat = (
            "Dispatcher queue depth unavailable — free_queue_eta_hours is null. "
            "Dispatcher may be disabled or not yet initialised."
        )

    # Build a chatbot-quotable basis string.
    llm_nodes = sum(1 for n in branch.node_defs if n.prompt_template)
    code_nodes = node_count - llm_nodes
    basis_parts = [
        f"{node_count} node(s) total: {llm_nodes} LLM node(s) at ~0.01 credits each, "
        f"{code_nodes} code/other node(s) at ~0.001 credits each.",
        f"Confidence: {confidence} ({run_count} prior completed run(s)).",
    ]
    if free_queue_caveat:
        basis_parts.append(free_queue_caveat)
    else:
        basis_parts.append(
            f"Free-queue ETA based on ~{free_queue_eta_hours}h "
            "(estimated from current queue depth)."
        )
    basis = " ".join(basis_parts)

    return json.dumps({
        "branch_def_id": bid,
        "node_count": node_count,
        "estimated_paid_market_credits": estimated_paid_market_credits,
        "free_queue_eta_hours": free_queue_eta_hours,
        "confidence": confidence,
        "basis": basis,
        "prior_run_count": run_count,
    })



def _action_query_runs(kwargs: dict[str, Any]) -> str:
    from tinyassets.runs import _VALID_AGGREGATES, query_runs

    bid = kwargs.get("branch_def_id", "").strip()
    raw_filters = kwargs.get("filters_json", "") or kwargs.get("filters", "") or ""
    raw_select = kwargs.get("select", "") or ""
    raw_aggregate = kwargs.get("aggregate_json", "") or kwargs.get("aggregate", "") or ""
    raw_limit = kwargs.get("limit", _DEFAULT_QUERY_LIMIT) or _DEFAULT_QUERY_LIMIT

    filters: dict[str, Any] = {}
    if raw_filters:
        try:
            filters = json.loads(raw_filters) if isinstance(raw_filters, str) else raw_filters
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "filters_json is not valid JSON."})

    select: list[str] = []
    if raw_select:
        if isinstance(raw_select, str):
            select = [s.strip() for s in raw_select.split(",") if s.strip()]
        elif isinstance(raw_select, list):
            select = raw_select

    aggregate: dict[str, Any] | None = None
    if raw_aggregate:
        try:
            agg_parsed = (
                json.loads(raw_aggregate) if isinstance(raw_aggregate, str)
                else raw_aggregate
            )
            if isinstance(agg_parsed, dict):
                agg_fn = agg_parsed.get("fn", agg_parsed.get("op", "count"))
                if agg_fn not in _VALID_AGGREGATES:
                    return json.dumps({
                        "error": f"aggregate.fn must be one of: {sorted(_VALID_AGGREGATES)}",
                    })
                aggregate = agg_parsed
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"error": "aggregate_json is not valid JSON."})

    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = _DEFAULT_QUERY_LIMIT

    result = query_runs(
        _base_path(),
        branch_def_id=bid,
        filters=filters,
        select=select,
        aggregate=aggregate,
        limit=limit,
        # Exclude runs of a private universe the caller cannot read BEFORE
        # projection/aggregation, so select/aggregate can't leak private data.
        row_filter=lambda r: _run_read_allowed(
            {"actor": r["actor"], "queue_universe_id": r["queue_universe_id"]}
        ),
    )
    return json.dumps(result, default=str)


def _action_record_run_receipt(kwargs: dict[str, Any]) -> str:
    from tinyassets.runs import record_run_receipt

    run_id = (kwargs.get("run_id") or "").strip()
    receipt_type = (kwargs.get("receipt_type") or "").strip()
    node_id = (kwargs.get("node_id") or "").strip()
    payload_raw = kwargs.get("payload_json", "") or kwargs.get("payload", "") or ""
    if not run_id:
        return json.dumps({"error": "run_id is required."})
    if not receipt_type:
        return json.dumps({"error": "receipt_type is required."})
    if not payload_raw:
        return json.dumps({"error": "payload_json is required."})

    try:
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
    except (json.JSONDecodeError, TypeError) as exc:
        return json.dumps({"error": f"payload_json is not valid JSON: {exc}"})
    if not isinstance(payload, dict):
        return json.dumps({"error": "payload_json must decode to a JSON object."})

    from tinyassets.runs import get_run as _get_run

    _receipt_record = _get_run(_base_path(), run_id)
    if _receipt_record is not None and not _run_write_allowed(_receipt_record):
        return _run_write_denied_error(_receipt_record, "record_run_receipt")

    try:
        receipt = record_run_receipt(
            _base_path(),
            run_id=run_id,
            receipt_type=receipt_type,
            node_id=node_id,
            payload=payload,
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    return json.dumps({
        "status": "recorded",
        "receipt": receipt,
    }, default=str)


def _action_list_run_receipts(kwargs: dict[str, Any]) -> str:
    from tinyassets.runs import list_run_receipts

    # When scoped to a specific run, don't expose a private universe's receipts.
    _lrr_run_id = (kwargs.get("run_id") or "").strip()
    if _lrr_run_id:
        from tinyassets.runs import get_run as _get_run

        _lrr_record = _get_run(_base_path(), _lrr_run_id)
        if _lrr_record is not None and not _run_read_allowed(_lrr_record):
            return _run_read_denied_error(_lrr_record, "list_run_receipts")

    raw_limit = kwargs.get("limit", _DEFAULT_QUERY_LIMIT) or _DEFAULT_QUERY_LIMIT
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = _DEFAULT_QUERY_LIMIT
    try:
        receipts = list_run_receipts(
            _base_path(),
            run_id=(kwargs.get("run_id") or "").strip(),
            receipt_type=(kwargs.get("receipt_type") or "").strip(),
            subject_id=(kwargs.get("subject_id") or "").strip(),
            limit=limit,
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    # Filter out receipts whose run belongs to a private universe the caller
    # cannot read — covers the no-run_id enumeration mode too. Per-run cache so
    # many receipts of one run cost one access check.
    from tinyassets.runs import get_run as _get_run

    _visible: dict[str, bool] = {}

    def _receipt_visible(rc: dict[str, Any]) -> bool:
        rid = str(rc.get("run_id") or "")
        if not rid:
            return True
        if rid not in _visible:
            rec = _get_run(_base_path(), rid)
            _visible[rid] = rec is None or _run_read_allowed(rec)
        return _visible[rid]

    receipts = [rc for rc in receipts if _receipt_visible(rc)]
    return json.dumps({
        "receipts": receipts,
        "count": len(receipts),
    }, default=str)


_DEFAULT_QUERY_LIMIT = 100


def _action_run_routing_evidence(kwargs: dict[str, Any]) -> str:
    """Return recent run records shaped for provider/routing self-audit.

    Answers "which LLM answered the last call?" and "why did the run fail?"
    Each record includes derived latency_ms, failure_class, suggested_action,
    and a caveat noting that provider_used / token_count fields are not yet
    in the runs schema (pending schema migration).
    """
    from tinyassets.runs import list_recent_runs

    bid = (kwargs.get("branch_def_id") or "").strip()
    raw_limit = kwargs.get("limit", 10)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = 10

    records = list_recent_runs(_base_path(), branch_def_id=bid, limit=limit)
    # Don't expose routing evidence for runs of a private universe the caller
    # cannot read.
    records = [r for r in records if _run_read_allowed(r)]
    return json.dumps({
        "runs": records,
        "count": len(records),
        "caveat": records[0]["caveat"] if records else (
            f'No runs found. Execute it with run_graph branch_def_id="{bid}" '
            'inputs_json="<state JSON>"; dedicated routing-evidence lookup is '
            "not exposed by the advertised handles."
        ),
    }, default=str)


# ---------------------------------------------------------------------------
# get_memory_scope_status — self-auditing primitive §4.1
# ---------------------------------------------------------------------------


def _action_get_memory_scope_status(kwargs: dict[str, Any]) -> str:
    """Snapshot of memory-scope enforcement state for chatbot self-audit.

    Self-auditing-tools pattern (§4.1). Answers: "Is tiered scope active?
    Which tiers are being enforced? Have any scope mismatches been logged?"
    Returns concrete evidence the chatbot can narrate; does not infer.

    Shape (schema_version=1):
        {
          "schema_version": int,
          "tiered_scope_enabled": bool,
          "flag_state": str,
          "active_enforcement_tiers": [str, ...],
          "all_scope_tiers": [str, ...],
          "retrieval_stats_by_tier": {},
          "recent_scope_mismatch_warnings": [str, ...],
          "caveats": [str, ...],
          "actionable_next_steps": [str, ...],
        }
    """
    import os as _os

    from tinyassets.retrieval.router import tiered_scope_enabled

    flag_on = tiered_scope_enabled()
    flag_raw = _os.environ.get("TINYASSETS_TIERED_SCOPE", "off")
    all_tiers = ["universe_id", "goal_id", "branch_id", "user_id"]
    active_tiers = all_tiers if flag_on else ["universe_id"]

    universe_id = _request_universe(kwargs.get("universe_id") or "")
    # Don't expose a universe's activity.log / scope-mismatch warnings. These are
    # raw log LINES, so the gate is `read_content`, not the legacy read bit.
    #
    # It used to be `universe_access_allows(write=False)` alone. That bit is
    # `public_read`, which `set_universe_visibility` turns on whenever a level
    # grants a public visitor ANY capability — so a `metadata_only` universe,
    # whose whole point is that it withholds content, disclosed its literal
    # activity lines to any authenticated principal here. Reproduced by the Codex
    # cross-family review of PR #4019, which could only be reached deliberately
    # once that PR gave an owner a way to select `metadata_only`.
    #
    # `visibility_permits` is tighten-only — it ANDs the legacy gate with the
    # declared level — so this subsumes the old check rather than replacing it.
    from tinyassets.api.permissions import universe_access_error
    from tinyassets.api.visibility import visibility_permits

    if not visibility_permits(universe_id, "read_content"):
        return json.dumps(universe_access_error(
            universe_id=universe_id, write=False,
            action="get_memory_scope_status", surface="extensions",
        ))
    udir = _universe_dir(universe_id)
    log_content = _read_text(udir / "activity.log")
    mismatch_lines: list[str] = []
    if log_content:
        for line in log_content.strip().splitlines():
            if "retrieval.scope_mismatch" in line:
                mismatch_lines.append(line.strip())
    recent_mismatches = mismatch_lines[-10:]

    caveats: list[str] = [
        "retrieval_stats_by_tier is not yet instrumented (Stage 2b.3);"
        " per-tier drop counts will appear in Stage 2c.",
    ]
    if not flag_on:
        caveats.append(
            "TINYASSETS_TIERED_SCOPE=off: only universe_id is enforced."
            " goal_id / branch_id / user_id isolation is NOT active."
        )
    if recent_mismatches:
        caveats.append(
            f"{len(recent_mismatches)} recent scope-mismatch warning(s) in"
            " activity.log — inspect recent_scope_mismatch_warnings."
        )

    next_steps: list[str] = []
    if not flag_on:
        next_steps.append(
            "Set TINYASSETS_TIERED_SCOPE=on to enable full four-tier"
            " isolation (command center/goal/branch/user)."
        )
    next_steps.append(
        "Check activity.log for 'retrieval.scope_mismatch' to diagnose"
        " any cross-universe content bleed."
    )

    return json.dumps({
        "schema_version": 1,
        "tiered_scope_enabled": flag_on,
        "flag_state": flag_raw,
        "active_enforcement_tiers": active_tiers,
        "all_scope_tiers": all_tiers,
        "retrieval_stats_by_tier": {},
        "recent_scope_mismatch_warnings": recent_mismatches,
        "caveats": caveats,
        "actionable_next_steps": next_steps,
        "universe_id": universe_id,
    })


def _legacy_request_provider(kwargs):
    """Existing scalar path only; admitted origins bind after their start CAS."""
    try:
        from tinyassets.providers.call import call_provider

        return _bind_run_provider_call(
            call_provider, _request_universe(kwargs.get("universe_id") or ""),
        )
    except ImportError:
        return None


def _action_run_branch_version(kwargs: dict[str, Any]) -> str:
    """Execute a published branch_version snapshot.

    Phase A item 6 (Task #65b). Sibling to ``run_branch``; resolves a
    ``branch_version_id`` via ``branch_versions``, reconstructs a
    ``BranchDefinition`` from the immutable snapshot, and hands off to
    the same async executor pool. Records the ``branch_version_id`` on
    the new ``runs.branch_version_id`` column for attribution.
    """
    from tinyassets.runs import (
        MissingRequiredInputs,
        SnapshotSchemaDrift,
        execute_branch_version_async,
    )

    _ensure_runs_recovery()

    bvid = (kwargs.get("branch_version_id") or "").strip()
    if not bvid:
        return json.dumps({"error": "branch_version_id is required."})

    inputs_raw = kwargs.get("inputs_json", "").strip()
    inputs: dict[str, Any] = {}
    if inputs_raw:
        try:
            parsed = json.loads(inputs_raw)
            if not isinstance(parsed, dict):
                return json.dumps({
                    "error": "inputs_json must decode to a JSON object.",
                })
            inputs = parsed
        except json.JSONDecodeError as exc:
            return json.dumps({
                "error": f"inputs_json is not valid JSON: {exc}",
            })

    # Parse `recursion_limit_override` — same shape as run_branch: any positive
    # integer, no upper bound.
    _rl_raw = kwargs.get("recursion_limit_override", "")
    recursion_limit_override: int | None = None
    if _rl_raw:
        try:
            _rl_val = int(_rl_raw)
        except (TypeError, ValueError):
            return json.dumps({"error": "recursion_limit_override must be an integer."})
        if _rl_val < 1:
            return json.dumps({
                "error": (
                    f"recursion_limit_override {_rl_val} is not a number of steps. "
                    "Use a positive integer; there is no upper bound."
                ),
            })
        recursion_limit_override = _rl_val

    # Readability BEFORE the snapshot is loaded: every caller, not only the
    # explicit run_graph path. A goal's canonical run reached this with any
    # version id, and the preflight then returned the private snapshot's input
    # names and field descriptions (astra refute 2026-09-30). Unreadable answers
    # exactly as absent does.
    from tinyassets.api.branches import _resolve_readable_version

    if _resolve_readable_version(bvid, str(_base_path())) is None:
        return json.dumps({
            "error": f"branch_version_id {bvid!r} not found in branch_versions",
        })

    try:
        from tinyassets.api.run_files import dispatch_file_branch
        from tinyassets.runs import _load_branch_version

        branch = _load_branch_version(_base_path(), bvid)
        outcome = dispatch_file_branch(
            branch, inputs, universe_id=_request_universe(kwargs.get("universe_id") or ""),
            run_name=kwargs.get("run_name", ""), recursion_limit_override=recursion_limit_override,
            branch_version_id=bvid,
        )
        if outcome is None:
            provider_call = _legacy_request_provider(kwargs)
            outcome = execute_branch_version_async(
                _base_path(),
                branch_version_id=bvid,
                inputs=inputs,
                run_name=kwargs.get("run_name", ""),
                actor=_run_actor_for_kwargs(kwargs),
                owner_user_id=_run_owner_for_request(),
                _enqueue_universe_id=_request_universe(kwargs.get("universe_id") or ""),
                provider_call=provider_call,
                recursion_limit_override=recursion_limit_override,
            )
    except MissingRequiredInputs as exc:
        return _missing_required_inputs_response(exc)
    except KeyError as exc:
        return json.dumps({"error": str(exc).strip("'\"")})
    except SnapshotSchemaDrift as exc:
        return json.dumps({
            "error": str(exc),
            "failure_class": SnapshotSchemaDrift.failure_class,
            "suggested_action": SnapshotSchemaDrift.suggested_action,
            "actionable_by": SnapshotSchemaDrift.actionable_by,
        })
    except Exception as exc:
        logger.exception("run_branch_version failed for %s", bvid)
        return json.dumps(_classify_run_error(exc, bvid))

    # Write-ack mirroring _action_run_branch's response shape.
    error_annotation = _classify_run_outcome_error(outcome.error) if outcome.error else None
    error_lines: list[str] = []
    if outcome.error:
        error_lines.append(f"Error: {outcome.error}")
    if error_annotation:
        error_lines.append(f"Suggested action: {error_annotation[1]}")
    text = "\n".join([
        f"**Run {outcome.status}.** Version-based workflow accepted.",
        "",
        *error_lines,
        "Use `read_graph target=\"run\" run_id=\"<run id>\"` for a "
        "snapshot. Wait and cancel controls are not exposed by the "
        "advertised handles.",
    ]).strip()

    result: dict[str, Any] = {
        "text": text,
        "run_id": outcome.run_id,
        "status": outcome.status,
        "output": outcome.output,
        "error": outcome.error,
        "branch_version_id": bvid,
    }
    if error_annotation:
        result["failure_class"] = error_annotation[0]
        result["suggested_action"] = error_annotation[1]
        result["actionable_by"] = _actionable_by(error_annotation[0])
    return json.dumps(result)


def _action_rollback_merge(kwargs: dict[str, Any]) -> str:
    """Surgical-rollback (Task #22 Phase B). Explicit rollback capability.

    Required kwargs: ``branch_version_id`` (seed), ``reason``.
    Optional kwargs: ``severity`` (P0/P1/P2; default P1).

    Computes the dependency closure from the seed, atomically flips each
    closure version to ``status='rolled_back'`` + emits one
    ``caused_regression`` event per version (single runs-DB transaction),
    then re-points any goal canonical pointing into the closure to the
    nearest non-rolled-back ancestor (separate author_server-DB step
    per cross-DB refinement; see ``tinyassets/rollback.py`` module
    docstring).
    """
    from tinyassets.api.engine_helpers import _current_actor
    from tinyassets.daemon_server import CAP_ROLLBACK_BRANCH
    from tinyassets.rollback import rollback_merge_orchestrator

    bvid = (kwargs.get("branch_version_id") or "").strip()
    reason = (kwargs.get("reason") or "").strip()
    severity = (kwargs.get("severity") or "P1").strip().upper()
    if not bvid:
        return json.dumps({"error": "branch_version_id is required."})
    if not reason:
        return json.dumps({"error": "reason is required."})

    actor = _current_actor()
    if not _current_actor_has_capability(CAP_ROLLBACK_BRANCH):
        return json.dumps({
            "error": (
                f"Missing capability: {CAP_ROLLBACK_BRANCH} "
                f"(request actor was {actor!r})."
            ),
        })

    result = rollback_merge_orchestrator(
        _base_path(),
        bvid,
        reason=reason,
        set_by=actor,
        severity=severity,
    )
    if result.get("status") == "rejected":
        return json.dumps(result, default=str)

    closure = result.get("closure", [])
    repoint = result.get("repoint", {})
    text_lines = [
        f"**Rolled back** {len(closure)} branch_version(s) seeded from "
        f"`{bvid}` (severity {severity}).",
        f"Reason: {reason}",
    ]
    repointed_count = repoint.get("repointed_count", 0)
    if repointed_count:
        text_lines.append(
            f"Re-pointed canonical bindings on {repointed_count} Goal(s) "
            "to nearest non-rolled-back ancestor."
        )
    # DESIGN-008 round 4 — selector bindings are cleared (not walked up)
    # so the leaderboard read path falls back to the platform default.
    selector_repoint = result.get("selector_repoint", {})
    selector_repointed_count = selector_repoint.get("repointed_count", 0)
    if selector_repointed_count:
        text_lines.append(
            f"Cleared selector bindings on {selector_repointed_count} "
            "Goal(s); leaderboard falls back to platform default until "
            "an operator rebinds through the internal selector-binding "
            "surface, which is not exposed by the advertised handles."
        )
    return json.dumps({
        "text": "\n".join(text_lines),
        **result,
    }, default=str)


def _action_get_rollback_history(kwargs: dict[str, Any]) -> str:
    """Read-only rollback history surface. No authority restriction.

    Optional kwargs: ``since_days`` (default 7).
    """
    from tinyassets.rollback import get_rollback_history

    try:
        since_days = int(kwargs.get("since_days", 7) or 7)
    except (TypeError, ValueError):
        since_days = 7
    since_days = max(1, min(since_days, 365))

    rollbacks = get_rollback_history(_base_path(), since_days=since_days)
    if rollbacks:
        text_lines = [
            f"**{len(rollbacks)} rollback(s)** in the past {since_days} day(s):",
            "",
        ]
        for r in rollbacks[:20]:
            text_lines.append(
                f"- `{r['branch_version_id']}` · "
                f"{r['rolled_back_at']} · by `{r['rolled_back_by']}` · "
                f"{r['rolled_back_reason']}"
            )
        if len(rollbacks) > 20:
            text_lines.append(f"_… and {len(rollbacks) - 20} more._")
        text = "\n".join(text_lines)
    else:
        text = f"_No rollbacks in the past {since_days} day(s)._"
    return json.dumps({
        "text": text,
        "rollbacks": rollbacks,
        "count": len(rollbacks),
        "since_days": since_days,
    }, default=str)


from tinyassets.api.webhook_ops import _action_create_source as _source_create  # noqa: E402
from tinyassets.api.webhook_ops import _action_list_sources as _source_list  # noqa: E402
from tinyassets.api.webhook_ops import _action_list_webhooks as _webhook_list  # noqa: E402
from tinyassets.api.webhook_ops import _action_mint_webhook as _webhook_mint  # noqa: E402
from tinyassets.api.webhook_ops import _action_revoke_source as _source_revoke  # noqa: E402
from tinyassets.api.webhook_ops import _action_revoke_webhook as _webhook_revoke  # noqa: E402

_RUN_ACTIONS: dict[str, Any] = {
    "run_branch": _action_run_branch,
    "run_branch_version": _action_run_branch_version,
    "get_run": _action_get_run,
    "list_runs": _action_list_runs,
    "stream_run": _action_stream_run,
    "wait_for_run": _action_wait_for_run,
    "cancel_run": _action_cancel_run,
    "get_run_output": _action_get_run_output,
    "attach_existing_child_run": _action_attach_existing_child_run,
    "resume_run": _action_resume_run,
    "estimate_run_cost": _action_estimate_run_cost,
    "query_runs": _action_query_runs,
    "record_run_receipt": _action_record_run_receipt,
    "list_run_receipts": _action_list_run_receipts,
    "get_routing_evidence": _action_run_routing_evidence,
    "get_memory_scope_status": _action_get_memory_scope_status,
    "rollback_merge": _action_rollback_merge,
    "get_rollback_history": _action_get_rollback_history,
    "mint_webhook": _webhook_mint,
    "revoke_webhook": _webhook_revoke,
    "list_webhooks": _webhook_list,
    "create_source": _source_create,
    "revoke_source": _source_revoke,
    "list_sources": _source_list,
}

_RUN_WRITE_ACTIONS: frozenset[str] = frozenset(
    {"run_branch", "run_branch_version", "cancel_run", "resume_run",
     "rollback_merge", "attach_existing_child_run", "record_run_receipt",
     "mint_webhook", "revoke_webhook", "create_source", "revoke_source"}
)

# Native deliveries retain the canonical action admission and ledger boundary.
from functools import partial as _partial  # noqa: E402

from tinyassets.api.deliveries import READ_ACTIONS as _DELIVERY_READS  # noqa: E402
from tinyassets.api.deliveries import WRITE_ACTIONS as _DELIVERY_WRITES  # noqa: E402
from tinyassets.api.deliveries import action as _delivery_action  # noqa: E402

_RUN_ACTIONS.update({name: _partial(_delivery_action, name)
                     for name in _DELIVERY_READS | _DELIVERY_WRITES})
_RUN_WRITE_ACTIONS = _RUN_WRITE_ACTIONS | _DELIVERY_WRITES


def _dispatch_run_action(
    action: str,
    handler: Any,
    kwargs: dict[str, Any],
) -> str:
    """Dispatch a Phase 3 run action, ledger the write actions.

    run_branch and cancel_run both mutate durable state so they land in
    the global ledger with the run_id as the target.
    """
    from tinyassets.api.branches import _append_global_ledger, ledger_actor
    from tinyassets.api.engine_helpers import _truncate

    scope_error = _branch_run_scope_error(action, kwargs)
    if scope_error is not None:
        return scope_error

    result_str = handler(kwargs)
    if action not in _RUN_WRITE_ACTIONS:
        return result_str

    try:
        result = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return result_str
    if not isinstance(result, dict):
        return result_str
    # Only skip ledger on actual error responses (non-empty 'error' value).
    # _action_run_branch always includes an empty 'error' field on success.
    if result.get("error"):
        return result_str

    try:
        target = (result.get("delivery_id", "") if action == "deliver_output"
                  else result.get("run_id", "") or kwargs.get("run_id", ""))
        summary_bits = [action]
        if kwargs.get("branch_def_id"):
            summary_bits.append(f"branch={kwargs['branch_def_id']}")
        if result.get("status"):
            summary_bits.append(f"status={result['status']}")
        _append_global_ledger(
            action,
            actor=ledger_actor(),
            target=str(target),
            summary=_truncate(" ".join(summary_bits)),
            payload=None,
        )
    except Exception as exc:
        logger.warning("Ledger write failed for run action %s: %s", action, exc)
    return result_str
