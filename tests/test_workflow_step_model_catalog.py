"""Founder regressions: exact step choices and connected model availability."""

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_llm_policy_override import _make_branch, _make_node
from tests.test_work_candidate_data import data
from tinyassets.graph_compiler import CompilerError
from tinyassets.providers.model_pins import ModelPinError
from tinyassets.providers.model_policy import Exhaustion, ModelRef


def test_step_model_reaches_the_single_prompt_call():
    from tinyassets.graph_compiler import compile_branch

    node = _make_node({"preferred": {"provider": "codex", "model": "gpt-6-astra"}})
    seen = []

    def chosen(_prompt, _system, **kwargs):
        seen.append(kwargs["policy"])
        return "reviewed"

    app = compile_branch(_make_branch(node), provider_call=chosen).graph.compile()
    assert app.invoke({"topic": "review"})["out"] == "reviewed"
    assert seen == [node.llm_policy]


def test_step_model_without_a_bound_provider_cannot_return_mock_success():
    from tinyassets.graph_compiler import compile_branch

    node = _make_node({"preferred": {"provider": "codex", "model": "gpt-6-astra"}})
    app = compile_branch(_make_branch(node)).graph.compile()
    with pytest.raises(CompilerError, match="no bound model call"):
        app.invoke({"topic": "review"})


def test_pinned_step_exhaustion_never_substitutes_the_automatic_default():
    choices = data(automatic=True)
    policy = {"preferred": {"provider": "b", "model": "B"}}
    choices.fit({"node_defs": [{"prompt_template": "review", "llm_policy": policy}]},
                ceiling=10, retry_multiplier=1)
    assert choices.next_candidate(policy) == ModelRef("b", "B")
    assert choices.next_candidate(policy, (Exhaustion("model", ModelRef("b", "B")),)) is None


def test_missing_step_model_error_names_requested_model_and_source():
    choices = data(automatic=True)
    with pytest.raises(ModelPinError, match="gpt-6-astra.*codex.*eligible"):
        choices.fit({"node_defs": [{"prompt_template": "review", "llm_policy": {
            "preferred": {"provider": "codex", "model": "gpt-6-astra"},
        }}]}, ceiling=10, retry_multiplier=1)


@pytest.mark.parametrize("model_pin", [{}, {"model": "A"}, {"model_id": "A"}])
def test_provider_pin_retains_same_source_breadth_but_exact_model_does_not(model_pin):
    from tests.test_model_policy import AUTO, NEEDS, connection, model
    from tinyassets.providers.agent_model_plan import AgentModelPlan
    from tinyassets.providers.model_policy import Catalog
    from tinyassets.providers.work_candidate_data import WorkCandidateData

    catalog = Catalog("owner", "universe", (
        connection("a", models=[model("A"), model("A2")]),
        connection("b", models=[model("B")]),
    ))
    choices = WorkCandidateData(AgentModelPlan(catalog, AUTO, NEEDS))
    policy = {"preferred": {"provider": "a", **model_pin}}
    assert choices.fit(
        {"node_defs": [{"prompt_template": "review", "llm_policy": policy}]},
        ceiling=10, retry_multiplier=3,
    ) == (3 if model_pin else 6)
    assert choices.next_candidate(policy) == ModelRef("a", "A")
    next_choice = choices.next_candidate(policy, (Exhaustion("model", ModelRef("a", "A")),))
    assert next_choice == (None if model_pin else ModelRef("a", "A2"))
    assert choices.next_candidate(policy, (Exhaustion("model", ModelRef("a", "A2")),)) is None
