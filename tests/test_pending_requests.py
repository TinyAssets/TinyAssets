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


def _answer(uid, **doc):
    from tests.owner_answer import answer_request

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


def test_the_agent_composes_a_kind_nobody_coded_for(base):
    """An unknown kind must render and work, or "however he likes" is not true."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")

    out = _ask("u-1", kind="Decision", title="Which repo should I open it on?",
               body="", action={"type": "answer"},
               fields=[{"name": "repo", "label": "Repo", "type": "choice",
                        "options": ["tinyassets", "scratch"]}])

    assert out["status"] == "pending"
    assert out["kind"] == "Decision"
    assert out["fields"][0]["options"] == ["tinyassets", "scratch"]

    done = _answer("u-1", request_id=out["request_id"], values={"repo": "tinyassets"})
    assert done["answer"] == {"repo": "tinyassets"}
    assert _rail("u-1")["recently_answered"][0]["answer"] == {"repo": "tinyassets"}


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

    _answer("u-1", request_id=asked["request_id"],
            values={"secret": "ghp_" + "x" * 36})

    answered = _rail("u-1")["recently_answered"][0]
    # Empty or absent; both mean the same thing and neither holds a value.
    assert not (answered.get("answer") or {})
    assert "ghp_" not in json.dumps(answered)


# --------------------------------------------------------------------------- #
# The credential kind.
# --------------------------------------------------------------------------- #


def test_the_tab_states_the_exact_grant(base):
    _make_universe(base, "u-1", admin="alice")
    _login("alice")

    out = _ask("u-1")
    assert out["grant_sentence"] == (
        'This key as "github" will be able to POST '
        "api.github.com/repos/o/r/pulls - nothing else."
    )
    assert [f["type"] for f in out["fields"]] == ["secret"]


def test_answering_deposits_under_the_policy_on_the_request(base):
    """A caller cannot substitute a different endpoint at answer time."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    asked = _ask("u-1")

    out = _answer("u-1", request_id=asked["request_id"],
                  values={"secret": "ghp_" + "x" * 36},
                  host="api.evil.example", path_template="/steal",
                  destination="evil")

    assert out["status"] == "answered"
    assert out["destination"] == "github"
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(base / "outbound.db",
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


def test_dismissing_writes_nothing(base):
    from tinyassets.credential_vault import load_credential_vault

    udir = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    asked = _ask("u-1")

    assert _answer("u-1", request_id=asked["request_id"],
                   dismiss=True)["status"] == "dismissed"
    assert load_credential_vault(udir) == []
    assert _rail("u-1")["count"] == 0


def test_one_answer_counts_once(base):
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    asked = _ask("u-1")
    _answer("u-1", request_id=asked["request_id"], values={"secret": "ghp_" + "x" * 36})

    again = _answer("u-1", request_id=asked["request_id"],
                    values={"secret": "ghp_" + "y" * 36})
    assert again["error"] == "already_resolved"


def test_a_failed_deposit_leaves_the_tab_open(base):
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    asked = _ask("u-1")

    assert "error" in _answer("u-1", request_id=asked["request_id"],
                              values={"secret": "   "})
    assert _rail("u-1")["count"] == 1


def test_retrying_the_same_ask_does_not_stack_tabs(base):
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    assert _ask("u-1")["request_id"] == _ask("u-1")["request_id"]
    assert _rail("u-1")["count"] == 1


def test_the_rail_has_no_ceiling(base):
    """Past the old MAX_PENDING of 50, and the 51st through 60th still land.

    Founder, 2026-09-30: an account's only limits are cloud storage and
    concurrent agent seats. A looping universe is bounded by its seats, not by a
    count of tabs, so ``too_many_pending`` is gone.
    """
    from tinyassets.storage import pending_requests as pr

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    for i in range(60):
        out = _ask("u-1", title="ask %d" % i)
        assert "error" not in out, f"ask {i} was refused: {out}"
    from pathlib import Path

    assert len(pr.list_pending(Path(base) / "u-1")) == 60
    assert not hasattr(pr, "MAX_PENDING"), "the pending ceiling is gone"


def test_listing_every_pending_row_is_not_silently_paged(base):
    """The queue is read whole: there is no page, and no default page.

    A default page of 10 (and the connector's 30 passed through it) cut the
    owner's own queue; on 2026-09-30 that is how a waiting request went unseen.
    """
    from pathlib import Path

    from tinyassets.storage.pending_requests import list_pending

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    for i in range(55):
        _ask("u-1", title="ask %d" % i)
    udir = Path(base) / "u-1"
    assert len(list_pending(udir)) == 55
    assert len(_raw_rail("u-1")["pending"]) >= 55, "the rail is the whole queue"


def test_an_unreadable_queue_raises_instead_of_reading_as_empty(base):
    """``[]`` would draw "nothing is waiting on you" over a queue we cannot read."""
    from pathlib import Path

    from tinyassets.agent_activities import store_path
    from tinyassets.storage.pending_requests import list_pending

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _ask("u-1", title="one")
    udir = Path(base) / "u-1"
    store_path(udir).write_bytes(b"this is not a sqlite database" * 64)
    with pytest.raises(Exception):
        list_pending(udir)


@pytest.mark.parametrize("fn", ["ask", "rail", "answer"])
def test_a_write_collaborator_is_not_an_owner(base, fn):
    _make_universe(base, "u-1", admin="alice", write="bob")
    _login("bob")
    out = {"ask": lambda: _ask("u-1"), "rail": lambda: _rail("u-1"),
           "answer": lambda: _answer("u-1", request_id="req_x", values={})}[fn]()
    assert out == {"error": "not_found", "resource": "connection"}


def test_dispatch_through_the_pinned_handles(base):
    """New OPERATIONS on write_graph/read_graph - no new advertised tool."""
    import importlib

    from tinyassets import universe_server as us

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    importlib.reload(us)
    try:
        raw = us.write_graph(target="connection", operation="request_from_user",
                             graph_id="u-1", payload_json=json.dumps(_CRED))
        assert json.loads(raw)["status"] == "pending"

        rail = json.loads(us.read_graph(target="pending_requests", graph_id="u-1"))
        asks = [r for r in rail["pending"] if r["request_id"] != "sys_connect_llm"]
        assert len(asks) == 1
        assert asks[0]["kind"] == "API"

        rid = asks[0]["request_id"]
        done = us.write_graph(target="connection", operation="answer_request",
                              graph_id="u-1",
                              payload_json=json.dumps(
                                  {"request_id": rid,
                                   "values": {"secret": "ghp_" + "z" * 36}}))
        assert json.loads(done)["error"] == "interactive_approval_required"
        done = json.dumps(_answer("u-1", request_id=rid,
                                  values={"secret": "ghp_" + "z" * 36}))
        assert json.loads(done)["status"] == "answered"
    finally:
        importlib.reload(us)


def test_feedback_and_dont_ask_again_ride_the_answer(base):
    """Founder 2026-08-27: an approval needs a way to disagree, and a way to say
    stop asking. Both belong on the answer — that is when the user has the
    opinion — and the agent is TOLD it was muted rather than looping silently."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    asked = _ask("u-1", kind="Approval", title="Post this to X?",
                 action={"type": "answer"},
                 fields=[{"name": "ok", "label": "Go ahead?", "type": "choice",
                          "options": ["yes", "no"]}])

    # A user who thinks it is too promotional clicks "Not now" — the system
    # knows Send vs Not now, and cannot read intent out of a field value.
    out = _answer("u-1", request_id=asked["request_id"], dismiss=True,
                  feedback="too promotional", dont_ask_again=True)

    assert out["suppressed"] is True
    assert out["feedback"] == "too promotional"
    # The agent can read back WHY, not just that it was refused.
    assert _rail("u-1")["recently_answered"][0]["feedback"] == "too promotional"

    # Asking the identical thing again returns the STANDING DECISION, with the
    # reason attached — not a bare refusal.
    again = _ask("u-1", kind="Approval", title="Post this to X?",
                 action={"type": "answer"},
                 fields=[{"name": "ok", "label": "Go ahead?", "type": "choice",
                          "options": ["yes", "no"]}])
    assert again["status"] == "settled"
    assert again["decision"] == "declined"
    assert again["may_proceed"] is False
    assert again["feedback"] == "too promotional"
    assert _rail("u-1")["count"] == 0


def test_a_dismissal_can_also_mute(base):
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    asked = _ask("u-1")

    out = _answer("u-1", request_id=asked["request_id"], dismiss=True,
                  feedback="I will do this myself", dont_ask_again=True)
    assert out["suppressed"] is True
    settled = _ask("u-1")
    assert settled["status"] == "settled" and settled["may_proceed"] is False


def test_muting_is_visible_and_undoable(base):
    """A standing refusal the user cannot lift is a trap."""
    from tinyassets.api.pending_requests import unmute_request

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    asked = _ask("u-1")
    _answer("u-1", request_id=asked["request_id"], dismiss=True, dont_ask_again=True)

    muted = _rail("u-1")["muted"]
    assert len(muted) == 1 and muted[0]["kind"] == "API"

    lifted = unmute_request(universe_id="u-1",
                            payload=json.dumps({"dedupe_key": muted[0]["dedupe_key"]}))
    assert lifted["status"] == "unmuted"
    assert _ask("u-1")["status"] == "pending"


def test_a_standing_yes_lets_the_agent_proceed_without_asking_again(base):
    """Founder 2026-08-27: "it might know from past interaction what it is
    allowed and what needs approval".

    "Don't ask me this again" on an approval usually means *yes, and stop
    asking*. The first cut only ever produced a standing refusal, so a remembered
    decision could only ever block — the opposite of what the user meant.
    """
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    asked = _ask("u-1", kind="Approval", title="Post the weekly summary?",
                 action={"type": "answer"},
                 fields=[{"name": "ok", "label": "Go ahead?", "type": "text"}])

    _answer("u-1", request_id=asked["request_id"], values={"ok": "yes"},
            feedback="always fine for the weekly one", dont_ask_again=True)

    again = _ask("u-1", kind="Approval", title="Post the weekly summary?",
                 action={"type": "answer"},
                 fields=[{"name": "ok", "label": "Go ahead?", "type": "text"}])

    assert again["status"] == "settled"
    assert again["decision"] == "allowed"
    assert again["may_proceed"] is True, "a standing yes must let it proceed"
    assert again["answer"] == {"ok": "yes"}
    assert again["feedback"] == "always fine for the weekly one"
    # And it costs the user nothing: no new tab appears.
    assert _rail("u-1")["count"] == 0


def test_the_rail_shows_what_was_decided_not_just_that_it_was_muted(base):
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    yes = _ask("u-1", kind="Approval", title="Allowed thing",
               action={"type": "answer"},
               fields=[{"name": "ok", "label": "OK?", "type": "text"}])
    _answer("u-1", request_id=yes["request_id"], values={"ok": "y"},
            dont_ask_again=True)
    no = _ask("u-1", kind="Approval", title="Refused thing",
              action={"type": "answer"},
              fields=[{"name": "ok", "label": "OK?", "type": "text"}])
    _answer("u-1", request_id=no["request_id"], dismiss=True, dont_ask_again=True)

    decisions = {m["title"]: m["decision"] for m in _rail("u-1")["muted"]}
    assert decisions == {"Allowed thing": "allowed", "Refused thing": "declined"}


def test_muting_one_ask_does_not_mute_a_different_one(base):
    """The key is the specific ask, not the whole kind — a user muting one
    approval must not go deaf to every future 'API' tab."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    first = _ask("u-1")
    _answer("u-1", request_id=first["request_id"], dismiss=True, dont_ask_again=True)

    other = _ask("u-1", title="Different key for a different repo",
                 action={**_CRED["action"], "path_template": "/repos/o/other/pulls"})
    assert other["status"] == "pending"


def test_the_agent_prompt_teaches_the_ask_verb():
    """A primitive the agent does not know about is a primitive it never uses.

    The rail shipped before the served prompt mentioned `request_from_user`, so
    the universe had no way to learn it could ask — it would keep telling the
    user to go find a form instead.
    """
    import inspect

    from tinyassets.api import prompts

    src = inspect.getsource(prompts)
    assert 'operation="request_from_user"' in src
    assert 'read_graph target="pending_requests"' in src
    # It must know BOTH shapes, or it can only ever ask for credentials.
    assert '"type":"connect_http"' in src
    # It must know to ask for every call the flow needs in ONE request.
    assert '"endpoints":[' in src
    assert '"type":"answer"' in src


def test_one_request_can_cover_the_several_calls_a_real_flow_needs(base):
    """A GitHub pull request takes three calls: create a ref, put contents, open
    the pull. One path per request would mean pasting the same key three times.
    Several EXACT paths is still least privilege — a named list, not a widening."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")

    out = _ask("u-1", action={
        "type": "connect_http", "destination": "github", "auth_scheme": "bearer",
        "endpoints": [
            {"host": "api.github.com", "path_template": "/repos/o/r/git/refs",
             "methods": ["POST"]},
            {"host": "api.github.com", "path_template": "/repos/o/r/contents/x.css",
             "methods": ["PUT"]},
            {"host": "api.github.com", "path_template": "/repos/o/r/pulls",
             "methods": ["POST"]},
        ],
    })

    assert out["status"] == "pending"
    # The user sees every line before pasting once.
    assert out["grant_sentence"].startswith(
        'This key as "github" will be able to reach exactly these, and nothing '
        "else:")
    assert "/repos/o/r/pulls" in out["grant_sentence"]
    assert "/repos/o/r/git/refs" in out["grant_sentence"]

    done = _answer("u-1", request_id=out["request_id"],
                   values={"secret": "ghp_" + "x" * 36})
    assert done["status"] == "answered"

    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(base / "outbound.db",
                              verify_authenticated_principal=lambda: "alice")
    resource = ledger._get_connection_resource(done["connection_id"])
    paths = sorted(e.path_template for e in resource.allowed_endpoints)
    assert paths == ["/repos/o/r/contents/x.css", "/repos/o/r/git/refs",
                     "/repos/o/r/pulls"]


def test_a_request_cannot_ask_for_a_whole_api(base):
    """Several named endpoints is fine; an unbounded list is asking for the API."""
    from tinyassets.api.pending_requests import _MAX_REQUEST_ENDPOINTS

    _make_universe(base, "u-1", admin="alice")
    _login("alice")

    out = _ask("u-1", action={
        "type": "connect_http", "destination": "github", "auth_scheme": "bearer",
        "endpoints": [
            {"host": "api.github.com", "path_template": "/repos/o/r/p%d" % i,
             "methods": ["POST"]}
            for i in range(_MAX_REQUEST_ENDPOINTS + 1)
        ],
    })
    assert out["error"] == "request_invalid"
    assert "at most" in out["detail"]


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
    out = _answer("u-1", **doc)

    assert out["error"] == "request_invalid"
    assert "credential" in out["detail"]
    assert "ghp_REPRO" not in json.dumps(_rail("u-1"))


def test_codex_duplicate_field_names_are_refused(base):
    """Duplicate names collide as DOM ids: the browser clears the first control
    twice and leaves the second holding its secret."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")

    out = _ask("u-1", fields=[
        {"name": "same", "label": "One", "type": "secret"},
        {"name": "same", "label": "Two", "type": "secret"},
    ])
    assert out["error"] == "request_invalid"
    assert "unique" in out["detail"]


def test_codex_muting_one_approval_does_not_silence_a_different_one(base):
    """The dedupe key omitted body and fields, and an `answer` action normalizes
    to a bare {"type":"answer"} — so muting "Approve this?" about a harmless
    draft also silenced "Approve this?" about deleting production data."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    harmless = _ask("u-1", kind="Approval", title="Approve this?",
                    body="post a harmless draft", action={"type": "answer"},
                    fields=[{"name": "ok", "label": "OK?", "type": "text"}])
    _answer("u-1", request_id=harmless["request_id"], dismiss=True,
            dont_ask_again=True)

    dangerous = _ask("u-1", kind="Approval", title="Approve this?",
                     body="delete production data", action={"type": "answer"},
                     fields=[{"name": "ok", "label": "OK?", "type": "text"}])
    assert dangerous["status"] == "pending", "a different ask must still reach the user"


def test_codex_a_lifted_mute_is_recorded_because_the_agent_shares_the_principal(base):
    """The agent runs as the user's own principal, so nothing at the gate can
    tell "the user lifted this" from "the universe lifted it". A distinction the
    auth model cannot make is not faked — the lift is recorded and surfaced."""
    from tinyassets.api.pending_requests import unmute_request

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    asked = _ask("u-1")
    _answer("u-1", request_id=asked["request_id"], dismiss=True, dont_ask_again=True)
    key = _rail("u-1")["muted"][0]["dedupe_key"]

    unmute_request(universe_id="u-1", payload=json.dumps({"dedupe_key": key}))

    rail = _rail("u-1")
    assert rail["mutes_lifted"], "a lifted mute must be visible, not silent"
    assert rail["mutes_lifted"][0]["dedupe_key"] == key


def test_two_asks_differing_only_by_destination_are_distinguishable(base):
    """Observed live, 2026-08-28. The agent asked for one endpoint under
    destination "github"; that would have conflicted with the existing
    connection, so it re-raised the SAME endpoint under "github-theme". Both
    tabs then rendered identical titles AND identical grant sentences — the user
    could not tell the one that works from the one that fails. Naming the
    connection is the difference."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    ep = {"host": "api.github.com",
          "path_template": "/repos/o/r/contents/theme.json", "methods": ["PUT"]}

    a = _ask("u-1", title="GitHub endpoint so I can update the theme",
             action={"type": "connect_http", "destination": "github",
                     "auth_scheme": "bearer", "endpoints": [ep]})
    b = _ask("u-1", title="GitHub endpoint so I can update the theme",
             action={"type": "connect_http", "destination": "github-theme",
                     "auth_scheme": "bearer", "endpoints": [ep]})

    assert a["grant_sentence"] != b["grant_sentence"]
    assert '"github"' in a["grant_sentence"]
    assert '"github-theme"' in b["grant_sentence"]

# --------------------------------------------------------------------------- #
# Widening a grant without a re-paste.
# Founder 2026-08-28: "why would we have the user need to reput the api in
# again? you said it was safe in the vault so why would the user give it again?"
# --------------------------------------------------------------------------- #


def _seed_connection(uid, destination="github", path="/repos/o/r/pulls"):
    """A deposited connection, as if the user had already pasted once."""
    from tinyassets.api.http_connection import connect_http

    return connect_http(universe_id=uid, payload=json.dumps({
        "destination": destination, "secret": "ghp_" + "x" * 36,
        "auth_scheme": "bearer",
        "allowed_endpoints": [{"host": "api.github.com",
                               "path_template": path, "methods": ["POST"]}],
    }))


def test_extending_a_grant_needs_no_secret_and_no_new_field(base):
    """The key is already in the vault. Widening it is an approval, not a paste."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    assert _seed_connection("u-1")["status"] == "provisioned"

    ask = _ask("u-1", kind="API", title="Also let me write the theme file",
               body="one more endpoint on the key you already gave",
               fields=[], action={"type": "extend_http", "destination": "github",
                       "endpoints": [{"host": "api.github.com",
                                      "path_template": "/repos/o/r/contents/t.json",
                                      "methods": ["GET", "PUT"]}]})

    assert ask["status"] == "pending"
    assert ask["fields"] == [], "nothing to type - it is a yes/no"
    assert "do not need to paste it again" in ask["grant_sentence"]

    out = _answer("u-1", request_id=ask["request_id"], values={})
    assert out["status"] == "answered"
    assert out["secret_reused"] is True

    from tinyassets.api.http_connection import _ids
    from tinyassets.storage.outbound_connections import ConnectionLedger

    conn_id, _ = _ids(universe_id="u-1", destination="github")
    ledger = ConnectionLedger(base / "outbound.db",
                              verify_authenticated_principal=lambda: "alice")
    res = ledger._get_connection_resource(conn_id)
    assert sorted(e.path_template for e in res.allowed_endpoints) == [
        "/repos/o/r/contents/t.json", "/repos/o/r/pulls",
    ]


def test_extending_never_writes_a_second_vault_record(base):
    """The whole point: one key, one vault record, however many endpoints."""
    from tinyassets.credential_vault import load_credential_vault

    udir = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _seed_connection("u-1")
    before = [r for r in load_credential_vault(udir) if r["credential_type"] == "http"]

    ask = _ask("u-1", kind="API", title="one more endpoint",
               fields=[], action={"type": "extend_http", "destination": "github",
                       "endpoints": [{"host": "api.github.com",
                                      "path_template": "/repos/o/r/contents/t.json",
                                      "methods": ["PUT"]}]})
    _answer("u-1", request_id=ask["request_id"], values={})

    after = [r for r in load_credential_vault(udir) if r["credential_type"] == "http"]
    assert len(after) == len(before) == 1
    assert after[0]["token"] == before[0]["token"], "the stored key is untouched"


def test_extending_a_connection_that_does_not_exist_is_refused(base):
    """Refused when RAISED (2026-09-02): the agent is told, the owner never
    sees a tab that cannot be honoured. Answer-time is covered by extend_http
    itself, which keeps the uniform not_found envelope."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    ask = _ask("u-1", kind="API", title="widen nothing",
               fields=[], action={"type": "extend_http", "destination": "nope",
                       "endpoints": [{"host": "api.github.com",
                                      "path_template": "/x", "methods": ["PUT"]}]})
    assert ask["error"] == "request_invalid"
    assert 'no key is deposited as "nope"' in ask["detail"]
    assert _rail("u-1")["count"] == 0


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

def test_the_prompt_says_credentials_are_asked_for_once_not_per_action():
    """Founder 2026-08-27: a credential is added "one or as they expire as
    needed, not for each action with that credential". The earlier row pushed the
    opposite way — it framed the tab as the way to get permission to act."""
    import inspect

    from tinyassets.api import prompts

    src = inspect.getsource(prompts)
    assert "ONCE per" in src and "never once per action" in src
    # An action is a conversation by default; the tab is optional.
    assert "ask in the conversation" in src
    # And a standing yes must be acted on, not re-asked.
    assert "may_proceed=true" in src



# --------------------------------------------------------------------------- #
# The one ask the platform raises for itself.
# Founder 2026-08-27: "if the universe doesnt have an llm to run on then a
# request for that will remain be present intil that is added and it will be
# already in expanded view."
# --------------------------------------------------------------------------- #


def test_a_universe_with_no_model_is_asked_to_connect_one(base):
    _make_universe(base, "u-1", admin="alice")
    _login("alice")

    rail = _raw_rail("u-1")
    first = rail["pending"][0]

    assert first["request_id"] == "sys_connect_llm"
    assert first["kind"] == "LLM"
    assert first["sticky"] is True, "it renders expanded and cannot be dismissed"


def test_the_model_ask_leads_the_rail(base):
    """It is a precondition, so it sits above whatever else is waiting."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _ask("u-1")

    kinds = [r["kind"] for r in _raw_rail("u-1")["pending"]]
    assert kinds[0] == "LLM"
    assert "API" in kinds


def test_the_model_ask_cannot_be_answered_or_dismissed_away(base):
    """A blocking ask that could be dismissed would leave the universe mute with
    no way back. It is satisfied by connecting a model, not by answering."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")

    for doc in ({"dismiss": True}, {"values": {"anything": "x"}}):
        out = _answer("u-1", request_id="sys_connect_llm", **doc)
        assert out["error"] == "not_answerable"

    # Still there.
    assert _raw_rail("u-1")["pending"][0]["request_id"] == "sys_connect_llm"


def test_the_model_ask_becomes_optional_once_something_serves(base, monkeypatch):
    """September14 direction: retain a generic additional-source entry when ready."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    monkeypatch.setattr(
        "tinyassets.api.pending_requests._serving_llm_bound",
        lambda *a, **k: True,
    )

    _ask("u-1")
    rows = _raw_rail("u-1")["pending"]
    assert rows[-1]["request_id"] == "sys_connect_llm"
    assert rows[-1]["title"] == "Connect another LLM"
    assert rows[-1]["sticky"] is False
    assert rows[0]["kind"] == "API"
