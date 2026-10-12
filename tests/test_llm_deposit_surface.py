"""BYO-LLM subscription deposit surface (write_graph target=connection
operation=connect_llm).

Requirement source:
``openspec/specs/byo-llm-deposit-surface/spec.md``.

Covers: owner-scoped Claude/Codex deposit round-trip, re-deposit upsert with
unrelated-credential preservation, the owner/admin (not write) gate, the
first-depositor-owns / no-transfer rule, sanitized responses/exceptions, and the
no-new-advertised-handle invariant.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import AuthProvider, DevAuthProvider, Identity


class _StaticAuthProvider(AuthProvider):
    """Resolves the fixed token ``valid`` to one identity; enforces auth."""

    def __init__(self, identity: Identity | None) -> None:
        self.identity = identity

    def resolve_token(self, token: str) -> Identity | None:
        return self.identity if token == "valid" else None

    def is_auth_required(self) -> bool:
        return True

    def register_client(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {"client_id": "test-client", **metadata}

    def create_authorization(self, *a: Any, **k: Any) -> str:
        return "test-code"

    def exchange_code(self, *a: Any, **k: Any) -> dict[str, Any] | None:
        return None


def _login(user_id: str) -> None:
    set_provider(
        _StaticAuthProvider(
            Identity(
                user_id=user_id,
                username=user_id,
                capabilities=["tinyassets.universe.write"],
            )
        )
    )
    auth_middleware("valid")


def _logout() -> None:
    """Nobody bound. Resolving a "dev" token binds the LOCAL OPERATOR, which is
    a real principal -- so a logged-out caller was still somebody, and a test
    expecting `authentication_required` got a real answer instead."""
    from tinyassets.auth.middleware import clear_identity

    set_provider(DevAuthProvider())
    clear_identity()


@pytest.fixture(autouse=True)
def _reset_auth() -> Any:
    _logout()
    yield
    _logout()


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    return root


def _make_universe(base: Path, uid: str, *, admin: str = "", write: str = "") -> Path:
    from tinyassets.daemon_server import grant_universe_access

    udir = base / uid
    udir.mkdir(parents=True, exist_ok=True)
    if admin:
        grant_universe_access(
            base, universe_id=uid, actor_id=admin, permission="admin", granted_by=admin
        )
    if write:
        grant_universe_access(
            base, universe_id=uid, actor_id=write, permission="write", granted_by=admin
        )
    return udir


def _deposit(uid: str, service: str, material_b64: str) -> dict[str, Any]:
    from tinyassets.api.llm_deposit import connect_llm

    return connect_llm(
        universe_id=uid,
        payload=json.dumps({"service": service, "auth_material_b64": material_b64}),
    )


def _owners_table_exists(base: Path) -> bool:
    """Distinguish an ABSENT owner table from an EXISTING-but-empty one.

    `_owner_rows` returns [] in both cases, which would mask a schema-creation
    mutation on a refused/malformed deposit — so zero-mutation assertions check
    this separately.
    """
    from tinyassets.storage import db_path

    path = db_path(base)
    if not path.exists():
        return False
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='llm_credential_deposit_owners'"
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _owner_rows(base: Path, uid: str) -> list[tuple[str, str]]:
    from tinyassets.storage import db_path

    conn = sqlite3.connect(db_path(base))
    try:
        try:
            rows = conn.execute(
                "SELECT service, owner_user_id FROM llm_credential_deposit_owners "
                "WHERE universe_id = ? ORDER BY service",
                (uid,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(str(r[0]), str(r[1])) for r in rows]
    finally:
        conn.close()


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


# --------------------------------------------------------------------------- #
# Positive: owner deposits round-trip to the resolvers.
# --------------------------------------------------------------------------- #


def test_owner_deposits_claude_round_trips(base: Path) -> None:
    from tinyassets.credential_vault import (
        claude_subscription_auth_available,
        resolve_claude_oauth_token,
    )

    udir = _make_universe(base, "u-owner", admin="founder")
    _login("founder")
    token = "sk-ant-oat-SECRET-round-trip-value"

    result = _deposit("u-owner", "claude", _b64(token))

    assert result["status"] == "deposited"
    assert result["service"] == "claude"
    assert result["next"].startswith("write_graph target=agent_binding")
    assert resolve_claude_oauth_token(udir) == token
    assert claude_subscription_auth_available(udir) is True
    assert _owner_rows(base, "u-owner") == [("claude", "founder")]


@pytest.mark.parametrize("wrong", [
    "browser-authorization-code#state", "not-a-token",
    "sk-ant-oat01-first\nsecond", "export CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-value",
    '{"access_token":"sk-ant-oat01-value"}', "sk-ant-value#state",
])
def test_wrong_claude_artifact_preserves_existing_connection(base, caplog, wrong):
    from tinyassets.credential_vault import load_credential_vault, resolve_claude_oauth_token

    udir = _make_universe(base, "u-reconnect", admin="founder")
    _login("founder")
    old = "sk-ant-oat01-existing-fixture"
    assert _deposit("u-reconnect", "claude", _b64(old))["status"] == "deposited"
    before = load_credential_vault(udir)
    owners = _owner_rows(base, "u-reconnect")
    result = _deposit("u-reconnect", "claude", _b64(wrong))
    assert result["error"] == "connection_setup_invalid"
    assert "back in the terminal" in result["detail"]
    assert load_credential_vault(udir) == before
    assert _owner_rows(base, "u-reconnect") == owners
    assert resolve_claude_oauth_token(udir) == old
    assert wrong not in json.dumps(result) + caplog.text
    assert _b64(wrong) not in json.dumps(result) + caplog.text


def test_browser_code_cannot_create_first_claude_connection(base):
    from tinyassets.credential_vault import credential_vault_path

    udir = _make_universe(base, "u-first", admin="founder")
    _login("founder")
    result = _deposit("u-first", "claude", _b64("browser-code#state"))
    assert result["error"] == "connection_setup_invalid"
    assert not credential_vault_path(udir).exists()
    assert not _owners_table_exists(base)


def test_owner_deposits_codex_materializes_auth_json(base: Path) -> None:
    from tinyassets.credential_vault import (
        codex_subscription_auth_available,
        ensure_codex_home_from_vault,
        load_credential_vault,
    )

    udir = _make_universe(base, "u-codex", admin="founder")
    _login("founder")
    auth_json = json.dumps({"OPENAI_API_KEY": "unused", "tokens": {"id": "abc"}})
    material = _b64(auth_json)

    result = _deposit("u-codex", "codex", material)

    assert result["status"] == "deposited"
    assert result["service"] == "codex"

    # Stored as a base64 STRING, never raw decoded bytes.
    records = [
        r
        for r in load_credential_vault(udir)
        if r.get("credential_type") == "llm_subscription" and r.get("service") == "codex"
    ]
    assert len(records) == 1
    assert isinstance(records[0]["auth_json_b64"], str)
    assert records[0]["auth_json_b64"] == material
    assert "auth_json" not in records[0]  # no raw-bytes field

    # Materializes CODEX_HOME/auth.json with the decoded bytes.
    assert codex_subscription_auth_available(udir) is True
    home = ensure_codex_home_from_vault(udir)
    assert home is not None
    assert (home / "auth.json").read_bytes() == auth_json.encode("utf-8")
    assert _owner_rows(base, "u-codex") == [("codex", "founder")]


def test_deposit_routes_through_write_graph(base: Path) -> None:
    """Integration: the connection dispatch routes connect_llm to the handler."""
    import importlib

    from tinyassets import universe_server as us

    importlib.reload(us)
    try:
        _make_universe(base, "u-route", admin="founder")
        _login("founder")
        raw = us.write_graph(
            target="connection",
            operation="connect_llm",
            graph_id="u-route",
            payload_json=json.dumps(
                {"service": "claude", "auth_material_b64": _b64("sk-ant-tok-route")}
            ),
        )
        payload = json.loads(raw)
        assert payload["status"] == "deposited"
        assert payload["service"] == "claude"
    finally:
        importlib.reload(us)


# --------------------------------------------------------------------------- #
# Re-deposit upserts one slot and preserves every unrelated credential.
# --------------------------------------------------------------------------- #


def test_redeposit_upserts_and_preserves_unrelated(base: Path) -> None:
    from tinyassets.credential_vault import (
        load_credential_vault,
        resolve_claude_oauth_token,
        write_credential_vault,
    )

    udir = _make_universe(base, "u-mix", admin="founder")
    _login("founder")

    codex_b64 = _b64(json.dumps({"tokens": {"id": "codex-1"}}))
    seed = [
        {"credential_type": "llm_subscription", "service": "codex", "auth_json_b64": codex_b64},
        {
            "credential_type": "vcs",
            "service": "github",
            "destination": "founder/repo",
            "token": "ghp-KEEP-ME",
            "purpose": "write",
        },
        {
            "credential_type": "social",
            "service": "slack",
            "destination": "conn-1",
            "app_token": "xapp-KEEP-ME",
        },
    ]
    write_credential_vault(udir, seed, owner_user_id="founder", universe_id="u-mix")

    def _by(kind: str, service: str) -> dict[str, Any]:
        return next(
            r
            for r in load_credential_vault(udir)
            if r.get("credential_type") == kind and r.get("service") == service
        )

    codex_before = _by("llm_subscription", "codex")
    github_before = _by("vcs", "github")
    slack_before = _by("social", "slack")

    # First claude deposit, then a re-deposit — must upsert the single slot.
    assert _deposit("u-mix", "claude", _b64("sk-ant-claude-v1"))["status"] == "deposited"
    assert _deposit("u-mix", "claude", _b64("sk-ant-claude-v2"))["status"] == "deposited"

    claude_records = [
        r
        for r in load_credential_vault(udir)
        if r.get("credential_type") == "llm_subscription" and r.get("service") == "claude"
    ]
    assert len(claude_records) == 1
    assert resolve_claude_oauth_token(udir) == "sk-ant-claude-v2"

    # Every unrelated credential is byte-for-byte intact.
    assert _by("llm_subscription", "codex") == codex_before
    assert _by("vcs", "github") == github_before
    assert _by("social", "slack") == slack_before

    # Ownership rows unchanged for codex; claude now owned by founder.
    assert set(_owner_rows(base, "u-mix")) == {("claude", "founder"), ("codex", "founder")}


# --------------------------------------------------------------------------- #
# Negative: each refusal leaves ZERO vault / ownership mutation.
# --------------------------------------------------------------------------- #


def test_other_universe_founder_cannot_deposit_into_victim(base: Path) -> None:
    from tinyassets.credential_vault import credential_vault_path

    _make_universe(base, "u-a", admin="founder-a")
    victim = _make_universe(base, "u-b", admin="founder-b")

    _login("founder-a")  # admin of A, nothing on B
    result = _deposit("u-b", "claude", _b64("attacker-token"))

    assert result == {"error": "not_found", "resource": "connection"}
    assert not credential_vault_path(victim).exists()
    assert _owner_rows(base, "u-b") == []


def test_write_collaborator_cannot_seize_empty_slot(base: Path) -> None:
    from tinyassets.credential_vault import credential_vault_path

    udir = _make_universe(base, "u-collab", admin="founder", write="collab")

    _login("collab")  # holds only "write"
    result = _deposit("u-collab", "claude", _b64("collab-token"))

    assert result == {"error": "not_found", "resource": "connection"}
    assert not credential_vault_path(udir).exists()
    assert _owner_rows(base, "u-collab") == []


def test_admin_can_where_write_cannot(base: Path) -> None:
    _make_universe(base, "u-both", admin="founder", write="collab")

    _login("collab")
    assert _deposit("u-both", "claude", _b64("x"))["error"] == "not_found"

    _login("founder")
    assert _deposit("u-both", "claude", _b64("sk-ant-y"))["status"] == "deposited"
    assert _owner_rows(base, "u-both") == [("claude", "founder")]


def test_anonymous_refused_before_vault_touch(base: Path, nobody) -> None:
    from tinyassets.credential_vault import credential_vault_path

    udir = _make_universe(base, "u-anon", admin="founder")
    # No _login(): the autouse fixture left us anonymous.
    result = _deposit("u-anon", "claude", _b64("anon-token"))

    assert result["error"] == "authentication_required"
    assert not credential_vault_path(udir).exists()
    assert _owner_rows(base, "u-anon") == []


def test_non_owner_admin_cannot_overwrite_owned_credential(base: Path) -> None:
    """A second admin cannot transfer an existing owned credential."""
    from tinyassets.credential_vault import load_credential_vault, resolve_claude_oauth_token

    udir = _make_universe(base, "u-co", admin="founder")
    from tinyassets.daemon_server import grant_universe_access

    grant_universe_access(
        base, universe_id="u-co", actor_id="coadmin", permission="admin", granted_by="founder"
    )

    _login("founder")
    assert _deposit("u-co", "claude", _b64("sk-ant-founder-token"))["status"] == "deposited"
    before = load_credential_vault(udir)
    owners_before = _owner_rows(base, "u-co")

    _login("coadmin")  # also admin, but NOT the credential owner
    result = _deposit("u-co", "claude", _b64("sk-ant-coadmin-token"))

    assert result["error"] == "credential_ownership_transfer_unsupported"
    # Existing owned record unchanged, ownership not transferred.
    assert load_credential_vault(udir) == before
    assert resolve_claude_oauth_token(udir) == "sk-ant-founder-token"
    assert _owner_rows(base, "u-co") == owners_before == [("claude", "founder")]


def test_unsupported_service_rejected_without_write(base: Path) -> None:
    from tinyassets.credential_vault import credential_vault_path

    udir = _make_universe(base, "u-svc", admin="founder")
    _login("founder")
    result = _deposit("u-svc", "gemini", _b64("whatever"))

    assert result["error"] == "unsupported_service"
    assert "claude" in result["allowed_services"]
    assert not credential_vault_path(udir).exists()
    assert _owner_rows(base, "u-svc") == []


# --------------------------------------------------------------------------- #
# Sanitized: no secret in the response, logs, or exceptions.
# --------------------------------------------------------------------------- #


def test_success_response_and_logs_carry_no_secret(
    base: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _make_universe(base, "u-clean", admin="founder")
    _login("founder")
    token = "sk-ant-SUPER-SECRET-do-not-echo"
    material = _b64(token)

    with caplog.at_level("DEBUG"):
        result = _deposit("u-clean", "claude", material)

    blob = json.dumps(result)
    assert token not in blob
    assert material not in blob
    assert token not in caplog.text
    assert material not in caplog.text


def test_malformed_base64_claude_zero_mutation_and_no_secret(base: Path) -> None:
    from tinyassets.credential_vault import credential_vault_path

    udir = _make_universe(base, "u-badc", admin="founder")
    _login("founder")
    bad = "!!!not-valid-base64!!!"

    assert not _owners_table_exists(base)  # absent before any deposit
    result = _deposit("u-badc", "claude", bad)

    assert result["error"] == "connection_setup_invalid"
    assert bad not in json.dumps(result)  # do not echo the submitted material
    assert not credential_vault_path(udir).exists()
    assert _owner_rows(base, "u-badc") == []
    assert not _owners_table_exists(base)


def test_malformed_base64_codex_zero_mutation(base: Path) -> None:
    from tinyassets.credential_vault import credential_vault_path

    udir = _make_universe(base, "u-badx", admin="founder")
    _login("founder")
    # Valid base64 whose decoded content is not JSON — the vault rejects it.
    not_json = _b64("this is not json")

    assert not _owners_table_exists(base)  # absent before any deposit
    result = _deposit("u-badx", "codex", not_json)

    assert result["error"] == "connection_setup_invalid"
    assert not credential_vault_path(udir).exists()
    assert _owner_rows(base, "u-badx") == []
    # FIX 2: a malformed record must not even create the owner schema.
    assert not _owners_table_exists(base)


def test_missing_material_rejected(base: Path) -> None:
    from tinyassets.api.llm_deposit import connect_llm
    from tinyassets.credential_vault import credential_vault_path

    udir = _make_universe(base, "u-nomat", admin="founder")
    _login("founder")
    result = connect_llm(universe_id="u-nomat", payload=json.dumps({"service": "claude"}))

    assert result["error"] == "connection_setup_invalid"
    assert not credential_vault_path(udir).exists()


# --------------------------------------------------------------------------- #
# Atomicity: a deposit that fails mid-write mutates NOTHING (fail-closed).
# --------------------------------------------------------------------------- #


def test_failed_deposit_is_atomic_prior_token_unchanged(
    base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-deposit whose owner-row DB write fails must leave the prior token intact.

    The owner-row transaction now runs and commits BEFORE the vault file is
    replaced, so an owner-row INSERT failure aborts before the file is touched —
    the prior token stays active and no owner row changes.
    """
    import tinyassets.credential_vault as cv
    from tinyassets.credential_vault import resolve_claude_oauth_token

    udir = _make_universe(base, "u-atom", admin="founder")
    _login("founder")

    # First deposit succeeds and binds the owner.
    assert _deposit("u-atom", "claude", _b64("sk-ant-old-token"))["status"] == "deposited"
    assert resolve_claude_oauth_token(udir) == "sk-ant-old-token"
    owners_before = _owner_rows(base, "u-atom")

    # Inject a failure in the owner-row INSERT of the NEXT (re)deposit. The proxy
    # is fully transparent for every other statement (and every other sqlite3
    # user the shared-module patch touches) — only the owner-row INSERT raises.
    real_connect = cv.sqlite3.connect

    class _FailingInsertConn:
        def __init__(self, real: Any) -> None:
            object.__setattr__(self, "_real", real)

        def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
            if "INSERT INTO llm_credential_deposit_owners" in sql:
                raise sqlite3.IntegrityError("injected owner-row failure")
            return self._real.execute(sql, *args, **kwargs)

        def __enter__(self) -> Any:
            self._real.__enter__()
            return self

        def __exit__(self, *exc: Any) -> Any:
            return self._real.__exit__(*exc)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

        def __setattr__(self, name: str, value: Any) -> None:
            # Forward attribute writes (e.g. row_factory) to the real connection.
            setattr(self._real, name, value)

    def _fake_connect(*args: Any, **kwargs: Any) -> Any:
        return _FailingInsertConn(real_connect(*args, **kwargs))

    monkeypatch.setattr(cv.sqlite3, "connect", _fake_connect)

    result = _deposit("u-atom", "claude", _b64("sk-ant-new-token"))

    # Handler fails closed; the prior credential and ownership are untouched.
    assert result["error"] == "deposit_failed"
    assert resolve_claude_oauth_token(udir) == "sk-ant-old-token"
    assert _owner_rows(base, "u-atom") == owners_before == [("claude", "founder")]


def test_reader_never_sees_new_token_and_file_failure_rolls_back_owner(
    base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB-first, file-last: after the owner-row commit but before the file write,
    a concurrent unlocked reader still sees the OLD token; and if the file replace
    then fails, the owner row is compensated back so no orphaned ownership remains.
    """
    import tinyassets.credential_vault as cv
    from tinyassets.credential_vault import resolve_claude_oauth_token

    udir = _make_universe(base, "u-read", admin="founder")
    _login("founder")
    assert _deposit("u-read", "claude", _b64("sk-ant-old-token"))["status"] == "deposited"
    owners_before = _owner_rows(base, "u-read")

    observed: dict[str, str] = {}

    def _spy_persist(universe_dir: Any, records: Any, *, owner: str = "") -> None:
        # The owner-row DB transaction has already committed here. A concurrent
        # unlocked reader MUST still see the OLD token — the file is not yet
        # written. Then simulate the file-replace failing.
        observed["mid_deposit"] = resolve_claude_oauth_token(udir)
        raise OSError("injected file-replace failure")

    monkeypatch.setattr(cv, "_persist_credential_vault_file", _spy_persist)

    result = _deposit("u-read", "claude", _b64("sk-ant-new-token"))

    assert result["error"] == "deposit_failed"
    # The reader never observed the not-yet-committed token.
    assert observed["mid_deposit"] == "sk-ant-old-token"
    # After the failure the file is unchanged and the owner row is compensated.
    assert resolve_claude_oauth_token(udir) == "sk-ant-old-token"
    assert _owner_rows(base, "u-read") == owners_before == [("claude", "founder")]


def test_file_failure_on_first_deposit_leaves_no_orphan_owner(
    base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A FIRST deposit whose file write fails after the owner-row commit must not
    leave a committed-but-ineffective owner row (no credential, but an owner).
    """
    import tinyassets.credential_vault as cv
    from tinyassets.credential_vault import (
        credential_vault_path,
        resolve_claude_oauth_token,
    )

    udir = _make_universe(base, "u-orphan", admin="founder")
    _login("founder")
    assert not _owners_table_exists(base) or _owner_rows(base, "u-orphan") == []

    def _fail_persist(universe_dir: Any, records: Any, *, owner: str = "") -> None:
        raise OSError("injected file-replace failure")

    monkeypatch.setattr(cv, "_persist_credential_vault_file", _fail_persist)

    result = _deposit("u-orphan", "claude", _b64("sk-ant-first-token"))

    assert result["error"] == "deposit_failed"
    assert not credential_vault_path(udir).exists()  # no vault file
    # The owner row was compensated away — no orphaned ownership without a credential.
    assert _owner_rows(base, "u-orphan") == []
    assert resolve_claude_oauth_token(udir) == ""


def test_post_commit_durability_failure_still_succeeds(
    base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The successful Path.replace is the commit point: a post-commit durability
    failure (fsync/close) must NOT fail the deposit or wipe the owner row.
    """
    import tinyassets.credential_vault as cv
    from tinyassets.credential_vault import resolve_claude_oauth_token

    udir = _make_universe(base, "u-dur", admin="founder")
    _login("founder")

    def _boom(vault_file: Any, universe: Any) -> None:
        raise OSError("injected post-commit durability failure")

    monkeypatch.setattr(cv, "_post_commit_durability", _boom)

    result = _deposit("u-dur", "claude", _b64("sk-ant-durable-token"))

    # The credential is committed (file visible) and its owner row is present.
    assert result["status"] == "deposited"
    assert resolve_claude_oauth_token(udir) == "sk-ant-durable-token"
    assert _owner_rows(base, "u-dur") == [("claude", "founder")]


def test_double_failure_replace_and_compensation_raises(
    base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-commit file failure whose owner-row compensation ALSO fails must be
    raised (chaining both), never swallowed.
    """
    import tinyassets.credential_vault as cv

    udir = _make_universe(base, "u-dbl", admin="founder")
    cv.write_credential_vault(
        udir,
        [{"credential_type": "llm_subscription", "service": "claude", "oauth_token": "seed"}],
        owner_user_id="founder",
        universe_id="u-dbl",
    )

    def _persist_boom(universe_dir: Any, records: Any, *, owner: str = "") -> None:
        raise OSError("replace failed pre-commit")

    def _restore_boom(conn: Any, uid: Any, prior: Any) -> None:
        raise sqlite3.OperationalError("compensation failed")

    monkeypatch.setattr(cv, "_persist_credential_vault_file", _persist_boom)
    monkeypatch.setattr(cv, "_restore_owner_rows", _restore_boom)

    with pytest.raises(sqlite3.OperationalError) as excinfo:
        cv.write_credential_vault(
            udir,
            [{"credential_type": "llm_subscription", "service": "claude", "oauth_token": "new"}],
            owner_user_id="founder",
            universe_id="u-dbl",
        )
    # Both failures surfaced: the compensation error chains the original persist error.
    assert isinstance(excinfo.value.__cause__, OSError)


def test_malformed_payload_against_absent_universe_creates_nothing(
    base: Path,
) -> None:
    """FIX B: a malformed deposit must not create the universe dir, the admission
    lock, the vault, or the owner table — validation runs BEFORE the lock.
    """
    from tinyassets.credential_vault import credential_vault_path
    from tinyassets.daemon_server import grant_universe_access

    # Grant admin so the handler's ACL gate passes and we actually reach the vault
    # write — but do NOT create the universe directory (that is the point).
    grant_universe_access(
        base, universe_id="u-ghost", actor_id="founder", permission="admin",
        granted_by="founder",
    )
    _login("founder")
    udir = base / "u-ghost"
    assert not udir.exists()  # precondition: the universe dir does not exist yet
    assert not _owners_table_exists(base)

    # Valid base64 whose decoded content is not JSON — rejected before the lock.
    result = _deposit("u-ghost", "codex", _b64("this is not json"))

    assert result["error"] == "connection_setup_invalid"
    assert not udir.exists()  # no directory created
    assert not (udir / ".provider-assignment-admission.lock").exists()  # no lock
    assert not credential_vault_path(udir).exists()  # no vault file
    assert not _owners_table_exists(base)  # no owner schema


# --------------------------------------------------------------------------- #
# The deposit adds no advertised MCP handle (Hard Rule #11 / PR-178 guard).
# --------------------------------------------------------------------------- #


def test_connect_llm_adds_no_advertised_handle(base: Path) -> None:
    import importlib

    from scripts.mcp_public_canary import CANONICAL_HANDLES
    from tinyassets import universe_server as us

    importlib.reload(us)
    try:
        advertised = {
            t.name for t in asyncio.run(us.mcp.list_tools(run_middleware=True))
        }
        assert advertised == set(CANONICAL_HANDLES)
        assert "connect_llm" not in advertised
    finally:
        importlib.reload(us)
