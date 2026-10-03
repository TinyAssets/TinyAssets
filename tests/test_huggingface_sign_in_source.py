"""The Hugging Face source card: one tap raises the platform's own ``connect``
ask, signing in answers it through the generic OAuth flow, and the source then
joins the owner's agent (openspec change ``huggingface-sign-in-source``).

Hugging Face is played by one local fake: ``huggingface.co`` is the issuer
(OpenID + RFC 8414 metadata, authorize, token) and ``router.huggingface.co`` is
the inference host, which publishes no RFC 9728 document -- the shape measured
live on 2026-10-01. Everything else is production code.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import sqlite3
import threading
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest

from tests import test_generic_oauth_connections as generic
from tests.test_authenticated_external_call_effector import _install_loopback_driver
from tests.test_generic_oauth_connections import (
    CHALLENGE,
    OWNER,
    RESOURCE,
    UID,
    VERIFIER,
    _as,
    _get,
    _post,
)

# The generic OAuth suite's two-owner rig, reused as fixtures.
app = generic.app
universes = generic.universes

ISSUER = "huggingface.co"
ROUTER = "router.huggingface.co"
CIMD = "https://tinyassets.io/app/oauth/client-metadata.json"
REDIRECT = "https://tinyassets.io/app/model-callback/connect"


class FakeHuggingFace:
    def __init__(self):
        self.codes: dict[str, dict] = {}
        self.authorize_queries: list[dict] = []
        self.token_forms: list[dict] = []
        self.issued: list[str] = []
        provider = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                return

            def _handle(self):
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(length) if length else b""
                host = (self.headers.get("Host") or "").split(":")[0]
                status, doc = provider.route(self.command, host, self.path, body)
                payload = json.dumps(doc).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _handle  # noqa: N815
            do_POST = _handle  # noqa: N815

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def metadata(self):
        # Fields as published at https://huggingface.co/.well-known/openid-configuration.
        return {
            "issuer": f"https://{ISSUER}",
            "authorization_endpoint": f"https://{ISSUER}/oauth/authorize",
            "token_endpoint": f"https://{ISSUER}/oauth/token",
            "registration_endpoint": f"https://{ISSUER}/oauth/register",
            "userinfo_endpoint": f"https://{ISSUER}/oauth/userinfo",
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic",
                                                      "client_secret_post"],
            "client_id_metadata_document_supported": True,
            "grant_types_supported": ["authorization_code", "refresh_token"],
        }

    def authorize(self, authorize_url: str) -> str:
        parts = urlsplit(authorize_url)
        assert parts.hostname == ISSUER and parts.path == "/oauth/authorize"
        q = dict(parse_qsl(parts.query))
        self.authorize_queries.append(q)
        code = secrets.token_urlsafe(16)
        self.codes[code] = q
        return q["redirect_uri"] + "?" + urlencode({"code": code, "state": q["state"]})

    def route(self, method, host, path, body):
        if host == ISSUER and path in ("/.well-known/oauth-authorization-server",
                                       "/.well-known/openid-configuration"):
            return 200, self.metadata()
        if host == ISSUER and path == "/oauth/register":
            raise AssertionError("a sign-in source never registers a client dynamically")
        if host == ISSUER and path == "/oauth/token" and method == "POST":
            form = dict(parse_qsl(body.decode()))
            self.token_forms.append(form)
            grant = self.codes.pop(form.get("code", ""), None)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(
                form.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
            if (grant is None or grant["client_id"] != form.get("client_id")
                    or grant["redirect_uri"] != form.get("redirect_uri")
                    or grant["code_challenge"] != challenge):
                return 400, {"error": "invalid_grant"}
            access = "hf_oauth_" + secrets.token_urlsafe(16)
            self.issued.append(access)
            return 200, {"access_token": access, "token_type": "Bearer",
                         "expires_in": 28800,
                         "refresh_token": "hf_rt_" + secrets.token_urlsafe(8),
                         "scope": grant.get("scope", "")}
        return 404, {"error": "not_found"}


def enable_fake_source(monkeypatch):
    """Protocol fixture only: the fake has no billable upstream.

    The real installed source stays unavailable until its free-only boundary is
    proved. Enabling this test copy does not assert real provider eligibility.
    """
    from copy import deepcopy

    from tinyassets.providers import free_sources

    sources = deepcopy(free_sources._SOURCES)
    for row in sources:
        if row["id"] == "huggingface":
            row["available"] = True
    monkeypatch.setattr(free_sources, "_SOURCES", sources)


@pytest.fixture
def hf(monkeypatch):
    from tinyassets.connection_oauth import discovery

    enable_fake_source(monkeypatch)
    fake = FakeHuggingFace()
    _install_loopback_driver(monkeypatch, fake.port)
    monkeypatch.setattr(discovery, "DISCOVERY_ENABLED", True)
    monkeypatch.delenv("TINYASSETS_HUGGINGFACE_OAUTH_CLIENT_ID", raising=False)
    yield fake
    fake.stop()


def _tap(preset_id="huggingface"):
    with _as(OWNER):
        return _post("source_sign_in", {"preset_id": preset_id})


def _sign_in(hf, request_id):
    with _as(OWNER):
        begun = _post("oauth_begin", {"request_id": request_id, "code_challenge": CHALLENGE})
        assert begun.status_code == 200, begun.text
        back = urlsplit(hf.authorize(begun.json()["authorize_url"]))
        assert _get(back.path + "?" + back.query).status_code == 200
        q = dict(parse_qsl(back.query))
        return _post("oauth_exchange", {"flow": q["state"], "code": q["code"],
                                        "code_verifier": VERIFIER})


def test_one_tap_raises_the_platforms_ask_from_installed_data(hf, app):
    tapped = _tap()
    assert tapped.status_code == 200, tapped.text
    request = tapped.json()["request"]
    action = request["action"]
    assert request["origin"] == "platform" and request["fields"] == []
    # ONE host: inference needs exactly one, and no profile endpoint is granted.
    assert action["endpoints"] == [{"host": ROUTER, "path_template": "/v1/chat/completions",
                                    "methods": ["POST"]}]
    assert action["uses"]["model"]["billing"] == "free"
    assert [m["id"] for m in action["uses"]["model"]["models"]] == [
        "openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    offer = action["oauth"]
    assert offer["issuer"] == f"https://{ISSUER}"
    assert offer["token_url"] == f"https://{ISSUER}/oauth/token"
    assert offer["client_id"] == CIMD and offer["registration_url"] == ""
    assert offer["scopes"] == ["openid", "profile", "inference-api"]
    # The owner reads the billing truth on the ask itself.
    assert "$0.10" in request["body"] and "cannot enforce" in request["body"]
    # A second tap reuses the same pending ask.
    again = _tap()
    assert again.json()["request"]["request_id"] == request["request_id"]


def test_unknown_or_key_card_ids_are_refused_before_any_home(hf, app):
    assert _tap("not-a-source").status_code == 404
    assert _tap("groq").status_code == 404  # a key card is never a sign-in source


def test_a_configured_registered_client_replaces_the_metadata_document(hf, app, monkeypatch):
    monkeypatch.setenv("TINYASSETS_HUGGINGFACE_OAUTH_CLIENT_ID", "registered-public-client")
    tapped = _tap()
    assert tapped.json()["request"]["action"]["oauth"]["client_id"] == "registered-public-client"


def test_an_agent_cannot_raise_the_sign_in_ask_itself(hf, app):
    """No server-set discovery root, no offer: the identical payload makes no row."""
    from tinyassets.api.pending_requests import list_requests, request_from_user
    from tinyassets.onboarding.source_connect import _ask_body, sign_in_action
    from tinyassets.providers.free_sources import sign_in_preset

    preset = sign_in_preset("huggingface")
    with _as(OWNER):
        refused = request_from_user(universe_id=UID, payload={
            "kind": "LLM", "title": "Use your Hugging Face models", "body": _ask_body(preset),
            "fields": [], "action": sign_in_action(preset, RESOURCE)})
        assert refused["error"] == "request_invalid"
        assert "no sign-in is offered" in refused["detail"]
        pending = list_requests(universe_id=UID)["pending"]
    assert all(r.get("action", {}).get("destination") != "model-huggingface" for r in pending)


def test_sign_in_on_an_unpowered_command_center_serves_on_it(hf, app):
    from tinyassets.connection_oauth.tokens import decode
    from tinyassets.credential_vault import load_credential_vault

    request_id = _tap().json()["request"]["request_id"]
    done = _sign_in(hf, request_id)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "answered" and body["signed_in"] is True
    assert body["destination"] == "model-huggingface"
    assert body["serving"]["status"] != "unchanged" and "confirmation" not in body
    for token in hf.issued:
        assert token not in done.text
    authorize = hf.authorize_queries[0]
    assert authorize["client_id"] == CIMD and authorize["redirect_uri"] == REDIRECT
    assert authorize["scope"] == "openid profile inference-api"
    records = [r for r in load_credential_vault(app / UID)
               if r.get("destination") == "model-huggingface"]
    assert len(records) == 1
    bundle = decode(records[0]["token"])
    assert bundle.token_url == f"https://{ISSUER}/oauth/token" and bundle.client_id == CIMD
    assert bundle.access_token == hf.issued[0] and bundle.refresh_token


def test_sign_in_on_a_powered_command_center_asks_to_add_it(hf, app, monkeypatch):
    from tinyassets.api import connection_uses

    monkeypatch.setattr(connection_uses, "select_model_if_unpowered",
                        lambda **_: {"status": "unchanged", "reason": "already_powered"})
    done = _sign_in(hf, _tap().json()["request"]["request_id"])
    assert done.status_code == 200, done.text
    confirmation = done.json()["confirmation"]
    assert confirmation["action"]["type"] == "bind_model_access"
    assert confirmation["origin"] == "platform"
    access = confirmation["action"]["model_access"]
    (entry,) = access.values()
    assert entry["model_ids"] == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    assert entry["cost_caps"] is None
    assert "$0.10" in confirmation["body"]


def test_post_sign_in_database_failure_preserves_success(hf, app, monkeypatch):
    from tinyassets.api import connection_uses
    from tinyassets.onboarding import source_connect

    monkeypatch.setattr(connection_uses, "select_model_if_unpowered",
                        lambda **_: {"status": "unchanged", "reason": "already_powered"})

    def broken_offer(**kwargs):
        raise sqlite3.OperationalError("private database failure detail")

    monkeypatch.setattr(source_connect, "_offer_pool_access", broken_offer)
    done = _sign_in(hf, _tap().json()["request"]["request_id"])
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "answered"
    assert done.json()["signed_in"] is True
    assert done.json()["confirmation_error"] == "model_confirmation_requires_review"
    assert "private database failure detail" not in done.text
    assert "confirmation" not in done.json()


def test_an_agent_ask_under_the_same_name_gets_no_pool_offer(hf, app, monkeypatch):
    """Destination is agent-controllable; the offer needs the canonical platform ask."""
    from tinyassets.api import connection_uses
    from tinyassets.api.pending_requests import request_from_user

    monkeypatch.setattr(connection_uses, "select_model_if_unpowered",
                        lambda **_: {"status": "unchanged", "reason": "already_powered"})
    with _as(OWNER):
        # An endpoint ON the issuer host roots discovery, so this ask does get
        # a sign-in offer -- but it is not the installed card's action.
        asked = request_from_user(universe_id=UID, payload={
            "kind": "LLM", "title": "Use your Hugging Face models", "body": "", "fields": [],
            "action": {"type": "connect", "destination": "model-huggingface",
                       "auth_scheme": "bearer", "host": ISSUER,
                       "path_template": "/v1/chat/completions", "methods": ["POST"],
                       "uses": {"model": {"wire": "openai_chat", "billing": "free", "models": [
                           {"id": "agent-picked", "tools": True, "context": 32768}]}},
                       "oauth": {"scopes": ["inference-api"], "client_id": CIMD}}})
    assert asked.get("primary") == "sign_in", asked
    done = _sign_in(hf, asked["request_id"])
    assert done.status_code == 200, done.text
    assert "confirmation" not in done.json()


def test_the_client_metadata_document_is_public_and_constant(app):
    from tinyassets.auth.middleware import _auth_challenge_path

    assert _auth_challenge_path("/app/oauth/client-metadata.json") is False
    assert _auth_challenge_path("/app/oauth/other.json") is True
    assert _auth_challenge_path("/app/model-connect/source_sign_in") is True
    served = _get("/app/oauth/client-metadata.json")
    assert served.status_code == 200
    assert served.json() == {
        "client_id": CIMD, "client_name": "TinyAssets", "client_uri": "https://tinyassets.io",
        "redirect_uris": [REDIRECT], "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none",
        "application_type": "web",
    }


def test_the_document_never_follows_the_request_host(app):
    import asyncio

    import httpx
    from starlette.applications import Starlette

    from tinyassets import onboarding

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
                app=Starlette(routes=onboarding.onboarding_routes())),
                base_url="https://attacker.example") as client:
            return await client.get("/app/oauth/client-metadata.json")
    served = asyncio.run(run())
    assert served.json()["redirect_uris"] == [REDIRECT]


def test_the_connect_setup_offers_sign_in_sources_and_daily_caps(hf, universes):
    from tinyassets.api.pending_requests import _connect_llm_request

    setup = _connect_llm_request(connected=True)["action"]["setup"]
    from tinyassets.providers.free_sources import sign_in_preset

    preset = sign_in_preset("huggingface")
    assert setup["sign_in_sources"] == [{
        "id": "huggingface", "name": "Hugging Face",
        "offer": preset["offer"], "label": "Sign in with Hugging Face",
        "billing_note": preset["billing_note"], "daily_cap": preset["daily_cap"]}]
    assert "$0.10" in setup["sign_in_sources"][0]["billing_note"]
    assert "cannot enforce" in setup["sign_in_sources"][0]["billing_note"]
    assert "huggingface" not in {card["id"] for card in setup["sources"]}
    (cap,) = setup["daily_caps"]
    assert cap == {"host": "openrouter.ai", "name": "OpenRouter", "free_requests_per_day": 50,
                   "credit_requests_per_day": 1000, "credit_amount": "$10",
                   "credit_url": "https://openrouter.ai/settings/credits",
                   "reset_timezone": "UTC"}
    # The credit link is the same page the daily-quota detail already names.
    from tinyassets.providers.free_sources import billing_url_for_host

    assert billing_url_for_host("openrouter.ai") == cap["credit_url"]


def test_installed_unsafe_source_is_not_offered():
    from tinyassets.api.pending_requests import _connect_llm_request
    from tinyassets.providers.free_sources import sign_in_cards, sign_in_preset, source_cards

    assert sign_in_preset("huggingface") is None
    assert all(row["id"] != "huggingface" for row in sign_in_cards() + source_cards())
    assert _connect_llm_request()["action"]["setup"]["sign_in_sources"] == []


def test_withheld_source_cannot_start_oauth_or_create_a_request(app, monkeypatch):
    from tinyassets import onboarding
    from tinyassets.api.pending_requests import list_requests

    def forbidden(*args, **kwargs):
        raise AssertionError("withheld source must refuse before home/bootstrap/discovery")

    monkeypatch.setattr(onboarding, "_bootstrap_home", forbidden)
    with _as(OWNER):
        before = list_requests(universe_id=UID)
        response = _post("source_sign_in", {"preset_id": "huggingface"})
        after = list_requests(universe_id=UID)
    assert response.status_code == 404
    assert before == after
