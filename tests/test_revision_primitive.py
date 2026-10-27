"""S34 revision-primitive: one compare-and-swap primitive, and no collection door reaches a box.

An etag compare-and-swap store is a persisting module that compares a stored etag
with an expected one: a line comparing ``etag`` with ``expected``, or SQL
``etag = ?``. The generic collection in the broker's per-owner conversation store
is the one revision primitive for shared rows; app rooms and the board are row sets
in it. Every other such store is named below, and that list only shrinks.
``COLLECTION`` reaches the broker from the router's owner channel only, and the room
door answers ``/app/<slug>/state`` without forwarding to, asking or waking a box.
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tests.test_collections import _rows, broker  # noqa: F401
from tests.test_owner_policy import admissions
from tinyassets import owner_router, room_door
from tinyassets import rpc_frames as rf
from tinyassets.auth.provider import Identity
from tinyassets.broker.policy import account_path
from tinyassets.published_apps import bind

ROOT = Path(__file__).resolve().parents[1]
#: Falls to one with app_room's own compare-and-swap deleted (S5a); it never rises.
CEILING = 1
PRIMITIVES = {"tinyassets/broker/conversations.py"}
#: Etag compare-and-swap stores that are not shared rows. A ceiling that only falls.
ALLOWED = {
    # The owner's own UI library in the owner's database: an edit may pin the UI's
    # etag (expected_etag). One writer, never a room or the board.
    "tinyassets/app_ui_store.py",
}
ALLOWED_CEILING = 1
_CAS = re.compile(r"(?m)^(?=[^\n]*etag)(?=[^\n]*expected)(?=[^\n]*[!=]=)[^\n]*$"
                  r"|\betag\s*=\s*\?")
_PERSISTS = re.compile(r"\.execute\(|\bos\.(?:replace|write)\(|\.write_(?:text|bytes)\(")
#: Every way the router reaches a box.
BOX_PATHS = {"forward", "ask_box", "box_result", "route", "resolve_box", "wake_owner",
             "admit_box"}

pytestmark = pytest.mark.verifies("trusted-message-ledger:One revision primitive")


def primitives(root: Path = ROOT) -> set[str]:
    """Persisting modules that compare a stored etag with an expected one."""
    found = set()
    for path in (root / "tinyassets").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if _CAS.search(source) and _PERSISTS.search(source):
            found.add(path.relative_to(root).as_posix())
    return found


def test_one_revision_primitive():
    found = primitives()
    assert len(found - ALLOWED) <= CEILING and found - ALLOWED == PRIMITIVES, found
    assert len(ALLOWED) <= ALLOWED_CEILING and ALLOWED <= found, "remove a stale allowance"


@pytest.mark.parametrize("source", [
    "def cas(path, body, expected_etag):\n    if current != expected_etag:\n"
    "        raise Conflict\n    path.write_text(body)\n",
    "def cas(db, body, etag):\n    db.execute('UPDATE rows SET body=? WHERE id=? AND "
    "etag = ?', (body, 1, etag))\n",
])
def test_the_guard_sees_a_second_room_store(tmp_path, source):
    probe = tmp_path / "tinyassets" / "rooms.py"
    probe.parent.mkdir()
    probe.write_text(source)
    assert primitives(tmp_path) == {"tinyassets/rooms.py"}


def test_no_collection_door_names_a_way_into_a_box():
    tree = ast.parse(Path(room_door.__file__).read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
              for alias in node.names}
    assert not names & BOX_PATHS
    for method in ("GET", "POST", "PUT", "DELETE"):
        trusted = owner_router.TRUSTED._app("/app/chess/state", method)
        assert trusted is not None and not trusted.owner and trusted.session
        assert owner_router.session_door("/app/chess/state", method)
    assert not owner_router.forwarded_path("/app/{slug}/state")
    assert owner_router.forwarded_path("/app/chess") and owner_router.forwarded_path(
        "/app/chess/state/x")


def test_the_box_serves_no_room(tmp_path, monkeypatch):
    """The router serves the room route; a box's route table and ingress have none."""
    import os

    from tinyassets import owner_runtime, universe_server
    from tinyassets.app_access import visitor_surface

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    router_paths = {route.path for route in universe_server.create_streamable_http_app().routes}
    monkeypatch.setattr(owner_runtime, "_runtime", owner_runtime.OwnerRuntime(
        "alice", os.getuid(), tmp_path / "alice", os.getpid(), None))
    box_paths = {route.path for route in universe_server.create_streamable_http_app().routes}
    assert "/app/{slug}/state" in router_paths - box_paths and "/app/{slug}" in box_paths
    assert not any(visitor_surface("/app/chess/state", method) for method in ("GET", "POST"))
    assert visitor_surface("/app/chess", "GET") and not visitor_surface("/app/chess", "POST")


def test_the_box_channel_refuses_collection(tmp_path):
    from tinyassets.broker.server import BOX, _Connection

    server = SimpleNamespace(_owner_identities=admissions(tmp_path))

    async def send(doc):
        connection = _Connection(server, None, BOX)
        connection._box_owner = "alice"  # admitted, so only the op itself is refused
        await connection._connection_op(doc["op"], doc)

    for action in ("write", "pull"):
        with pytest.raises(rf.FrameError, match="not available on the box channel"):
            asyncio.run(send({"op": "COLLECTION", "action": action, "principal": "alice"}))
    assert not account_path(tmp_path, "alice", "conversations.db").exists()


@pytest.fixture
def router(broker, monkeypatch):  # noqa: F811
    """The real router and route table; every way into a box fails the test."""
    from starlette.applications import Starlette

    from tinyassets import onboarding, router_traffic, routing_snapshot
    from tinyassets.auth import middleware
    from tinyassets.broker import owner_identities
    from tinyassets.onboarding import owner_sessions

    def refuse(*args, **kwargs):
        raise AssertionError("a room door reached a box")

    for name in ("forward", "box_result", "resolve_box", "admit_box"):
        monkeypatch.setattr(owner_router, name, refuse)
    monkeypatch.setattr(owner_router.Front, "ask_box", refuse)
    monkeypatch.setattr(owner_identities, "wake_owner", refuse)
    monkeypatch.setattr(routing_snapshot, "snapshot", lambda root: SimpleNamespace(
        require_current=lambda: None))
    monkeypatch.setattr(router_traffic, "admits", lambda *args: True)
    monkeypatch.setattr("tinyassets.storage.data_dir", lambda: broker.root)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(broker.root))
    monkeypatch.setenv("UNIVERSE_SERVER_URL", "https://tinyassets.io")
    from tinyassets.mailbox_door import rest_routes

    own = Starlette(routes=[route for route in onboarding.onboarding_routes()
                            if route.path == "/app/{slug}/state"] + rest_routes())
    door = owner_router.OwnerRouter(own, resolver=refuse, admitter=refuse)
    bind(broker.root, "alice", "chess", "Chess")

    def call(method, path, caller, body=None, *, outside=None, cookie=None, provider=None):
        metadata = {} if outside is None else {"outside_origin": outside}
        if provider is not None:
            metadata["auth_provider"] = provider

        async def app(scope, receive, send):
            token = middleware._current_identity.set(Identity(caller, caller,
                                                              metadata=metadata))
            try:
                await door(scope, receive, send)
            finally:
                middleware._current_identity.reset(token)

        headers = {"origin": "https://tinyassets.io"}
        if cookie is not None:
            headers["cookie"] = f"{owner_sessions.COOKIE}={cookie}"

        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="https://tinyassets.io") as client:
                return await client.request(method, path, json=body, headers=headers)
        return asyncio.run(exercise())
    return call


def test_the_router_answers_a_room_from_the_publishers_store(router, broker):  # noqa: F811
    """Read, write and pull: the publisher's collection, the verified caller, no box."""
    turn = {"scope": "room", "key": "turn", "value": {"user_id": "alice"},
            "expected_etag": ""}
    first = router("POST", "/app/chess/state", "bob", turn)
    assert first.status_code == 200 and first.json()["writer"] == "bob"
    lost = router("POST", "/app/chess/state", "cara", turn)
    assert lost.status_code == 409 and lost.json() == {"error": "conflict",
                                                       "etag": first.json()["etag"]}
    seat = dict(turn, scope="player", key="seat", value={"secret": "cara"})
    assert router("POST", "/app/chess/state", "cara", seat).status_code == 200
    bob = router("GET", "/app/chess/state?since=0", "bob").json()
    assert bob == {"seq": 2, "records": [{"scope": "room", "key": "turn",
                                          "value": {"user_id": "alice"},
                                          "etag": first.json()["etag"], "writer": "bob",
                                          "seq": 1}]}
    cara = router("GET", "/app/chess/state?since=1", "cara").json()["records"]
    assert [(row["scope"], row["key"], row["value"]) for row in cara] == [
        ("player", "seat", {"secret": "cara"})]
    assert router("GET", "/app/chess/state?since=x", "bob").status_code == 400
    assert router("PUT", "/app/chess/state", "bob").status_code == 405
    # An unpublished slug is the caller's own room, never the publisher's.
    assert router("POST", "/app/draft/state", "bob", turn).status_code == 200
    assert [row[:2] for row in _rows(broker.root, "bob")] == [("room:draft:main", "room:turn")]
    assert json.loads(_rows(broker.root, "alice")[0][-1]) == {"user_id": "alice"}


OUTSIDE = {"client": "a" * 64, "family": "b" * 64}


def test_an_outside_client_is_refused_on_app_paths_before_any_handler(router, broker):  # noqa: F811
    """As at the box: an outside client has the MCP surface only, revoked or not."""
    turn = {"scope": "player", "key": "seat", "value": {}, "expected_etag": ""}
    assert router("POST", "/app/chess/state", "bob", turn).status_code == 200
    for method, body in (("GET", None), ("POST", turn)):
        refused = router(method, "/app/chess/state?since=0", "bob", body, outside=OUTSIDE)
        assert (refused.status_code, refused.json()) == (403, {
            "error": "forbidden", "detail": owner_router.OUTSIDE_SURFACE})
    assert len(_rows(broker.root, "alice")) == 1


def test_the_outside_refusal_leaves_api_mailbox_alone(router):
    """An API-key agent's outside_origin still reaches the mailbox doors (/api, not /app)."""
    for method, path, body in (("GET", "/api/mailbox", None),
                               ("POST", "/api/mailbox/ack", {"through": 0}),
                               ("POST", "/api/mailbox/replies", {"client_message_id": "c",
                                                                 "body": "hi"})):
        answer = router(method, path, "alice", body, outside=OUTSIDE, provider="api_key")
        # The mailbox door itself answered (outside access is off for this unregistered
        # key), never the router's /app outside-surface refusal.
        assert answer.json()["error"] in {"refused", "outside_access_off"}, answer.text
        assert answer.json() != {"error": "forbidden", "detail": owner_router.OUTSIDE_SURFACE}


def test_only_the_publisher_clears_from_a_protected_session(router, broker):  # noqa: F811
    import time

    from tinyassets.onboarding import owner_sessions

    with owner_sessions.store() as sessions:
        for user in ("alice", "bob"):
            sessions.execute("INSERT INTO owner_sessions VALUES (?,?,?)", (
                owner_sessions.hashed(f"{user}-cookie"), json.dumps({"user_id": user}),
                time.time() + 3600))
    seat = {"scope": "player", "key": "seat", "value": {}, "expected_etag": ""}
    for player in ("bob", "cara"):
        assert router("POST", "/app/chess/state", player, seat).status_code == 200
    assert router("DELETE", "/app/chess/state", "bob", {}, cookie="bob-cookie").json() == {
        "error": "private_surface"}
    assert router("DELETE", "/app/chess/state", "alice", {}).json() == {
        "error": "interactive_owner_session_required"}
    assert router("DELETE", "/app/chess/state", "alice", {"writer": "bob"},
                  cookie="alice-cookie").json() == {"deleted": 1}
    assert [row[4] for row in _rows(broker.root, "alice")] == ["cara"]
    # A bearer beside another account's session cookie is refused, as at the box door.
    assert router("GET", "/app/chess/state", "alice", cookie="bob-cookie").json() == {
        "error": "identity_conflict"}
