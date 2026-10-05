"""The request rail tells the truth at the moment it can be acted on.

Live, 2026-09-02, in the founder's app: their universe raised an ask that added
a ``github.com`` endpoint to a connection whose endpoints were all on
``api.github.com``. The ask was accepted and rendered; the founder's click on
yes was refused by the ledger's one-host rule for git scopes, worded for the
agent, shown to the founder, and recorded as a "Not now" they never chose.
The same universe had asked for reach its git scopes already gave it.

So: an ask the answer would refuse is refused when RAISED, with the reason to
the agent; an ask that adds nothing is answered ``already_held`` with no tab;
Deny is a recorded decision that executes nothing; and a widening never stores
the same endpoint twice.
"""

from __future__ import annotations

import json
import pathlib

from tests.test_pending_requests import (  # noqa: F401 - fixtures ride the import
    _answer,
    _ask,
    _login,
    _make_universe,
    _rail,
    _reset_auth,
    _seed_connection,
    base,
)


def _stored_paths(root, uid="u-1", destination="github", actor="alice"):
    from tinyassets.api.http_connection import _ids
    from tinyassets.storage.outbound_connections import ConnectionLedger

    conn_id, _ = _ids(universe_id=uid, destination=destination)
    ledger = ConnectionLedger(root / "outbound.db",
                              verify_authenticated_principal=lambda: actor)
    res = ledger._get_connection_resource(conn_id)
    return sorted(e.path_template for e in res.allowed_endpoints)


def test_an_ask_the_answer_would_refuse_is_refused_when_raised(base):  # noqa: F811
    """The exact live ask: a git-host endpoint on an API-host connection."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    assert _seed_connection("u-1")["status"] == "provisioned"

    out = _ask("u-1", kind="API",
               title="Extend GitHub transport host for o/r workspace checkout",
               fields=[], action={"type": "extend_http", "destination": "github",
                       "endpoints": [{"host": "github.com",
                                      "path_template": "/o/r.git",
                                      "methods": ["GET"]}],
                       "scopes": ["git_read:o/r"]})

    assert out["error"] == "ask_cannot_be_granted", out
    assert "ONE host" in out["detail"]
    # ...and the part the agent can act on: an HTTP endpoint never moves git;
    # the connection declares where git lives (there is no per-service table).
    assert "does not change where git goes" in out["detail"]
    assert '"git_host"' in out["detail"]
    assert "no tab was raised" in out["note"]
    assert _rail("u-1")["count"] == 0, "the owner never sees a tab that cannot be honoured"


def test_an_ask_that_adds_nothing_is_answered_already_held(base):  # noqa: F811
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _seed_connection("u-1")           # api.github.com POST /repos/o/r/pulls

    out = _ask("u-1", kind="API", title="let me open pull requests",
               fields=[], action={"type": "extend_http", "destination": "github",
                       "endpoints": [{"host": "api.github.com",
                                      "path_template": "/repos/o/r/pulls",
                                      "methods": ["POST"]}]})

    assert out["status"] == "already_held", out
    assert [e["path_template"] for e in out["allowed_endpoints"]] == ["/repos/o/r/pulls"]
    assert "Act on the grant you have" in out["note"]
    assert _rail("u-1")["count"] == 0


def test_extending_a_key_that_was_never_deposited_says_so_when_raised(base):  # noqa: F811
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    out = _ask("u-1", kind="API", title="widen nothing",
               fields=[], action={"type": "extend_http", "destination": "nope",
                       "endpoints": [{"host": "api.github.com",
                                      "path_template": "/x", "methods": ["PUT"]}]})
    assert out["error"] == "request_invalid", out
    assert 'no key is deposited as "nope"' in out["detail"]
    assert _rail("u-1")["count"] == 0


def test_denying_an_action_ask_executes_nothing(base):  # noqa: F811
    """Deny is a decision the agent reads. It must not fall through to the
    grant: for an action-bearing ask the answer IS the act."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _seed_connection("u-1")
    ask = _ask("u-1", kind="API", title="also the theme file",
               fields=[], action={"type": "extend_http", "destination": "github",
                       "endpoints": [{"host": "api.github.com",
                                      "path_template": "/repos/o/r/contents/t.json",
                                      "methods": ["PUT"]}]})
    assert ask["status"] == "pending"

    out = _answer("u-1", request_id=ask["request_id"], decision="declined",
                  feedback="not that file")

    assert out == {"status": "answered", "decision": "declined",
                   "request_id": ask["request_id"], "answer": None,
                   "feedback": "not that file", "suppressed": False}
    assert _stored_paths(base) == ["/repos/o/r/pulls"], "nothing was granted"
    assert _rail("u-1")["count"] == 0


def test_a_standing_deny_is_told_to_the_agent_next_time(base):  # noqa: F811
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _seed_connection("u-1")
    action = {"type": "extend_http", "destination": "github",
              "endpoints": [{"host": "api.github.com",
                             "path_template": "/repos/o/r/contents/t.json",
                             "methods": ["PUT"]}]}
    ask = _ask("u-1", kind="API", title="also the theme file", fields=[], action=action)
    out = _answer("u-1", request_id=ask["request_id"], decision="declined",
                  dont_ask_again=True)
    assert out["suppressed"] is True

    again = _ask("u-1", kind="API", title="also the theme file", fields=[], action=action)
    assert again["status"] == "settled"
    assert again["decision"] == "declined"
    assert again["may_proceed"] is False


def test_extending_never_stores_a_duplicate_endpoint(base):  # noqa: F811
    """The founder's github connection carried three duplicated rows on
    2026-09-02 because the union kept repeats."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _seed_connection("u-1")
    ask = _ask("u-1", kind="API", title="pulls again, plus the theme file",
               fields=[], action={"type": "extend_http", "destination": "github",
                       "endpoints": [
                           {"host": "api.github.com",
                            "path_template": "/repos/o/r/pulls", "methods": ["POST"]},
                           {"host": "api.github.com",
                            "path_template": "/repos/o/r/contents/t.json",
                            "methods": ["PUT"]}]})
    assert ask["status"] == "pending"
    out = _answer("u-1", request_id=ask["request_id"], values={})
    assert out["status"] == "answered", out
    assert _stored_paths(base) == ["/repos/o/r/contents/t.json", "/repos/o/r/pulls"]


def _ledger(root, actor="alice"):
    from tinyassets.storage.outbound_connections import ConnectionLedger

    return ConnectionLedger(root / "outbound.db",
                            verify_authenticated_principal=lambda: actor)


def test_a_tab_whose_key_was_revoked_after_it_was_raised_refuses_at_answer(base):  # noqa: F811
    """Codex round 1, T5: the raise-time verdict is a snapshot; the answer
    reruns it, and a grant revoked in between refuses with the uniform
    envelope and writes nothing."""
    from tinyassets.api.http_connection import _ids

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _seed_connection("u-1")
    ask = _ask("u-1", kind="API", title="also the theme file",
               fields=[], action={"type": "extend_http", "destination": "github",
                       "endpoints": [{"host": "api.github.com",
                                      "path_template": "/repos/o/r/contents/t.json",
                                      "methods": ["PUT"]}]})
    assert ask["status"] == "pending"
    _conn_id, grant_id = _ids(universe_id="u-1", destination="github")
    assert _ledger(base).revoke_grant(grant_id) is True

    out = _answer("u-1", request_id=ask["request_id"], values={})

    assert out == {"error": "not_found", "resource": "connection"}
    assert _stored_paths(base) == ["/repos/o/r/pulls"]


def test_an_orphaned_connection_is_not_an_oracle(base):  # noqa: F811
    """Codex round 1, T1: a connection whose grant is revoked is invisible to
    `read_graph target=connections`; the raise-time verdict must not reveal
    its endpoints through `already_held`."""
    from tinyassets.api.http_connection import _ids

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _seed_connection("u-1")
    _conn_id, grant_id = _ids(universe_id="u-1", destination="github")
    assert _ledger(base).revoke_grant(grant_id) is True

    out = _ask("u-1", kind="API", title="let me open pull requests",
               fields=[], action={"type": "extend_http", "destination": "github",
                       "endpoints": [{"host": "api.github.com",
                                      "path_template": "/repos/o/r/pulls",
                                      "methods": ["POST"]}]})

    assert out["error"] == "request_invalid"
    assert "allowed_endpoints" not in out and "scopes" not in out


def test_the_write_is_guarded_on_scopes_as_well_as_endpoints(base):  # noqa: F811
    """Codex round 1, T3: two scope-only widenings read identical endpoints;
    guarding endpoints alone let the second clobber the first's scope."""
    from tinyassets.api.http_connection import _ids

    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _seed_connection("u-1")
    conn_id, _grant_id = _ids(universe_id="u-1", destination="github")
    ledger = _ledger(base)
    endpoints_json, scopes_json = ledger.policy_json(conn_id)
    endpoints = [e.as_dict() for e in ledger._get_connection_resource(conn_id).allowed_endpoints]

    first = ledger.extend_http_connection_endpoints(
        connection_id=conn_id, endpoints=endpoints,
        scopes=("POST", "git_read:o/r"),
        expected_endpoints_json=endpoints_json, expected_scopes_json=scopes_json)
    assert first is True
    # The same snapshot, a moment later: endpoints unchanged, scopes moved.
    second = ledger.extend_http_connection_endpoints(
        connection_id=conn_id, endpoints=endpoints,
        scopes=("POST", "git_write:o/r"),
        expected_endpoints_json=endpoints_json, expected_scopes_json=scopes_json)
    assert second is False, "a stale scopes snapshot must not clobber"
    assert "git_read:o/r" in ledger._get_connection_resource(conn_id).scopes


def test_the_scopes_guard_cannot_be_skipped():
    """Codex round 2: an optional scopes CAS is one a caller forgets."""
    import inspect

    from tinyassets.storage.outbound_connections import ConnectionLedger

    param = inspect.signature(
        ConnectionLedger.extend_http_connection_endpoints
    ).parameters["expected_scopes_json"]
    assert param.default is inspect.Parameter.empty
    src = pathlib.Path(
        __import__("tinyassets.api.http_connection", fromlist=["x"]).__file__
    ).read_text(encoding="utf-8")
    assert src.count("expected_scopes_json=") == src.count("expected_endpoints_json="), (
        "every caller passes both halves of the snapshot"
    )


def test_the_reprovision_path_carries_scopes_from_its_cas_snapshot():
    """Codex round 3: connect_http on an existing key took the git scopes it
    carries forward from an early parsed read and its CAS baseline from a
    later raw read. One policy_json read now feeds both."""
    import tinyassets.api.http_connection as hc

    src = pathlib.Path(hc.__file__).read_text(encoding="utf-8")
    end = (src.index("def _reprovision_conflict(") if "def _reprovision_conflict(" in src
           else src.index("def extend_http("))
    body = src[src.index("def connect_http("):end]
    assert body.count("ledger.policy_json(connection_id)") == 1
    assert "_stored_git_scopes(resource)" not in body
    assert "_git_scopes_in(raw_policy[1])" in body


def test_the_preview_derives_everything_from_one_snapshot():
    """Codex round 2: the union came from a parsed read, the CAS from a later
    raw read; a write between them was lost. Now the raw read is the source."""
    import tinyassets.api.http_connection as hc

    src = pathlib.Path(hc.__file__).read_text(encoding="utf-8")
    body = src[src.index("def _extend_preview("):src.index("def preview_extend_http(")]
    assert "resource.allowed_endpoints" not in body
    assert "resource.scopes" not in body
    assert "_stored_git_scopes(resource)" not in body
    assert "grant.connection_id != connection_id" in body


def test_raise_time_and_answer_time_share_one_definition():
    """`extend_http` and the rail's verdict both go through `_extend_preview`:
    the source of the live bug was two definitions of what can be granted."""
    import tinyassets.api.http_connection as hc

    src = pathlib.Path(hc.__file__).read_text(encoding="utf-8")
    assert src.count("_extend_preview(") >= 3        # def + two callers
    body = src[src.index("def extend_http("):src.index("def _extend_preview(")]
    for rederived in (
        "_parse_allowed_endpoints(", "validate_git_scopes(", "_canonical_endpoint_set(",
    ):
        assert rederived not in body, (
            f"extend_http must not re-derive the verdict beside the preview ({rederived})"
        )


def test_the_served_prompt_teaches_the_two_verdicts():
    import tinyassets.engine_mcp_server as em

    src = pathlib.Path(em.__file__).read_text(encoding="utf-8")
    assert "already_held" in src
    assert "ask_cannot_be_granted" in src
    assert "needs no HTTP endpoint on the git host" in src


def test_the_app_no_longer_offers_send_or_not_now():
    from tinyassets.onboarding import render_app_html

    html, _csp = render_app_html()
    assert '"Not now"' not in html
    for label in ("Accept", "Deny", "Clear", "Send chat (keeps open)"):
        assert f'verb("{label}"' in html, label
    assert json.dumps("declined") in html or '"declined"' in html
