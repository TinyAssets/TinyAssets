"""Git scopes and workspace consents: the two gates the workspace sink needs.

A SCOPE says the deposited credential may reach exactly one repository on
github.com; a CONSENT says the owner agreed to that kind of work on it. The
tests hold three lines hardest, because each one is a way the authority could
quietly become something else:

* a git scope is not an HTTP verb, and the proxy's membership test must not
  accept one as a verb now that both live in the same ``scopes`` tuple;
* the binding is exact - ``git_read:owner/name`` grants nothing about
  ``owner/name2``, ``owner/name.git`` or another host; and
* nothing crosses a universe boundary, so a remix carries neither.
"""

from __future__ import annotations

import json

import pytest

from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import AuthProvider, DevAuthProvider, Identity
from tinyassets.storage import workspace_authority as wa
from tinyassets.storage.effector_consents import is_consent_active, list_consents
from tinyassets.storage.outbound_connections import (
    ConnectionLedger,
    CredentialBlindBroker,
    ScopedConnectionProxy,
    _verb_within_scopes,
)

# --------------------------------------------------------------------------
# harness (mirrors tests/test_pending_requests.py)
# --------------------------------------------------------------------------


class _StaticAuthProvider(AuthProvider):
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


def _login(user_id):
    set_provider(_StaticAuthProvider(Identity(
        user_id=user_id, username=user_id,
        capabilities=[
            "tinyassets.universe.write",
            "tinyassets.extensions.read",
            "tinyassets.extensions.write",
        ])))
    auth_middleware("valid")


def _logout():
    set_provider(DevAuthProvider())
    auth_middleware("dev")


@pytest.fixture(autouse=True)
def _reset_auth():
    _logout()
    yield
    _logout()


@pytest.fixture
def base(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    return root


def _make_universe(base, uid, *, admin=""):
    from tinyassets.daemon_server import grant_universe_access

    udir = base / uid
    udir.mkdir(parents=True, exist_ok=True)
    if admin:
        grant_universe_access(base, universe_id=uid, actor_id=admin,
                              permission="admin", granted_by=admin)
    return udir


ENDPOINT_HOST = "api.github.com"
#: Where git actually lives for that connection. The service serves its API on
#: one host and its git on another, so the fixture connection DECLARES
#: `git_host` (there is no per-service table); the consent key and the clone URL
#: both use it -- cloning from the API host returns 403.
GIT_HOST = "github.com"
GITHUB_ENDPOINT = {
    "host": ENDPOINT_HOST,
    "path_template": "/repos/o/r/pulls",
    "methods": ["POST"],
}


def _deposit(uid, *, scopes=None, endpoints=None, destination="github"):
    """connect_http through its own API, the way the rail's answer does."""
    from tinyassets.api.http_connection import connect_http

    document = {
        "destination": destination,
        "secret": "ghp_" + "x" * 30,
        "auth_scheme": "bearer",
        "allowed_endpoints": endpoints if endpoints is not None else [GITHUB_ENDPOINT],
        "git_host": GIT_HOST if endpoints is None else "",
    }
    if scopes is not None:
        document["scopes"] = scopes
    return connect_http(universe_id=uid, payload=json.dumps(document))


def _ask(uid, **payload):
    from tinyassets.api.pending_requests import request_from_user

    return request_from_user(universe_id=uid, payload=json.dumps(payload))


def _answer(uid, **document):
    from tests.owner_answer import answer_request

    return answer_request(universe_id=uid, payload=json.dumps(document))


def _connection(uid, connection_id):
    from pathlib import Path

    from tinyassets.api.helpers import _base_path

    return ConnectionLedger(Path(_base_path()) / "outbound.db").get_connection(
        connection_id
    )


class _Connection:
    """The duck the predicates read: scopes, endpoints, provider, revocation."""

    def __init__(self, scopes=(), hosts=(), provider="http", revoked_at=None):
        self.scopes = tuple(scopes)
        self.allowed_endpoints = tuple(_Endpoint(host) for host in hosts)
        self.provider = provider
        self.revoked_at = revoked_at


class _Endpoint:
    def __init__(self, host):
        self.host = host


# --------------------------------------------------------------------------
# the grammar
# --------------------------------------------------------------------------


def test_a_git_scope_round_trips_through_its_grammar() -> None:
    assert wa.format_git_scope("git_read", "octocat/hello") == "git_read:octocat/hello"
    assert wa.require_git_scope("git_write:octocat/hello") == (
        "git_write",
        "octocat/hello",
    )
    assert wa.parse_git_scope("git_read:o/n") == ("git_read", "o/n")
    assert wa.normalize_repo("  octocat/hello  ") == "octocat/hello"


@pytest.mark.parametrize(
    "repo",
    [
        "octocat/hello.git",          # the same repo under a second spelling
        "octocat/hello/extra",        # a second slash
        "octocat",                    # no owner or no name
        "/hello",
        "octocat/",
        "../etc",                     # traversal
        "octocat/..",
        "oct at/hello",               # whitespace
        "octocat/hel:lo",             # outside the charset
        "",
    ],
)
def test_a_repository_that_is_not_exactly_owner_name_is_refused(repo: str) -> None:
    with pytest.raises(wa.GitScopeError):
        wa.normalize_repo(repo)


@pytest.mark.parametrize(
    "scope",
    ["git_read", "git_read:", "git_read:o", "git_write:o/n.git", "GET", "read:o/n"],
)
def test_a_malformed_git_scope_never_parses(scope: str) -> None:
    assert wa.parse_git_scope(scope) is None
    with pytest.raises(wa.GitScopeError):
        wa.require_git_scope(scope)


def test_anything_git_shaped_is_recognised_as_a_git_scope() -> None:
    """The predicate the verb checks use is SHAPE, not validity: a verb named
    ``git_read`` must be refused whether or not it would parse."""
    assert wa.is_git_scope("git_read") is True
    assert wa.is_git_scope("git_write:o/n") is True
    assert wa.is_git_scope("git_read:garbage") is True
    assert wa.is_git_scope("POST") is False
    assert wa.is_git_scope("") is False
    assert wa.is_git_scope(None) is False


def test_a_git_scope_needs_a_connection_that_reaches_exactly_one_host() -> None:
    """ANY one host. The platform does not name the forge (founder, 2026-08-31).

    A workspace is channel- and workflow-agnostic; ``github.com`` was our own
    demo's host, and pinning it here meant a GitLab, Gitea or self-hosted user
    could not check anything out at all. What the rule actually protects is
    UNAMBIGUITY: ``git_read:owner/name`` names a repository, and a repository
    only means something together with a host.
    """
    wa.validate_git_scopes(["git_read:o/n"], hosts=["api.github.com"])
    wa.validate_git_scopes(["git_read:o/n"], hosts=["github.com"])
    wa.validate_git_scopes(["git_read:o/n"], hosts=["gitlab.example.com"])
    wa.validate_git_scopes(["git_read:o/n"], hosts=["git.internal"])
    # The same host twice is still one host.
    wa.validate_git_scopes(["git_read:o/n"], hosts=["git.internal", "git.internal"])
    # A declared git host supplies the host whatever the endpoints are.
    wa.validate_git_scopes(["git_read:o/n"], hosts=[], git_host="git.example.com")
    wa.validate_git_scopes(
        ["git_read:o/n"], hosts=["a.example", "b.example"], git_host="git.example.com"
    )
    with pytest.raises(wa.GitScopeError, match="ONE host"):
        # Two hosts: the same credential could be spent on whichever the
        # caller preferred, which is what the scope exists to stop.
        wa.validate_git_scopes(["git_read:o/n"], hosts=["api.github.com", "evil.com"])
    with pytest.raises(wa.GitScopeError, match="ONE host"):
        wa.validate_git_scopes(["git_read:o/n"], hosts=["gitlab.example.com", "evil.com"])
    with pytest.raises(wa.GitScopeError, match="ONE host"):
        # No endpoints and no declared git_host: nothing says what the host is.
        wa.validate_git_scopes(["git_read:o/n"], hosts=[])
    # An HTTP-only scope tuple is not this rule's business.
    wa.validate_git_scopes(["POST", "GET"], hosts=["api.example.com"])


def test_a_lookalike_host_is_just_a_host_the_owner_declared() -> None:
    """``github.com.evil.com`` used to be refused as a near-miss of the pin.

    With no pin there is nothing to near-miss: it is one host, on the owner's
    own connection, with the owner's own credential, and the transport pins to
    the address it resolves to. Treating it specially would be the platform
    having an opinion about which forges are real.
    """
    wa.validate_git_scopes(["git_read:o/n"], hosts=["github.com.evil.com"])
    assert wa.git_host_for_endpoints(["github.com.evil.com"]) == "github.com.evil.com"


def test_the_scope_binding_is_exactly_one_repository() -> None:
    connection = _Connection(
        scopes=("POST", "git_read:octocat/hello"), hosts=("api.github.com",)
    )
    assert wa.has_git_scope(connection, "git_read", "octocat/hello") is True
    assert wa.has_git_scope(connection, "git_read", "octocat/hello2") is False
    assert wa.has_git_scope(connection, "git_read", "octocat2/hello") is False
    assert wa.has_git_scope(connection, "git_write", "octocat/hello") is False
    assert wa.has_git_scope(connection, "git_read", "octocat/hello.git") is False
    assert wa.has_git_scope(connection, "not_a_kind", "octocat/hello") is False
    assert wa.has_git_scope(None, "git_read", "octocat/hello") is False


def test_a_revoked_or_ambiguous_connection_grants_no_git_scope() -> None:
    revoked = _Connection(
        scopes=("git_read:o/n",), hosts=("api.github.com",), revoked_at=1.0
    )
    assert wa.has_git_scope(revoked, "git_read", "o/n") is False
    # A row that somehow holds a git scope while reaching TWO hosts grants
    # nothing at READ time either: the check does not rely on the write path
    # having been the only way in.
    ambiguous = _Connection(
        scopes=("git_read:o/n",), hosts=("api.github.com", "api.example.com")
    )
    assert wa.has_git_scope(ambiguous, "git_read", "o/n") is False
    # One host that is not github is a perfectly good git connection.
    self_hosted = _Connection(scopes=("git_read:o/n",), hosts=("git.internal",))
    assert wa.has_git_scope(self_hosted, "git_read", "o/n") is True
    assert wa.connection_git_host(self_hosted) == "git.internal"


def test_a_malformed_stored_scope_is_dropped_not_raised() -> None:
    connection = _Connection(
        scopes=("git_read:garbage", "git_read:o/n"), hosts=("api.github.com",)
    )
    assert wa.connection_git_scopes(connection) == {("git_read", "o/n")}


def test_the_consent_destination_has_one_spelling() -> None:
    assert (
        wa.workspace_consent_destination(
            "workspace_checkout",
            "octocat/hello",
            connection_id="http_ab",
            host="github.com",
        )
        == "checkout:http_ab:github.com/octocat/hello"
    )
    assert (
        wa.workspace_consent_destination(
            "workspace_provision",
            "octocat/hello",
            connection_id="http_ab",
            host="github.com",
        )
        == "provision:http_ab:github.com/octocat/hello"
    )
    assert wa.parse_workspace_consent_destination(
        "push:http_ab:github.com/octocat/hello") == {
        "consent": "workspace_push",
        "operation": "push",
        "connection_id": "http_ab",
        "host": "github.com",
        "repo": "octocat/hello",
    }
    # Any host, spelled by the caller: the grammar has no opinion about forges.
    assert (
        wa.workspace_consent_destination(
            "workspace_checkout",
            "octocat/hello",
            connection_id="http_ab",
            host="git.internal.example",
        )
        == "checkout:http_ab:git.internal.example/octocat/hello"
    )
    with pytest.raises(wa.GitScopeError, match="host"):
        # No host at all is a key the sink would never look up.
        wa.workspace_consent_destination(
            "workspace_checkout", "octocat/hello", connection_id="http_ab", host=""
        )
    assert wa.parse_workspace_consent_destination("api.github.com/repos") is None
    # The pre-connection spelling is not one of ours any more, so an old row
    # reads as absent rather than as a consent for every connection.
    assert (
        wa.parse_workspace_consent_destination("checkout:github.com/octocat/hello")
        is None
    )
    with pytest.raises(wa.GitScopeError):
        wa.workspace_consent_destination(
            "workspace_delete", "octocat/hello", connection_id="http_ab", host=GIT_HOST)


@pytest.mark.parametrize(
    "connection_id", ["", "http:ab", "http/ab", "conn ab", "x" * 201, None]
)
def test_a_connection_id_that_could_forge_a_key_is_refused(connection_id) -> None:
    """It sits between two separators in the destination: a colon or a slash
    would make the row ambiguous, and an ambiguous row is a forgeable one."""
    with pytest.raises(wa.GitScopeError):
        wa.workspace_consent_destination(
            "workspace_checkout", "octocat/hello", connection_id=connection_id, host=GIT_HOST)


# --------------------------------------------------------------------------
# the ledger stores it, or refuses it
# --------------------------------------------------------------------------


def _ledger(tmp_path, actor="user-1"):
    return ConnectionLedger(
        tmp_path / "outbound.db", verify_authenticated_principal=lambda: actor
    )


def _create(ledger, *, scopes, endpoints=(GITHUB_ENDPOINT,), **over):
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


def test_the_ledger_stores_a_git_scope_on_a_github_connection(tmp_path) -> None:
    view = _create(_ledger(tmp_path), scopes=("POST", "git_read:octocat/hello"))
    assert "git_read:octocat/hello" in view.scopes
    assert wa.has_git_scope(view, "git_read", "octocat/hello") is True


def test_the_ledger_refuses_a_git_scope_on_a_connection_that_reaches_two_hosts(
    tmp_path,
) -> None:
    with pytest.raises(wa.GitScopeError, match="ONE host"):
        _create(
            _ledger(tmp_path),
            scopes=("POST", "git_read:octocat/hello"),
            endpoints=(
                GITHUB_ENDPOINT,
                {
                    "host": "api.example.com",
                    "path_template": "/v1/things",
                    "methods": ["POST"],
                },
            ),
        )


def test_the_ledger_stores_a_git_scope_on_a_self_hosted_forge(tmp_path) -> None:
    """The connection names the forge; the platform does not."""
    view = _create(
        _ledger(tmp_path),
        scopes=("POST", "git_read:octocat/hello"),
        endpoints=(
            {
                "host": "git.internal.example",
                "path_template": "/octocat/hello",
                "methods": ["POST"],
            },
        ),
    )
    assert wa.has_git_scope(view, "git_read", "octocat/hello") is True
    assert wa.connection_git_host(view) == "git.internal.example"


def test_the_ledger_refuses_a_malformed_git_scope(tmp_path) -> None:
    with pytest.raises(wa.GitScopeError):
        _create(_ledger(tmp_path), scopes=("POST", "git_read:octocat/hello.git"))
    with pytest.raises(wa.GitScopeError):
        _create(_ledger(tmp_path), scopes=("POST", "git_write:../../etc"))


def test_the_scope_extension_refuses_a_git_scope_once_two_hosts_are_reachable(
    tmp_path,
) -> None:
    """The extension writes the UNION, so the union is what has to be one host.

    This is the widening that matters now that any host is allowed: a
    connection legitimately scoped for one forge must not gain a second forge
    while keeping the scope.
    """
    ledger = _ledger(tmp_path)
    _create(ledger, scopes=("POST",))
    stored_endpoints_json, stored_scopes_json = ledger.policy_json("conn-1")
    with pytest.raises(wa.GitScopeError, match="ONE host"):
        ledger.extend_http_connection_endpoints(
            connection_id="conn-1",
            endpoints=[
                GITHUB_ENDPOINT,
                {
                    "host": "api.example.com",
                    "path_template": "/v1/things",
                    "methods": ["POST"],
                },
            ],
            scopes=("POST", "git_read:octocat/hello"),
            expected_endpoints_json=stored_endpoints_json,
            expected_scopes_json=stored_scopes_json,
        )


# --------------------------------------------------------------------------
# a git scope is not an HTTP verb
# --------------------------------------------------------------------------


def test_the_verb_check_never_accepts_a_git_scope_as_a_verb() -> None:
    scopes = ("POST", "git_read:octocat/hello")
    assert _verb_within_scopes("POST", scopes) is True
    assert _verb_within_scopes("git_read:octocat/hello", scopes) is False
    assert _verb_within_scopes("git_read", scopes) is False
    assert _verb_within_scopes("DELETE", scopes) is False
    assert _verb_within_scopes(None, scopes) is False
    assert _verb_within_scopes("", scopes) is False


def test_the_scoped_proxy_refuses_a_git_scope_as_a_verb() -> None:
    proxy = ScopedConnectionProxy(
        grant_id="grant-1",
        provider="http",
        destination="github",
        scopes=("POST", "git_read:octocat/hello"),
        _channel=None,
    )
    with pytest.raises(PermissionError, match="outside the granted connection scope"):
        proxy.request("git_read:octocat/hello", {"url": "https://api.github.com/x"})


def test_the_broker_refuses_a_git_scope_as_a_verb(tmp_path) -> None:
    """The credentialed dispatcher is the one that would spend the token."""
    ledger = _ledger(tmp_path)
    _create(ledger, scopes=("POST", "git_read:octocat/hello"))
    ledger.grant_connection(
        grant_id="grant-1",
        connection_id="conn-1",
        owner_user_id="user-1",
        universe_id="u-1",
    )
    dispatched: list[str] = []

    broker = CredentialBlindBroker(
        ledger,
        resolve_credential=lambda ref: "secret",
        network_request=lambda **kwargs: dispatched.append(kwargs.get("method", "")),
    )
    with pytest.raises(PermissionError, match="outside the granted connection scope"):
        broker.dispatch("grant-1", "git_read:octocat/hello", {"url": "https://x"})
    assert dispatched == []


# --------------------------------------------------------------------------
# the request rail asks for it
# --------------------------------------------------------------------------


def test_the_rail_accepts_a_git_scope_on_a_github_ask(base) -> None:
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    # An extension ask is checked against the held key when it is RAISED, so
    # the key has to exist; without one the rail says so and raises no tab.
    _deposit("u-1")
    asked = _ask(
        "u-1",
        kind="API",
        title="Let me check out the repo",
        body="I need to clone it to run the tests.",
        action={
            "type": "extend_http",
            "destination": "github",
            "endpoints": [GITHUB_ENDPOINT],
            "scopes": ["git_read:octocat/hello", "git_write:octocat/hello"],
        },
    )
    assert asked["status"] == "pending", asked
    assert asked["action"]["scopes"] == [
        "git_read:octocat/hello",
        "git_write:octocat/hello",
    ]


def test_the_rail_refuses_a_git_scope_on_an_ask_that_reaches_two_hosts(base) -> None:
    """One host is fine whatever it is; two make the scope ambiguous."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    asked = _ask(
        "u-1",
        kind="API",
        title="Widen my key",
        body="",
        action={
            "type": "extend_http",
            "destination": "example",
            "endpoints": [
                {
                    "host": "api.example.com",
                    "path_template": "/v1/things",
                    "methods": ["POST"],
                },
                GITHUB_ENDPOINT,
            ],
            "scopes": ["git_read:octocat/hello"],
        },
    )
    assert asked["error"] == "request_invalid"
    assert "ONE host" in asked["detail"]


def test_the_rail_refuses_an_http_verb_smuggled_in_as_a_scope(base) -> None:
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    asked = _ask(
        "u-1",
        kind="API",
        title="Widen my key",
        body="",
        action={
            "type": "extend_http",
            "destination": "github",
            "endpoints": [GITHUB_ENDPOINT],
            "scopes": ["DELETE"],
        },
    )
    assert asked["error"] == "request_invalid"
    assert "git scopes only" in asked["detail"]


def test_answering_the_extend_ask_puts_the_git_scope_on_the_connection(base) -> None:
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    deposited = _deposit("u-1")
    assert deposited.get("connection_id"), deposited
    assert wa.connection_git_scopes(
        _connection("u-1", deposited["connection_id"])
    ) == set()

    asked = _ask(
        "u-1",
        kind="API",
        title="Let me check out the repo",
        body="",
        action={
            "type": "extend_http",
            "destination": "github",
            "endpoints": [GITHUB_ENDPOINT],
            "scopes": ["git_read:octocat/hello"],
        },
    )
    assert asked["status"] == "pending", asked
    # No paste box: the key is already in the vault, this is the yes.
    assert asked["fields"] == []
    answered = _answer("u-1", request_id=asked["request_id"], values={})
    assert answered["status"] == "answered", answered

    connection = _connection("u-1", deposited["connection_id"])
    assert wa.has_git_scope(connection, "git_read", "octocat/hello") is True
    assert wa.has_git_scope(connection, "git_write", "octocat/hello") is False
    # The HTTP verb the endpoints imply is still there, unchanged.
    assert "POST" in connection.scopes


def test_a_later_deposit_does_not_drop_a_git_scope(base) -> None:
    """Scopes are otherwise derived from endpoint methods, so a scope nothing
    can re-derive would vanish on the next deposit and the sink would start
    refusing checkouts nobody revoked."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    deposited = _deposit("u-1", scopes=["git_read:octocat/hello"])
    assert deposited.get("connection_id"), deposited

    again = _deposit("u-1")               # the same ask, no scopes named
    assert not again.get("error"), again
    connection = _connection("u-1", deposited["connection_id"])
    assert wa.has_git_scope(connection, "git_read", "octocat/hello") is True


def test_the_deposit_refuses_an_http_verb_smuggled_in_as_a_scope(base) -> None:
    """The HTTP verbs stay DERIVED from the endpoints. A caller that could name
    its own verbs could widen the HTTP surface without widening the endpoint
    allow-list, which is the whole of the deposit's least-privilege story."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    refused = _deposit("u-1", scopes=["DELETE"])
    assert refused["error"] == "connection_setup_invalid", refused
    assert "git scopes only" in refused["detail"]


def test_a_scope_only_widening_is_not_reported_as_unchanged(base) -> None:
    """extend_http used to short-circuit on "no new endpoints", which left a
    scope-only ask with no route through the verb at all."""
    from tinyassets.api.http_connection import extend_http

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")
    widened = extend_http(
        universe_id="u-1",
        payload=json.dumps(
            {
                "destination": "github",
                "endpoints": [GITHUB_ENDPOINT],
                "scopes": ["git_write:octocat/hello"],
            }
        ),
    )
    assert widened["status"] == "extended", widened
    assert "git_write:octocat/hello" in widened["scopes"]
    # And asking for the same thing twice IS unchanged.
    repeat = extend_http(
        universe_id="u-1",
        payload=json.dumps(
            {
                "destination": "github",
                "endpoints": [GITHUB_ENDPOINT],
                "scopes": ["git_write:octocat/hello"],
            }
        ),
    )
    assert repeat["status"] == "unchanged", repeat


# --------------------------------------------------------------------------
# the typed consents
# --------------------------------------------------------------------------


def _consent_ask(uid, deposited, **over):
    action = {
        "type": "grant_workspace_consent",
        "connection_id": deposited["connection_id"],
        "repo": "octocat/hello",
        "consents": [
            "workspace_checkout",
            "workspace_push",
            "workspace_provision",
        ],
    }
    action.update(over)
    return _ask(
        uid,
        kind="Approval",
        title="May I work on octocat/hello?",
        body="Check it out, run the tests, push a branch.",
        action=action,
    )


def test_granting_the_consents_writes_one_row_per_operation(base) -> None:
    udir = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    deposited = _deposit("u-1", scopes=["git_read:octocat/hello"])
    asked = _consent_ask("u-1", deposited)
    assert asked["status"] == "pending", asked
    assert asked["fields"] == []
    assert "octocat/hello" in asked["grant_sentence"]

    answered = _answer("u-1", request_id=asked["request_id"], values={})
    assert answered["status"] == "answered", answered
    conn = deposited["connection_id"]
    # The key is the GIT host, not the API host the connection declares.
    #
    # Two earlier revisions of this got it wrong in opposite directions. First
    # the rail defaulted to github.com while the sink derived api.github.com
    # from the endpoints, so the owner's yes was recorded where nothing looked
    # for it. Then both sides were made to agree on api.github.com -- CONSISTENT,
    # and still wrong, because git does not live there: the clone came back
    # `403` from https://api.github.com/owner/name.git and the consent looked
    # missing for a repo the owner had plainly granted.
    expected = [
        f"checkout:{conn}:{GIT_HOST}/octocat/hello",
        f"provision:{conn}:{GIT_HOST}/octocat/hello",
        f"push:{conn}:{GIT_HOST}/octocat/hello",
    ]
    assert sorted(answered["destinations"]) == expected
    rows = list_consents(udir, sink="workspace")
    assert sorted(row["destination"] for row in rows) == expected
    assert all(row["granted_by"] == "alice" for row in rows)


def test_a_consent_is_active_only_for_the_exact_operation_and_repo(base) -> None:
    udir = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    deposited = _deposit("u-1")
    asked = _consent_ask("u-1", deposited, consents=["workspace_checkout"])
    _answer("u-1", request_id=asked["request_id"], values={})

    conn = deposited["connection_id"]
    checkout = wa.workspace_consent_destination(
        "workspace_checkout", "octocat/hello", connection_id=conn, host=GIT_HOST)
    assert is_consent_active(udir, sink="workspace", destination=checkout) is True
    for other in (
        wa.workspace_consent_destination(
            "workspace_push", "octocat/hello", connection_id=conn, host=GIT_HOST),
        wa.workspace_consent_destination(
            "workspace_checkout", "octocat/hello2", connection_id=conn, host=GIT_HOST),
        wa.workspace_consent_destination(
            "workspace_checkout", "octocat2/hello", connection_id=conn, host=GIT_HOST),
        wa.workspace_consent_destination(
            "workspace_checkout", "octocat/hello", connection_id="http_other", host=GIT_HOST),
        f"checkout:{conn}:gitlab.com/octocat/hello",
        f"checkout:{conn}:github.com/octocat/hello/extra",
        "checkout:github.com/octocat/hello",
    ):
        assert is_consent_active(udir, sink="workspace", destination=other) is False
    # And it is not a consent for some other sink's destination either.
    assert is_consent_active(udir, sink="github_pr", destination=checkout) is False


def test_two_connections_to_the_same_repo_hold_independent_consents(base) -> None:
    """The delta binds a consent to ``(connection, repo)``. A universe can hold a
    second key under another destination label, and a yes given for one
    credential must not authorize work under the other."""
    from tinyassets.storage.effector_consents import revoke_consent

    udir = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    first = _deposit("u-1", destination="github")
    second = _deposit("u-1", destination="github-two")
    assert first["connection_id"] != second["connection_id"]

    asked = _consent_ask("u-1", first, consents=["workspace_checkout"])
    _answer("u-1", request_id=asked["request_id"], values={})

    def active(connection, repo="octocat/hello"):
        return is_consent_active(
            udir,
            sink="workspace",
            destination=wa.workspace_consent_destination(
                "workspace_checkout",
                repo,
                connection_id=connection["connection_id"],
                host=GIT_HOST,
            ),
        )

    assert active(first) is True
    assert active(second) is False

    asked_two = _consent_ask("u-1", second, consents=["workspace_checkout"])
    _answer("u-1", request_id=asked_two["request_id"], values={})
    assert active(first) is True
    assert active(second) is True

    # Revoking one leaves the other exactly as it was.
    revoke_consent(
        udir,
        sink="workspace",
        destination=wa.workspace_consent_destination(
            "workspace_checkout",
            "octocat/hello",
            connection_id=first["connection_id"], host=GIT_HOST),
    )
    assert active(first) is False
    assert active(second) is True

    from tinyassets.api.cloud_connections import cloud_connections

    listed = cloud_connections(action="list", universe_id="u-1")
    assert [row["connection_id"] for row in listed["workspace_consents"]] == [
        second["connection_id"]
    ]


def test_a_consent_ask_names_a_connection_this_owner_holds(base) -> None:
    udir = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    deposited = _deposit("u-1")
    asked = _consent_ask("u-1", deposited, connection_id="conn-somebody-elses")
    # Refused when RAISED now: the tab must name the host the key would be used
    # against, and a connection this owner does not hold has none to name
    # (Tier 2 review round 1). Same uniform envelope as the answer path.
    assert asked == {"error": "not_found", "resource": "connection"}, asked
    assert list_consents(udir, sink="workspace") == []


@pytest.mark.parametrize(
    "over,fragment",
    [
        ({"repo": "octocat/hello.git"}, ".git"),
        ({"repo": "../etc"}, "traversal"),
        ({"consents": ["workspace_delete"]}, "unknown consent"),
        ({"consents": []}, "at least one"),
        ({"connection_id": ""}, "connection_id"),
    ],
)
def test_a_malformed_consent_ask_never_becomes_a_tab(base, over, fragment) -> None:
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    deposited = _deposit("u-1")
    asked = _consent_ask("u-1", deposited, **over)
    assert asked["error"] == "request_invalid", asked
    assert fragment in asked["detail"]


# --------------------------------------------------------------------------
# nothing crosses a universe boundary
# --------------------------------------------------------------------------


def test_a_remix_carries_neither_the_consents_nor_the_scopes(base, monkeypatch) -> None:
    """The remixer's universe starts with no authority of its own: consents live
    in the universe directory and scopes on the owner's connection, and a branch
    is a SHAPE - copying it copies neither.

    The acting user is switched by ``_login`` plus the env the identity resolver
    reads AT CALL TIME - never ``importlib.reload``. A reload rebinds this
    module's globals while every module that already imported it keeps the old
    objects, so which suite ran first decided the outcome: after
    ``test_run_branch_failure_taxonomy`` this failed with "Branch not found"
    and a ledger writing ``str / str`` (2026-08-30).
    """
    from tinyassets import universe_server as us

    source_dir = _make_universe(base, "alice", admin="alice")
    target_dir = _make_universe(base, "bob", admin="bob")
    _login("alice")
    deposited = _deposit("alice", scopes=["git_read:octocat/hello"])
    asked = _consent_ask("alice", deposited)
    _answer("alice", request_id=asked["request_id"], values={})
    assert len(list_consents(source_dir, sink="workspace")) == 3

    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")
    spec = {
        "name": "Run the tests",
        "entry_point": "ready",
        "node_defs": [
            {
                "node_id": "ready",
                "display_name": "Ready",
                "prompt_template": "Do the work.",
            }
        ],
        "edges": [{"from": "START", "to": "ready"}, {"from": "ready", "to": "END"}],
        "state_schema": [{"name": "x", "type": "str"}],
        "visibility": "public",
    }
    origin = json.loads(us._extensions_impl(action="build_branch", spec_json=json.dumps(spec)))
    assert origin.get("branch_def_id"), origin
    published = json.loads(
        us._extensions_impl(action="publish_version", branch_def_id=origin["branch_def_id"])
    )
    assert published.get("branch_version_id"), published

    _login("bob")
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "bob")
    remixed = json.loads(
        us._extensions_impl(
            action="build_branch",
            spec_json=json.dumps(
                {**spec, "fork_from": published["branch_version_id"]}
            ),
        )
    )
    assert remixed.get("branch_def_id"), remixed
    assert remixed["branch_def_id"] != origin["branch_def_id"]

    # The remixer holds nothing: not the consents, not the connection.
    assert list_consents(target_dir, sink="workspace") == []
    for consent in wa.WORKSPACE_CONSENTS:
        assert (
            is_consent_active(
                target_dir,
                sink="workspace",
                destination=wa.workspace_consent_destination(
                    consent,
                    "octocat/hello",
                    connection_id=deposited["connection_id"], host=GIT_HOST),
            )
            is False
        )
    from tinyassets.api.cloud_connections import cloud_connections

    _login("bob")
    listed = cloud_connections(action="list", universe_id="bob")
    assert listed["connections"] == []
    assert listed["workspace_consents"] == []


# --------------------------------------------------------------------------
# the universe can see what it holds
# --------------------------------------------------------------------------


def test_the_inventory_shows_the_git_scopes_and_the_consents(base) -> None:
    from tinyassets.api.cloud_connections import cloud_connections

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    deposited = _deposit("u-1", scopes=["git_read:octocat/hello"])
    asked = _consent_ask("u-1", deposited, consents=["workspace_checkout"])
    _answer("u-1", request_id=asked["request_id"], values={})

    listed = cloud_connections(action="list", universe_id="u-1")
    assert listed["count"] == 1
    row = listed["connections"][0]
    assert row["git_scopes"] == [
        {"kind": "git_read", "repo": "octocat/hello", "host": GIT_HOST}
    ]
    assert "git_read:octocat/hello" in row["scopes"]
    assert listed["workspace_consents"] == [
        {
            "consent": "workspace_checkout",
            "operation": "checkout",
            "connection_id": deposited["connection_id"],
            "host": GIT_HOST,
            "repo": "octocat/hello",
            "granted_at": listed["workspace_consents"][0]["granted_at"],
        }
    ]
    # No secret rode along with any of it.
    assert "credential_ref" not in json.dumps(listed)


# --------------------------------------------------------------------------
# a scope-only widening: the shape the served rail documents (Codex R2 #14)
# --------------------------------------------------------------------------


#: Copied from the served docstring in tinyassets/engine_mcp_server.py. If the
#: two ever drift, the agent is being taught an action that cannot execute -
#: which is precisely the defect this test exists for.
DOCUMENTED_SCOPE_ONLY_ACTION = {
    "type": "extend_http",
    "destination": "github",
    "scopes": ["git_read:octocat/hello", "git_write:octocat/hello"],
}


def test_the_documented_scope_only_ask_runs_end_to_end(base) -> None:
    """The rail's own example, verbatim: no endpoints, only scopes.

    A workspace checkout makes no HTTP call, so there are no endpoints to widen
    - and requiring some meant the action the agent is told to raise could
    never execute. The connection's STORED endpoints are what the host rule is
    checked against.
    """
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    deposited = _deposit("u-1")
    assert deposited.get("connection_id"), deposited
    assert wa.connection_git_scopes(_connection("u-1", deposited["connection_id"])) == set()

    asked = _ask(
        "u-1",
        kind="Approval",
        title="Let me check out and push octocat/hello",
        body="I need the repository scope on the key you already gave.",
        action=dict(DOCUMENTED_SCOPE_ONLY_ACTION),
    )
    assert asked["status"] == "pending", asked
    assert asked["action"]["endpoints"] == []
    assert asked["action"]["scopes"] == [
        "git_read:octocat/hello",
        "git_write:octocat/hello",
    ]
    assert asked["fields"] == [], "a scope-only ask has nothing to paste"

    answered = _answer("u-1", request_id=asked["request_id"], values={})
    assert answered["status"] == "answered", answered

    connection = _connection("u-1", deposited["connection_id"])
    assert wa.has_git_scope(connection, "git_read", "octocat/hello") is True
    assert wa.has_git_scope(connection, "git_write", "octocat/hello") is True
    # The endpoints it already had are untouched, and so are their verbs.
    assert [ep.host for ep in connection.allowed_endpoints] == ["api.github.com"]
    assert "POST" in connection.scopes


def test_a_scope_only_extension_leaves_the_endpoints_exactly_as_they_were(base) -> None:
    from tinyassets.api.http_connection import extend_http

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    deposited = _deposit("u-1")
    before = [
        ep.as_dict() for ep in _connection("u-1", deposited["connection_id"]).allowed_endpoints
    ]

    widened = extend_http(
        universe_id="u-1",
        payload=json.dumps(
            {"destination": "github", "scopes": ["git_read:octocat/hello"]}
        ),
    )
    assert widened["status"] == "extended", widened
    after = [
        ep.as_dict() for ep in _connection("u-1", deposited["connection_id"]).allowed_endpoints
    ]
    assert after == before
    assert "git_read:octocat/hello" in widened["scopes"]


def test_a_scope_only_extension_still_obeys_the_host_rule(base) -> None:
    """The stored endpoints are what vouch for the host. A connection pointed
    somewhere else cannot gain a git scope by leaving the endpoints out."""
    from tinyassets.api.http_connection import extend_http

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit(
        "u-1",
        destination="elsewhere",
        endpoints=[
            {
                "host": "api.example.com",
                "path_template": "/v1/things",
                "methods": ["POST"],
            }
        ],
    )
    allowed = extend_http(
        universe_id="u-1",
        payload=json.dumps(
            {"destination": "elsewhere", "scopes": ["git_read:octocat/hello"]}
        ),
    )
    # ONE host, so the scope is unambiguous and the extension stands. It used
    # to be refused for not being github, which is what stopped every other
    # forge from working at all.
    assert allowed.get("error") is None, allowed


def test_an_ask_with_neither_endpoints_nor_scopes_is_still_refused(base) -> None:
    """Scope-only is a NEW shape, not the removal of a check: an extend that
    widens nothing has nothing to answer."""
    from tinyassets.api.http_connection import extend_http

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit("u-1")
    refused = extend_http(
        universe_id="u-1", payload=json.dumps({"destination": "github"})
    )
    assert refused["error"] == "connection_setup_invalid", refused
    assert "at least one git scope" in refused["detail"]


def test_the_endpoint_carrying_form_still_works(base) -> None:
    """The old shape is untouched: endpoints, or endpoints AND scopes."""
    from tinyassets.api.http_connection import extend_http

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    deposited = _deposit("u-1")
    widened = extend_http(
        universe_id="u-1",
        payload=json.dumps(
            {
                "destination": "github",
                "endpoints": [
                    {
                        "host": "api.github.com",
                        "path_template": "/repos/o/r/issues",
                        "methods": ["POST"],
                    }
                ],
                "scopes": ["git_write:octocat/hello"],
            }
        ),
    )
    assert widened["status"] == "extended", widened
    connection = _connection("u-1", deposited["connection_id"])
    assert sorted(ep.path_template for ep in connection.allowed_endpoints) == [
        "/repos/o/r/issues",
        "/repos/o/r/pulls",
    ]
    assert wa.has_git_scope(connection, "git_write", "octocat/hello") is True


def test_the_served_docs_and_the_rail_agree_on_the_scope_only_shape() -> None:
    """The example in the tool docstring IS the contract: an agent copying it
    must get a tab, not a refusal. Pinned because the two live in different
    files and only a reader would notice them drifting."""
    from pathlib import Path as _Path

    import tinyassets.engine_mcp_server as server

    source = _Path(server.__file__).read_text(encoding="utf-8")
    assert '"type": "extend_http", "destination": "github", "scopes":' in source
    assert "no new endpoints" in source or "no endpoints" in source
    # The example stays concrete so it can be copied, but the docs must also
    # say the destination is the CONNECTION's - the forge is whatever host that
    # connection declares. A github-shaped example that reads as a github-only
    # rule is how the sink got pinned to one forge in the first place.
    assert "whatever forge it points at" in source
    assert "GitLab, Gitea, self-hosted" in source
