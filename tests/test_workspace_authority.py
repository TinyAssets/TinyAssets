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
from tinyassets.storage.outbound_connections import (
    ConnectionLedger,
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
    from tests.support.connection_door import connect_http

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

    return ConnectionLedger(
        Path(_base_path()) / ".broker" / "outbound.db", data_root=Path(_base_path()),
    ).get_connection(
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


@pytest.mark.verifies('owner-daemon-in-box:C7')
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


@pytest.mark.verifies('owner-daemon-in-box:C7')
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


# --------------------------------------------------------------------------
# the ledger stores it, or refuses it
# --------------------------------------------------------------------------


def _ledger(tmp_path, actor="user-1"):
    return ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: actor
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


# --------------------------------------------------------------------------
# a git scope is not an HTTP verb
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# the request rail asks for it
# --------------------------------------------------------------------------


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


# --------------------------------------------------------------------------
# nothing crosses a universe boundary
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# the universe can see what it holds
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# a scope-only widening: the shape the served rail documents (Codex R2 #14)
# --------------------------------------------------------------------------


#: Copied from the served docstring in tinyassets/universe_tools.py. If the
#: two ever drift, the agent is being taught an action that cannot execute -
#: which is precisely the defect this test exists for.
DOCUMENTED_SCOPE_ONLY_ACTION = {
    "type": "extend_http",
    "destination": "github",
    "scopes": ["git_read:octocat/hello", "git_write:octocat/hello"],
}
