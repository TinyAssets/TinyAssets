"""The founder's actual sequence: remove a key, put it back, still do the work.

Steps 1-3 of the live test are remove GitHub, re-deposit it through the new
labelled path, then open a real PR. Between step 1 and step 3 sits every piece of
authority the checkout needs, and removal deletes some of it and not the rest:

* the connection and its GRANT are deleted (the grant is where git scopes live);
* the workspace CONSENT row is not -- it lives in the effector-consent store,
  which `remove_http` never touches.

Whether that composes back together turns entirely on connection ids being
deterministic on `(universe_id, destination)`. They are, by construction, and
`docs/concerns/2026-08-31-fixing-an-authority-key-orphans-the-grants-written-
under-the-old-one.md` is what happens when an authority key moves underneath
rows written against the old one -- that one hit live and blocked this universe
mid-run with `missing_consent`.

So this is reasoning that has to be tested rather than believed, and it carries
a consequence for the person doing the test: the SCOPES have to be asked for
again, because they died with the grant. `test_the_scopes_do_not_come_back_by
_themselves` is the one that says so.
"""
from __future__ import annotations

import json

import pytest

from tests.test_pending_requests import (  # noqa: F401 - fixtures and harness
    _ask,
    _login,
    _make_universe,
    _owner_answer,
    _reset_auth,
)


@pytest.fixture
def base(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    return root


REPO = "tinyassets/tinyassets"


def _deposit(uid, *, scopes=(), secret="ghp_" + "x" * 36):
    from tinyassets.api.http_connection import connect_http

    return connect_http(universe_id=uid, payload=json.dumps({
        "destination": "github", "secret": secret, "auth_scheme": "bearer",
        "allowed_endpoints": [{"host": "api.github.com",
                               "path_template": "/repos/o/r/pulls",
                               "methods": ["POST"]}],
        "scopes": list(scopes),
    }))


def _remove_through_the_rail(uid):
    ask = _ask(uid, kind="API", title="Remove the GitHub key", fields=[],
               action={"type": "remove_http", "destination": "github"})
    assert ask.get("request_id"), ask
    return _owner_answer(uid, request_id=ask["request_id"], values={})


def _connection(basedir, uid, actor="alice"):
    from tinyassets.api.http_connection import _ids
    from tinyassets.storage.outbound_connections import ConnectionLedger

    conn_id, _ = _ids(universe_id=uid, destination="github")
    ledger = ConnectionLedger(basedir / ".broker" / "outbound.db", data_root=basedir,
                              verify_authenticated_principal=lambda: actor)
    return ledger._get_connection_resource(conn_id)


def test_the_connection_id_is_the_same_one_afterwards(base):
    """Everything else here depends on this, so it is asserted directly.

    The id is deterministic on (universe, destination) and deliberately excludes
    the actor. If that ever changes, every consent row written against the old
    id is orphaned and the failure surfaces far away as `missing_consent`.
    """
    from tinyassets.api.http_connection import _ids

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    before, _ = _ids(universe_id="u-1", destination="github")
    _deposit("u-1")
    _remove_through_the_rail("u-1")
    _deposit("u-1", secret="ghp_" + "y" * 36)
    after, _ = _ids(universe_id="u-1", destination="github")

    assert before == after
    assert _connection(base, "u-1") is not None, "the connection did not come back"


def test_removing_the_key_takes_back_what_it_authorized(base):
    """Removal REVOKES the consents keyed on that connection.

    This test used to assert the opposite -- that a consent survives a
    remove-and-redeposit, so an owner rotating a key would not have to
    re-grant checkout. That convenience had a cost the comment did not say
    out loud: a connection id is deterministic per (universe, destination),
    so the surviving row also outlives a removal the owner meant as a
    revocation, and a key deposited later under the same name inherits
    repository access nobody granted it.

    Taking a key back takes back what it authorized. Rotation is served
    without inheritance: ``remove_http`` returns ``removed_consents`` beside
    the endpoints and scopes it already returned, so the agent re-asks for
    exactly those and the owner is never asked to remember them -- and a key
    re-deposited with ``access: "full"`` needs no per-repository consent at
    all. Decision recorded in docs/host-actions.md; reversible.
    """
    from tinyassets.storage.effector_consents import list_consents

    udir = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1", scopes=[f"git_read:{REPO}", f"git_write:{REPO}"])

    from tinyassets.api.http_connection import _ids
    conn_id, _ = _ids(universe_id="u-1", destination="github")

    granted = _ask("u-1", kind="Approval", title="Let me check out the repo",
                   fields=[],
                   action={"type": "grant_workspace_consent", "repo": REPO,
                           "connection_id": conn_id, "host": "github.com",
                           "consents": ["workspace_checkout"]})
    assert granted.get("request_id"), granted
    assert not _owner_answer("u-1", request_id=granted["request_id"], values={}).get("error")

    def _keys():
        return {r.get("destination")
                for r in list_consents(udir, sink="workspace")}

    # Read the key the system WROTE rather than predicting it. An expected
    # value spelled here would be a second definition of the destination
    # competing with `workspace_consent_destination`.
    granted_keys = _keys()
    assert granted_keys, "consent was never written"
    [key] = granted_keys
    assert conn_id in key, "the consent is not keyed by the connection"

    removed = _remove_through_the_rail("u-1")

    assert _keys() == set(), "removing the key left its consents active"
    # And it SAYS what it took, so a rotation can re-ask for exactly that.
    if isinstance(removed, dict) and "removed_consents" in removed:
        assert set(removed["removed_consents"]) == granted_keys

    _deposit("u-1", secret="ghp_" + "y" * 36,
             scopes=[f"git_read:{REPO}", f"git_write:{REPO}"])
    assert _keys() == set(), (
        "the re-deposited key inherited a consent nobody granted it"
    )


def test_the_scopes_do_not_come_back_by_themselves(base):
    """The consequence the person doing this needs to know.

    Git scopes live on the GRANT, and removal deletes the grant. Consent alone
    grants nothing -- the sink wants both -- so a re-deposit that forgets
    `scopes` leaves a connection that looks present and cannot check anything
    out. That failure surfaces at the checkout, a long way from the paste box.
    """
    from tinyassets.storage.workspace_authority import has_git_scope

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1", scopes=[f"git_read:{REPO}", f"git_write:{REPO}"])
    assert has_git_scope(_connection(base, "u-1"), "git_read", REPO)

    _remove_through_the_rail("u-1")
    _deposit("u-1", secret="ghp_" + "y" * 36)          # scopes forgotten

    assert not has_git_scope(_connection(base, "u-1"), "git_read", REPO), (
        "scopes survived a removal, so removal is not actually removing authority"
    )

    # Asking for them again restores it, without touching the consent.
    _remove_through_the_rail("u-1")
    _deposit("u-1", secret="ghp_" + "z" * 36,
             scopes=[f"git_read:{REPO}", f"git_write:{REPO}"])
    assert has_git_scope(_connection(base, "u-1"), "git_read", REPO)


def test_a_removed_key_grants_nothing_while_it_is_gone(base):
    """Between removal and re-deposit the consent row still exists. It must not
    be usable: there is no connection behind it, so authority is absent even
    though the owner's past yes is still on record."""
    from tinyassets.storage.workspace_authority import has_git_scope

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1", scopes=[f"git_read:{REPO}"])
    _remove_through_the_rail("u-1")

    assert _connection(base, "u-1") is None
    assert not has_git_scope(_connection(base, "u-1"), "git_read", REPO)


def test_removal_hands_back_the_shape_it_destroyed(base):
    """So putting it back does not start from anyone's memory.

    Founder, 2026-08-31: the agent should "ask its user for only exactly what it
    cant do on its own". The platform knew the endpoints and scopes a moment
    before it deleted them, so asking the owner to remember is the guessing this
    change set exists to end.
    """
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1", scopes=[f"git_read:{REPO}", f"git_write:{REPO}"])

    from tinyassets.api.http_connection import remove_http

    out = remove_http(universe_id="u-1",
                      payload=json.dumps({"destination": "github"}))

    assert out["status"] == "removed"
    assert out["removed_scopes"] == [f"git_read:{REPO}", f"git_write:{REPO}"]
    assert [e["host"] for e in out["removed_endpoints"]] == ["api.github.com"]
    assert out["removed_endpoints"][0]["path_template"] == "/repos/o/r/pulls"
    assert out["removed_endpoints"][0]["methods"] == ["POST"]
    # And it says what to do with them, because a value nobody is told to use
    # is a value nobody uses.
    assert "removed_scopes" in out["next"]


def test_a_rotation_restores_itself_from_the_RAILS_readback_alone(base):
    """The whole point, end to end, through the surface the owner drives.

    An earlier version of this called `remove_http` and `connect_http` directly.
    It passed while the RAIL was dropping the readback on the floor, so the
    thing it was named for did not work and the test could not see it (Codex,
    R4) -- the same "prove the API, not the door" mistake this file exists to
    stop.

    Nothing here remembers the original shape: the deposit is built purely from
    what answering the removal tab returned, and the only new input is the new
    key.
    """
    from tinyassets.api.http_connection import connect_http
    from tinyassets.storage.workspace_authority import has_git_scope

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1", scopes=[f"git_read:{REPO}", f"git_write:{REPO}"])

    gone = _remove_through_the_rail("u-1")          # the OWNER's route
    assert not _connection(base, "u-1")
    assert gone.get("removed_scopes"), "the rail dropped the readback"

    back = connect_http(universe_id="u-1", payload=json.dumps({
        "destination": gone["destination"],
        "secret": "ghp_" + "r" * 36,             # the ONLY new thing
        "auth_scheme": gone["auth_scheme"],
        "allowed_endpoints": gone["removed_endpoints"],
        "scopes": gone["removed_scopes"],
    }))

    assert back["status"] == "provisioned"
    assert has_git_scope(_connection(base, "u-1"), "git_read", REPO)
    assert has_git_scope(_connection(base, "u-1"), "git_write", REPO)


def test_an_oauth1a_connection_with_a_PATTERNED_endpoint_rotates_too(base):
    """The case a three-field readback silently got wrong.

    Flattening endpoints to host/path/methods loses `param_patterns`, and
    omitting `auth_scheme` re-deposits an OAuth connection as a bearer one. Both
    produce a connection that provisions cleanly and is not the one destroyed.
    """
    from tinyassets.api.http_connection import connect_http

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    endpoint = {
        "host": "api.x.com",
        "path_template": "/2/users/{id}/tweets",
        "methods": ["POST"],
        "param_patterns": {"id": "[0-9]{1,20}"},
    }
    first = connect_http(universe_id="u-1", payload=json.dumps({
        "destination": "x:posting", "auth_scheme": "oauth1a",
        "secret": json.dumps({"api_key": "k", "api_secret": "s",
                              "access_token": "t", "access_token_secret": "ts"}),
        "allowed_endpoints": [endpoint],
    }))
    assert first["status"] == "provisioned", first

    ask = _ask("u-1", kind="API", title="Rotate the X keys", fields=[],
               action={"type": "remove_http", "destination": "x:posting"})
    gone = _owner_answer("u-1", request_id=ask["request_id"], values={})

    assert gone["auth_scheme"] == "oauth1a", "the scheme did not survive"
    [read_back] = gone["removed_endpoints"]
    assert read_back["param_patterns"] == {"id": "[0-9]{1,20}"}, (
        "the pattern was flattened away, so the endpoint cannot be rebuilt"
    )

    again = connect_http(universe_id="u-1", payload=json.dumps({
        "destination": gone["destination"],
        "auth_scheme": gone["auth_scheme"],
        "secret": json.dumps({"api_key": "k2", "api_secret": "s2",
                              "access_token": "t2", "access_token_secret": "ts2"}),
        "allowed_endpoints": gone["removed_endpoints"],
    }))
    assert again["status"] == "provisioned", again


def test_the_readback_carries_no_secret(base):
    """Four boxes of shape, none of them the key."""
    from tinyassets.api.http_connection import remove_http

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    secret = "ghp_" + "s" * 36
    _deposit("u-1", scopes=[f"git_read:{REPO}"], secret=secret)

    out = remove_http(universe_id="u-1",
                      payload=json.dumps({"destination": "github"}))
    assert secret not in json.dumps(out)


def test_removing_something_absent_readbacks_empty_not_missing(base):
    """Idempotent removal keeps its shape: the keys are always there, empty."""
    from tinyassets.api.http_connection import remove_http

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    out = remove_http(universe_id="u-1",
                      payload=json.dumps({"destination": "never-deposited"}))

    assert out["status"] == "removed"
    assert out["removed_endpoints"] == []
    assert out["removed_scopes"] == []


def test_the_owner_is_shown_the_git_authority_they_are_granting(base):
    """The sentence they say yes to has to name the git scopes.

    It listed methods and paths only, so a connection could carry
    ``git_write`` on a repository the owner was never shown -- the strongest
    authority in the grant, and the one thing absent from the grant sentence.

    It is also what makes the removal readback honest. I claimed the readback
    repeated only what the owner had already seen; that was false for scopes
    until this (Codex, R2). The claim and the fix are the same line of code, so
    they are tested together.
    """
    _make_universe(base, "u-1", admin="alice")
    _login("alice")

    ask = _ask("u-1", kind="API", title="Connect GitHub",
               fields=[{"name": "token", "label": "Token", "type": "secret"}],
               action={"type": "connect_http", "destination": "github",
                       "auth_scheme": "bearer",
                       "endpoints": [{"host": "api.github.com",
                                      "path_template": "/repos/o/r/pulls",
                                      "methods": ["POST"]}],
                       "scopes": [f"git_read:{REPO}", f"git_write:{REPO}"]})

    sentence = ask["grant_sentence"]
    assert "api.github.com/repos/o/r/pulls" in sentence, "endpoints still shown"
    assert "git" in sentence.lower(), "the git authority is not mentioned at all"
    # Assert the MEANING, not the exact wording -- an over-specific string
    # match here just makes the sentence unimprovable.
    lowered = sentence.lower()
    assert f"write to {REPO}" in lowered, (
        "the owner is not told this key may WRITE to the repository"
    )
    assert f"read {REPO}" in lowered


def test_a_key_with_no_git_scopes_says_nothing_about_git(base):
    """No ceremony where there is no authority: the sentence stays as it was."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")

    ask = _ask("u-1", kind="API", title="Connect something",
               fields=[{"name": "token", "label": "Token", "type": "secret"}],
               action={"type": "connect_http", "destination": "plain",
                       "auth_scheme": "bearer",
                       "endpoints": [{"host": "api.example.com",
                                      "path_template": "/v1/x",
                                      "methods": ["POST"]}]})

    assert "git" not in ask["grant_sentence"].lower()


def test_an_endpoint_with_QUERY_policy_survives_the_round_trip(base):
    """W2: the readback has to be re-depositable, not merely informative.

    The first version hand-wrote the endpoint serialization and drifted from the
    validator immediately: it dropped `allowed_query` and `required_query` and
    emitted the pattern maps as stored TUPLES, so a rebuild was refused with
    "query_patterns must be an object". It looked like a rotation right up to
    the point the deposit said no.

    `OutboundEndpoint.as_dict()` already existed and is exactly what the
    validator parses. The rule: never hand-write the inverse of a parser that
    publishes one.
    """
    from tinyassets.api.http_connection import connect_http

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    endpoint = {
        "host": "api.example.com",
        "path_template": "/v2/items/{id}",
        "methods": ["GET", "PUT"],
        "param_patterns": {"id": "[0-9]{1,12}"},
        "allowed_query": ["ref", "page"],
        "query_patterns": {"ref": "[a-z0-9._-]{1,40}"},
        "required_query": ["ref"],
    }
    assert connect_http(universe_id="u-1", payload=json.dumps({
        "destination": "svc", "auth_scheme": "bearer", "secret": "tok_" + "a" * 20,
        "allowed_endpoints": [endpoint],
    }))["status"] == "provisioned"

    ask = _ask("u-1", kind="API", title="Rotate svc", fields=[],
               action={"type": "remove_http", "destination": "svc"})
    gone = _owner_answer("u-1", request_id=ask["request_id"], values={})

    [read_back] = gone["removed_endpoints"]
    assert read_back["allowed_query"] == ["ref", "page"]
    assert read_back["required_query"] == ["ref"]
    assert read_back["query_patterns"] == {"ref": "[a-z0-9._-]{1,40}"}, (
        "query patterns came back as something the validator refuses"
    )

    # The proof that matters: it goes back in.
    again = connect_http(universe_id="u-1", payload=json.dumps({
        "destination": gone["destination"],
        "auth_scheme": gone["auth_scheme"],
        "secret": "tok_" + "b" * 20,
        "allowed_endpoints": gone["removed_endpoints"],
    }))
    assert again["status"] == "provisioned", again


def test_a_scope_only_EXTENSION_says_what_it_grants(base):
    """W4: answering it grants repository write, so the tab must say so.

    The extension sentence was assembled separately from the deposit's, so
    teaching the deposit about git scopes left this one silent -- and a
    scope-only extension, which carries no endpoints at all, rendered the empty
    phrase "reach ." while granting write access to a repository.

    One builder now serves both, which is why this cannot drift again.
    """
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")

    ask = _ask("u-1", kind="API", title="Also let me push", fields=[],
               action={"type": "extend_http", "destination": "github",
                       "scopes": [f"git_write:{REPO}"]})

    sentence = ask["grant_sentence"]
    assert sentence.strip(), "a scope-only extension rendered nothing at all"
    assert "reach ." not in sentence, "the empty-endpoint phrasing is back"
    assert f"write to {REPO}" in sentence.lower(), (
        "the owner is asked to approve repository WRITE without being told"
    )


def test_a_grant_sentence_never_promises_more_than_it_lists(base):
    """W3: the sentence closed with "nothing else" and then listed more.

    Appending git authority after "- nothing else." produced a sentence that
    contradicted itself in the same breath. Scopes belong INSIDE the list the
    sentence closes over.
    """
    _make_universe(base, "u-1", admin="alice")
    _login("alice")

    ask = _ask("u-1", kind="API", title="Connect GitHub",
               fields=[{"name": "t", "label": "Token", "type": "secret"}],
               action={"type": "connect_http", "destination": "github",
                       "auth_scheme": "bearer",
                       "endpoints": [{"host": "api.github.com",
                                      "path_template": "/repos/o/r/pulls",
                                      "methods": ["POST"]}],
                       "scopes": [f"git_write:{REPO}"]})

    import re

    sentence = ask["grant_sentence"]
    # The regression was a whole CLAUSE appended after the sentence closed:
    # "... - nothing else. It may also use git to ...". Enumerating after
    # "nothing else:" is the correct form and must not trip this.
    assert not re.search(r"nothing else\.\s+\S", sentence), (
        "a clause follows the sentence that said there was nothing else: " + sentence
    )
    assert sentence.rstrip().endswith(".")
    assert f"write to {REPO}" in sentence.lower(), "the git authority vanished"
