"""Actual local HTTP parsing/declassification feeds the capacity decoder.

This uses the production hardened driver with explicit loopback/TLS test seams;
it is not a real provider call or a full credential-broker subprocess test.
"""

import json

import pytest

from tests.test_outbound_ssrf_driver import _local_driver, stub_server  # noqa: F401
from tinyassets.providers.discovery_protocols import discovery_protocol
from tinyassets.storage.outbound_connections import (
    ConnectionSecretBundle,
    ProxyRequestError,
    _parse_allowed_endpoints,
)


@pytest.mark.parametrize("status,scope", [(402, "account"), (429, "unknown"), (503, "model")])
def test_retry_after_survives_real_http_driver(stub_server, status, scope):  # noqa: F811
    stub_server.stub.update(
        status=status,
        body=json.dumps({"error": {"code": status, "message": "capacity unavailable"}}).encode(),
        extra_headers=(("Retry-After", "71"),),
    )
    driver, calls, _context, port = _local_driver(stub_server)
    result = driver(
        bundle=ConnectionSecretBundle(token="synthetic-private-token"),
        auth_scheme="bearer",
        allowed_endpoints=_parse_allowed_endpoints([{
                "host": "public.example", "path_template": "/chat", "methods": ["POST"]}]),
        method="POST",
        url=f"https://public.example:{port}/chat",
        body={"messages": []},
    )
    assert calls["open_socket"] == [("127.0.0.1", port)]
    assert result["headers"]["retry-after"] == "71"
    signal = discovery_protocol("openrouter_user_models_v1").capacity_decoder(
        result["status"],
        result["headers"],
    )
    assert signal.scope == scope and signal.retry_after_s == 71


def test_secret_in_capacity_response_is_not_declassified(stub_server):  # noqa: F811
    stub_server.stub.update(
        status=429,
        body=b'{"error":"rate limited"}',
        extra_headers=(("Retry-After", "synthetic-private-token"),),
    )
    driver, _calls, _context, port = _local_driver(stub_server)
    with pytest.raises(ProxyRequestError, match="unsafe destination response"):
        driver(
            bundle=ConnectionSecretBundle(token="synthetic-private-token"),
            auth_scheme="bearer",
            allowed_endpoints=_parse_allowed_endpoints([{
                "host": "public.example", "path_template": "/chat", "methods": ["POST"]}]),
            method="POST",
            url=f"https://public.example:{port}/chat",
            body={"messages": []},
        )


from tests.support.egress import admitted_egress  # noqa: E402, F401
