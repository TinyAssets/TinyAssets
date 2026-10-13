"""Finite work allowance, without adding author-repair knobs or tool permission."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_run_provider_session import _branch, _run_branch
from tests.test_work_model_selection import http_wire  # noqa: F401 - pytest fixture
from tinyassets.foreground_run_provider import _work_invocation_allowance
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.provider_work_authority import ProviderInvocationReservationState
from tinyassets.request_budget import TurnRequestBudget
from tinyassets.storage.provider_work_authority import _reservation_charge, db_path

pytestmark = pytest.mark.usefixtures("cloud_runtime")


def snapshot(*, agent=False):
    return {"node_defs": [{
        "node_id": "step", "prompt_template": "Do the work", "model_hint": "writer",
        "tools_allowed": ["universe_self"] if agent else [],
    }]}


@pytest.mark.parametrize("minimum,ceiling", [(1, 1), (1, 8), (3, 8), (12, 12)])
@pytest.mark.parametrize("agent", [False, True])
def test_existing_ceiling_is_shared_only_by_opted_in_work(minimum, ceiling, agent):
    assert _work_invocation_allowance(
        snapshot(agent=agent), minimum=minimum, ceiling=ceiling,
    ) == (ceiling if agent else minimum)


@pytest.mark.parametrize("minimum,ceiling", [(0, 10), (2, 1), (1, 0), (True, 3), (1, True)])
@pytest.mark.parametrize("agent", [False, True])
def test_invalid_or_infeasible_budget_cannot_enable_rounds(minimum, ceiling, agent):
    with pytest.raises(PermissionError, match="allowance"):
        _work_invocation_allowance(snapshot(agent=agent), minimum=minimum, ceiling=ceiling)


def test_other_tools_and_mutable_inputs_cannot_request_agent_allowance():
    branch = snapshot()
    branch["inputs"] = {"universe_self": True, "max_agent_rounds": 100}
    branch["node_defs"][0]["tools_allowed"] = ["read_page"]
    assert _work_invocation_allowance(branch, minimum=1, ceiling=20) == 1


def test_invalid_agent_subject_does_not_gain_allowance():
    branch = snapshot(agent=True)
    branch["node_defs"][0]["source_code"] = "def run(state): return {}"
    with pytest.raises(ValueError, match="requires_prompt_node"):
        _work_invocation_allowance(branch, minimum=2, ceiling=20)


def test_a_plain_prompt_node_beside_an_agent_node_shares_the_allowance():
    branch = snapshot(agent=True)
    branch["node_defs"].append(dict(snapshot()["node_defs"][0], node_id="plain"))
    assert _work_invocation_allowance(branch, minimum=2, ceiling=20) == 20


@pytest.mark.parametrize("agent", [False, True])
@pytest.mark.parametrize("open_provider", [False, True])
@pytest.mark.usefixtures("http_wire")
def test_actual_foreground_receipt_uses_parent_allocation_ceiling(
    tmp_path, monkeypatch, authenticate_request, agent, open_provider,
):
    from tinyassets.daemon_server import grant_universe_access

    # Exercise admission only for agent work. No engine route is installed in
    # this fixture; full tool-loop proof lives in test_workflow_http_agent.py.
    branch = _branch(node_count=1)
    if agent:
        branch.node_defs[0].tools_allowed = ["universe_self"]
        grant_universe_access(
            tmp_path, universe_id="universe_alice", actor_id="acct_alice",
            permission="admin", granted_by="acct_alice",
        )
    _run_branch(
        tmp_path, monkeypatch, authenticate_request, branch, open_provider=open_provider,
        model_access=ModelAccess("discovered") if open_provider else None,
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        receipts = [json.loads(row[0]) for row in conn.execute(
            "SELECT record_json FROM provider_work_receipts",
        )]
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["authority_scope"] == "manifest"
    assert receipt["binding_id"] is None
    assert receipt["provider"] is None
    assert receipt["principal_id"] == "acct_alice"
    if open_provider:
        assert len(receipt["admitted_sources"]) == 1
        assert receipt["admitted_sources"][0].startswith("api_key_http:")
    else:
        assert receipt["admitted_sources"] == ["codex"]
    # Open-provider fixture supplies a policy. The node pins a provider and no model,
    # so the run's captured automatic order is that source's whole eligible
    # catalogue (two models in this fixture), fitted under the parent allocation
    # ceiling. It used to be one slot's worth, because a run with no saved
    # preference built no order and could only ever attempt the source's
    # declared default (tests/test_free_account_run_provider_parity.py).
    retry_slots = 1
    allocation = TurnRequestBudget("acct_alice", "universe_alice")
    ceiling = allocation.max_requests or 1000
    assert receipt["max_invocations"] == (
        ceiling if agent
        else 2 * retry_slots if open_provider
        else 1
    )
    assert receipt["max_tokens"] == allocation.max_tokens
    assert receipt["max_cost_microunits"] == allocation.max_cost_microunits


@pytest.mark.parametrize("state", list(ProviderInvocationReservationState))
@pytest.mark.parametrize("tokens,cost", [(None, None), (7, None), (None, 3), (0, 0), (7, 3)])
def test_shared_reservation_charge_matches_original_admission_expression(state, tokens, cost):
    item = SimpleNamespace(state=state, actual_total_tokens=tokens, actual_cost_microunits=cost,
                           max_tokens=1000, max_cost_microunits=500)
    # Frozen pre-extraction admission expression is the executable specification.
    expected = (
        (0, 0, 0)
        if item.state is ProviderInvocationReservationState.CANCELLED_BEFORE_LAUNCH
        else (1, int(item.actual_total_tokens), int(item.actual_cost_microunits))
        if item.state in {ProviderInvocationReservationState.SUCCEEDED,
                          ProviderInvocationReservationState.FAILED}
        and item.actual_total_tokens is not None and item.actual_cost_microunits is not None
        else (1, item.max_tokens, item.max_cost_microunits)
    )
    assert _reservation_charge(item) == expected
