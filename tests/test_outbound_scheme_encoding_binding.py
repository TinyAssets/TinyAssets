"""Scheme <-> credential-encoding binding in the broker's bundle builder.

Codex ADAPT (PR #2525): the vault stores ONE opaque string per connection whose
encoding is fixed by the scheme it was deposited under. ``_build_http_secret_bundle``
is handed the connection row's CURRENT scheme, so a row mutated from ``oauth1a`` to
``bearer`` would re-interpret the four-value JSON bundle as a single token and emit
it verbatim as ``Authorization: Bearer {json…}`` to an allowlisted endpoint — leaking
all four secrets. Codex reproduced exactly that header. These tests pin the fix:
the builder refuses, fail-closed, any credential whose encoding is recognisably that
of a different scheme, BEFORE any header can be built.
"""

from __future__ import annotations

import json

import pytest

from tinyassets.storage.outbound_connections import (
    SsrfValidationError,
    _build_http_secret_bundle,
    _looks_like_oauth1a_bundle,
    _ssrf_auth_headers,
)

_BUNDLE = json.dumps(
    {
        "api_key": "ck-secret",
        "api_secret": "cs-secret",
        "access_token": "at-secret",
        "access_token_secret": "ats-secret",
    }
)


def test_oauth1a_bundle_under_its_own_scheme_parses() -> None:
    bundle = _build_http_secret_bundle("oauth1a", _BUNDLE)
    assert bundle.get("api_key") == "ck-secret"
    assert bundle.get("access_token_secret") == "ats-secret"


@pytest.mark.parametrize("mutated_scheme", ["bearer", "header", "basic"])
def test_oauth1a_bundle_is_refused_under_a_mutated_scheme(mutated_scheme: str) -> None:
    """THE LEAK: a row flipped from oauth1a to another scheme must NOT hand the JSON
    bundle to that scheme's header builder. Fail closed before any header exists."""
    with pytest.raises(SsrfValidationError, match="encoding does not match"):
        _build_http_secret_bundle(mutated_scheme, _BUNDLE)


def test_mutated_scheme_never_reaches_a_bearer_header() -> None:
    """End-to-end at the header layer: even if a caller tried to apply bearer auth to
    the oauth1a encoding, no header carrying the bundle can be produced."""
    with pytest.raises(SsrfValidationError):
        bundle = _build_http_secret_bundle("bearer", _BUNDLE)
        _ssrf_auth_headers("bearer", bundle)  # unreachable — builder refuses first
    # And a genuine bearer token still works as before.
    ok = _build_http_secret_bundle("bearer", "plain-token")
    assert _ssrf_auth_headers("bearer", ok) == {"Authorization": "Bearer plain-token"}


def test_looks_like_oauth1a_bundle_is_precise() -> None:
    assert _looks_like_oauth1a_bundle(_BUNDLE)
    assert _looks_like_oauth1a_bundle("  " + _BUNDLE)  # leading whitespace tolerated
    # Ordinary tokens / JSON that is NOT the four-key bundle are left alone.
    assert not _looks_like_oauth1a_bundle("sk-live-abc123")
    assert not _looks_like_oauth1a_bundle('{"token": "x"}')
    assert not _looks_like_oauth1a_bundle('{"api_key": "only-one"}')
    assert not _looks_like_oauth1a_bundle("[1,2,3]")
    assert not _looks_like_oauth1a_bundle("{not json")
    assert not _looks_like_oauth1a_bundle("")


@pytest.mark.parametrize("bad", ["user:", ":pw", ":", "nocolon"])
def test_basic_requires_both_halves_non_empty_at_dispatch(bad: str) -> None:
    with pytest.raises(SsrfValidationError, match="username:password"):
        _build_http_secret_bundle("basic", bad)


def test_basic_password_may_itself_contain_colons() -> None:
    bundle = _build_http_secret_bundle("basic", "user:pa:ss:wd")
    assert bundle.get("username") == "user" and bundle.get("password") == "pa:ss:wd"


pytestmark = pytest.mark.verifies('owner-daemon-in-box:T6')
