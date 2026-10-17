"""No-LLM sign-in routing uses owner setup, not host/native credential presence."""

from tests.support.onboarding import _drive_get, _user
from tests.test_onboarding_serving import _grant_admin
from tests.test_open_serving_bind import _bound_and_serving, _setup


def _me(tmp_path, monkeypatch, *, owner="owner-1", uid="u-owner"):
    import tinyassets.onboarding as onboarding

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(onboarding, "_read_home", lambda identity, **kw: uid)
    _grant_admin(tmp_path, owner, uid)
    return _drive_get("/app/me", identity=_user(owner), monkeypatch=monkeypatch)


def test_http_only_serving_is_connected_not_bootstrap(tmp_path, monkeypatch):
    _bound_and_serving(tmp_path, monkeypatch)
    status, doc = _me(tmp_path, monkeypatch)
    assert status == 200
    assert doc["engine_connected"] is True
    assert doc["principal_id"] == "owner-1"
    assert doc["setup"] == "connected"


def test_http_candidate_without_serving_is_recovery(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, doc = _me(tmp_path, monkeypatch)
    assert doc["setup"] == "recovery"
    assert doc["engine_connected"] is False


def test_actual_empty_universe_is_empty(tmp_path, monkeypatch):
    (tmp_path / "u-owner").mkdir()
    _, doc = _me(tmp_path, monkeypatch)
    assert doc["setup"] == "empty"
    assert doc["engine_connected"] is False


def test_unreadable_vault_is_not_empty_or_ready(tmp_path, monkeypatch):
    import tinyassets.credential_vault as vault

    (tmp_path / "u-owner").mkdir()
    def unavailable(_path):
        raise OSError("private diagnostic must not leave the server")
    monkeypatch.setattr(vault, "load_credential_vault", unavailable)
    _, doc = _me(tmp_path, monkeypatch)
    assert doc["setup"] == "unavailable"
    assert doc["engine_connected"] is False
    assert "private diagnostic" not in str(doc)
    assert doc["principal_id"] == "owner-1"


def test_other_owners_serving_does_not_power_this_home(tmp_path, monkeypatch):
    _bound_and_serving(tmp_path, monkeypatch)
    (tmp_path / "u-other").mkdir()
    _, doc = _me(tmp_path, monkeypatch, owner="owner-2", uid="u-other")
    assert doc["universe_id"] == "u-other"
    assert doc["principal_id"] == "owner-2"
    assert doc["setup"] == "empty"
    assert doc["engine_connected"] is False


def test_no_home_get_does_not_bootstrap(tmp_path, monkeypatch):
    import tinyassets.onboarding as onboarding

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(onboarding, "_read_home", lambda identity, **kw: "")
    def forbidden(_identity):
        raise AssertionError("GET must not create a home")
    monkeypatch.setattr(onboarding, "_bootstrap_home", forbidden)
    _, doc = _drive_get("/app/me", identity=_user("owner-1"), monkeypatch=monkeypatch)
    assert doc == {"principal_id": "owner-1", "universe_id": "", "home_bound": False,
                   "engine_connected": False, "setup": "empty"}
