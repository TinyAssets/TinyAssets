"""One yes for the whole channel.

Founder, 2026-09-02, after their universe raised a second GitHub ask minutes
after they had approved full repo patching: *"it shouldn't have needed to ask
that, the other request was already for full repo access"* and *"a more
agnostic term it should have asked for should have been full channel
access."*

A connection carries one ``access_mode``. Four readers consult it -- the
egress allowlist, the git-scope check, the workspace consents, and the
inventory -- always AFTER the safety checks it does not replace. Nothing is
stored as a wildcard, so no ``*/*`` can reach the git transport or a consent
row.

Change: ``openspec/changes/full-channel-access``.
"""

from __future__ import annotations

import pytest

# The universe + login + deposit harness that drives the real
# `extend_http`. `base` is a fixture and `_reset_auth` is autouse, so
# both are imported by name rather than reimplemented.
from tests.test_workspace_authority import (  # noqa: F401
    _reset_auth,
    base,
)
from tinyassets.storage.outbound_connections import (  # noqa: E402
    ACCESS_EXACT,
    ACCESS_FULL,
    ConnectionLedger,
)

GITHUB_ENDPOINT = {
    "host": "api.github.com",
    "path_template": "/repos/octocat/hello/contents/{path+}",
    "methods": ["GET", "PUT"],
    "param_patterns": {"path": "[A-Za-z0-9._/-]{1,200}"},
}


def _ledger(tmp_path, actor="user-1"):
    return ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: actor
    )


def _create(ledger, *, scopes=("GET",), endpoints=(GITHUB_ENDPOINT,), **over):
    document = dict(
        connection_id="conn-1",
        owner_user_id="user-1",
        connection_class="http",
        connection_type="http",
        auth_scheme="bearer",
        scopes=scopes,
        provider="http",
        destination="github",
        credential_ref="vault://http/github",
        allowed_endpoints=list(endpoints),
    )
    document.update(over)
    return ledger.create_connection(**document)


# --------------------------------------------------------------------------
# the mode: stored, migrated, moved under CAS
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# reader 1: the egress allowlist, after the safety checks, never instead
# --------------------------------------------------------------------------


def _driver():
    from tinyassets.storage.outbound_connections import _SsrfHardenedHttpDriver

    return _SsrfHardenedHttpDriver


def _enforce(url, method, endpoints, mode):
    from tinyassets.storage.outbound_connections import (
        _credential_endpoint,
        _parse_allowed_endpoints,
        _parse_canonical_https_url,
    )

    canonical = _parse_canonical_https_url(url, allowed_ports=frozenset({443}))
    return _credential_endpoint(
        canonical, method, _parse_allowed_endpoints(list(endpoints)), mode
    )


# --------------------------------------------------------------------------
# reader 2: git scopes
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# reader 3: workspace consents
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# D6: taking a key back takes back what it authorized
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# the ask shape
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# the sentence: what the owner reads before they say yes
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# the inventory the agent reads back
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# the decision survives the rail (Codex code review round 1, P0)
# --------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The yes lands on the reach the owner read
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Codex code review round 3: the two P0s
# ---------------------------------------------------------------------------


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_the_broker_dispatches_a_verb_a_full_channel_carries(tmp_path):
    """The same P0 at the dispatcher, driven through it. The broker re-reads
    the row before every call, so it must read the row's MODE too -- otherwise
    the owner's POST is refused at the last gate on a channel they granted in
    full."""
    from tinyassets.storage.outbound_connections import CredentialBlindBroker

    ledger = _ledger(tmp_path)
    _create(ledger, scopes=("GET",))
    ledger.grant_connection(
        grant_id="grant-1", connection_id="conn-1",
        owner_user_id="user-1",
    )

    sent, resolved = [], []

    broker = CredentialBlindBroker(
        ledger,
        resolve_credential=lambda _ref, _ctype=None: resolved.append(_ref) or "ghp_" + "x" * 30,
        network_request=lambda **kw: (
            sent.append((kw["verb"], kw["auth_scheme"])) or {"status": 200}),
    )

    # C7: an operation outside credential scope reaches the public web without auth.
    broker.dispatch("grant-1", "POST", {"url": "https://api.github.com/x"})
    assert sent == [("POST", "none")]
    assert resolved == []

    endpoints_json, scopes_json = ledger.policy_json("conn-1")
    assert ledger.set_access_mode(
        connection_id="conn-1", access_mode=ACCESS_FULL, expected_mode=ACCESS_EXACT,
        expected_endpoints_json=endpoints_json, expected_scopes_json=scopes_json,
        expected_incarnation=ledger.incarnation("conn-1"),
    ) is True

    # The upgrade rewrote no scope row...
    upgraded = ledger._get_connection_resource("conn-1")
    assert upgraded.scopes == ("GET",)
    # ...and the same POST now reaches the network.
    broker.dispatch("grant-1", "POST", {"url": "https://api.github.com/x"})
    assert sent == [("POST", "none"), ("POST", "bearer")]
    assert resolved == ["vault://http/github"]

    # A git scope is still not an HTTP verb, whatever the mode.
    with pytest.raises(PermissionError, match="outside the granted"):
        broker.dispatch("grant-1", "git_read:octocat/hello", {"url": "https://api.github.com/x"})


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_a_replacement_key_with_an_identical_policy_does_not_inherit_the_yes(tmp_path):
    """Codex code review round 3, P0. The connection id and the credential
    reference are BOTH deterministic per (universe, destination), so a key
    removed and a different one deposited with exactly the same endpoints and
    scopes matched every compare-and-swap predicate -- and the owner's full
    decision landed on a key they never granted it for.

    My earlier test changed the host, so the endpoint text did the rejecting
    and replacement detection was never exercised.
    """
    ledger = _ledger(tmp_path)
    _create(ledger)
    seen_endpoints, seen_scopes = ledger.policy_json("conn-1")
    seen_incarnation = ledger.incarnation("conn-1")
    assert seen_incarnation

    ledger.delete_connection("conn-1")
    # ...and a DIFFERENT key under the same destination, with an identical
    # policy. Every field the swap used to compare is unchanged.
    _create(ledger)
    assert ledger.policy_json("conn-1") == (seen_endpoints, seen_scopes)
    assert ledger.access_mode("conn-1") == ACCESS_EXACT
    replacement = ledger.incarnation("conn-1")
    assert replacement != seen_incarnation, "the deposit did not mint a new incarnation"

    assert ledger.set_access_mode(
        connection_id="conn-1", access_mode=ACCESS_FULL, expected_mode=ACCESS_EXACT,
        expected_endpoints_json=seen_endpoints, expected_scopes_json=seen_scopes,
        expected_incarnation=seen_incarnation,
    ) is False
    assert ledger.access_mode("conn-1") == ACCESS_EXACT, (
        "a replacement key inherited a decision made about a different one"
    )

    # Answering about the key that is actually there works.
    assert ledger.set_access_mode(
        connection_id="conn-1", access_mode=ACCESS_FULL, expected_mode=ACCESS_EXACT,
        expected_endpoints_json=seen_endpoints, expected_scopes_json=seen_scopes,
        expected_incarnation=replacement,
    ) is True


from tests.support.egress import admitted_egress  # noqa: E402, F401
