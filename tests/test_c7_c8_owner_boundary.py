"""C7/C8 real broker transport and box-local decision boundaries."""
# ruff: noqa: F811
import pytest

from tests.support.admission import drive, projection
from tests.test_http_redirect_chain import _driver, chain  # noqa: F401
from tests.test_owner_broker_egress import box, broker  # noqa: F401
from tinyassets.broker.admission import local_mutate
from tinyassets.storage import outbound_connections as oc


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
