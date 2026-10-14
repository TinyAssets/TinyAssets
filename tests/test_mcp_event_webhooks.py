import pytest

from tinyassets.mcp_event_webhooks import post
from tinyassets.storage import outbound_connections as outbound


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "fc00::1", "::ffff:127.0.0.1", "0.0.0.0"],
)
def test_every_dns_answer_must_be_public(monkeypatch, address):
    monkeypatch.setattr(
        outbound, "_make_default_resolver", lambda timeout: lambda host, port: ["8.8.8.8", address]
    )
    monkeypatch.setattr(
        outbound,
        "_PinnedHTTPSConnection",
        lambda *a, **k: pytest.fail("unsafe DNS reached the network"),
    )
    with pytest.raises(outbound.SsrfValidationError):
        post("https://receiver.example/callback", b"{}", {})


def test_rebinding_rechecked_and_redirect_never_followed(monkeypatch):
    dns, connections = [["8.8.8.8"]], []
    monkeypatch.setattr(
        outbound, "_make_default_resolver", lambda timeout: lambda host, port: dns[0]
    )

    class Connection:
        def __init__(self, host, **kwargs):
            connections.append((host, kwargs["pinned_address"]))

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return self

        status = 302

        def read(self, size):
            return b"{}"

        def close(self):
            pass

    monkeypatch.setattr(outbound, "_PinnedHTTPSConnection", Connection)
    assert post("https://receiver.example/callback", b"{}", {}) == (302, b"{}")
    assert connections == [("receiver.example", "8.8.8.8")]
    dns[0] = ["127.0.0.1"]
    with pytest.raises(outbound.SsrfValidationError):
        post("https://receiver.example/callback", b"{}", {})
    assert len(connections) == 1
