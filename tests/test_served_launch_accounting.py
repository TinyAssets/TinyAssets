"""Launch counting excludes refusals before the provider actually starts."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace

import pytest

from tests.support.model_call import call as model_call
from tests.test_provider_served_router import _RecordingProvider, _served_context, recording_router
from tinyassets.auth import middleware as auth
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.provider_admission import ProviderBusy


@pytest.mark.parametrize("refusal", ["budget", "slot", "pre-launch"])
def test_pre_launch_refusal_preserves_request_allowance(tmp_path, monkeypatch, refusal):
    import tinyassets.provider_assignment as assignments
    import tinyassets.providers.router as routing

    _, _, capability, context = _served_context(tmp_path, monkeypatch)
    auth.seal_provider_request_launch_allowance(capability, limit=2)
    provider = _RecordingProvider("codex")
    router = recording_router(monkeypatch, provider)
    original_authorize = assignments.authorize_served_provider_call

    def refused(*args, **kwargs):
        raise ProviderAuthorityHeldError("fixture pre-launch refusal")

    @asynccontextmanager
    async def busy_slot(**kwargs):
        raise ProviderBusy("fixture slot refusal")
        yield  # pragma: no cover - async context-manager shape

    @contextmanager
    def refused_authority(*args, **kwargs):
        with original_authorize(*args, **kwargs) as authority:
            yield replace(authority, before_provider_launch=refused)

    def call():
        return asyncio.run(model_call(
            router, "writer", "hello", "system", operation="converse",
            universe_context=context,
        ))

    try:
        with monkeypatch.context() as patch:
            if refusal == "budget":
                patch.setattr(assignments, "reserve_served_provider_budget", refused)
            elif refusal == "slot":
                patch.setattr(routing, "_provider_slot", busy_slot)
            else:
                patch.setattr(assignments, "authorize_served_provider_call", refused_authority)
            with pytest.raises((ProviderAuthorityHeldError, ProviderBusy)):
                call()
        assert provider.calls == 0
        assert auth._active_provider_request(capability)["invocations"] == 0
        if refusal != "budget":
            from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

            with SQLiteProviderWorkAuthorityStore(tmp_path).connection() as conn:
                rows = conn.execute(
                    "SELECT state, actual_total_tokens, actual_cost_microunits "
                    "FROM served_provider_budget_reservations"
                ).fetchall()
            assert [tuple(row) for row in rows] == [("succeeded", 0, 0)]
        call()
        call()
        with pytest.raises(ProviderAuthorityHeldError):
            call()
        assert provider.calls == 2
    finally:
        auth.revoke_provider_request(capability)


def test_sealed_plan_can_launch_more_than_two_without_new_carriers(tmp_path, monkeypatch):
    _, _, capability, context = _served_context(tmp_path, monkeypatch)
    provider = _RecordingProvider("codex")
    router = recording_router(monkeypatch, provider)
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
