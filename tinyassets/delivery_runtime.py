"""Internal dispatch/recovery for already accepted receiver deliveries.

Not an intake API: acceptance, resource/file admission and public graph wiring
remain separate. Never treat a queued run row as a durable executor queue.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import threading

from tinyassets import runs
from tinyassets.api.receiver_links import _owned_branch
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.branches import BranchDefinition
from tinyassets.run_admission_envelope import encode_admission_envelope
from tinyassets.storage import _connect as author_connection
from tinyassets.storage import deliveries
from tinyassets.storage.delivery_lock import try_attempt_lock

logger = logging.getLogger(__name__)
_submitted = set()
_submitted_lock = threading.Lock()


def reject_file_references(value):
    """Fail-closed default for any path with no resolved file custody authority.

    These are reference envelope fields, not credential-key redaction. Ordinary
    structured fields named key/token remain exact data.
    """
    if isinstance(value, dict):
        if ({"handle_id", "artifact_id", "file_id"} & value.keys()
                or value.get("type") in ("file", "file_bundle")):
            raise ValueError("delivery_file_transfer_not_implemented")
        for child in value.values():
            reject_file_references(child)
    elif isinstance(value, list):
        for child in value:
            reject_file_references(child)


def is_file_reference(value):
    """Exactly the versioned public custody reference shape, nothing adjacent."""
    from tinyassets.run_file_contract import _FIELDS

    return isinstance(value, dict) and set(value) == _FIELDS | {"version"}


def carries_file_reference(value):
    if is_file_reference(value):
        return True
    if isinstance(value, dict):
        return any(carries_file_reference(child) for child in value.values())
    if isinstance(value, list):
        return any(carries_file_reference(child) for child in value)
    return False


def validate_output_envelopes(value, *, allow_file_references=False):
    """Typed pre-authority validator. Ownership resolution happens later and
    elsewhere; this only decides whether a *shape* may travel at all.

    Without a trusted source run there is nothing to resolve a reference against,
    so the refusal stays exactly as before. With one, only the exact versioned
    reference shape is admitted: ad-hoc handle/artifact envelopes and declared
    ``type: file`` markers stay refused everywhere.
    """
    from tinyassets.run_file_contract import _metadata

    if not allow_file_references:
        reject_file_references(value)
        return
    if is_file_reference(value):
        _metadata(value)
        return
    if isinstance(value, dict):
        if ({"handle_id", "artifact_id", "file_id"} & value.keys()
                or value.get("type") in ("file", "file_bundle")):
            raise ValueError("delivery_file_reference_invalid")
        for child in value.values():
            validate_output_envelopes(child, allow_file_references=True)
    elif isinstance(value, list):
        for child in value:
            validate_output_envelopes(child, allow_file_references=True)


def receiver_file_inputs(conn, delivery, branch, inputs, run_id):
    """Rewrite sender envelopes to the receiver's OWN bound references (Phase D).

    Ownership resolution at the acting principal IS the discriminator: at
    execution time the acting owner is the receiver, so a sender reference that
    survived into ``inputs_json`` cannot resolve and refuses. Nothing is bound
    here -- acceptance already bound these ids inside its own transaction -- and
    no sender file id, path or storage key is ever placed in the result.
    """
    from tinyassets.run_file_binding import validate_bound_files
    from tinyassets.run_file_crossowner import receiver_reference

    rows = conn.execute(
        "SELECT * FROM graph_delivery_files WHERE delivery_id=? ORDER BY field_name, ordinal",
        (delivery["delivery_id"],),
    ).fetchall()
    if not rows:
        reject_file_references(inputs)
        return inputs
    kinds = {field["name"]: field.get("type") for field in branch.state_schema
             if isinstance(field, dict) and type(field.get("name")) is str}
    fields = {}
    for row in rows:
        reference = receiver_reference(
            conn, run_id=run_id, owner_id=delivery["receiver_owner_id"],
            universe_id=delivery["receiver_universe_id"], file_id=row["receiver_file_id"],
        )
        if (reference["sha256"], reference["size_bytes"]) != (row["sha256"], row["size_bytes"]):
            raise ValueError("receiver_file_provenance_mismatch")
        fields.setdefault(row["field_name"], []).append(reference)
    # Every non-provenance position keeps the fail-closed default.
    reject_file_references({name: value for name, value in inputs.items() if name not in fields})
    result = dict(inputs)
    for field, references in fields.items():
        if kinds.get(field) == "list":
            result[field] = references
        elif len(references) == 1:
            result[field] = references[0]
        else:
            raise ValueError("receiver_file_cardinality_mismatch")
    validate_bound_files(
        conn, run_id=run_id, owner_id=delivery["receiver_owner_id"],
        universe_id=delivery["receiver_universe_id"], branch=branch, inputs=result,
    )
    return result


def _execution_subject(base, delivery, conn=None, run_id=None):
    """Revalidate the persisted receiver grant; never use sender authority."""
    _owned_branch(
        base, delivery["receiver_universe_id"], delivery["receiver_branch_id"],
        delivery["receiver_owner_id"],
    )
    snapshot = delivery["snapshot_json"]
    if hashlib.sha256(snapshot.encode("utf-8")).hexdigest() != delivery["snapshot_sha256"]:
        raise ValueError("receiver_snapshot_integrity_failed")
    branch = BranchDefinition.from_dict(json.loads(snapshot))
    inputs = json.loads(delivery["inputs_json"])
    if conn is None or run_id is None:
        # No receiver custody transaction to resolve against: fail closed.
        reject_file_references(inputs)
    else:
        inputs = receiver_file_inputs(conn, delivery, branch, inputs, run_id)
    if branch.validate():
        raise ValueError("receiver_snapshot_invalid")
    runs.preflight_required_inputs(branch, inputs)
    # Same owner-run capabilities as the existing served run bridge, derived
    # from the receiver-authored exposure and current admin/graph checks above.
    # Effects still enforce their own destination grants and consent.
    identity = Identity(
        user_id=delivery["receiver_owner_id"], username=delivery["receiver_owner_id"],
        capabilities=["read", "list", "write", "submit_request", "costly"],
    )
    return branch, inputs, identity


def _receiver_provider(base, delivery, branch, run_id):
    from tinyassets.api.runs import _bind_run_provider_call
    from tinyassets.foreground_run_provider import prepare_foreground_run_provider
    from tinyassets.providers.model_call import text_sync

    provider = _bind_run_provider_call(
        text_sync, delivery["receiver_universe_id"],
        principal_id=delivery["receiver_owner_id"],
    )
    return prepare_foreground_run_provider(
        provider, run_id=run_id, branch=branch, branch_version_id=None,
        allowed_statuses={runs.RUN_STATUS_QUEUED},
    )


def _work(base, delivery_id, attempt):
    """Lock lifetime includes provider execution and terminal settlement."""
    with try_attempt_lock(base, delivery_id=delivery_id, attempt=attempt) as guard:
        if guard is None:
            return
        state = deliveries.reconcile_attempt(base, guard)
        if state["state"] != "pending":
            return
        run_id = state["run_id"]
        claim = None
        try:
            # Preserve the canonical lock order: author store, then runs store.
            # No provider call or user code executes while either is held.
            with author_connection(base) as authority:
                authority.execute("BEGIN IMMEDIATE")
                with deliveries.transaction(base) as conn:
                    delivery = dict(conn.execute(
                        "SELECT * FROM graph_deliveries WHERE delivery_id=?", (delivery_id,),
                    ).fetchone())
                    # Phase D: receiver-owned references are resolved here, inside
                    # the same transaction that claims the attempt. No binding.
                    branch, inputs, identity = _execution_subject(
                        base, delivery, conn=conn, run_id=run_id,
                    )
                    claim = deliveries.start_attempt_in_transaction(conn, guard)
            if not claim:
                return
            # A crash after the durable start marker is ambiguous and NEVER
            # automatically replayed, even if it happened before first execution.
            with identity_context(identity):
                actor = f"universe:{delivery['receiver_universe_id']}"
                # This seam EXECUTES the reserved run, so it admits it: capture
                # the envelope from the exact receiver snapshot and execution
                # choices dispatched below, plus the run row's own identity.
                # Without it the run would execute and then refuse to resume.
                # Capture is guarded IS NULL — a re-prepared attempt re-supplies
                # the identical envelope; it never replays anything.
                run_name, version_id = runs._reserved_run_admission_identity(base, run_id)
                envelope = encode_admission_envelope(
                    branch,
                    recursion_limit=runs.DEFAULT_RECURSION_LIMIT,
                    concurrency_budget_override=None,
                    run_name=run_name,
                    branch_version_id=version_id,
                )
                runs._initialize_prepared_run(
                    base, run_id=run_id, branch=branch, actor=actor,
                    admission_envelope=envelope,
                )
                provider = _receiver_provider(base, delivery, branch, run_id)
                runs._invoke_prepared_branch(
                    base, run_id=run_id, branch=branch, inputs=inputs, actor=actor,
                    provider_call=provider, recursion_limit=runs.DEFAULT_RECURSION_LIMIT,
                    enqueue_universe_id=delivery["receiver_universe_id"],
                )
        except Exception:
            logger.exception("Receiver delivery attempt failed: %s/%s", delivery_id, attempt)
            runs.update_run_status(
                base, run_id, status=runs.RUN_STATUS_FAILED,
                error="Receiver delivery processing failed; see receiver logs.",
                finished_at=runs._now(),
            )
        finally:
            if claim:
                with deliveries.transaction(base) as conn:
                    deliveries.finish_attempt_in_transaction(conn, guard, claim_token=claim)
            else:
                deliveries.reconcile_attempt(base, guard)


def dispatch_accepted_delivery(base, *, delivery_id, attempt=1):
    """Submit one reserved attempt in a fresh context to the existing executor.

    The OS guard is acquired INSIDE that worker and never handed across threads.
    Repeated reconciliation can resubmit only after the earlier Future settles;
    durable state/lock checks still decide whether execution is permitted.
    """
    key = (str(runs.runs_db_path(base).resolve()), delivery_id, attempt)
    with _submitted_lock:
        if key in _submitted:
            return False
        _submitted.add(key)
    try:
        with deliveries.transaction(base) as conn:
            row = conn.execute(
                "SELECT run_id FROM graph_delivery_attempts WHERE delivery_id=? AND attempt=?",
                (delivery_id, attempt),
            ).fetchone()
            if row is None:
                raise ValueError("delivery_attempt_not_found")
        future = runs._get_executor(
            invocation_depth=0, pool_key=runs.run_pool_key_for_run(base, row["run_id"]),
        ).submit(contextvars.Context().run, _work, base, delivery_id, attempt)
        runs._track_future(row["run_id"], future)
        def finished(_future):
            with _submitted_lock:
                _submitted.discard(key)
        future.add_done_callback(finished)
        return True
    except BaseException:
        with _submitted_lock:
            _submitted.discard(key)
        raise


def reconcile_deliveries(base):
    """One internal startup/maintenance pass; no new occurrence or retry.

    Wiring to daemon startup is deliberately separate from this function. A
    caller must not describe this as an installed background queue until wired.
    """
    with deliveries.transaction(base) as conn:
        pending = conn.execute(
            "SELECT delivery_id, attempt FROM graph_delivery_attempts "
            "WHERE state IN ('pending','executing') ORDER BY created_at",
        ).fetchall()
    return sum(dispatch_accepted_delivery(
        base, delivery_id=row["delivery_id"], attempt=row["attempt"],
    ) for row in pending)
