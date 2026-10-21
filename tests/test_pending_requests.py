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




# --------------------------------------------------------------------------- #
# It is a general primitive, not a credential feature.
# --------------------------------------------------------------------------- #




def test_a_secret_field_requires_a_deposit_action(base):
    """THE boundary: otherwise composing requests freely becomes a way to ask
    for a password and land it in readable storage."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")

    out = _ask("u-1", kind="Login", title="What is your password?",
               action={"type": "answer"},
               fields=[{"name": "pw", "label": "Password", "type": "secret"}])

    assert out["error"] == "request_invalid"
    assert "only allowed on a connect_http request" in out["detail"]
    assert _rail("u-1")["count"] == 0


def test_a_credential_ask_records_no_answer_at_all(base):
    """The guarantee got STRONGER, so this test did too.

    It used to pair a secret field with a `text` one and assert the secret was
    not recorded while the text was. Every field on a credential ask must now be
    a secret, because a non-secret answer is persisted and relayed into chat and
    an ask could label a token `text` (Codex, Q1). So nothing is recorded for a
    credential ask -- there is no longer a field whose value could be.
    """
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    asked = _ask("u-1", fields=[
        {"name": "secret", "label": "Key", "type": "secret"},
    ])

    _owner_answer("u-1", request_id=asked["request_id"],
            values={"secret": "ghp_" + "x" * 36})

    answered = _rail("u-1")["recently_answered"][0]
    # Empty or absent; both mean the same thing and neither holds a value.
    assert not (answered.get("answer") or {})
    assert "ghp_" not in json.dumps(answered)


# --------------------------------------------------------------------------- #
# The credential kind.
# --------------------------------------------------------------------------- #




def test_answering_deposits_under_the_policy_on_the_request(base):
    """A caller cannot substitute a different endpoint at answer time."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    asked = _ask("u-1")

    out = _owner_answer("u-1", request_id=asked["request_id"],
                  values={"secret": "ghp_" + "x" * 36},
                  host="api.evil.example", path_template="/steal",
                  destination="evil")

    assert out["status"] == "answered"
    assert out["destination"] == "github"
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(base / ".broker" / "outbound.db", data_root=base,
                              verify_authenticated_principal=lambda: "alice")
    resource = ledger._get_connection_resource(out["connection_id"])
    assert [e.host for e in resource.allowed_endpoints] == ["api.github.com"]


@pytest.mark.parametrize("over", [
    {"action": dict(_CRED["action"], path_template="")},
    {"action": dict(_CRED["action"], path_template="/{rest+}")},
    {"action": dict(_CRED["action"], host="https://api.github.com")},
    # Every verb the egress layer knows is askable now (founder 2026-09-02, on
    # limits); a verb it does not know is still refused.
    {"action": dict(_CRED["action"], methods=["GET", "OPTIONS"])},
    {"action": dict(_CRED["action"], destination="GitHub PR")},
])
def test_a_tab_the_deposit_could_not_honour_never_reaches_the_user(base, over):
    _make_universe(base, "u-1", admin="alice")
    _login("alice")

    assert "error" in _ask("u-1", **over)
    assert _rail("u-1")["count"] == 0














def test_a_failed_deposit_leaves_the_tab_open(base):
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    asked = _ask("u-1")

    assert "error" in _owner_answer("u-1", request_id=asked["request_id"],
                              values={"secret": "   "})
    assert _rail("u-1")["count"] == 1
































# --------------------------------------------------------------------------- #
# Codex cross-family review, 2026-08-27 (verdict ADAPT). Each case below is a
# reproduction it supplied against the primitive as first written.
# --------------------------------------------------------------------------- #


def test_codex_an_undeclared_key_cannot_smuggle_a_secret_into_storage(base):
    """Excluding known secret NAMES was not enough — an undeclared key persisted
    verbatim. Only values for declared non-secret fields are recorded."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    # An APPROVAL ask, not a credential one: every field on a credential ask
    # must now be a secret, and the property under test -- an undeclared key is
    # never recorded -- belongs to every ask, not just that one.
    asked = _ask("u-1", action={"type": "answer"}, fields=[
        {"name": "note", "label": "Note", "type": "text"},
    ])

    _answer("u-1", request_id=asked["request_id"], values={
        "shadow": "ghp_REPRO_SECRET_123456789",   # never declared
        "note": "fine",
    })

    answered = _rail("u-1")["recently_answered"][0]
    assert answered["answer"] == {"note": "fine"}
    assert "ghp_" not in json.dumps(answered)


@pytest.mark.parametrize("mode", ["answer", "dismiss"])
def test_codex_feedback_cannot_carry_a_credential(base, mode):
    """Feedback is free text stored in the clear; Codex put the key there."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    asked = _ask("u-1")

    doc = {"request_id": asked["request_id"],
           "feedback": "ghp_REPRO_SECRET_123456789"}
    if mode == "dismiss":
        doc["dismiss"] = True
    else:
        doc["values"] = {"secret": "ghp_" + "x" * 36}
    out = _owner_answer("u-1", **doc)

    assert out["error"] == "request_invalid"
    assert "credential" in out["detail"]
    assert "ghp_REPRO" not in json.dumps(_rail("u-1"))









# --------------------------------------------------------------------------- #
# Widening a grant without a re-paste.
# Founder 2026-08-28: "why would we have the user need to reput the api in
# again? you said it was safe in the vault so why would the user give it again?"
# --------------------------------------------------------------------------- #










def test_an_extend_request_cannot_carry_a_secret_field(base):
    """A secret field belongs only where a secret is deposited."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    out = _ask("u-1", kind="API", title="sneaky",
               action={"type": "extend_http", "destination": "github",
                       "endpoints": [{"host": "api.github.com",
                                      "path_template": "/x", "methods": ["PUT"]}]},
               fields=[{"name": "secret", "label": "Key", "type": "secret"}])
    assert out["error"] == "request_invalid"
    assert "only allowed on a connect_http request" in out["detail"]



# --------------------------------------------------------------------------- #
# The one ask the platform raises for itself.
# Founder 2026-08-27: "if the universe doesnt have an llm to run on then a
# request for that will remain be present intil that is added and it will be
# already in expanded view."
# --------------------------------------------------------------------------- #








