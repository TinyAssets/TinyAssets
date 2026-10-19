"""Shared platform recovery and the retained cutover/provider boundaries."""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from tests.test_owner_cutover_census import source as source
from tinyassets import restore_recovery as recovery


def seed(root):
    (root / ".broker/state").mkdir(parents=True)
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
    for relative, tables in recovery.RESET_TABLES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            for table in tables:
                db.execute(f'CREATE TABLE "{table}" (value TEXT)')
                db.execute(f'INSERT INTO "{table}" VALUES (?)', ("old-authority",))
    from tinyassets.router_traffic import initialize

    initialize(root)


def fingerprint(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def platform(tmp_path, monkeypatch):
    staged = tmp_path / "staged"
    seed(staged)
    # File ownership is asserted by the root shell proof; no real providers here.
    monkeypatch.setattr("os.chown", lambda *args: None)
    return staged


def providers(monkeypatch, events):
    def resume(staged, *, backup_time):
        assert json.loads((staged / ".layout.json").read_text())["state"] == "recovering"
        assert backup_time == 1
        events.append("deletion")

    monkeypatch.setattr(recovery, "resume_deletion_jobs", resume)

    def authority(staged, now):
        assert json.loads((staged / ".layout.json").read_text())["state"] == "recovering"
        events.extend(["billing", "spend", "suspension"])

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
        report = recovery.platform_recovery(platform, backup_time=1, now=2000000000, **adapters)
    assert fingerprint(owners) == before
    assert events == ["deletion", "billing", "spend", "suspension", "identity", "revoke"]
    assert report["credential_roots_reset"]
    assert report["broker_replay_cutoff_advanced"]
    assert report["owners_reconciled"] == 1
    for relative, tables in recovery.RESET_TABLES.items():
        with sqlite3.connect(platform / relative) as db:
            for table in tables:
                assert db.execute(f'SELECT * FROM "{table}"').fetchall() == []
    with sqlite3.connect(platform / ".broker/state/owner-identities.db") as db:
        assert db.execute("SELECT secret FROM owner_box_secrets").fetchone()[0] != "old-box-secret"
    assert json.loads((platform / ".router-credentials.json").read_text())
    ops = OpStore(platform / ".broker/ops.db", clock=lambda: 2000000001)
    assert ops.admit("outbound", new_op_id(lambda: 1999999999), "digest").kind == "expired"
    assert ops.admit("outbound", new_op_id(lambda: 2000000301), "digest").kind == "new"
    assert not (platform / ".restore-epoch").exists()
    assert not (platform / ".broker/restore-spend-until").exists()
    assert json.loads((platform / ".layout.json").read_text())["state"] == "stable"


def test_platform_recovery_waits_for_deletion_jobs(platform):
    with pytest.raises(recovery.RecoveryUnavailable, match="deletion-job recovery"):
        recovery.platform_recovery(platform, backup_time=1, now=2000000000)
    assert json.loads((platform / ".layout.json").read_text())["state"] == "recovering"
    assert not (platform / ".platform-recovery.json").exists()
    from tinyassets.boxes.layout import hold
    with pytest.raises(RuntimeError, match="committed owner-box layout"):
        with hold(platform):
            pytest.fail("incomplete recovery admitted a shared service")
    with sqlite3.connect(platform / ".api-keys.sqlite3") as db:
        assert db.execute("SELECT * FROM api_keys").fetchall() == []


@pytest.mark.parametrize(
    "failed", ["deletion", "billing", "spend", "suspension", "identity", "revoke"]
)
def test_platform_recovery_authority_failure_never_reopens(platform, monkeypatch, failed):
    events = []
    adapters = providers(monkeypatch, events)

    def unavailable(*args, **kwargs):
        raise recovery.RecoveryUnavailable(failed)

    if failed == "deletion":
        monkeypatch.setattr(recovery, "resume_deletion_jobs", unavailable)
    elif failed in {"billing", "spend", "suspension"}:
        adapters["authority"] = unavailable
    elif failed == "identity":
        adapters["identity_exists"] = unavailable
    else:
        adapters["revoke_sessions"] = unavailable
    with pytest.raises(recovery.RecoveryUnavailable, match=failed):
        recovery.platform_recovery(platform, backup_time=1, now=2000000000, **adapters)
    assert json.loads((platform / ".layout.json").read_text())["state"] == "recovering"
    assert not (platform / ".platform-recovery.json").exists()


@pytest.mark.parametrize("identity", [False, None])
def test_platform_recovery_missing_identity_denies(platform, monkeypatch, identity):
    adapters = providers(monkeypatch, [])
    adapters["identity_exists"] = lambda owner: identity
    with pytest.raises(recovery.RecoveryUnavailable, match="identity provider"):
        recovery.platform_recovery(platform, backup_time=1, now=2000000000, **adapters)
    assert json.loads((platform / ".layout.json").read_text())["state"] == "recovering"


def test_platform_recovery_billing_refresh_is_not_spend_or_suspension_authority(
    platform, monkeypatch
):
    monkeypatch.setattr(
        "tinyassets.billing.stripe_adapter.restore_billing",
        lambda root, now: {"plans_synced": 1, "abuse_suspension": "no external source"},
    )
    with pytest.raises(recovery.RecoveryUnavailable, match="spend and suspension"):
        recovery.reconcile_authority(platform, 2000000000)


def test_future_dated_consumed_operation_cannot_replay_after_restore(platform):
    from tinyassets.broker.ops import MAX_FUTURE_SKEW_S, OpStore, new_op_id

    start = 2000000000
    op_id = new_op_id(lambda: start + 240)
    live_ops = OpStore(platform / ".broker/ops.db", clock=lambda: start)
    assert live_ops.admit("outbound", op_id, "digest").kind == "new"
    live_ops.mark_may_have_sent("outbound", op_id)
    live_ops.finish("outbound", op_id, "completed")
    recovery.fence_ops(platform, start + 60)
    restored = OpStore(platform / ".broker/ops.db", clock=lambda: start + 60)
    assert restored.admit("outbound", op_id, "digest").kind == "expired"
    boundary = new_op_id(lambda: start + 60 + MAX_FUTURE_SKEW_S)
    assert restored.admit("outbound", boundary, "digest").kind == "expired"


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


def test_only_root_can_open_router_traffic(tmp_path, monkeypatch):
    import asyncio

    from tinyassets import router_credentials, rpc_frames
    from tinyassets.router_traffic import initialize, state

    initialize(tmp_path)
    caller = [1002]
    monkeypatch.setattr("tinyassets.broker.server.peer_uid", lambda sock: caller[0])

    async def run():
        async with router_credentials.serving(tmp_path):
            for uid in (1002, 0):
                caller[0] = uid
                reader, writer = await asyncio.open_unix_connection(
                    str(router_credentials.credential_socket(tmp_path))
                )
                writer.write(rpc_frames.control(rpc_frames.CONNECTION, {"op": "OPEN_TRAFFIC"}))
                await writer.drain()
                answer = (await rpc_frames.read_frame(reader)).control()
                if uid:
                    assert answer == {"op": "ROUTER_CREDENTIAL_REFUSED"}
                    assert state(tmp_path)[0] is None
                else:
                    assert answer == {"op": "TRAFFIC_OPENED", "traffic_opened": True}
                    assert state(tmp_path)[0] is not None
                writer.close()
                await writer.wait_closed()

    asyncio.run(run())


def test_router_cutover_gate_and_external_token_floor(tmp_path, monkeypatch):
    from tinyassets.router_traffic import accepts_issued_at, admits, initialize, open_traffic, state

    initialize(tmp_path)
    monkeypatch.setenv("TINYASSETS_CANARY_USER_ID", "canary")
    assert admits(tmp_path, "canary")
    assert not admits(tmp_path, "alice")
    assert state(tmp_path)[0] is None
    open_traffic(tmp_path)
    first = state(tmp_path)[0]
    assert admits(tmp_path, "alice")
    open_traffic(tmp_path)
    assert state(tmp_path)[0] == first
    with sqlite3.connect(tmp_path / ".router-traffic.sqlite3") as db:
        db.execute("UPDATE traffic SET credentials_after=100")
    assert not accepts_issued_at(tmp_path, 99)
    assert not accepts_issued_at(tmp_path, None)
    assert accepts_issued_at(tmp_path, 101)


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

    from tinyassets.owner_router import OwnerRouter
    from tinyassets.router_traffic import initialize, open_traffic, state

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
            assert state(tmp_path)[0] is None
            open_traffic(tmp_path)
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
    calls = []
    adapters = providers(monkeypatch, calls)
    report = recovery.platform_recovery(platform, backup_time=1, now=2000000000, **adapters)
    assert report["owners_reconciled"] == 1
    assert calls == ["deletion", "billing", "spend", "suspension", "identity", "revoke"]
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
    from tinyassets.erasure_ledger import ErasureLedger

    ErasureLedger(staged).initialize()
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
        db.execute("UPDATE owner_box_secrets SET secret='old-secret'")
    assert ApiKeyStore(staged).resolve(key) is not None
    assert HookOwners(staged).resolve("hook") == "alice"
    assert owner_sessions.lookup("session") is not None
    report = recovery.platform_recovery(staged, backup_time=1, now=2000000000, **adapters)
    assert ApiKeyStore(staged).resolve(key) is None
    assert HookOwners(staged).resolve("hook") is None
    assert owner_sessions.lookup("session") is None
    with sqlite3.connect(staged / ".broker/state/owner-identities.db") as db:
        assert db.execute("SELECT secret FROM owner_box_secrets").fetchone()[0] != "old-secret"
    assert report["credential_roots_reset"] and report["broker_replay_cutoff_advanced"]
    assert "spend" in events and "suspension" in events


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
        recovery.platform_recovery(
            staged, backup_time=1, now=2000000000, **{**adapters, "revoke_sessions": revoke}
        )
    assert calls == ["alice"]
    assert not (staged / ".platform-recovery.json").exists()
    report = recovery.platform_recovery(
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
    recovery.platform_recovery(staged, backup_time=1, now=2000000000, **adapters)
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
        ".router-traffic.sqlite3",
        ".broker/ops.db",
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
        recovery.platform_recovery(platform, backup_time=1, now=2000000000, **adapters)
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
    from tinyassets.cutover_restore import restore_old_layout
    from tinyassets.erasure_ledger import ErasureLedger, seed_legacy

    damage_many_indexes(source / ".tinyassets.db", corrupt_table=True)
    before = fingerprint(source)
    if caller == "seed":
        ledger_root = tmp_path / "ledger"
        ledger_root.mkdir()
        ledger = ErasureLedger(ledger_root)
        ledger.initialize()
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
        recovery.platform_recovery(platform, backup_time=1, now=2000000000)
