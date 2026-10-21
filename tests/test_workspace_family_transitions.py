"""Run lifecycle writes serialize against family admission across processes."""


from tests.test_graph_run_controls import controls  # noqa: F401
from tests.workspace_family_test_support import (
    managed_runtime,  # noqa: F401
    simulated_managed_runtime,
)
from tinyassets import runs
from tinyassets import workspace_family as family


def _create(base, *, parent=None):
    with simulated_managed_runtime(base):
        rid = runs.create_run(
            base,
            branch_def_id="branch",
            thread_id="",
            inputs={},
            actor="actor",
            owner_user_id="owner",
            queue_universe_id="universe",
            _workspace_parent=parent,
        )
    runs.update_run_status(base, rid, status="running")
    return rid, family.execution_member(base, rid)


def _reason(base, root):
    with runs._connect(base) as conn:
        return conn.execute(
            "SELECT workspace_budget_closing_reason FROM runs WHERE run_id=?", (root,)
        ).fetchone()[0]
















def _close_in_other_process(base, root, ready, proceed):
    with family.family_fence(base, root) as fence:
        ready.set()
        assert proceed.wait(15)
        with runs._connect(base) as conn:
            conn.execute("BEGIN IMMEDIATE")
            family.close_in_transaction(conn, fence, 1, "memory_limit")






def _other_worker_start_and_read(base, root, pipe):
    interrupted = runs.recover_in_flight_runs(base)
    status = runs.get_run(base, root)["status"]
    swept = runs._workspace_sweep_once(base, claimant="independent-worker")
    pipe.send((interrupted, status, swept))


