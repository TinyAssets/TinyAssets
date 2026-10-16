"""The canary door opens for one pinned account, on two proofs, or not at all.

Every fixture value is a placeholder: no real key, password or account id.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from starlette.applications import Starlette

from tinyassets import onboarding
from tinyassets.auth import middleware as mw
from tinyassets.auth.provider import Identity
from tinyassets.onboarding import canary_sign_in as door

#: The real client class, captured before any fixture replaces the name.
CLIENT = httpx.AsyncClient
KEY = "k" * 48
CANARY = "user_01CANARY"
OTHER = "user_01SOMEONEELSE"
EMAIL = "canary@example.invalid"
PASSWORD = "canary-password"
RESOURCE_TOKEN = "resource-bound-access-token"
SESSION_TOKEN = "password-grant-access-token"


class _WorkOS:
    """The three upstream calls the door makes, and nothing else."""

    def __init__(self, *, user=CANARY, password_status=200, refresh="canary-refresh",
                 resource_status=200, lookup_status=200, pinned_email=EMAIL):
        self.user, self.password_status = user, password_status
        self.refresh, self.resource_status = refresh, resource_status
        self.lookup_status, self.pinned_email = lookup_status, pinned_email
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            self.calls.append(("lookup", {"url": str(request.url)}))
            if self.lookup_status >= 400:
                return httpx.Response(self.lookup_status, json={"code": "entity_not_found"})
            return httpx.Response(200, json={"id": CANARY, "email": self.pinned_email})
        if str(request.url) == door.AUTHENTICATE_URL:
            import json

            body = json.loads(request.content.decode())
            self.calls.append(("password", body))
            if self.password_status >= 400:
                return httpx.Response(self.password_status, json={"code": "invalid_credentials"})
            payload = {"user": {"id": self.user}, "access_token": SESSION_TOKEN}
            if self.refresh:
                payload["refresh_token"] = self.refresh
            return httpx.Response(200, json=payload)
        from urllib.parse import parse_qs

        self.calls.append(("resource", parse_qs(request.content.decode())))
        if self.resource_status >= 400:
            return httpx.Response(self.resource_status, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"access_token": RESOURCE_TOKEN, "expires_in": 300})


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A configured door with fake upstreams; returns the WorkOS stub."""
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setenv(door.KEY_ENV, KEY)
    monkeypatch.setenv(door.USER_ENV, CANARY)
    monkeypatch.setenv("WORKOS_API_KEY", "sk_test_placeholder")
    monkeypatch.setattr(onboarding, "app_config", lambda: {
        "configured": True, "resource": "https://tinyassets.io/mcp", "client_id": "client_test",
        "token_endpoint": "https://id.example/oauth2/token",
    })
    # The connector's own validation: only a token AuthKit issued for the
    # resource resolves, and it resolves to the account it was issued for.
    accepted = {RESOURCE_TOKEN: CANARY}
    monkeypatch.setattr(mw, "_get_provider", lambda: SimpleNamespace(
        resolve_token=lambda token: (
            Identity(user_id=accepted[token], username="canary") if token in accepted else None
        )
    ))
    workos = _WorkOS()

    class Upstream(CLIENT):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", httpx.MockTransport(workos))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Upstream)
    workos.accepted = accepted
    return workos


def knock(body, *, headers=None, path="/app/canary-sign-in"):
    async def run():
        async with CLIENT(
            transport=httpx.ASGITransport(app=Starlette(routes=onboarding.onboarding_routes())),
            base_url="https://tinyassets.io",
        ) as client:
            return await client.post(path, json=body, headers=headers or {})

    return asyncio.run(run())


def _ask(**overrides):
    return {"key": KEY, "email": EMAIL, "password": PASSWORD, **overrides}


def test_the_pinned_canary_gets_the_session_the_connector_accepts(rig):
    response = knock(_ask())
    assert response.status_code == 200, response.text
    assert response.json() == {"access_token": RESOURCE_TOKEN, "issued_via": "resource_refresh"}
    assert response.headers["cache-control"] == "no-store"
    # No new cookie path: the protected approval session is not issued here.
    assert not response.headers.get_list("set-cookie")
    assert [name for name, _ in rig.calls] == ["lookup", "password", "resource"]
    assert rig.calls[0][1]["url"].endswith("/user_management/users/" + CANARY)
    password_call = rig.calls[1][1]
    assert password_call["grant_type"] == "password"
    assert password_call["client_id"] == "client_test"
    resource_call = rig.calls[2][1]
    assert resource_call["resource"] == ["https://tinyassets.io/mcp"]
    assert resource_call["grant_type"] == ["refresh_token"]


@pytest.mark.parametrize("missing", [door.KEY_ENV, door.USER_ENV, "TINYASSETS_ONBOARDING_APP"])
def test_an_unconfigured_door_is_simply_not_there(rig, monkeypatch, missing):
    monkeypatch.delenv(missing)
    response = knock(_ask())
    assert response.status_code == 404
    assert not rig.calls  # Nothing upstream is asked about an account.


@pytest.mark.parametrize("key", ["k" * 47, "k" * 49, "", "K" * 48])
def test_a_wrong_key_is_refused_without_naming_why_and_never_reaches_workos(rig, key):
    response = knock(_ask(key=key))
    assert response.status_code in (400, 403)
    assert response.json()["error"] in ("refused", "invalid_request")
    assert not rig.calls


def test_a_short_configured_key_disables_the_door(rig, monkeypatch):
    monkeypatch.setenv(door.KEY_ENV, "k" * 31)
    assert knock(_ask(key="k" * 31)).status_code == 404
    assert door.configured() is None


def test_without_the_workos_key_the_door_says_so_instead_of_guessing(rig, monkeypatch):
    monkeypatch.delenv("WORKOS_API_KEY")
    response = knock(_ask())
    assert response.status_code == 503
    assert response.json() == {"error": "password_check_unconfigured"}
    assert not rig.calls


def test_a_wrong_password_is_refused_and_no_token_is_issued(rig):
    rig.password_status = 401
    response = knock(_ask(password="not-the-password"))
    assert response.status_code == 403
    assert response.json() == {"error": "password_refused"}
    assert [name for name, _ in rig.calls] == ["lookup", "password"]


def test_another_address_never_reaches_the_password_api_at_all(rig):
    """A key holder cannot use the door to test a third party's credentials:
    the only address it ever authenticates is the pinned user's own."""
    response = knock(_ask(email="someone-else@example.invalid"))
    assert response.status_code == 403
    assert response.json() == {"error": "not_the_canary_account"}
    assert [name for name, _ in rig.calls] == ["lookup"]


def test_an_account_workos_authenticates_as_someone_else_is_refused(rig):
    # The address is the pinned user's, but WorkOS authenticates another id.
    rig.user = OTHER
    rig.accepted[RESOURCE_TOKEN] = OTHER
    response = knock(_ask())
    assert response.status_code == 403
    assert response.json() == {"error": "not_the_canary_account"}
    # Refused before any token was asked for, let alone returned.
    assert [name for name, _ in rig.calls] == ["lookup", "password"]


def test_a_mistyped_pinned_user_id_says_so_instead_of_signing_anybody_in(rig):
    rig.lookup_status = 404
    response = knock(_ask())
    assert response.status_code == 502
    assert response.json() == {"error": "pinned_account_unknown"}
    assert [name for name, _ in rig.calls] == ["lookup"]


def test_a_session_this_server_would_not_accept_is_never_handed_back(rig):
    rig.accepted.clear()
    response = knock(_ask())
    assert response.status_code == 502
    assert response.json() == {"error": "no_accepted_session"}


def test_a_session_for_another_account_is_never_handed_back(rig):
    # The upstream claims the canary, then issues somebody else's token.
    rig.accepted[RESOURCE_TOKEN] = OTHER
    assert knock(_ask()).json() == {"error": "no_accepted_session"}


def test_without_audience_binding_the_password_session_is_accepted_on_its_own(rig):
    """A deployment that runs without resource binding: the grant's own token
    IS what /mcp accepts there, and only because this server accepted it."""
    rig.resource_status = 400
    rig.accepted.clear()
    rig.accepted[SESSION_TOKEN] = CANARY
    response = knock(_ask())
    assert response.status_code == 200
    assert response.json() == {"access_token": SESSION_TOKEN, "issued_via": "password_grant"}


def test_grinding_the_door_is_rate_limited(rig):
    rig.password_status = 401
    for _ in range(door.ATTEMPTS):
        assert knock(_ask()).status_code == 403
    response = knock(_ask())
    assert response.status_code == 429
    assert response.json() == {"error": "rate_limited"}
    assert response.headers["Retry-After"] == str(int(door.WINDOW))


@pytest.mark.parametrize("body,headers", [
    ({"email": EMAIL, "password": PASSWORD}, None),
    ({"key": KEY, "password": PASSWORD}, None),
    ({"key": KEY, "email": EMAIL}, None),
    (_ask(), {"content-type": "text/plain"}),
])
def test_an_incomplete_or_unlabelled_request_is_a_400(rig, body, headers):
    response = knock(body, headers=headers)
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request"}
    assert not rig.calls


def test_nothing_secret_reaches_the_log_or_the_body(rig, caplog):
    caplog.set_level("INFO")
    assert knock(_ask()).status_code == 200
    rig.password_status = 401
    refused = knock(_ask())
    text = caplog.text + refused.text
    for secret in (KEY, PASSWORD, EMAIL, RESOURCE_TOKEN):
        assert secret not in text
    assert "canary_sign_in issued a canary session via=resource_refresh" in caplog.text
    assert "canary_sign_in refused code=password_refused" in caplog.text


def test_the_carve_out_is_this_exact_path_and_nothing_under_it(monkeypatch):
    monkeypatch.setattr(mw, "_inbound_hooks_enabled", lambda: False)
    assert mw._auth_challenge_path("/app/canary-sign-in") is False
    assert mw._auth_challenge_path("/app/canary-sign-in/x") is True
    assert mw._auth_challenge_path("/app/canary-sign-in/") is True
