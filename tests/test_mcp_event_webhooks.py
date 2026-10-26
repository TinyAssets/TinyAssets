"""Callbacks leave through the owner's broker egress: public addresses only, owner policy."""

from types import SimpleNamespace

import pytest

from tinyassets.broker import egress
from tinyassets.broker.mcp_events import egress_sender
from tinyassets.storage import outbound_connections as outbound


class Policy:
    def __init__(self, mode, destinations=()):
        self.document = {"egress": {"mode": mode, "destinations": list(destinations)}}
        self.generation = 1
        self.receipts = []

    def read(self, owner):
        return self

    def receipt_egress(self, owner, generation, target, decision):
        self.receipts.append((owner, target, decision))


@pytest.fixture
def owner_egress(monkeypatch):
    """The real ``broker.egress.destination`` for an admitted owner under a policy."""
    admitted = []
    monkeypatch.setattr(egress, "require_admitted", lambda identities, owner: admitted.append(
        owner))

    def sender(policy):
        return egress_sender(lambda owner: (SimpleNamespace(), owner, policy, None))

    return sender, admitted


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "fc00::1", "::ffff:127.0.0.1", "0.0.0.0"],
)
def test_every_dns_answer_must_be_public(monkeypatch, owner_egress, address):
    sender, admitted = owner_egress
    monkeypatch.setattr(
        outbound, "_make_default_resolver", lambda timeout: lambda host, port: ["8.8.8.8", address]
    )
    monkeypatch.setattr(
        outbound,
        "_PinnedHTTPSConnection",
        lambda *a, **k: pytest.fail("unsafe DNS reached the network"),
    )
    policy = Policy("open")
    with pytest.raises(outbound.SsrfValidationError):
        sender(policy)("alice", "https://receiver.example/callback", b"{}", {})
    assert admitted == ["alice"]
    assert policy.receipts[-1][2] == "deny_public_address"


class Connection:
    opened = []

    def __init__(self, host, **kwargs):
        self.opened.append((host, kwargs["pinned_address"]))

    def request(self, *args, **kwargs):
        pass

    def getresponse(self):
        return self

    status = 302

    def read(self, size):
        return b"{}"

    def close(self):
        pass


def test_rebinding_rechecked_and_redirect_never_followed(monkeypatch, owner_egress):
    sender, _ = owner_egress
    dns = [["8.8.8.8"]]
    Connection.opened = []
    monkeypatch.setattr(
        outbound, "_make_default_resolver", lambda timeout: lambda host, port: dns[0]
    )
    monkeypatch.setattr(outbound, "_PinnedHTTPSConnection", Connection)
    send = sender(Policy("open"))
    assert send("alice", "https://receiver.example/callback", b"{}", {}) == (302, b"{}")
    assert Connection.opened == [("receiver.example", "8.8.8.8")]
    dns[0] = ["127.0.0.1"]
    with pytest.raises(outbound.SsrfValidationError):
        send("alice", "https://receiver.example/callback", b"{}", {})
    assert len(Connection.opened) == 1


@pytest.mark.parametrize("mode", ["allowlist", "ask"])
def test_owner_egress_policy_applies_to_callbacks(monkeypatch, owner_egress, mode):
    sender, _ = owner_egress
    Connection.opened = []
    monkeypatch.setattr(
        outbound, "_make_default_resolver", lambda timeout: lambda host, port: ["8.8.8.8"]
    )
    monkeypatch.setattr(outbound, "_PinnedHTTPSConnection", Connection)
    policy = Policy(mode)
    with pytest.raises(egress.EgressPolicyRefused):
        sender(policy)("alice", "https://receiver.example/callback", b"{}", {})
    assert Connection.opened == []
    allowed = Policy("allowlist", [{"host": "receiver.example", "port": 443}])
    assert sender(allowed)("alice", "https://receiver.example/callback", b"{}", {})[0] == 302
