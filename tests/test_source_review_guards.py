"""Bounded discovery, site-scoped sign-in, and conservative discovered billing."""
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tests.test_model_source_detector import API, AS, CATALOGUE, fetcher, server
from tinyassets.connection_oauth import model_discovery as discovery
from tinyassets.connection_oauth.transport import OAuthError


def test_discovery_deadline_stops_probing(monkeypatch):
    import time

    clock = [0]
    calls = []
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def slow(url, **kwargs):
        calls.append(url)
        clock[0] += 15
        return 404, None, {}

    with pytest.raises(OAuthError, match="discovery_timeout"):
        discovery.detect(API, fetch=slow)
    assert len(calls) == 3
    assert clock[0] < 60


def test_discovery_runs_outside_fence_and_rechecks_after(monkeypatch):
    from tinyassets.broker import admission, model_connect
    from tinyassets.broker.server import OWNER, _Connection

    held, checks = [], []

    @contextmanager
    def send(*args):
        checks.append(args)
        held.append(True)
        try:
            yield
        finally:
            held.pop()

    connection = object.__new__(_Connection)
    connection._role = OWNER
    connection._closed = threading.Event()
    connection._server = SimpleNamespace(_fence=SimpleNamespace(send=send), _owner_identities=None)
    monkeypatch.setattr(admission, "require_admitted", lambda *a: None)

    def discover(ledger, **kwargs):
        assert ledger is None and not held
        return {"source": "fixture"}

    monkeypatch.setattr(model_connect, "local_operation", discover)
    result = connection._http_connect({"op": "HTTP_CONNECT", "principal": "owner",
        "generation": 1, "token": "fence", "document": {"action": "model_discover",
        "address": API, "callback": "https://app.example/callback"}})
    assert result["result"] == {"source": "fixture"}
    assert checks == [(1, "fence"), (1, "fence")]


def test_discovery_cannot_borrow_another_sites_sign_in():
    attacker = "https://evil.example"
    fetch = fetcher({
        attacker + "/v1/models": (200, CATALOGUE),
        attacker + "/.well-known/oauth-protected-resource": (200, {
            "resource": attacker, "authorization_servers": [AS]}),
        AS + "/.well-known/oauth-authorization-server": server(
            registration_endpoint=AS + "/register"),
    })
    assert discovery.detect(attacker, fetch=fetch)["chosen"]["verdict"] == "api_key"


def test_discovered_models_need_explicit_zero_prices():
    from tinyassets.onboarding.hosted_model_auth import HostedAuthError
    from tinyassets.onboarding.source_connect import _discovered_free_models

    rows = [{"id": "unknown"}, {"id": "paid", "pricing": {"prompt": "1", "completion": "2"}},
            {"id": "free", "pricing": {"prompt": "0", "completion": "0", "request": "0"}}]
    assert [m["id"] for m in _discovered_free_models({"data": rows})] == ["free"]
    with pytest.raises(HostedAuthError):
        _discovered_free_models({"data": rows[:2]})


def test_site_identity_respects_private_and_country_suffixes():
    from tinyassets.connection_oauth.site_identity import _psl, site

    assert _psl() is not None, "Linux images must supply libpsl"
    assert site("https://auth.provider.co.uk") == site("https://api.provider.co.uk")
    assert site("https://attacker.github.io") != site("https://provider.github.io")
    assert site("https://provider.co.uk") != site("https://attacker.co.uk")
