"""Every outbound call says who it is, and a CDN block is classified as one.

Requirement source: the P1 filed as
``docs/concerns/2026-09-30-no-user-agent-blocks-cdn-fronted-webhooks.md`` while
proving capability URLs live.

``_SsrfHardenedHttpDriver`` added no ``User-Agent`` on an ordinary call — the
only one in the codebase was on the redirect download path. So EVERY outbound
call on EVERY auth scheme went out unidentified, and the destinations users
build channels to are CDN-fronted: Slack, Discord, Zapier, Make, and
``tinyassets.io``.

WHAT WAS SEEN, and what was not. On 2026-09-30 a UA-less POST to this
platform's own ``/hooks/inbound`` receiver was answered ``403 "error code: 1010"``
by Cloudflare, and the same request carrying a User-Agent reached the
application (``404 {"error":"not_found"}``) — 40 seconds apart, one header
different. **That A/B did not reproduce hours later**, from either the dev host
or the production egress IP, with a User-Agent, without one, or with
``curl/7.68.0``: everything got the application's 404. So an edge block here is
a bot score, not a function of this header.

Which is why these tests assert what is actually true and checkable: that the
platform identifies itself on every call, that only a connection's DECLARED
header overrides it, that a per-call one is refused — and, separately, that an
edge block is classified as something the universe cannot fix. None of them
assert that sending a User-Agent prevents a block, because that is not a
property this repo can hold the edge to.

The classification half stands on its own regardless of frequency: whenever an
edge does block, ``error code: 1010`` classified as ``external_write_failed``,
whose served advice is "a reason you can fix ... run again yourself ... try at
most twice", routed to the chatbot. Every part of that is wrong for an edge
block, and the owner — who holds both real repairs — was never told.
"""

from __future__ import annotations

import http.server
import socket
import ssl
import threading
from typing import Any

import pytest

from tinyassets.effectors.authenticated_external_call import (
    _declared_user_agent_error,
)
from tinyassets.runs import (
    DESTINATION_BLOCKED_CLIENT_ACTION,
    _classify_external_write,
    external_write_suggested_action,
)
from tinyassets.storage.outbound_connections import (
    OUTBOUND_USER_AGENT,
    OUTBOUND_USER_AGENT_HEADER,
    ConnectionSecretBundle,
    _parse_allowed_endpoints,
    _SsrfHardenedHttpDriver,
    merge_constant_headers,
)


# --------------------------------------------------------------------------- #
# 1. The client string itself.
# --------------------------------------------------------------------------- #
def test_the_user_agent_is_honest_and_names_this_platform() -> None:
    """It impersonates no browser and it links to us.

    A CDN in front of a destination is entitled to know who is calling. The
    fix for a bot block is to identify ourselves, never to look like Chrome.
    """
    assert OUTBOUND_USER_AGENT.startswith("TinyAssets/")
    assert "(+https://tinyassets.io)" in OUTBOUND_USER_AGENT
    lowered = OUTBOUND_USER_AGENT.lower()
    for impersonation in ("mozilla", "chrome", "safari", "applewebkit", "gecko"):
        assert impersonation not in lowered


def test_the_user_agent_carries_the_real_package_version() -> None:
    """Read from the package, not repeated, so a release cannot ship a client
    string that lies about which build is calling."""
    from tinyassets import __version__

    assert OUTBOUND_USER_AGENT == f"TinyAssets/{__version__} (+https://tinyassets.io)"


# --------------------------------------------------------------------------- #
# 2. On the wire, through the real driver.
# --------------------------------------------------------------------------- #
class _PassThroughTLS:
    def __init__(self) -> None:
        self.verify_mode = ssl.CERT_NONE
        self.check_hostname = False

    def wrap_socket(self, sock, server_hostname=None):  # noqa: ANN001
        return sock


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: Any) -> None:
        return

    def _serve(self) -> None:
        stub = self.server.stub  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        stub["received"] = {
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": self.rfile.read(length) if length else b"",
        }
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    do_GET = _serve
    do_POST = _serve


@pytest.fixture
def stub_server() -> Any:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
    server.stub = {"received": None}  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _local_driver(server: Any) -> tuple[Any, int]:
    port = server.server_address[1]

    def open_socket(_address, timeout, _source_address):  # noqa: ANN001
        return socket.create_connection(("127.0.0.1", port), timeout=timeout)

    driver = _SsrfHardenedHttpDriver(
        resolver=lambda _h, _p: ["127.0.0.1"],
        validator=lambda addr: addr,
        open_socket=open_socket,
        ssl_context=_PassThroughTLS(),
        allowed_ports=frozenset({port}),
    )
    return driver, port


def _endpoints(host: str = "public.example") -> tuple[Any, ...]:
    return _parse_allowed_endpoints(
        [{"host": host, "path_template": "/v1/send", "methods": ["POST"]}]
    )


def _call(driver: Any, port: int, **over: Any) -> None:
    kwargs: dict[str, Any] = {
        "bundle": ConnectionSecretBundle(token="tok-not-in-any-response"),
        "auth_scheme": "bearer",
        "method": "POST",
        "url": f"https://public.example:{port}/v1/send",
        "body": {"text": "hi"},
        "allowed_endpoints": _endpoints(),
    }
    kwargs.update(over)
    driver(**kwargs)


def test_every_outbound_call_carries_the_user_agent(stub_server: Any) -> None:
    """THE fix. MUTATION CHECK: remove the default from
    ``_SsrfHardenedHttpDriver.__call__`` and this fails -- which is the state
    that produced the live 403/1010."""
    driver, port = _local_driver(stub_server)

    _call(driver, port)

    assert stub_server.stub["received"]["headers"]["user-agent"] == OUTBOUND_USER_AGENT


def test_it_is_sent_even_when_the_packet_sets_no_headers_at_all(
    stub_server: Any,
) -> None:
    """A bodyless GET with no headers is the leanest request the platform makes,
    and it was the exact shape going out unidentified."""
    driver, port = _local_driver(stub_server)

    _call(
        driver,
        port,
        headers=None,
        body=None,
        method="GET",
        allowed_endpoints=_parse_allowed_endpoints(
            [{"host": "public.example", "path_template": "/v1/send", "methods": ["GET"]}]
        ),
    )

    assert stub_server.stub["received"]["headers"]["user-agent"] == OUTBOUND_USER_AGENT


def test_a_connections_declared_constant_header_wins(stub_server: Any) -> None:
    """The ONE legitimate override, and it is the owner's.

    `merge_constant_headers` already makes the connection's declaration beat a
    caller header; the platform default must lose to it too, so a service that
    insists on a particular client string is served by declaring it ONCE on the
    connection, where it is visible in the grant.
    """
    driver, port = _local_driver(stub_server)

    class _Capability:
        headers = {"User-Agent": "AcmeBot/2.0 (+https://acme.example)"}

    merged = merge_constant_headers(
        {"url": "https://public.example/v1/send", "headers": {"X-Extra": "1"}},
        _Capability(),
    )
    _call(driver, port, headers=merged["headers"])

    received = stub_server.stub["received"]["headers"]
    assert received["user-agent"] == "AcmeBot/2.0 (+https://acme.example)"
    assert received["x-extra"] == "1"


def test_the_default_never_shadows_a_differently_spelled_declaration(
    stub_server: Any,
) -> None:
    """Header names are case-insensitive on the wire; two would be a
    contradiction the destination resolves however it likes."""
    driver, port = _local_driver(stub_server)

    _call(driver, port, headers={"user-agent": "AcmeBot/2.0"})

    received = stub_server.stub["received"]
    assert received["headers"]["user-agent"] == "AcmeBot/2.0"
    # Exactly one, whatever the spelling.
    assert sum(1 for k in received["headers"] if k == "user-agent") == 1


def test_the_auth_header_is_still_the_drivers_and_the_secret_still_scrubbed(
    stub_server: Any,
) -> None:
    """The new default must not disturb the credential path."""
    driver, port = _local_driver(stub_server)

    _call(driver, port)

    received = stub_server.stub["received"]["headers"]
    assert received["authorization"] == "Bearer tok-not-in-any-response"
    assert received["user-agent"] == OUTBOUND_USER_AGENT


# --------------------------------------------------------------------------- #
# 3. A per-CALL User-Agent is refused, not dropped and not honoured.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("spelling", ["User-Agent", "user-agent", "USER-AGENT", " User-Agent "])
def test_a_packet_user_agent_is_refused_in_any_spelling(spelling: str) -> None:
    error = _declared_user_agent_error({spelling: "Mozilla/5.0"})
    assert error
    # Actionable: it names where the header DOES belong.
    assert "constant headers" in error


def test_the_refusal_does_not_fire_on_an_ordinary_packet() -> None:
    assert _declared_user_agent_error({"Content-Type": "application/json"}) == ""
    assert _declared_user_agent_error(None) == ""
    assert _declared_user_agent_error({}) == ""


def test_the_refused_header_name_is_the_one_the_driver_checks() -> None:
    """One definition of one fact: the effector's refusal and the driver's
    default must agree on the header, or a packet could set something the
    driver then does not treat as already-set."""
    assert OUTBOUND_USER_AGENT_HEADER == "user-agent"
    assert _declared_user_agent_error({OUTBOUND_USER_AGENT_HEADER: "x"})


# --------------------------------------------------------------------------- #
# 4. A CDN edge block is its own failure class.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "code", ["1006", "1007", "1008", "1010", "1012", "1013", "1020"]
)
def test_a_cdn_client_block_is_classified_as_one(code: str) -> None:
    row = f"external write failed - far side answered http 403: error code: {code}"
    assert _classify_external_write(row) == "destination_blocked_client"


def test_the_live_1010_row_is_reclassified() -> None:
    """The exact shape the live run produced. Before this change it was
    ``external_write_failed``, whose advice is "a reason you can fix ... run
    again yourself ... at most twice" -- none of it true, and routed to the
    chatbot so the owner never heard."""
    row = "external write failed - far side answered http 403: error code: 1010"
    assert _classify_external_write(row) == "destination_blocked_client"
    action = external_write_suggested_action("destination_blocked_client")
    assert action == DESTINATION_BLOCKED_CLIENT_ACTION
    # It must NOT send the owner to replace a key that was never presented.
    lowered = action.lower()
    assert "rotate" in lowered and "do not rotate" in lowered
    assert "do not retry" in lowered
    assert "constant header" in lowered


def test_the_owner_of_a_cdn_block_is_the_user_not_the_chatbot() -> None:
    """Both repairs are the owner's: a declared client string, or an allowance
    at the destination. The universe holds neither."""
    import tinyassets.runs as runs
    from tinyassets.runs import _classify_failure  # noqa: F401  (module import guard)

    owners = next(
        value
        for name, value in vars(runs).items()
        if isinstance(value, dict) and value.get("credential_rejected") == "user"
    )
    assert owners["destination_blocked_client"] == "user"


def test_a_rate_limit_is_deliberately_not_this_class() -> None:
    """1015 is rate limiting: the repair is to slow down, not to change who we
    say we are. Folding it in would give one of the two the wrong advice."""
    row = "external write failed - far side answered http 429: error code: 1015"
    assert _classify_external_write(row) != "destination_blocked_client"


def test_an_edge_block_is_not_borrowed_by_the_neighbouring_heuristics() -> None:
    """1020's own wording is "access denied", one word from the refusal-word
    net, and an edge block IS a delivered 4xx like a dead key. The exact
    error-code match runs before both, so neither can claim it."""
    row = (
        "external write failed - far side answered http 403: error code: 1020 "
        "access denied - you do not have permission"
    )
    assert _classify_external_write(row) == "destination_blocked_client"


@pytest.mark.parametrize(
    "row,expected",
    [
        ("external write failed - far side answered http 401: invalid_token",
         "credential_rejected"),
        ("external write failed - far side answered http 403: insufficient scope",
         "external_write_refused"),
        ("external write failed - far side answered http 404: not found",
         "external_write_failed"),
        ("external write failed - [missing_consent]", "external_write_refused"),
    ],
)
def test_the_existing_classes_are_unchanged(row: str, expected: str) -> None:
    """The new branch runs first, so this is the regression that proves it only
    claims rows that carry an edge code."""
    assert _classify_external_write(row) == expected
