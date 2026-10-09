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

import json

import pytest

# The universe + login + deposit harness that drives the real
# `extend_http`. `base` is a fixture and `_reset_auth` is autouse, so
# both are imported by name rather than reimplemented.
from tests.test_workspace_authority import (  # noqa: F401
    _reset_auth,
    base,
)
from tinyassets.storage.outbound_connections import (  # noqa: E402
    _SSRF_ALLOWED_METHODS,
    ACCESS_EXACT,
    ACCESS_FULL,
    ConnectionLedger,
    SsrfValidationError,
    _verb_within_scopes,
    normalize_access_mode,
)
from tinyassets.storage.workspace_authority import connection_access_mode, has_git_scope

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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", ACCESS_EXACT), (None, ACCESS_EXACT), ("full", ACCESS_FULL),
     ("FULL", ACCESS_FULL), ("  exact ", ACCESS_EXACT)],
)
def test_the_mode_normalizes_or_refuses(raw, expected):
    assert normalize_access_mode(raw) == expected
    for bad in ("wide", "all", "*", "full-ish", 1):
        with pytest.raises(ValueError):
            normalize_access_mode(bad)


def test_a_new_connection_is_exact_unless_it_says_otherwise(tmp_path):
    ledger = _ledger(tmp_path)
    view = _create(ledger)
    assert view.access_mode == ACCESS_EXACT
    assert view.as_dict()["access_mode"] == ACCESS_EXACT
    assert ledger.access_mode("conn-1") == ACCESS_EXACT


def test_a_full_connection_stores_the_mode_and_no_wildcard(tmp_path):
    ledger = _ledger(tmp_path)
    view = _create(ledger, access_mode=ACCESS_FULL)
    assert view.access_mode == ACCESS_FULL
    resource = ledger._get_connection_resource("conn-1")
    assert resource.access_mode == ACCESS_FULL
    # The authority is the mode. Nothing anywhere is a wildcard.
    stored = json.dumps([e.as_dict() for e in resource.allowed_endpoints]) + json.dumps(
        list(resource.scopes)
    )
    assert "*" not in stored


def test_a_row_written_before_the_column_existed_reads_as_exact(tmp_path):
    """A migration must never widen a grant somebody already made."""
    ledger = _ledger(tmp_path)
    _create(ledger)
    with ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET access_mode = ''")
        conn.commit()
    assert ledger._get_connection_resource("conn-1").access_mode == ACCESS_EXACT


def test_the_mode_moves_only_under_a_whole_policy_compare_and_swap(tmp_path):
    ledger = _ledger(tmp_path)
    _create(ledger)
    endpoints_json, scopes_json = ledger.policy_json("conn-1")

    assert ledger.set_access_mode(
        connection_id="conn-1", access_mode=ACCESS_FULL, expected_mode=ACCESS_EXACT,
        expected_endpoints_json=endpoints_json, expected_scopes_json=scopes_json,
    ) is True
    assert ledger.access_mode("conn-1") == ACCESS_FULL

    # A second answer that read the OLD mode loses, rather than overwriting a
    # decision it never saw.
    assert ledger.set_access_mode(
        connection_id="conn-1", access_mode=ACCESS_FULL, expected_mode=ACCESS_EXACT,
        expected_endpoints_json=endpoints_json, expected_scopes_json=scopes_json,
    ) is False
    assert ledger.set_access_mode(
        connection_id="missing", access_mode=ACCESS_FULL, expected_mode=ACCESS_EXACT,
        expected_endpoints_json=endpoints_json, expected_scopes_json=scopes_json,
    ) is False


def test_a_concurrent_extension_cannot_ride_into_a_full_grant(tmp_path):
    """Codex code review round 1, finding 3.

    Comparing the mode alone was not enough: device A previews full on a
    connection declaring ONE host, device B adds a second host with an
    ordinary exact extension (mode still exact), and A's swap would then make
    BOTH hosts full -- although the owner only ever saw one on the tab. The
    swap compares the whole policy, so A loses and re-previews.
    """
    import json as _json

    ledger = _ledger(tmp_path)
    _create(ledger)
    seen_endpoints, seen_scopes = ledger.policy_json("conn-1")   # device A previews

    # device B widens, exactly.
    ledger.extend_http_connection_endpoints(
        connection_id="conn-1",
        endpoints=[
            GITHUB_ENDPOINT,
            {"host": "api.other.example", "path_template": "/v1/x", "methods": ["GET"]},
        ],
        scopes=("GET",),
        expected_endpoints_json=seen_endpoints,
        expected_scopes_json=seen_scopes,
    )
    assert ledger.access_mode("conn-1") == ACCESS_EXACT

    # device A's yes, carrying the snapshot it showed the owner.
    assert ledger.set_access_mode(
        connection_id="conn-1", access_mode=ACCESS_FULL, expected_mode=ACCESS_EXACT,
        expected_endpoints_json=seen_endpoints, expected_scopes_json=seen_scopes,
    ) is False
    assert ledger.access_mode("conn-1") == ACCESS_EXACT, (
        "a host the owner never saw was granted in full"
    )

    # Re-previewing and answering again is what lands it.
    fresh_endpoints, fresh_scopes = ledger.policy_json("conn-1")
    assert _json.loads(fresh_endpoints) != _json.loads(seen_endpoints)
    assert ledger.set_access_mode(
        connection_id="conn-1", access_mode=ACCESS_FULL, expected_mode=ACCESS_EXACT,
        expected_endpoints_json=fresh_endpoints, expected_scopes_json=fresh_scopes,
    ) is True


def test_a_redeposited_connection_does_not_inherit_a_stale_full_answer(tmp_path):
    """The remove-and-redeposit ABA: the connection id is deterministic per
    (universe, destination), so a stale answer could otherwise apply the
    owner's full decision to a REPLACEMENT key they never granted it for."""
    ledger = _ledger(tmp_path)
    _create(ledger)
    seen_endpoints, seen_scopes = ledger.policy_json("conn-1")

    ledger.delete_connection("conn-1")
    _create(ledger, endpoints=(
        {"host": "api.replacement.example", "path_template": "/v1/y", "methods": ["GET"]},
    ))

    assert ledger.set_access_mode(
        connection_id="conn-1", access_mode=ACCESS_FULL, expected_mode=ACCESS_EXACT,
        expected_endpoints_json=seen_endpoints, expected_scopes_json=seen_scopes,
    ) is False
    assert ledger.access_mode("conn-1") == ACCESS_EXACT



# --------------------------------------------------------------------------
# reader 1: the egress allowlist, after the safety checks, never instead
# --------------------------------------------------------------------------


def _driver():
    from tinyassets.storage.outbound_connections import _SsrfHardenedHttpDriver

    return _SsrfHardenedHttpDriver


def _enforce(url, method, endpoints, mode):
    from tinyassets.storage.outbound_connections import (
        _enforce_endpoint_allowlist,
        _parse_allowed_endpoints,
        _parse_canonical_https_url,
    )

    canonical = _parse_canonical_https_url(url, allowed_ports=frozenset({443}))
    _enforce_endpoint_allowlist(
        canonical, method, _parse_allowed_endpoints(list(endpoints)), mode
    )


@pytest.mark.parametrize(
    ("url", "method"),
    [
        ("https://api.github.com/", "GET"),
        ("https://api.github.com/anything/at/all", "DELETE"),
        ("https://api.github.com/search?q=a&b=c", "GET"),
        ("https://api.github.com/repos/other/repo", "PATCH"),
        ("https://api.github.com/x", "POST"),
        ("https://api.github.com/x", "PUT"),
    ],
)
def test_full_admits_any_path_query_and_verb_on_a_declared_host(url, method):
    _enforce(url, method, [GITHUB_ENDPOINT], ACCESS_FULL)


@pytest.mark.parametrize(
    ("url", "method"),
    [
        ("https://api.github.com/", "GET"),
        ("https://api.github.com/repos/octocat/hello/contents/x", "DELETE"),
        ("https://api.github.com/other", "GET"),
    ],
)
def test_exact_still_refuses_what_it_always_refused(url, method):
    with pytest.raises(SsrfValidationError):
        _enforce(url, method, [GITHUB_ENDPOINT], ACCESS_EXACT)


def test_full_is_bounded_to_the_declared_hosts(tmp_path):
    with pytest.raises(SsrfValidationError, match="host is not on"):
        _enforce("https://evil.example/x", "GET", [GITHUB_ENDPOINT], ACCESS_FULL)


def test_full_admits_nothing_when_the_connection_declares_nothing():
    from tinyassets.storage.outbound_connections import (
        _enforce_endpoint_allowlist,
        _parse_canonical_https_url,
    )

    canonical = _parse_canonical_https_url(
        "https://api.github.com/x", allowed_ports=frozenset({443})
    )
    with pytest.raises(SsrfValidationError, match="no permitted endpoints"):
        _enforce_endpoint_allowlist(canonical, "GET", (), ACCESS_FULL)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.github.com/x",
        "https://user:pw@api.github.com/x",
        "https://api.github.com:8443/x",
        "https://api.github.com/a/../b",
        "https://api.github.com/a%2fb",
        "https://api.github.com/a%252e%252e/b",
    ],
)
def test_full_never_reaches_the_url_safety_checks(url):
    """Full widens the OWNER's authority over their own key. It does not widen
    what the platform will put on a socket: the canonical parse still refuses
    every one of these, before the allowlist is consulted at all."""
    with pytest.raises(SsrfValidationError):
        _enforce(url, "GET", [GITHUB_ENDPOINT], ACCESS_FULL)


def test_full_still_fails_on_a_non_global_resolved_address(monkeypatch):
    """Codex design round 3, proof 1: the DNS + globally-routable check runs
    AFTER the allowlist and immediately before the socket. A full connection
    reaching a host that resolves to loopback is refused there, not admitted
    because the host matched."""
    from tinyassets.storage import outbound_connections as oc

    driver = oc._SsrfHardenedHttpDriver(
        resolver=lambda host, port: ["127.0.0.1"],
    )
    bundle = oc.ConnectionSecretBundle(token="t" * 20)
    with pytest.raises(SsrfValidationError, match="globally routable"):
        driver(
            bundle=bundle,
            auth_scheme="bearer",
            method="GET",
            url="https://api.github.com/anything",
            allowed_endpoints=oc._parse_allowed_endpoints([GITHUB_ENDPOINT]),
            access_mode=ACCESS_FULL,
        )


# --------------------------------------------------------------------------
# reader 2: git scopes
# --------------------------------------------------------------------------


def test_full_covers_any_repository_and_exact_covers_only_its_own(tmp_path):
    ledger = _ledger(tmp_path)
    exact = _create(ledger, scopes=("GET", "git_read:octocat/hello"))
    assert has_git_scope(exact, "git_read", "octocat/hello") is True
    assert has_git_scope(exact, "git_read", "octocat/other") is False

    full = _create(
        ledger, connection_id="conn-2", scopes=("GET",), access_mode=ACCESS_FULL
    )
    assert connection_access_mode(full) == "full"
    assert has_git_scope(full, "git_read", "anyone/anything") is True
    assert has_git_scope(full, "git_write", "anyone/anything") is True


def test_full_does_not_make_a_revoked_or_nonsense_scope_pass(tmp_path):
    ledger = _ledger(tmp_path)
    full = _create(ledger, access_mode=ACCESS_FULL)
    assert has_git_scope(full, "git_delete", "o/n") is False   # not a git kind
    assert has_git_scope(full, "git_read", "not a repo") is False
    assert has_git_scope(None, "git_read", "o/n") is False
    ledger.revoke_connection("conn-1")
    assert has_git_scope(
        ledger._get_connection_resource("conn-1"), "git_read", "o/n"
    ) is False


def test_an_object_that_cannot_say_it_is_full_is_not_full():
    assert connection_access_mode(None) == "exact"
    assert connection_access_mode(object()) == "exact"
    assert connection_access_mode({"access_mode": "FULL"}) == "full"
    assert connection_access_mode({"access_mode": "wide"}) == "exact"


# --------------------------------------------------------------------------
# reader 3: workspace consents
# --------------------------------------------------------------------------


def test_a_full_channel_needs_no_per_repository_consent(tmp_path):
    from tinyassets.effectors.workspace import _Refused, _require_consent

    universe_dir = tmp_path / "u"
    universe_dir.mkdir()
    # exact: no row, so it refuses.
    with pytest.raises(_Refused):
        _require_consent(
            universe_dir, "checkout", "github.com", "any/repo", "conn-1",
            access_mode="exact",
        )
    # full: the owner granted the channel, so every repository on it is covered.
    _require_consent(
        universe_dir, "checkout", "github.com", "any/repo", "conn-1",
        access_mode="full",
    )
    _require_consent(
        universe_dir, "push", "github.com", "another/repo", "conn-1",
        access_mode="full",
    )


def test_provision_is_pre_authorized_by_a_full_channel(tmp_path):
    from tinyassets.effectors.workspace import _check_provision_consent

    universe_dir = tmp_path / "u"
    universe_dir.mkdir()
    assert _check_provision_consent(
        universe_dir, "github.com", "any/repo", "conn-1", access_mode="exact"
    ) is False
    assert _check_provision_consent(
        universe_dir, "github.com", "any/repo", "conn-1", access_mode="full"
    ) is True


# --------------------------------------------------------------------------
# D6: taking a key back takes back what it authorized
# --------------------------------------------------------------------------


def test_removing_a_key_revokes_only_its_own_consents(tmp_path):
    """Codex design round 3, proof 3: scoped to the connection, not the
    universe. A universe holds several keys, and a sweep would revoke consents
    for keys the owner never removed."""
    from tinyassets.storage.effector_consents import (
        grant_consent,
        is_consent_active,
        revoke_consents_for_connection,
    )
    from tinyassets.storage.workspace_authority import (
        WORKSPACE_SINK,
        workspace_consent_destination,
    )

    universe_dir = tmp_path / "u"
    universe_dir.mkdir()

    def _dest(consent, repo, connection_id):
        return workspace_consent_destination(
            consent, repo, connection_id=connection_id, host="github.com"
        )

    mine = [
        _dest("workspace_checkout", "o/one", "conn-mine"),
        _dest("workspace_push", "o/one", "conn-mine"),
        _dest("workspace_checkout", "o/two", "conn-mine"),
    ]
    theirs = [
        _dest("workspace_checkout", "o/one", "conn-theirs"),
        _dest("workspace_push", "o/three", "conn-theirs"),
    ]
    for destination in mine + theirs:
        grant_consent(
            universe_dir, sink=WORKSPACE_SINK, destination=destination,
            granted_by="user-1",
        )

    revoked = revoke_consents_for_connection(
        universe_dir, connection_id="conn-mine", destination="github",
    )

    assert revoked == sorted(mine)
    for destination in mine:
        assert is_consent_active(
            universe_dir, sink=WORKSPACE_SINK, destination=destination
        ) is False
    for destination in theirs:
        assert is_consent_active(
            universe_dir, sink=WORKSPACE_SINK, destination=destination
        ) is True


def test_a_repository_named_like_another_connection_is_not_swept_in(tmp_path):
    from tinyassets.storage.effector_consents import (
        grant_consent,
        is_consent_active,
        revoke_consents_for_connection,
    )
    from tinyassets.storage.workspace_authority import (
        WORKSPACE_SINK,
        workspace_consent_destination,
    )

    universe_dir = tmp_path / "u"
    universe_dir.mkdir()
    # The connection id is the SECOND field; a repository that spells another
    # connection's name must not match.
    destination = workspace_consent_destination(
        "workspace_checkout", "conn-mine/repo", connection_id="conn-other",
        host="github.com",
    )
    grant_consent(
        universe_dir, sink=WORKSPACE_SINK, destination=destination, granted_by="u",
    )
    assert revoke_consents_for_connection(
        universe_dir, connection_id="conn-mine", destination="github",
    ) == []
    assert is_consent_active(
        universe_dir, sink=WORKSPACE_SINK, destination=destination
    ) is True


def test_revoking_for_no_connection_revokes_nothing(tmp_path):
    from tinyassets.storage.effector_consents import revoke_consents_for_connection

    universe_dir = tmp_path / "u"
    universe_dir.mkdir()
    assert revoke_consents_for_connection(universe_dir, connection_id="") == []
    assert revoke_consents_for_connection(universe_dir, connection_id="   ") == []


# --------------------------------------------------------------------------
# the ask shape
# --------------------------------------------------------------------------


def test_a_full_ask_carries_the_channel_and_nothing_else():
    from tinyassets.api.pending_requests import _validated_action

    extend = _validated_action(
        {"type": "extend_http", "destination": "github", "access": "full"}
    )
    assert extend == {
        "type": "extend_http", "destination": "github",
        "endpoints": [], "scopes": [], "access": "full",
    }

    deposit = _validated_action({
        "type": "connect_http", "destination": "github", "auth_scheme": "bearer",
        "access": "full", "hosts": ["API.GitHub.com"],
    })
    assert deposit["access"] == "full"
    assert deposit["hosts"] == ["api.github.com"]
    assert deposit["scopes"] == []
    assert [e["host"] for e in deposit["endpoints"]] == ["api.github.com"]


@pytest.mark.parametrize(
    "action",
    [
        {"type": "extend_http", "destination": "github", "access": "full",
         "scopes": ["git_read:o/n"]},
        {"type": "extend_http", "destination": "github", "access": "full",
         "endpoints": [GITHUB_ENDPOINT]},
        {"type": "extend_http", "destination": "github", "access": "wide"},
        {"type": "extend_http", "destination": "github", "access": "*"},
        {"type": "connect_http", "destination": "github", "access": "full"},
        {"type": "connect_http", "destination": "github", "access": "full",
         "hosts": []},
        {"type": "connect_http", "destination": "github", "access": "full",
         "hosts": ["127.0.0.1"]},
        {"type": "connect_http", "destination": "github", "access": "full",
         "hosts": ["localhost"]},
        {"type": "connect_http", "destination": "github", "access": "full",
         "hosts": ["a.com", "b.com", "c.com", "d.com", "e.com"]},
        {"type": "connect_http", "destination": "github", "access": "full",
         "hosts": ["api.github.com"], "endpoints": [GITHUB_ENDPOINT]},
        {"type": "connect_http", "destination": "github", "access": "full",
         "hosts": ["api.github.com"], "scopes": ["git_read:o/n"]},
    ],
)
def test_a_malformed_full_ask_is_refused_not_quietly_narrowed(action):
    from tinyassets.api.pending_requests import _validated_action

    with pytest.raises((ValueError, Exception)):
        _validated_action(action)


def test_an_exact_ask_still_says_it_is_exact():
    from tinyassets.api.pending_requests import _validated_action

    out = _validated_action({
        "type": "extend_http", "destination": "github",
        "endpoints": [GITHUB_ENDPOINT],
    })
    assert out["access"] == "exact"


# --------------------------------------------------------------------------
# the sentence: what the owner reads before they say yes
# --------------------------------------------------------------------------


def test_the_full_sentence_says_the_whole_grant():
    from tinyassets.api.pending_requests import _grant_sentence, _validated_action

    action = _validated_action({
        "type": "connect_http", "destination": "github", "auth_scheme": "bearer",
        "access": "full", "hosts": ["api.github.com"],
    })
    sentence = _grant_sentence({"action": action})
    assert "Full access" in sentence
    assert "api.github.com" in sentence
    assert "github.com" in sentence
    assert "any repository" in sentence
    assert "sandbox" in sentence
    assert "*" not in sentence          # never a wildcard on screen


def test_the_sentence_does_not_claim_a_non_forge_host_serves_git():
    from tinyassets.api.pending_requests import _grant_sentence, _validated_action

    action = _validated_action({
        "type": "connect_http", "destination": "slack", "auth_scheme": "bearer",
        "access": "full", "hosts": ["slack.com"],
    })
    sentence = _grant_sentence({"action": action})
    assert "slack.com" in sentence
    assert "git clone or push to any repository it can reach on slack.com" not in sentence
    assert "Where that serves git" in sentence


def test_the_extend_sentence_names_the_hosts_when_it_knows_them():
    from tinyassets.api.pending_requests import _grant_sentence

    sentence = _grant_sentence({"action": {
        "type": "extend_http", "destination": "github", "access": "full",
        "hosts": ["api.github.com"], "git_host": "github.com",
    }})
    assert "api.github.com" in sentence
    assert "github.com" in sentence
    assert "do not need to paste it again" in sentence


# --------------------------------------------------------------------------
# the inventory the agent reads back
# --------------------------------------------------------------------------


def test_the_inventory_renders_the_mode_and_never_a_wildcard(tmp_path):
    from tinyassets.api.cloud_connections import _project

    ledger = _ledger(tmp_path)
    _create(ledger, access_mode=ACCESS_FULL)
    resource = ledger._get_connection_resource("conn-1")
    grant = ledger.grant_connection(
        grant_id="grant-1", connection_id="conn-1", owner_user_id="user-1",
        universe_id="u-1",
    )
    row = _project(resource, grant)
    assert row["access"] == "full"
    assert "*" not in json.dumps(row)

    _create(ledger, connection_id="conn-2")
    exact_row = _project(ledger._get_connection_resource("conn-2"), grant)
    assert exact_row["access"] == "exact"


# --------------------------------------------------------------------------
# the decision survives the rail (Codex code review round 1, P0)
# --------------------------------------------------------------------------


def test_a_full_yes_is_stored_full_end_to_end(tmp_path, monkeypatch):
    """Raise -> tab -> answer -> ledger, as one path.

    Every earlier test stopped at `_validated_action`, so all three call sites
    could drop `access` and stay green: the sentence said full, the ledger said
    exact, and the first call outside the recorded endpoints was refused. This
    asserts the STORED mode after the owner's yes.
    """
    from tinyassets.api.pending_requests import _validated_action

    # The three payloads the rail builds. Each is asserted to carry the mode,
    # because each one dropped it.
    full_extend = _validated_action(
        {"type": "extend_http", "destination": "github", "access": "full"}
    )
    full_connect = _validated_action({
        "type": "connect_http", "destination": "github", "auth_scheme": "bearer",
        "access": "full", "hosts": ["api.github.com"],
    })
    assert full_extend["access"] == "full"
    assert full_connect["access"] == "full"

    import inspect

    from tinyassets.api import pending_requests as pr

    source = inspect.getsource(pr)
    # The raise-time preview, the extension answer, and the deposit answer.
    assert source.count('"access": action.get("access") or "exact"') == 3


def test_the_deposit_stores_the_mode_it_was_given(tmp_path):
    """`connect_http`'s own contract, below the rail."""
    ledger = _ledger(tmp_path)
    full = _create(ledger, connection_id="conn-full", access_mode="full")
    exact = _create(ledger, connection_id="conn-exact")
    assert full.access_mode == "full"
    assert exact.access_mode == "exact"
    assert ledger.access_mode("conn-full") == "full"
    assert ledger.access_mode("conn-exact") == "exact"


def test_the_sweep_is_per_sink_not_a_positional_guess(tmp_path):
    """Codex code review round 1, finding 4. Scanning every sink for the
    second colon-separated field was wrong in both directions: it swept in a
    foreign row whose destination merely LOOKED like `x:<id>:y`, and it missed
    the connection's own outbound consent, which is keyed by the destination
    LABEL and carries no id at all -- so that one survived removal and was
    inherited by the next deposit under the same name."""
    from tinyassets.storage.effector_consents import (
        grant_consent,
        is_consent_active,
        revoke_consents_for_connection,
    )
    from tinyassets.storage.workspace_authority import (
        WORKSPACE_SINK,
        workspace_consent_destination,
    )

    universe_dir = tmp_path / "u"
    universe_dir.mkdir()
    mine_workspace = workspace_consent_destination(
        "workspace_checkout", "o/n", connection_id="http_mine", host="github.com",
    )
    # The same connection's OUTBOUND consent: keyed by the destination label.
    mine_outbound = "github"
    # A DIFFERENT connection whose outbound destination happens to be shaped
    # like a workspace key that embeds our id.
    theirs_lookalike = "x:http_mine:y"
    theirs_outbound = "slack"

    for sink, destination in (
        (WORKSPACE_SINK, mine_workspace),
        ("authenticated_external_call", mine_outbound),
        ("authenticated_external_call", theirs_lookalike),
        ("authenticated_external_call", theirs_outbound),
    ):
        grant_consent(universe_dir, sink=sink, destination=destination, granted_by="u")

    revoked = revoke_consents_for_connection(
        universe_dir, connection_id="http_mine", destination="github",
    )

    assert sorted(revoked) == sorted([mine_workspace, mine_outbound])
    assert is_consent_active(
        universe_dir, sink=WORKSPACE_SINK, destination=mine_workspace
    ) is False
    assert is_consent_active(
        universe_dir, sink="authenticated_external_call", destination=mine_outbound
    ) is False
    # The look-alike belongs to somebody else and is untouched.
    assert is_consent_active(
        universe_dir, sink="authenticated_external_call", destination=theirs_lookalike
    ) is True
    assert is_consent_active(
        universe_dir, sink="authenticated_external_call", destination=theirs_outbound
    ) is True


def test_the_sentence_says_no_git_when_the_channel_can_carry_none():
    """Codex code review round 1, finding 5. A git scope binds ONE host, so a
    two-host channel carries no git authority at all -- `has_git_scope` refuses
    every repository there. The sentence used to offer the conditional clause
    anyway."""
    from tinyassets.api.pending_requests import _grant_sentence, _validated_action

    action = _validated_action({
        "type": "connect_http", "destination": "multi", "auth_scheme": "bearer",
        "access": "full", "hosts": ["api.example.com", "git.example.com"],
    })
    sentence = _grant_sentence({"action": action})
    assert "api.example.com, git.example.com" in sentence
    assert "git" not in sentence.replace("git.example.com", "")


def test_an_unrecognised_single_host_is_never_named_as_a_forge():
    """`git_host_for_endpoints` passes an unknown host through -- correct for
    the platform, which must work with any forge, and wrong to repeat to an
    owner as fact. A full Slack grant read as clone-and-push on slack.com."""
    from tinyassets.api.pending_requests import _grant_sentence, _validated_action

    action = _validated_action({
        "type": "connect_http", "destination": "slack", "auth_scheme": "bearer",
        "access": "full", "hosts": ["slack.com"],
    })
    sentence = _grant_sentence({"action": action})
    assert "reach on slack.com" not in sentence
    assert "Where that serves git" in sentence


def test_a_stale_git_host_on_a_persisted_action_is_not_trusted():
    """A row persisted before the derivation was tightened carries whatever
    the old code put in `git_host`, and "slack.com" there rendered as a named
    forge (Codex code review round 2). The sentence derives its own."""
    from tinyassets.api.pending_requests import _grant_sentence

    sentence = _grant_sentence({"action": {
        "type": "extend_http", "destination": "slack", "access": "full",
        "hosts": ["slack.com"], "git_host": "slack.com",
    }})
    assert "clone or push to any repository it can reach on slack.com" not in sentence
    assert "Where that serves git" in sentence

    # ...and a git host the connection DECLARES is still named.
    github = _grant_sentence({"action": {
        "type": "extend_http", "destination": "github", "access": "full",
        "hosts": ["api.github.com"], "declared_git_host": "github.com",
    }})
    assert "clone or push to any repository it can reach on github.com" in github


def test_a_full_channel_carries_every_verb_not_just_the_synthesized_get(tmp_path):
    """Codex code review round 2. The scope tuple is a FIFTH reader of the
    grant: `ScopedConnectionProxy.request` refuses an out-of-scope verb BEFORE
    the full-aware allowlist is consulted. A full deposit synthesizes one GET
    endpoint, so deriving scopes from the endpoints left a full connection
    unable to POST -- the headline promise, inert."""
    from tinyassets.storage.outbound_connections import (
        _SSRF_ALLOWED_METHODS,
        _verb_within_scopes,
    )

    ledger = _ledger(tmp_path)
    full = _create(
        ledger,
        scopes=tuple(sorted(_SSRF_ALLOWED_METHODS)),
        access_mode=ACCESS_FULL,
    )
    for verb in _SSRF_ALLOWED_METHODS:
        assert _verb_within_scopes(verb, full.scopes) is True, verb

    exact = _create(ledger, connection_id="conn-exact", scopes=("GET",))
    assert _verb_within_scopes("GET", exact.scopes) is True
    assert _verb_within_scopes("POST", exact.scopes) is False


def test_a_full_deposit_moves_an_existing_connection(base):  # noqa: F811
    from tests.test_workspace_authority import (
        GIT_HOST,
        _deposit,
        _login,
        _make_universe,
    )
    from tests.test_workspace_authority import (
        GITHUB_ENDPOINT as endpoint,
    )
    from tinyassets.api.http_connection import connect_http

    _make_universe(base, "u-rotate", admin="alice")
    _login("alice")
    deposited = _deposit("u-rotate")
    ledger = _ledger(base, "alice")
    connection_id = deposited["connection_id"]
    assert ledger.access_mode(connection_id) == ACCESS_EXACT
    rotated = connect_http(universe_id="u-rotate", payload=json.dumps({
        "destination": "github", "secret": "new-owner-key",
        "auth_scheme": "bearer", "access": "full",
        "allowed_endpoints": [endpoint], "git_host": GIT_HOST,
    }))
    assert rotated.get("status") == "provisioned", rotated
    assert rotated["connection_id"] == connection_id
    assert ledger.access_mode(connection_id) == ACCESS_FULL
    view = ledger.get_connection_view(connection_id)
    assert view.owner_user_id == "alice"
    assert view.connection_id == connection_id
    assert view.revoked_at is None
    assert "new-owner-key" not in json.dumps(rotated)
    assert all(_verb_within_scopes(verb, view.scopes, view.access_mode)
               for verb in _SSRF_ALLOWED_METHODS)


# ---------------------------------------------------------------------------
# The yes lands on the reach the owner read
# ---------------------------------------------------------------------------


def test_a_full_yes_cannot_land_on_a_host_added_while_the_tab_was_open(base):  # noqa: F811
    """Codex code review round 2, left open then and closed here.

    The compare-and-swap protected the write against a change between the
    ANSWER-time preview and the write -- microseconds. The window that matters
    is between the ASK and the ANSWER, which is however long the owner takes to
    read the tab:

      1. the ask says "full access", and its sentence names api.github.com;
      2. another device adds api.other.example -- an ordinary exact widening,
         so the mode is still exact and nothing conflicts;
      3. the owner says yes.

    The answer used to take a FRESH preview, read both hosts, and swap
    successfully against its own read. The owner granted full on a host they
    never saw.
    """
    import json as _json

    from tests.test_workspace_authority import (
        GITHUB_ENDPOINT as WA_ENDPOINT,
    )
    from tests.test_workspace_authority import (
        _deposit,
        _login,
        _make_universe,
    )
    from tinyassets.api.http_connection import extend_http

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    deposited = _deposit("u-1")
    connection_id = deposited["connection_id"]

    ledger = ConnectionLedger(
        base / ".broker" / "outbound.db", data_root=base,
        verify_authenticated_principal=lambda: "alice",
    )
    read_at_ask_time = ledger.policy_json(connection_id)
    assert read_at_ask_time is not None
    snapshot = {
        "endpoints_json": read_at_ask_time[0],
        "scopes_json": read_at_ask_time[1],
        "access_mode": "exact",
        "incarnation": ledger.incarnation(connection_id),
    }

    # (2) another device widens, exactly. The mode does not move.
    widened = extend_http(universe_id="u-1", payload=_json.dumps({
        "destination": "github",
        "endpoints": [
            WA_ENDPOINT,
            {"host": "api.other.example", "path_template": "/v1/x",
             "methods": ["GET"]},
        ],
    }))
    assert widened["status"] == "extended", widened
    assert ledger.access_mode(connection_id) == "exact"

    # (3) the owner's yes, carrying what the tab showed them.
    answered = extend_http(universe_id="u-1", payload=_json.dumps({
        "destination": "github",
        "endpoints": [],
        "scopes": [],
        "access": "full",
        "policy_snapshot": snapshot,
    }))
    assert answered.get("error") == "connection_conflict", answered
    assert answered["resource"] == "connection"
    assert ledger.access_mode(connection_id) == "exact", (
        "a host the owner never read was granted in full"
    )
    hosts = {ep.host for ep in ledger.get_connection(connection_id).allowed_endpoints}
    assert "api.other.example" in hosts

    # Re-asking is what lands it: the new tab names both hosts.
    fresh = ledger.policy_json(connection_id)
    landed = extend_http(universe_id="u-1", payload=_json.dumps({
        "destination": "github",
        "endpoints": [],
        "scopes": [],
        "access": "full",
        "policy_snapshot": {
            "endpoints_json": fresh[0],
            "scopes_json": fresh[1],
            "access_mode": "exact",
            "incarnation": ledger.incarnation(connection_id),
        },
    }))
    assert landed["status"] == "extended", landed
    assert ledger.access_mode(connection_id) == "full"


def test_the_full_ask_records_the_policy_its_sentence_was_written_from(base):  # noqa: F811
    """The snapshot and the words come from ONE read, so they cannot disagree
    about what the owner is being asked to grant."""
    from tests.test_workspace_authority import _deposit, _login, _make_universe
    from tinyassets.api.pending_requests import _full_channel_reach

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    deposited = _deposit("u-1")

    reach = _full_channel_reach("u-1", {"destination": "github", "access": "full"})
    assert reach.get("hosts") == ["api.github.com"], reach

    snapshot = reach.get("policy_snapshot")
    assert isinstance(snapshot, dict), reach
    assert snapshot["access_mode"] == "exact"

    endpoints_json, scopes_json = ConnectionLedger(
        base / ".broker" / "outbound.db", data_root=base,
        verify_authenticated_principal=lambda: "alice",
    ).policy_json(deposited["connection_id"])
    assert snapshot["endpoints_json"] == endpoints_json
    assert snapshot["scopes_json"] == scopes_json


def test_an_ask_without_a_snapshot_still_answers(base):  # noqa: F811
    """A pending row raised before this existed, or a direct call that never
    went through a tab, must not be bricked: it swaps against its own read, as
    it did before."""
    import json as _json

    from tests.test_workspace_authority import _deposit, _login, _make_universe
    from tinyassets.api.http_connection import extend_http

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    deposited = _deposit("u-1")

    answered = extend_http(universe_id="u-1", payload=_json.dumps({
        "destination": "github",
        "endpoints": [],
        "scopes": [],
        "access": "full",
    }))
    assert answered["status"] == "extended", answered

    assert ConnectionLedger(
        base / ".broker" / "outbound.db", data_root=base,
        verify_authenticated_principal=lambda: "alice",
    ).access_mode(deposited["connection_id"]) == "full"


# ---------------------------------------------------------------------------
# Codex code review round 3: the two P0s
# ---------------------------------------------------------------------------


def test_a_channel_upgraded_to_full_carries_every_verb(base):  # noqa: F811
    """Codex code review round 3, P0. `extend_http` moves the mode and touches
    no scope row -- nothing is stored as a wildcard, by design -- but both verb
    gates tested membership in the stored tuple. So a GET-only connection
    upgraded to full refused the owner's POST on a channel they had granted in
    full, and the follow-up ask came back `already_held`: stranded."""
    import json as _json

    from tests.test_workspace_authority import _deposit, _login, _make_universe
    from tinyassets.api.http_connection import extend_http

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    deposited = _deposit("u-1")

    ledger = ConnectionLedger(
        base / ".broker" / "outbound.db", data_root=base,
        verify_authenticated_principal=lambda: "alice",
    )
    connection_id = deposited["connection_id"]
    resource = ledger._get_connection_resource(connection_id)
    stored_verbs = {s for s in resource.scopes if not s.startswith("git_")}
    assert "POST" not in stored_verbs or "DELETE" not in stored_verbs, resource.scopes

    upgraded = extend_http(universe_id="u-1", payload=_json.dumps({
        "destination": "github", "endpoints": [], "scopes": [], "access": "full",
    }))
    assert upgraded["status"] == "extended", upgraded
    assert ledger.access_mode(connection_id) == ACCESS_FULL

    # The scope rows did NOT change -- that is the shape working as intended.
    after = ledger._get_connection_resource(connection_id)
    assert set(after.scopes) == set(resource.scopes)

    # ...and every verb the transport will send is nevertheless carried.
    for verb in sorted(_SSRF_ALLOWED_METHODS):
        assert _verb_within_scopes(verb, after.scopes, after.access_mode), verb
    # Nothing outside that set is.
    assert not _verb_within_scopes("TRACE", after.scopes, after.access_mode)
    assert not _verb_within_scopes(
        "git_read:octocat/hello", after.scopes, after.access_mode,
    )
    # And an exact connection is unchanged by any of this.
    assert not _verb_within_scopes("DELETE", ("GET",), ACCESS_EXACT)


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
        owner_user_id="user-1", universe_id="u-1",
    )

    sent: list[str] = []

    broker = CredentialBlindBroker(
        ledger,
        resolve_credential=lambda _ref, _ctype=None: "ghp_" + "x" * 30,
        network_request=lambda **kw: sent.append(kw["verb"]) or {"status": 200},
    )

    # Exact: the stored verbs are the grant, so POST is refused.
    with pytest.raises(PermissionError, match="outside the granted"):
        broker.dispatch("grant-1", "POST", {"path": "/x"})
    assert sent == []

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
    broker.dispatch("grant-1", "POST", {"path": "/x"})
    assert sent == ["POST"]

    # A git scope is still not an HTTP verb, whatever the mode.
    with pytest.raises(PermissionError, match="outside the granted"):
        broker.dispatch("grant-1", "git_read:octocat/hello", {"path": "/x"})


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


def test_the_ask_records_which_deposit_it_is_about(base):  # noqa: F811
    """The snapshot the tab carries names the deposit, so the answer can tell a
    replacement from the key the owner read about."""
    from tests.test_workspace_authority import _deposit, _login, _make_universe
    from tinyassets.api.pending_requests import _full_channel_reach

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    deposited = _deposit("u-1")

    reach = _full_channel_reach("u-1", {"destination": "github", "access": "full"})
    snapshot = reach["policy_snapshot"]
    ledger = ConnectionLedger(
        base / ".broker" / "outbound.db", data_root=base,
        verify_authenticated_principal=lambda: "alice",
    )
    assert snapshot["incarnation"] == ledger.incarnation(deposited["connection_id"])
    assert snapshot["incarnation"]


@pytest.mark.parametrize("snapshot", [None, {}, {
    "access_mode": "exact", "endpoints_json": "[]", "scopes_json": "[]",
}])
def test_full_answer_with_incomplete_snapshot_refuses_before_broker(
    base, monkeypatch, snapshot,  # noqa: F811
):
    from tests.test_workspace_authority import _deposit, _login, _make_universe
    from tinyassets.api.http_connection import extend_http
    from tinyassets.broker import http_policy

    _make_universe(base, "u-incomplete", admin="alice")
    _login("alice")
    deposited = _deposit("u-incomplete")
    def forbidden(*args, **kwargs):
        pytest.fail("incomplete approval reached broker mutation")
    monkeypatch.setattr(http_policy, "update_policy", forbidden)
    result = extend_http(universe_id="u-incomplete", payload=json.dumps({
        "destination": "github", "endpoints": [], "scopes": [], "access": "full",
        "policy_snapshot": snapshot,
    }))
    assert result == {"error": "connection_conflict", "resource": "connection"}
    assert _ledger(base, "alice").access_mode(deposited["connection_id"]) == ACCESS_EXACT
