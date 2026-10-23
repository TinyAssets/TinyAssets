"""Shared platform recovery and the retained cutover/provider boundaries."""

import copy
import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.admission import Plans, drive, projection
from tests.test_owner_cutover_census import source as source
from tinyassets import restore_recovery as recovery
from tinyassets.billing import stripe_adapter as stripe
from tinyassets.broker import admission
from tinyassets.broker.deletion_jobs import STEPS, DeletionJobs, new_job
from tinyassets.broker.owner_identities import OwnerIdentities
from tinyassets.broker.routing import BrokerLifecycle


def seed(root):
    from tinyassets.broker.admission import mutate, schema

    (root / ".broker/state").mkdir(parents=True, mode=0o700)
    (root / ".layout.json").write_text(
        json.dumps({"layout": 2, "box_layout": "owner-box", "state": "stable"})
    )
    (root / ".layout.lock").touch()
    with sqlite3.connect(root / ".broker/state/owner-identities.db") as db:
        db.execute("CREATE TABLE owner_identities (principal TEXT, machine_id INT)")
        db.execute("INSERT INTO owner_identities VALUES ('alice', 300001)")
        db.execute("CREATE TABLE box_records (principal TEXT, machine INT)")
        db.execute("INSERT INTO box_records VALUES ('alice', 300001)")
        db.execute("CREATE TABLE owner_box_secrets (principal TEXT, secret TEXT)")
        db.execute("INSERT INTO owner_box_secrets VALUES ('alice', 'old-box-secret')")
        schema(db)
        mutate(db, "alice", "admit")
    (root / ".broker/state/owner-identities.db").chmod(0o600)
    for relative, tables in recovery.RESET_TABLES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            for table in tables:
                db.execute(f'CREATE TABLE "{table}" (value TEXT)')
                db.execute(f'INSERT INTO "{table}" VALUES (?)', ("old-authority",))
    from tinyassets.cutover_control import close_admission as initialize

    initialize(root)


def run_recovery(root, *, backup_time, now=None, **adapters):
    from types import SimpleNamespace

    recovery.prepare_platform(root, backup_time=backup_time, now=now)
    identities = SimpleNamespace(
        _connect=lambda **kwargs: sqlite3.connect(root / ".broker/state/owner-identities.db"))
    server = SimpleNamespace(_data_root=root, _owner_identities=identities)
    report = drive(recovery.platform_recovery(server, backup_time=backup_time, now=now, **adapters))
    recovery.finish_platform(root)
    return report


def fingerprint(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_platform_recovery_closes_service_owned_marker_under_sticky_root(tmp_path, monkeypatch):
    import os

    seed(tmp_path)
    tmp_path.chmod(0o1775)
    from tinyassets.router_account_deletion import revocation_path, revoke_local_tokens

    revoke_local_tokens(tmp_path, "alice", now=1)
    os.chown(revocation_path(tmp_path), 1001, 1001)
    marker = tmp_path / ".layout.json"
    os.chown(marker, 1001, 1001)
    marker.chmod(0o644)
    async def unavailable(*args, **kwargs):
        raise recovery.RecoveryUnavailable("deletion transport unavailable")
    monkeypatch.setattr(recovery, "resume_deletion_jobs", unavailable)
    with pytest.raises(recovery.RecoveryUnavailable, match="deletion transport unavailable"):
        run_recovery(tmp_path, backup_time=1, now=2000000000)
    assert json.loads(marker.read_text())["state"] == "recovering"
    assert marker.stat().st_uid == 0
    assert not list(tmp_path.glob(".platform-layout-*"))
    with sqlite3.connect(tmp_path / ".api-keys.sqlite3") as db:
        assert db.execute("SELECT * FROM api_keys").fetchall() == []


@pytest.fixture
def platform(tmp_path, monkeypatch):
    staged = tmp_path / "staged"
    seed(staged)
    # File ownership is asserted by the root shell proof; no real providers here.
    monkeypatch.setattr("os.chown", lambda *args: None)
    return staged


def providers(monkeypatch, events):
    async def resume(server, *, backup_time):
        staged = server._data_root
        assert json.loads((staged / ".layout.json").read_text())["state"] == "recovering"
        assert backup_time == 1
        events.append("deletion")
        return True

    monkeypatch.setattr(recovery, "resume_deletion_jobs", resume)

    def authority(staged, now):
        assert json.loads((staged / ".layout.json").read_text())["state"] == "recovering"
        events.append("billing")

    def identity(owner):
        assert owner == "alice"
        events.append("identity")
        return True

    def revoke(owner):
        assert owner == "alice"
        events.append("revoke")

    return {"authority": authority, "identity_exists": identity, "revoke_sessions": revoke}


def test_platform_recovery_resets_credentials_without_owner_access(platform, tmp_path, monkeypatch):
    from tinyassets.broker.ops import OpStore, new_op_id

    owners = tmp_path / "owner-volumes"
    owners.mkdir()
    (owners / "300001.img").write_bytes(b"opaque current owner image")
    before = fingerprint(owners)
    original_open, original_stat, original_iterdir = Path.open, Path.stat, Path.iterdir

    def no_owner(path):
        assert not path.is_relative_to(owners), "shared recovery accessed owner volume"

    def guarded_open(path, *args, **kwargs):
        no_owner(path)
        return original_open(path, *args, **kwargs)

    def guarded_stat(path, *args, **kwargs):
        no_owner(path)
        return original_stat(path, *args, **kwargs)

    def guarded_iterdir(path):
        no_owner(path)
        return original_iterdir(path)

    events = []
    adapters = providers(monkeypatch, events)
    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", guarded_open)
        guard.setattr(Path, "stat", guarded_stat)
        guard.setattr(Path, "iterdir", guarded_iterdir)
        report = run_recovery(platform, backup_time=1, now=2000000000, **adapters)
    assert fingerprint(owners) == before
    assert events == ["deletion", "billing", "identity", "revoke"]
    from tinyassets.router_account_deletion import local_revocation

    assert local_revocation("alice", platform) == (2000000000, 0)
    assert "credential_roots_reset" not in report
    assert "broker_replay_cutoff_advanced" not in report
    preparation = json.loads((platform / ".layout.json").read_text())["preparation"]
    assert preparation["credential_roots_reset"]
    assert preparation["broker_replay_cutoff_advanced"]
    assert report["owners_reconciled"] == 1
    for relative, tables in recovery.RESET_TABLES.items():
        with sqlite3.connect(platform / relative) as db:
            for table in tables:
                assert db.execute(f'SELECT * FROM "{table}"').fetchall() == []
    with sqlite3.connect(platform / ".broker/state/owner-identities.db") as db:
        assert db.execute("SELECT secret FROM owner_box_secrets").fetchone()[0] != "old-box-secret"
    assert json.loads((platform / ".router-credentials.json").read_text())
    ops = OpStore(platform / ".broker/state/ops.db", clock=lambda: 2000000001)
    assert ops.admit("outbound", new_op_id(lambda: 1999999999), "digest").kind == "expired"
    assert ops.admit("outbound", new_op_id(lambda: 2000000301), "digest").kind == "new"
    assert not (platform / ".restore-epoch").exists()
    assert not (platform / ".broker/restore-spend-until").exists()
    assert json.loads((platform / ".layout.json").read_text())["state"] == "stable"


def test_platform_recovery_waits_for_deletion_jobs(platform, monkeypatch):
    async def unavailable(*args, **kwargs):
        raise recovery.RecoveryUnavailable("deletion transport unavailable")
    monkeypatch.setattr(recovery, "resume_deletion_jobs", unavailable)
    with pytest.raises(recovery.RecoveryUnavailable, match="deletion transport unavailable"):
        run_recovery(platform, backup_time=1, now=2000000000)
    assert json.loads((platform / ".layout.json").read_text())["state"] == "recovering"
    assert json.loads((platform / ".platform-recovery.json").read_text()) == {}
    from tinyassets.boxes.layout import hold
    from tinyassets.router_traffic import admits
    with hold(platform):
        assert not admits(platform, "alice")
        assert not admits(platform, "canary")
    with sqlite3.connect(platform / ".api-keys.sqlite3") as db:
        assert db.execute("SELECT * FROM api_keys").fetchall() == []


@pytest.mark.parametrize(
    "failed", ["deletion", "billing", "identity", "revoke"]
)
def test_platform_recovery_authority_failure_never_reopens(platform, monkeypatch, failed):
    events = []
    adapters = providers(monkeypatch, events)

    def unavailable(*args, **kwargs):
        raise recovery.RecoveryUnavailable(failed)

    if failed == "deletion":
        monkeypatch.setattr(recovery, "resume_deletion_jobs", unavailable)
    elif failed == "billing":
        adapters["authority"] = unavailable
    elif failed == "identity":
        adapters["identity_exists"] = unavailable
    else:
        adapters["revoke_sessions"] = unavailable
    with pytest.raises(recovery.RecoveryUnavailable, match=failed):
        run_recovery(platform, backup_time=1, now=2000000000, **adapters)
    assert json.loads((platform / ".layout.json").read_text())["state"] == "recovering"
    assert json.loads((platform / ".platform-recovery.json").read_text()) == {}


@pytest.mark.parametrize("identity", [False, None])
def test_platform_recovery_missing_identity_denies(platform, monkeypatch, identity):
    adapters = providers(monkeypatch, [])
    adapters["identity_exists"] = lambda owner: identity
    with pytest.raises(recovery.RecoveryUnavailable, match="identity provider"):
        run_recovery(platform, backup_time=1, now=2000000000, **adapters)
    assert json.loads((platform / ".layout.json").read_text())["state"] == "recovering"


def test_platform_recovery_requires_configured_stripe(platform, monkeypatch):
    from tinyassets.billing.stripe_adapter import BillingUnavailable

    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    with pytest.raises(BillingUnavailable):
        drive(recovery.reconcile_authority(platform, 2000000000,
            routing=projection(
            platform / ".broker/state/owner-identities.db")))


def test_future_dated_consumed_operation_cannot_replay_after_restore(platform):
    from tinyassets.broker.ops import MAX_FUTURE_SKEW_S, OpStore, new_op_id

    start = 2000000000
    op_id = new_op_id(lambda: start + 240)
    live_ops = OpStore(platform / ".broker/state/ops.db", clock=lambda: start)
    assert live_ops.admit("outbound", op_id, "digest").kind == "new"
    live_ops.mark_may_have_sent("outbound", op_id)
    live_ops.finish("outbound", op_id, "completed")
    recovery.prepare_platform(platform, backup_time=start, now=start + 60)
    restored = OpStore(platform / ".broker/state/ops.db", clock=lambda: start + 60)
    assert restored.admit("outbound", op_id, "digest").kind == "expired"
    boundary = new_op_id(lambda: start + 60 + MAX_FUTURE_SKEW_S)
    assert restored.admit("outbound", boundary, "digest").kind == "expired"
    unseen = new_op_id(lambda: start + 30)
    assert restored.admit("outbound", unseen, "digest").kind == "expired"
    assert not (platform / ".broker/ops.db").exists()


@pytest.mark.parametrize("failure", [None, "list", "revoke"])
def test_workos_revokes_all_sessions_through_paginated_provider_api(monkeypatch, failure):
    import io
    import urllib.error

    from tinyassets.router_account_deletion import AccountDeletionError, revoke_workos_sessions

    monkeypatch.setenv("WORKOS_API_KEY", "synthetic")
    revoked = []

    def request(req, timeout):
        assert timeout == 20
        if (
            failure == "list"
            and req.get_method() == "GET"
            or failure == "revoke"
            and req.get_method() == "POST"
        ):
            raise urllib.error.URLError("unavailable")
        if req.get_method() == "POST":
            revoked.append(json.loads(req.data)["session_id"])
            response = io.BytesIO(b"")
            response.status = 204
        else:
            assert "/users/user_fixture/sessions?" in req.full_url
            last = "after=session_first" in req.full_url
            response = io.BytesIO(
                json.dumps(
                    {
                        "data": [{"id": "session_second" if last else "session_first"}],
                        "list_metadata": {"after": None if last else "session_first"},
                    }
                ).encode()
            )
            response.status = 200
        return response

    if failure:
        with pytest.raises(AccountDeletionError):
            revoke_workos_sessions("user_fixture", request=request)
    else:
        revoke_workos_sessions("user_fixture", request=request)
        assert revoked == ["session_first", "session_second"]


def test_workos_seam_distinguishes_absent_from_unavailable(monkeypatch):
    import urllib.error

    from tinyassets.router_account_deletion import AccountDeletionError, workos_user_exists

    monkeypatch.setenv("WORKOS_API_KEY", "synthetic")

    def missing(req, **kwargs):
        raise urllib.error.HTTPError(req.full_url, 404, "missing", {}, None)

    def unavailable(req, **kwargs):
        raise TimeoutError()

    assert not workos_user_exists("user_fixture", request=missing)
    with pytest.raises(AccountDeletionError):
        workos_user_exists("user_fixture", request=unavailable)


def test_router_rpc_cannot_open_admission_even_for_root(tmp_path, monkeypatch):
    import asyncio

    from tinyassets import router_credentials, rpc_frames
    from tinyassets.cutover_control import close_admission
    from tinyassets.router_traffic import admits

    close_admission(tmp_path)
    caller = [1002]
    monkeypatch.setattr("tinyassets.broker.server.peer_uid", lambda sock: caller[0])

    async def run():
        async with router_credentials.serving(tmp_path):
            for uid in (1002, 0):
                caller[0] = uid
                reader, writer = await asyncio.open_unix_connection(
                    str(router_credentials.credential_socket(tmp_path)))
                writer.write(rpc_frames.control(rpc_frames.CONNECTION, {"op": "OPEN_TRAFFIC"}))
                await writer.drain()
                try:
                    answer = await rpc_frames.read_frame(reader)
                except ConnectionResetError:
                    assert uid == 0
                    answer = None
                assert answer is None or answer.control() == {"op": "ROUTER_CREDENTIAL_REFUSED"}
                assert not admits(tmp_path, "alice")
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionResetError:
                    assert uid == 0

    asyncio.run(run())


def test_router_admission_is_positive_and_has_no_restore_predicates(tmp_path, monkeypatch):
    from tinyassets.cutover_control import close_admission
    from tinyassets.router_traffic import NAME, admits

    monkeypatch.setenv("TINYASSETS_CANARY_USER_ID", "probe-owner")
    assert not admits(tmp_path, "alice")
    assert not admits(tmp_path, "canary")
    close_admission(tmp_path)
    assert admits(tmp_path, "canary")
    assert admits(tmp_path, "probe-owner")
    assert not admits(tmp_path, "alice")
    assert not admits(tmp_path, None)
    (tmp_path / NAME).write_text(json.dumps({"ordinary": True}))
    assert admits(tmp_path, "alice")
    (tmp_path / NAME).write_text("unreadable")
    assert not admits(tmp_path, "alice")
    for damaged in ({"ordinary": 1}, {"ordinary": 0}, {}, None, {"ordinary": "true"}):
        (tmp_path / NAME).write_text(json.dumps(damaged))
        assert not admits(tmp_path, "alice")
        assert not admits(tmp_path, "canary")


@pytest.mark.parametrize(
    "path,method,status",
    [
        ("/app/token", "POST", 503),
        ("/app/owner-sign-in", "GET", 503),
        ("/app/billing/webhook", "POST", 503),
        ("/authorize", "GET", 503),
        ("/token", "POST", 503),
        ("/app", "POST", 503),
        ("/app", "GET", 200),
        ("/app/m/build/main.js", "GET", 200),
        ("/app/canary-sign-in", "POST", 200),
    ],
)
def test_closed_admission_blocks_anonymous_writers(tmp_path, monkeypatch, path, method, status):
    import asyncio

    import httpx
    from starlette.responses import JSONResponse

    from tinyassets.cutover_control import close_admission as initialize
    from tinyassets.owner_router import OwnerRouter
    from tinyassets.router_traffic import NAME

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TINYASSETS_CANARY_USER_ID", raising=False)
    initialize(tmp_path)
    entered = []

    async def shared(scope, receive, send):
        entered.append(scope["path"])
        await JSONResponse({"ok": True})(scope, receive, send)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(OwnerRouter(shared)), base_url="http://test"
        ) as client:
            response = await client.request(method, path)
            assert response.status_code == status
            assert entered == ([path] if status == 200 else [])
            assert json.loads((tmp_path / NAME).read_text()) == {"ordinary": False}
            (tmp_path / NAME).write_text(json.dumps({"ordinary": True}))
            assert (await client.request(method, path)).status_code == 200

    asyncio.run(exercise())


def test_staged_repair_survives_sidecars_removed_by_repairing_their_database(tmp_path):
    # Real owner trees hold WAL databases (e.g. a pre-migrate backup) with
    # -wal/-shm on disk; repairing and closing one removes its sidecars.
    from tinyassets.cutover_sqlite import _repair_staged_databases

    live = tmp_path / "live.db"
    stage = tmp_path / "stage"
    stage.mkdir()
    keep = sqlite3.connect(live)
    keep.execute("PRAGMA journal_mode=WAL")
    keep.execute("PRAGMA wal_autocheckpoint=0")
    keep.execute("CREATE TABLE notes (body)")
    keep.execute("INSERT INTO notes VALUES ('kept')")
    keep.commit()
    for suffix in ("", "-wal", "-shm"):
        shutil.copyfile(f"{live}{suffix}", stage / f"memory.db.bak{suffix}")
    keep.close()
    _repair_staged_databases(stage)
    with sqlite3.connect(stage / "memory.db.bak") as db:
        assert db.execute("SELECT body FROM notes").fetchall() == [("kept",)]


@pytest.mark.parametrize("name", ["notes-wal", "ledger-shm", "ledger-journal"])
def test_staged_repair_inspects_a_sidecar_named_database_with_no_verified_base(tmp_path, name):
    # Only a verified database's own sidecars are skipped; a database merely
    # named like one still gets link refusal and the integrity verdict.
    from contextlib import closing

    from tests.test_cutover_index_integrity import damage_many_indexes
    from tinyassets.cutover_sqlite import _repair_staged_databases

    stage = tmp_path / "stage"
    stage.mkdir()
    # A base that merely exists is not a verified database.
    (stage / name[: name.rindex("-")]).write_text("not a database")
    with closing(sqlite3.connect(stage / name)) as db, db:
        db.execute("CREATE TABLE notes (body)")
    (stage / "alias").hardlink_to(stage / name)
    with pytest.raises(ValueError, match="private regular file"):
        _repair_staged_databases(stage)
    (stage / "alias").unlink()
    damage_many_indexes(stage / name, corrupt_table=True)
    with pytest.raises(ValueError, match="not index-only"):
        _repair_staged_databases(stage)


@pytest.mark.parametrize("source", [None, "missing_key", "empty", "malformed", "wrong_type"])
def test_missing_current_operational_credential_never_uses_environment(
    tmp_path,
    monkeypatch,
    source,
):
    from tinyassets.auth.wiki_canary import WIKI_CANARY_TOKEN_ENV, wiki_canary_token_matches
    from tinyassets.onboarding import canary_sign_in as door

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(WIKI_CANARY_TOKEN_ENV, "old" * 32)
    monkeypatch.setenv(door.KEY_ENV, "old" * 32)
    monkeypatch.setenv(door.USER_ENV, "user_fixture")
    if source is not None:
        values = {
            "missing_key": "{}",
            "empty": json.dumps({WIKI_CANARY_TOKEN_ENV: "", door.KEY_ENV: ""}),
            "malformed": "{",
            "wrong_type": json.dumps({WIKI_CANARY_TOKEN_ENV: 42, door.KEY_ENV: 42}),
        }
        (tmp_path / ".router-credentials.json").write_text(values[source])
    assert not wiki_canary_token_matches("old" * 32)
    assert door.configured() is None


def test_current_operational_credentials_replace_previous_environment(tmp_path, monkeypatch):
    from tinyassets.auth.wiki_canary import WIKI_CANARY_TOKEN_ENV, wiki_canary_token_matches
    from tinyassets.onboarding import canary_sign_in as door

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(WIKI_CANARY_TOKEN_ENV, "old" * 32)
    monkeypatch.setenv(door.KEY_ENV, "old" * 32)
    monkeypatch.setenv(door.USER_ENV, "user_fixture")
    current = "new" * 32
    (tmp_path / ".router-credentials.json").write_text(
        json.dumps({WIKI_CANARY_TOKEN_ENV: current, door.KEY_ENV: current})
    )
    assert wiki_canary_token_matches(current)
    assert not wiki_canary_token_matches("old" * 32)
    assert door.configured() == (current, "user_fixture")


def test_platform_recovery_uses_current_admission_not_erased_identity_history(
    platform, monkeypatch
):
    with sqlite3.connect(platform / ".broker/state/owner-identities.db") as db:
        db.execute("INSERT INTO owner_identities VALUES ('erased', 300002)")
        admission.mutate(db, "erased", "erase")
    calls = []
    adapters = providers(monkeypatch, calls)
    report = run_recovery(platform, backup_time=1, now=2000000000, **adapters)
    assert report["owners_reconciled"] == 1
    assert calls == ["deletion", "billing", "identity", "revoke"]
    from tinyassets.router_account_deletion import local_revocation

    assert local_revocation("erased", platform) == (2000000000, 1)
    with sqlite3.connect(platform / ".broker/state/owner-identities.db") as db:
        assert db.execute(
            "SELECT machine_id FROM owner_identities WHERE principal='erased'"
        ).fetchone() == (300002,)


def test_disaster_stale_key_session_hook_and_secret_all_fail(platform, monkeypatch):
    import hashlib

    from tinyassets.api_keys import ApiKeyStore
    from tinyassets.onboarding import owner_sessions
    from tinyassets.router_credentials import HookOwners

    staged = platform
    events = []
    adapters = providers(monkeypatch, events)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(staged))

    for relative in (".api-keys.sqlite3", ".hook-owners.sqlite3", ".runtime/owner-sessions.db"):
        (staged / relative).unlink()
    key = ApiKeyStore(staged).mint("alice", name="fixture", scopes=[], authority=None)["key"]
    HookOwners(staged).publish(hashlib.sha256(b"hook").hexdigest(), "alice")
    with owner_sessions.store() as db:
        db.execute(
            "INSERT INTO owner_sessions VALUES (?,?,?)",
            (owner_sessions.hashed("session"), json.dumps({"user_id": "alice"}), 3000000000),
        )
    with sqlite3.connect(staged / ".broker/state/owner-identities.db") as db:
        db.execute("UPDATE owner_box_secrets SET secret=?", ("o" * 43,))
    assert ApiKeyStore(staged).resolve(key) is not None
    assert HookOwners(staged).resolve("hook") == "alice"
    assert owner_sessions.lookup("session") is not None
    report = run_recovery(staged, backup_time=1, now=2000000000, **adapters)
    assert ApiKeyStore(staged).resolve(key) is None
    assert HookOwners(staged).resolve("hook") is None
    assert owner_sessions.lookup("session") is None
    with sqlite3.connect(staged / ".broker/state/owner-identities.db") as db:
        assert db.execute("SELECT secret FROM owner_box_secrets").fetchone()[0] != "o" * 43
    assert "credential_roots_reset" not in report and "broker_replay_cutoff_advanced" not in report
    assert report["jobs_resumed"] and report["billing_reconciled"]
    assert "billing" in events


def test_disaster_revokes_every_surviving_owner_and_fails_closed(platform, monkeypatch):
    from tinyassets.router_account_deletion import AccountDeletionError

    staged = platform
    events = []
    adapters = providers(monkeypatch, events)
    calls = []

    def revoke(owner):
        calls.append(owner)
        raise AccountDeletionError("provider unavailable")

    with pytest.raises(AccountDeletionError):
        run_recovery(
            staged, backup_time=1, now=2000000000, **{**adapters, "revoke_sessions": revoke}
        )
    assert calls == ["alice"]
    assert json.loads((staged / ".platform-recovery.json").read_text()) == {}
    report = run_recovery(
        staged, backup_time=1, now=2000000000, **{**adapters, "revoke_sessions": calls.append}
    )
    assert calls == ["alice", "alice"]
    assert report["owners_reconciled"] == 1


def test_disaster_drops_provider_custody_and_outbound_authority(platform, monkeypatch):
    staged = platform
    events = []
    adapters = providers(monkeypatch, events)
    (staged / ".broker/credential-custody.db").unlink()
    (staged / ".broker/outbound.db").unlink()
    with sqlite3.connect(staged / ".broker/credential-custody.db") as db:
        db.execute("CREATE TABLE llm_credential_custody (secret TEXT)")
        db.execute("INSERT INTO llm_credential_custody VALUES ('external-refresh-token')")
    with sqlite3.connect(staged / ".broker/outbound.db") as db:
        for table in (
            "outbound_connections",
            "outbound_connection_grants",
            "connection_capabilities",
            "browser_vault",
        ):
            db.execute(f"CREATE TABLE {table} (authority TEXT)")
            db.execute(f"INSERT INTO {table} VALUES ('external-authority')")
    run_recovery(staged, backup_time=1, now=2000000000, **adapters)
    with sqlite3.connect(staged / ".broker/credential-custody.db") as db:
        assert db.execute("SELECT * FROM llm_credential_custody").fetchall() == []
    with sqlite3.connect(staged / ".broker/outbound.db") as db:
        for table in (
            "outbound_connections",
            "outbound_connection_grants",
            "connection_capabilities",
            "browser_vault",
        ):
            assert db.execute(f"SELECT * FROM {table}").fetchall() == []


@pytest.mark.parametrize(
    "relative",
    [
        ".ordinary-admission.json",
        ".broker/state/ops.db",
        ".broker/plans.db",
        ".platform-recovery.json",
        ".router-credentials.json",
        ".layout.json",
    ],
)
def test_disaster_never_follows_staged_authority_links(
    platform,
    tmp_path,
    monkeypatch,
    relative,
):
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    path = platform / relative
    path.unlink(missing_ok=True)
    path.symlink_to(outside)
    adapters = providers(monkeypatch, [])
    with pytest.raises(ValueError, match="symlink"):
        run_recovery(platform, backup_time=1, now=2000000000, **adapters)
    assert outside.read_bytes() == b"untouched"
    assert path.is_symlink()


@pytest.mark.parametrize("many", [False, True])
def test_rollback_repairs_index_only_drift(source, tmp_path, many):
    import tarfile

    from tests.test_box_layout_migration import _archive
    from tests.test_cutover_index_integrity import corrupt_index, damage_many_indexes
    from tinyassets.cutover_restore import restore_old_layout
    from tinyassets.sqlite_integrity import check

    path = source / ".tinyassets.db"
    if many:
        damage_many_indexes(path, corrupt_table=False)
    else:
        corrupt_index(path)
    before = fingerprint(source)
    archive = tmp_path / "old-layout.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(source, arcname="_data")
    _archive(archive, archive.read_bytes())
    restored = restore_old_layout(archive, tmp_path / "restored")
    with sqlite3.connect(restored / ".tinyassets.db") as db:
        assert check(db) == []
        assert db.execute("SELECT count(*) FROM agent_turns").fetchone() == (4,)
    assert fingerprint(source) == before


@pytest.mark.parametrize("caller", ["seed", "rollback"])
def test_non_index_fault_after_110_damaged_indexes_refuses_before_erasure(source, tmp_path, caller):
    import tarfile

    from tests.test_box_layout_migration import _archive
    from tests.test_cutover_index_integrity import damage_many_indexes
    from tinyassets.cutover_deletions import LegacyFacts, seed_legacy
    from tinyassets.cutover_restore import restore_old_layout

    damage_many_indexes(source / ".tinyassets.db", corrupt_table=True)
    before = fingerprint(source)
    if caller == "seed":
        ledger_root = tmp_path / "ledger"
        ledger_root.mkdir()
        ledger = LegacyFacts(ledger_root)
        with pytest.raises(ValueError, match="not index-only"):
            seed_legacy(source, ledger, store_root=tmp_path)
        assert ledger.entries() == []
    else:
        archive = tmp_path / "old-layout.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            output.add(source, arcname="_data")
        _archive(archive, archive.read_bytes())
        with pytest.raises(ValueError, match="not index-only"):
            restore_old_layout(archive, tmp_path / "restored")
        assert fingerprint(source) == before
    assert fingerprint(source) == before


@pytest.mark.parametrize("layout", [None, {"split": "owner-split", "state": "stable"}])
def test_pre_cutover_pair_is_not_a_disaster_source(platform, layout):
    marker = platform / ".layout.json"
    if layout is None:
        marker.unlink()
    else:
        marker.write_text(json.dumps(layout))
    with pytest.raises(FileNotFoundError if layout is None else recovery.RecoveryUnavailable):
        run_recovery(platform, backup_time=1, now=2000000000)


class Remote:
    def __init__(self):
        self.rows = {}

    def save(self, job):
        self.rows[job["id"]] = copy.deepcopy(job)

    def records(self):
        return list(copy.deepcopy(self.rows).values())


@pytest.fixture
def startup(tmp_path, monkeypatch):
    from tests.support.broker_double import seed_owner_admission
    from tinyassets.broker.box_admission import provision

    for owner in ("alice", "bob"):
        seed_owner_admission(tmp_path, principal=owner, center=owner)
    identities = OwnerIdentities(tmp_path / ".broker/state/owner-identities.db",
                                 plans=Plans(tmp_path))
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT PRIMARY KEY, machine INT, "
                   "project_id INT, bhard_bytes INT)")
    for owner in ("alice", "bob"):
        provision(identities, tmp_path, owner)
    (tmp_path / ".layout.lock").touch()
    (tmp_path / ".layout.json").write_text(json.dumps(
        {"layout": 2, "box_layout": "owner-box", "state": "stable"}))
    remote, events = Remote(), []
    server = SimpleNamespace(_data_root=tmp_path, _owner_identities=identities)
    server.routing = BrokerLifecycle(server)
    server.deletion_jobs = DeletionJobs(server, records=remote)
    async def changed(owner):
        if admission.read(identities, owner).state == "erased":
            events.append("withdrawal")
    monkeypatch.setattr(server.routing, "changed", changed)
    from tests.support.admission import bind

    bind(identities.path, server.routing)
    for step in STEPS[1:]:
        def execute(self, job, step=step):
            assert not identities.work_ready
            assert admission.read(identities, job["owner"]).state == "erased"
            events.append(step)
            if step == "receipt":
                return {"deleted": True, "completed_at": 4}
        monkeypatch.setattr(DeletionJobs, "_" + step, execute)
    monkeypatch.setattr("os.chown", lambda *args: None)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "synthetic-key")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_secret")
    def get(path):
        assert not identities.work_ready
        events.append("stripe")
        return {"data": [], "has_more": False}
    monkeypatch.setattr(stripe, "_get", get)
    monkeypatch.setattr("tinyassets.router_account_deletion.workos_user_exists",
                        lambda owner: events.append("identity:" + owner) or True)
    monkeypatch.setattr("tinyassets.router_account_deletion.revoke_workos_sessions",
                        lambda owner: events.append("revoke:" + owner))
    recovery.prepare_platform(tmp_path, backup_time=1, now=2)
    identities.work_ready = False
    return SimpleNamespace(root=tmp_path, identities=identities, server=server,
                           remote=remote, events=events)


@pytest.mark.parametrize("complete", [False, True])
def test_jobs_newer_than_backup_finish_before_any_owner_is_admitted(startup, complete):
    from tinyassets.router_traffic import admits

    a = startup
    job = new_job("alice", a.identities.owner_machine("alice"), clock=lambda: 3)
    if complete:
        job["completed_steps"] = list(STEPS)
        job["results"]["receipt"] = {"deleted": True, "completed_at": 4}
    a.remote.save(job)
    # Neither the erased fact nor the newer job exists in the restored DB.
    assert a.server.deletion_jobs.records.records() == []
    assert admission.read(a.identities, "alice").state == "admitted"
    for owner in ("alice", "bob", "canary"):
        assert not admits(a.root, owner)
        with pytest.raises(admission.OwnerUnadmitted):
            admission.require_admitted(a.identities, owner)
    async def recover_jobs():
        await a.server.routing.tick()
        assert not a.identities.work_ready
        await a.server.routing.close()
        await a.server.routing.tick()
    drive(recover_jobs())
    assert admission.read(a.identities, "alice").state == "erased"
    assert a.server.deletion_jobs.records.load(job["id"])["completed_steps"] == list(STEPS)
    assert a.events[0] == "withdrawal"
    if complete:
        assert not set(STEPS[1:]) & set(a.events)
    else:
        assert a.events.index("receipt") < a.events.index("stripe")
    assert "identity:alice" not in a.events
    assert "identity:bob" in a.events
    assert not admits(a.root, "bob")
    assert not a.identities.work_ready
    recovery.finish_platform(a.root)
    assert admits(a.root, "bob")
    # Publish and volume work are already separately covered; prove this tick
    # only opens work after root consumed the matching completion receipt.
    a.server.routing.publish = lambda **kwargs: None
    a.server.routing.volumes.tick_owner = lambda owner: None
    drive(a.server.routing.tick())
    admission.require_admitted(a.identities, "bob")
    with pytest.raises(admission.OwnerErased):
        admission.require_admitted(a.identities, "alice")
    assert a.events.count("volume") == (0 if complete else 1)


@pytest.mark.parametrize("failure", ["stripe", "unconfigured", "offhost", "missing_job"])
def test_unavailable_authority_keeps_every_owner_closed(startup, monkeypatch, failure):
    a = startup
    def unavailable(*args, **kwargs):
        raise OSError("synthetic unavailable")
    if failure == "stripe":
        monkeypatch.setattr(stripe, "_get", unavailable)
    elif failure == "unconfigured":
        monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    elif failure == "offhost":
        monkeypatch.setattr(a.remote, "records", unavailable)
    else:
        job = new_job("alice", a.identities.owner_machine("alice"))
        a.server.deletion_jobs.records.save(job)
        a.remote.rows.clear()
    with pytest.raises((OSError, stripe.BillingUnavailable, recovery.RecoveryUnavailable)):
        drive(a.server.routing.tick())
    with pytest.raises(recovery.RecoveryUnavailable):
        recovery.finish_platform(a.root)
    assert not a.identities.work_ready
    for owner in ("alice", "bob", "canary"):
        with pytest.raises(admission.OwnerUnadmitted):
            admission.require_admitted(a.identities, owner)


@pytest.mark.parametrize("billing", [False, True])
def test_billing_follows_stripe_and_abuse_survives(startup, monkeypatch, billing):
    from tests.test_billing_boundary import _subscription_object

    a = startup
    # Seed migration-style reasons through the sole admission writer.
    for reason in ("billing", "abuse"):
        drive(admission.local_mutate(a.identities.path, "bob", "suspend",
                               reason=reason, suspended=True,
              routing=projection(a.identities.path)))
    obj = _subscription_object(owner_id="bob", status="past_due" if billing else "active")
    monkeypatch.setattr(stripe, "find_active_subscription",
                        lambda owner, **kwargs: obj["id"] if owner == "bob" else None)
    monkeypatch.setattr(stripe, "_get", lambda path: obj)
    drive(a.server.routing.tick())
    assert admission.read(a.identities, "bob").reasons == (
        frozenset({"abuse", "billing"}) if billing else frozenset({"abuse"}))
    recovery.finish_platform(a.root)
    a.server.routing.publish = lambda **kwargs: None
    a.server.routing.volumes.tick_owner = lambda owner: None
    drive(a.server.routing.tick())
    admission.require_admitted(a.identities, "alice")
    with pytest.raises(admission.OwnerSuspended):
        admission.require_admitted(a.identities, "bob")


def test_recovery_tick_retries_provider_failure_and_interrupted_receipt(startup, monkeypatch):
    a = startup
    available = stripe._get
    def unavailable(path):
        raise stripe.BillingUnavailable("synthetic unavailable")
    monkeypatch.setattr(stripe, "_get", unavailable)
    with pytest.raises(stripe.BillingUnavailable):
        drive(a.server.routing.tick())
    assert not a.identities.work_ready
    with pytest.raises(recovery.RecoveryUnavailable):
        recovery.finish_platform(a.root)
    (a.root / ".platform-recovery.json").write_text('{"backup_time":')
    monkeypatch.setattr(stripe, "_get", available)
    drive(a.server.routing.tick())
    assert not a.identities.work_ready
    assert recovery.finish_platform(a.root)["owners_reconciled"] == 2


def test_prebackup_completed_receipt_import_never_executes_or_allocates(startup):
    a = startup
    drive(admission.local_mutate(a.identities.path, "gone", "erase",
        routing=projection(a.identities.path)))
    job = new_job("gone", None, clock=lambda: 0)
    job.update(completed_steps=list(STEPS))
    job["results"]["receipt"] = {"deleted": True, "completed_at": .5}
    a.remote.save(job)
    a.events.clear()
    drive(a.server.routing.tick())
    assert a.server.deletion_jobs.records.load(job["id"]) == job
    assert admission.read(a.identities, "gone").state == "erased"
    assert a.identities.owner_machine("gone") is None
    assert not set(STEPS) & set(a.events)
    assert recovery.finish_platform(a.root)["owners_reconciled"] == 2


def test_broker_receipt_cannot_attest_missing_root_preparation(platform, monkeypatch):
    run_recovery(platform, backup_time=1, now=2000000000,
                 **providers(monkeypatch, []))
    layout_path = platform / ".layout.json"
    layout = json.loads(layout_path.read_text())
    del layout["preparation"]
    layout_path.write_text(json.dumps(layout))
    report_path = platform / ".platform-recovery.json"
    report = json.loads(report_path.read_text())
    report.update(credential_roots_reset=True, broker_replay_cutoff_advanced=True)
    report_path.write_text(json.dumps(report))
    with pytest.raises(recovery.RecoveryUnavailable):
        recovery.finish_platform(platform)


def test_recovery_tick_returns_while_per_owner_deletion_is_blocked(startup, monkeypatch):
    import asyncio
    import threading

    a = startup
    job = new_job("alice", a.identities.owner_machine("alice"), clock=lambda: 3)
    a.remote.save(job)
    entered, release = threading.Event(), threading.Event()
    def volume(job):
        entered.set()
        assert release.wait(10)
    monkeypatch.setattr(a.server.deletion_jobs, "_volume", volume)
    async def exercise():
        try:
            await asyncio.wait_for(a.server.routing.tick(), 2)
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.wait_for(a.server.routing.tick(), 2)
            assert not a.server.routing.tasks["alice"].done()
            assert not a.identities.work_ready
            with pytest.raises(recovery.RecoveryUnavailable):
                recovery.finish_platform(a.root)
        finally:
            release.set()
            await a.server.routing.close()
        await a.server.routing.tick()
        assert recovery.finish_platform(a.root)["jobs_resumed"]
    asyncio.run(exercise())


pytestmark = pytest.mark.verifies('owner-daemon-in-box:C3')


def test_platform_recovery_resets_native_custody(platform):
    target = platform / ".broker/accounts/synthetic/credentials/.subscription-state.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"fixture":"stale token"}')
    recovery.reset_credentials(platform, now=2000000000)
    assert not target.exists()
