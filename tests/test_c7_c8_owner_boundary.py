"""C7/C8 real broker transport and box-local decision boundaries."""
# ruff: noqa: F811
import pytest

from tests.support.admission import drive, projection
from tests.test_box_git_bridge import git_server  # noqa: F401
from tests.test_http_redirect_chain import _driver, chain  # noqa: F401
from tests.test_owner_broker_egress import box, broker  # noqa: F401
from tinyassets.broker.admission import local_mutate
from tinyassets.storage import outbound_connections as oc
from tinyassets.storage.outbound_connections import _classify_global_address


@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize("verb", ["GET", "POST"])
@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
def test_model_and_connector_real_broker_refuse_private_before_dial(box, chain, monkeypatch,
                                                                   verb, address):
    def dispatch_for(owner, center, grant, resource):
        driver = _driver(chain, resolver=lambda host, port: [address],
                         validator=oc._classify_global_address,
                         open_socket=lambda *args: pytest.fail("private destination dialed"))
        network = oc._TrustedNetworkDriver(
            {"allow_test_fixtures": False, "allow_http_connections": True}, box.shared)
        network._http = driver
        return oc.CredentialBlindBroker(box.ledger, resolve_credential=lambda *args: "test-secret",
                                        network_request=network).dispatch

    box.broker.server._dispatch_for = dispatch_for
    channel = oc._broker_channel(box.root, principal="owner", command_center="center",
                                 grant_id="grant-model", connection_id="conn-model")
    with pytest.raises(oc.ProxyRequestError):
        channel.request(verb, {"url": "https://models.example.com/v1/chat"})
    assert chain.state["sockets"] == []
    assert chain.state["requests"] == []


@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize("host", ["models.example.com", "another.example.com"])
def test_public_broker_destination_uses_only_its_own_credential(box, chain, host):
    resolved = []

    def dispatch_for(owner, center, grant, resource):
        network = oc._TrustedNetworkDriver(
            {"allow_test_fixtures": False, "allow_http_connections": True}, box.shared)
        network._http = _driver(chain)

        def credential(*args):
            resolved.append(True)
            return "scoped-test-secret"

        return oc.CredentialBlindBroker(box.ledger, resolve_credential=credential,
                                        network_request=network).dispatch

    box.broker.server._dispatch_for = dispatch_for
    chain.state["responses"] = [{"body": b"public response"}]
    channel = oc._broker_channel(box.root, principal="owner", command_center="center",
                                 grant_id="grant-model", connection_id="conn-model")
    result = channel.request("GET", {"url": f"https://{host}/v1/chat"})
    assert result["status"] == 200
    assert result["body"] == "public response"
    authorized = host == "models.example.com"
    assert resolved == ([True] if authorized else [])
    assert chain.state["requests"][0]["headers"].get("Authorization") == (
        "Bearer scoped-test-secret" if authorized else None)
    drive(local_mutate(box.identities.path, "owner", "erase",
                       routing=projection(box.identities.path)))
    with pytest.raises((PermissionError, oc.ProxyRequestError)):
        channel.request("GET", {"url": f"https://{host}/v1/chat"})
    assert len(chain.state["requests"]) == 1


@pytest.mark.verifies('owner-daemon-in-box:C8')
def test_broker_neither_stores_nor_decides_owner_actions(box):
    store = box.broker.server._policy_store
    document = store.read("owner").document
    assert set(document) == {"entries", "spend"}
    for action in ("workspace.files", "workspace.shell", "workspace.workflows", "app.read",
                   "app.write"):
        with pytest.raises(PermissionError, match="spending action required"):
            store.decide("owner", center="center", action_class=action)
        with pytest.raises(PermissionError, match="invalid approval rule"):
            store._validate("owner", {**document, "entries": [{"scope": "account",
                "table": "rules", "values": {"action_class": action, "behaviour": "do"}}]})


@pytest.mark.verifies('owner-daemon-in-box:C8')
@pytest.mark.parametrize("missing", ["row", "file"])
def test_missing_local_policy_is_never_recreated_by_a_decision(box, missing):
    import sqlite3

    from tinyassets.action_policy import ActionPolicyStore

    path = box.root / ".action-policy.db"
    if missing == "row":
        with sqlite3.connect(path) as db:
            db.execute("DELETE FROM policy")
    else:
        path.unlink()
    store = ActionPolicyStore(box.root / "center")
    with pytest.raises((PermissionError, sqlite3.OperationalError)):
        store.decide("owner", center="center", action_class="workspace.shell")
    assert path.exists() == (missing == "row")


def _git_request(server, host="git.test"):
    from tinyassets.broker.git_client import exchange

    chunks, heads = [], []
    exchange(server.client, grant_id="grant", connection_id="git", verb="git_read:owner/repo",
             request={"host": host, "method": "GET",
                      "target": "/owner/repo.git/info/refs?service=git-upload-pack",
                      "agent": "developer", "incarnation": server.incarnation, "upload": False},
             upload=None, head=heads.append, data=chunks.append)
    return heads, b"".join(chunks)


@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
def test_git_real_broker_refuses_private_before_dial(git_server, monkeypatch, address):
    monkeypatch.setattr(oc, "_classify_global_address", _classify_global_address)
    monkeypatch.setattr(oc, "_make_default_resolver", lambda timeout: lambda h, p: [address])
    monkeypatch.setattr(oc, "_default_open_socket",
                        lambda *args: pytest.fail("private git destination dialed"))
    with pytest.raises(oc.ProxyRequestError):
        _git_request(git_server)
    assert git_server.state["seen"] == []


@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize("host", ["git.test", "another.example.com"])
def test_git_public_destination_scopes_credential_to_service(git_server, chain, monkeypatch, host):
    import socket

    def open_local(address, timeout, source):
        return socket.create_connection(  # hermetic-ok: this test's loopback HTTP listener
            ("127.0.0.1", chain.server_port), timeout)

    monkeypatch.setattr(oc, "_default_open_socket", open_local)
    chain.state["responses"] = [{"body": b"public git response"}]
    heads, body = _git_request(git_server, host)
    assert len(heads) == 1
    assert body == b"public git response"
    assert chain.state["requests"][0]["headers"].get("Authorization") == (
        "Bearer " + git_server.secret if host == "git.test" else None)


@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize("missing", ["operation", "credential"])
def test_public_operation_without_credential_authority_uses_no_secret(box, chain, missing):
    resolved = []
    with box.ledger._connect() as db:
        db.execute("UPDATE outbound_connections SET scopes_json='[\"POST\"]'")

    def dispatch_for(owner, center, grant, resource):
        network = oc._TrustedNetworkDriver(
            {"allow_test_fixtures": False, "allow_http_connections": True}, box.shared)
        network._http = _driver(chain)
        from tinyassets.credential_vault import custody_namespace

        resolver = oc._GeneralVaultCredentialResolver(
            universe_dir=custody_namespace(box.shared, owner))

        def credential(*args):
            resolved.append(True)
            return resolver("vault://http/absent-record")

        return oc.CredentialBlindBroker(box.ledger, resolve_credential=credential,
                                        network_request=network).dispatch

    box.broker.server._dispatch_for = dispatch_for
    chain.state["responses"] = [{"body": b"public response"}]
    channel = oc._broker_channel(box.root, principal="owner", command_center="center",
                                 grant_id="grant-model", connection_id="conn-model")
    proxy = oc.ScopedConnectionProxy(grant_id="grant-model", provider="http",
        destination="model", scopes=("POST",), _channel=channel)
    result = proxy.request("GET" if missing == "operation" else "POST",
                           {"url": "https://models.example.com/v1/chat"})
    assert result["status"] == 200
    assert result["body"] == "public response"
    assert resolved == ([] if missing == "operation" else [True])
    assert "Authorization" not in chain.state["requests"][0]["headers"]


@pytest.mark.verifies('owner-daemon-in-box:C1')
def test_policy_initialization_releases_the_volume_handle(tmp_path, monkeypatch):
    import sqlite3

    from tinyassets.action_policy import initialize

    opened = []
    connect = sqlite3.connect

    def track(*args, **kwargs):
        connection = connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", track)
    initialize(tmp_path / ".action-policy.db")
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="Cannot operate on a closed database"):
        opened[0].execute("SELECT 1")
