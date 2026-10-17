"""Restart durability and cross-process single redemption with synthetic PKCE."""
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.test_hosted_model_auth import CHALLENGE, VERIFIER, begin, pending, seed_home, take
from tinyassets.account_deletion import _delete_satellite_rows
from tinyassets.broker import acquisition
from tinyassets.onboarding import hosted_model_auth as auth
from tinyassets.storage import data_dir


@pytest.fixture(autouse=True)
def isolated_flow_store(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    seed_home("user-a", "home-a")
    seed_home("user-b", "home-b")


def child_take(handle):
    program = """
import json,sys
from tinyassets.onboarding import hosted_model_auth as auth
data=json.loads(sys.stdin.read())
from pathlib import Path
from types import SimpleNamespace
from tinyassets.broker import supervisor
live=SimpleNamespace(socket_path=Path(data["socket"]), fence=lambda: tuple(data["fence"]),
                     verify_broker=lambda _: None)
supervisor.get_supervisor=lambda _: live
try:
    flow=auth.take_flow(handle=data['handle'], owner='user-a', universe_id='home-a',
                        verifier=data['verifier'])
    print(json.dumps({'owner':flow.owner, 'home':flow.universe_id}))
except auth.HostedAuthError as error:
    print(json.dumps({'error':error.code}))
"""
    from tinyassets.broker.supervisor import get_supervisor

    live = get_supervisor(data_dir())
    result = subprocess.run([sys.executable, "-c", program],
                            input=json.dumps({"handle": handle, "verifier": VERIFIER,
                                              "socket": str(live.socket_path),
                                              "fence": live.fence()}),
                            text=True, capture_output=True, timeout=30, check=True)
    return json.loads(result.stdout)


def test_new_process_redeems_pre_restart_binding_once():
    handle = begin()["flow"]
    assert child_take(handle) == {"owner": "user-a", "home": "home-a"}
    with pytest.raises(auth.HostedAuthError, match="unknown_model_connection"):
        take(handle)


def test_competing_processes_have_one_winner():
    handle = begin()["flow"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(child_take, [handle] * 4))
    assert sum(item.get("owner") == "user-a" for item in outcomes) == 1
    assert sum(item.get("error") == "unknown_model_connection" for item in outcomes) == 3


def test_stored_rows_exclude_raw_handle_verifier_code_and_key():
    import sqlite3

    handle = begin()["flow"]
    path = data_dir() / ".hosted-model-auth.db"
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(hosted_model_flows)")}
        doc = conn.execute("SELECT * FROM hosted_model_flows").fetchone()
    assert columns == {"handle_digest", "owner_user_id", "bound_home_id", "preset_id",
                       "preset_digest", "challenge", "callback_origin", "created_at", "expires_at",
                       "request_ref"}
    assert handle not in doc and VERIFIER not in doc
    assert CHALLENGE in doc
    assert "synthetic-authorization-code" not in doc
    assert "synthetic-api-key" not in doc


def test_pending_erasure_is_owner_scoped_even_after_home_changes():
    first = begin()["flow"]
    seed_home("user-a", "former-home-a")
    former = begin(universe_id="former-home-a")["flow"]
    second = begin(owner="user-b", universe_id="home-b")["flow"]
    counts = {}
    _delete_satellite_rows(data_dir() / ".hosted-model-auth.db", principal="user-a",
                           home="new-home-a", counts=counts, label="hosted")
    assert counts == {"hosted:hosted_model_flows": 2}
    assert not pending(first)
    with pytest.raises(auth.HostedAuthError, match="unknown_model_connection"):
        take(former, universe_id="former-home-a")
    assert take(second, owner="user-b", universe_id="home-b").owner == "user-b"


def test_clock_rollback_rows_are_swept_without_blocking_capacity(monkeypatch):
    first = begin()["flow"]
    created = pending(first)["created_at"]
    monkeypatch.setattr(auth.pkce.time, "time", lambda: created - 1)
    monkeypatch.setattr(auth, "MAX_PENDING", 1)
    second = begin()["flow"]
    assert not pending(first)
    assert pending(second)["expires_at"] == created - 1 + auth.FLOW_TTL_SECONDS


@pytest.mark.parametrize("change", ["future", "overlong", "expired"])
def test_invalid_stored_lifetime_cannot_be_taken(change):
    handle = begin()["flow"]
    now = auth.pkce.time.time()
    created, expiry = {"future": (now + 10, now + 100),
                       "overlong": (now, now + 601),
                       "expired": (now - 601, now - 1)}[change]
    with sqlite3.connect(data_dir() / ".hosted-model-auth.db") as conn:
        conn.execute("UPDATE hosted_model_flows SET created_at=?, expires_at=?", (created, expiry))
    with pytest.raises(auth.HostedAuthError):
        take(handle)


def test_lock_failure_is_loud_and_does_not_consume(monkeypatch):
    handle = begin()["flow"]
    original_connect = sqlite3.connect
    path = data_dir() / ".hosted-model-auth.db"
    locked = original_connect(path, isolation_level=None)
    locked.execute("BEGIN IMMEDIATE")

    def short_connect(*args, **kwargs):
        kwargs["timeout"] = 0.01
        return original_connect(*args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(auth.sqlite3, "connect", short_connect)
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                take(handle)
    finally:
        locked.rollback()
        locked.close()
    assert take(handle).owner == "user-a"


def test_exchange_after_take_uses_epoch_expiry_not_monotonic(monkeypatch):
    import asyncio

    flow = take(begin()["flow"])
    monkeypatch.setattr(auth.pkce.time, "time", lambda: flow.expires_at)

    def forbidden():
        raise AssertionError("expired flow cannot contact provider")

    with pytest.raises(auth.HostedAuthError, match="model_connection_expired"):
        asyncio.run(acquisition.exchange_key(flow=flow, code="synthetic-code", verifier=VERIFIER,
                                     client_factory=forbidden))
