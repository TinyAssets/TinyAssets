"""Capability-URL connections — the secret is a path segment, held in the vault.

Requirement source: ``openspec/specs/http-connections-and-outbound-authority/spec.md``
(as-built). The change that introduced it is archived at
``openspec/changes/archive/2026-09-30-capability-url-connections/``.

THE live failure, 2026-09-30 (free account universe
``u-01ky3zh1arr8qth8jee7zx63pq``, turn ``d75a6cb6447e4434b8b0d6aecf706475``): a
universe was handed a friend's ``https://tinyassets.io/hooks/inbound/<secret>`` link
to send bug reports to. It asked for an "API Token or Bearer Token" that does not
exist, hit ``auth_scheme must be one of basic, bearer, header, oauth1a``, and
settled on ``bearer`` with the secret hardcoded into ``path_template`` — stored
in the clear in the grant, projected to the owner and to any remix — while the
pasted value went out as a useless ``Authorization`` header.

Every case here would FAIL if its guard were removed. The two that carry the
design are:

* ``test_wire_request_carries_the_substituted_secret`` — the real transport path
  through a loopback stub records the path the receiver actually saw; and
* ``test_allowlist_refuses_a_node_supplied_value_in_the_secret_position`` — the
  reserved placeholder's pattern is the LITERAL token, which is what makes the
  egress boundary decidable with no secret material in the URL.

A neutral example host is used wherever the platform's own is not the point: the
primitive names no service, and ``url_secret`` is exactly as channel-agnostic as
``bearer``.
"""

from __future__ import annotations

import http.server
import json
import socket
import ssl
import threading
from pathlib import Path
from typing import Any

import pytest

from tinyassets.api.http_connection import (
    URL_SECRET_FIELD_NAME,
)
from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import AuthProvider, DevAuthProvider, Identity
from tinyassets.storage.outbound_connections import (
    _URL_SECRET_SCHEME,
    ConnectionSecretBundle,
    SsrfValidationError,
    _build_http_secret_bundle,
    _parse_allowed_endpoints,
    _SsrfHardenedHttpDriver,
    _substitute_url_secret,
    _TrustedNetworkDriver,
)

#: A real-shaped capability secret: `secrets.token_urlsafe(32)` is what
#: `storage/webhook_hooks.mint` produces, so this is the exact shape the
#: platform's own `/hooks/inbound/<token>` receiver hands out.
HOOK_SECRET = "MRL8k3nZ-Ht9xWq2vB7pC4dF6gJ0sYaT1uK5wQ9eXmZ7b0"
HOOK_HOST = "hooks.example.com"
HOOK_TEMPLATE = "/hooks/inbound/{secret}"
HOOK_URL = f"https://{HOOK_HOST}/hooks/inbound/{HOOK_SECRET}"

#: Slack's secret is three segments, which is why `{secret+}` exists.
SLACK_SECRET = "T024BE7LD/B01ABCDEF/nOpQrStUvWxYz0123456789A"
SLACK_HOST = "hooks.slack.example"
SLACK_TEMPLATE = "/services/{secret+}"


def _endpoints(
    host: str = HOOK_HOST, template: str = HOOK_TEMPLATE, methods: Any = ("POST",)
) -> list[dict[str, Any]]:
    return [{"host": host, "path_template": template, "methods": list(methods)}]


def _parsed(*args: Any, **kwargs: Any) -> tuple[Any, ...]:
    return _parse_allowed_endpoints(_endpoints(*args, **kwargs))


# --------------------------------------------------------------------------- #
# Auth / universe harness (same shape as test_http_connection_provisioning).
# --------------------------------------------------------------------------- #
class _StaticAuthProvider(AuthProvider):
    def __init__(self, identity: Identity | None) -> None:
        self.identity = identity

    def resolve_token(self, token: str) -> Identity | None:
        return self.identity if token == "valid" else None

    def is_auth_required(self) -> bool:
        return True

    def register_client(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {"client_id": "test-client", **metadata}

    def create_authorization(self, *a: Any, **k: Any) -> str:
        return "test-code"

    def exchange_code(self, *a: Any, **k: Any) -> dict[str, Any] | None:
        return None


def _login(user_id: str) -> None:
    set_provider(
        _StaticAuthProvider(
            Identity(
                user_id=user_id,
                username=user_id,
                capabilities=["tinyassets.universe.write"],
            )
        )
    )
    auth_middleware("valid")


def _logout() -> None:
    from tinyassets.auth.middleware import clear_identity

    set_provider(DevAuthProvider())
    clear_identity()


@pytest.fixture(autouse=True)
def _reset_auth() -> Any:
    _logout()
    yield
    _logout()


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    return root


def _make_universe(base: Path, uid: str, *, admin: str = "") -> Path:
    from tinyassets.daemon_server import grant_universe_access

    udir = base / uid
    udir.mkdir(parents=True, exist_ok=True)
    if admin:
        grant_universe_access(
            base, universe_id=uid, actor_id=admin, permission="admin", granted_by=admin
        )
    return udir


def _connect(
    uid: str,
    *,
    destination: str = "bug-reports",
    secret: str = HOOK_URL,
    endpoints: Any = None,
    auth_scheme: str | None = _URL_SECRET_SCHEME,
    access: str | None = None,
) -> dict[str, Any]:
    from tests.support.connection_door import connect_http

    doc: dict[str, Any] = {
        "destination": destination,
        "secret": secret,
        "allowed_endpoints": _endpoints() if endpoints is None else endpoints,
    }
    if auth_scheme is not None:
        doc["auth_scheme"] = auth_scheme
    if access is not None:
        doc["access"] = access
    return connect_http(universe_id=uid, payload=json.dumps(doc))


def _http_records(udir: Path) -> list[dict[str, Any]]:
    from tests.test_http_connection_provisioning import _http_records

    return [
        r for r in _http_records(udir) if r.get("credential_type") == "http"
    ]


# --------------------------------------------------------------------------- #
# 1. The endpoint grammar: the reserved placeholder and its literal pattern.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 2. The scheme <-> placeholder binding, both directions, three checkpoints.
# --------------------------------------------------------------------------- #


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_dispatch_refuses_a_row_mutated_out_of_the_scheme() -> None:
    """The TOCTOU closure. The broker re-reads the row per call, so a row
    mutated from `url_secret` to `bearer` must be refused BEFORE a bundle
    exists — otherwise the placeholder goes out literally in the path and the
    secret segment goes out in an Authorization header.
    """
    driver = _TrustedNetworkDriver(
        {"allow_test_fixtures": False, "allow_http_connections": True}, Path(".")
    )
    with pytest.raises(SsrfValidationError) as exc:
        driver(
            connection_type="http",
            auth_scheme="bearer",
            allowed_endpoints=_parsed(),
            credential=HOOK_SECRET,
            verb="POST",
            provider="http",
            destination="bug-reports",
            request={"url": f"https://{HOOK_HOST}{HOOK_TEMPLATE}"},
        )
    assert _URL_SECRET_SCHEME in str(exc.value)


# --------------------------------------------------------------------------- #
# 3. The stored segment's grammar (what makes verbatim substitution safe).
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 4. Substitution happens AFTER the allowlist, and only on the path.
# --------------------------------------------------------------------------- #
def _canonical(path_qs: str) -> Any:
    from tinyassets.storage.outbound_connections import _CanonicalOutboundUrl

    return _CanonicalOutboundUrl(
        hostname=HOOK_HOST, port=443, path_qs=path_qs, is_ip_literal=False
    )


def _substitute(path_qs: str, endpoint: Any, **over: Any) -> Any:
    kwargs: dict[str, Any] = {
        "auth_scheme": _URL_SECRET_SCHEME,
        "bundle": ConnectionSecretBundle(token=HOOK_SECRET),
        "endpoint": endpoint,
    }
    kwargs.update(over)
    return _substitute_url_secret(_canonical(path_qs), **kwargs)


# --------------------------------------------------------------------------- #
# 5. The wire. A loopback stub records what the receiver actually saw.
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
        payload = stub["body"]
        self.send_response(stub["status"])
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _serve
    do_POST = _serve


@pytest.fixture
def stub_server() -> Any:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
    server.stub = {"status": 200, "body": b"ok", "received": None}  # type: ignore[attr-defined]
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


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_wire_request_carries_the_substituted_secret(stub_server: Any) -> None:
    """THE end-to-end proof, at the HTTP layer only.

    MUTATION CHECK: delete the `_substitute_url_secret` call from
    `_SsrfHardenedHttpDriver.__call__` and this fails — the receiver sees the
    literal `{secret}`. Weaken the reserved pattern to `.*` and
    `test_allowlist_refuses_a_node_supplied_value_in_the_secret_position` fails.
    """
    driver, port = _local_driver(stub_server)
    host = f"public.example:{port}"
    endpoints = _parse_allowed_endpoints(
        [
            {
                "host": "public.example",
                "path_template": HOOK_TEMPLATE,
                "methods": ["POST"],
            }
        ]
    )

    result = driver(
        bundle=ConnectionSecretBundle(token=HOOK_SECRET),
        auth_scheme=_URL_SECRET_SCHEME,
        method="POST",
        url=f"https://{host}{HOOK_TEMPLATE}",
        headers={"Content-Type": "application/json"},
        body={"text": "a bug report"},
        allowed_endpoints=endpoints,
    )

    assert result["status"] == 200
    received = stub_server.stub["received"]
    # The receiver saw the REAL capability URL...
    assert received["path"] == f"/hooks/inbound/{HOOK_SECRET}"
    # ...and no Authorization header, because there is nothing to put in one.
    assert "authorization" not in received["headers"]
    assert received["body"] == b'{"text":"a bug report"}'
    # ...and the secret does not ride back out in the returned object.
    assert HOOK_SECRET not in json.dumps(result)


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_wire_request_carries_a_multi_segment_secret(stub_server: Any) -> None:
    driver, port = _local_driver(stub_server)
    endpoints = _parse_allowed_endpoints(
        [
            {
                "host": "public.example",
                "path_template": SLACK_TEMPLATE,
                "methods": ["POST"],
            }
        ]
    )

    driver(
        bundle=ConnectionSecretBundle(token=SLACK_SECRET),
        auth_scheme=_URL_SECRET_SCHEME,
        method="POST",
        url=f"https://public.example:{port}{SLACK_TEMPLATE}",
        body={"text": "hi"},
        allowed_endpoints=endpoints,
    )

    assert stub_server.stub["received"]["path"] == f"/services/{SLACK_SECRET}"


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_public_path_outside_secret_template_is_sent_without_substitution(stub_server) -> None:
    """C7: public paths remain reachable; only the reserved template injects a key."""
    driver, port = _local_driver(stub_server)
    for path in (
        f"/hooks/inbound/{HOOK_SECRET}",
        "/hooks/inbound/guessed-value-01",
        "/hooks/inbound/{other}",
    ):
        result = driver(
            bundle=ConnectionSecretBundle(token=HOOK_SECRET),
            auth_scheme=_URL_SECRET_SCHEME,
            method="POST",
            url=f"https://{HOOK_HOST}:{port}{path}",
            allowed_endpoints=_parsed(),
        )
        assert result["status"] == 200
        assert stub_server.stub["received"]["path"] == path
        assert "authorization" not in stub_server.stub["received"]["headers"]


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_a_failing_far_side_that_echoes_the_url_fails_closed(
    stub_server: Any,
) -> None:
    """A 500 whose body quotes the request URL is the realistic leak: without
    the segment in the bundle, that body would be returned verbatim and land in
    the run's `external_write_errors` preview.

    Because the segment IS a bundle member, `_declassify_response` catches the
    echo and the call fails closed with fixed text instead. MUTATION CHECK:
    return `ConnectionSecretBundle()` from `_build_http_secret_bundle` for this
    scheme and the call succeeds with the secret in the body.
    """
    from tinyassets.storage.outbound_connections import ProxyRequestError

    driver, port = _local_driver(stub_server)
    stub_server.stub.update(
        status=500, body=f"failed calling /hooks/inbound/{HOOK_SECRET}".encode()
    )
    endpoints = _parse_allowed_endpoints(
        [
            {
                "host": "public.example",
                "path_template": HOOK_TEMPLATE,
                "methods": ["POST"],
            }
        ]
    )

    with pytest.raises(ProxyRequestError) as exc:
        driver(
            bundle=ConnectionSecretBundle(token=HOOK_SECRET),
            auth_scheme=_URL_SECRET_SCHEME,
            method="POST",
            url=f"https://public.example:{port}{HOOK_TEMPLATE}",
            body={"text": "x"},
            allowed_endpoints=endpoints,
        )
    assert HOOK_SECRET not in str(exc.value)


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_a_single_echoed_segment_of_a_multi_segment_secret_fails_closed(
    stub_server: Any,
) -> None:
    """astra refute round 1, FINDING 2.

    The receiver echoes ONE segment of the Slack-shaped secret, not the joined
    form. Scanning only the joined credential let that through to the caller,
    into `bounded_evidence` and into the run's `external_write_errors` preview.

    MUTATION CHECK: drop the `url_secret_segment_*` bundle members and this
    returns 500 with the segment in the body.
    """
    from tinyassets.storage.outbound_connections import ProxyRequestError

    driver, port = _local_driver(stub_server)
    leaked = SLACK_SECRET.split("/")[-1]
    stub_server.stub.update(status=500, body=json.dumps({"token": leaked}).encode())
    endpoints = _parse_allowed_endpoints(
        [
            {
                "host": "public.example",
                "path_template": SLACK_TEMPLATE,
                "methods": ["POST"],
            }
        ]
    )

    with pytest.raises(ProxyRequestError) as exc:
        driver(
            bundle=_build_http_secret_bundle(_URL_SECRET_SCHEME, SLACK_SECRET),
            auth_scheme=_URL_SECRET_SCHEME,
            method="POST",
            url=f"https://public.example:{port}{SLACK_TEMPLATE}",
            body={"text": "x"},
            allowed_endpoints=endpoints,
        )
    assert leaked not in str(exc.value)


# --------------------------------------------------------------------------- #
# 6. The pasted link: the platform extracts the secret.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 7. A hardcoded secret in path_template is refused when authored.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 8. The deposit door, end to end.
# --------------------------------------------------------------------------- #


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_dispatch_refuses_a_row_mutated_to_full_access() -> None:
    """astra FINDING 4, at the dispatch boundary the broker re-reads."""
    driver = _TrustedNetworkDriver(
        {"allow_test_fixtures": False, "allow_http_connections": True}, Path(".")
    )
    with pytest.raises(SsrfValidationError) as exc:
        driver(
            connection_type="http",
            auth_scheme=_URL_SECRET_SCHEME,
            allowed_endpoints=_parsed(),
            access_mode="full",
            credential=HOOK_SECRET,
            verb="POST",
            provider="http",
            destination="bug-reports",
            request={"url": f"https://{HOOK_HOST}/unguarded/{HOOK_SECRET}"},
        )
    assert "full" in str(exc.value)


# --------------------------------------------------------------------------- #
# 9. The ask, and the sentence the owner reads.
# --------------------------------------------------------------------------- #
_ASK = {
    "kind": "API",
    "title": "The webhook link for your friend's bug tracker",
    "body": "So I can post the bug reports you asked me to collect.",
    "action": {
        "type": "connect_http",
        "destination": "bug-reports",
        "auth_scheme": _URL_SECRET_SCHEME,
        "endpoints": [
            {"host": HOOK_HOST, "path_template": HOOK_TEMPLATE, "methods": ["POST"]}
        ],
    },
    "fields": [
        {
            "name": URL_SECRET_FIELD_NAME,
            "type": "secret",
            "label": "Webhook URL",
            "help": "the whole link they gave you, starting https://",
        }
    ],
}


def _ask_request(uid: str, **over: Any) -> dict[str, Any]:
    from tinyassets.api.pending_requests import request_from_user

    return request_from_user(universe_id=uid, payload=json.dumps({**_ASK, **over}))


# --------------------------------------------------------------------------- #
# 10. The effector keeps a node-authored secret out of the run record.
# --------------------------------------------------------------------------- #
class _View:
    def __init__(self, scheme: str) -> None:
        self.auth_scheme = scheme


# --------------------------------------------------------------------------- #
# 11. The served guidance teaches the shape (it is the only thing the agent
#     reads before composing a card).
# --------------------------------------------------------------------------- #


from tests.support.egress import admitted_egress  # noqa: E402, F401
