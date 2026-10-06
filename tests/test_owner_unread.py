"""Cross-device exact-ID receipts, late arrivals and real owner authorization."""
import asyncio
import json
import sqlite3
from contextlib import closing

import pytest
from starlette.requests import Request

from tests.test_automations import OWNER, UNIVERSE, _seed_owner
from tests.test_onboarding_openai_device import _user
from tinyassets.conversation_store import record_exchange
from tinyassets.storage.owner_unread import attention
from tinyassets.storage.pending_requests import create_request, get_request


def message(root, owner=OWNER):
    record_exchange(root, "principal:" + owner, "hello", "agent reply")
    with closing(sqlite3.connect(root / ".conversation_memory.db")) as conn:
        return str(conn.execute("SELECT max(id) FROM conversation_turns").fetchone()[0])


def test_two_devices_share_receipts_without_clearing_later_arrivals(tmp_path):
    root = tmp_path / UNIVERSE
    root.mkdir()
    first = message(root)
    message(root, "other")
    record_exchange(root, "agent:extra:principal:other:principal:" + OWNER,
                    "malformed legacy thread", "not this owner's reply")
    ask = create_request(root, kind="question", title="Pick one", body="Choose", fields=[],
                         action={}, dedupe_key="one")
    assert attention(tmp_path, root, OWNER, UNIVERSE) == {"messages": 1, "asks": 1}
    message(root)  # Arrived after device one rendered its first message.
    got = attention(tmp_path, root, OWNER, UNIVERSE, messages=[first, "9999"],
                    asks=[ask["request_id"]])
    assert got == {"messages": 1, "asks": 0}
    assert attention(tmp_path, root, OWNER, UNIVERSE) == got  # Device two, fresh connection.
    assert get_request(root, ask["request_id"])["status"] == "pending"
    assert attention(tmp_path, root, "other", UNIVERSE)["messages"] == 1


def drive(base, monkeypatch, *, owner=OWNER, method="GET", body=None, origin=True):
    from tinyassets.auth.middleware import identity_context
    from tinyassets.onboarding.owner_unread import handle_unread

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    headers = [(b"host", b"app.test"), (b"content-type", b"application/json")]
    if origin:
        headers.append((b"origin", b"https://app.test"))
    scope = {"type": "http", "method": method, "path": "/app/unread", "headers": headers,
             "query_string": ("universe=" + UNIVERSE).encode()}

    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}

    async def run():
        with identity_context(_user(owner) if owner else None):
            return await handle_unread(Request(scope, receive))

    return asyncio.run(run())


@pytest.mark.parametrize("owner,status", [(OWNER, 200), ("user_other", 404), (None, 401)])
def test_route_owner_matrix(tmp_path, monkeypatch, owner, status):
    _seed_owner(tmp_path)
    assert drive(tmp_path, monkeypatch, owner=owner).status_code == status


def test_receipt_post_requires_same_origin_and_ignores_claimed_owner(tmp_path, monkeypatch):
    _seed_owner(tmp_path)
    root = tmp_path / UNIVERSE
    first = message(root)
    body = {"universe": UNIVERSE, "messages": [first], "owner_user_id": "other"}
    assert drive(tmp_path, monkeypatch, method="POST", body=body, origin=False).status_code == 403
    assert attention(tmp_path, root, OWNER, UNIVERSE)["messages"] == 1
    response = drive(tmp_path, monkeypatch, method="POST", body=body)
    assert response.status_code == 200
    assert json.loads(response.body)["messages"] == 0


@pytest.mark.parametrize("status", ["pending", "unresolved", "deferred", "approved"])
def test_derived_pending_asks_use_the_same_durable_receipts(tmp_path, status):
    root = tmp_path / UNIVERSE
    root.mkdir()
    pending = [{"request_id": "setup", "status": status},
               {"request_id": "optional", "status": "optional"}]
    assert attention(tmp_path, root, OWNER, UNIVERSE, pending=pending)["asks"] == 1
    assert attention(tmp_path, root, OWNER, UNIVERSE, pending=pending,
                     asks=["setup"])["asks"] == 0
    assert attention(tmp_path, root, OWNER, UNIVERSE, pending=pending)["asks"] == 0


@pytest.mark.parametrize("rebind", [True, False])
def test_account_deletion_removes_former_home_receipts_and_preserves_other_owner(tmp_path, rebind):
    from tests.test_account_deletion import HOME_A, HOME_B, A, B, _seed_user
    from tinyassets.account_deletion import delete_account
    from tinyassets.storage import DB_FILENAME

    for owner, home in ((A, HOME_A), (B, HOME_B)):
        root = _seed_user(tmp_path, owner, home)
        ident = message(root, owner)
        attention(tmp_path, root, owner, home, messages=[ident])
    if rebind:
        _seed_user(tmp_path, A, "u-new-home")
    else:
        with closing(sqlite3.connect(tmp_path / DB_FILENAME)) as conn, conn:
            conn.execute("DELETE FROM founder_home WHERE founder_sub=?", (A,))
    receipt = delete_account(tmp_path, founder_sub=A, cancel_billing=lambda home: "cancelled",
                             delete_identity=lambda sub: "deleted")
    assert receipt["unfinished_phases"] == []
    with closing(sqlite3.connect(tmp_path / DB_FILENAME)) as conn:
        owners = conn.execute("SELECT owner_user_id FROM owner_view_receipts").fetchall()
    assert owners == [(B,)]
