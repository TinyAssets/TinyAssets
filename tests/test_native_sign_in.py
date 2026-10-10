"""Native callbacks cannot transfer credentials without the initiating app's PKCE."""

import asyncio
import base64
import hashlib
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.test_app_owner_sign_in import browser
from tests.test_app_owner_sign_in import login as login_fixture
from tests.test_model_bootstrap import rig  # noqa: F401
from tests.test_onboarding_model_connect import ingress  # noqa: F401
from tinyassets.onboarding import native_sign_in as native
from tinyassets.onboarding.owner_sessions import COOKIE, hashed

login = login_fixture

VERIFIER = "v" * 64
ORIGIN = {"origin": "https://tinyassets.io"}


async def start(client, platform):
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
    )
    result = await client.post(
        "/app/native-sign-in",
        headers=ORIGIN,
        json={"client": platform, "code_challenge": challenge},
    )
    assert result.status_code == 200
    flow = result.json()
    query = parse_qs(urlsplit(flow["url"]).query)
    assert query["code_challenge"] == [challenge]
    assert query["redirect_uri"] == ["https://tinyassets.io/app"]
    return flow["ref"], query["state"][0]


@pytest.mark.parametrize(
    "platform,scheme",
    [
        ("android", "intent://auth?"),
        ("android-debug", "intent://auth?"),
        ("ios", "tinyassets://auth?"),
        ("desktop", "tinyassets-desktop://auth?"),
    ],
)
def test_system_callback_is_opaque_and_redeemed_once(login, platform, scheme):
    calls, _ = login

    async def run():
        async with browser() as client:
            ref, state = await start(client, platform)
            body = {"native_ref": ref, "code_verifier": VERIFIER}
            assert (await client.post("/app/token", headers=ORIGIN, json=body)).status_code == 202
            response = await client.get("/app", params={"state": state, "code": "secret-code"})
            assert response.status_code == 200
            assert scheme in response.text and f"signin={ref}" in response.text
            assert "secret-code" not in response.text and VERIFIER not in response.text
            assert not response.headers.get_list("set-cookie")
            if platform.startswith("android"):
                package = "io.tinyassets.app" + (".debug" if platform.endswith("debug") else "")
                assert f"package={package};end" in response.text
            with native.flows() as conn:
                payload = conn.execute("SELECT payload FROM native_login_flows").fetchone()[0]
                assert b"secret-code" not in payload
            wrong = await client.post(
                "/app/token", headers=ORIGIN, json={**body, "code_verifier": "w" * 64}
            )
            assert wrong.status_code == 400 and not calls
            assert (
                await client.get("/app", params={"state": state, "code": "other"})
            ).status_code == 410
            response = await client.post("/app/token", headers=ORIGIN, json=body)
            assert response.status_code == 200 and response.json()["access_token"] == "test-access"
            assert response.json()["session_ref"]
            assert client.cookies.get(COOKIE) is None  # Never grants interactive approval proof.
            assert calls[0]["code"] == ["secret-code"]
            assert calls[0]["code_verifier"] == [VERIFIER]
            assert (await client.post("/app/token", headers=ORIGIN, json=body)).status_code == 400
            assert len(calls) == 1

    asyncio.run(run())


def test_pending_and_rejected_polls_preserve_existing_approval_session(login):
    from tinyassets.onboarding.owner_sessions import lookup, store

    cookie = "existing-protected-browser"
    with store() as conn:
        conn.execute("INSERT INTO owner_sessions VALUES (?,?,?)",
                     (hashed(cookie), '{"user_id":"owner"}', time.time() + 600))

    async def run():
        async with browser() as client:
            client.cookies.set(COOKIE, cookie, domain="tinyassets.io", path="/")
            ref, state = await start(client, "desktop")
            data = {"native_ref": ref, "code_verifier": VERIFIER}
            assert (await client.post("/app/token", headers=ORIGIN, json=data)).status_code == 202
            assert lookup(cookie)
            denied = await client.post("/app/token", headers=ORIGIN,
                                       json={**data, "code_verifier": "w" * 64})
            assert denied.status_code == 400 and lookup(cookie)
            await client.get("/app", params={"state": state, "code": "ready"})
            assert (await client.post("/app/token", headers=ORIGIN, json=data)).status_code == 200
            assert lookup(cookie) is None

    asyncio.run(run())


def test_native_expiry_cancel_and_cross_origin_fail_closed(login):
    calls, _ = login

    async def run():
        async with browser() as client:
            assert (await client.post("/app/native-sign-in", json={})).status_code == 403
            assert (
                await client.post(
                    "/app/native-sign-in", headers=ORIGIN, json={"client": "attacker"}
                )
            ).status_code == 400
            ref, state = await start(client, "ios")
            with native.flows() as conn:
                conn.execute(
                    "UPDATE native_login_flows SET expires=0 WHERE ref_hash=?", (hashed(ref),)
                )
            assert (
                await client.get("/app", params={"state": state, "code": "late"})
            ).status_code == 410
            assert (
                await client.post(
                    "/app/token",
                    headers=ORIGIN,
                    json={"native_ref": ref, "code_verifier": VERIFIER},
                )
            ).status_code == 400
            ref, state = await start(client, "desktop")
            await client.get("/app", params={"state": state, "error": "access_denied"})
            response = await client.post(
                "/app/token", headers=ORIGIN, json={"native_ref": ref, "code_verifier": VERIFIER}
            )
            assert response.json()["error"] == "sign_in_cancelled"
            assert not calls

    asyncio.run(run())
