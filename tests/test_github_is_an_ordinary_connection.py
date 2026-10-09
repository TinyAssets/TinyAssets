"""GitHub is an ordinary universe connection, not platform machinery.

Founder, 2026-09-24: a universe pushes branches, opens pull requests and
comments with its OWNER's own connected credential, never a platform token --
and GitHub is not special. A user who wants their universe on GitHub connects it
the way they connect anything (the ``connect`` request), and their workflow
reaches it through the generic authenticated call. A user who has not connected
it simply has no connection: the generic refusal, no GitHub-shaped message.

What these pin, through the real request rail, vault, ledger, authority checks
and credential-blind broker dispatch (only the child spawn is elided, so the
loopback standing in for api.github.com is reachable):

* the platform's own GitHub token never reaches the wire from any universe
  action, and the GitHub-only setup path (the WorkOS pipe) is gone;
* with no connection, a pull request is refused before the wire;
* with the owner's connection, the call carries exactly the owner's key plus
  the connection's constant headers;
* another user's connection is never used, even when named explicitly.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from tests.test_authenticated_external_call_effector import (  # noqa: F401
    _a_reviewer_that_approves,  # autouse: an explicit approving D1d reviewer
    _install_loopback_driver,
    _Loopback,
)
from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import DevAuthProvider, Identity

ALICE, ALICE_UID = "alice", "u-alice"
BOB, BOB_UID = "bob", "u-bob"
ALICE_PAT = "github_pat_" + "A" * 40
BOB_PAT = "github_pat_" + "B" * 40
PLATFORM_TOKEN = "ghs_platform" + "P" * 30

#: What an owner would declare for "open a pull request and comment on it".
#: Nothing here is platform code: it is the connection the owner approves.
GITHUB_ASK = {
    "type": "connect",
    "destination": "github",
    "auth_scheme": "bearer",
    "endpoints": [
        {"host": "api.github.com", "path_template": "/repos/octo/hello/pulls",
         "methods": ["POST"]},
        {"host": "api.github.com",
         "path_template": "/repos/octo/hello/issues/{number}/comments",
         "methods": ["POST"], "param_patterns": {"number": "[0-9]{1,9}"}},
    ],
    "uses": {"call": {}},
    "constant_headers": {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    },
}
_PAT_FIELD = [{"name": "token", "type": "secret", "label": "Fine-grained token"}]


class _Auth:
    def __init__(self, identity):
        self.identity = identity

    def resolve_token(self, token):
        return self.identity if token == "valid" else None

    def is_auth_required(self):
        return True

    def register_client(self, metadata):
        return {"client_id": "test-client", **metadata}

    def create_authorization(self, *a, **k):
        return "test-code"

    def exchange_code(self, *a, **k):
        return None


def _login(user):
    set_provider(_Auth(Identity(user_id=user, username=user,
                                capabilities=["tinyassets.universe.write"])))
    auth_middleware("valid")


@pytest.fixture
def data(tmp_path, monkeypatch):
    """Two users, each the admin of their own universe. Nobody logged in yet."""
    from tinyassets.daemon_server import grant_universe_access

    base = tmp_path / "data"
    for user, uid in ((ALICE, ALICE_UID), (BOB, BOB_UID)):
        (base / uid).mkdir(parents=True)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    # The platform's own token is IN the environment, exactly as it is in the
    # deployed daemon today. Nothing a universe does may send it.
    monkeypatch.setenv("GH_TOKEN", PLATFORM_TOKEN)
    monkeypatch.setenv("GITHUB_TOKEN", PLATFORM_TOKEN)
    monkeypatch.setenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", "1")
    for user, uid in ((ALICE, ALICE_UID), (BOB, BOB_UID)):
        grant_universe_access(base, universe_id=uid, actor_id=user,
                              permission="admin", granted_by=user)
    yield base
    set_provider(DevAuthProvider())
    auth_middleware("dev")


def _connect(base, user, uid, pat):
    """The owner answers the universe's ordinary ``connect`` request."""
    from tests.owner_answer import answer_request
    from tinyassets.api.pending_requests import request_from_user
    from tinyassets.effectors import authenticated_external_call as aec
    from tinyassets.storage.effector_consents import grant_consent

    _login(user)
    asked = request_from_user(universe_id=uid, payload=json.dumps({
        "kind": "API", "title": "Connect GitHub", "body": "",
        "action": GITHUB_ASK, "fields": _PAT_FIELD,
    }))
    assert asked["status"] == "pending", asked
    answered = answer_request(universe_id=uid, payload=json.dumps(
        {"request_id": asked["request_id"], "values": {"token": pat}}))
    assert answered["status"] == "answered", answered
    grant_consent(base / uid, sink=aec.EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
                  destination="github", granted_by=user)
    return answered


@pytest.fixture
def broker(monkeypatch):
    """The real in-process broker owns admission and credential dispatch.

    Only HTTPS is redirected to the synthetic upstream; the shared broker
    fixture supplies the isolated broker protocol and its private ledger.
    """
    loop = _Loopback(responder=lambda *_: b'{"number": 7}')
    _install_loopback_driver(monkeypatch, loop.port)
    yield loop
    loop.stop()


def _open_pr(base, uid, *, connection_id, grant_id):
    from tinyassets.effectors import authenticated_external_call as aec

    packet = {
        "sink": aec.EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        "connection_id": connection_id,
        "grant_id": grant_id,
        "verb": "POST",
        "request": {"method": "POST", "path": "/repos/octo/hello/pulls",
                    "body": {"title": "Small fix", "head": "tiny/fix", "base": "main"}},
    }
    return aec.run_authenticated_external_call_effector(
        node_id="open_pr", output_keys=["out"], run_state={"out": json.dumps(packet)},
        base_path=str(base / uid), run_id="r1",
    )


# --------------------------------------------------------------------------- #
# The platform's token and the GitHub-only setup path are gone.
# --------------------------------------------------------------------------- #


def test_no_universe_action_sends_the_platforms_github_token(data, monkeypatch):
    """The change-context reader used to attach the daemon's GH_TOKEN to every
    GitHub read a user asked for. Now there is no such action to call."""
    from tinyassets.api.universe import _universe_impl

    sent: list[dict] = []

    def record(req, *_a, **_k):
        headers = dict(req.header_items()) if hasattr(req, "header_items") else {}
        sent.append(headers)
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", record)
    # The deployment-default repository is how the reader was reachable at all.
    monkeypatch.setenv("TINYASSETS_GITHUB_REPO", "octo/hello")
    set_provider(DevAuthProvider())
    auth_middleware("dev")
    reply = json.loads(_universe_impl(
        action="community_change_context", universe_id=ALICE_UID,
    ))

    assert all(PLATFORM_TOKEN not in json.dumps(h) for h in sent), sent
    assert sent == []
    assert reply.get("error", "").startswith("Unknown action"), reply


def test_the_pr_opener_that_fell_back_to_the_platform_token_is_gone(data):
    """``open_auto_ship_pr`` opened a pull request with a GitHub-only vault
    record (``vcs``/``github``, read by ``resolve_github_token``) -- a second
    credential store beside connections -- and its module fell back to the
    daemon's GH_TOKEN/GITHUB_TOKEN when called without a universe. A universe
    opens a pull request the way it calls any API: through its owner's
    connection."""
    from tinyassets import credential_vault
    from tinyassets.api.auto_ship_actions import _AUTO_SHIP_ACTIONS

    assert "open_auto_ship_pr" not in _AUTO_SHIP_ACTIONS
    assert not hasattr(credential_vault, "resolve_github_token")
    with pytest.raises(ModuleNotFoundError):
        __import__("tinyassets.auto_ship_pr")


def test_connecting_github_is_the_ordinary_connect_request_not_a_pipe(data):
    """``write_graph target=connection operation=connect`` used to start a
    GitHub-only WorkOS pipe whose credential nothing could ever resolve. It is
    no longer a GitHub path: the operation is refused and names the generic
    ones."""
    from tinyassets.universe_server import write_graph

    _login(ALICE)
    reply = json.loads(write_graph(
        target="connection", operation="connect", graph_id=ALICE_UID,
        payload_json=json.dumps({"destination": "octo/hello"}),
    ))

    assert reply.get("error") == "unknown_connection_action", reply
    assert reply.get("provider") != "github"
    assert "authorization_url" not in reply


# --------------------------------------------------------------------------- #
# The generic path does everything a pull request needs.
# --------------------------------------------------------------------------- #


def test_without_a_connection_a_pull_request_is_refused_before_the_wire(data, broker):
    _login(ALICE)
    evidence = _open_pr(data, ALICE_UID, connection_id="http_nothing",
                        grant_id="grant_nothing")

    assert evidence.get("delivered") is not True
    assert evidence["error_kind"] == "connection_authority_unavailable", evidence
    assert broker.recorded == []


def test_the_owners_connection_carries_exactly_the_owners_key(data, broker):
    alice = _connect(data, ALICE, ALICE_UID, ALICE_PAT)

    evidence = _open_pr(data, ALICE_UID, connection_id=alice["connection_id"],
                        grant_id=alice["grant_id"])

    assert evidence["delivered"] is True, evidence
    (wire,) = broker.recorded
    headers = {k.lower(): v for k, v in wire["headers"].items()}
    assert wire["method"] == "POST"
    assert wire["path"] == "/repos/octo/hello/pulls"
    assert headers["host"] == "api.github.com"
    assert headers["authorization"] == f"Bearer {ALICE_PAT}"
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["x-github-api-version"] == "2022-11-28"
    assert json.loads(wire["body"]) == {
        "title": "Small fix", "head": "tiny/fix", "base": "main"}
    assert PLATFORM_TOKEN not in json.dumps(wire["headers"])


def test_another_users_connection_is_never_used(data, broker):
    alice = _connect(data, ALICE, ALICE_UID, ALICE_PAT)
    bob = _connect(data, BOB, BOB_UID, BOB_PAT)

    # Bob's universe names Alice's connection outright: refused, nothing sent.
    stolen = _open_pr(data, BOB_UID, connection_id=alice["connection_id"],
                      grant_id=alice["grant_id"])
    assert stolen.get("delivered") is not True
    assert stolen["error_kind"] == "connection_authority_unavailable", stolen
    assert broker.recorded == []

    # Each universe's own call carries its own owner's key, and only that.
    assert _open_pr(data, BOB_UID, connection_id=bob["connection_id"],
                    grant_id=bob["grant_id"])["delivered"] is True
    _login(ALICE)
    assert _open_pr(data, ALICE_UID, connection_id=alice["connection_id"],
                    grant_id=alice["grant_id"])["delivered"] is True
    keys = [w["headers"]["Authorization"] for w in broker.recorded]
    assert keys == [f"Bearer {BOB_PAT}", f"Bearer {ALICE_PAT}"]


# --------------------------------------------------------------------------- #
# Where git goes is the connection's declared field, never a service table.
# --------------------------------------------------------------------------- #


def _connect_forge(base, *, git_host=None):
    """An owner connects a forge whose git is NOT on its API host."""
    from tests.owner_answer import answer_request
    from tinyassets.api.pending_requests import request_from_user

    _login(ALICE)
    action = {
        "type": "connect", "destination": "forge", "auth_scheme": "bearer",
        "endpoints": [{"host": "api.forge.example",
                       "path_template": "/repos/octo/hello/pulls",
                       "methods": ["POST"]}],
        "scopes": ["git_write:octo/hello"],
        "uses": {"call": {}},
    }
    if git_host is not None:
        action["git_host"] = git_host
    asked = request_from_user(universe_id=ALICE_UID, payload=json.dumps({
        "kind": "API", "title": "Connect the forge", "body": "",
        "action": action, "fields": _PAT_FIELD,
    }))
    assert asked["status"] == "pending", asked
    answered = answer_request(universe_id=ALICE_UID, payload=json.dumps(
        {"request_id": asked["request_id"], "values": {"token": ALICE_PAT}}))
    assert answered["status"] == "answered", answered
    return asked, answered


def _stored(base, connection_id):
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(base / ".broker" / "outbound.db", data_root=base)
    return ledger._get_connection_resource(connection_id)


def test_the_per_service_git_host_table_is_gone():
    from tinyassets.storage import workspace_authority

    assert not hasattr(workspace_authority, "FORGE_GIT_HOSTS")
    assert not hasattr(workspace_authority, "PROVIDER_PIPE_HOSTS")


def test_a_declared_git_host_is_where_git_goes(data):
    from tinyassets.effectors.workspace import transport_host_for

    asked, answered = _connect_forge(data, git_host="git.forge.example")

    # The owner read where the key goes before pasting it.
    assert "on git.forge.example" in asked["grant_sentence"], asked["grant_sentence"]
    resource = _stored(data, answered["connection_id"])
    assert resource.git_host == "git.forge.example"
    assert transport_host_for(resource) == "git.forge.example"


def test_without_a_declaration_git_uses_the_connections_own_host(data):
    """No per-service default: api.github.com is not quietly turned into
    github.com, and api.forge.example is not turned into anything."""
    from tinyassets.effectors.workspace import transport_host_for
    from tinyassets.storage.workspace_authority import git_host_for_endpoints

    _, answered = _connect_forge(data)

    resource = _stored(data, answered["connection_id"])
    assert resource.git_host == ""
    assert transport_host_for(resource) == "api.forge.example"
    assert git_host_for_endpoints(["api.github.com"]) == "api.github.com"


# --------------------------------------------------------------------------- #
# Tier 2 round 1: a declared git host is seen and approved wherever it matters.
# The reproduced chain: (1) a git_host on an exact ask with no git scope was
# stored without the owner seeing it; (2) a scope-only extension then passed on
# it, naming no host; (3) the workspace consent said "the connection's host";
# (4) the push sent the key there. Each step now names the host or refuses.
# --------------------------------------------------------------------------- #

EVIL = "evil.example.com"
_FORGE_ENDPOINT = {"host": "api.forge.example",
                   "path_template": "/repos/o/r/pulls", "methods": ["POST"]}


def _raise(action, *, kind="API", fields=_PAT_FIELD):
    from tinyassets.api.pending_requests import request_from_user

    return request_from_user(universe_id=ALICE_UID, payload=json.dumps({
        "kind": kind, "title": "t", "body": "", "action": action, "fields": fields,
    }))


def _answer_ok(asked, values):
    from tests.owner_answer import answer_request

    return answer_request(universe_id=ALICE_UID, payload=json.dumps(
        {"request_id": asked["request_id"], "values": values}))


def _deposit_with_git_host(git_host, scopes=("git_write:o/a",)):
    asked = _raise({"type": "connect_http", "destination": "forge",
                    "auth_scheme": "bearer", "endpoints": [_FORGE_ENDPOINT],
                    "scopes": list(scopes), "git_host": git_host})
    assert asked["status"] == "pending", asked
    answered = _answer_ok(asked, {"token": ALICE_PAT})
    assert answered["status"] == "answered", answered
    return asked, answered


def test_step1_a_git_host_on_an_exact_ask_with_no_git_scope_is_refused(data):
    _login(ALICE)
    asked = _raise({"type": "connect_http", "destination": "forge",
                    "auth_scheme": "bearer", "endpoints": [_FORGE_ENDPOINT],
                    "git_host": EVIL})
    assert asked.get("status") != "pending", asked
    assert "git_host" in json.dumps(asked)


def test_step1_every_deposit_that_declares_a_git_host_names_it(data):
    _login(ALICE)
    asked, _ = _deposit_with_git_host(EVIL)
    assert f"Git operations with this key go to {EVIL}." in asked["grant_sentence"]
    assert f"o/a on {EVIL}" in asked["grant_sentence"]

    full = _raise({"type": "connect_http", "destination": "forge2",
                   "auth_scheme": "bearer", "access": "full",
                   "hosts": ["api.forge.example"], "git_host": EVIL})
    assert full["status"] == "pending", full
    assert EVIL in full["grant_sentence"]


def test_step2_a_scope_only_extension_names_the_stored_git_host(data):
    _login(ALICE)
    _deposit_with_git_host(EVIL)
    ext = _raise({"type": "extend_http", "destination": "forge",
                  "scopes": ["git_write:o/r"]}, fields=[])
    assert ext["status"] == "pending", ext
    assert f"use git to WRITE to o/r on {EVIL}" in ext["grant_sentence"]


def test_step2_without_a_declaration_the_extension_names_the_endpoint_host(data):
    _login(ALICE)
    asked = _raise({"type": "connect_http", "destination": "forge",
                    "auth_scheme": "bearer", "endpoints": [_FORGE_ENDPOINT]})
    assert _answer_ok(asked, {"token": ALICE_PAT})["status"] == "answered"
    ext = _raise({"type": "extend_http", "destination": "forge",
                  "scopes": ["git_write:o/r"]}, fields=[])
    assert ext["status"] == "pending", ext
    assert "use git to WRITE to o/r on api.forge.example" in ext["grant_sentence"]


def test_step3_the_workspace_consent_names_the_resolved_host(data):
    _login(ALICE)
    _, deposited = _deposit_with_git_host(EVIL, scopes=("git_write:o/r",))
    consent = _raise({"type": "grant_workspace_consent",
                      "connection_id": deposited["connection_id"],
                      "repo": "o/r", "consents": ["workspace_push"]},
                     kind="Approval", fields=[])
    assert consent["status"] == "pending", consent
    assert f"o/r on {EVIL}" in consent["grant_sentence"]
    assert "the connection's host" not in consent["grant_sentence"]


def test_step3_a_consent_row_that_names_no_host_cannot_be_granted(data):
    from tinyassets.api.pending_requests import _grant_sentence

    sentence = _grant_sentence({"action": {
        "type": "grant_workspace_consent", "connection_id": "c", "repo": "o/r",
        "consents": ["workspace_push"]}})
    assert "cannot be granted" in sentence
    assert "the connection's host" not in sentence


def test_step4_a_yes_does_not_follow_the_key_to_a_new_host(data):
    """The owner read one host; the connection is reconnected elsewhere before
    they answer. Neither the consent nor the extension lands."""
    from tinyassets.api.http_connection import remove_http

    _login(ALICE)
    _, deposited = _deposit_with_git_host("git.forge.example", scopes=("git_write:o/r",))
    consent = _raise({"type": "grant_workspace_consent",
                      "connection_id": deposited["connection_id"],
                      "repo": "o/r", "consents": ["workspace_push"]},
                     kind="Approval", fields=[])
    ext = _raise({"type": "extend_http", "destination": "forge",
                  "scopes": ["git_write:o/s"]}, fields=[])
    assert consent["status"] == ext["status"] == "pending"

    assert "error" not in remove_http(universe_id=ALICE_UID,
                                      payload=json.dumps({"destination": "forge"}))
    _deposit_with_git_host(EVIL, scopes=("git_write:o/r",))

    for ask in (consent, ext):
        out = _answer_ok(ask, {})
        assert out.get("status") != "answered", out
        assert out.get("error") == "connection_conflict", out
