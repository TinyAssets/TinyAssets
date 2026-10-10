"""DNS canary tests — verify tinyassets.io + mcp.tinyassets.io resolve to public IPs.

Runs in GHA as part of the dns-canary.yml workflow (scheduled every 15 min).
Also acts as a local sanity gate: if DNS is broken the test fails immediately
rather than waiting for an MCP probe timeout.

Network skip: set NO_NETWORK=1 in the environment to skip all resolution tests
(useful for offline CI sandboxes or air-gapped builds).
"""

from __future__ import annotations

import ipaddress
import os
import socket

import pytest

HOSTNAMES = [
    "tinyassets.io",
    "mcp.tinyassets.io",
]

# RFC 1918 + link-local + loopback prefixes that are never valid public IPs.
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

# Fixture / skip marker — checked once per session.
_no_network = os.environ.get("NO_NETWORK", "").strip() in ("1", "true", "yes")


def _is_private(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in _PRIVATE_NETWORKS)


# ---------------------------------------------------------------------------
# Parametrised resolution tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hostname", HOSTNAMES)
def test_hostname_resolves(hostname: str):
    """Each hostname must resolve to at least one IP address."""
    if _no_network:
        pytest.skip("NO_NETWORK=1")
    try:
        addrs = socket.gethostbyname_ex(hostname)[2]
    except OSError as exc:
        pytest.fail(f"{hostname} failed to resolve: {exc}")
    assert addrs, f"{hostname} resolved to empty address list"


@pytest.mark.parametrize("hostname", HOSTNAMES)
def test_hostname_resolves_to_public_ip(hostname: str):
    """Each hostname must resolve to at least one public (non-RFC-1918) IP."""
    if _no_network:
        pytest.skip("NO_NETWORK=1")
    try:
        addrs = socket.gethostbyname_ex(hostname)[2]
    except OSError as exc:
        pytest.fail(f"{hostname} failed to resolve: {exc}")
    public = [a for a in addrs if not _is_private(a)]
    assert public, (
        f"{hostname} resolved only to private/loopback addresses: {addrs}. "
        "Expected at least one public Cloudflare IP."
    )


# ---------------------------------------------------------------------------
# Unit tests for helpers (no network required, always run)
# ---------------------------------------------------------------------------


def test_is_private_loopback():
    assert _is_private("127.0.0.1")


def test_is_private_rfc1918():
    assert _is_private("10.0.0.1")
    assert _is_private("192.168.1.1")
    assert _is_private("172.16.0.1")


def test_is_private_public_ip():
    assert not _is_private("1.1.1.1")
    assert not _is_private("8.8.8.8")


def test_is_private_link_local():
    assert _is_private("169.254.1.1")


def test_is_private_ipv6_loopback():
    assert _is_private("::1")


def test_is_private_public_ipv6():
    assert not _is_private("2606:4700:4700::1111")
