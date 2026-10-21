"""Trusted parent-mediated delivery: real subprocess RPC and two-owner SQLite."""
# ruff: noqa: F811 -- imported pytest fixtures

import pytest

from tests.test_delivery_public import linked  # noqa: F401
from tests.test_receiver_links import env  # noqa: F401
from tinyassets import runs
from tinyassets.api import deliveries as api
from tinyassets.auth.middleware import identity_context
from tinyassets.branches import BranchDefinition
from tinyassets.daemon_server import (
    get_branch_definition,
    save_branch_definition,
)
from tinyassets.storage import deliveries

SOURCE = """def run(state):
    ids = []
    for occurrence in ['first', 'first', 'second']:
        receipt = invoke_mcp_action('deliver_output', link_id=state['link'],
            occurrence_id=occurrence, outputs={'result': state['topic']})
        ids.append(receipt['delivery_id'])
    return {'result': state['topic'], 'receipts': ids}
"""


@pytest.fixture
def node_env(linked, monkeypatch, admitted_center):
    base, auth, receiver, link = linked
    admitted_center(base, "u-sender", "sender")
    branch = BranchDefinition.from_dict(get_branch_definition(base, branch_def_id="b-sender"))
    node = branch.node_defs[0]
    node.prompt_template = ""
    node.source_code = SOURCE
    node.input_keys = ["topic", "link"]
    node.output_keys = ["result", "receipts"]
    node.tools_allowed = ["deliver_output"]
    # Attribution/approval is not an additional permission grant.
    node.approved = False
    branch.fork_from = "original-contributor-version"
    branch.state_schema += [{"name": "link", "type": "str"}, {"name": "receipts", "type": "list"}]
    save_branch_definition(base, branch_def=branch.to_dict())
    dispatched = []
    monkeypatch.setattr(api.delivery_runtime, "dispatch_accepted_delivery",
                        lambda *args, **kwargs: dispatched.append(kwargs))
    return base, auth, receiver, link, branch, dispatched


def _prepare(base, branch, *, owner="sender", universe="u-sender"):
    return runs._prepare_run(
        base, branch=branch, inputs={}, run_name="RPC source", actor=f"universe:{universe}",
        owner_user_id=owner, queue_universe_id=universe,
    )


def _execute(base, branch, link, *, run_id=None):
    run_id = run_id or _prepare(base, branch)
    # No request identity exists on a scheduled/background execution thread.
    with identity_context(None):
        outcome = runs._invoke_graph(
            base, run_id=run_id, branch=branch,
            inputs={"topic": "exact 🍉", "link": link["link_id"]},
            provider_call=None, recursion_limit=100,
        )
    return outcome


def _rows(base):
    with deliveries.transaction(base) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM graph_deliveries")]


def _source(base, branch, run_id):
    return api.NodeDeliverySource(
        owner_user_id="sender", universe_id="u-sender", actor="universe:u-sender",
        run_id=run_id, branch_def_id=branch.branch_def_id, node_id="entry",
        output_keys=tuple(branch.node_defs[0].output_keys),
    )


def _send(base, source, link, occurrence="one", outputs=None, cancel=lambda: False):
    return api.deliver_node_output(
        base, source=source, link_id=link["link_id"], occurrence_id=occurrence,
        outputs=outputs or {"result": "exact"}, should_cancel=cancel,
    )


@pytest.mark.real_jail
def test_real_linux_jail_transports_delivery_rpc(node_env, monkeypatch):
    base, _, _, link, branch, _ = node_env
    outcome = _execute(base, branch, link)
    assert outcome.status == "completed", outcome.error
    assert len(_rows(base)) == 2

