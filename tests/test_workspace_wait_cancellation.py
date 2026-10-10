"""Real pool/effector cancellation without a worker, network or platform service."""

import json

import pytest

from tests.test_workspace_effector import UNIVERSE, _create_packet, _packet, _setup
from tinyassets import effectors, runs, workspace_pool
from tinyassets.branches import NodeDefinition
from tinyassets.effectors import EffectChain, dispatch_node_effects
from tinyassets.effectors import workspace as workspace_effect


@pytest.mark.parametrize("operation", ["create", "checkout"])
@pytest.mark.parametrize("through_dispatch", [False, True])
def test_cancelled_wait_unwinds_without_lease_population_or_later_effects(
    tmp_path, monkeypatch, operation, through_dispatch,
):
    _, universe = _setup(tmp_path)
    runs.initialize_runs_db(universe)
    db = workspace_effect._pool_db(universe)
    holder = workspace_pool.admit(
        db, universe_id=UNIVERSE, connection_id="", repo_key="held",
        storage_class="scratch", run_id="holder", max_bytes=1,
        pool_root=workspace_effect.scratch_pool_root(universe),
        universe_root=workspace_effect.universe_workspace_root(universe),
    )
    monkeypatch.setattr(runs, "ensure_workspace_reconciled", lambda *_: None)
    monkeypatch.setattr(runs, "_workspace_sweep_once", lambda *_, **__: None)
    stopped = [False]
    sleeps = []
    original_admit = workspace_pool.admit

    def sleep_and_cancel(seconds):
        sleeps.append(seconds)
        stopped[0] = True

    def controlled_admit(*args, **kwargs):
        assert kwargs["should_cancel"] is should_cancel
        return original_admit(*args, **kwargs, sleep=sleep_and_cancel)

    def should_cancel():
        return stopped[0]

    def forbidden_population(*_, **__):
        pytest.fail("cancelled admission reached the owner cell or a lease handle")

    monkeypatch.setattr(workspace_pool, "admit", controlled_admit)
    # Nothing the daemon does after admission may run: the cell is what makes
    # the lease now, and the daemon only OPENS what it answered for.
    monkeypatch.setattr(workspace_effect, "_open_cell_lease", forbidden_population)
    later_effects = []
    monkeypatch.setitem(
        effectors._EFFECTORS, "authenticated_external_call",
        lambda **_: later_effects.append("unexpected"),
    )
    packet = _create_packet() if operation == "create" else _packet()
    chain = EffectChain(run_id="waiter", base_path=universe)
    with pytest.raises(runs.RunCancelledError):
        if through_dispatch:
            node = NodeDefinition(
                node_id="waiting", display_name="waiting", prompt_template="packet",
                output_keys=["packet"], timeout_seconds=60,
                effects=["workspace", "authenticated_external_call"],
            )
            dispatch_node_effects(
                chain, node, {"packet": json.dumps(packet)}, should_cancel=should_cancel,
            )
        else:
            workspace_effect.run_workspace_effector(
                node_id="waiting", output_keys=["packet"],
                run_state={"packet": json.dumps(packet)}, base_path=universe,
                run_id="waiter", chain=chain, timeout_seconds=60,
                should_cancel=should_cancel, execute=forbidden_population,
            )
    assert sleeps == [workspace_pool.LOCK_POLL_S]
    assert later_effects == []
    assert chain.active == 0 and not chain.inflight
    assert chain.workspace_mount_or_none("waiting") is None
    with workspace_pool._connect(db) as conn:
        assert [tuple(row) for row in conn.execute(
            "SELECT lease_id, run_id FROM workspace_leases"
        )] == [(holder.lease_id, "holder")]
        assert {row[0] for row in conn.execute(
            "SELECT run_id FROM workspace_locks"
        )} == {"holder"}
        assert conn.execute("SELECT COUNT(*) FROM workspace_generations").fetchone()[0] == 0
