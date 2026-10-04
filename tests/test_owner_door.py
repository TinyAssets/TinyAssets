"""The owner door: the app's reads, through the real routes and middleware.

Live 2026-09-30: the founder's request rail vanished after a deploy while the free
test account kept it. Same code, different DATA: his 7 requests were 34 KB, over
the connector's 24 KB model ceiling, and the app read the rail through that same
connector. These tests pin the structure that makes the class impossible:

* a heavy account gets its WHOLE rail through the owner door, while the same read
  on the connector (the model door) is bounded, visibly;
* the owner door is the connector's authority, not a new one: another account is
  refused exactly as the connector refuses it;
* account type is the only per-account input: the same data reads the same on a
  free and a subscription account;
* history is paged by an explicit cursor that reaches every turn exactly once.
"""

from __future__ import annotations

import json

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from tinyassets import onboarding
from tinyassets.auth import middleware as mw
from tinyassets.auth.provider import Identity

A, B = "user_owner_a", "user_owner_b"
HOME_A, HOME_B = "u-aaaaaaaaaaaaaaaa", "u-bbbbbbbbbbbbbbbb"
READ, STATUS = "/app/api/read", "/app/api/status"


class _Auth:
    def resolve_token(self, token):
        if token in {A, B}:
            return Identity(user_id=token, username=token, capabilities=[
                "tinyassets.universe.write", "tinyassets.extensions.read",
            ])
        return None

    def is_auth_required(self):
        return True

    def resolve_always_writes(self):
        return False

    def writes_require_identity(self):
        return True

    def challenge_unauthenticated(self):
        return True


def _headers(owner=A):
    return {"Authorization": f"Bearer {owner}", "Content-Type": "application/json"}


@pytest.fixture
def door(tmp_path, monkeypatch):
    from tinyassets.daemon_server import grant_universe_ownership, set_founder_home

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setattr(onboarding, "app_config",
                        lambda: {"resource": "https://tinyassets.io/mcp"})
    monkeypatch.setattr(mw, "_provider", _Auth())
    for owner, home in ((A, HOME_A), (B, HOME_B)):
        (tmp_path / home).mkdir()
        (tmp_path / home / "soul.md").write_text("# Test home", encoding="utf-8")
        grant_universe_ownership(tmp_path, universe_id=home, owner_id=owner)
        set_founder_home(tmp_path, founder_sub=owner, universe_id=home,
                         platform_generated=True)
    with TestClient(
        mw.AuthContextMiddleware(Starlette(routes=onboarding.onboarding_routes())),
        base_url="https://tinyassets.io",
    ) as client:
        yield client, tmp_path


def _as(owner):
    return mw.identity_context(Identity(user_id=owner, username=owner,
                                        capabilities=["tinyassets.universe.write"]))


def test_failed_run_and_output_are_owner_readable_but_private(door):
    from tinyassets import runs
    from tinyassets.api.visibility import set_universe_visibility
    from tinyassets.daemon_server import ensure_universe_registered

    client, base = door
    ensure_universe_registered(base, universe_id=HOME_A, universe_path=base / HOME_A)
    set_universe_visibility(HOME_A, "private", source="owner")
    rid = runs.create_run(
        base, branch_def_id="failed-branch", thread_id="t", inputs={},
        actor=A, owner_user_id=A, queue_universe_id=HOME_A,
    )
    runs.update_run_status(base, rid, status="failed", error="provider timed out",
                           output={"reply": "partial result"})
    for target in ("run", "run_output"):
        args = {"target": target, "graph_id": HOME_A, "run_id": rid}
        if target == "run_output":
            args["field_name"] = "reply"
        doc = client.post(READ, headers=_headers(A), json=args).json()
        assert doc.get("run_id") == rid, doc
        assert doc["status"] == "failed"
        if target == "run":
            assert doc["error"] == "provider timed out"
        else:
            assert doc["chunk"] == "partial result"
        denied = client.post(READ, headers=_headers(B), json=args).json()
        assert denied.get("error")
        assert "partial result" not in json.dumps(denied)
        assert "provider timed out" not in json.dumps(denied)


def _ask_many(owner, home, count):
    """``count`` owner-visible asks, each about 2 KB: 40 of them are ~80 KB."""
    from tinyassets.api.pending_requests import request_from_user

    with _as(owner):
        for i in range(count):
            out = request_from_user(universe_id=home, payload=json.dumps({
                "kind": "Question",
                "title": f"Question {i} for the owner",
                "body": ("Context the agent wrote for this ask. " * 15)[:590],
                "action": {"type": "answer"},
                "fields": [
                    {"name": f"f{n}", "type": "text", "label": f"Field {n}",
                     "help": ("How to answer this field, in full. " * 12)[:390]}
                    for n in range(3)
                ],
            }))
            assert "error" not in out, out


def _agent_asks(document):
    return [r for r in document["pending"] if str(r.get("title", "")).startswith("Question ")]


# --------------------------------------------------------------------------- #
# The incident: a heavy account's rail
# --------------------------------------------------------------------------- #


def test_a_heavy_account_gets_its_whole_rail_through_the_owner_door(door):
    client, _ = door
    _ask_many(A, HOME_A, 40)

    reply = client.post(READ, headers=_headers(A), json={"target": "pending_requests"})

    assert reply.status_code == 200
    assert len(reply.content) > 60_000, "the fixture must be heavier than the ceiling"
    document = reply.json()
    assert "truncated" not in document
    assert len(_agent_asks(document)) == 40, "every ask, none cut by a page or a ceiling"
    assert reply.headers["cache-control"] == "no-store"


def test_the_same_read_on_the_model_door_is_bounded_visibly(door):
    """The bound still exists -- at the model door, where a context window does."""
    from tinyassets import universe_server as us

    _ask_many(A, HOME_A, 40)
    with _as(A):
        raw = us.read_graph(target="pending_requests")
        # Before any projection the dispatch is complete: all 40 are there...
        assert len(_agent_asks(json.loads(raw))) == 40
        result = us._structured_return(raw, tool="read_graph",
                                       arguments={"target": "pending_requests"})
    # ...and the model door projects it, saying so.
    assert result.structured_content["truncated"] is True
    assert "pending" not in result.structured_content


def test_the_connector_limit_no_longer_cuts_the_rail(door):
    """``limit=30`` was the connector's default page, and it reached the rail."""
    from tinyassets import universe_server as us

    _ask_many(A, HOME_A, 35)
    with _as(A):
        document = json.loads(us.read_graph(target="pending_requests", limit=3))
    assert len(_agent_asks(document)) == 35


# --------------------------------------------------------------------------- #
# No new authority
# --------------------------------------------------------------------------- #


def test_anonymous_and_unknown_bearers_are_challenged(door):
    client, _ = door
    for path in (READ, STATUS):
        assert client.post(path, json={}).status_code == 401
        assert client.post(path, headers=_headers("nobody"), json={}).status_code == 401


@pytest.mark.parametrize("target,extra", [
    ("pending_requests", {}),
    ("agent_bindings", {"limit": 10}),
    ("model_options", {}),
    ("command_center_files", {}),
])
def test_another_account_is_refused_exactly_as_the_connector_refuses_it(door, target, extra):
    from tinyassets import universe_server as us

    client, _ = door
    _ask_many(A, HOME_A, 2)
    arguments = {"target": target, "graph_id": HOME_A, **extra}

    reply = client.post(READ, headers=_headers(B), json=arguments)
    with _as(B):
        connector = json.loads(us.read_graph(**arguments))

    assert reply.status_code == 200
    assert reply.json() == connector, "the owner door is the connector's gate, unchanged"
    assert "error" in reply.json()
    assert "Question 0" not in reply.text and HOME_A not in json.dumps(
        {k: v for k, v in reply.json().items() if k != "universe_id"})


def test_another_accounts_conversation_is_not_readable(door):
    client, base = door
    from tinyassets.conversation_store import record_turn

    record_turn(base / HOME_A, f"principal:{A}", "founder", "a private line")
    reply = client.post(STATUS, headers=_headers(B),
                        json={"universe_id": HOME_A, "include_conversation": True})
    assert "a private line" not in reply.text


@pytest.mark.parametrize("body,status", [
    ({"target": "pending_requests", "nonsense": 1}, 400),
    ({"target": 7}, 400),
    ({"target": "runs", "limit": True}, 400),
    ([], 400),
])
def test_arguments_are_the_domain_reads_own_and_nothing_else(door, body, status):
    client, _ = door
    reply = client.post(READ, headers=_headers(A), content=json.dumps(body))
    assert reply.status_code == status
    assert reply.json()["error"] == "invalid_arguments"


def test_a_list_read_has_no_default_page(door):
    """The owner door names no page: a list read without one says so."""
    client, _ = door
    reply = client.post(READ, headers=_headers(A), json={"target": "agent_bindings"})
    assert reply.json()["error"] == "limit_required"


# --------------------------------------------------------------------------- #
# One per-account input: account type
# --------------------------------------------------------------------------- #


def _normalized(document):
    text = json.dumps(document, sort_keys=True)
    return json.loads(text.replace(HOME_A, "HOME").replace(HOME_B, "HOME"))


def test_free_and_subscription_accounts_read_the_same_data_identically(door):
    from tinyassets.storage.subscription_state import apply_tier_event
    from tinyassets.universe_owner import account_type_of
    from tinyassets.usage_policy import AccountType

    client, base = door
    _ask_many(A, HOME_A, 5)
    _ask_many(B, HOME_B, 5)
    apply_tier_event(base / HOME_B, tier="paid", event_created=1_000.0)
    assert account_type_of(base, A) is AccountType.FREE
    assert account_type_of(base, B) is AccountType.SUBSCRIPTION

    for body in ({"target": "pending_requests"},
                 {"target": "agent_bindings", "limit": 50},
                 {"target": "model_options"}):
        free = client.post(READ, headers=_headers(A), json=body).json()
        paid = client.post(READ, headers=_headers(B), json=body).json()
        for doc in (free, paid):
            for row in doc.get("pending", ()):
                row.pop("request_id", None)
                row.pop("created_at", None)
        assert _normalized(free) == _normalized(paid), body


def test_a_subscribers_second_universe_is_a_subscription_universe(door):
    """Per ACCOUNT (founder, 2026-09-30): billing writes the home, and a second
    universe used to read its own empty record as free."""
    from tinyassets.daemon_server import grant_universe_ownership
    from tinyassets.storage.subscription_state import apply_tier_event
    from tinyassets.universe_owner import account_type_for_universe
    from tinyassets.usage_policy import AccountType, limits_for, limits_for_universe

    _, base = door
    second = base / "u-second-of-b000"
    second.mkdir()
    grant_universe_ownership(base, universe_id=second.name, owner_id=B)
    apply_tier_event(base / HOME_B, tier="paid", event_created=1_000.0)

    assert account_type_for_universe(second) is AccountType.SUBSCRIPTION
    assert limits_for_universe(second) == limits_for(AccountType.SUBSCRIPTION)
    assert account_type_for_universe(base / "u-nobody-owns-it") is AccountType.FREE


# --------------------------------------------------------------------------- #
# History: an explicit cursor, never a silent default
# --------------------------------------------------------------------------- #


def test_history_pages_reach_every_turn_exactly_once(door):
    from tinyassets.conversation_store import record_turn

    client, base = door
    for i in range(75):
        record_turn(base / HOME_A, f"principal:{A}", "founder", f"line {i}", ts=1_000.0 + i)

    seen, before, pages = [], None, 0
    while True:
        body = {"include_conversation": True, "conversation_limit": 30}
        if before is not None:
            body["conversation_before"] = before
        page = client.post(STATUS, headers=_headers(A), json=body).json()["recent_conversation"]
        pages += 1
        seen = [t["text"] for t in page["turns"]] + seen
        if not page["has_more"]:
            assert "next_before" not in page
            break
        before = page["next_before"]
    assert pages == 3
    assert seen == [f"line {i}" for i in range(75)]


def test_an_unreadable_history_is_reported_not_drawn_empty(door):
    from tinyassets.conversation_store import _db_path, record_turn

    client, base = door
    record_turn(base / HOME_A, f"principal:{A}", "founder", "hello")
    _db_path(base / HOME_A).write_bytes(b"not a database" * 100)
    page = client.post(STATUS, headers=_headers(A),
                       json={"include_conversation": True}).json()["recent_conversation"]
    assert page == {"error": "conversation_unavailable"}


def test_a_heavy_ui_library_reaches_the_app_whole(door):
    """Live 2026-09-30, main account: "Could not read your installed UIs
    (unexpected app UI reply). Default chat is in use." -- the installed-UI read
    crossed the connector's ceiling too. A library is the owner's own bytes."""
    from tinyassets import universe_server as us
    from tinyassets.custom_agents import save_app_ui

    client, base = door
    library = [{"ui_id": f"ui-{i}", "name": f"Screen {i}",
                "bundle": {"html": "<main>" + ("x" * 3_000) + "</main>"}}
               for i in range(12)]
    save_app_ui(base, owner_user_id=A, universe_id=HOME_A, expected_revision=0,
                changes={"ui_library": library})

    reply = client.post(READ, headers=_headers(A), json={"target": "app_ui", "graph_id": HOME_A})
    assert reply.status_code == 200 and len(reply.content) > 30_000
    row = reply.json()["app_ui"]
    assert row["ui_library"] == library and row["universe_id"] == HOME_A

    with _as(A):
        raw = us.read_graph(target="app_ui", graph_id=HOME_A)
        bounded = us._structured_return(raw, tool="read_graph",
                                        arguments={"target": "app_ui"}).structured_content
    assert bounded["truncated"] is True, "the model door still bounds it, visibly"


def test_the_connector_follows_its_own_history_cursor_in_model_sized_pages(door):
    """Codex round 1: the connector returned next_before but could not take it."""
    from tinyassets import universe_server as us
    from tinyassets.conversation_store import record_turn

    _, base = door
    for i in range(45):
        record_turn(base / HOME_A, f"principal:{A}", "founder", f"line {i}", ts=2_000.0 + i)
    with _as(A):
        first = json.loads(us.get_status(include_conversation=True,
                                         conversation_limit=10_000))["recent_conversation"]
        assert len(first["turns"]) == 30, "the model door's page is bounded"
        assert first["has_more"] is True
        second = json.loads(us.get_status(
            include_conversation=True, conversation_before=first["next_before"],
        ))["recent_conversation"]
    assert [t["text"] for t in second["turns"]] == [f"line {i}" for i in range(15)]
    assert second["has_more"] is False
