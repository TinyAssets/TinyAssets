"""Pinned leaf compatibility and child-only redirect budget plumbing."""

import runpy
import socket
import time
from pathlib import Path

import pytest

from tests.test_outbound_ssrf_driver import _PassThroughTLS
from tests.test_outbound_ssrf_driver import stub_server as stub_server
from tinyassets.storage.outbound_connections import (
    SsrfValidationError,
    _parse_canonical_https_url,
    _SsrfHardenedHttpDriver,
)


def _leaf_kwargs(server, **overrides):
    port = server.server_address[1]
    return {
        "method": "GET",
        "canonical": _parse_canonical_https_url(
            f"https://api.example.com:{port}/download",
            allowed_ports=frozenset({port}),
        ),
        "pinned_address": "127.0.0.1",
        "headers": {},
        "body": None,
        "ssl_context": _PassThroughTLS(),
        "open_socket": lambda address, timeout, source: socket.create_connection(address, timeout),
        "timeout": 2.0,
        "max_total_seconds": 2.0,
        "max_body_bytes": 1000,
        "max_header_count": 20,
        "max_header_bytes": 5000,
        **overrides,
    }


@pytest.fixture(scope="module")
def legacy_leaf():
    return runpy.run_path(
        str(Path(__file__).with_name("fixtures") / "legacy_download_transport.py")
    )["_execute_pinned_https_request"]


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_redirect_dns_still_validates_every_resolved_address():
    driver = _SsrfHardenedHttpDriver(resolver=lambda host, port: ["8.8.8.8", "127.0.0.1"])
    canonical = _parse_canonical_https_url("https://api.example.com/download", allowed_ports={443})
    with pytest.raises(SsrfValidationError):
        driver._redirect_address(canonical, time.monotonic() + 2)


from tests.support.egress import admitted_egress  # noqa: E402, F401
