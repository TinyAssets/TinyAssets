"""A first sign-in is admitted by the broker; the router only asks (C5, C6).

Attempt 10 (2026-10-22): after cutover, ``/mcp`` under the canary's bearer
answered 404 because nothing admits an owner that the migration did not carry.
The core canary and the canary service principal are ordinary owners: each is
admitted by the broker's ADMIT_OWNER path and routed to its own box. Synthetic
principals only.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from starlette.responses import JSONResponse

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.support.admission import drive, projection
from tests.test_broker_server import broker  # noqa: F401
from tests.test_owner_router import admitted  # noqa: F401
from tinyassets.auth.provider import Identity

SERVICE_TOKEN = "s" * 48


@pytest.fixture
def door(admitted, broker, tmp_path, monkeypatch):  # noqa: F811
    """The real router stack over the real broker RPC; forwarding is recorded."""
    from tinyassets import owner_router
    from tinyassets.auth import middleware
    from tinyassets.broker.admission import read
    from tinyassets.routing_snapshot import RoutingSnapshot

    broker.server._routing = projection(admitted.path)
    (tmp_path / ".ordinary-admission.json").write_text(json.dumps({"ordinary": True}))
    (tmp_path / ".router-credentials.json").write_text(
        json.dumps({"TINYASSETS_WIKI_CANARY_TOKEN": SERVICE_TOKEN}))
    monkeypatch.setattr(RoutingSnapshot, "require_current", lambda self: None)

    class Provider:
        def resolve_token(self, token):
            if token.startswith("user-"):
                return Identity(user_id=token[5:], username=token[5:])

    monkeypatch.setattr(middleware, "_provider", Provider())
    forwarded = []

    def resolve(owner):
        # The installed snapshot's answer: admitted owners only.
        if read(admitted, owner).state != "admitted":
            raise LookupError("owner has no admitted route")
        return owner_router.BoxRoute(Path("/run/box") / str(admitted.owner_machine(owner)),
                                     "secret")

    async def forward(scope, receive, send, identity, route, *, cold=True):
        forwarded.append((identity.user_id, route.socket.name))
        await JSONResponse({"owner": identity.user_id})(scope, receive, send)

    monkeypatch.setattr(owner_router, "forward", forward)
    app = middleware.AuthContextMiddleware(
        owner_router.OwnerRouter(JSONResponse({}), resolver=resolve), router=True)

    def post(token, path="/mcp"):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="https://tinyassets.io") as client:
                return await client.post(path, headers={"authorization": "Bearer " + token},
                                         json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        return asyncio.run(run())

    return post, forwarded


@pytest.mark.verifies('owner-daemon-in-box:C5')
def test_a_first_sign_in_reaches_its_own_new_box(door, admitted):  # noqa: F811
    post, forwarded = door
    alice = admitted.owner_machine("alice")
    response = post("user-user_01CORECANARY")
    assert response.status_code == 200, response.text
    uid = admitted.owner_machine("user_01CORECANARY")
    assert uid is not None and uid != alice
    assert forwarded == [("user_01CORECANARY", str(uid))]
    # Idempotent: the second request routes to the same box, spends no label.
    assert post("user-user_01CORECANARY").status_code == 200
    assert admitted.owner_machine("user_01CORECANARY") == uid
    assert forwarded[-1] == ("user_01CORECANARY", str(uid))


@pytest.mark.verifies('owner-daemon-in-box:C5')
def test_the_canary_service_bearer_routes_to_the_canary_box(door, admitted):  # noqa: F811
    """`mcp_public_canary.py --assert-handles` got 404 after cutover. The
    service principal is the owner ``canary``; its box applies the probe
    allowlist (auth/middleware.py, trusted CANARY branch)."""
    post, forwarded = door
    response = post(SERVICE_TOKEN)
    assert response.status_code == 200, response.text
    uid = admitted.owner_machine("canary")
    assert uid is not None
    assert forwarded == [("canary", str(uid))]


@pytest.mark.verifies('owner-daemon-in-box:C6')
def test_erased_and_recovering_owners_are_never_admitted_by_a_request(door, admitted):  # noqa: F811
    from tinyassets.broker.admission import digest, mutate

    post, forwarded = door
    admitted.resolve("dave", allocate=True)
    with sqlite3.connect(admitted.path) as db:
        mutate(db, "dave", "erase")
        # A pre-cutover tombstone: erased by digest, no identity row at all.
        mutate(db, None, "erase", erased_digest=digest("erin"))
    for owner in ("dave", "erin"):
        response = post("user-" + owner)
        assert response.status_code == 404, response.text
    assert admitted.owner_machine("erin") is None  # no label spent on a tombstone
    admitted.work_ready = False  # C3: recovery keeps admission closed
    assert post("user-frank").status_code == 404
    assert admitted.owner_machine("frank") is None
    assert forwarded == []


@pytest.mark.verifies('owner-daemon-in-box:C6')
def test_admission_creates_or_repairs_identity_admission_and_box(admitted, tmp_path):  # noqa: F811
    """Provisioning idempotence: every rerun converges on one label, one
    admitted record and one box reservation with its volume binding."""
    from tinyassets.broker.admission import read
    from tinyassets.broker.box_admission import provision

    with sqlite3.connect(admitted.path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS box_records (principal TEXT PRIMARY KEY, "
                   "machine INTEGER, project_id INTEGER, bhard_bytes INTEGER)")
    routing = projection(admitted.path)
    for _ in range(3):
        identity = drive(admitted.admit("user_01CORECANARY", routing=routing))
        provision(admitted, tmp_path, "user_01CORECANARY")
    assert read(admitted, "user_01CORECANARY").state == "admitted"
    with sqlite3.connect(admitted.path) as db:
        assert db.execute("SELECT machine_id FROM owner_identities WHERE principal=?",
                          ("user_01CORECANARY",)).fetchall() == [(identity.uid,)]
        assert db.execute("SELECT machine, project_id FROM box_records WHERE principal=?",
                          ("user_01CORECANARY",)).fetchall() == [(identity.uid, identity.uid)]
        assert db.execute("SELECT uid FROM owner_volumes WHERE principal=?",
                          ("user_01CORECANARY",)).fetchall() == [(identity.uid,)]
