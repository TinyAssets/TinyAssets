"""C13: installation/revocation never waits for an in-flight credential mint."""
import hashlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from tinyassets import router_credentials as credentials
from tinyassets.api_keys import ApiKeyStore
from tinyassets.broker.deletion_jobs import DeletionJobs
from tinyassets.broker.routing import BrokerLifecycle
from tinyassets.routing_snapshot import snapshot


@pytest.mark.parametrize("kind", ["key", "hook", "session"])
@pytest.mark.parametrize("order", ["before_write", "after_write", "after_recheck"])
def test_no_credential_survives_withdrawal_and_step_six(tmp_path, monkeypatch, kind, order):
    from tinyassets.onboarding import session_store

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    projection = snapshot(tmp_path)
    projection.install(projection.instance, 1, {hashlib.sha256(b"alice").hexdigest(): {
        "state": "admitted", "socket": "/synthetic/app.sock", "secret": "s" * 43}})
    entered, release = threading.Event(), threading.Event()
    if kind == "key":
        target, name = ApiKeyStore, "mint"
        def mint():
            return credentials.dispatch(tmp_path, "alice", {
                "operation": "key_mint", "name": "race", "request_ref": "race"})
    elif kind == "hook":
        target, name = credentials.HookOwners, "publish"
        def mint():
            return credentials.dispatch(tmp_path, "alice", {"operation": "hook_mint"})
    else:
        target, name = session_store, "_write_record"
        def mint():
            return session_store.mint("synthetic-refresh", owner="alice")
    original = getattr(target, name)
    def writing(*args, **kwargs):
        if order == "before_write":
            entered.set()
            assert release.wait(10)
        result = original(*args, **kwargs)
        if order == "after_write":
            entered.set()
            assert release.wait(10)
        return result
    monkeypatch.setattr(target, name, writing)
    lifecycle = object.__new__(BrokerLifecycle)
    lifecycle._router = lambda doc: credentials.routing_dispatch(tmp_path, doc)
    jobs = object.__new__(DeletionJobs)
    jobs.server = SimpleNamespace(_data_root=tmp_path, routing=lifecycle)
    def revoke(root, owner):
        credentials.revoke_account(root, owner)
        return {"op": "ROUTER_CREDENTIAL_IS", "result": {"revoked": True}}
    monkeypatch.setattr(credentials, "revoke_request", revoke)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(mint)
        try:
            if order == "after_recheck":
                pending.result(5)
            else:
                assert entered.wait(5)
            receipt = {"instance": projection.instance, "revision": 2}
            projection.install(projection.instance, 2, {})
            jobs._credentials({"owner": "alice", "results": {"withdrawal": receipt}})
        finally:
            release.set()
        if order != "after_recheck":
            with pytest.raises((LookupError, PermissionError)):
                pending.result(5)
    assert all(row["revoked_at"] is not None for row in ApiKeyStore(tmp_path).list(
        "alice", authority=None)["keys"])
    hooks = credentials.HookOwners(tmp_path)
    if hooks.path.exists():
        with sqlite3.connect(hooks.path) as db:
            assert db.execute("SELECT * FROM hook_owners WHERE owner='alice'").fetchall() == []
    assert not list(session_store.store_dir().glob("*.json"))


def test_step_six_denies_until_the_withdrawal_revision_is_installed(tmp_path, monkeypatch):
    projection = snapshot(tmp_path)
    projection.install(projection.instance, 1, {})
    lifecycle = object.__new__(BrokerLifecycle)
    lifecycle._router = lambda doc: credentials.routing_dispatch(tmp_path, doc)
    jobs = object.__new__(DeletionJobs)
    jobs.server = SimpleNamespace(_data_root=tmp_path, routing=lifecycle)
    calls = []
    monkeypatch.setattr(credentials, "revoke_request", lambda *args: calls.append(args))
    with pytest.raises(RuntimeError, match="withdrawal installation"):
        jobs._credentials({"owner": "alice", "results": {
            "withdrawal": {"instance": projection.instance, "revision": 2}}})
    assert calls == []
