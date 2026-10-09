"""Real temporary owner/request/ledger path for redirect consent, with no network."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.owner_answer import answer_request
from tests.test_pending_requests import (  # noqa: F401
    _login,
    _make_universe,
    _reset_auth,
)
from tests.test_pending_requests import (
    base as base,
)
from tinyassets.api.http_connection import _ids, extend_http
from tinyassets.api.pending_requests import request_from_user
from tinyassets.storage.outbound_connections import ConnectionLedger
from tinyassets.storage.pending_requests import create_request, get_request

SOURCE = {"host": "api.example.com", "path_template": "/download", "methods": ["GET"]}
OPTED = {**SOURCE, "redirect_mode": "public_https_get"}


def _seed(base, mode):
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    ledger = ConnectionLedger(base / ".broker" / "outbound.db", data_root=base,
    verify_authenticated_principal=lambda: "alice")
    cid, gid = _ids(universe_id="u-1", destination="downloads")
    ledger.create_connection(
        connection_id=cid,
        owner_user_id="alice",
        connection_class="http",
        connection_type="http",
        auth_scheme="bearer",
        scopes=("GET", "POST"),
        provider="http",
        destination="downloads",
        credential_ref="vault://http/downloads",
        allowed_endpoints=[SOURCE],
        access_mode=mode,
    )
    ledger.grant_connection(
        grant_id=gid, connection_id=cid, owner_user_id="alice", universe_id="u-1"
    )
    return ledger, cid


def _ask(endpoint=OPTED):
    return request_from_user(
        universe_id="u-1",
        payload={
            "kind": "permission",
            "title": "Allow redirected downloads",
            "fields": [],
            "action": {"type": "extend_http", "destination": "downloads", "endpoints": [endpoint]},
        },
    )


@pytest.mark.parametrize("mode", ["exact", "full"])
def test_legacy_connection_can_request_redirect_consent_without_redeposit(base, mode):
    ledger, cid = _seed(base, mode)
    # The old column migration and rollback writers leave the default empty.
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE outbound_connections SET incarnation = '' WHERE connection_id = ?",
            (cid,),
        )
    before = ledger._get_connection_resource(cid)
    asked = _ask()
    assert "request_id" in asked, asked
    snapshot = get_request(base / "u-1", asked["request_id"])["action"]["policy_snapshot"]
    assert snapshot["incarnation"]
    assert snapshot["incarnation"] == ledger.incarnation(cid)
    # Creating the request must not itself grant the additional permission.
    assert ledger._get_connection_resource(cid) == before
    done = answer_request(
        universe_id="u-1", payload={"request_id": asked["request_id"], "values": {}}
    )
    assert done.get("status") == "answered", done
    after = ledger._get_connection_resource(cid)
    assert after.access_mode == before.access_mode
    assert after.scopes == before.scopes
    assert after.credential_ref == before.credential_ref
    assert any(e.redirect_mode == "public_https_get" for e in after.allowed_endpoints)


def test_legacy_identity_repair_is_stable_and_changes_no_policy_or_grants(base):
    ledger, cid = _seed(base, "full")
    with ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET incarnation = '', revoked_at = 123")
        conn.execute("UPDATE outbound_connection_grants SET revoked_at = 456")
        before = dict(conn.execute("SELECT * FROM outbound_connections").fetchone())
        grants = [dict(row) for row in conn.execute("SELECT * FROM outbound_connection_grants")]

    def reopen(_):
        return ConnectionLedger(base / ".broker" / "outbound.db", data_root=base).incarnation(cid)

    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(reopen, range(12)))
    assert tokens[0]
    assert len(set(tokens)) == 1
    assert len(tokens[0]) == 32
    assert all(char in "0123456789abcdef" for char in tokens[0])
    with ledger._connect() as conn:
        after = dict(conn.execute("SELECT * FROM outbound_connections").fetchone())
        after_grants = [
            dict(row) for row in conn.execute("SELECT * FROM outbound_connection_grants")
        ]
        assert after_grants == grants
    assert after == {**before, "incarnation": tokens[0]}


def test_repair_covers_rollback_rows_and_never_reuses_previous_identity(base):
    ledger, cid = _seed(base, "exact")
    original_token = ledger.incarnation(cid)
    asked = _ask()
    assert "request_id" in asked
    with ledger._connect() as conn:
        # Simulate a rollback writer recreating the same id and policy without
        # the new field. A previous approval must not attach to that generation.
        conn.execute("UPDATE outbound_connections SET incarnation = ''")
    reopened = ConnectionLedger(base / ".broker" / "outbound.db", data_root=base)
    replacement_token = reopened.incarnation(cid)
    assert replacement_token and replacement_token != original_token
    ledger = ConnectionLedger(base / ".broker" / "outbound.db", data_root=base)
    assert ledger.incarnation(cid) == replacement_token
    refused = answer_request(
        universe_id="u-1", payload={"request_id": asked["request_id"], "values": {}}
    )
    assert refused.get("error") == "connection_conflict", refused
    assert all(e.redirect_mode == "none" for e in reopened.get_connection(cid).allowed_endpoints)


@pytest.mark.parametrize("mode", ["exact", "full"])
def test_owner_can_approve_redirects_without_redeposit_or_mode_scope_loss(base, mode):
    ledger, cid = _seed(base, mode)
    before = ledger._get_connection_resource(cid)
    asked = _ask()
    assert "request_id" in asked, asked
    assert "public HTTPS redirects" in asked["grant_sentence"]
    row = get_request(base / "u-1", asked["request_id"])
    snapshot = row["action"]["policy_snapshot"]
    assert snapshot["access_mode"] == mode
    assert snapshot["incarnation"] == ledger.incarnation(cid)
    assert (snapshot["endpoints_json"], snapshot["scopes_json"]) == ledger.policy_json(cid)
    done = answer_request(
        universe_id="u-1", payload={"request_id": asked["request_id"], "values": {}}
    )
    assert done.get("status") == "answered", done
    after = ledger._get_connection_resource(cid)
    assert after.access_mode == mode
    assert after.scopes == before.scopes
    assert after.credential_ref == before.credential_ref
    assert any(e.redirect_mode == "public_https_get" for e in after.allowed_endpoints)
    assert _ask().get("status") == "already_held"


@pytest.mark.parametrize("mode", ["exact", "full"])
@pytest.mark.parametrize(
    "changed", ["access_mode", "scopes_json", "allowed_endpoints_json", "incarnation"]
)
def test_owner_approval_refuses_changed_displayed_policy(base, mode, changed):
    ledger, cid = _seed(base, mode)
    asked = _ask()
    assert "request_id" in asked, asked
    replacement = {
        "access_mode": "full" if mode == "exact" else "exact",
        "scopes_json": json.dumps(["GET", "POST", "PUT"]),
        "allowed_endpoints_json": json.dumps([SOURCE, {**SOURCE, "path_template": "/other"}]),
        "incarnation": "replacement-deposit",
    }[changed]
    with ledger._connect() as conn:
        conn.execute(
            f"UPDATE outbound_connections SET {changed} = ? WHERE connection_id = ?",
            (replacement, cid),
        )
    done = answer_request(
        universe_id="u-1", payload={"request_id": asked["request_id"], "values": {}}
    )
    assert done.get("error") == "connection_conflict", done
    assert all(e.redirect_mode == "none" for e in ledger.get_connection(cid).allowed_endpoints)


@pytest.mark.parametrize("mode", ["exact", "full"])
def test_redirect_extension_without_displayed_snapshot_cannot_execute(base, mode):
    ledger, cid = _seed(base, mode)
    out = extend_http(universe_id="u-1", payload={"destination": "downloads", "endpoints": [OPTED]})
    assert out.get("error") == "connection_conflict", out
    assert all(e.redirect_mode == "none" for e in ledger.get_connection(cid).allowed_endpoints)


def test_full_connection_does_not_gain_another_authenticated_host_through_redirect_extension(base):
    _seed(base, "full")
    refused = _ask({**OPTED, "host": "different.example.com"})
    assert refused.get("error") == "ask_cannot_be_granted", refused


@pytest.mark.parametrize("mode", ["exact", "full"])
@pytest.mark.parametrize(
    "changed", ["mode", "incarnation", "scopes", "endpoints", "revoked", "grant"]
)
def test_approval_cannot_race_a_policy_change_or_revocation_at_the_sql_write(
    base,
    monkeypatch,
    mode,
    changed,
):
    ledger, cid = _seed(base, mode)
    asked = _ask()
    original = ConnectionLedger.extend_http_connection_endpoints

    def change_then_write(self, **kwargs):
        with self._connect() as conn:
            if changed == "mode":
                conn.execute(
                    "UPDATE outbound_connections SET access_mode = ?",
                    ("full" if mode == "exact" else "exact",),
                )
            elif changed == "incarnation":
                conn.execute("UPDATE outbound_connections SET incarnation = 'new-deposit'")
            elif changed == "scopes":
                conn.execute(
                    "UPDATE outbound_connections SET scopes_json = ?", (json.dumps(["GET"]),)
                )
            elif changed == "endpoints":
                conn.execute(
                    "UPDATE outbound_connections SET allowed_endpoints_json = ?",
                    (json.dumps([{**SOURCE, "path_template": "/different"}]),),
                )
            elif changed == "revoked":
                conn.execute("UPDATE outbound_connections SET revoked_at = 1")
            else:
                conn.execute("UPDATE outbound_connection_grants SET revoked_at = 1")
        return original(self, **kwargs)

    monkeypatch.setattr(ConnectionLedger, "extend_http_connection_endpoints", change_then_write)
    refused = answer_request(
        universe_id="u-1",
        payload={
            "request_id": asked["request_id"],
            "values": {},
        },
    )
    assert refused.get("error") == "connection_conflict", refused
    assert all(e.redirect_mode == "none" for e in ledger.get_connection(cid).allowed_endpoints)


@pytest.mark.parametrize("missing", ["access_mode", "endpoints_json", "scopes_json", "incarnation"])
def test_partial_snapshot_cannot_authorize_redirects(base, missing):
    ledger, cid = _seed(base, "exact")
    asked = _ask()
    snapshot = get_request(base / "u-1", asked["request_id"])["action"]["policy_snapshot"]
    snapshot.pop(missing)
    refused = extend_http(
        universe_id="u-1",
        payload={
            "destination": "downloads",
            "endpoints": [OPTED],
            "policy_snapshot": snapshot,
        },
    )
    assert refused.get("error") == "connection_conflict", refused
    assert all(e.redirect_mode == "none" for e in ledger.get_connection(cid).allowed_endpoints)


def test_reordered_equivalent_redirect_requests_reuse_the_displayed_consent(base):
    _seed(base, "exact")
    endpoints = [OPTED, {**OPTED, "path_template": "/other"}]

    def ask(rows):
        return request_from_user(
            universe_id="u-1",
            payload={
                "kind": "permission",
                "title": "Allow redirected downloads",
                "fields": [],
                "action": {"type": "extend_http", "destination": "downloads", "endpoints": rows},
            },
        )

    first = ask(endpoints)
    second = ask(list(reversed(endpoints)))
    assert first["request_id"] == second["request_id"]


def test_prior_no_follow_approval_does_not_satisfy_redirect_request(base):
    ledger, cid = _seed(base, "exact")
    new_source = {**SOURCE, "path_template": "/second"}
    old = _ask(new_source)
    assert (
        answer_request(
            universe_id="u-1",
            payload={
                "request_id": old["request_id"],
                "values": {},
            },
        ).get("status")
        == "answered"
    )
    fresh = _ask({**new_source, "redirect_mode": "public_https_get"})
    assert "request_id" in fresh, fresh
    assert fresh["request_id"] != old["request_id"]
    assert all(e.redirect_mode == "none" for e in ledger.get_connection(cid).allowed_endpoints)


@pytest.mark.parametrize("kind", ["connect_http", "extend_http"])
def test_legacy_pending_request_cannot_acquire_redirect_authority_on_upgrade(base, kind):
    ledger, cid = _seed(base, "exact")
    action = {"type": kind, "destination": "downloads", "endpoints": [OPTED]}
    fields = []
    if kind == "connect_http":
        action["auth_scheme"] = "bearer"
        fields = [{"name": "secret", "type": "secret", "label": "API key"}]
    title, body = "Old pending permission", "Saved by the old server"
    # A genuine internally consistent legacy row, not a tampered modern row:
    # its normal row-integrity check passes but it lacks versioned disclosure.
    old = create_request(
        base / "u-1",
        kind="permission",
        title=title,
        body=body,
        fields=fields,
        action=action,
        dedupe_key=json.dumps(
            ["permission", title, body, fields, action],
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    refused = answer_request(
        universe_id="u-1",
        payload={
            "request_id": old["request_id"],
            "values": {},
        },
    )
    assert refused.get("error") == "request_invalid", refused
    assert "did not disclose" in refused["detail"]
    assert all(e.redirect_mode == "none" for e in ledger.get_connection(cid).allowed_endpoints)


def test_new_connection_deposit_uses_the_disclosed_redirect_policy(base):
    ledger, _ = _seed(base, "exact")
    asked = request_from_user(
        universe_id="u-1",
        payload={
            "kind": "API",
            "title": "Connect the download service",
            "action": {
                "type": "connect_http",
                "destination": "new-downloads",
                "auth_scheme": "bearer",
                "endpoints": [OPTED],
            },
            "fields": [{"name": "secret", "type": "secret", "label": "API key"}],
        },
    )
    assert "public HTTPS redirects" in asked["grant_sentence"]
    assert get_request(base / "u-1", asked["request_id"])["action"]["redirect_consent_version"] == 1
    done = answer_request(
        universe_id="u-1",
        payload={
            "request_id": asked["request_id"],
            "values": {"secret": "synthetic-download-test-key"},
        },
    )
    assert done.get("status") == "answered", done
    cid, _ = _ids(universe_id="u-1", destination="new-downloads")
    assert ledger.get_connection(cid).allowed_endpoints[0].redirect_mode == "public_https_get"
