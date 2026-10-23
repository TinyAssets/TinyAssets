"""Bounded discovery, site-scoped sign-in, and conservative discovered billing."""
import pytest

from tests.test_model_source_detector import AS, CATALOGUE, fetcher, server
from tinyassets.connection_oauth import model_discovery as discovery


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


pytestmark = pytest.mark.verifies('model-connect-by-discovery:Frozen exact destinations')
