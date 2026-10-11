"""Depth-zero direct v1 adapter; explicit owner, frozen input, fresh authority."""

import json
import uuid
from pathlib import Path

from tinyassets import runs
from tinyassets.auth.provider import Identity
from tinyassets.branches import BranchDefinition
from tinyassets.run_file_binding import bind_declared_files, validate_bound_files
from tinyassets.run_input_origin import OriginHeld, decode_origin, encode_origin
from tinyassets.run_input_runtime import PreparedRunExecution


def _source_authority(author_conn, *, owner, universe, branch_id):
    from tinyassets.storage.current_home import check_principal_not_deleted

    check_principal_not_deleted(author_conn, owner)
    if not author_conn.execute(
        "SELECT 1 FROM founder_home h JOIN universe_acl a ON a.universe_id=h.universe_id "
        "AND a.actor_id=h.founder_sub WHERE h.founder_sub=? AND h.universe_id=? "
        "AND a.permission='admin'", (owner, universe),
    ).fetchone():
        raise PermissionError("direct_origin_owner_unavailable")
    source = author_conn.execute(
        "SELECT author,visibility FROM branch_definitions WHERE branch_def_id=?", (branch_id,),
    ).fetchone()
    if source is None or (source["visibility"] != "public" and source["author"] != owner):
        raise PermissionError("direct_origin_source_unavailable")


def reserve_direct_run(
    base, *, owner_id, universe_id, branch, inputs, recursion_limit,
    concurrency_budget_override=None, branch_version_id=None, run_name="",
    invocation_depth=0, parent=None,
):
    """Reserve only: one transaction for run, exact envelope/options and bindings.

    Trusted intake supplies resolved branch/owner, never raw payload authority.
    No event, lineage, provider binding or dispatch happens before the start CAS.
    """
    from tinyassets.scoped_reset import _assert_recovery_state_is_clean, acquire_maintenance_barrier
    from tinyassets.storage import _connect as author_connect
    from tinyassets.storage import run_input_admissions as admissions

    if type(invocation_depth) is not int or invocation_depth != 0 or parent is not None:
        raise OriginHeld("direct_origin_requires_depth_zero_root")
    options = {"recursion_limit": recursion_limit,
               "concurrency_budget_override": concurrency_budget_override}
    encode_origin("direct", 1, options)
    branch = BranchDefinition.from_dict(branch.to_dict())
    if branch.validate():
        raise ValueError("direct_origin_branch_invalid")
    inputs = json.loads(admissions._encode(inputs))
    runs.preflight_required_inputs(branch, inputs)
    base = Path(base).absolute()
    with acquire_maintenance_barrier(base, exclusive=False, timeout=5):
        _assert_recovery_state_is_clean(base)
        runs.initialize_runs_db(base)
        with author_connect(base) as authority:
            authority.execute("BEGIN IMMEDIATE")
            _source_authority(authority, owner=owner_id, universe=universe_id,
                              branch_id=branch.branch_def_id)
            with runs._connect(base) as conn:
                conn.execute("BEGIN IMMEDIATE")
                admissions.ensure_schema(conn)
                run_id = uuid.uuid4().hex[:16]
                runs._insert_run_in_transaction(
                    conn, run_id=run_id, branch_def_id=branch.branch_def_id, thread_id=run_id,
                    inputs=inputs, actor=f"universe:{universe_id}", owner_user_id=owner_id,
                    queue_universe_id=universe_id, branch_version_id=branch_version_id,
                    run_name=run_name,
                )
                admissions.accept_in_transaction(
                    conn, run_id=run_id, owner_id=owner_id, universe_id=universe_id,
                    snapshot=branch.to_dict() if branch_version_id is None else None,
                    branch_version_id=branch_version_id, origin_kind="direct", origin_version=1,
                    origin_options=options,
                )
                envelope = admissions.load_in_transaction(
                    conn, run_id=run_id, owner_id=owner_id, universe_id=universe_id,
                )
                # Version pins, not the caller's mutable Branch object, own the
                # execution/file contract. Never bind against an alternate graph.
                snapshot = dict(envelope["snapshot"])
                snapshot.setdefault("name", snapshot.get("branch_def_id", ""))
                frozen = BranchDefinition.from_dict(snapshot)
                runs.preflight_required_inputs(frozen, inputs)
                bind_declared_files(conn, run_id=run_id, owner_id=owner_id,
                                    universe_id=universe_id, branch=frozen, inputs=inputs)
    return run_id


def _root_only(row):
    root, epoch = row["workspace_budget_root_run_id"], row["workspace_budget_epoch"]
    if root is None and epoch is None:
        return
    if root != row["run_id"] or type(epoch) is not int or epoch < 1:
        raise OriginHeld("direct_origin_requires_depth_zero_root")
    if row["workspace_budget_closing_reason"]:
        raise PermissionError("direct_origin_family_closing")


def prepare_admitted_direct(base, envelope, *, author_conn, runs_conn):
    options = decode_origin(envelope)
    if envelope["origin_kind"] != "direct":
        raise OriginHeld("direct_origin_mismatch")
    owner, universe = envelope["owner_id"], envelope["universe_id"]
    row = runs_conn.execute("SELECT * FROM runs WHERE run_id=?", (envelope["run_id"],)).fetchone()
    if (row is None or row["owner_user_id"] != owner or row["queue_universe_id"] != universe
            or row["actor"] != f"universe:{universe}"):
        raise OriginHeld("direct_origin_identity_mismatch")
    _root_only(row)
    _source_authority(author_conn, owner=owner, universe=universe,
                      branch_id=envelope["branch_def_id"])
    snapshot = dict(envelope["snapshot"])
    snapshot.setdefault("name", snapshot.get("branch_def_id", ""))
    branch = BranchDefinition.from_dict(snapshot)
    validate_bound_files(runs_conn, run_id=envelope["run_id"], owner_id=owner,
                         universe_id=universe, branch=branch, inputs=envelope["inputs"])

    def bind(base, admitted, branch):
        from tinyassets.config import load_universe_config
        from tinyassets.foreground_run_provider import new_foreground_run_provider_session
        from tinyassets.providers.base import UniverseContext
        from tinyassets.providers.model_call import bind_model_call, text_sync

        session = new_foreground_run_provider_session(
            base, universe_id=universe, principal_id=owner, provider_call=text_sync,
        )
        session.prepare(run_id=admitted["run_id"], branch=branch,
                        branch_version_id=admitted["branch_version_id"],
                        allowed_statuses={"queued"})
        return bind_model_call(
            session, UniverseContext(universe_dir=Path(base) / universe,
                                     config=load_universe_config(Path(base) / universe)),
            operation="run_graph",
        )

    return PreparedRunExecution(
        Identity(owner, owner), row["actor"], bind,
        recursion_limit=options["recursion_limit"],
        concurrency_budget_override=options["concurrency_budget_override"],
    )
