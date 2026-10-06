"""Only a bounded proposal reaches the owner's pending-request queue."""
from __future__ import annotations

import json

import pytest
from fastmcp import Client

from tests.engine_authority_helpers import mock_engine_admission
from tests.owner_answer import answer_request as owner_answer
from tests.test_pending_requests import _login, _logout, _make_universe
from tinyassets import engine_mcp_server as server
from tinyassets import engine_steering
from tinyassets.api import pending_requests as api
from tinyassets.storage.pending_requests import get_request, list_pending

PROPOSAL = {"action": "Fix the stale documentation", "why": "The example fails.",
            "evidence": "The documented argument is absent."}


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    root = _make_universe(tmp_path, "u-test", admin="owner")
    _login("owner")
    monkeypatch.setattr(server, "_ACTOR_ID", "owner")
    monkeypatch.setattr(server, "_GRAPH_ID", "u-test")
    mock_engine_admission(monkeypatch, {"u-test"})
    # Proposing is research-only, so the research session is the baseline every
    # case here starts from; the cases about refusing it override this.
    monkeypatch.setattr(engine_steering, "_session_key", lambda: "research:agent:turn")
    yield root
    _logout()


def propose(payload=None):
    return api.propose(universe_id="u-test", payload=PROPOSAL if payload is None else payload)


@pytest.mark.asyncio
async def test_served_proposal(owner, monkeypatch):
    async with Client(server.mcp) as client:
        result = await client.call_tool("write_graph", {
            "target": "proposal", "operation": "propose", "payload_json": PROPOSAL,
        })
    assert not result.is_error
    request_id = json.loads(result.content[0].text)["request_id"]
    row = get_request(owner, request_id)
    assert row["kind"] == "proposal"
    assert row["origin"] == "agent"
    assert row["fields"] == []
    assert row["status"] == "pending"
    assert row["action"] == {"type": "start_activity", "title": PROPOSAL["action"],
                             "brief": PROPOSAL["why"] + "\n\n" + PROPOSAL["evidence"]}
    assert api.displayed_row_matches(row)


@pytest.mark.parametrize("field,limit", [("action", 200), ("why", 1000), ("evidence", 2000)])
@pytest.mark.parametrize("bad", ["missing", "oversized", "empty", "object"])
def test_invalid_field(owner, field, limit, bad):
    payload = dict(PROPOSAL)
    if bad == "missing":
        del payload[field]
    else:
        payload[field] = {"oversized": "x" * (limit + 1), "empty": " ", "object": {}}[bad]
    result = propose(payload)
    assert result["error"] == "request_invalid"
    assert field in result["detail"]
    assert list_pending(owner) == []


@pytest.mark.parametrize("extra", [{"kind": "connect"}, {"fields": []},
                                    {"type": "connect"}, {"action_type": "connect"}])
def test_cannot_choose_request_fields(owner, extra):
    assert propose({**PROPOSAL, **extra})["error"] == "request_invalid"


def test_action_cannot_be_object_or_multiple_lines(owner):
    for value in ({"type": "connect"}, "first\nsecond", "first\n"):
        assert "action" in propose({**PROPOSAL, "action": value})["detail"]


def test_exact_limits_and_dedupe(owner):
    payload = {"action": "a" * 200, "why": "b" * 1000, "evidence": "c" * 2000}
    first = propose(payload)
    again = propose({**payload, "why": "new reason"})
    assert first["request_id"] == again["request_id"]
    row = list_pending(owner)[0]
    assert len(row["title"]) == 200
    assert row["body"] == payload["why"] + "\n\n" + payload["evidence"]


def test_approval_starts_once(owner, monkeypatch):
    calls = []
    monkeypatch.setattr(api, "_start_approved_proposal",
                        lambda uid, row: calls.append((uid, row["action"])) or {"activity_id": "a"})
    row = propose()
    payload = {"request_id": row["request_id"], "values": {}, "decision": "allowed"}
    result = owner_answer(universe_id="u-test", payload=payload)
    assert result["activity_id"] == "a"
    assert result["decision"] == "allowed"
    assert owner_answer(universe_id="u-test", payload=payload)["error"] == "already_resolved"
    assert calls == [("u-test", row["action"])]


@pytest.mark.parametrize("decline", [
    {"decision": "declined"}, {"decline": True}, {"dismiss": True},
])
def test_decline_does_not_start(owner, monkeypatch, decline):
    def forbidden(*a):
        pytest.fail("decline must not start an activity")

    monkeypatch.setattr(api, "_start_approved_proposal", forbidden)
    row = propose()
    result = owner_answer(universe_id="u-test", payload={
        "request_id": row["request_id"], "values": {}, **decline,
    })
    assert result["status"] in {"answered", "dismissed"}


def test_unwired_approval_refuses_in_the_open_and_stays_pending(owner):
    """Approve on a proposal whose start path is unwired is a refusal, not a crash.

    The owner reads why nothing started and keeps a pending request to retry
    once #4221 lands; an unhandled NotImplementedError reached them instead.
    """
    row = propose()
    result = owner_answer(universe_id="u-test", payload={
        "request_id": row["request_id"], "values": {},
    })
    assert result["error"] == "proposal_start_unavailable"
    assert result["request_pending"] is True
    assert "#4221" in result["detail"]
    assert get_request(owner, row["request_id"])["status"] == "pending"
    # Still refused on a second Approve, and still never resolved.
    assert owner_answer(universe_id="u-test", payload={
        "request_id": row["request_id"], "values": {},
    })["error"] == "proposal_start_unavailable"
    assert get_request(owner, row["request_id"])["status"] == "pending"


@pytest.mark.parametrize("decision", [{"decision": "allowed"}, {"decision": "declined"},
                                      {"dismiss": True}])
def test_bearer_cannot_decide_a_proposed_activity(owner, monkeypatch, decision):
    def forbidden(*args):
        pytest.fail("bearer answer must not start an activity")

    monkeypatch.setattr(api, "_start_approved_proposal", forbidden)
    row = propose()
    result = api.answer_request(universe_id="u-test", payload={
        "request_id": row["request_id"], "values": {}, **decision,
    })
    assert result["error"] == "interactive_approval_required"
    assert get_request(owner, row["request_id"])["status"] == "pending"


@pytest.mark.parametrize("session", ["thread:owner", "", "researchx:agent", "agent:research:x"])
def test_only_a_research_session_may_propose(owner, monkeypatch, session):
    """``write_graph target=proposal`` reaches one propose() for every caller.

    Everything that is not the reserved ``research:`` prefix is refused, so a
    malformed key cannot buy the one write research is allowed.
    """
    monkeypatch.setattr(engine_steering, "_session_key", lambda: session)
    assert propose()["error"] == "proposals_are_research_only"
    assert list_pending(owner) == []


@pytest.mark.asyncio
async def test_served_proposal_refused_outside_research(owner, monkeypatch):
    monkeypatch.setattr(engine_steering, "_session_key", lambda: "thread:owner")
    async with Client(server.mcp) as client:
        result = await client.call_tool("write_graph", {
            "target": "proposal", "operation": "propose", "payload_json": PROPOSAL,
        }, raise_on_error=False)
    assert json.loads(result.content[0].text)["error"] == "proposals_are_research_only"
    assert list_pending(owner) == []
