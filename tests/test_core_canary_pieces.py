"""The core canary's HMAC door, recording intake, and echo branch.

These run the real handlers the live probe calls. No GitHub secret is required:
a broken piece here rolls a deploy back once that secret exists.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest

from scripts import verify_core_canary_provision as provision
from tinyassets.core_canary import (
    CONFIG_FIELDS,
    ECHO_BRANCH_ID,
    ECHO_SOURCE,
    INTAKE_RECEIVER_ID,
    RECORD_SOURCE,
    SERVICE_PATH,
    CoreCanaryProvisionError,
    CoreCanaryRecordError,
    _read_founder_secret,
    install,
    load_record,
    main,
    offered_intake,
    probe_config,
    service_hmac_key,
)
from tinyassets.daemon_server import (
    get_branch_definition,
    grant_universe_access,
    initialize_author_server,
)
from tinyassets.patch_intake import configured_intake, consent_is_active, request_payload


def _tree_text(path: Path) -> str:
    parts: list[str] = []
    if not path.exists():
        return ""
    for file in path.rglob("*"):
        if not file.is_file() or file.is_symlink():
            continue
        parts.append(file.read_bytes().decode("utf-8", "ignore"))
    return "\n".join(parts)


@pytest.fixture
def root(tmp_path, monkeypatch):
    base = tmp_path / "data"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    initialize_author_server(base)
    return base


def _prepare(base, owner, universe):
    (base / universe).mkdir(exist_ok=True)
    grant_universe_access(
        base,
        universe_id=universe,
        actor_id=owner,
        permission="admin",
        granted_by=owner,
    )


def _install(
    base,
    owner="user_01TESTCANARY",
    universe="u-canary",
    email="canary@example.invalid",
    password="pw-canary",
):
    _prepare(base, owner, universe)
    return install(
        base,
        owner_id=owner,
        universe_id=universe,
        email=email,
        password=password,
    )


def test_config_fields_match_the_deploy_verifier():
    assert CONFIG_FIELDS == provision.REQUIRED


def test_install_is_idempotent_and_keeps_secrets_out_of_the_universe(root, monkeypatch):
    from tinyassets.api import http_connection
    from tinyassets.credential_vault import load_credential_vault

    seen: list[dict] = []
    real = http_connection.connect_http

    def _wrap(**kwargs):
        result = real(**kwargs)
        seen.append(result)
        return result

    monkeypatch.setattr(http_connection, "connect_http", _wrap)
    first = _install(root)
    assert seen and seen[0]["status"] == "provisioned"
    projected = json.dumps(seen[0])
    assert first["hmac_key"] not in projected
    assert first["bearer"] not in projected
    assert first["branch_id"] == ECHO_BRANCH_ID
    assert first["service_path"] == SERVICE_PATH
    record_path = root / ".runtime" / "core-canary.json"
    assert record_path.stat().st_mode & 0o777 == 0o600
    assert record_path.parent.stat().st_mode & 0o007 == 0
    universe = _tree_text(root / "u-canary")
    assert first["hmac_key"] not in universe
    assert first["bearer"] in universe
    vault = load_credential_vault(root / "u-canary")
    assert any(row.get("token") == first["bearer"] for row in vault)
    assert all(row.get("token") != first["hmac_key"] for row in vault)
    branch = get_branch_definition(root, branch_def_id=ECHO_BRANCH_ID)
    assert branch["visibility"] == "private"
    encoded = json.dumps(branch)
    assert "source_code" in encoded
    assert first["hmac_key"] not in encoded
    assert first["bearer"] not in encoded
    source = branch["node_defs"][0]["source_code"]
    assert source == ECHO_SOURCE
    record_branch = get_branch_definition(root, branch_def_id="core-canary-record")
    recorded = record_branch["node_defs"][0]["source_code"]
    assert recorded == RECORD_SOURCE
    assert "file_bug" not in recorded
    assert "github" not in recorded.lower()
    second = _install(root)
    assert second["hmac_key"] == first["hmac_key"]
    assert second["bearer"] == first["bearer"]
    assert second["connection_id"] == first["connection_id"]
    assert second["intake_receiver_id"] == first["intake_receiver_id"]
    assert second["branch_id"] == first["branch_id"]
    with pytest.raises(CoreCanaryRecordError, match="different account"):
        install(
            root,
            owner_id="user_01OTHERACCOUNT",
            universe_id="u-canary",
            email="other@example.invalid",
            password="pw-canary",
        )
    assert load_record(root)["hmac_key"] == first["hmac_key"]
    derived = probe_config("canary@example.invalid", "pw-canary", "u-canary")
    assert derived["service_hmac_key"] == first["hmac_key"]
    assert derived["connection_id"] == first["connection_id"]
    assert derived["intake_receiver_id"] == first["intake_receiver_id"] == INTAKE_RECEIVER_ID
    assert derived["branch_id"] == ECHO_BRANCH_ID
    assert derived["service_path"] == SERVICE_PATH
    assert first["hmac_key"] == service_hmac_key("canary@example.invalid", "pw-canary")
    founder = {"email": "canary@example.invalid", "password": "pw-canary"}
    assert set(founder) == set(provision.REQUIRED) == set(CONFIG_FIELDS)
    assert provision.classify(json.dumps(founder)) == (True, "")
    assert "pw-canary" not in _tree_text(root)


def test_stored_sources_echo_and_record(root):
    from tinyassets.node_sandbox import NodeSandbox

    record = _install(root)
    echo = get_branch_definition(root, branch_def_id=ECHO_BRANCH_ID)
    nonce = "nonce-7f3a"
    ran = NodeSandbox(timeout=30).run_sync(
        node_id="entry",
        source_code=echo["node_defs"][0]["source_code"],
        input_state={"nonce": nonce},
        input_keys=["nonce"],
        output_keys=["echo"],
    )
    assert ran.success, ran
    assert ran.output_state["echo"] == nonce
    recorded = get_branch_definition(root, branch_def_id="core-canary-record")
    report = "Title\n\nDetails"
    ran = NodeSandbox(timeout=30).run_sync(
        node_id="entry",
        source_code=recorded["node_defs"][0]["source_code"],
        input_state={"report": report},
        input_keys=["report"],
        output_keys=["recorded"],
    )
    assert ran.success, ran
    assert ran.output_state["recorded"] == report
    assert record["hmac_key"] not in echo["node_defs"][0]["source_code"]


pytest_plugins = ["tests.cloud_runtime_fixture"]


def test_hmac_endpoint_matches_the_probe_formula(root, caplog, cloud_runtime):
    import logging

    from starlette.testclient import TestClient

    from tinyassets.universe_server import create_streamable_http_app

    record = _install(root)
    nonce = "probe-nonce"
    expected = hmac.new(
        record["hmac_key"].encode(),
        nonce.encode(),
        hashlib.sha256,
    ).hexdigest()
    caplog.set_level(logging.DEBUG)
    with TestClient(create_streamable_http_app()) as client:
        response = client.post(
            SERVICE_PATH,
            headers={"Authorization": "Bearer " + record["bearer"]},
            json={"nonce": nonce},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"hmac": expected}
        assert response.headers["cache-control"] == "no-store"
        assert hmac.compare_digest(response.json()["hmac"], expected)
        body = response.text
        assert record["hmac_key"] not in body
        assert record["bearer"] not in body
        refused = client.post(
            SERVICE_PATH,
            headers={"Authorization": "Bearer " + ("ab" * 32)},
            json={"nonce": nonce},
        )
        assert refused.status_code == 401
        assert refused.json() == {"error": "unauthorized"}
        missing = client.post(SERVICE_PATH, json={"nonce": nonce})
        assert missing.status_code == 401
        for payload in ({}, {"nonce": ""}, {"nonce": 1}, {"nonce": "x" * 257}):
            bad = client.post(
                SERVICE_PATH,
                headers={"Authorization": "Bearer " + record["bearer"]},
                json=payload,
            )
            assert bad.status_code == 400
            assert bad.json() == {"error": "nonce_required"}
        broken = client.post(
            SERVICE_PATH,
            headers={
                "Authorization": "Bearer " + record["bearer"],
                "Content-Type": "application/json",
            },
            content=b"not-json",
        )
        assert broken.status_code == 400
        assert broken.json() == {"error": "invalid_json"}
        huge = client.post(
            SERVICE_PATH,
            headers={"Authorization": "Bearer " + record["bearer"]},
            content=b'{"nonce":"' + (b"n" * 5000) + b'"}',
        )
        assert huge.status_code == 413
        assert huge.json() == {"error": "too_large"}
        other = client.get(SERVICE_PATH)
        assert other.status_code != 200 or "hmac" not in other.text
        (root / ".runtime" / "core-canary.json").unlink()
        gone = client.post(
            SERVICE_PATH,
            headers={"Authorization": "Bearer " + record["bearer"]},
            json={"nonce": nonce},
        )
        assert gone.status_code == 401
        assert gone.json() == {"error": "unauthorized"}
    assert record["hmac_key"] not in caplog.text
    assert record["bearer"] not in caplog.text


def _production_receiver(base, auth):
    from tinyassets.api import receiver_links
    from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition
    from tinyassets.daemon_server import save_branch_definition

    owner = "prod-owner"
    _prepare(base, owner, "u-prod")
    auth(owner)
    branch = BranchDefinition(
        branch_def_id="prod-intake",
        name="Production intake",
        author=owner,
        visibility="private",
        entry_point="entry",
        node_defs=[
            NodeDefinition(
                node_id="entry",
                display_name="File production report",
                input_keys=["topic"],
                output_keys=["filed"],
                source_code='def run(state):\n    return {"filed": state["topic"]}\n',
            )
        ],
        graph_nodes=[GraphNodeRef(id="entry", node_def_id="entry")],
        edges=[EdgeDefinition("START", "entry"), EdgeDefinition("entry", "END")],
        state_schema=[
            {"name": "topic", "type": "str"},
            {"name": "filed", "type": "str"},
        ],
    )
    save_branch_definition(base, branch_def=branch.to_dict())
    return receiver_links.save_receiver(
        universe_id="u-prod",
        branch_def_id="prod-intake",
        node_id="entry",
        input_keys=["topic"],
        allowed_senders=["other-owner", "user_01TESTCANARY"],
        open_to_all=False,
        discoverable=False,
    )


def _rows(base):
    from tinyassets.storage import deliveries

    with deliveries.transaction(base) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM graph_deliveries")]


def _send():
    from tinyassets import engine_mcp_server as engine

    return json.loads(
        engine.write_graph(
            target="patch_request",
            operation="send",
            payload_json=json.dumps(
                {
                    "title": "Canary gap",
                    "details": "The probe tried this.",
                }
            ),
        )
    )


def test_canary_patches_are_recorded_and_other_owners_keep_production(
    root,
    monkeypatch,
    authenticate_request,
):
    from tests.engine_authority_helpers import mock_engine_admission
    from tinyassets import engine_mcp_server as engine
    from tinyassets.api.pending_requests import request_from_user
    from tinyassets.patch_intake import grant_send_consent

    record = _install(root)
    production = _production_receiver(root, authenticate_request)
    monkeypatch.setenv("TINYASSETS_PATCH_INTAKE_RECEIVER_ID", production["receiver_id"])
    monkeypatch.setattr(
        "tinyassets.delivery_runtime.dispatch_accepted_delivery",
        lambda *a, **k: None,
    )

    def _file_bug(*_args, **_kwargs):
        raise AssertionError("file_bug must not run")

    monkeypatch.setattr("tinyassets.api.wiki._wiki_file_bug", _file_bug)
    assert configured_intake("u-canary")["receiver_id"] == record["intake_receiver_id"]
    assert configured_intake("u-other")["receiver_id"] == production["receiver_id"]
    assert offered_intake("u-other") is None
    authenticate_request("user_01TESTCANARY")
    ask = request_from_user(
        universe_id="u-canary",
        origin="platform",
        payload=json.dumps(request_payload(configured_intake("u-canary"))),
    )
    assert ask.get("request_id"), ask
    from tests.test_inline_owner_sessions import request as owner_request
    from tinyassets import onboarding
    from tinyassets.api.pending_requests import _answer_request
    from tinyassets.onboarding import owner_sessions as sessions

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setattr(
        onboarding,
        "app_config",
        lambda: {
            "configured": True,
            "resource": "https://tinyassets.io/mcp",
            "client_id": "client",
            "scopes": "openid profile",
            "authorization_endpoint": "https://identity.example/authorize",
            "token_endpoint": "https://identity.example/token",
        },
    )
    with sessions.store() as conn:
        conn.execute(
            "INSERT INTO owner_sessions VALUES (?,?,?)",
            (
                sessions.hashed("protected"),
                json.dumps({"user_id": "user_01TESTCANARY"}),
                time.time() + 300,
            ),
        )
    session = sessions.require(owner_request(), owner="user_01TESTCANARY")
    answered = _answer_request(
        universe_id="u-canary",
        payload={"request_id": ask["request_id"], "values": {}},
        owner_session=session,
    )
    assert answered.get("status") == "answered", answered
    assert answered.get("decision") == "allowed"
    assert consent_is_active(root / "u-canary", record["intake_receiver_id"])
    monkeypatch.setattr(engine, "_ACTOR_ID", "user_01TESTCANARY")
    monkeypatch.setattr(engine, "_GRAPH_ID", "u-canary")
    mock_engine_admission(monkeypatch, {"u-canary", "u-other"})
    started = time.time()
    sent = _send()
    assert sent.get("sent") is True, sent
    rows = _rows(root)
    assert len(rows) == 1
    assert rows[0]["receiver_id"] == record["intake_receiver_id"]
    assert rows[0]["receiver_id"] != production["receiver_id"]
    assert rows[0]["accepted_at"] >= started - 5
    inputs = json.loads(rows[0]["inputs_json"])
    assert "Canary gap" in json.dumps(inputs)
    assert "The probe tried this." in json.dumps(inputs)
    _prepare(root, "other-owner", "u-other")
    grant_send_consent(
        root / "u-other",
        receiver_id=production["receiver_id"],
        granted_by="other-owner",
    )
    monkeypatch.setattr(engine, "_ACTOR_ID", "other-owner")
    monkeypatch.setattr(engine, "_GRAPH_ID", "u-other")
    mock_engine_admission(monkeypatch, {"u-canary", "u-other"})
    other = _send()
    assert other.get("sent") is True, other
    landed = [row for row in _rows(root) if row["sender_id"] == "other-owner"]
    assert len(landed) == 1
    assert landed[0]["receiver_id"] == production["receiver_id"]


def test_existing_runtime_directory_keeps_its_mode(root):
    runtime = root / ".runtime"
    runtime.mkdir()
    runtime.chmod(0o710)
    record = _install(root)
    stored = runtime / "core-canary.json"
    text = stored.read_text(encoding="utf-8")
    assert stored.stat().st_mode & 0o777 == 0o600
    assert runtime.stat().st_mode & 0o777 == 0o710
    assert record["hmac_key"] in text
    assert "pw-canary" not in text


def test_a_linked_or_empty_record_is_unreadable(root, tmp_path):
    runtime = root / ".runtime"
    runtime.mkdir(mode=0o700)
    record = runtime / "core-canary.json"
    record.write_bytes(b"")
    with pytest.raises(CoreCanaryRecordError, match="core canary record is unreadable"):
        load_record(root)
    record.unlink()
    other = tmp_path / "elsewhere.json"
    other.write_text("{}", encoding="utf-8")
    record.symlink_to(other)
    with pytest.raises(CoreCanaryRecordError, match="core canary record is unreadable"):
        load_record(root)
    assert other.read_text(encoding="utf-8") == "{}"


def test_corrupt_record_does_not_fall_through_to_production(
    root,
    monkeypatch,
    authenticate_request,
):
    from tests.engine_authority_helpers import mock_engine_admission
    from tinyassets import engine_mcp_server as engine
    from tinyassets.patch_intake import grant_send_consent

    record = _install(root)
    production = _production_receiver(root, authenticate_request)
    monkeypatch.setenv("TINYASSETS_PATCH_INTAKE_RECEIVER_ID", production["receiver_id"])
    monkeypatch.setattr(
        "tinyassets.delivery_runtime.dispatch_accepted_delivery",
        lambda *a, **k: None,
    )
    grant_send_consent(
        root / "u-canary",
        receiver_id=production["receiver_id"],
        granted_by="user_01TESTCANARY",
    )
    (root / ".runtime" / "core-canary.json").write_text(
        '{"hmac_key": "super-secret-key-value"}',
        encoding="utf-8",
    )
    with pytest.raises(CoreCanaryRecordError, match="core canary record is unreadable"):
        configured_intake("u-canary")
    monkeypatch.setattr(engine, "_ACTOR_ID", "user_01TESTCANARY")
    monkeypatch.setattr(engine, "_GRAPH_ID", "u-canary")
    mock_engine_admission(monkeypatch, {"u-canary"})
    sent = _send()
    assert sent.get("error") == "invalid_patch_request", sent
    assert "super-secret-key-value" not in json.dumps(sent)
    assert _rows(root) == []
    assert record["hmac_key"] not in json.dumps(sent)


def _founder_json(password="pw-not-stored-7f3a", email="canary@example.invalid"):
    return json.dumps({"email": email, "password": password})


def test_cli_stores_the_derived_key_and_prints_no_secret(root, monkeypatch, capsys):
    monkeypatch.setattr(
        "tinyassets.core_canary._signed_in_owner",
        lambda email, password: "user_01TESTCANARY",
    )
    monkeypatch.setenv("TINYASSETS_CORE_CANARY_CONFIG", _founder_json())
    assert main([]) == 0
    captured = capsys.readouterr()
    assert captured.out == "core_canary_provisioned=1\n"
    assert "pw-not-stored-7f3a" not in captured.out
    assert "pw-not-stored-7f3a" not in captured.err
    record = load_record(root)
    assert record is not None
    assert record["hmac_key"] == service_hmac_key("canary@example.invalid", "pw-not-stored-7f3a")
    assert record["bearer"] not in captured.out
    assert record["hmac_key"] not in captured.out
    assert record["hmac_key"] not in captured.err
    assert "pw-not-stored-7f3a" not in _tree_text(root)
    assert record["hmac_key"] not in _tree_text(root / record["universe_id"])
    derived = probe_config("Canary@Example.invalid", "pw-not-stored-7f3a", record["universe_id"])
    assert derived["service_hmac_key"] == record["hmac_key"]
    assert derived["connection_id"] == record["connection_id"]


def test_password_change_rotates_only_the_derived_key(root, monkeypatch):
    monkeypatch.setattr(
        "tinyassets.core_canary._signed_in_owner",
        lambda email, password: "user_01TESTCANARY",
    )
    first = _install(root, password="pw-one")
    second = _install(root, password="pw-two")
    assert second["hmac_key"] == service_hmac_key("canary@example.invalid", "pw-two")
    assert second["hmac_key"] != first["hmac_key"]
    assert second["bearer"] == first["bearer"]
    assert second["connection_id"] == first["connection_id"]
    assert second["intake_receiver_id"] == first["intake_receiver_id"]
    assert "pw-one" not in _tree_text(root)
    assert "pw-two" not in _tree_text(root)


def test_missing_workos_user_creates_nothing(root, monkeypatch, capsys):
    def _missing(email, password):
        raise CoreCanaryProvisionError(
            "no WorkOS user for that email; sign up at https://tinyassets.io first"
        )

    monkeypatch.setattr("tinyassets.core_canary._signed_in_owner", _missing)
    monkeypatch.setenv(
        "TINYASSETS_CORE_CANARY_CONFIG",
        _founder_json(email="missing@example.invalid"),
    )
    assert main([]) == 1
    captured = capsys.readouterr()
    assert "sign up at https://tinyassets.io" in captured.err
    assert "pw-not-stored-7f3a" not in captured.err
    assert "pw-not-stored-7f3a" not in captured.out
    assert load_record(root) is None
    assert not (root / ".runtime" / "core-canary.json").exists()


def test_secret_channels(monkeypatch):
    monkeypatch.setenv("TINYASSETS_CORE_CANARY_CONFIG", "from-env")
    assert _read_founder_secret() == "from-env"
    monkeypatch.setenv("TINYASSETS_CORE_CANARY_CONFIG", "")
    assert _read_founder_secret() == ""
    monkeypatch.delenv("TINYASSETS_CORE_CANARY_CONFIG")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert _read_founder_secret() == ""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdin.read", lambda: "from-stdin")
    assert _read_founder_secret() == "from-stdin"


def test_sign_in_uses_the_web_token_endpoint_and_names_no_secret(monkeypatch):
    from types import SimpleNamespace

    import httpx

    from tinyassets.auth.provider import Identity
    from tinyassets.core_canary import _signed_in_owner
    from tinyassets.onboarding.owner_sessions import OwnerSignInError, sign_in_with_password
    from tinyassets.platform_secrets import CHILD_FORBIDDEN_ENV, ENGINE_REQUIRED_SECRET_ENV

    source = Path("tinyassets/core_canary.py").read_text(encoding="utf-8")
    leaked = sorted(
        name for name in (CHILD_FORBIDDEN_ENV | set(ENGINE_REQUIRED_SECRET_ENV)) if name in source
    )
    assert leaked == []
    monkeypatch.setenv("WORKOS_API_KEY", "test-workos-key")
    cfg = {
        "configured": True,
        "client_id": "client_public",
        "token_endpoint": "https://id.example/oauth2/token",
        "resource": "https://tinyassets.io/mcp",
        "scopes": "openid profile email",
    }
    monkeypatch.setattr("tinyassets.onboarding.app_config", lambda: cfg)
    seen: dict = {}

    def _post(url, data=None, headers=None, timeout=0):
        seen["url"] = url
        seen["data"] = dict(data or {})
        seen["headers"] = dict(headers or {})
        return httpx.Response(
            200,
            json={"access_token": "header.payload.sig", "user": {"id": "user_OTHER"}},
        )

    monkeypatch.setattr(httpx, "post", _post)
    identity = Identity(
        user_id="user_01TESTCANARY",
        username="canary@example.invalid",
        metadata={"email": "canary@example.invalid"},
    )
    monkeypatch.setattr(
        "tinyassets.auth.middleware._get_provider",
        lambda: SimpleNamespace(
            resolve_token=lambda token: identity if token == "header.payload.sig" else None
        ),
    )
    assert sign_in_with_password("canary@example.invalid", "pw-not-stored-7f3a").user_id == (
        "user_01TESTCANARY"
    )
    assert seen["url"] == cfg["token_endpoint"]
    assert seen["data"]["grant_type"] == "password"
    assert seen["data"]["client_id"] == "client_public"
    assert seen["data"]["username"] == "canary@example.invalid"
    assert seen["data"]["resource"] == cfg["resource"]
    assert "client_secret" not in seen["data"]
    assert "authorization" not in {name.lower() for name in seen["headers"]}
    assert "test-workos-key" not in json.dumps(seen["data"])

    def _status(code, body=None):
        def _post_status(url, data=None, headers=None, timeout=0):
            return httpx.Response(code, json=body if body is not None else {})

        return _post_status

    monkeypatch.setattr(httpx, "post", _status(401))
    with pytest.raises(OwnerSignInError, match="no_user") as missing:
        sign_in_with_password("canary@example.invalid", "pw-not-stored-7f3a")
    assert "pw-not-stored-7f3a" not in str(missing.value)
    assert "test-workos-key" not in str(missing.value)

    monkeypatch.setattr(httpx, "post", _status(503, {"error": "test-workos-key"}))
    with pytest.raises(OwnerSignInError, match="http_503") as failed:
        sign_in_with_password("canary@example.invalid", "pw-not-stored-7f3a")
    assert "test-workos-key" not in str(failed.value)
    assert "pw-not-stored-7f3a" not in str(failed.value)

    monkeypatch.setattr(httpx, "post", _status(200, {"user": {"id": "user_01TESTCANARY"}}))
    with pytest.raises(OwnerSignInError, match="unexpected"):
        sign_in_with_password("canary@example.invalid", "pw-not-stored-7f3a")

    monkeypatch.setattr("tinyassets.onboarding.app_config", lambda: {"configured": False})
    with pytest.raises(CoreCanaryProvisionError, match="sign-in is not configured") as bare:
        _signed_in_owner("canary@example.invalid", "pw-not-stored-7f3a")
    assert "test-workos-key" not in str(bare.value)
    assert "WORKOS_API_KEY" not in str(bare.value)


def test_empty_password_exits_before_lookup(root, monkeypatch, capsys):
    monkeypatch.setenv(
        "TINYASSETS_CORE_CANARY_CONFIG",
        json.dumps({"email": "canary@example.invalid", "password": ""}),
    )

    def _boom(*_args, **_kwargs):
        raise AssertionError("lookup")

    monkeypatch.setattr("tinyassets.core_canary._signed_in_owner", _boom)
    assert main([]) == 1
    captured = capsys.readouterr()
    assert "missing or empty: password" in captured.err
    assert "canary@example.invalid" not in captured.err
    assert load_record(root) is None


def test_arguments_are_refused_without_echoing_them(capsys):
    assert main(["--password", "pw-not-stored-7f3a"]) == 1
    captured = capsys.readouterr()
    assert "stdin" in captured.err
    assert "pw-not-stored-7f3a" not in captured.err
    assert "pw-not-stored-7f3a" not in captured.out


def test_live_probe_rejects_derived_fields_in_the_secret(monkeypatch):
    import asyncio

    from scripts import core_capability_live as live

    monkeypatch.setenv(
        "TINYASSETS_CORE_CANARY_CONFIG",
        json.dumps(
            {
                "email": "canary@example.invalid",
                "password": "pw-not-stored-7f3a",
                "service_hmac_key": "hmac-secret",
            }
        ),
    )
    report: dict = {}
    asyncio.run(live.run(report))
    assert report.get("configuration_missing") is True
    rendered = json.dumps(report)
    assert "hmac-secret" not in rendered
    assert "pw-not-stored-7f3a" not in rendered
