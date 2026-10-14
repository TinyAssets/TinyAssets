"""A refused model call propagates; it never becomes template or empty output.

The platform has no LLM (AGENTS.md Hard Rule 15). The router refuses any call
not bound to one universe's owner authority with
``PlatformLLMCallRefusedError`` (a ``ProviderAuthorityHeldError``). The PR #3961
review found three legacy callers catching ``Exception`` around the provider
call and turning that refusal into a template critique, an empty
reflection, a skipped RAPTOR build or an empty connection proposal. Each case
below drives the REAL caller with ``text_sync`` replaced by one that
refuses, and requires the refusal to come out the other side.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tinyassets.exceptions import (
    AllProvidersExhaustedError,
    PlatformLLMCallRefusedError,
)

REFUSAL = "The platform has no LLM: refused for the test."


def _refuse(*_args, **_kwargs):
    raise PlatformLLMCallRefusedError(REFUSAL)


@pytest.fixture
def refusing_provider(monkeypatch):
    import tinyassets.providers.model_call as call_mod

    monkeypatch.setattr(call_mod, "text_sync", _refuse)
    return call_mod


# ---------------------------------------------------------------------------
# memory/reflexion.py
# ---------------------------------------------------------------------------

_REVERTED_SCENE = {
    "book_number": 1,
    "chapter_number": 2,
    "scene_number": 3,
    "draft_output": {"prose": "A draft that was reverted."},
}


def test_reflexion_critique_propagates_a_refusal(refusing_provider):
    from tinyassets.memory.reflexion import ReflexionEngine

    with pytest.raises(PlatformLLMCallRefusedError, match="platform has no LLM"):
        ReflexionEngine()._generate_critique(_REVERTED_SCENE, [{"verdict": "revert"}])


def test_reflexion_reflection_propagates_a_refusal(refusing_provider):
    from tinyassets.memory.reflexion import ReflexionEngine

    with pytest.raises(PlatformLLMCallRefusedError):
        ReflexionEngine()._generate_reflection("critique text", _REVERTED_SCENE)


def test_reflexion_reflect_does_not_return_a_template_on_refusal(refusing_provider):
    from tinyassets.memory.reflexion import ReflexionEngine

    with pytest.raises(PlatformLLMCallRefusedError):
        ReflexionEngine().reflect(_REVERTED_SCENE, judge_feedback=[{"verdict": "revert"}])


# ---------------------------------------------------------------------------
# knowledge/raptor.py
# ---------------------------------------------------------------------------


def _canon(tmp_path: Path) -> str:
    canon = tmp_path / "canon"
    canon.mkdir()
    (canon / "world.md").write_text(
        "\n\n".join(
            f"Paragraph {i} about the river kingdom and its long winter, "
            f"with enough words to count as a real canon paragraph number {i}."
            for i in range(6)
        ),
        encoding="utf-8",
    )
    return str(canon)


def _embed(text: str) -> list[float]:
    seed = sum(map(ord, text)) % 97
    return [float((seed + k) % 7) for k in range(8)]


def test_raptor_rebuild_propagates_a_refusal(tmp_path, refusing_provider):
    from tinyassets.knowledge.raptor import rebuild_raptor_from_canon

    with pytest.raises(PlatformLLMCallRefusedError):
        rebuild_raptor_from_canon(_canon(tmp_path), _embed, universe_id="u-test")


def test_raptor_never_builds_a_tree_of_empty_summaries(tmp_path, monkeypatch):
    """An exhausted provider fails the build; it never yields "" summary nodes.

    The stub honours ``fallback_response`` exactly as ``text_sync`` does, so
    a caller that still passes ``fallback_response=""`` gets an empty summary
    back -- which is the empty-output shape this test forbids.
    """
    import tinyassets.providers.model_call as call_mod
    from tinyassets.knowledge.raptor import rebuild_raptor_from_canon

    def exhausted(prompt, system="", *, role="writer", fallback_response=None, **_kw):
        if fallback_response is not None:
            return fallback_response
        raise AllProvidersExhaustedError("no provider for the test")

    monkeypatch.setattr(call_mod, "text_sync", exhausted)

    tree = rebuild_raptor_from_canon(_canon(tmp_path), _embed, universe_id="u-test")

    if tree is not None:
        summaries = [n.text for n in tree.nodes.values() if n.level > 0]
        assert all(s.strip() for s in summaries), "RAPTOR built empty summary nodes"
        pytest.fail("RAPTOR returned a tree although every summary call failed")


def test_worldbuild_rebuild_propagates_a_refusal(tmp_path, refusing_provider, monkeypatch):
    import tinyassets.runtime_singletons as runtime
    from domains.fantasy_daemon.phases.worldbuild import _rebuild_raptor

    universe = tmp_path / "u-test"
    universe.mkdir()
    _canon(universe)
    monkeypatch.setattr(runtime, "embed_fn", _embed, raising=False)

    with pytest.raises(PlatformLLMCallRefusedError):
        _rebuild_raptor({"_universe_path": str(universe)})


# ---------------------------------------------------------------------------
# api/connection_inference.py
# ---------------------------------------------------------------------------


def _bound_request(monkeypatch):
    import tinyassets.auth.middleware as middleware
    import tinyassets.provider_serving_binding as serving

    class _Capability:
        principal_id = "user:owner"

    monkeypatch.setattr(middleware, "provider_request_capability", lambda: _Capability())
    monkeypatch.setattr(
        serving,
        "resolve_serving_agent_binding",
        lambda *_a, **_k: {"agent_binding_id": "b1", "revision": 1},
    )
    monkeypatch.setattr(middleware, "mint_provider_request_carrier", lambda **_k: object())


def test_connection_inference_propagates_a_refusal(tmp_path, refusing_provider, monkeypatch):
    from tinyassets.api.connection_inference import _run_model

    _bound_request(monkeypatch)
    udir = tmp_path / "u-test"
    udir.mkdir()

    with pytest.raises(PlatformLLMCallRefusedError):
        _run_model(udir, "u-test", shape=[], hints=["api.example.com"], intent="search")


def test_connection_inference_without_an_owner_request_does_not_call_a_model(
    tmp_path, monkeypatch,
):
    """No carrier means no owner-bound call is possible: degrade to the manual
    fields WITHOUT asking (and so without a refusal to swallow)."""
    import tinyassets.auth.middleware as middleware
    import tinyassets.providers.model_call as call_mod
    from tinyassets.api.connection_inference import _run_model

    calls: list[str] = []
    monkeypatch.setattr(middleware, "provider_request_capability", lambda: None)
    monkeypatch.setattr(call_mod, "text_sync", lambda *a, **k: calls.append("x") or "")
    udir = tmp_path / "u-test"
    udir.mkdir()

    assert _run_model(udir, "u-test", shape=[], hints=[], intent="search") == ""
    assert calls == [], "a model call was attempted with no owner request to bind it to"


# ---------------------------------------------------------------------------
# domains/fantasy_daemon/phases/reflect.py (one level up from reflexion)
# ---------------------------------------------------------------------------


def test_reflect_phase_propagates_a_refusal_from_reflexion(monkeypatch):
    import tinyassets.runtime_singletons as runtime
    from domains.fantasy_daemon.phases.reflect import reflect

    class _RefusingManager:
        def run_reflexion(self, _state):
            raise PlatformLLMCallRefusedError(REFUSAL)

    # Assignment, not monkeypatch: reading the unset name opens a session.
    runtime.memory_manager = _RefusingManager()
    monkeypatch.setattr(runtime, "memory_manager", runtime.memory_manager, raising=False)

    with pytest.raises(PlatformLLMCallRefusedError):
        reflect({"_universe_path": ""})
