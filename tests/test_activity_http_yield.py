"""Activities yield through the real HTTP compiler/router/journal lifecycle."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tests import test_workflow_http_agent as workflow_tests
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets import activity_runner, agent_activities
from tinyassets.providers.base import ModelConfig, ProviderResponse, UniverseContext
from tinyassets.providers.model_policy import ModelRef
from tinyassets.workflow_agent import WorkAgentAdapter

pytestmark = pytest.mark.usefixtures("cloud_runtime")
@pytest.fixture(autouse=True)
def run_cancel_ledger(tmp_path):
    from tinyassets.runs import initialize_runs_db

    initialize_runs_db(tmp_path)


http_wire = workflow_tests.http_wire
work_agent = workflow_tests.work_agent










class _LaunchRecorder:
    """Stands in for the router at the ONE place a launch happens.

    Records each launch and the execution kind it ran on.
    """

    def __init__(self):
        self.launches = []

    async def call(self, role, prompt, system, config, **kwargs):
        self.launches.append((role, kwargs.get("_agent_execution_kind")))
        return ProviderResponse("launched", "owned-http", "some-model", "test", 0)


#: The native candidate the owner's order offers mid-turn.
_HTTP_NEXT = ModelRef("native-cli", "native-model")


def _live_activity_adapter(tmp_path, *, name, with_binding=True):
    """A real adapter over a real live binding; only the session/launch are doubles.

    The doubles are deliberately PERMISSIVE: identity, authority, candidate
    staging and re-authorization all succeed, so a round reaches
    ``_LaunchRecorder`` unless the adapter itself refuses it.
    """
    from tinyassets.runs import initialize_runs_db

    initialize_runs_db(tmp_path)
    universe = tmp_path / name
    universe.mkdir(exist_ok=True)
    record = agent_activities.create(
        universe, owner_principal="acct_alice", title="t", brief="b", origin_kind="ask",
    )
    aid = record["activity_id"]
    generation = agent_activities.claim(universe, aid, replaceable=lambda _: False)
    assert agent_activities.bind_run(universe, aid, generation, "run-1")
    receipt = SimpleNamespace(
        principal_id="acct_alice", universe_id=universe.name, receipt_id="receipt-1",
    )
    claim = object()

    def _carrier(provider):
        return SimpleNamespace(
            _receipt=receipt, _claim=claim, provider=provider, operation="run_graph",
            selected_model=SimpleNamespace(model_id="some-model"),
        )

    authorized = []

    @contextmanager
    def authorize_attempt(*, role, prompt, system, policy):
        authorized.append(policy["preferred"])
        yield (_carrier(policy["preferred"]["provider"]),
               policy["preferred"]["provider"])

    session = SimpleNamespace(
        _universe_dir=universe,
        _work_candidates=SimpleNamespace(next_candidate=lambda *a, **k: _HTTP_NEXT),
        _authorize_attempt=authorize_attempt,
        _check_agent_authority=lambda _carrier: receipt.principal_id,
    )
    binding = activity_runner.ActivityRunBinding(universe, aid, generation, "run-1")
    adapter = WorkAgentAdapter(
        session, (_carrier("owned-http"), "owned-http"), {},
        **({"activity_binding": binding} if with_binding else {}),
    )
    config = ModelConfig(
        engine_mcp_enabled=True, engine_mcp_actor_id=receipt.principal_id,
        engine_mcp_graph_id=receipt.universe_id,
    )
    context = UniverseContext(universe_dir=universe, model_selection=adapter.selection)
    return adapter, config, context, authorized


