from __future__ import annotations

import asyncio
import pickle
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest


@pytest.fixture
def live_request():
    from tinyassets.auth.middleware import (
        claim_provider_request,
        reserve_provider_request,
        revoke_provider_request,
    )

    reserve = reserve_provider_request(
        principal_id="owner-1", session_id="session-plan", request_id="plan",
        tool_name="converse",
    )
    capability = claim_provider_request(reserve, tool_name="converse")
    try:
        yield capability
    finally:
        revoke_provider_request(capability)


def test_sealed_allowance_cannot_be_inflated_or_reset(live_request):
    from tinyassets.auth.middleware import (
        consume_provider_request_invocation as consume,
    )
    from tinyassets.auth.middleware import (
        seal_provider_request_launch_allowance as seal,
    )

    assert seal(live_request, limit=3) == 3
    assert seal(live_request, limit=3) == 3
    assert consume(live_request, limit=100) == 1
    with pytest.raises(PermissionError, match="already sealed"):
        seal(live_request, limit=100)
    with pytest.raises(PermissionError, match="already sealed"):
        seal(live_request, limit=1)
    assert seal(live_request, limit=3) == 3  # idempotence is not replenishment
    assert consume(live_request, limit=2) == 2
    assert consume(live_request, limit=2) == 3
    with pytest.raises(PermissionError, match="exhausted"):
        consume(live_request, limit=100)


def test_legacy_allowance_is_unchanged_and_cannot_be_sealed_late(live_request):
    from tinyassets.auth.middleware import (
        consume_provider_request_invocation as consume,
    )
    from tinyassets.auth.middleware import (
        seal_provider_request_launch_allowance as seal,
    )

    assert consume(live_request, limit=2) == 1
    with pytest.raises(PermissionError, match="after a launch"):
        seal(live_request, limit=6)
    assert consume(live_request, limit=2) == 2
    with pytest.raises(PermissionError, match="exhausted"):
        consume(live_request, limit=2)


@pytest.mark.parametrize("limit", [0, -1, True, 2.5, "3", None])
def test_seal_rejects_invalid_allowances_without_changing_request(live_request, limit):
    from tinyassets.auth.middleware import seal_provider_request_launch_allowance as seal

    with pytest.raises(ValueError, match="positive"):
        seal(live_request, limit=limit)
    assert seal(live_request, limit=3) == 3


def test_seal_requires_a_live_server_issued_request(live_request):
    from tinyassets.auth.middleware import (
        revoke_provider_request,
    )
    from tinyassets.auth.middleware import (
        seal_provider_request_launch_allowance as seal,
    )

    with pytest.raises(PermissionError, match="server-issued"):
        seal(SimpleNamespace(), limit=3)
    revoke_provider_request(live_request)
    with pytest.raises(PermissionError, match="revoked"):
        seal(live_request, limit=3)


def test_concurrent_consumers_share_the_sealed_allowance(live_request):
    from tinyassets.auth.middleware import (
        consume_provider_request_invocation as consume,
    )
    from tinyassets.auth.middleware import (
        seal_provider_request_launch_allowance as seal,
    )

    seal(live_request, limit=3)

    def launch(_):
        try:
            return consume(live_request, limit=100)
        except PermissionError:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(launch, range(12)))
    assert sorted(value for value in results if value is not None) == [1, 2, 3]
    assert results.count(None) == 9


def test_concurrent_seals_choose_exactly_one_allowance(live_request):
    from tinyassets.auth.middleware import seal_provider_request_launch_allowance as seal

    def try_seal(limit):
        try:
            return seal(live_request, limit=limit)
        except PermissionError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(try_seal, [3, 5]))
    assert results in ([3, None], [None, 5])


def test_request_capability_is_one_shot_worker_bound_and_revoked():
    from tinyassets.auth import middleware as auth
    from tinyassets.auth.middleware import (
        claim_provider_request,
        provider_request_capability,
        reserve_provider_request,
        revoke_provider_request,
    )

    reserve = reserve_provider_request(
        principal_id="owner-1",
        session_id="session-1",
        request_id="request-1",
        tool_name="converse",
    )
    capability = claim_provider_request(reserve, tool_name="converse")

    assert provider_request_capability() is capability
    with pytest.raises(TypeError):
        pickle.dumps(capability)
    with pytest.raises(PermissionError, match="claimed"):
        claim_provider_request(reserve, tool_name="converse")

    revoke_provider_request(capability)
    assert provider_request_capability() is None
    assert capability._nonce not in auth._PROVIDER_REQUESTS  # noqa: SLF001


def test_request_carrier_cannot_outlive_or_change_its_request_lease():
    from tinyassets.auth.middleware import (
        claim_provider_request,
        mint_provider_request_carrier,
        reserve_provider_request,
        revoke_provider_request,
        validate_provider_request_carrier,
    )

    reserve = reserve_provider_request(
        principal_id="owner-1",
        session_id="session-1",
        request_id="request-1",
        tool_name="converse",
    )
    capability = claim_provider_request(reserve, tool_name="converse")
    carrier = mint_provider_request_carrier(
        universe_id="u-owner",
        agent_binding_id="agent_binding_1",
        binding_revision=7,
        operation="converse",
    )

    validated = validate_provider_request_carrier(
        carrier,
        universe_id="u-owner",
        agent_binding_id="agent_binding_1",
        binding_revision=7,
        operation="converse",
    )
    assert validated.principal_id == "owner-1"
    with pytest.raises(PermissionError, match="another command center"):
        validate_provider_request_carrier(
            carrier,
            universe_id="u-other",
            agent_binding_id="agent_binding_1",
            binding_revision=7,
            operation="converse",
        )

    revoke_provider_request(capability)
    with pytest.raises(PermissionError, match="revoked"):
        validate_provider_request_carrier(
            carrier,
            universe_id="u-owner",
            agent_binding_id="agent_binding_1",
            binding_revision=7,
            operation="converse",
        )


def test_fastmcp_current_message_reserve_is_claimed_in_tool_worker(monkeypatch):
    from mcp.server.lowlevel.server import RequestContext, request_ctx
    from starlette.requests import Request

    from tinyassets.auth import middleware as auth
    from tinyassets.auth.provider import Identity
    from tinyassets.universe_server import _ProviderRequestAuthority

    class _Provider:
        @staticmethod
        def resolve_token(token):
            assert token == "message-token"
            return Identity(user_id="owner-1", username="owner")

    monkeypatch.setattr(auth, "_get_provider", lambda: _Provider())
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": [(b"authorization", b"Bearer message-token")],
        }
    )
    request_token = request_ctx.set(
        RequestContext(
            request_id="request-1",
            meta=None,
            session=object(),
            lifespan_context={},
            request=request,
        )
    )
    context = SimpleNamespace(
        message=SimpleNamespace(name="converse"),
        fastmcp_context=SimpleNamespace(
            is_background_task=False,
            session_id="session-1",
        ),
    )

    async def _call_next(_context):
        reserve = auth.provider_request_reserve()
        assert reserve is not None
        capability = auth.claim_provider_request(reserve, tool_name="converse")
        try:
            assert capability.principal_id == "owner-1"
            assert capability.session_id == "session-1"
            assert capability.request_id == "request-1"
            return "dispatched"
        finally:
            auth.revoke_provider_request(capability)

    try:
        result = asyncio.run(
            _ProviderRequestAuthority().on_call_tool(context, _call_next)
        )
    finally:
        request_ctx.reset(request_token)

    assert result == "dispatched"
    assert auth.provider_request_reserve() is None


def test_outer_asgi_identity_without_current_message_mints_no_reserve():
    from tinyassets.auth import middleware as auth
    from tinyassets.auth.provider import Identity
    from tinyassets.universe_server import _ProviderRequestAuthority

    identity_token = auth._current_identity.set(  # noqa: SLF001 - negative proof
        Identity(user_id="owner-outer", username="owner")
    )
    context = SimpleNamespace(
        message=SimpleNamespace(name="converse"),
        fastmcp_context=SimpleNamespace(
            is_background_task=False,
            session_id="session-outer",
        ),
    )

    async def _call_next(_context):
        assert auth.provider_request_reserve() is None
        return "no-authority"

    try:
        result = asyncio.run(
            _ProviderRequestAuthority().on_call_tool(context, _call_next)
        )
    finally:
        auth._current_identity.reset(identity_token)  # noqa: SLF001

    assert result == "no-authority"
