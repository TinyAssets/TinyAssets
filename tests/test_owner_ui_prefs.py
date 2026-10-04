"""An owner's UI preferences: private, bounded, and gone with the account.

openspec/changes/owner-ui-prefs. The chat cloud's placement follows its owner to
every browser and app install, and to nobody else.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tinyassets.storage import owner_ui_prefs as prefs
from tinyassets.storage.owner_ui_prefs import PrefRefused

ALICE, BOB = "user_01ALICEPREFS0000000000000", "user_01BOBPREFS00000000000000"
CLOUD = {"v": 1, "mode": "open", "open": {"x": 40, "y": 30, "w": 600, "h": 420},
         "bubble": {"x": 900, "y": 640}}


def _write(base, owner=ALICE, viewport="wide", value=None, **kw):
    return prefs.write_pref(base, owner_user_id=owner, agent_id=kw.get("agent", "main"),
                            viewport=viewport, key=kw.get("key", "chat_cloud"),
                            value=CLOUD if value is None else value)


def _read(base, owner=ALICE, viewport="wide"):
    return prefs.read_prefs(base, owner_user_id=owner, agent_id="main", viewport=viewport)


# --- the store ----------------------------------------------------------------


def test_a_placement_round_trips_per_viewport_class(tmp_path):
    _write(tmp_path)
    _write(tmp_path, viewport="phone", value={**CLOUD, "mode": "bubble"})

    assert _read(tmp_path) == {"chat_cloud": CLOUD}
    assert _read(tmp_path, viewport="phone")["chat_cloud"]["mode"] == "bubble"


def test_nothing_saved_reads_as_empty(tmp_path):
    assert _read(tmp_path) == {}


def test_one_owner_never_sees_or_changes_anothers(tmp_path):
    _write(tmp_path, owner=ALICE)
    _write(tmp_path, owner=BOB, value={**CLOUD, "mode": "bubble"})

    assert _read(tmp_path, owner=ALICE)["chat_cloud"]["mode"] == "open"
    assert _read(tmp_path, owner=BOB)["chat_cloud"]["mode"] == "bubble"


@pytest.mark.parametrize(("kw", "value"), [
    ({"key": "theme"}, None),
    ({"agent": "scout"}, None),
    ({}, {**CLOUD, "v": 2}),
    ({}, {**CLOUD, "mode": "huge"}),
    ({}, {**CLOUD, "open": {"x": float("inf"), "y": 0, "w": 9, "h": 9}}),
    ({}, {**CLOUD, "bubble": {"x": True, "y": 0}}),
    ({}, {**CLOUD, "bubble": {"x": 10 ** 400, "y": 0}}),      # float() would overflow
    ({}, {**CLOUD, "open": {"x": 0, "y": 0, "w": 10 ** 7, "h": 9}}),
    ({}, "not an object"),
])
def test_a_refused_write_leaves_the_stored_value_alone(tmp_path, kw, value):
    _write(tmp_path)

    with pytest.raises(PrefRefused):
        _write(tmp_path, value=value, **kw)

    assert _read(tmp_path) == {"chat_cloud": CLOUD}


def test_an_unknown_viewport_is_refused(tmp_path):
    with pytest.raises(PrefRefused):
        _write(tmp_path, viewport="tablet")


def test_extra_fields_are_not_stored(tmp_path):
    """Only the shape is kept, so the record cannot carry anything else."""
    _write(tmp_path, value={**CLOUD, "notes": "x" * 1000, "open": {**CLOUD["open"], "z": 1}})

    assert _read(tmp_path) == {"chat_cloud": CLOUD}


# --- account deletion -----------------------------------------------------------


def test_deleting_an_account_takes_its_preferences_and_no_one_elses(tmp_path):
    from tests.test_account_deletion import HOME_A, HOME_B, A, B, _seed_user
    from tinyassets.account_deletion import delete_account

    _seed_user(tmp_path, A, HOME_A)
    _seed_user(tmp_path, B, HOME_B)
    _write(tmp_path, owner=A)
    _write(tmp_path, owner=B)

    receipt = delete_account(tmp_path, founder_sub=A, cancel_billing=lambda home: "cancelled",
                             delete_identity=lambda sub: "deleted")

    assert receipt["unfinished_phases"] == []
    assert _read(tmp_path, owner=A) == {}
    assert _read(tmp_path, owner=B) == {"chat_cloud": CLOUD}


# --- the routes -----------------------------------------------------------------


def _drive(method, query="", body=None, *, identity, monkeypatch, base: Path, origin=True):
    from starlette.requests import Request

    import tinyassets.api.helpers as helpers
    from tinyassets.auth.middleware import clear_identity, identity_context
    from tinyassets.onboarding import onboarding_routes

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setattr(helpers, "_base_path", lambda: str(base))
    route = next(r for r in onboarding_routes() if r.path == "/app/ui-prefs")
    raw = json.dumps(body).encode() if body is not None else b""
    headers = [(b"host", b"app.test"), (b"content-type", b"application/json")]
    if origin:
        headers.append((b"origin", b"https://app.test"))
    scope = {"type": "http", "method": method, "path": "/app/ui-prefs", "headers": headers,
             "query_string": query.encode()}

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    async def run():
        req = Request(scope, receive)
        if identity is None:
            clear_identity()
            return await route.endpoint(req)
        with identity_context(identity):
            return await route.endpoint(req)

    resp = asyncio.run(run())
    return resp.status_code, json.loads(resp.body or b"{}")


def _as(sub):
    from tests.test_onboarding_openai_device import _user

    return _user(sub)


def test_the_owner_writes_and_reads_their_own_through_the_app(tmp_path, monkeypatch):
    status, saved = _drive("POST", body={"agent": "main", "viewport": "wide",
                                         "key": "chat_cloud", "value": CLOUD},
                           identity=_as(ALICE), monkeypatch=monkeypatch, base=tmp_path)
    got = _drive("GET", "agent=main&viewport=wide", identity=_as(ALICE),
                 monkeypatch=monkeypatch, base=tmp_path)

    assert (status, saved) == (200, {"saved": True})
    assert got == (200, {"prefs": {"chat_cloud": CLOUD}})


def test_a_body_naming_another_owner_changes_only_the_caller(tmp_path, monkeypatch):
    _write(tmp_path, owner=BOB)
    status, _ = _drive("POST", body={"viewport": "wide", "key": "chat_cloud",
                                     "owner_user_id": BOB, "owner": BOB,
                                     "value": {**CLOUD, "mode": "bubble"}},
                       identity=_as(ALICE), monkeypatch=monkeypatch, base=tmp_path)

    assert status == 200
    assert _read(tmp_path, owner=BOB) == {"chat_cloud": CLOUD}
    assert _read(tmp_path, owner=ALICE)["chat_cloud"]["mode"] == "bubble"
    # And a read names no one but the caller either.
    _, got = _drive("GET", f"viewport=wide&owner={BOB}", identity=_as(ALICE),
                    monkeypatch=monkeypatch, base=tmp_path)
    assert got["prefs"]["chat_cloud"]["mode"] == "bubble"


def test_no_identity_is_refused_both_ways(tmp_path, monkeypatch):
    for method, body in (("GET", None), ("POST", {"viewport": "wide", "key": "chat_cloud",
                                                  "value": CLOUD})):
        status, doc = _drive(method, "viewport=wide", body, identity=None,
                             monkeypatch=monkeypatch, base=tmp_path)
        assert status == 401 and doc["error"] == "authentication_required"
    assert _read(tmp_path, owner=ALICE) == {}


def test_a_refused_value_is_a_400_with_a_reason(tmp_path, monkeypatch):
    status, doc = _drive("POST", body={"viewport": "wide", "key": "theme", "value": {}},
                         identity=_as(ALICE), monkeypatch=monkeypatch, base=tmp_path)

    assert status == 400 and doc["error"] == "ui_prefs_invalid" and "theme" in doc["detail"]


def test_a_cross_origin_write_is_refused(tmp_path, monkeypatch):
    status, doc = _drive("POST", body={"viewport": "wide", "key": "chat_cloud", "value": CLOUD},
                         identity=_as(ALICE), monkeypatch=monkeypatch, base=tmp_path,
                         origin=False)

    assert status == 403 and doc["error"] == "cross_origin_rejected"
    assert _read(tmp_path) == {}


def test_a_huge_coordinate_is_a_400_not_a_500(tmp_path, monkeypatch):
    body = {"viewport": "wide", "key": "chat_cloud",
            "value": {**CLOUD, "bubble": {"x": 10 ** 400, "y": 0}}}
    status, doc = _drive("POST", body=body, identity=_as(ALICE),
                         monkeypatch=monkeypatch, base=tmp_path)

    assert status == 400 and doc["error"] == "ui_prefs_invalid"
