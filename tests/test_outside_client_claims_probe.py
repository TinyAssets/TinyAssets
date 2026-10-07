"""Signed evidence must never be confused with re-consent authorization."""

import io
import json
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from scripts import outside_client_claims_probe as probe
from tinyassets.auth.workos_provider import WorkOSAuthProvider

ISSUER = "https://tenant.example"
RESOURCE = "https://tinyassets.io/mcp"


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def evidence(key):
    class Keys:
        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=key.public_key())

    provider = WorkOSAuthProvider(
        issuer=ISSUER, jwks_uri=ISSUER + "/oauth2/jwks",
        audience=RESOURCE, jwks_client=Keys(),
    )
    now = int(time.time())

    def samples(*, initial=None, refreshed=None, reauthorized=None):
        result = {}
        for index, (stage, extra) in enumerate(zip(
            probe._STAGES, (initial, refreshed, reauthorized), strict=True,
        )):
            claims = {
                "iss": ISSUER, "aud": RESOURCE, "sub": "secret-owner",
                "client_id": "https://client.example/metadata.json",
                "iat": now, "exp": now + 600, "jti": f"secret-token-{index}",
                "sid": "secret-consent-old" if index < 2 else "secret-consent-new",
                "auth_time": now - 100 if index < 2 else now - 10,
            }
            claims.update(extra or {})
            result[stage] = jwt.encode(claims, key, algorithm="RS256")
        return result

    return provider, samples


def test_verified_candidates_are_observations_not_cutover_permission(evidence):
    provider, samples = evidence
    tokens = samples()
    code, receipt = probe.observe(provider, tokens)
    assert code == 0
    assert receipt["cutover_ready"] is False
    assert receipt["auth_time_stable_on_refresh_and_increases_on_reauth"] is True
    assert receipt["sid_stable_on_refresh_and_changes_on_reauth"] is True
    assert receipt["registered_metadata"] == "not_observed"
    assert receipt["exchange_provenance"] == "operator_supplied_not_verified_by_probe"
    serialized = json.dumps(receipt)
    assert "secret-" not in serialized
    assert "client.example" not in serialized
    assert all(token not in serialized for token in tokens.values())


@pytest.mark.parametrize("bad", [
    {"client_id": None}, {"client_id": ""}, {"client_id": []},
    {"client_id": " padded "}, {"azp": "forged-first-party"},
])
def test_missing_or_conflicting_identity_has_no_audience_fallback(evidence, bad):
    provider, samples = evidence
    code, receipt = probe.observe(provider, samples(refreshed=bad))
    assert code == 2
    assert receipt == {"status": "client_identity_unavailable_or_conflicting"}


@pytest.mark.parametrize("bad", [
    {"sub": "other-owner"}, {"client_id": "other-client"},
])
def test_same_owner_and_client_required_for_family_comparison(evidence, bad):
    provider, samples = evidence
    assert probe.observe(provider, samples(reauthorized=bad)) == (
        2, {"status": "owner_or_client_mismatch"},
    )


@pytest.mark.parametrize("bad", [
    {"iss": "https://attacker.example"}, {"aud": "other-resource"},
    {"exp": 1}, {"sub": ""},
])
def test_rejects_wrong_issuer_audience_expired_and_unnamed_tokens(evidence, bad):
    provider, samples = evidence
    assert probe.observe(provider, samples(initial=bad)) == (2, {"status": "invalid_evidence"})


def test_unverified_token_cannot_supply_candidate_claims(evidence):
    provider, samples = evidence
    tokens = samples()
    claims = jwt.decode(tokens["reauthorized"], options={"verify_signature": False})
    tokens["reauthorized"] = jwt.encode(claims, "attacker-key", algorithm="HS256")
    assert probe.observe(provider, tokens) == (2, {"status": "invalid_evidence"})


@pytest.mark.parametrize("auth_time", [None, True, "123", -1, 999999999999])
def test_no_generation_from_iat_or_reused_consent_id(evidence, auth_time):
    provider, samples = evidence
    identical_family = {"auth_time": auth_time, "sid": "same-consent"}
    code, receipt = probe.observe(provider, samples(
        initial=identical_family, refreshed=identical_family, reauthorized=identical_family,
    ))
    assert code == 1
    assert receipt["status"] == "protected_family_binding_required"
    assert receipt["cutover_ready"] is False
    assert receipt["auth_time_stable_on_refresh_and_increases_on_reauth"] is False
    assert receipt["sid_stable_on_refresh_and_changes_on_reauth"] is False


def test_refresh_rotation_is_not_proof_of_interactive_reconsent(evidence):
    provider, samples = evidence
    code, receipt = probe.observe(provider, samples(refreshed={"sid": "rotated", "auth_time": 1}))
    assert code == 1
    assert receipt["status"] == "protected_family_binding_required"


@pytest.mark.parametrize("payload", [[], {}, {"initial": "secret"}])
def test_incomplete_samples_are_not_proof(evidence, payload):
    provider, _ = evidence
    assert probe.observe(provider, payload) == (2, {"status": "invalid_evidence"})


def test_replayed_samples_are_not_refresh_evidence(evidence):
    provider, samples = evidence
    tokens = samples()
    tokens["refreshed"] = tokens["initial"]
    assert probe.observe(provider, tokens) == (2, {"status": "invalid_evidence"})


def test_probe_errors_never_print_input(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("secret-token-not-json"))
    assert probe.main(["--issuer", ISSUER]) == 2
    assert capsys.readouterr().out == '{"status":"evidence_unavailable"}\n'


def test_cli_always_binds_canonical_resource(monkeypatch, capsys):
    observed = {}

    def provider(**kwargs):
        observed.update(kwargs)
        return object()

    monkeypatch.setattr(probe, "WorkOSAuthProvider", provider)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert probe.main(["--issuer", ISSUER]) == 2
    assert observed["audience"] == RESOURCE
    assert capsys.readouterr().out == '{"status": "invalid_evidence"}\n'


@pytest.mark.parametrize("resource", ["", "  ", "https://other.example/mcp"])
def test_cli_cannot_override_canonical_audience(resource, capsys):
    with pytest.raises(SystemExit) as error:
        probe.main(["--issuer", ISSUER, "--resource", resource])
    assert error.value.code == 2
    assert "unrecognized arguments: --resource" in capsys.readouterr().err


@pytest.mark.parametrize("issuer", [
    "http://tenant.example", "https://secret@tenant.example", "https://tenant.example/path",
])
def test_probe_refuses_unsafe_issuer_before_reading_credentials(issuer, capsys):
    assert probe.main(["--issuer", issuer]) == 2
    assert capsys.readouterr().out == '{"status":"invalid_configured_issuer"}\n'
