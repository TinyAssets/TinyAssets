"""Pending requests — the agent asks, the app shows a tab, the user answers.

Founder 2026-08-27: *"pending-request should show up as tabs on the left side
screen of the app, the hedder notates what it is like api ... the agent can
construct these pending requests really how ever he likes"*.

So the tests treat this as a GENERAL primitive with a credential kind, not a
credential feature. The two properties they lean on hardest:

* a ``secret`` field is only possible on a request that actually deposits to the
  vault, so "compose them however you like" can never become a way to harvest a
  password into readable storage; and
* the deposit uses the policy stored ON THE REQUEST, so the tab's promise is
  what gets granted.
"""

from __future__ import annotations

import json

import pytest

from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import AuthProvider, DevAuthProvider, Identity


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
        capabilities=["tinyassets.universe.write"])))
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


def _make_universe(base, uid, *, admin="", write=""):
    from tinyassets.daemon_server import grant_universe_access

    udir = base / uid
    udir.mkdir(parents=True, exist_ok=True)
    if admin:
        grant_universe_access(base, universe_id=uid, actor_id=admin,
                              permission="admin", granted_by=admin)
    if write:
        grant_universe_access(base, universe_id=uid, actor_id=write,
                              permission="write", granted_by=admin)
    return udir


_CRED = {
    "kind": "API",
    "title": "GitHub key so I can open your pull request",
    "body": "I built the branch and need a key to POST the PR.",
    "action": {"type": "connect_http", "destination": "github",
               "host": "api.github.com", "path_template": "/repos/o/r/pulls",
               "methods": ["POST"], "auth_scheme": "bearer"},
    # One field per value the service asks for, labelled the way that service
    # labels it. There is no unlabelled fallback any more: a credential ask that
    # forgets its fields is refused, so every fixture states them like the agent
    # must (founder 2026-08-31).
    "fields": [
        # The NAME is just an identifier the answer keys on; the LABEL is what
        # stops the owner guessing, which is why it is the label that carries
        # the service's own wording.
        {"name": "secret", "type": "secret", "label": "Personal access token",
         "help": "Settings -> Developer settings -> Personal access tokens",
         "url": "https://github.com/settings/tokens"},
    ],
}


def _ask(uid, **over):
    from tinyassets.api.pending_requests import request_from_user

    return request_from_user(universe_id=uid, payload=json.dumps({**_CRED, **over}))


def _owner_answer(uid, **doc):
    from tests.owner_answer import answer_request

    return answer_request(universe_id=uid, payload=json.dumps(doc))


def _answer(uid, **doc):
    from tinyassets.api.pending_requests import answer_request

    return answer_request(universe_id=uid, payload=json.dumps(doc))


def _rail(uid):
    """The rail, minus the platform's own synthesized "connect a model" tab.

    That row is prepended whenever nothing serves the universe, which is true of
    every fixture here. These tests are about the AGENT's asks, so they filter it
    out; it has its own tests below.
    """
    from tinyassets.api.pending_requests import _LLM_REQUEST_ID, list_requests

    out = list_requests(universe_id=uid)
    if isinstance(out.get("pending"), list):
        out["pending"] = [r for r in out["pending"]
                          if r.get("request_id") != _LLM_REQUEST_ID]
        out["count"] = len(out["pending"])
    return out


def _raw_rail(uid):
    from tinyassets.api.pending_requests import list_requests

    return list_requests(universe_id=uid)


# --------------------------------------------------------------------------- #
# It is a general primitive, not a credential feature.
# --------------------------------------------------------------------------- #








# --------------------------------------------------------------------------- #
# The credential kind.
# --------------------------------------------------------------------------- #




















































# --------------------------------------------------------------------------- #
# Codex cross-family review, 2026-08-27 (verdict ADAPT). Each case below is a
# reproduction it supplied against the primitive as first written.
# --------------------------------------------------------------------------- #













# --------------------------------------------------------------------------- #
# Widening a grant without a re-paste.
# Founder 2026-08-28: "why would we have the user need to reput the api in
# again? you said it was safe in the vault so why would the user give it again?"
# --------------------------------------------------------------------------- #


def _seed_connection(uid, destination="github", path="/repos/o/r/pulls"):
    """A deposited connection, as if the user had already pasted once."""
    from tests.support.connection_door import connect_http

    return connect_http(universe_id=uid, payload=json.dumps({
        "destination": destination, "secret": "ghp_" + "x" * 36,
        "auth_scheme": "bearer",
        "allowed_endpoints": [{"host": "api.github.com",
                               "path_template": path, "methods": ["POST"]}],
    }))











# --------------------------------------------------------------------------- #
# The one ask the platform raises for itself.
# Founder 2026-08-27: "if the universe doesnt have an llm to run on then a
# request for that will remain be present intil that is added and it will be
# already in expanded view."
# --------------------------------------------------------------------------- #








