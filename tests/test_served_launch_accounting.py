"""Launch counting excludes refusals before the provider actually starts."""
from __future__ import annotations

import asyncio

import pytest

from tests.support.model_call import call as model_call
from tests.test_provider_served_router import _RecordingProvider, _served_context
from tinyassets.auth import middleware as auth
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.providers.router import ProviderRouter


def test_sealed_plan_can_launch_more_than_two_without_new_carriers(tmp_path):
    _, _, capability, context = _served_context(tmp_path)
    provider = _RecordingProvider("codex")
    router = ProviderRouter({"codex": provider})
    try:
        auth.seal_provider_request_launch_allowance(capability, limit=6)
        for index in range(6):
            asyncio.run(model_call(router,
                "writer", f"attempt {index}", "system", operation="converse",
                universe_context=context,
            ))
        with pytest.raises(ProviderAuthorityHeldError):
            asyncio.run(model_call(router,
                "writer", "too many", "system", operation="converse", universe_context=context,
            ))
        assert provider.calls == 6
        assert auth._active_provider_request(capability)["invocations"] == 6
    finally:
        auth.revoke_provider_request(capability)
