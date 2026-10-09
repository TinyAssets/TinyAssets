"""A key the far side stopped accepting, and the one card that replaces it.

Live failure, 2026-09-16. A connection's stored credential died on the provider
side (one fine-grained token hit its expiry, another was revoked by its user).
The platform sent it correctly and the far side answered 401 — and then three
things went wrong in a row:

1. the 401 landed in the generic ``external_write_failed``, which is
   ``actionable_by: chatbot`` and whose action says "fix that and run again
   yourself", so the universe retried the same dead key and then narrated
   "please reconnect" in chat prose, raising no card at all;
2. there was no one-step replace — only remove-then-connect re-carrying every
   endpoint and scope — and the owner read "remove" as deletion and dismissed
   three such cards;
3. the connection stayed dead for ten days.

So the tests here are about the two halves of that: the CLASS (which decides
what the agent is told to do) and the CARD (which has to change the key and
nothing else).
"""
from __future__ import annotations

import json
import logging

import pytest

from tests.test_authenticated_external_call_effector import (  # noqa: F401
    _a_reviewer_that_approves,  # autouse: an explicit approving D1d reviewer
)
from tests.test_pending_requests import (  # noqa: F401 - fixtures and harness
    _ask,
    _login,
    _make_universe,
    _owner_answer,
    _rail,
    _reset_auth,
)

SECRET = "old-" + "a" * 40
REPLACEMENT = "new-" + "b" * 40


@pytest.fixture
def base(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    return root


def _summary(status: int, body: str, kind: str = "far_side_error") -> str:
    """One row of a run's ``error``, exactly as the effect layer writes it.

    Built from the real composer rather than hand-typed, so a change to the
    message shape breaks this test instead of silently making the classifier
    unreachable.
    """
    from tinyassets.runs import _external_write_error_summary

    return _external_write_error_summary([{
        "node_id": "call", "sink": "authenticated_external_call",
        "error": f"far side answered HTTP {status}: {body}", "error_kind": kind,
    }]).lower()


def _classify(status: int, body: str, kind: str = "far_side_error") -> str:
    from tinyassets.runs import _classify_external_write

    return _classify_external_write(_summary(status, body, kind))


# --------------------------------------------------------------------------- #
# The class
# --------------------------------------------------------------------------- #
def test_a_delivered_401_is_a_rejected_credential():
    """Unconditionally, whatever the body says.

    RFC 7235: a 401 means the request lacked valid authentication credentials.
    There is no 401 that retrying the same secret fixes, so the body does not
    get a vote.
    """
    assert _classify(401, '{"message":"Bad credentials"}') == "credential_rejected"
    assert _classify(401, "") == "credential_rejected"
    assert _classify(401, "something nobody has seen before") == "credential_rejected"


def test_the_rejected_credential_is_the_users_to_fix():
    """`external_write_failed` is `chatbot` — "yours to fix, run again" — which
    is what made the universe retry a dead key twice and then stop."""
    from tinyassets.api.runs import _actionable_by

    assert _actionable_by("credential_rejected") == "user"
    assert _actionable_by("external_write_failed") == "chatbot"


def test_a_revoked_token_no_longer_reads_as_an_authority_refusal():
    """The ordering that matters most, and the one a live key actually hit.

    A revoked token's own body contains the word "revoked", which
    ``_EXTERNAL_WRITE_REFUSED_WORDS`` reads as an authority refusal — so before
    this the agent was told to raise ``extend_http``, widening a grant that was
    never the problem. The class has to be decided before that heuristic runs.
    """
    assert _classify(401, '{"serviceErrorCode":"REVOKED_ACCESS_TOKEN"}') == (
        "credential_rejected"
    )


@pytest.mark.parametrize("body", [
    '{"error":"invalid_token","error_description":"The access token expired"}',
    '{"message":"expired_access_token"}',
    '{"ok":false,"error":"invalid_auth"}',
    '{"errors":[{"code":89,"message":"Invalid or expired token"}]}',
    '{"message":"Bad credentials"}',
    '{"message":"The API key is invalid"}',
])
def test_a_403_that_says_the_key_itself_is_dead(body):
    """Real strings from real APIs. The rule is generic vocabulary only — a
    credential noun and a finality word within one word of each other."""
    assert _classify(403, body) == "credential_rejected", body


@pytest.mark.parametrize("body,expected", [
    # The decoys. Each one is a 403 that mentions a credential and is NOT about
    # the credential's validity, so replacing a working key would be the wrong
    # ask and would cost the owner a paste for nothing.
    ('{"message":"Resource not accessible by personal access token"}',
     "external_write_failed"),
    ('{"message":"Invalid repository for token"}', "external_write_failed"),
    ('{"message":"Token does not have the required scopes"}',
     "external_write_refused"),
    ('{"message":"Must have admin rights to Repository."}', "external_write_failed"),
])
def test_a_403_about_what_the_key_may_do_keeps_its_class(body, expected):
    assert _classify(403, body) == expected, body


@pytest.mark.parametrize("status", [400, 404, 409, 422, 429, 500])
def test_no_other_status_is_a_rejected_credential(status):
    """Only 401 and a narrow 403. A 422 naming an invalid token in its body is
    still a 422 — the far side accepted the key and refused the request."""
    assert _classify(status, '{"message":"invalid_token"}') != "credential_rejected"


def test_a_refusal_before_the_wire_is_not_a_rejected_credential():
    """The distinction the class is built on: nothing was ever sent, so nothing
    rejected our key. These keep their own classes whatever their text says."""
    from tinyassets.runs import _classify_external_write

    for kind in ("missing_consent", "soul_authority_denied", "no_universe_authority"):
        assert _classify_external_write(
            f"external write failed - call/authenticated_external_call: "
            f"refused before the wire: {kind} [{kind}]"
        ) == "external_write_refused", kind
    # And a pre-wire failure whose message happens to contain the delivered
    # phrasing is not promoted either: the phrase alone is not the trigger, the
    # status it carries is.
    assert _classify_external_write(
        "external write failed - call/authenticated_external_call: "
        "could not build request url: bad_path [invalid_request_path]"
    ) == "external_write_failed"


def test_one_rows_body_cannot_decide_another_rows_class():
    """A summary carries up to five rows. The 403 body test is bounded to the row
    whose status it is reading — at the start of the NEXT delivered status, not at
    a character count.

    Codex refute-review, P1 #2: with a fixed 220-character window, a 403 with a
    SHORT body borrowed `invalid_token` out of the following 404 and classified as
    a dead key. The first version of this test padded the 403's body to 150
    characters and so never met it; the row below is Codex's counterexample
    unpadded.
    """
    from tinyassets.runs import _classify_external_write, _external_write_error_summary

    def _summary_of(*rows: tuple[str, int, str]) -> str:
        return _external_write_error_summary([
            {"node_id": node, "sink": "authenticated_external_call",
             "error": f"far side answered HTTP {status}: {body}",
             "error_kind": "far_side_error"}
            for node, status, body in rows
        ]).lower()

    # Short 403 body, dead-key marker in the NEXT row: not a rejected credential.
    assert _classify_external_write(
        _summary_of(("a", 403, "Forbidden"), ("b", 404, "invalid_token"))
    ) == "external_write_failed"
    # Same shape, the 403 carrying the marker ITSELF: still caught, so the bound
    # narrows the window rather than disabling the rule.
    assert _classify_external_write(
        _summary_of(("a", 403, "invalid_token"), ("b", 404, "Not Found"))
    ) == "credential_rejected"


def test_a_credential_declared_dead_in_a_sentence_is_caught():
    """The copular form, which a one-word gap cannot reach and a wider gap must
    not be used for (Codex refute-review, P1 #3).

    The first string is verbatim from a real 403 body — five words between
    `token` and `invalid`. A copula binds its predicate to its SUBJECT, which is
    why the extra reach is safe here and would not be for `invalid <n> token`;
    the decoys below are the cases that proves.
    """
    assert _classify(403, "The security token included in the request is invalid.") == (
        "credential_rejected"
    )
    assert _classify(403, '{"message":"Your credentials have expired"}') == (
        "credential_rejected"
    )
    assert _classify(403, '{"message":"The API key you supplied has been revoked"}') == (
        "credential_rejected"
    )
    # The subject is not a credential, so the sentence is not about one.
    assert _classify(403, '{"message":"The repository is invalid"}') == (
        "external_write_failed"
    )
    # No copula at all, and five words of distance: still not promoted.
    assert _classify(403, '{"message":"Invalid target selected for this token"}') == (
        "external_write_failed"
    )


def test_the_action_names_the_card_and_forbids_the_retry():
    """The agent's whole instruction set for this failure. Each clause is here
    because its absence is what happened live: it retried, it widened, and it
    answered in prose instead of raising anything."""
    from tinyassets.runs import external_write_suggested_action

    action = external_write_suggested_action("credential_rejected")
    assert "rotate_http" in action
    assert "pending_request" in action
    assert "destination" in action
    assert "Do not retry" in action
    assert "prose" in action
    assert "widening the grant changes nothing" in action
    # It must not send the agent down either of the two wrong paths.
    assert "extend_http" not in action
    assert "remove_http" not in action


def test_the_error_row_names_which_connection_failed():
    """Without this the agent has only the url — the API host, not the label the
    owner deposited the key under — so it cannot raise the card for the right
    destination."""
    from tinyassets.runs import _collect_external_write_errors

    [row] = _collect_external_write_errors({
        "call": {"authenticated_external_call": {
            "delivered": True,
            "destination": "acme-api",
            "response": {"status": 401, "body": '{"message":"Bad credentials"}'},
        }},
    })
    assert row["destination"] == "acme-api"
    assert row["error_kind"] == "far_side_error"
    # And an evidence row from before the field existed does not grow an empty one.
    [legacy] = _collect_external_write_errors({
        "call": {"authenticated_external_call": {
            "delivered": True,
            "response": {"status": 401, "body": "nope"},
        }},
    })
    assert "destination" not in legacy


class _Answering:
    """A loopback HTTPS endpoint that answers with the status it is given.

    The effector suite's own loopback always answers 200, and the whole point
    here is the status. Everything else about the path is the real thing: the
    real broker dispatch, the real general vault resolver, the real SSRF driver.
    """

    def __init__(self, status: int, body: bytes):
        import http.server
        import threading

        class _Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):  # noqa: ANN001, ANN002
                return

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0) or 0)
                if length:
                    self.rfile.read(length)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def _run_against(tmp_path, monkeypatch, *, status, body):
    """One real authenticated call whose far side answers ``status``.

    Returns the run's ``error`` line, built by the same composers the runner
    uses, so what is classified below is what a real run would actually carry.
    """
    from tests.test_authenticated_external_call_effector import (
        _install_inprocess_proxy,
        _install_loopback_driver,
        _setup,
    )
    from tinyassets.effectors.authenticated_external_call import (
        EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        run_authenticated_external_call_effector,
    )
    from tinyassets.runs import (
        _collect_external_write_errors,
        _external_write_error_summary,
    )

    monkeypatch.setenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", "1")
    _data_root, universe_dir, db_path = _setup(tmp_path)
    far_side = _Answering(status, body)
    _install_loopback_driver(monkeypatch, far_side.port)
    _install_inprocess_proxy(
        monkeypatch, db_path=db_path, universe_dir=universe_dir,
        grant_id="grant-http", provider="http",
        destination="api.example.com", runtime_root=tmp_path / "rt",
    )
    packet = {
        "sink": EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        "connection_id": "conn-http",
        "grant_id": "grant-http",
        "verb": "POST",
        "request": {"method": "POST", "path": "/v1/messages",
                    "body": {"text": "hello"}},
    }
    try:
        evidence = run_authenticated_external_call_effector(
            node_id="call", output_keys=["out"],
            run_state={"out": json.dumps(packet)},
            base_path=str(universe_dir), run_id="r1",
        )
    finally:
        far_side.stop()
    assert evidence["delivered"] is True, evidence
    rows = _collect_external_write_errors(
        {"call": {EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL: evidence}})
    return evidence, rows, _external_write_error_summary(rows)


def test_a_real_401_reaches_the_agent_as_a_rejected_credential(tmp_path, monkeypatch):
    """The whole loop, once, against a socket that really answers 401.

    Hand-built evidence dicts prove the classifier; this proves the classifier is
    REACHED — that the adapter records the destination, the row carries it, the
    summary is shaped the way the matcher expects, and the class that comes out
    is the one the agent acts on.
    """
    from tinyassets.api.runs import _actionable_by
    from tinyassets.runs import _classify_external_write

    evidence, rows, summary = _run_against(
        tmp_path, monkeypatch, status=401,
        body=b'{"message":"Bad credentials"}')

    assert evidence["destination"] == "api.example.com"
    assert rows[0]["destination"] == "api.example.com"
    assert _classify_external_write(summary.lower()) == "credential_rejected"
    assert _actionable_by("credential_rejected") == "user"
    # Credential-blindness is unchanged by any of this.
    assert "real-vault-http-token" not in json.dumps(evidence)
    assert "real-vault-http-token" not in summary


def test_a_real_404_is_still_the_universes_own_to_fix(tmp_path, monkeypatch):
    """The other side of the same loop: a delivered failure that is NOT about the
    key keeps `external_write_failed`, so the agent still fixes and reruns it
    rather than asking the owner for a new key."""
    from tinyassets.runs import _classify_external_write

    _evidence, _rows, summary = _run_against(
        tmp_path, monkeypatch, status=404, body=b'{"message":"Not Found"}')

    assert _classify_external_write(summary.lower()) == "external_write_failed"


# --------------------------------------------------------------------------- #
# The card
# --------------------------------------------------------------------------- #
def _deposit(uid, *, secret=SECRET, scopes=(), destination="acme"):
    from tinyassets.api.http_connection import connect_http

    return connect_http(universe_id=uid, payload=json.dumps({
        "destination": destination, "secret": secret, "auth_scheme": "bearer",
        "allowed_endpoints": [{"host": "api.acme.test",
                               "path_template": "/v1/things",
                               "methods": ["POST"]}],
        "scopes": list(scopes),
    }))


def _rotate_through_the_rail(uid, *, secret=REPLACEMENT, destination="acme"):
    ask = _ask(
        uid,
        kind="API",
        title=f"{destination} stopped accepting its key",
        body="The far side answered 401. Paste a new key and I will carry on.",
        fields=[{"name": "token", "type": "secret", "label": "API token"}],
        action={"type": "rotate_http", "destination": destination},
    )
    assert ask.get("request_id"), ask
    return ask, _owner_answer(uid, request_id=ask["request_id"],
                        values={"token": secret})


def _policy(base, uid, *, destination="acme", actor="alice"):
    """The connection's stored endpoint + scope JSON, and its grant row."""
    from tinyassets.api.http_connection import _ids
    from tinyassets.storage.outbound_connections import ConnectionLedger

    conn_id, grant_id = _ids(universe_id=uid, destination=destination)
    ledger = ConnectionLedger(base / ".broker" / "outbound.db", data_root=base,
                              verify_authenticated_principal=lambda: actor)
    return ledger.policy_json(conn_id), ledger.get_grant(grant_id)


def _stored_secret(base, uid, *, destination="acme"):
    """What the NEXT call would present, through the resolver the broker child
    actually runs — not by reading the vault file ourselves."""
    from tinyassets.storage.outbound_connections import _GeneralVaultCredentialResolver

    resolver = _GeneralVaultCredentialResolver(universe_dir=base / uid)
    return resolver(f"vault://http/{destination}")


def test_the_card_replaces_the_key_and_the_next_call_uses_it(base):
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    assert not _deposit("u-1").get("error")
    assert _stored_secret(base, "u-1") == SECRET

    ask, answered = _rotate_through_the_rail("u-1")
    assert answered.get("status") == "answered", answered
    assert _stored_secret(base, "u-1") == REPLACEMENT


def test_the_card_says_what_it_does_in_plain_words(base):
    """The failure this replaces was a card the owner read as deletion, so the
    sentence has to say that nothing is being taken away or newly granted."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")

    ask, _answered = _rotate_through_the_rail("u-1")
    sentence = ask["grant_sentence"]
    assert sentence.startswith("acme stopped accepting its key. Paste a new one.")
    assert "Nothing else changes" in sentence
    assert "nothing new to allow" in sentence


def test_nothing_but_the_key_changes(base):
    """The property the whole design rests on, asserted against the stored rows
    rather than the returned envelope: `rotate_http` makes exactly one mutation
    (the vault upsert) and touches the ledger not at all."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1", scopes=("git_read:o/r",))
    before_policy, before_grant = _policy(base, "u-1")

    _ask_row, answered = _rotate_through_the_rail("u-1")
    after_policy, after_grant = _policy(base, "u-1")

    assert before_policy == after_policy, "the endpoint/scope policy moved"
    assert before_grant.grant_id == after_grant.grant_id
    assert before_grant.granted_at == after_grant.granted_at
    assert before_grant.revoked_at is None and after_grant.revoked_at is None
    # And the receipt says so from the row it did not write.
    assert answered["allowed_endpoints"] == [
        {"host": "api.acme.test", "path_template": "/v1/things",
         "methods": ["POST"], "param_patterns": {}, "allowed_query": [],
         "query_patterns": {}, "required_query": []},
    ], answered["allowed_endpoints"]
    assert answered["git_scopes"] == ["git_read:o/r"]


def test_the_consents_the_key_authorized_survive_it(base):
    """A removal revokes them, deliberately. A rotation must not: the owner is
    replacing a key, not withdrawing what they already allowed it to do."""
    from tinyassets.storage.effector_consents import grant_consent, list_consents

    udir = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")
    grant_consent(udir, sink="authenticated_external_call", destination="acme",
                  granted_by="alice")
    before = list_consents(udir)
    assert before, "the fixture granted nothing, so this proves nothing"

    _rotate_through_the_rail("u-1")

    assert list_consents(udir) == before


def test_a_rotation_does_not_need_the_endpoints_restated(base):
    """The reason remove-and-connect was not a repair path: it makes the agent
    reproduce the whole grant, and the owner re-approve reach they already gave.
    The ask carries a destination and a secret field, and refuses the rest."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")

    for extra in (
        {"endpoints": [{"host": "api.acme.test", "path_template": "/v1/other",
                        "methods": ["POST"]}]},
        {"scopes": ["git_write:o/r"]},
        {"access": "full"},
        {"auth_scheme": "basic"},
    ):
        refused = _ask(
            "u-1", kind="API", title="rotate", fields=[
                {"name": "token", "type": "secret", "label": "API token"}],
            action={"type": "rotate_http", "destination": "acme", **extra},
        )
        assert refused.get("error") == "request_invalid", (extra, refused)
        assert "replaces only the key" in refused["detail"], extra


def test_no_tab_is_raised_for_a_destination_that_holds_no_key(base):
    """The owner never sees a tab that cannot be honoured — the agent can fix
    the ask and the owner cannot."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")

    refused = _ask(
        "u-1", kind="API", title="rotate", fields=[
            {"name": "token", "type": "secret", "label": "API token"}],
        action={"type": "rotate_http", "destination": "acme"},
    )
    assert refused["error"] == "ask_cannot_be_granted", refused
    assert "connect_http" in refused["detail"]


def test_a_secret_field_is_still_refused_on_an_ask_that_is_not_a_credential(base):
    """The boundary widened by one named type, and not by accident. Without it,
    "compose requests however you like" becomes a way to ask for a password and
    store it in the clear."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")

    for action in ({"type": "answer"},
                   {"type": "extend_http", "destination": "acme",
                    "scopes": ["git_read:o/r"]},
                   {"type": "remove_http", "destination": "acme"}):
        refused = _ask("u-1", kind="API", title="paste your password", fields=[
            {"name": "pw", "type": "secret", "label": "Password"}],
            action=action)
        assert refused.get("error") == "request_invalid", (action, refused)
        assert "only allowed on" in refused["detail"], action


def test_a_rotation_needs_a_box_to_paste_into(base):
    """A fieldless rotation would render as a plain confirm with nothing to
    type, which is what `remove_http` correctly looks like and this must not."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")

    refused = _ask("u-1", kind="API", title="rotate", fields=[],
                   action={"type": "rotate_http", "destination": "acme"})
    assert refused.get("error") == "request_invalid", refused
    assert "one field per value" in refused["detail"]


def test_the_boxes_come_from_the_stored_scheme_not_from_the_ask(base):
    """A multi-value connection's card needs one box per value, named the way
    the deposit reads them. The scheme is read off the CONNECTION when the ask
    is raised, so a one-box card for it is caught before the owner pastes —
    not after, by a write refusing the assembled value."""
    from tinyassets.api.http_connection import connect_http

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    assert not connect_http(universe_id="u-1", payload=json.dumps({
        "destination": "acme", "auth_scheme": "basic", "secret": "alice:pw-one",
        "allowed_endpoints": [{"host": "api.acme.test",
                               "path_template": "/v1/things",
                               "methods": ["POST"]}],
    })).get("error")

    thin = _ask("u-1", kind="API", title="rotate", fields=[
        {"name": "token", "type": "secret", "label": "API token"}],
        action={"type": "rotate_http", "destination": "acme"})
    assert thin.get("error") == "request_invalid", thin
    assert "username, password" in thin["detail"], thin

    ask = _ask("u-1", kind="API", title="rotate", fields=[
        {"name": "username", "type": "secret", "label": "User"},
        {"name": "password", "type": "secret", "label": "Password"}],
        action={"type": "rotate_http", "destination": "acme"})
    assert ask.get("request_id"), ask
    answered = _owner_answer("u-1", request_id=ask["request_id"],
                       values={"username": "alice", "password": "pw-two"})
    assert answered.get("status") == "answered", answered
    # `username:password`, the encoding the vault string has always used — not
    # JSON, which would hand the service `Basic base64({...})`.
    assert _stored_secret(base, "u-1") == "alice:pw-two"


def test_a_sign_in_connection_is_not_rotated_by_pasting(base):
    """Its secret is a token bundle naming where every refresh token is sent, so
    only the owner's own sign-in may write it."""
    from tinyassets.api.http_connection import rotate_http
    from tinyassets.credential_vault import load_credential_vault

    udir = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")
    # Move the stored connection to the sign-in scheme the way a real sign-in
    # would, then ask to paste over it.
    from tinyassets.api.http_connection import _ids
    from tinyassets.storage.outbound_connections import ConnectionLedger

    conn_id, _ = _ids(universe_id="u-1", destination="acme")
    ledger = ConnectionLedger(base / ".broker" / "outbound.db", data_root=base,
                              verify_authenticated_principal=lambda: "alice")
    with ledger._connect() as connection:
        connection.execute(
            "UPDATE outbound_connections SET auth_scheme = 'oauth2' "
            "WHERE connection_id = ?", (conn_id,))

    refused = _ask("u-1", kind="API", title="rotate", fields=[
        {"name": "token", "type": "secret", "label": "API token"}],
        action={"type": "rotate_http", "destination": "acme"})
    assert refused["error"] == "ask_cannot_be_granted", refused
    assert "signing in" in refused["detail"]

    direct = rotate_http(universe_id="u-1", payload=json.dumps({
        "destination": "acme", "secret": REPLACEMENT}))
    assert direct["error"] == "rotation_not_supported", direct
    [record] = [r for r in load_credential_vault(udir)
                if r.get("credential_type") == "http"]
    assert record["token"] == SECRET, "the old secret was overwritten anyway"


# --------------------------------------------------------------------------- #
# Whose key it is
# --------------------------------------------------------------------------- #
def test_another_admin_cannot_replace_the_depositors_key(base):
    """An admin may act on the universe, but not on another principal's
    deposited credential — and the refusal is the uniform absent envelope, so
    this surface cannot say which destinations exist or who deposited them."""
    from tinyassets.api.http_connection import rotate_http
    from tinyassets.daemon_server import grant_universe_access

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")
    grant_universe_access(base, universe_id="u-1", actor_id="mallory",
                          permission="admin", granted_by="alice")

    _login("mallory")
    refused = rotate_http(universe_id="u-1", payload=json.dumps({
        "destination": "acme", "secret": REPLACEMENT}))
    assert refused == {"error": "not_found", "resource": "connection"}, refused
    # And through the rail, where they would actually try it.
    railed = _ask("u-1", kind="API", title="rotate", fields=[
        {"name": "token", "type": "secret", "label": "API token"}],
        action={"type": "rotate_http", "destination": "acme"})
    assert railed["error"] == "ask_cannot_be_granted", railed

    _login("alice")
    assert _stored_secret(base, "u-1") == SECRET


def test_another_universe_cannot_replace_this_universes_key(base):
    """Two universes, the same destination label, one owner. The connection id
    is derived from (universe, destination), so a rotation in one can never
    reach the other's row — and the grant's universe is compared anyway,
    because a derivation is not a check."""
    from tinyassets.api.http_connection import rotate_http

    _make_universe(base, "u-1", admin="alice")
    _make_universe(base, "u-2", admin="alice")
    _login("alice")
    _deposit("u-1", secret=SECRET)
    _deposit("u-2", secret="other-" + "c" * 40)

    out = rotate_http(universe_id="u-2", payload=json.dumps({
        "destination": "acme", "secret": REPLACEMENT}))
    assert out.get("status") == "rotated", out

    assert _stored_secret(base, "u-2") == REPLACEMENT
    assert _stored_secret(base, "u-1") == SECRET, "the other universe's key moved"

    # A universe with no such connection is absent, never another's.
    _make_universe(base, "u-3", admin="alice")
    assert rotate_http(universe_id="u-3", payload=json.dumps({
        "destination": "acme", "secret": REPLACEMENT})) == {
        "error": "not_found", "resource": "connection"}
    assert _stored_secret(base, "u-1") == SECRET


def test_a_grant_bound_to_another_universe_is_refused(base):
    """The grant's `universe_id` is compared, and this is the test that says so.

    Codex refute-review, P2 #6: `test_another_universe_cannot_replace_this_
    universes_key` exercises only the id DERIVATION — delete the
    `grant.universe_id != uid` check and it still passes. This one moves the
    grant, so the derivation resolves the connection and only the explicit
    comparison can refuse it.
    """
    from tinyassets.api.http_connection import _ids, rotate_http
    from tinyassets.storage.outbound_connections import ConnectionLedger

    _make_universe(base, "u-1", admin="alice")
    _make_universe(base, "u-2", admin="alice")
    _login("alice")
    _deposit("u-1")
    _conn_id, grant_id = _ids(universe_id="u-1", destination="acme")
    ledger = ConnectionLedger(base / ".broker" / "outbound.db", data_root=base,
                              verify_authenticated_principal=lambda: "alice")
    with ledger._connect() as connection:
        connection.execute(
            "UPDATE outbound_connection_grants SET universe_id = 'u-2' "
            "WHERE grant_id = ?", (grant_id,))
    assert ledger.get_grant(grant_id).universe_id == "u-2", "the fixture did not move it"

    refused = rotate_http(universe_id="u-1", payload=json.dumps({
        "destination": "acme", "secret": REPLACEMENT}))
    assert refused == {"error": "not_found", "resource": "connection"}, refused
    assert _stored_secret(base, "u-1") == SECRET


def _ledger_connection(base, *, uid, destination, actor="alice", **over):
    """A connection row built straight through the ledger, to reach shapes
    `connect_http` cannot create but a lower-level path can."""
    from tinyassets.api.http_connection import _ids
    from tinyassets.storage.outbound_connections import ConnectionLedger

    conn_id, grant_id = _ids(universe_id=uid, destination=destination)
    ledger = ConnectionLedger(base / ".broker" / "outbound.db", data_root=base,
                              verify_authenticated_principal=lambda: actor)
    fields = {
        "connection_id": conn_id,
        "owner_user_id": actor,
        "connection_class": "http",
        "connection_type": "http",
        "auth_scheme": "bearer",
        "scopes": ("POST",),
        "provider": "http",
        "destination": destination,
        "credential_ref": f"vault://http/{destination}",
        "allowed_endpoints": [{"host": "api.acme.test",
                               "path_template": "/v1/things",
                               "methods": ["POST"]}],
        **over,
    }
    ledger.create_connection(**fields)
    ledger.grant_connection(grant_id=grant_id, connection_id=conn_id,
                            owner_user_id=actor, universe_id=uid)
    return conn_id, grant_id


def test_a_connection_that_does_not_read_this_vault_slot_is_refused(base):
    """A rotation writes ONE slot: `(http, <destination>)`. A connection whose
    `credential_ref` names a different key would be reported as rotated while the
    secret it actually presents was untouched — a success that changed nothing,
    which is the worst outcome available here.

    `connect_http` compares `credential_ref` as an immutable field; not comparing
    it here would have been the inconsistency. Found by re-reading my own gate
    against the deposit's before sending the review out.
    """
    from tinyassets.api.http_connection import rotate_http
    from tinyassets.credential_vault import write_credential_vault

    udir = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    write_credential_vault(
        udir, [{"credential_type": "http", "service": "elsewhere",
                "destination": "elsewhere", "token": SECRET}],
        owner_user_id="alice", universe_id="u-1")
    _ledger_connection(base, uid="u-1", destination="acme",
                       credential_ref="vault://http/elsewhere")

    refused = rotate_http(universe_id="u-1", payload=json.dumps({
        "destination": "acme", "secret": REPLACEMENT}))
    assert refused == {"error": "not_found", "resource": "connection"}, refused
    assert _stored_secret(base, "u-1", destination="elsewhere") == SECRET


def test_a_scheme_with_no_pasted_key_is_refused_in_both_places(base):
    """Fail closed on the SET the deposit door accepts, not on a list of known
    exceptions (Codex refute-review, P2 #5).

    `none` sends no credential at all, so storing a pasted value would report a
    repair that cannot have repaired anything. `connect_http` cannot create such a
    connection; a lower-level path can, and hard rule 8 says the answer is a
    refusal, not a receipt.
    """
    from tinyassets.api.http_connection import rotate_http

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _ledger_connection(base, uid="u-1", destination="acme", auth_scheme="none")

    direct = rotate_http(universe_id="u-1", payload=json.dumps({
        "destination": "acme", "secret": REPLACEMENT}))
    assert direct["error"] == "rotation_not_supported", direct
    assert "no pasted key" in direct["detail"]

    railed = _ask("u-1", kind="API", title="rotate", fields=[
        {"name": "token", "type": "secret", "label": "API token"}],
        action={"type": "rotate_http", "destination": "acme"})
    assert railed["error"] == "ask_cannot_be_granted", railed
    assert "no pasted key" in railed["detail"]


def test_a_record_with_no_recorded_depositor_is_refused_before_the_card(base):
    """The vault refuses to overwrite an http slot it cannot prove is yours. That
    refusal used to arrive AFTER the owner had pasted, because the preview did not
    check it (Codex refute-review, P2 #4) — and this module's own rule is that the
    owner never sees a tab that cannot be honoured."""
    from tinyassets.api.http_connection import rotate_http
    from tinyassets.credential_vault import load_credential_vault, write_credential_vault

    udir = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    # A legacy deposit: an http record with no ownership row at all.
    write_credential_vault(udir, [{"credential_type": "http", "service": "acme",
                                   "destination": "acme", "token": SECRET}])
    _ledger_connection(base, uid="u-1", destination="acme")

    railed = _ask("u-1", kind="API", title="rotate", fields=[
        {"name": "token", "type": "secret", "label": "API token"}],
        action={"type": "rotate_http", "destination": "acme"})
    # Under its OWN name, not flattened into `ask_cannot_be_granted`: there is no
    # ask to fix here. The note is what says no tab is pending.
    assert railed["error"] == "credential_ownership_transfer_unsupported", railed
    assert "no recorded depositor" in railed["detail"], railed
    assert "no tab was raised" in railed["note"], railed
    assert _rail("u-1")["count"] == 0, "a tab was raised anyway"

    direct = rotate_http(universe_id="u-1", payload=json.dumps({
        "destination": "acme", "secret": REPLACEMENT}))
    assert direct["error"] == "credential_ownership_transfer_unsupported", direct
    [record] = [r for r in load_credential_vault(udir)
                if r.get("credential_type") == "http"]
    assert record["token"] == SECRET


def test_a_rotation_that_cannot_close_its_card_says_so(base):
    """The key IS replaced and the card did NOT close.

    Codex refute-review, P1 #1: reporting "answered" here leaves a pending card
    the owner believes is done — and because a rotation does not move the
    incarnation, answering it again later would overwrite a NEWER key with this
    older value. So the answer reports what actually happened.
    """
    from tinyassets.api import pending_requests as rail
    from tinyassets.storage import pending_requests as store

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")

    ask = _ask("u-1", kind="API", title="rotate", fields=[
        {"name": "token", "type": "secret", "label": "API token"}],
        action={"type": "rotate_http", "destination": "acme"})
    assert ask.get("request_id"), ask
    # The storage fault `resolve_request` swallows, at the one moment it matters.
    rail_resolve = store.resolve_request
    try:
        store.resolve_request = lambda *a, **k: False
        out = _owner_answer("u-1", request_id=ask["request_id"],
                      values={"token": REPLACEMENT})
    finally:
        store.resolve_request = rail_resolve

    assert out["error"] == "request_resolution_unconfirmed", out
    assert out["request_pending"] is True
    assert out["destination"] == "acme"
    assert REPLACEMENT not in json.dumps(out)
    # The key really did land, which is why this is recoverable rather than an
    # error to undo: answering again with the same value settles it.
    assert _stored_secret(base, "u-1") == REPLACEMENT
    assert rail.answer_request  # the module under test, not a stale import


def test_a_connection_with_no_live_grant_is_not_rotatable(base):
    """The grant is what binds a connection to a universe, and it is compared
    even though the connection id is derived from (universe, destination): a
    derivation is not a check. A connection whose grant is gone authorizes
    nothing, so putting a fresh key into it would be putting one somewhere
    inert."""
    from tinyassets.api.http_connection import _ids, rotate_http
    from tinyassets.storage.outbound_connections import ConnectionLedger

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")
    _conn_id, grant_id = _ids(universe_id="u-1", destination="acme")
    ConnectionLedger(
        base / ".broker" / "outbound.db", data_root=base,
        verify_authenticated_principal=lambda: "alice",
    ).revoke_grant(grant_id)

    refused = rotate_http(universe_id="u-1", payload=json.dumps({
        "destination": "acme", "secret": REPLACEMENT}))
    assert refused == {"error": "not_found", "resource": "connection"}, refused
    assert _stored_secret(base, "u-1") == SECRET


def test_a_card_raised_for_one_deposit_cannot_rotate_a_different_one(base):
    """The tab sat open while the owner removed the key and deposited another.
    Answering it now would replace a key they never saw this card for."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")

    ask = _ask("u-1", kind="API", title="rotate", fields=[
        {"name": "token", "type": "secret", "label": "API token"}],
        action={"type": "rotate_http", "destination": "acme"})
    assert ask.get("request_id"), ask

    from tinyassets.api.http_connection import remove_http

    remove_http(universe_id="u-1", payload=json.dumps({"destination": "acme"}))
    _deposit("u-1", secret="third-" + "d" * 40)

    stale = _owner_answer("u-1", request_id=ask["request_id"],
                    values={"token": REPLACEMENT})
    assert stale.get("error") == "connection_changed", stale
    assert stale.get("request_pending") is True
    assert _stored_secret(base, "u-1") == "third-" + "d" * 40


# --------------------------------------------------------------------------- #
# The secret itself
# --------------------------------------------------------------------------- #
def test_the_new_key_is_in_no_result_and_no_log(base, caplog):
    """Every channel at once on the SUCCESS path: the returned envelopes, the
    request row the rail stores, and the log."""
    from tinyassets.api.http_connection import rotate_http
    from tinyassets.api.pending_requests import list_requests

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")

    caplog.set_level(logging.DEBUG)
    logging.getLogger("tinyassets.api.http_connection").debug("capture-sentinel")

    ask, answered = _rotate_through_the_rail("u-1")
    rail = list_requests(universe_id="u-1")
    direct = rotate_http(universe_id="u-1", payload=json.dumps({
        "destination": "acme", "secret": REPLACEMENT}))
    assert direct.get("status") == "rotated", direct

    for name, envelope in (("ask", ask), ("answer", answered),
                           ("rail", rail), ("direct", direct)):
        assert REPLACEMENT not in json.dumps(envelope, default=str), name
        # And the reference that resolves to it never rides along either.
        assert "vault://" not in json.dumps(envelope, default=str), name

    messages = [record.getMessage() for record in caplog.records]
    # The sentinel proves this capture is LIVE. Without it, "no record contains
    # the secret" is also what an empty capture says (Codex refute-review, P2 #6).
    assert any("capture-sentinel" in message for message in messages)
    for record in caplog.records:
        assert REPLACEMENT not in record.getMessage(), record.name
        assert REPLACEMENT not in str(record.args or ""), record.name


def test_a_storage_fault_carrying_the_key_leaks_it_nowhere(base, caplog):
    """The exception path, with a fault whose own message holds the secret.

    The earlier version of this only submitted whitespace and then checked that a
    DIFFERENT value was absent, which proves nothing about exception secrecy
    (Codex refute-review, P2 #6). A vault fault really can carry the value it was
    handed — that is the whole of memory `exceptions-carry-more-than-their-
    message` — so the fault is injected carrying it, and the refusal, the log and
    the traceback text are all checked afterwards.
    """
    from tinyassets import credential_vault
    from tinyassets.api.http_connection import rotate_http

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")

    caplog.set_level(logging.DEBUG)
    logging.getLogger("tinyassets.api.http_connection").debug("capture-sentinel")
    real = credential_vault.write_credential_vault
    try:
        def _explode(*_a, **_k):
            raise RuntimeError(f"disk error while storing {REPLACEMENT}")

        credential_vault.write_credential_vault = _explode
        out = rotate_http(universe_id="u-1", payload=json.dumps({
            "destination": "acme", "secret": REPLACEMENT}))
    finally:
        credential_vault.write_credential_vault = real

    assert out == {"error": "deposit_failed", "resource": "connection"}, out
    assert REPLACEMENT not in json.dumps(out)
    messages = [record.getMessage() for record in caplog.records]
    assert any("capture-sentinel" in message for message in messages)
    for record in caplog.records:
        assert REPLACEMENT not in record.getMessage(), record.name
        assert REPLACEMENT not in str(record.args or ""), record.name
        # `exc_info` renders the chained exception's own text, which is where the
        # value was planted.
        if record.exc_info:
            import traceback
            rendered = "".join(traceback.format_exception(*record.exc_info))
            assert REPLACEMENT not in rendered, record.name
    # The old key is untouched, because the refusal is the whole outcome.
    assert _stored_secret(base, "u-1") == SECRET


def test_a_malformed_replacement_is_refused_before_the_old_one_is_touched(base):
    """Every refusal happens before the single write, so a refused rotation
    leaves the working key exactly as it was."""
    from tinyassets.api.http_connection import connect_http, rotate_http

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    assert not connect_http(universe_id="u-1", payload=json.dumps({
        "destination": "acme", "auth_scheme": "basic", "secret": "alice:pw-one",
        "allowed_endpoints": [{"host": "api.acme.test",
                               "path_template": "/v1/things",
                               "methods": ["POST"]}],
    })).get("error")

    for value in ("no-colon-here", "alice:", ":pw"):
        refused = rotate_http(universe_id="u-1", payload=json.dumps({
            "destination": "acme", "secret": value}))
        assert refused["error"] == "connection_setup_invalid", value
        assert _stored_secret(base, "u-1") == "alice:pw-one", value


# --------------------------------------------------------------------------- #
# How old the key is
# --------------------------------------------------------------------------- #
def test_every_http_record_says_when_its_secret_was_stored(base):
    """There is no expiry warning yet — a key that died on the provider side was
    first noticed by a failed run. This is the field one needs, and it has to be
    written by every path that stores a secret: the merge replaces the whole
    slot, so a field only one writer sets disappears on the next write by
    another."""
    from tinyassets.credential_vault import HTTP_DEPOSITED_AT, load_credential_vault

    udir = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")
    [deposited] = [r for r in load_credential_vault(udir)
                   if r.get("credential_type") == "http"]
    first = deposited[HTTP_DEPOSITED_AT]
    assert first

    _rotate_through_the_rail("u-1")
    [rotated] = [r for r in load_credential_vault(udir)
                 if r.get("credential_type") == "http"]
    assert rotated["token"] == REPLACEMENT
    assert rotated[HTTP_DEPOSITED_AT] >= first


def test_one_builder_writes_every_http_credential_record():
    """Three paths store an http secret — the deposit, the rotation, and an
    oauth2 refresh. Two definitions of this record would drift, and the one that
    drifted would be the one whose `deposited_at` lies."""
    import inspect

    from tinyassets.api import http_connection
    from tinyassets.connection_oauth import tokens

    for module in (http_connection, tokens):
        source = inspect.getsource(module)
        assert '"credential_type": "http"' not in source, module.__name__
        assert "http_credential_record(" in source, module.__name__
