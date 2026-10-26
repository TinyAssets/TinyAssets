"""One published app lives in its owner's box and is played by verified callers."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from tinyassets.app_room import Conflict, cas, read_app, sync, write_app
from tinyassets.auth.middleware import _current_identity
from tinyassets.auth.provider import Identity
from tinyassets.published_apps import bind, owner_for

pytestmark = pytest.mark.verifies(
    "post-c6-multiplayer:A published app lives once in its owner's box",
    "post-c6-multiplayer:A signed-in player is routed as a verified caller",
    "post-c6-multiplayer:Room writes commit by compare-and-swap",
    "post-c6-multiplayer:A visiting player cannot use private owner tools",
)

_REPO = Path(__file__).resolve().parents[1]
_SHARED = (
    "tinyassets/owner_router.py",
    "tinyassets/broker/server.py",
    "tinyassets/broker/waker.py",
    "tinyassets/broker/egress.py",
    "tinyassets/broker/owner_reads.py",
    "tinyassets/published_apps.py",
)


def _runtime(monkeypatch, root: Path, owner: str = "alice"):
    from tinyassets import owner_runtime

    monkeypatch.setattr(owner_runtime, "_runtime", owner_runtime.OwnerRuntime(
        owner, os.getuid(), root, os.getpid(), None,
    ))


def test_app_and_records_stay_in_the_owner_box(tmp_path: Path):
    """A second account adds no copy, and the binding holds no room body."""
    owner = tmp_path / "alice"
    other = tmp_path / "bob"
    write_app(owner, "chess", name="Chess", markup="board")
    from tinyassets.app_room import mark_published

    mark_published(owner, "chess", published=True)
    bind(tmp_path, "alice", "chess", "Chess")
    committed = cas(
        owner, "chess", scope="room", key="turn", value={"seat": "bob"},
        expected_etag="", caller="bob", owner="alice",
    )
    listing = json.loads((tmp_path / ".published" / "apps.json").read_text(encoding="utf-8"))
    assert listing == {"chess": {"name": "Chess", "owner": "alice"}}
    assert owner_for(tmp_path, "chess") == "alice"
    assert "board" not in json.dumps(listing)
    assert not (other / "apps").exists()
    with pytest.raises(FileNotFoundError):
        read_app(other, "chess", caller="bob", owner="bob")
    assert sync(other, "chess", since=0, caller="bob", owner="bob") == {
        "seq": 0, "records": [],
    }
    assert (owner / "apps" / "chess" / "state.json").is_file()
    assert committed["writer"] == "bob"
    with pytest.raises(PermissionError):
        bind(tmp_path, "bob", "chess", "Chess")


def test_same_etag_commits_once_and_player_records_stay_with_the_caller(tmp_path: Path):
    """Two room writes cannot share an etag, and a body cannot choose the writer."""
    root = tmp_path / "alice"
    write_app(root, "chess", name="Chess", markup="")
    from tinyassets.app_room import mark_published

    mark_published(root, "chess", published=True)
    first = cas(
        root, "chess", scope="room", key="turn", value={"by": "bob"},
        expected_etag="", caller="bob", owner="alice",
    )
    with pytest.raises(Conflict) as caught:
        cas(
            root, "chess", scope="room", key="turn", value={"by": "cara"},
            expected_etag="", caller="cara", owner="alice",
        )
    assert caught.value.etag == first["etag"]
    stored = sync(root, "chess", since=0, caller="cara", owner="alice")
    assert stored["records"] == [{
        "scope": "room", "key": "turn", "value": {"by": "bob"},
        "etag": first["etag"], "writer": "bob", "seq": 1,
    }]
    cara = cas(
        root, "chess", scope="player", key="seat",
        value={"user_id": "bob", "secret": "cara-only"},
        expected_etag="", caller="cara", owner="alice",
    )
    assert cara["writer"] == "cara"
    bob_view = sync(root, "chess", since=0, caller="bob", owner="alice")
    assert [row["scope"] for row in bob_view["records"]] == ["room"]
    cara_view = sync(root, "chess", since=1, caller="cara", owner="alice")
    assert cara_view["records"][0]["writer"] == "cara"
    assert cara_view["records"][0]["value"]["secret"] == "cara-only"


def test_unpublished_app_is_owner_only_and_shared_code_has_no_room(tmp_path: Path):
    """A visitor cannot open an unpublished app, and shared doors do not name the room."""
    root = tmp_path / "alice"
    write_app(root, "chess", name="Chess", markup="board")
    assert read_app(root, "chess", caller="alice", owner="alice")["markup"] == "board"
    with pytest.raises(PermissionError):
        read_app(root, "chess", caller="bob", owner="alice")
    for rel in _SHARED:
        source = (_REPO / rel).read_text(encoding="utf-8")
        assert "app_room" not in source
        assert "state.json" not in source


def test_forged_caller_does_not_select_the_box(tmp_path: Path, monkeypatch):
    """The published slug selects the box. The signed-in account stays the caller."""
    from tinyassets.owner_router import BoxRoute, OwnerRouter, door_headers

    bind(tmp_path, "alice", "chess", "Chess")
    identity = Identity(user_id="bob", username="bob")
    route = BoxRoute(tmp_path / "alice.sock", "s" * 43)
    scope = {
        "type": "http", "method": "GET", "path": "/app/chess",
        "headers": [(b"x-tinyassets-caller-user-id", b"mallory")],
        "query_string": b"owner=mallory",
    }
    owner, headers = door_headers(scope, identity, route, "alice")
    assert owner == "alice"
    caller_headers = [value for name, value in headers
                      if name == b"x-tinyassets-caller-user-id"]
    assert caller_headers == [b"bob"]
    assert dict(headers)[b"x-owner"] == b"alice"

    seen = {}

    async def app(scope, receive, send):
        raise AssertionError("the shared app handled a published slug")

    async def forward(scope, receive, send, identity, route, *, cold=True, box_owner=None):
        seen["box"] = box_owner
        seen["caller"] = identity.user_id

    def resolver(owner):
        seen.setdefault("resolved", []).append(owner)
        if owner == "missing":
            raise LookupError(owner)
        return route

    def admit(owner):
        seen["admitted"] = owner

    monkeypatch.setattr("tinyassets.routing_snapshot.snapshot",
                        lambda root: type("S", (), {"require_current": lambda self: None})())
    monkeypatch.setattr("tinyassets.router_traffic.admits", lambda *args, **kwargs: True)
    monkeypatch.setattr("tinyassets.storage.data_dir", lambda: tmp_path)
    monkeypatch.setattr("tinyassets.onboarding.owner_sessions.lookup", lambda cookie: None)
    monkeypatch.setattr("tinyassets.auth.middleware.current_identity_or_none", lambda: identity)
    monkeypatch.setattr("tinyassets.owner_router.forward", forward)
    router = OwnerRouter(app, resolver=resolver, admitter=admit)

    async def call(path):
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await router({
            "type": "http", "method": "GET", "path": path, "query_string": b"owner=mallory",
            "headers": [(b"x-tinyassets-caller-user-id", b"mallory")],
        }, receive, send)
        return messages

    asyncio.run(call("/app/chess"))
    assert seen["resolved"] == ["alice"]
    assert seen["box"] == "alice"
    assert seen["caller"] == "bob"
    assert "admitted" not in seen

    seen.clear()
    messages = asyncio.run(call("/app/missing"))
    assert seen["resolved"] == ["bob"]
    assert messages == []


def test_visitor_private_tools_and_room_door(tmp_path: Path, monkeypatch):
    """write_page and read_page refuse a visitor. The room door still opens."""
    from tinyassets.owner_activity import activity
    from tinyassets.owner_ingress import OwnerIngress
    from tinyassets.universe_server import read_page, write_page

    root = tmp_path / "alice"
    _runtime(monkeypatch, root)
    visitor = Identity(
        user_id="alice", username="alice", metadata={"caller_user_id": "bob"},
    )
    token = _current_identity.set(visitor)
    try:
        assert json.loads(read_page(page="notes"))["error"] == "private_surface"
        assert json.loads(write_page(page="notes", content="no"))["error"] == "private_surface"
    finally:
        _current_identity.reset(token)
    assert not (root / "notes").exists()
    owner = Identity(
        user_id="alice", username="alice", metadata={"caller_user_id": "alice"},
    )
    token = _current_identity.set(owner)
    try:
        assert "private_surface" not in read_page(page="app_ui")
    finally:
        _current_identity.reset(token)

    activity.draining = False
    activity.revoked = False
    secret = tmp_path / "box-secret"
    secret.write_bytes(b"a" * 43)
    opened = []

    async def app(scope, receive, send):
        opened.append((scope["path"], scope.get("tinyassets_caller_user_id")))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    ingress = OwnerIngress(app, secret)

    async def once(path, method, caller, account="bob"):
        messages = []
        encoded = base64.urlsafe_b64encode(json.dumps(asdict(Identity(
            user_id=account, username=account))).encode())

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        headers = [
            (b"x-box-secret", b"a" * 43), (b"x-owner", b"alice"),
            (b"x-owner-identity", encoded),
        ]
        if caller is not None:
            headers.append((b"x-tinyassets-caller-user-id", caller))
        await ingress({
            "type": "http", "method": method, "path": path, "query_string": b"",
            "headers": headers,
        }, receive, send)
        return messages[0]["status"]

    assert asyncio.run(once("/mcp", "POST", b"bob")) == 403
    assert opened == []
    assert asyncio.run(once("/app/chess/state", "POST", b"bob")) == 200
    assert opened == [("/app/chess/state", "bob")]
    assert asyncio.run(once("/mcp", "POST", None)) == 401
    assert asyncio.run(once("/mcp", "POST", None, account="alice")) == 200


def test_state_route_attributes_the_verified_caller(tmp_path: Path, monkeypatch):
    """The room door writes as the caller on the scope, not a user id in the body."""
    from starlette.requests import Request

    from tinyassets.onboarding.app_play import handle_app_state

    root = tmp_path / "alice"
    _runtime(monkeypatch, root)
    write_app(root, "chess", name="Chess", markup="")
    from tinyassets.app_room import mark_published

    mark_published(root, "chess", published=True)
    payload = json.dumps({
        "scope": "room", "key": "turn", "value": {"user_id": "alice"},
        "expected_etag": "",
    }).encode()

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    response = asyncio.run(handle_app_state(Request({
        "type": "http", "method": "POST", "path": "/app/chess/state",
        "path_params": {"slug": "chess"}, "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "tinyassets_caller_user_id": "bob",
    }, receive)))
    assert response.status_code == 200
    assert json.loads(response.body)["writer"] == "bob"
