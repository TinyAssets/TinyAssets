"""A workflow agent node too large for its model moves to an accepted one that fits.

#4078 gave served chat this; a workflow agent node still failed on our own
pre-send measurement ("selected model cannot fit this workflow context") while
the owner's order held a model with a larger window. The measured need is
handed to the work adapter for this turn only, and the next model launches
through the adapter's own fresh authorization. Real foreground compiler,
admission, router and journal (tests/test_workflow_http_agent.py rig); only the
remote transports are synthetic.
"""

import pytest

from tests import test_workflow_http_agent as work_tests
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_run_provider_session import _branch, _run_branch
from tinyassets.provider_assignment_manifest import ModelAccess

http_wire = work_tests.http_wire
work_agent = work_tests.work_agent

pytestmark = pytest.mark.usefixtures("cloud_runtime")

SMALL = "synthetic-model"
ALSO_SMALL = "lab/also-small"
LARGE = "future-company/new-choice"


def _three_models(monkeypatch, windows):
    """The owner's order SMALL, ALSO_SMALL, LARGE, with the given windows.

    Three, not two: with two, excluding the failed model alone already reaches
    the large one, so the measured minimum context would go untested.
    """
    from tinyassets.config import load_universe_config
    from tinyassets.foreground_run_provider import _ForegroundRunProviderSession
    from tinyassets.providers import discovery_snapshot
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.model_preferences import ModelPreferences

    original = discovery_snapshot.read_http_discovery_document

    def catalogue(**kwargs):
        document = original(**kwargs)
        if all(m["id"] != ALSO_SMALL for m in document["data"]):
            extra = dict(document["data"][0])
            extra.update(id=ALSO_SMALL, canonical_slug=ALSO_SMALL)
            document["data"].append(extra)
        for model in document["data"]:
            model["context_length"] = windows[model["id"]]
        return document

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", catalogue)
    init = _ForegroundRunProviderSession.__init__

    def with_order(session, base_path, **kwargs):
        provider = load_universe_config(base_path / kwargs["universe_id"]).preferred_writer
        order = ModelPreferences("explicit", ModelRef(provider, SMALL), (
            ModelRef(provider, ALSO_SMALL), ModelRef(provider, LARGE),
        ))
        kwargs.pop("model_preference_data", None)
        init(session, base_path, **kwargs, model_preference_data={
            "version": 1, "saved": None, "observed_generation": 0,
            "current": order.document(),
        })

    monkeypatch.setattr(_ForegroundRunProviderSession, "__init__", with_order)


def _run(tmp_path, monkeypatch, authenticate_request):
    branch = _branch(node_count=1)
    branch.node_defs[0].tools_allowed = ["universe_self"]
    result, _, _ = _run_branch(tmp_path, monkeypatch, authenticate_request, branch,
                               open_provider=True, model_access=ModelAccess("discovered"))
    return result


@pytest.mark.parametrize("max_tokens", [1024, None], ids=["output-capped", "output-open"])
def test_a_node_too_large_for_its_model_answers_on_one_that_fits(
    tmp_path, monkeypatch, authenticate_request, work_agent, max_tokens,
):
    """Both router measurements: with an output cap the whole request is
    measured; without one the output room left is. Either way the call that
    overflowed launched nothing, so its reservation must not hold the run's
    aggregate budget the next model needs."""
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.router import ProviderRouter

    monkeypatch.setattr("tinyassets.shared_self.prepare_shared_self_turn", lambda *args: (
        args[3], "work system", ModelConfig(
            engine_mcp_enabled=True, engine_mcp_actor_id="wrong-owner",
            engine_mcp_graph_id="wrong-universe", max_tokens=max_tokens, absolute_cap_s=120,
        ),
    ))

    # Token windows: the fit check estimates tokens at 3 request bytes each.
    _three_models(monkeypatch, {SMALL: 200, ALSO_SMALL: 230, LARGE: 1_048_576})
    routed = []
    real_call = ProviderRouter.call

    async def call(router, *args, **kwargs):
        routed.append(kwargs["universe_context"].model_selection)
        return await real_call(router, *args, **kwargs)

    monkeypatch.setattr(ProviderRouter, "call", call)

    result = _run(tmp_path, monkeypatch, authenticate_request)

    assert result["terminal_status"] == "completed", (result, work_agent.errors)
    # Nothing was sent to a model too small; the measured need ruled out the
    # second small one in the same step, so it was never even routed.
    assert {wire["body"]["model"] for wire in work_agent.wires} == {LARGE}
    assert ALSO_SMALL not in {ref.model_id for ref in routed if ref is not None}
    assert work_agent.latest().state == "completed"


def test_a_node_too_large_for_every_accepted_model_still_fails_on_context(
    tmp_path, monkeypatch, authenticate_request, work_agent,
):
    _three_models(monkeypatch, {SMALL: 600, ALSO_SMALL: 600, LARGE: 600})

    result = _run(tmp_path, monkeypatch, authenticate_request)

    assert result["terminal_status"] == "failed"
    assert work_agent.wires == []
    assert "cannot fit" in (result["terminal_error"] or "")


def test_a_later_smaller_node_still_uses_the_model_an_earlier_node_outgrew(
    tmp_path, monkeypatch, authenticate_request, work_agent,
):
    """The fit is the NODE's, not the run's (gpt-6-astra on #4093): a run's
    exhaustion is shared by its nodes, so recording the outgrown model there
    made a short second node skip a primary that fits it."""
    from tinyassets.providers.router import ProviderRouter

    _three_models(monkeypatch, {SMALL: 12_000, ALSO_SMALL: 12_500, LARGE: 1_048_576})
    routed = []
    real_call = ProviderRouter.call

    async def call(router, *args, **kwargs):
        routed.append(kwargs["universe_context"].model_selection)
        return await real_call(router, *args, **kwargs)

    monkeypatch.setattr(ProviderRouter, "call", call)
    branch = _branch(node_count=2)
    for node in branch.node_defs:
        node.tools_allowed = ["universe_self"]
    branch.node_defs[0].prompt_template = "Summarize: " + "long context " * 5_000
    result, _, _ = _run_branch(tmp_path, monkeypatch, authenticate_request, branch,
                               open_provider=True, model_access=ModelAccess("discovered"))

    assert result["terminal_status"] == "completed", (result, work_agent.errors)
    sent = [wire["body"]["model"] for wire in work_agent.wires]
    # Node 1 outgrew both small models and answered on the large one; node 2,
    # short, answered on the owner's primary again.
    assert sent[0] == LARGE, sent
    assert sent[-1] == SMALL, sent
