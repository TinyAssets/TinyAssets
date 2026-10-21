"""The pin that the platform itself told agents to write, and the runtime ignores."""

from __future__ import annotations

from tinyassets.branches import _validate_llm_policy_shape


def test_preferred_provider_is_rejected_with_the_shape_that_works():
    """Live 2026-08-26: the founder's universe pinned
    llm_policy={"preferred_provider": "provdef_5d69..."} because connect_compute's own
    response told it to. The router reads policy["preferred"]["provider"], so the pin
    was ignored and both X posting runs died with provider_not_bound."""
    errors = _validate_llm_policy_shape(
        {"preferred_provider": "provdef_5d696592b51813f32673e3daf49c134a"},
        context="node 'deliver_post' llm_policy",
    )
    assert errors, "a silently-ignored pin must not validate"
    joined = " ".join(errors)
    assert "preferred_provider" in joined
    assert "provider_not_bound" in joined
    assert "'preferred': {'provider': 'api_key_http:provdef_...'}" in joined
    assert "omit the policy entirely" in joined


def test_the_canonical_shape_still_validates():
    assert _validate_llm_policy_shape(
        {"preferred": {"provider": "codex"}}, context="node 'n' llm_policy",
    ) == []
    # No policy at all is the normal case for a workflow node.
    assert _validate_llm_policy_shape({}, context="node 'n' llm_policy") == []


