"""Founder regressions: exact step choices and connected model availability."""

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_llm_policy_override import _compile_with_capture, _make_branch, _make_node
from tests.test_work_candidate_data import data
from tinyassets.graph_compiler import CompilerError
from tinyassets.providers.model_pins import ModelPinError
from tinyassets.providers.model_policy import Exhaustion, ModelRef


def test_step_model_cannot_fall_through_a_policy_incapable_bridge():
    node = _make_node({"preferred": {"provider": "codex", "model": "gpt-6-astra"}})
    app, events, prompts = _compile_with_capture(_make_branch(node))
    with pytest.raises(CompilerError, match="gpt-6-astra.*codex.*policy"):
        app.invoke({"topic": "review"})
    assert prompts == []
    assert not any(event.get("phase") == "ran" for event in events)


def test_step_model_without_a_bound_provider_cannot_return_mock_success():
    from tinyassets.graph_compiler import compile_branch

    node = _make_node({"preferred": {"provider": "codex", "model": "gpt-6-astra"}})
    app = compile_branch(_make_branch(node)).graph.compile()
    with pytest.raises(CompilerError, match="gpt-6-astra.*codex.*no bound provider"):
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


@pytest.mark.usefixtures("cloud_runtime")
def test_workflow_dispatches_each_step_to_its_owners_chosen_family(
    tmp_path, monkeypatch, authenticate_request,
):
    from tests.test_run_provider_session import _branch, _run_branch
    from tinyassets.provider_assignment_manifest import ModelAccess

    monkeypatch.setenv("TINYASSETS_ALLOW_CLAUDE_SERVING", "1")
    branch = _branch(node_count=2)
    branch.node_defs[0].llm_policy = {"preferred": {"provider": "claude-code", "model": "opus"}}
    branch.node_defs[1].llm_policy = {"preferred": {"provider": "codex", "model": "gpt-6-astra"}}
    result, _, captured = _run_branch(
        tmp_path, monkeypatch, authenticate_request, branch, services=("codex", "claude"),
        model_access={"codex": ModelAccess("explicit", ("", "gpt-6-astra")),
                      "claude-code": ModelAccess("explicit", ("", "opus"))},
    )
    assert result["terminal_status"] == "completed", result["terminal_error"]
    assert [c.native_model_id for c in captured["providers"]["claude-code"].calls] == ["opus"]
    assert [c.native_model_id for c in captured["providers"]["codex"].calls] == ["gpt-6-astra"]


@pytest.mark.usefixtures("cloud_runtime")
def test_workflow_cannot_silently_review_on_claude_when_its_codex_step_is_rate_limited(
    tmp_path, monkeypatch, authenticate_request,
):
    from tests.test_run_provider_session import _branch, _CountingProvider, _run_branch
    from tinyassets.exceptions import ProviderRateLimitedError
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.providers.agent_capacity_boundary import NativeCompletionEvidence

    monkeypatch.setenv("TINYASSETS_ALLOW_CLAUDE_SERVING", "1")
    monkeypatch.setattr(_CountingProvider, "agent_execution_kind", "native_agent", raising=False)
    branch = _branch(node_count=2)
    branch.node_defs[0].llm_policy = {"preferred": {"provider": "claude-code", "model": "opus"}}
    branch.node_defs[1].llm_policy = {"preferred": {"provider": "codex", "model": "gpt-6-astra"}}

    def quota(provider, count):
        if provider == "codex":
            error = ProviderRateLimitedError("rate limit reached")
            error.native_evidence = NativeCompletionEvidence("codex", True, True, "none")
            raise error

    result, _, captured = _run_branch(
        tmp_path, monkeypatch, authenticate_request, branch, services=("codex", "claude"),
        model_access={"codex": ModelAccess("explicit", ("", "gpt-6-astra")),
                      "claude-code": ModelAccess("explicit", ("", "opus"))},
        after_provider_call=quota,
    )
    assert result["terminal_status"] == "failed", result
    assert "gpt-6-astra" in result["terminal_error"]
    assert [c.native_model_id for c in captured["providers"]["claude-code"].calls] == ["opus"]
    assert [c.native_model_id for c in captured["providers"]["codex"].calls] == ["gpt-6-astra"]
