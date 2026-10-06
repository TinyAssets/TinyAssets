"""One owner's upstream cooldown must never drain another owner's router."""

from unittest.mock import patch

import pytest

from tests.test_providers import FakeProvider
from tests.test_providers import TestProviderRouterCall as _RouterHarness
from tinyassets.exceptions import AllProvidersExhaustedError, ProviderUnavailableError
from tinyassets.providers.base import ModelConfig
from tinyassets.providers.quota import QuotaTracker
from tinyassets.providers.router import ProviderRouter


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["claude-code", "codex"])
async def test_owner_a_failure_does_not_block_owner_b(provider_name):
    helper = _RouterHarness()
    provider = FakeProvider(provider_name, "test", fail_with=ProviderUnavailableError("limited"))
    router = ProviderRouter({provider_name: provider})
    a = helper._carrier(provider=provider_name, max_tokens=10)
    b = helper._carrier(provider=provider_name, max_tokens=10)
    a._receipt.principal_id, b._receipt.principal_id = "owner-a", "owner-b"

    with pytest.raises(AllProvidersExhaustedError):
        await helper._bound_call(router, a)
    assert provider.call_count == 1
    assert not router._quota.available(provider_name, owner="owner-a")
    assert router._quota.available(provider_name, owner="owner-b")

    provider._fail_with = None
    await helper._bound_call(router, b)
    assert provider.call_count == 2
    with pytest.raises(AllProvidersExhaustedError) as held:
        await helper._bound_call(router, a)
    assert provider.call_count == 2
    assert held.value.attempts[0].skip_class == "quota_or_cooldown"
    assert router.cooldown_reason(provider_name, owner="owner-a") == "provider_unavailable"
    assert router.cooldown_reason(provider_name, owner="owner-b") == ""


def test_daily_details_expiry_and_deferred_cooling_are_owner_scoped():
    quota = QuotaTracker()
    router = ProviderRouter(quota=quota)
    with patch("tinyassets.providers.quota.time.monotonic", return_value=100):
        router._cool(ModelConfig(), "claude-code", 100, owner="a", daily_detail="A daily cap")
        router.cool_source("claude-code", owner="b", retry_after_s=9, reason="overloaded")
        assert quota.daily_detail("claude-code", owner="a") == "A daily cap"
        assert quota.daily_detail("claude-code", owner="b") == ""
        assert quota.cooldown_remaining_dict(["claude-code"], owner="a") == {"claude-code": 100}
        assert quota.all_api_providers_in_cooldown(["claude-code"], owner="b")
        assert not quota.all_api_providers_in_cooldown(["claude-code"], owner="c")
    with patch("tinyassets.providers.quota.time.monotonic", return_value=111):
        assert quota.available("claude-code", owner="b")
        assert not quota.available("claude-code", owner="a")
        assert quota.daily_detail("claude-code", owner="a") == "A daily cap"


def test_deferred_cooling_requires_an_owner():
    router = ProviderRouter()
    with pytest.raises(ValueError, match="owner"):
        router.cool_source("claude-code", owner="")
