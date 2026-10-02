"""Canonical-turn adapter to the existing immutable run envelope.

Authenticated handles call this adapter; context is trusted server preparation,
never caller authority. Common run dispatch and fresh run-owned provider binding
remain the execution owners. An admitted request is not permission to replay it.
"""

import json
import sqlite3
from pathlib import Path

from tinyassets.branches import BranchDefinition
from tinyassets.consumer_selection import resolve_selection_in_transaction
from tinyassets.run_input_origin import OriginHeld, classify_admission_observation
from tinyassets.runs import preflight_required_inputs, runs_db_path
from tinyassets.scoped_reset import ScopedResetError
from tinyassets.sqlite_connection import ClosingConnection
from tinyassets.storage import conversation_run_admissions as canonical
from tinyassets.storage import run_input_admissions
from tinyassets.storage.current_home import CurrentHomeChanged


def _lookup_key(conn, scope, request_key):
    key = canonical._key(request_key)
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                          "AND name='conversation_run_admissions'").fetchone()
    if exists is None:
        return None
    found = conn.execute(
        "SELECT admission_id FROM conversation_run_admissions WHERE owner_user_id=? "
        "AND universe_id=? AND session_id=? AND request_key_hash=?",
        (scope.owner, scope.universe, scope.session, key),
    ).fetchone()
    return canonical._read(conn, scope, found[0]) if found else None


def _envelope(scope, row, conn):
    status = conn.execute("SELECT status FROM runs WHERE run_id=?", (row["run_id"],)).fetchone()[0]
    projection = row["projection_state"]
    state = ("expired" if projection == "expired" else "held" if projection == "held" else
             status if status in {"failed", "cancelled", "interrupted"} else
             "completed" if status == "completed" and projection == "committed" else "pending")
    result = {"universe_id": scope.universe, "consumer_turn": {
        "version": 1, "turn_id": row["admission_id"], "run_id": row["run_id"],
        "state": state, "run_status": status, "projection": projection,
    }}
    if status in {"queued", "running"} and projection != "expired":
        metadata = conn.execute(
            "SELECT origin_kind,origin_version,origin_options_json,"
            "execution_started_at,claim_token "
            "FROM run_input_admissions WHERE run_id=? AND owner_id=? AND universe_id=?",
            (row["run_id"], scope.owner, scope.universe),
        ).fetchone()
        observation = classify_admission_observation(dict(metadata) if metadata else {}, status)
        if observation:
            result["consumer_turn"].update(observation, state="held")
            result["notice"] = observation["suggested_action"]
    if projection == "committed" and row["terminal_json"]:
        terminal = json.loads(row["terminal_json"])
        if terminal["speaker"] == "universe":
            result["reply"] = terminal["content"]
            if terminal["execution"] is not None:
                result["execution"] = terminal["execution"]
        else:
            result.update(error=terminal["content"], failure_notice=terminal["content"],
                          turn_failure=terminal["failure"], history_saved=True)
    elif status in {"completed", "failed", "cancelled", "interrupted"}:
        result["consumer_turn"]["state"] = "held" if projection != "expired" else "expired"
        result["notice"] = "Terminal history projection is unavailable; execution is not replayed."
    return result


def _project(scope, row):
    """Project a terminal pair; True when THIS call put the owner's message in the thread.

    Pending or uncertain execution never acquires a second start, and missing
    terminal evidence remains visible: neither is a new writer.
    """
    try:
        after = canonical.project_terminal(scope, row["admission_id"])
    except canonical.TerminalUnavailable:
        return False
    return row["projection_state"] != "committed" and after["projection_state"] == "committed"


def _announce_owner_message(base, *, owner, universe):
    """Outside the scope's lock: wake what subscribed to the owner's messages."""
    try:
        from tinyassets.automation_events import emit_owner_message

        emit_owner_message(Path(base) / universe, principal_id=owner)
    except Exception:  # noqa: BLE001 - an event must never fail the turn
        pass


def read_turn(base, *, owner, universe, request_key):
    """Observe and converge terminal history only; never dispatch or call providers."""
    try:
        with canonical.authorized_scope(base, owner=owner, universe=universe) as scope:
            with canonical.runs_transaction(scope) as conn:
                row = _lookup_key(conn, scope, request_key)
            if row is None:
                return {"error": "not_found"}
            projected = _project(scope, row)
            with canonical.runs_transaction(scope) as conn:
                result = _envelope(scope, canonical._read(conn, scope, row["admission_id"]), conn)
    except (PermissionError, ValueError, OSError, sqlite3.Error,
            CurrentHomeChanged, ScopedResetError):
        return {"error": "not_found"}
    if projected:
        _announce_owner_message(base, owner=owner, universe=universe)
    return result


def _observe_or_repair(base, *, owner, universe, row):
    with canonical.authorized_scope(base, owner=owner, universe=universe) as scope:
        projected = _project(scope, row)
        with canonical.runs_transaction(scope) as conn:
            result = _envelope(scope, canonical._read(conn, scope, row["admission_id"]), conn)
    if projected:
        _announce_owner_message(base, owner=owner, universe=universe)
    return result


def prepare_admitted_consumer(base, envelope, *, author_conn, runs_conn):
    """Static v1 adapter: validate original DATA under supplied worker fences."""
    from tinyassets.auth.provider import Identity
    from tinyassets.config import load_universe_config
    from tinyassets.foreground_run_provider import _ForegroundRunProviderSession
    from tinyassets.providers.base import UniverseContext
    from tinyassets.providers.call import bind_universe_provider_call, call_provider
    from tinyassets.run_input_runtime import PreparedRunExecution

    row = validate_prepared_turn(
        base, envelope, author_conn=author_conn, runs_conn=runs_conn,
    )
    owner, universe = row["owner_user_id"], row["universe_id"]
    context = json.loads(row["context_json"])

    def bind(base, supplied, branch):
        if supplied != envelope:
            raise PermissionError("canonical prepared envelope changed")
        # Discovery/provider preparation must never run inside SQL writers.
        session = _ForegroundRunProviderSession(
            base, universe_id=universe, principal_id=owner, provider_call=call_provider,
            model_preference_data=context["preferences"],
        )
        session.prepare(run_id=envelope["run_id"], branch=branch,
                        branch_version_id=envelope["branch_version_id"],
                        allowed_statuses={"queued"})
        return bind_universe_provider_call(
            session, UniverseContext(universe_dir=Path(base) / universe,
                                     config=load_universe_config(Path(base) / universe)),
            operation="run_graph",
        )

    # Versioned consumer v1 semantics: neither request nor component admits a
    # recursion/concurrency override. Do not inherit changed runtime defaults.
    return PreparedRunExecution(Identity(owner, owner), f"universe:{universe}", bind,
                                recursion_limit=100, concurrency_budget_override=None)


def settle_admitted_consumer(base, run_id):
    """Static notification adapter; current owner authorization precedes repair."""
    with sqlite3.connect(
        runs_db_path(base).as_uri() + "?mode=ro", uri=True, factory=ClosingConnection,
    ) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT admission_id,owner_user_id,universe_id FROM "
                           "conversation_run_admissions WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise PermissionError("canonical settlement identity unavailable")
    return _observe_or_repair(base, owner=row["owner_user_id"],
                              universe=row["universe_id"], row=row)


def _dispatch(base, row, *, owner, universe):
    """Initial, same-key and recovery calls share the static origin dispatcher."""
    from tinyassets.run_input_origins import dispatch_initial_run

    if (row["owner_user_id"], row["universe_id"]) != (owner, universe):
        raise PermissionError("canonical dispatch scope changed")
    dispatch_initial_run(base, run_id=row["run_id"])


def _installation_present(base, owner, universe):
    """Cheap default-path probe; no schema/home creation and no execution permission."""
    from tinyassets.storage import db_path

    with sqlite3.connect(
        db_path(base).as_uri() + "?mode=ro", uri=True, timeout=5,
        factory=ClosingConnection,
    ) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='agent_bindings'").fetchone() is None:
            return False
        rows = conn.execute("SELECT configuration_json FROM agent_bindings "
                            "WHERE universe_id=? AND created_by=?", (universe, owner)).fetchall()
        return any("turn_consumer" in json.loads(row[0]) for row in rows)


def converse_turn(base, *, owner, universe, message, input_method, model_choice, request):
    """Existing authenticated converse boundary; None means unchanged default path."""
    try:
        if request is None and not _installation_present(base, owner, universe):
            return None
        if request is not None:
            if (not isinstance(request, dict)
                    or set(request) != {"version", "request_key", "binding_id", "binding_revision"}
                    or type(request["version"]) is not int or request["version"] != 1):
                raise ValueError("invalid consumer request")
            canonical._key(request["request_key"])
            intent = {"version": 1, "message": message, "input_method": input_method,
                      "model_choice": model_choice, "binding_id": request["binding_id"],
                      "binding_revision": request["binding_revision"]}
        with canonical.authorized_scope(base, owner=owner, universe=universe) as scope:
            row = None
            if request is not None:
                with canonical.runs_transaction(scope) as conn:
                    row = canonical.find_intent_in_transaction(
                        conn, scope, request_key=request["request_key"], intent=intent,
                    )
            if row is None:
                selected = resolve_selection_in_transaction(
                    scope.author, owner=owner, universe=universe,
                )
                if selected is None:
                    return None if request is None else {"error": "consumer_not_selected"}
                if request is None:
                    return {"error": "consumer_request_required", "consumer_selection": {
                        "version": 1, "binding_id": selected["binding_id"],
                        "binding_revision": selected["binding_revision"],
                    }}
                source = canonical.authorize_source(
                    scope, selected["branch_version_id"], selected["content_hash"],
                )
                with canonical.runs_transaction(scope) as conn:
                    source_snapshot = canonical.load_source_in_transaction(conn, scope, source)
                if source_snapshot.get("author") != owner:
                    return {"error": "consumer_remix_required",
                            "notice": ("Make an owned copy of this shared workflow, publish it, "
                                       "then select that version for conversations."),
                            "source_version_id": source.version_id,
                            "next_action": {"handle": "write_graph", "target": "branch",
                                            "operation": "remix", "fork_from": source.version_id}}
                from tinyassets.conversation_store import load_recent_readonly
                from tinyassets.storage.model_preferences import _read as read_preferences

                prefs = read_preferences(scope.author, owner, universe)
                history = load_recent_readonly(scope.home, scope.session)
                context = {"version": 1, "history": [
                    {"speaker": item.speaker, "content": item.text} for item in history
                ], "preferences": {"version": 1,
                    "saved": prefs.policy.document() if prefs.policy else None,
                    "observed_generation": prefs.generation, "current": model_choice}}
        if row is None:
            row = reserve_prepared_turn(base, owner=owner, universe=universe,
                                        request_key=request["request_key"], intent=intent,
                                        context=context)
        _dispatch(base, row, owner=owner, universe=universe)
        return _observe_or_repair(base, owner=owner, universe=universe, row=row)
    except canonical.IntentConflict:
        return {"error": "consumer_request_conflict"}
    except OriginHeld:
        return {"error": "consumer_turn_held",
                "notice": ("This run's execution adapter is unavailable; "
                           "existing work is not replayed.")}
    except (ValueError, TypeError):
        return {"error": "invalid_consumer_request"}
    except (PermissionError, OSError, sqlite3.Error, canonical.TerminalUnavailable,
            CurrentHomeChanged, ScopedResetError):
        return {"error": "consumer_turn_held",
                "notice": ("The selected conversation could not proceed. "
                           "Existing work is not replayed.")}


def initialize(base):
    """Explicit schema setup only; not called inside request/worker fences."""
    canonical.initialize(base)
    with sqlite3.connect(runs_db_path(base), timeout=5, factory=ClosingConnection) as conn:
        run_input_admissions.ensure_schema(conn)


def _mapped_inputs(selection, intent, context):
    inputs = {selection["input_map"]["message"]: intent["message"]}
    if "history" in selection["input_map"]:
        inputs[selection["input_map"]["history"]] = context["history"]
    return inputs


def reserve_prepared_turn(base, *, owner, universe, request_key, intent, context):
    """Reserve exactly one run+envelope; never mark started, queue or invoke it."""
    with canonical.authorized_scope(base, owner=owner, universe=universe) as scope:
        with canonical.runs_transaction(scope) as conn:
            prior = canonical.find_intent_in_transaction(
                conn, scope, request_key=request_key, intent=intent,
            )
        if prior is not None:
            return prior  # Original intent wins over today's installation/history/defaults.
        selected = resolve_selection_in_transaction(scope.author, owner=owner, universe=universe)
        if (selected is None or selected["binding_id"] != intent["binding_id"]
                or selected["binding_revision"] != intent["binding_revision"]):
            raise PermissionError("consumer selection changed")
        if (not isinstance(context, dict) or context.get("version") != 1
                or not isinstance(context.get("history"), list)):
            raise ValueError("invalid captured consumer context")
        if context.get("preferences") is not None:
            from tinyassets.storage.model_preferences import _read as read_preferences

            current = read_preferences(scope.author, owner, universe)
            captured = context["preferences"]
            saved = current.policy.document() if current.policy else None
            if (current.generation != captured["observed_generation"]
                    or saved != captured["saved"]):
                raise PermissionError("model preferences changed before admission")
        source = canonical.authorize_source(
            scope, selected["branch_version_id"], selected["content_hash"],
        )
        with canonical.runs_transaction(scope) as conn:
            snapshot = canonical.load_source_in_transaction(conn, scope, source)
            selection = {**selected, "branch_def_id": snapshot["branch_def_id"]}
            inputs = _mapped_inputs(selection, intent, context)
            preflight_required_inputs(BranchDefinition.from_dict(snapshot), inputs)
            row = canonical.reserve_in_transaction(
                conn, scope, request_key=request_key, intent=intent, context=context,
                selection=selection, inputs=inputs,
            )
            run_input_admissions.accept_in_transaction(
                conn, run_id=row["run_id"], owner_id=owner, universe_id=universe,
                branch_version_id=selection["branch_version_id"],
                origin_kind="canonical_consumer", origin_version=1, origin_options={},
            )
            return row


def validate_prepared_turn(base, envelope, *, author_conn, runs_conn):
    """Current consumer authority inside the common worker's supplied fences.

    Returns original captured DATA, not PreparedRunExecution or a provider grant.
    The final worker adapter must additionally construct its run-owned provider
    binding outside SQL after shared start. No new connection/lock is acquired.
    """
    owner, universe = envelope["owner_id"], envelope["universe_id"]
    with canonical.scope_from_transactions(
        base, owner=owner, universe=universe, author_conn=author_conn, runs_conn=runs_conn,
    ) as scope:
        identity = runs_conn.execute(
            "SELECT admission_id FROM conversation_run_admissions WHERE run_id=? "
            "AND owner_user_id=? AND universe_id=?",
            (envelope["run_id"], owner, universe),
        ).fetchone()
        if identity is None:
            raise PermissionError("canonical request unavailable")
        row = canonical._read(runs_conn, scope, identity[0])
        if row["projection_state"] != "pending" or row["intent_json"] is None:
            raise PermissionError("canonical request is not executable")
        selected = resolve_selection_in_transaction(author_conn, owner=owner, universe=universe)
        captured = json.loads(row["selection_json"])
        if selected is None or captured != {**selected, "branch_def_id": envelope["branch_def_id"]}:
            raise PermissionError("consumer selection changed")
        source = canonical.authorize_source_in_transaction(
            runs_conn, scope, selected["branch_version_id"], selected["content_hash"],
        )
        snapshot = canonical.load_source_in_transaction(runs_conn, scope, source)
        if (envelope["branch_version_id"] != source.version_id
                or envelope["snapshot"] != snapshot
                or envelope["snapshot_sha256"] != source.content_hash):
            raise PermissionError("consumer source envelope changed")
        if envelope["inputs"] != _mapped_inputs(
            captured, json.loads(row["intent_json"]), json.loads(row["context_json"]),
        ):
            raise PermissionError("consumer input envelope changed")
        return row
