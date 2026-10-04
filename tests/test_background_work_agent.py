"""The background provider session refuses a substituted caller context.

The end-to-end tests that lived here launched through the consumer's epoch-2
claim pass, which went with the fleet-era pump (dark-code deletion plan C2).
This unit test goes with ``background_served_provider`` itself.
"""

import pytest


@pytest.mark.parametrize("field", ["provider_request", "provider_invocation", "served_provider",
                                  "agent_model_plan", "model_selection", "universe_dir"])
def test_background_rejects_caller_context_substitution_before_admission(tmp_path, field):
    from dataclasses import replace
    from types import SimpleNamespace

    from tinyassets.background_served_provider import _BackgroundAssignedProviderSession
    from tinyassets.providers.base import ModelConfig, UniverseContext

    session = _BackgroundAssignedProviderSession(
        tmp_path, SimpleNamespace(universe_id="universe_alice", actor_id="acct_alice"), None, None,
    )
    value = tmp_path / "another-root" / "universe_alice" if field == "universe_dir" else object()
    context = replace(UniverseContext(universe_dir=tmp_path / "universe_alice", config=None),
                      **{field: value})
    with pytest.raises(PermissionError, match="cannot be substituted"):
        session._call("writer", "prompt", "", ModelConfig(), None, {"universe_context": context})
    assert session._call_index == 0
