"""The canary signs in and repairs itself; anything short of ready records provisioned=0."""

from __future__ import annotations

import asyncio
import json

import pytest

from scripts import core_canary_provision as setup
from scripts import verify_core_canary_provision as provision

_SECRETS = {
    "TINYASSETS_CANARY_EMAIL": "canary@example.invalid",
    "TINYASSETS_CANARY_PASSWORD": "secret-value",
    "TINYASSETS_CANARY_HMAC_KEY": "hmac-secret",
    "TINYASSETS_CANARY_DOOR_KEY": "door-secret",
    "TINYASSETS_CANARY_UNIVERSE_ID": "u-canary",
}


def _set(monkeypatch, **overrides):
    for name, value in {**_SECRETS, **overrides}.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def test_missing_secret_warns_and_records_unprovisioned(monkeypatch, capsys, tmp_path):
    _set(monkeypatch, TINYASSETS_CANARY_PASSWORD=None)
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert provision.main() == 0
    captured = capsys.readouterr()
    assert "core_canary_provisioned=0" in captured.out
    assert "::warning::core canary is not provisioned" in captured.err
    assert "TINYASSETS_CANARY_PASSWORD" in captured.err
    assert "secret-value" not in captured.err + captured.out
    assert output.read_text(encoding="utf-8") == "provisioned=0\n"


def test_failed_setup_names_the_step_not_the_secret(monkeypatch, capsys, tmp_path):
    _set(monkeypatch)

    async def refused(config):
        raise setup.ProvisionError("no free model is offered by the canary's connected source")

    monkeypatch.setattr(provision, "_provision", refused)
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert provision.main() == 0
    captured = capsys.readouterr()
    assert "no free model" in captured.err
    assert "secret-value" not in captured.err + captured.out
    assert "hmac-secret" not in captured.err + captured.out
    assert "door-secret" not in captured.err + captured.out
    assert output.read_text(encoding="utf-8") == "provisioned=0\n"


def test_unexpected_error_prints_only_its_type(monkeypatch, capsys):
    _set(monkeypatch)

    async def crashed(config):
        raise RuntimeError("body with secret-value")

    monkeypatch.setattr(provision, "_provision", crashed)
    assert provision.main() == 0
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err
    assert "secret-value" not in captured.err


def test_ready_canary_records_provisioned(monkeypatch, capsys, tmp_path):
    _set(monkeypatch)
    seen = []

    async def ready(config):
        seen.append(config)

    monkeypatch.setattr(provision, "_provision", ready)
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert provision.main() == 0
    captured = capsys.readouterr()
    assert "core_canary_provisioned=1" in captured.out
    assert "secret-value" not in captured.out
    assert output.read_text(encoding="utf-8") == "provisioned=1\n"
    assert seen[0]["email"] == "canary@example.invalid"


class _Response:
    def __init__(self, document, status=200):
        self.document, self.status_code = document, status

    def json(self):
        return self.document

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http")


class _Owner:
    """The public doors the canary uses, holding one owner's state."""

    def __init__(self, *, saved=None, connections=(), branches=()):
        self.policy = saved
        self.generation = 0 if saved is None else 1
        self.connections = list(connections)
        self.branches = list(branches)
        self.writes = []

    async def get(self, url):
        if url.endswith("/app/models/preferences"):
            return _Response({"generation": self.generation, "policy": self.policy})
        if url.endswith("/app/connections"):
            return _Response({"connections": self.connections})
        raise AssertionError(url)

    async def post(self, url, json):
        assert url.endswith("/app/models/preferences")
        assert json["expected_generation"] == self.generation
        self.writes.append(("preferences", json["policy"]))
        self.policy, self.generation = json["policy"], self.generation + 1
        return _Response({})

    async def call(self, name, **arguments):
        assert arguments.pop("graph_id") == "u-canary"
        target = arguments["target"]
        if name == "read_graph" and target == "model_options":
            # The public door's compact view, searched for free models.
            assert arguments["query"] == ":free"
            return {"view": "compact", "models": [
                _option("api_key_http:or", "paid/model"),
                _option("api_key_http:or", "broken:free", reasons=["stale"]),
                _option("api_key_http:or", "good:free"),
            ]}
        if name == "read_graph" and target == "branches":
            return {"branches": self.branches}
        if name == "write_graph" and target == "connection":
            self.writes.append(("connection", json.loads(arguments["payload_json"])))
            return {"connection_id": "http_new"}
        if name == "write_graph" and target == "branch":
            self.writes.append(("branch", json.loads(arguments["payload_json"])))
            return {"branch_def_id": "b-new"}
        raise AssertionError((name, target))


def _option(provider, model, reasons=()):
    row = {"provider_ref": provider, "model_id": model, "selectable": True, "tools": True}
    if reasons:
        row["reasons"] = list(reasons)
    return row


def _run(owner, monkeypatch, url="https://sign.example.invalid"):
    if url is None:
        monkeypatch.delenv("TINYASSETS_CANARY_SERVICE_URL", raising=False)
    else:
        monkeypatch.setenv("TINYASSETS_CANARY_SERVICE_URL", url)
    config = {"universe_id": "u-canary"}
    return asyncio.run(setup.provision(config, owner, "https://origin", owner.call))


def test_a_new_canary_gets_a_free_default_connection_branch_and_intake(monkeypatch):
    owner = _Owner()
    config = _run(owner, monkeypatch)
    assert config["default_model"] == {"provider_ref": "api_key_http:or", "model_id": "good:free"}
    assert config["connection_id"] == "http_new"
    assert config["branch_id"] == "b-new"
    assert "intake_receiver_id" not in config
    assert "provision_warnings" not in config
    assert config["service_path"] == "/sign"
    assert [kind for kind, _ in owner.writes] == ["preferences", "connection", "branch"]
    connection = owner.writes[1][1]
    assert connection["destination"] == "canary-hmac"
    assert connection["allowed_endpoints"] == [
        {"host": "sign.example.invalid", "path_template": "/sign", "methods": ["POST"]}
    ]


def test_a_ready_canary_is_left_exactly_as_it_is(monkeypatch):
    good = {"provider_ref": "api_key_http:or", "model_id": "good:free"}
    owner = _Owner(
        saved={"version": 2, "mode": "explicit", "saved_default": good,
               "fallbacks": [], "efforts": []},
        connections=[{"destination": "canary-hmac", "connection_id": "http_old"}],
        branches=[{"name": "canary-echo", "branch_def_id": "b-old"}],
    )
    config = _run(owner, monkeypatch, url=None)
    assert owner.writes == []
    assert (config["connection_id"], config["branch_id"]) == ("http_old", "b-old")


def test_a_failing_default_is_replaced_with_a_free_one(monkeypatch):
    groq = {"provider_ref": "api_key_http:groq", "model_id": "gpt-oss-120b"}
    owner = _Owner(saved={"version": 2, "mode": "explicit", "saved_default": groq,
                          "fallbacks": [], "efforts": []})
    _run(owner, monkeypatch)
    assert owner.writes[0][1]["saved_default"]["model_id"] == "good:free"


def test_no_free_model_or_a_refused_save_is_a_warning_not_unready(monkeypatch):
    owner = _Owner()

    async def no_free(name, **arguments):
        if arguments.get("target") == "model_options":
            return {"view": "compact", "models": [_option("api_key_http:or", "paid/model")]}
        return await owner.call(name, **arguments)

    monkeypatch.setenv("TINYASSETS_CANARY_SERVICE_URL", "https://sign.example.invalid")
    config = asyncio.run(setup.provision({"universe_id": "u-canary"}, owner, "https://o", no_free))
    assert config["default_model"] is None
    assert config["provision_warnings"] == ["no_free_model_offered"]
    assert config["branch_id"] == "b-new"


def test_no_service_url_and_no_connection_is_not_ready(monkeypatch):
    with pytest.raises(setup.ProvisionError, match="TINYASSETS_CANARY_SERVICE_URL"):
        _run(_Owner(), monkeypatch, url=None)


def test_missing_home_id_is_not_provisioned(monkeypatch, capsys):
    _set(monkeypatch, TINYASSETS_CANARY_UNIVERSE_ID=None)
    assert provision.main() == 0
    assert "TINYASSETS_CANARY_UNIVERSE_ID" in capsys.readouterr().err


class _Door:
    """The canary door and /app/me, as the probe sees them."""

    def __init__(self, *, status=200, body=None, home="u-canary"):
        self.status, self.body, self.home = status, body, home
        self.headers, self.posts = {}, []

    async def post(self, url, json):
        self.posts.append((url, json))
        document = {"access_token": "canary-session"} if self.body is None else self.body
        return _Response(document, self.status)

    async def get(self, url):
        assert url.endswith("/app/me")
        assert self.headers["Authorization"] == "Bearer canary-session"
        return _Response({"universe_id": self.home})


def _login(door, monkeypatch, **overrides):
    _set(monkeypatch)
    from scripts import core_capability_live as live

    config = {**setup.credentials(), **overrides}
    return asyncio.run(live.login(config, door))


def test_login_presents_the_door_key_and_the_password_and_keeps_the_home_check(monkeypatch):
    door = _Door()
    assert _login(door, monkeypatch) == "canary-session"
    url, body = door.posts[0]
    assert url == "https://tinyassets.io/app/canary-sign-in"
    assert body == {
        "key": "door-secret", "email": "canary@example.invalid", "password": "secret-value",
    }
    assert door.headers["Authorization"] == "Bearer canary-session"


def test_a_refused_door_names_the_status_and_code_only(monkeypatch):
    from scripts import core_capability_live as live

    door = _Door(status=403, body={"error": "not_the_canary_account"})
    with pytest.raises(live.ProbeFailure) as refusal:
        _login(door, monkeypatch)
    message = str(refusal.value)
    assert "403" in message and "not_the_canary_account" in message
    assert "secret-value" not in message and "door-secret" not in message


def test_another_home_stops_the_probe_before_it_repairs_anything(monkeypatch):
    from scripts import core_capability_live as live

    with pytest.raises(live.ProbeFailure, match="dedicated canary home"):
        _login(_Door(home="u-someone-else"), monkeypatch)


def test_verification_calls_the_real_provisioning_pass(monkeypatch):
    """The pre-deploy check must reach provision(), not a name the probe lacks."""
    _set(monkeypatch)
    from scripts import core_capability_live as live

    reached = []

    async def login(config, http):
        return "token"

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def provision_pass(config, http, origin, call):
        reached.append(config["universe_id"])

    monkeypatch.setattr(live, "login", login)
    monkeypatch.setattr(live, "stream_transport", lambda token, observed: None)
    monkeypatch.setattr("fastmcp.Client", _Client)
    monkeypatch.setattr(provision, "provision", provision_pass)
    assert provision.classify() == (True, "")
    assert reached == ["u-canary"]
