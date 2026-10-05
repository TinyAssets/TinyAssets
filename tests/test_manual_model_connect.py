"""Manual acquisition composes the real bootstrap without inference or OAuth."""
# ruff: noqa: F811 -- imported shared pytest fixture is requested by name

import asyncio
import copy
import json

import httpx
import pytest
from starlette.applications import Starlette

from tests.test_model_bootstrap import rig  # noqa: F401
from tests.test_onboarding_model_connect import ingress, post  # noqa: F401
from tinyassets import onboarding
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.onboarding import hosted_model_auth as hosted

pytestmark = pytest.mark.usefixtures("rig", "ingress")
PRESET = "openrouter_user_models_v1"


@pytest.fixture
def installed_policy(monkeypatch):
    """Edit only a synthetic copy of installed data, never owner request metadata."""
    path = hosted.Path(hosted.__file__).parent.parent / "providers" / "acquisition_presets.json"
    documents = json.loads(path.read_text(encoding="utf-8"))
    discovery = hosted.bundled_discovery_documents()
    original = hosted.Path.read_text

    def read(self, *args, **kwargs):
        if self == path:
            return json.dumps(documents)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(hosted.Path, "read_text", read)
    monkeypatch.setattr(hosted, "bundled_discovery_documents", lambda: copy.deepcopy(discovery))
    return documents, discovery


def forbid_home_creation(monkeypatch):
    monkeypatch.setattr(onboarding, "_read_home", lambda *args, **kwargs: "")

    def forbidden(*args, **kwargs):
        raise AssertionError("unapproved acquisition data cannot create a home")

    monkeypatch.setattr(onboarding, "_bootstrap_home", forbidden)


def deposit(key="synthetic-private-key", **kwargs):
    return post("deposit_key", {"preset_id": PRESET, "key": key}, **kwargs)


def test_manual_owner_reaches_existing_approval_then_connects(rig, monkeypatch):
    from tests.owner_answer import answer_request
    from tinyassets.credential_vault import load_credential_vault
    from tinyassets.onboarding.model_setup import model_setup_state
    from tinyassets.provider_assignment import load_provider_assignment

    async def forbidden(**kwargs):
        raise AssertionError("manual acquisition must not exchange OAuth")
    monkeypatch.setattr(hosted, "exchange_key", forbidden)
    response = deposit()
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    result = response.json()
    assert result["status"] == "confirmation_required"
    assert result["request"]["action"]["type"] == "bind_model_access"
    assert "synthetic-private-key" not in response.text
    assert load_provider_assignment(rig, universe_id="u-owner") is None
    assert load_credential_vault(rig / "u-owner")[0]["token"] == "synthetic-private-key"
    resumed = post("resume", {"preset_id": PRESET})
    assert resumed.json()["request_id"] == result["request_id"]
    answered = answer_request(universe_id="u-owner", payload={
        "request_id": result["request_id"], "values": {},
    })
    assert not answered.get("error"), answered
    assert model_setup_state(rig, universe=rig / "u-owner", uid="u-owner",
                             owner="owner") == "connected"


@pytest.mark.parametrize("data", [
    {}, {"preset_id": PRESET, "key": ""}, {"preset_id": PRESET, "key": 123},
    {"preset_id": PRESET, "key": "a" * 2049}, {"preset_id": PRESET, "key": "has space"},
    {"preset_id": PRESET, "key": "key\n"}, {"preset_id": PRESET, "key": "é"},
    {"preset_id": PRESET, "key": "key", "model": "paid-model"},
    {"preset_id": PRESET, "key": "key", "manual_key_entry": True},
    {"preset_id": PRESET, "key": "key", "owner": "another-owner"},
])
def test_manual_invalid_input_refused_before_preset_or_home(data, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid input cannot load or bootstrap")
    monkeypatch.setattr(hosted, "load_preset", forbidden)
    monkeypatch.setattr(onboarding, "_bootstrap_home", forbidden)
    response = post("deposit_key", data)
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_model_connection"}


@pytest.mark.parametrize("flag", [False, None, "true", 1, {}, [], "absent"])
def test_manual_installed_opt_in_is_explicit_boolean_before_home(
    installed_policy, monkeypatch, flag,
):
    document = installed_policy[0][PRESET]
    if flag == "absent":
        document.pop("manual_key_entry")
    else:
        document["manual_key_entry"] = flag
    forbid_home_creation(monkeypatch)
    # Normal PKCE acquisition remains available independently of this capability.
    assert hosted.load_preset(PRESET).id == PRESET
    response = deposit()
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_model_connection"}


def test_second_installed_preset_does_not_silently_gain_manual_ingress(
    installed_policy, monkeypatch,
):
    documents, discovery = installed_policy
    second = "another_user_models_v1"
    documents[second] = {key: value for key, value in documents[PRESET].items()
                         if key != "manual_key_entry"}
    discovery[second] = copy.deepcopy(discovery[PRESET])
    assert hosted.load_preset(second).id == second  # Installed and otherwise valid.
    forbid_home_creation(monkeypatch)
    response = post("deposit_key", {"preset_id": second, "key": "synthetic-key"})
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_model_connection"}


def test_unknown_preset_cannot_create_home(monkeypatch):
    forbid_home_creation(monkeypatch)
    response = post("deposit_key", {"preset_id": "not-installed", "key": "synthetic-key"})
    assert response.status_code == 400


@pytest.mark.parametrize("mutation", [
    "http_endpoint", "credential_url", "protocol", "mismatched_catalogue",
    "mismatched_benchmark", "malformed_discovery", "not_owner_filtered", "wrong_auth",
    "missing_discovery",
])
def test_manual_opt_in_still_validates_endpoints_and_contract_before_home(
    installed_policy, monkeypatch, mutation,
):
    documents, discovery = installed_policy
    doc = documents[PRESET]
    if mutation == "http_endpoint":
        doc["inference_url"] = "http://provider.invalid/infer"
    elif mutation == "credential_url":
        doc["inference_url"] = "https://user:secret@provider.invalid/infer"
    elif mutation == "protocol":
        doc["protocol"] = "unsupported"
    elif mutation == "mismatched_catalogue":
        doc["catalogue_url"] = "https://provider.invalid/other"
    elif mutation == "mismatched_benchmark":
        doc["benchmark_url"] = "https://provider.invalid/other"
    elif mutation == "malformed_discovery":
        discovery[PRESET] = {"transport": {}}
    elif mutation == "not_owner_filtered":
        discovery[PRESET]["transport"]["account_filtered"] = False
    elif mutation == "wrong_auth":
        discovery[PRESET]["transport"]["auth_scheme"] = "none"
    else:
        discovery.pop(PRESET)
    forbid_home_creation(monkeypatch)
    response = deposit()
    assert response.status_code == (404 if mutation == "missing_discovery" else 503)
    assert "synthetic-private-key" not in response.text


def test_installed_manual_policy_participates_in_preset_digest(installed_policy):
    original = hosted.load_preset(PRESET)
    installed_policy[0][PRESET]["manual_key_entry"] = False
    assert hosted.load_preset(PRESET).digest != original.digest


@pytest.mark.parametrize("origin", ["", "http://tinyassets.io", "https://foreign.invalid"])
def test_manual_wrong_origin_has_no_vault(rig, origin):
    from tinyassets.credential_vault import load_credential_vault

    assert deposit(origin=origin).status_code == 403
    assert load_credential_vault(rig / "u-owner") == []


@pytest.mark.parametrize("principal", [None, "collaborator"])
def test_manual_requires_authenticated_home_owner(rig, principal):
    from tinyassets.credential_vault import load_credential_vault

    identity = (Identity(user_id=principal, username=principal, capabilities=["write"])
                if principal else None)
    with identity_context(identity):
        assert deposit().status_code == (409 if principal else 401)
    assert load_credential_vault(rig / "u-owner") == []


def test_manual_does_not_replace_existing_setup(rig):
    from tinyassets.credential_vault import load_credential_vault

    assert deposit("first-key").status_code == 200
    response = deposit("replacement-key")
    assert response.status_code == 409
    assert response.json()["error"] == "model_setup_changed"
    assert load_credential_vault(rig / "u-owner")[0]["token"] == "first-key"


def test_manual_unexpected_error_never_echoes_or_logs_secret(monkeypatch, caplog):
    from tinyassets.onboarding import model_bootstrap

    def broken(**kwargs):
        raise RuntimeError(kwargs["key"])
    monkeypatch.setattr(model_bootstrap, "complete_bootstrap", broken)
    response = deposit()
    assert response.status_code == 503
    assert response.json() == {"error": "model_connection_incomplete"}
    assert "synthetic-private-key" not in response.text + caplog.text


@pytest.mark.parametrize("content,content_type,expected", [
    ("{bad json", "application/json", 400),
    (json.dumps({"preset_id": PRESET, "key": "x" * 9000}), "application/json", 400),
    ("secret-text", "text/plain", 403),
])
def test_manual_bounded_json(content, content_type, expected):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=Starlette(routes=onboarding.onboarding_routes())), base_url="https://tinyassets.io",
        ) as client:
            return await client.post("/app/model-connect/deposit_key", content=content,
                                     headers={"Origin": "https://tinyassets.io",
                                              "Content-Type": content_type})
    assert asyncio.run(run()).status_code == expected


def test_manual_full_2048_printable_key_is_accepted():
    assert deposit("x" * 2048).status_code == 200


def test_manual_deleted_account_cannot_deposit(rig):
    from tinyassets.account_deletion import write_tombstone
    from tinyassets.credential_vault import load_credential_vault

    write_tombstone(rig, "owner")
    response = deposit()
    assert response.status_code == 409
    assert load_credential_vault(rig / "u-owner") == []


@pytest.mark.parametrize("free", [True, False])
def test_manual_catalogue_never_selects_paid_model(monkeypatch, free):
    from tests.test_model_bootstrap_candidate import row
    from tinyassets.providers import discovery_http
    from tinyassets.providers.definition import get_definition, list_definitions

    models = [row("paid-first", "1")]
    if free:
        models.append(row("eligible-free", "0"))
    monkeypatch.setattr(discovery_http, "read_granted_discovery_document",
                        lambda **kwargs: {"data": models})
    response = deposit()
    if not free:
        assert response.status_code == 409
        assert response.json()["error"] == "no_eligible_free_agent_model"
        assert not list_definitions("u-owner")
    else:
        assert response.status_code == 200, response.text
        did = response.json()["request"]["action"]["provider"]
        assert get_definition("u-owner", did).model == "eligible-free"
