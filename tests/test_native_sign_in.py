"""Native callbacks cannot transfer credentials without the initiating app's PKCE."""

import asyncio
import base64
import hashlib
import html
import re
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
            body["return_secret"] = return_params(response)["return_secret"]
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
        conn.execute(
            "INSERT INTO owner_sessions VALUES (?,?,?)",
            (hashed(cookie), '{"user_id":"owner"}', time.time() + 600),
        )

    async def run():
        async with browser() as client:
            client.cookies.set(COOKIE, cookie, domain="tinyassets.io", path="/")
            ref, state = await start(client, "desktop")
            data = {"native_ref": ref, "code_verifier": VERIFIER}
            assert (await client.post("/app/token", headers=ORIGIN, json=data)).status_code == 202
            assert lookup(cookie)
            denied = await client.post(
                "/app/token", headers=ORIGIN, json={**data, "code_verifier": "w" * 64}
            )
            assert denied.status_code == 400 and lookup(cookie)
            returned = await client.get("/app", params={"state": state, "code": "ready"})
            data["return_secret"] = return_params(returned)["return_secret"]
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
            returned = await client.get("/app", params={"state": state, "error": "access_denied"})
            response = await client.post(
                "/app/token",
                headers=ORIGIN,
                json={
                    "native_ref": ref,
                    "code_verifier": VERIFIER,
                    "return_secret": return_params(returned)["return_secret"],
                },
            )
            assert response.json()["error"] == "sign_in_cancelled"
            assert not calls

    asyncio.run(run())


def return_params(response):
    link = html.unescape(re.search(r'href="([^"]+)"', response.text)[1])
    return {k: v[0] for k, v in parse_qs(urlsplit(link).query).items()}


def test_forged_origin_initiator_cannot_redeem_victim_callback(login):
    calls, _ = login

    async def run():
        async with browser() as attacker, browser() as victim:
            ref, state = await start(attacker, "android")
            response = await victim.get("/app", params={"state": state, "code": "victim-code"})
            body = {"native_ref": ref, "code_verifier": VERIFIER}
            for extra in ({}, {"return_secret": "x" * 43}):
                denied = await attacker.post("/app/token", headers=ORIGIN, json={**body, **extra})
                assert denied.status_code == 400 and not calls
            secret = return_params(response)["return_secret"]
            assert secret != ref
            with native.flows() as conn:
                assert (
                    secret.encode()
                    not in conn.execute("SELECT payload FROM native_login_flows").fetchone()[0]
                )
            accepted = await victim.post(
                "/app/token", headers=ORIGIN, json={**body, "return_secret": secret}
            )
            assert accepted.status_code == 200 and len(calls) == 1

    asyncio.run(run())


def peer_browser(host):
    import httpx
    from starlette.applications import Starlette

    from tinyassets import onboarding

    app = Starlette(routes=onboarding.onboarding_routes())
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(host, 4242)),
        base_url="https://tinyassets.io",
    )


async def begin_as(client, edge_ip=None, forwarded=None):
    headers = {**ORIGIN}
    if edge_ip:
        headers["cf-connecting-ip"] = edge_ip
    if forwarded:
        headers["x-forwarded-for"] = forwarded
    return await client.post(
        "/app/native-sign-in",
        headers=headers,
        json={"client": "android", "code_challenge": "c" * 43},
    )


@pytest.mark.parametrize("tunnel_peer", ["127.0.0.1", "172.18.0.1", "::1"])
def test_native_begin_limits_each_edge_client_behind_the_tunnel(login, tunnel_peer):
    async def run():
        async with peer_browser(tunnel_peer) as tunnel:
            for _ in range(native.IP_ATTEMPTS):
                assert (await begin_as(tunnel, "203.0.113.5")).status_code == 200
            assert (await begin_as(tunnel, "203.0.113.5")).status_code == 429
            # Another public client behind the same tunnel keeps its own bucket.
            assert (await begin_as(tunnel, "198.51.100.7")).status_code == 200
            # Caller-chosen X-Forwarded-For (preserved by the Worker) is never identity.
            assert (await begin_as(tunnel, "203.0.113.5", "192.0.2.9")).status_code == 429
            with native.flows() as conn:
                assert conn.execute("SELECT COUNT(*) FROM native_login_limits").fetchone()[0] == 2
                assert native.IP_ATTEMPTS + 1 == conn.execute(
                    "SELECT COUNT(*) FROM native_login_flows"
                ).fetchone()[0]
                conn.execute("UPDATE native_login_limits SET expires=0")
            assert (await begin_as(tunnel, "203.0.113.5")).status_code == 200

    asyncio.run(run())


def test_native_begin_direct_peer_cannot_spoof_edge_headers(login):
    async def run():
        async with peer_browser("192.0.2.44") as direct:
            for index in range(native.IP_ATTEMPTS):
                response = await begin_as(direct, f"203.0.113.{index}", f"198.51.100.{index}")
                assert response.status_code == 200
            response = await begin_as(direct, "203.0.113.99", "198.51.100.99")
            assert response.status_code == 429
            assert response.json()["error"] == "native_sign_in_rate_limited"
            with native.flows() as conn:
                assert conn.execute("SELECT COUNT(*) FROM native_login_limits").fetchone()[0] == 1
        async with peer_browser("127.0.0.1") as tunnel:
            for bad in ("", "not-an-ip", "203.0.113.5, 198.51.100.1"):
                await begin_as(tunnel, bad or None)
            with native.flows() as conn:
                # Missing or malformed edge identity shares the tunnel's own bucket.
                assert conn.execute("SELECT COUNT(*) FROM native_login_limits").fetchone()[0] == 2

    asyncio.run(run())


def test_native_begin_pending_cap_and_expiry(login, monkeypatch):
    monkeypatch.setattr(native, "MAX_PENDING", 2, raising=False)

    async def run():
        async with browser() as client:
            await start(client, "desktop")
            await start(client, "desktop")
            response = await client.post(
                "/app/native-sign-in",
                headers=ORIGIN,
                json={"client": "desktop", "code_challenge": "c" * 43},
            )
            assert response.status_code == 503
            assert response.json()["error"] == "native_sign_in_capacity_reached"
            with native.flows() as conn:
                assert conn.execute("SELECT COUNT(*) FROM native_login_flows").fetchone()[0] == 2
                conn.execute("UPDATE native_login_flows SET expires=0")
            await start(client, "desktop")

    asyncio.run(run())


@pytest.mark.parametrize(
    "prefix,package", [("app.", "io.tinyassets.app"), ("appdebug.", "io.tinyassets.app.debug")]
)
def test_legacy_android_callback_has_bounded_package_pinned_compatibility(
    login, monkeypatch, prefix, package
):
    from tinyassets import onboarding

    onboarding.app_config()["issuer"] = "https://id.example"
    monkeypatch.setattr(native.time, "time", lambda: 1791676800)  # 2026-10-11 UTC

    async def run():
        async with browser() as client:
            state = prefix + "r" * 24
            response = await client.get("/app", params={"state": state, "code": "old-code"})
            assert f"package={package};end" in response.text
            assert return_params(response) == {"code": "old-code", "state": state}
            monkeypatch.setattr(native.time, "time", lambda: 1792800000)  # 2026-10-24 UTC
            expired = await client.get("/app", params={"state": state, "code": "old-code"})
            assert expired.status_code == 410 and "Update" in expired.text

    asyncio.run(run())
