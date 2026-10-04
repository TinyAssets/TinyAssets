"""Model settings through real app routes and the real identity middleware."""

import asyncio
import json
import sqlite3

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from tinyassets import onboarding
from tinyassets.auth import middleware as mw
from tinyassets.auth.provider import Identity
from tinyassets.daemon_server import set_founder_home
from tinyassets.providers.model_preferences import MAX_POLICY_BYTES
from tinyassets.storage.model_preferences import ModelPreferenceStore
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

URL = "/app/models/preferences"
A, B = "user_model_a", "user_model_b"
HOME_A, HOME_B = "u-1111111111111111", "u-2222222222222222"
AUTO = {"version": 1, "mode": "automatic", "saved_default": None, "fallbacks": []}
PIN = {
    "version": 1,
    "mode": "explicit",
    "fallbacks": [],
    "saved_default": {"provider_ref": "owner-connected:future-provider", "model_id": "New/モデル"},
}


def stored(policy):
    """What the server stores for a given CLIENT document.

    The documents above are deliberately left at version 1 -- a client that
    predates per-model effort must keep working -- and the server normalizes
    them to the current version on the way in. So the input proves back-compat
    and this is the expected output.
    """
    return {**policy, "version": 2, "efforts": []}


class Auth:
    def resolve_token(self, token):
        return Identity(user_id=token, username=token) if token in {A, B, "no_home"} else None

    def is_auth_required(self):
        return True

    def resolve_always_writes(self):
        return False

    def writes_require_identity(self):
        return True

    def challenge_unauthenticated(self):
        return True


def headers(owner=A, **updates):
    result = {
        "Authorization": f"Bearer {owner}",
        "Origin": "https://tinyassets.io",
        "Content-Type": "application/json",
    }
    result.update(updates)
    return result


def payload(generation=0, policy=None):
    return {"expected_generation": generation, "policy": PIN if policy is None else policy}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setattr("tinyassets.api.helpers._base_path", lambda: tmp_path)
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://tinyassets.io/mcp"})
    monkeypatch.setattr(mw, "_provider", Auth())
    for owner, home in ((A, HOME_A), (B, HOME_B)):
        (tmp_path / home).mkdir()
        (tmp_path / home / "soul.md").write_text("# Test home", encoding="utf-8")
        set_founder_home(tmp_path, founder_sub=owner, universe_id=home, platform_generated=True)

    def no_inference(*args, **kwargs):
        raise AssertionError("settings must not need inference or bind a provider")

    monkeypatch.setattr("tinyassets.providers.call.call_provider", no_inference)
    monkeypatch.setattr("tinyassets.onboarding.serving.ensure_founder_serving", no_inference)
    with TestClient(
        mw.AuthContextMiddleware(Starlette(routes=onboarding.onboarding_routes())),
        base_url="https://tinyassets.io",
    ) as client:
        yield client, tmp_path


def test_anonymous_and_invalid_bearer_cannot_reach_preferences(app):
    client, base = app
    for method in ("GET", "POST"):
        assert client.request(method, URL).status_code == 401
        assert client.request(method, URL, headers=headers("invalid")).status_code == 401
    assert ModelPreferenceStore(base).get(A, HOME_A).generation == 0


def test_unpowered_get_then_save_default_then_stale_tab_conflicts(app):
    client, base = app
    missing = client.get(URL, headers=headers())
    assert missing.status_code == 200
    assert missing.json() == {
        "universe_id": HOME_A,
        "generation": 0,
        "policy": None,
        "updated_at": None,
    }
    assert missing.headers["cache-control"] == "no-store"
    saved = client.post(URL, headers=headers(), json=payload())
    assert saved.status_code == 200 and saved.json()["generation"] == 1
    assert saved.json()["policy"] == stored(PIN)
    conflict = client.post(URL, headers=headers(), json=payload(policy=AUTO))
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "model_preferences_conflict"
    assert conflict.json()["policy"] == stored(PIN) and conflict.json()["generation"] == 1
    assert client.get(URL, headers=headers()).json() == saved.json()
    reset = client.post(URL, headers=headers(), json=payload(1, AUTO))
    assert reset.status_code == 200 and reset.json()["generation"] == 2
    with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
        assert conn.execute("SELECT count(*) FROM provider_work_bindings").fetchone()[0] == 0


def test_authenticated_user_cannot_select_another_home(app):
    client, _ = app
    client.post(URL, headers=headers(A), json=payload())
    other = client.get(URL + f"?universe_id={HOME_A}&owner_user_id={A}", headers=headers(B))
    assert other.status_code == 409
    assert other.json() == {"error": "model_preference_home_changed"}
    forged = client.post(URL, headers=headers(B), json={**payload(), "universe_id": HOME_A})
    assert forged.status_code == 400
    saved = client.post(URL, headers=headers(B), json=payload(policy=AUTO))
    assert saved.status_code == 200 and saved.json()["universe_id"] == HOME_B
    assert client.get(URL, headers=headers(A)).json()["policy"] == stored(PIN)


def test_explicit_dialog_target_cannot_write_newly_rebound_home(app):
    client, base = app
    snapshot = client.get(URL, headers=headers()).json()
    with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
        conn.execute("UPDATE founder_home SET universe_id = ? WHERE founder_sub = ?", (HOME_B, A))
    response = client.post(URL + "?universe_id=" + snapshot["universe_id"],
                           headers=headers(), json=payload())
    assert response.status_code == 409
    assert response.json() == {"error": "model_preference_home_changed"}
    assert ModelPreferenceStore(base).get(A, HOME_A).generation == 0
    assert ModelPreferenceStore(base).get(A, HOME_B).generation == 0


def test_explicit_current_home_target_preserves_cas(app):
    client, _ = app
    first = client.post(URL + "?universe_id=" + HOME_A, headers=headers(), json=payload())
    assert first.status_code == 200
    second = client.post(URL + "?universe_id=" + HOME_A, headers=headers(), json=payload())
    assert second.status_code == 409
    assert second.json()["error"] == "model_preferences_conflict"


@pytest.mark.parametrize(
    "changes",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "http://tinyassets.io"},
        {"Origin": "https://[invalid"},
        {"Origin": "https://tinyassets.io/not-an-origin"},
        {"Origin": ""},
        {"Content-Type": "text/plain"},
        {"Content-Type": "application/x-www-form-urlencoded"},
    ],
)
def test_write_requires_same_origin_json(app, changes):
    client, base = app
    response = client.post(URL, headers=headers(**changes), json=payload())
    assert response.status_code == 403 and response.headers["cache-control"] == "no-store"
    assert ModelPreferenceStore(base).get(A, HOME_A).policy is None


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"null",
        b"[]",
        b"{}",
        b'{"policy":null,"expected_generation":0}',
        b'{"expected_generation":0,"expected_generation":1,"policy":{}}',
        b'{"expected_generation":0,"policy":{"mode":"automatic","mode":"explicit"}}',
        json.dumps(payload()).encode("utf-16"),
        b"\xff",
    ],
    ids=[
        "empty",
        "null",
        "list",
        "object",
        "null-policy",
        "duplicate-root",
        "duplicate-nested",
        "utf16",
        "bad-utf8",
    ],
)
def test_malformed_or_ambiguous_input_refused(app, raw):
    client, base = app
    response = client.post(URL, headers=headers(), content=raw)
    assert response.status_code == 400
    assert ModelPreferenceStore(base).get(A, HOME_A).policy is None


def test_body_bound_declared_and_chunked(app):
    client, base = app
    declared = client.post(
        URL, headers=headers(**{"Content-Length": str(MAX_POLICY_BYTES + 1)}), content=b"{}"
    )
    streamed = client.post(URL, headers=headers(), content=iter([b"x" * (MAX_POLICY_BYTES + 1)]))
    assert declared.status_code == streamed.status_code == 400
    assert ModelPreferenceStore(base).get(A, HOME_A).policy is None


def test_no_home_does_not_bootstrap(app):
    client, base = app
    for method in ("GET", "POST"):
        response = client.request(method, URL, headers=headers("no_home"), json=payload())
        assert response.status_code == 409 and response.json()["error"] == "no_home_universe"
    assert ModelPreferenceStore(base).get("no_home", HOME_A).policy is None


def test_home_read_error_is_503_not_no_home_and_legacy_helper_unchanged(app, monkeypatch, caplog):
    client, _ = app

    def broken(*args):
        raise sqlite3.OperationalError("private database path / secret")

    monkeypatch.setattr("tinyassets.daemon_server.get_founder_home", broken)
    response = client.get(URL, headers=headers())
    assert response.status_code == 503
    assert response.json() == {"error": "model_preferences_unavailable"}
    assert "secret" not in response.text + caplog.text
    assert onboarding._read_home(Identity(user_id=A, username="A")) == ""


def test_corruption_is_held_even_with_stale_generation(app):
    client, base = app
    client.post(URL, headers=headers(), json=payload())
    with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
        conn.execute(
            "UPDATE universe_model_preferences SET policy_json = '{}' WHERE owner_user_id = ?", (A,)
        )
    assert client.get(URL, headers=headers()).status_code == 503
    assert client.post(URL, headers=headers(), json=payload(policy=AUTO)).status_code == 503


def test_disabled_route_is_not_activated_by_preferences(app, monkeypatch):
    client, _ = app
    monkeypatch.delenv("TINYASSETS_ONBOARDING_APP")
    assert client.get(URL, headers=headers()).status_code == 404
    assert client.post(URL, headers=headers(), json=payload()).status_code == 404


def test_handler_refuses_missing_identity_without_middleware():
    from tinyassets.onboarding.model_preferences import handle_model_preferences

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TINYASSETS_ONBOARDING_APP", "1")
        with mw.identity_context(None):
            response = asyncio.run(handle_model_preferences(object()))
    assert response.status_code == 401 and response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("mutation", ["rebound", "removed", "deleted"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_home_is_refenced_inside_database_transaction(app, monkeypatch, mutation, method):
    from tinyassets.account_deletion import principal_digest

    client, base = app
    original = onboarding._read_home

    def racing(identity, **kwargs):
        home = original(identity, **kwargs)
        with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
            if mutation == "rebound":
                conn.execute(
                    "UPDATE founder_home SET universe_id = ? WHERE founder_sub = ?", (HOME_B, A)
                )
            elif mutation == "removed":
                conn.execute("DELETE FROM founder_home WHERE founder_sub = ?", (A,))
            else:
                conn.execute(
                    "INSERT INTO deleted_principals (founder_sub, deleted_at) VALUES (?, 1)",
                    (principal_digest(A),),
                )
        return home

    monkeypatch.setattr(onboarding, "_read_home", racing)
    response = client.request(method, URL, headers=headers(), json=payload())
    assert response.status_code == 409
    assert response.json() == {"error": "model_preference_home_changed"}
    assert ModelPreferenceStore(base).get(A, HOME_A).policy is None
    assert ModelPreferenceStore(base).get(A, HOME_B).policy is None
