from __future__ import annotations

import inspect
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import tinyassets.storage.accounts as accounts_store
from tinyassets.api import permissions as permissions_api
from tinyassets.auth.provider import Identity
from tinyassets.branch_tasks import read_queue
from tinyassets.daemon_server import (
    ensure_universe_registered,
    grant_universe_access,
    initialize_author_server,
    revoke_universe_access,
)
from tinyassets.storage import CAP_GRANT_CAPABILITIES, db_path
from tinyassets.storage.accounts import (
    PRIORITY_REQUEST_CAPABILITY,
    CapabilityGrantAuthorizationError,
    active_priority_grant_from_connection,
    create_or_update_account,
    get_account,
    get_active_priority_grant,
    grant_capabilities,
    issue_priority_grant,
    list_capabilities,
    list_capability_grant_history,
    revoke_priority_grant,
)
from tinyassets.storage.request_admissions import RequestAdmissionStore


@pytest.fixture
def server_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[float], None]:
    current = [time.time()]
    monkeypatch.setattr(accounts_store, "_now", lambda: current[0])

    def set_time(timestamp: float) -> None:
        current[0] = timestamp

    return set_time


def _connect(base_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(base_path))
    conn.row_factory = sqlite3.Row
    return conn


def _create_universe(base_path: Path, universe_id: str) -> None:
    universe_path = base_path / universe_id
    universe_path.mkdir(parents=True, exist_ok=True)
    ensure_universe_registered(
        base_path,
        universe_id=universe_id,
        universe_path=universe_path,
    )


def _create_actor(base_path: Path, username: str) -> str:
    return str(
        create_or_update_account(
            base_path,
            username=username,
        )["user_id"]
    )


def _authorized_fixture(
    base_path: Path,
    *,
    universe_id: str = "universe-a",
) -> tuple[str, str]:
    initialize_author_server(base_path)
    _create_universe(base_path, universe_id)
    issuer_id = _create_actor(base_path, "issuer")
    subject_id = _create_actor(base_path, "subject")
    grant_universe_access(
        base_path,
        universe_id=universe_id,
        actor_id=issuer_id,
        permission="admin",
        granted_by=issuer_id,
    )
    grant_capabilities(
        base_path,
        user_id=issuer_id,
        capabilities=[CAP_GRANT_CAPABILITIES],
        granted_by=issuer_id,
        universe_id=universe_id,
    )
    return issuer_id, subject_id


def _authenticate_request(
    monkeypatch: pytest.MonkeyPatch,
    *,
    base_path: Path,
    actor_id: str,
    capabilities: list[str],
    tenant_id: str = "tenant-a",
) -> None:
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base_path))
    identity = Identity(
        user_id=actor_id,
        username=actor_id,
        capabilities=capabilities,
        metadata={"org_id": tenant_id},
    )
    monkeypatch.setattr(
        "tinyassets.auth.middleware.current_identity",
        lambda: identity,
    )


def _commit_priority_admission(
    store: RequestAdmissionStore,
    *,
    actor_id: str,
    grant_generation: int,
) -> dict:
    return store.commit_admission(
        tenant_id="tenant-a",
        actor_id=actor_id,
        universe_id="universe-a",
        idempotency_key_hash="hmac-sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        body_digest="sha256:body-a",
        body_digest_version="rfc8785-v1",
        request_type="general",
        text="historical priority admission",
        branch_id="",
        branch_def_id="loop-branch",
        trigger_source="operator_request",
        accepted_priority_weight=50,
        policy_version="operator-priority-v1",
        grant_generation=grant_generation,
        receipt={},
        directed_daemon_id="",
        created_at="2026-07-24T19:00:00Z",
    )


def test_pretraffic_migration_preserves_legacy_capability_row(
    tmp_path: Path,
) -> None:
    with _connect(tmp_path) as conn:
        conn.executescript(
            """
            CREATE TABLE user_accounts (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE capability_grants (
                user_id TEXT NOT NULL,
                capability TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT '*',
                granted_by TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(user_id, capability, scope),
                FOREIGN KEY(user_id) REFERENCES user_accounts(user_id)
                    ON DELETE CASCADE
            );
            INSERT INTO user_accounts (
                user_id, username, display_name, created_at, updated_at
            ) VALUES ('user::legacy', 'legacy', 'Legacy', 100, 100);
            INSERT INTO capability_grants (
                user_id, capability, scope, granted_by, created_at
            ) VALUES (
                'user::legacy', 'submit_request', '*', 'system', 100
            );
            """
        )

    initialize_author_server(tmp_path)

    with _connect(tmp_path) as conn:
        columns = {
            row["name"]: row
            for row in conn.execute(
                "PRAGMA table_info(capability_grants)"
            ).fetchall()
        }
        row = conn.execute(
            "SELECT * FROM capability_grants"
        ).fetchone()
        assert {"generation", "expires_at", "revoked_at"} <= set(columns)
        assert [
            columns[name]["pk"]
            for name in ("user_id", "capability", "scope", "generation")
        ] == [1, 2, 3, 4]
        assert row["generation"] == 1
        assert row["expires_at"] is None
        assert row["revoked_at"] is None
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    assert list_capabilities(
        tmp_path,
        user_id="user::legacy",
    ) == ["submit_request"]


def test_exact_admin_and_capability_issue_generation(
    tmp_path: Path,
    server_clock: Callable[[float], None],
) -> None:
    issuer_id, subject_id = _authorized_fixture(tmp_path)
    issued_at = time.time() + 1
    server_clock(issued_at)

    grant = issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
        expires_at=issued_at + 60,
    )

    assert grant == {
        "user_id": subject_id,
        "capability": PRIORITY_REQUEST_CAPABILITY,
        "scope": "universe-a",
        "granted_by": issuer_id,
        "created_at": issued_at,
        "expires_at": issued_at + 60,
        "revoked_at": None,
        "generation": 1,
    }
    assert get_active_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        evaluated_at=issued_at,
    ) == grant


def test_trusted_issue_binds_opaque_request_subject_without_ordinary_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_clock: Callable[[float], None],
) -> None:
    initialize_author_server(tmp_path)
    _create_universe(tmp_path, "universe-a")
    issuer_id = "user_workos_01J4MCPADMIN"
    request_subject = "user_workos_01J4MCPFOUNDER"
    grant_universe_access(
        tmp_path,
        universe_id="universe-a",
        actor_id=issuer_id,
        permission="admin",
        granted_by=issuer_id,
    )
    grant_capabilities(
        tmp_path,
        user_id=issuer_id,
        capabilities=[CAP_GRANT_CAPABILITIES],
        granted_by=issuer_id,
        universe_id="universe-a",
    )
    grant_universe_access(
        tmp_path,
        universe_id="universe-a",
        actor_id=request_subject,
        permission="write",
        granted_by=issuer_id,
    )
    evaluated_at = time.time() + 1
    server_clock(evaluated_at)

    grant = issue_priority_grant(
        tmp_path,
        subject_id=request_subject,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )
    account = get_account(tmp_path, user_id=request_subject)
    _authenticate_request(
        monkeypatch,
        base_path=tmp_path,
        actor_id=request_subject,
        capabilities=["write"],
    )
    monkeypatch.setattr(
        permissions_api,
        "_now",
        lambda: evaluated_at,
        raising=False,
    )
    verdict = permissions_api.operator_request_admission_verdict(
        "universe-a",
        requested_priority_weight=1.0,
    )

    assert grant["user_id"] == request_subject
    assert account is not None
    assert account["user_id"] == request_subject
    assert list_capabilities(
        tmp_path,
        user_id=request_subject,
        universe_id="universe-a",
    ) == [PRIORITY_REQUEST_CAPABILITY]
    assert verdict.allowed is True
    assert verdict.actor_id == request_subject
    assert verdict.grant_generation == grant["generation"]


def test_unauthorized_issue_does_not_provision_opaque_subject(
    tmp_path: Path,
    server_clock: Callable[[float], None],
) -> None:
    initialize_author_server(tmp_path)
    _create_universe(tmp_path, "universe-a")
    issuer_id = _create_actor(tmp_path, "issuer")
    request_subject = "user_workos_untrusted_target"
    server_clock(time.time() + 1)

    with pytest.raises(CapabilityGrantAuthorizationError):
        issue_priority_grant(
            tmp_path,
            subject_id=request_subject,
            universe_id="universe-a",
            issuer_id=issuer_id,
        )

    assert get_account(tmp_path, user_id=request_subject) is None


@pytest.mark.parametrize(
    ("admin", "capability_scope"),
    [
        (False, "universe-a"),
        (True, ""),
        (True, "*"),
        (True, "universe-b"),
    ],
)
def test_issue_requires_exact_admin_and_exact_grant_capability(
    tmp_path: Path,
    admin: bool,
    capability_scope: str,
    server_clock: Callable[[float], None],
) -> None:
    initialize_author_server(tmp_path)
    _create_universe(tmp_path, "universe-a")
    _create_universe(tmp_path, "universe-b")
    issuer_id = _create_actor(tmp_path, "issuer")
    subject_id = _create_actor(tmp_path, "subject")
    if admin:
        grant_universe_access(
            tmp_path,
            universe_id="universe-a",
            actor_id=issuer_id,
            permission="admin",
            granted_by=issuer_id,
        )
    if capability_scope:
        grant_capabilities(
            tmp_path,
            user_id=issuer_id,
            capabilities=[CAP_GRANT_CAPABILITIES],
            granted_by=issuer_id,
            universe_id=(
                None if capability_scope == "*" else capability_scope
            ),
        )
    server_clock(time.time() + 1)

    with pytest.raises(CapabilityGrantAuthorizationError):
        issue_priority_grant(
            tmp_path,
            subject_id=subject_id,
            universe_id="universe-a",
            issuer_id=issuer_id,
        )

    assert list_capability_grant_history(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        capability=PRIORITY_REQUEST_CAPABILITY,
    ) == []


def test_revoked_issuer_cannot_backdate_issue_or_revoke(
    tmp_path: Path,
    server_clock: Callable[[float], None],
) -> None:
    issuer_id, subject_id = _authorized_fixture(tmp_path)
    other_subject_id = _create_actor(tmp_path, "other-subject")
    issued_at = time.time() + 1
    server_clock(issued_at)
    subject_grant = issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )
    issuer_revoked_at = issued_at + 1
    with _connect(tmp_path) as conn:
        conn.execute(
            """
            UPDATE capability_grants
            SET revoked_at = ?
            WHERE user_id = ? AND capability = ? AND scope = ?
            """,
            (
                issuer_revoked_at,
                issuer_id,
                CAP_GRANT_CAPABILITIES,
                "universe-a",
            ),
        )

    server_clock(issuer_revoked_at + 1)
    with pytest.raises(CapabilityGrantAuthorizationError):
        issue_priority_grant(
            tmp_path,
            subject_id=other_subject_id,
            universe_id="universe-a",
            issuer_id=issuer_id,
        )
    with pytest.raises(CapabilityGrantAuthorizationError):
        revoke_priority_grant(
            tmp_path,
            subject_id=subject_id,
            universe_id="universe-a",
            issuer_id=issuer_id,
        )

    assert list_capability_grant_history(
        tmp_path,
        subject_id=other_subject_id,
        universe_id="universe-a",
        capability=PRIORITY_REQUEST_CAPABILITY,
    ) == []
    assert list_capability_grant_history(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        capability=PRIORITY_REQUEST_CAPABILITY,
    ) == [subject_grant]
    assert "issued_at" not in inspect.signature(
        issue_priority_grant
    ).parameters
    assert "revoked_at" not in inspect.signature(
        revoke_priority_grant
    ).parameters


def test_generic_grant_path_cannot_issue_operator_priority(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    subject_id = _create_actor(tmp_path, "subject")

    with pytest.raises(
        ValueError,
        match="trusted priority-grant service",
    ):
        grant_capabilities(
            tmp_path,
            user_id=subject_id,
            capabilities=[
                "submit_request",
                PRIORITY_REQUEST_CAPABILITY,
            ],
            granted_by="system",
            universe_id="universe-a",
        )

    assert list_capabilities(
        tmp_path,
        user_id=subject_id,
        universe_id="universe-a",
    ) == []

    with pytest.raises(
        ValueError,
        match="trusted priority-grant service",
    ):
        create_or_update_account(
            tmp_path,
            username="partial-account",
            capabilities=[
                "submit_request",
                PRIORITY_REQUEST_CAPABILITY,
            ],
        )
    assert get_account(tmp_path, username="partial-account") is None


def test_wildcard_priority_target_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    issuer_id = _create_actor(tmp_path, "issuer")
    subject_id = _create_actor(tmp_path, "subject")

    with pytest.raises(ValueError, match="exact command center"):
        issue_priority_grant(
            tmp_path,
            subject_id=subject_id,
            universe_id="*",
            issuer_id=issuer_id,
        )

    assert list_capability_grant_history(
        tmp_path,
        subject_id=subject_id,
        capability=PRIORITY_REQUEST_CAPABILITY,
    ) == []


def test_expiry_boundary_and_regrant_preserve_history(
    tmp_path: Path,
    server_clock: Callable[[float], None],
) -> None:
    issuer_id, subject_id = _authorized_fixture(tmp_path)
    issued_at = time.time() + 1
    expires_at = issued_at + 10
    server_clock(issued_at)
    first = issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
        expires_at=expires_at,
    )

    assert get_active_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        evaluated_at=expires_at - 0.001,
    ) == first
    assert get_active_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        evaluated_at=expires_at,
    ) is None
    assert get_active_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        evaluated_at=expires_at + 1,
    ) is None

    server_clock(expires_at + 1)
    second = issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )
    assert second["generation"] == 2
    assert [
        row["generation"]
        for row in list_capability_grant_history(
            tmp_path,
            subject_id=subject_id,
            universe_id="universe-a",
            capability=PRIORITY_REQUEST_CAPABILITY,
        )
    ] == [1, 2]


def test_repeat_issue_cannot_silently_change_active_expiry(
    tmp_path: Path,
    server_clock: Callable[[float], None],
) -> None:
    issuer_id, subject_id = _authorized_fixture(tmp_path)
    issued_at = time.time() + 1
    server_clock(issued_at)
    first = issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
        expires_at=issued_at + 60,
    )

    server_clock(issued_at + 1)
    with pytest.raises(ValueError, match="different expiry"):
        issue_priority_grant(
            tmp_path,
            subject_id=subject_id,
            universe_id="universe-a",
            issuer_id=issuer_id,
            expires_at=issued_at + 120,
        )

    assert list_capability_grant_history(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        capability=PRIORITY_REQUEST_CAPABILITY,
    ) == [first]


def test_issue_time_cannot_predate_existing_generation(
    tmp_path: Path,
    server_clock: Callable[[float], None],
) -> None:
    issuer_id, subject_id = _authorized_fixture(tmp_path)
    issued_at = time.time() + 10
    server_clock(issued_at)
    first = issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )

    server_clock(issued_at - 1)
    with pytest.raises(ValueError, match="predate"):
        issue_priority_grant(
            tmp_path,
            subject_id=subject_id,
            universe_id="universe-a",
            issuer_id=issuer_id,
        )

    assert list_capability_grant_history(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        capability=PRIORITY_REQUEST_CAPABILITY,
    ) == [first]


@pytest.mark.parametrize("invalid_time", [float("nan"), float("inf")])
def test_nonfinite_issue_times_fail_without_mutation(
    tmp_path: Path,
    invalid_time: float,
    server_clock: Callable[[float], None],
) -> None:
    issuer_id, subject_id = _authorized_fixture(tmp_path)

    server_clock(invalid_time)
    with pytest.raises(ValueError, match="finite"):
        issue_priority_grant(
            tmp_path,
            subject_id=subject_id,
            universe_id="universe-a",
            issuer_id=issuer_id,
        )
    server_clock(time.time() + 1)
    with pytest.raises(ValueError, match="finite"):
        issue_priority_grant(
            tmp_path,
            subject_id=subject_id,
            universe_id="universe-a",
            issuer_id=issuer_id,
            expires_at=invalid_time,
        )
    assert list_capability_grant_history(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        capability=PRIORITY_REQUEST_CAPABILITY,
    ) == []


def test_revoke_is_exact_prospective_idempotent_and_regrant_increments(
    tmp_path: Path,
    server_clock: Callable[[float], None],
) -> None:
    issuer_id, subject_id = _authorized_fixture(tmp_path)
    issued_at = time.time() + 1
    server_clock(issued_at)
    first = issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )
    revoked_at = issued_at + 2

    server_clock(revoked_at)
    revoked = revoke_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )
    server_clock(revoked_at + 1)
    repeated = revoke_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )
    assert revoked == repeated
    assert revoked["generation"] == first["generation"]
    assert revoked["revoked_at"] == revoked_at
    active_before_revocation = get_active_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        evaluated_at=revoked_at - 0.001,
    )
    assert active_before_revocation is not None
    assert active_before_revocation["generation"] == first["generation"]
    assert active_before_revocation["revoked_at"] == revoked_at
    assert get_active_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        evaluated_at=revoked_at,
    ) is None

    server_clock(revoked_at + 2)
    second = issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )
    assert second["generation"] == 2
    history = list_capability_grant_history(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        capability=PRIORITY_REQUEST_CAPABILITY,
    )
    assert history[0]["revoked_at"] == revoked_at
    assert history[1]["revoked_at"] is None


def test_revoke_cannot_predate_grant(
    tmp_path: Path,
    server_clock: Callable[[float], None],
) -> None:
    issuer_id, subject_id = _authorized_fixture(tmp_path)
    issued_at = time.time() + 10
    server_clock(issued_at)
    first = issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )

    server_clock(issued_at - 1)
    with pytest.raises(ValueError, match="predate"):
        revoke_priority_grant(
            tmp_path,
            subject_id=subject_id,
            universe_id="universe-a",
            issuer_id=issuer_id,
        )

    assert list_capability_grant_history(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        capability=PRIORITY_REQUEST_CAPABILITY,
    ) == [first]


def test_concurrent_repeated_issue_creates_one_active_generation(
    tmp_path: Path,
    server_clock: Callable[[float], None],
) -> None:
    issuer_id, subject_id = _authorized_fixture(tmp_path)
    issued_at = time.time() + 1
    server_clock(issued_at)

    def issue(_index: int) -> dict:
        return issue_priority_grant(
            tmp_path,
            subject_id=subject_id,
            universe_id="universe-a",
            issuer_id=issuer_id,
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(issue, range(24)))

    assert {result["generation"] for result in results} == {1}
    history = list_capability_grant_history(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        capability=PRIORITY_REQUEST_CAPABILITY,
    )
    assert len(history) == 1
    assert history[0]["generation"] == 1


def test_admission_callback_rereads_current_grant_in_write_transaction(
    tmp_path: Path,
    server_clock: Callable[[float], None],
) -> None:
    issuer_id, subject_id = _authorized_fixture(tmp_path)
    issued_at = time.time() + 1
    server_clock(issued_at)
    grant = issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )
    store = RequestAdmissionStore(tmp_path)

    def require_generation(evaluated_at: float):
        def check(conn: sqlite3.Connection) -> None:
            current = active_priority_grant_from_connection(
                conn,
                subject_id=subject_id,
                universe_id="universe-a",
                evaluated_at=evaluated_at,
            )
            if current is None:
                raise CapabilityGrantAuthorizationError(
                    "priority_authorization_required"
                )
            assert current["generation"] == grant["generation"]

        return check

    first = store.commit_admission(
        tenant_id="tenant-a",
        actor_id=subject_id,
        universe_id="universe-a",
        idempotency_key_hash="hmac-sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        body_digest="sha256:body-a",
        body_digest_version="rfc8785-v1",
        request_type="general",
        text="admit while grant is active",
        branch_id="",
        branch_def_id="loop-branch",
        trigger_source="operator_request",
        accepted_priority_weight=50,
        policy_version="operator-priority-v1",
        grant_generation=grant["generation"],
        receipt={},
        directed_daemon_id="",
        created_at="2026-07-24T19:00:00Z",
        authority_check=require_generation(issued_at),
    )
    revoked_at = issued_at + 2
    server_clock(revoked_at)
    revoke_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )

    with pytest.raises(
        CapabilityGrantAuthorizationError,
        match="priority_authorization_required",
    ):
        store.commit_admission(
            tenant_id="tenant-a",
            actor_id=subject_id,
            universe_id="universe-a",
            idempotency_key_hash="hmac-sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
            body_digest="sha256:body-b",
            body_digest_version="rfc8785-v1",
            request_type="general",
            text="must not admit after revocation",
            branch_id="",
            branch_def_id="loop-branch",
            trigger_source="operator_request",
            accepted_priority_weight=50,
            policy_version="operator-priority-v1",
            grant_generation=grant["generation"],
            receipt={},
            directed_daemon_id="",
            created_at="2026-07-24T19:01:00Z",
            authority_check=require_generation(revoked_at),
        )

    with _connect(tmp_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM request_admissions"
        ).fetchone()[0] == 1
        assert conn.execute(
            """
            SELECT admission_id
            FROM request_admissions
            WHERE request_id = ?
            """,
            (first["request_id"],),
        ).fetchone() is not None


@pytest.mark.parametrize(
    ("ordinary_capabilities", "acl_permission"),
    [
        (["write"], "write"),
        (["tinyassets.universe.write"], "admin"),
    ],
)
def test_request_local_verdict_composes_exact_priority_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_clock: Callable[[float], None],
    ordinary_capabilities: list[str],
    acl_permission: str,
) -> None:
    issuer_id, subject_id = _authorized_fixture(tmp_path)
    grant_universe_access(
        tmp_path,
        universe_id="universe-a",
        actor_id=subject_id,
        permission=acl_permission,
        granted_by=issuer_id,
    )
    evaluated_at = time.time() + 1
    server_clock(evaluated_at)
    grant = issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )
    _authenticate_request(
        monkeypatch,
        base_path=tmp_path,
        actor_id=subject_id,
        capabilities=ordinary_capabilities,
    )
    monkeypatch.setattr(
        permissions_api,
        "_now",
        lambda: evaluated_at,
        raising=False,
    )

    verdict = permissions_api.operator_request_admission_verdict(
        "universe-a",
        requested_priority_weight=37.5,
    )

    assert verdict.allowed is True
    assert verdict.error_code == ""
    assert verdict.actor_id == subject_id
    assert verdict.tenant_id == "tenant-a"
    assert verdict.universe_id == "universe-a"
    assert verdict.trigger_source == "operator_request"
    assert verdict.accepted_priority_weight == 37.5
    assert verdict.grant_generation == grant["generation"]
    assert verdict.priority_policy_version == "operator-priority-v1"


def test_zero_weight_is_ordinary_even_with_priority_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_clock: Callable[[float], None],
) -> None:
    issuer_id, subject_id = _authorized_fixture(tmp_path)
    grant_universe_access(
        tmp_path,
        universe_id="universe-a",
        actor_id=subject_id,
        permission="admin",
        granted_by=issuer_id,
    )
    evaluated_at = time.time() + 1
    server_clock(evaluated_at)
    issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )
    _authenticate_request(
        monkeypatch,
        base_path=tmp_path,
        actor_id=subject_id,
        capabilities=["write"],
    )
    monkeypatch.setattr(
        permissions_api,
        "_now",
        lambda: evaluated_at,
        raising=False,
    )

    ordinary = permissions_api.operator_request_admission_verdict(
        "universe-a",
        requested_priority_weight=0.0,
    )
    directed = permissions_api.operator_request_admission_verdict(
        "universe-a",
        requested_priority_weight=0.0,
        directed=True,
    )

    assert ordinary.allowed is True
    assert ordinary.trigger_source == "user_request"
    assert ordinary.accepted_priority_weight == 0.0
    assert ordinary.grant_generation is None
    assert directed.allowed is True
    assert directed.trigger_source == "owner_queued"
    assert directed.accepted_priority_weight == 0.0
    assert directed.grant_generation is None


def test_priority_verdict_rejects_missing_wildcard_and_cross_universe_grants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_clock: Callable[[float], None],
) -> None:
    issuer_id, subject_id = _authorized_fixture(tmp_path)
    _create_universe(tmp_path, "universe-b")
    for universe_id in ("universe-a", "universe-b"):
        grant_universe_access(
            tmp_path,
            universe_id=universe_id,
            actor_id=subject_id,
            permission="write",
            granted_by=issuer_id,
        )
    evaluated_at = time.time() + 1
    server_clock(evaluated_at)
    _authenticate_request(
        monkeypatch,
        base_path=tmp_path,
        actor_id=subject_id,
        capabilities=["write"],
    )
    monkeypatch.setattr(
        permissions_api,
        "_now",
        lambda: evaluated_at,
        raising=False,
    )

    missing = permissions_api.operator_request_admission_verdict(
        "universe-a",
        requested_priority_weight=1.0,
    )
    with _connect(tmp_path) as conn:
        conn.execute(
            """
            INSERT INTO capability_grants (
                user_id, capability, scope, granted_by, created_at,
                expires_at, revoked_at, generation
            ) VALUES (?, ?, '*', ?, ?, NULL, NULL, 1)
            """,
            (
                subject_id,
                PRIORITY_REQUEST_CAPABILITY,
                issuer_id,
                evaluated_at,
            ),
        )
    wildcard = permissions_api.operator_request_admission_verdict(
        "universe-a",
        requested_priority_weight=1.0,
    )
    issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )
    cross_universe = permissions_api.operator_request_admission_verdict(
        "universe-b",
        requested_priority_weight=1.0,
    )

    for verdict in (missing, wildcard, cross_universe):
        assert verdict.allowed is False
        assert verdict.error_code == "priority_authorization_required"
        assert verdict.grant_generation is None


def test_priority_expiry_is_exclusive_at_server_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_clock: Callable[[float], None],
) -> None:
    issuer_id, subject_id = _authorized_fixture(tmp_path)
    grant_universe_access(
        tmp_path,
        universe_id="universe-a",
        actor_id=subject_id,
        permission="write",
        granted_by=issuer_id,
    )
    issued_at = time.time() + 1
    expires_at = issued_at + 10
    server_clock(issued_at)
    issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
        expires_at=expires_at,
    )
    _authenticate_request(
        monkeypatch,
        base_path=tmp_path,
        actor_id=subject_id,
        capabilities=["write"],
    )
    current = [expires_at - 1]
    monkeypatch.setattr(
        permissions_api,
        "_now",
        lambda: current[0],
        raising=False,
    )

    before = permissions_api.operator_request_admission_verdict(
        "universe-a",
        requested_priority_weight=1.0,
    )
    current[0] = expires_at
    at_boundary = permissions_api.operator_request_admission_verdict(
        "universe-a",
        requested_priority_weight=1.0,
    )
    current[0] = expires_at + 1
    after = permissions_api.operator_request_admission_verdict(
        "universe-a",
        requested_priority_weight=1.0,
    )

    assert before.allowed is True
    assert at_boundary.allowed is False
    assert at_boundary.error_code == "priority_authorization_required"
    assert after.allowed is False
    assert after.error_code == "priority_authorization_required"


def test_priority_grant_without_ordinary_authority_is_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_clock: Callable[[float], None],
) -> None:
    from tinyassets.api.universe import _action_submit_request
    from tinyassets.work_targets import REQUESTS_FILENAME

    issuer_id, subject_id = _authorized_fixture(tmp_path)
    (tmp_path / "universe-a" / "PROGRAM.md").write_text(
        "Legacy fixture with an explicit compatibility Loop.",
        encoding="utf-8",
    )
    grant_universe_access(
        tmp_path,
        universe_id="universe-a",
        actor_id=subject_id,
        permission="admin",
        granted_by=issuer_id,
    )
    evaluated_at = time.time() + 1
    server_clock(evaluated_at)
    issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )
    _authenticate_request(
        monkeypatch,
        base_path=tmp_path,
        actor_id=subject_id,
        capabilities=[],
    )
    monkeypatch.setattr(
        permissions_api,
        "_now",
        lambda: evaluated_at,
        raising=False,
    )

    verdict = permissions_api.operator_request_admission_verdict(
        "universe-a",
        requested_priority_weight=1.0,
    )

    assert verdict.allowed is False
    assert verdict.error_code == "universe_access_denied"
    assert verdict.grant_generation is None
    response = _action_submit_request(
        universe_id="universe-a",
        text="a priority grant is not ordinary write authority",
        priority_weight=1.0,
    )
    assert '"error": "universe_access_denied"' in response
    universe_dir = tmp_path / "universe-a"
    assert not (universe_dir / REQUESTS_FILENAME).exists()
    assert read_queue(universe_dir) == []


def test_host_environment_cannot_substitute_for_request_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_clock: Callable[[float], None],
) -> None:
    from tinyassets.api.universe import _action_submit_request
    from tinyassets.work_targets import REQUESTS_FILENAME

    issuer_id, subject_id = _authorized_fixture(tmp_path)
    (tmp_path / "universe-a" / "PROGRAM.md").write_text(
        "Legacy fixture with an explicit compatibility Loop.",
        encoding="utf-8",
    )
    grant_universe_access(
        tmp_path,
        universe_id="universe-a",
        actor_id=subject_id,
        permission="admin",
        granted_by=issuer_id,
    )
    evaluated_at = time.time() + 1
    server_clock(evaluated_at)
    issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )
    _authenticate_request(
        monkeypatch,
        base_path=tmp_path,
        actor_id="anonymous",
        capabilities=["write"],
    )
    monkeypatch.setattr(
        permissions_api,
        "_now",
        lambda: evaluated_at,
        raising=False,
    )
    monkeypatch.setenv("UNIVERSE_SERVER_USER", subject_id)
    monkeypatch.setenv("UNIVERSE_SERVER_HOST_USER", subject_id)
    monkeypatch.setenv(
        "UNIVERSE_SERVER_CAPABILITIES",
        PRIORITY_REQUEST_CAPABILITY,
    )
    universe_dir = tmp_path / "universe-a"

    response = _action_submit_request(
        universe_id="universe-a",
        text="environment identity is not request identity",
        priority_weight=1.0,
    )

    assert '"error": "universe_access_denied"' in response
    assert not (universe_dir / REQUESTS_FILENAME).exists()
    assert read_queue(universe_dir) == []


def test_submit_request_uses_request_identity_and_persists_no_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_clock: Callable[[float], None],
) -> None:
    from tinyassets.api.universe import _action_submit_request
    from tinyassets.work_targets import REQUESTS_FILENAME

    issuer_id, subject_id = _authorized_fixture(tmp_path)
    (tmp_path / "universe-a" / "PROGRAM.md").write_text(
        "Legacy fixture with an explicit compatibility Loop.",
        encoding="utf-8",
    )
    grant_universe_access(
        tmp_path,
        universe_id="universe-a",
        actor_id=subject_id,
        permission="admin",
        granted_by=issuer_id,
    )
    evaluated_at = time.time() + 1
    server_clock(evaluated_at)
    _authenticate_request(
        monkeypatch,
        base_path=tmp_path,
        actor_id=subject_id,
        capabilities=["write"],
    )
    monkeypatch.setattr(
        permissions_api,
        "_now",
        lambda: evaluated_at,
        raising=False,
    )
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "host")
    monkeypatch.setenv(
        "UNIVERSE_SERVER_CAPABILITIES",
        PRIORITY_REQUEST_CAPABILITY,
    )
    universe_dir = tmp_path / "universe-a"

    missing_grant = _action_submit_request(
        universe_id="universe-a",
        text="must not be silently demoted",
        priority_weight=25.0,
    )
    assert '"error": "priority_authorization_required"' in missing_grant
    assert not (universe_dir / REQUESTS_FILENAME).exists()
    assert read_queue(universe_dir) == []

    issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )
    writer_off = _action_submit_request(
        universe_id="universe-a",
        text="must wait for the v2 writer",
        priority_weight=25.0,
    )
    assert '"error": "operator_priority_unavailable"' in writer_off
    assert not (universe_dir / REQUESTS_FILENAME).exists()
    assert read_queue(universe_dir) == []

    ordinary = _action_submit_request(
        universe_id="universe-a",
        text="explicit ordinary opt-out",
        priority_weight=0.0,
    )
    assert '"error"' not in ordinary
    queue = read_queue(universe_dir)
    assert len(queue) == 1
    assert queue[0].trigger_source == "user_request"
    assert queue[0].priority_weight == 0.0
    requests = (universe_dir / REQUESTS_FILENAME).read_text(encoding="utf-8")
    assert f'"source": "{subject_id}"' in requests
    assert '"source": "host"' not in requests


@pytest.mark.parametrize(
    "denial",
    ["unauthenticated", "missing_scope", "missing_acl"],
)
def test_replay_reauthorizes_every_ordinary_leg_before_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    denial: str,
) -> None:
    from tinyassets.api.universe import _lookup_operator_request_replay

    issuer_id, subject_id = _authorized_fixture(tmp_path)
    if denial != "missing_acl":
        grant_universe_access(
            tmp_path,
            universe_id="universe-a",
            actor_id=subject_id,
            permission="write",
            granted_by=issuer_id,
        )
    _authenticate_request(
        monkeypatch,
        base_path=tmp_path,
        actor_id=(
            "anonymous" if denial == "unauthenticated" else subject_id
        ),
        capabilities=[] if denial == "missing_scope" else ["write"],
    )

    class NoLookupStore:
        def lookup_replay(self, **_kwargs):
            pytest.fail("replay lookup ran before ordinary reauthorization")

    result = _lookup_operator_request_replay(
        NoLookupStore(),
        universe_id="universe-a",
        idempotency_key_hash="hmac-sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        body_digest="sha256:body-a",
        body_digest_version="rfc8785-v1",
    )

    assert result == {"error": "universe_access_denied"}


def test_replay_after_acl_loss_is_non_enumerating_and_mutation_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_clock: Callable[[float], None],
) -> None:
    from tinyassets.api.universe import _lookup_operator_request_replay

    issuer_id, subject_id = _authorized_fixture(tmp_path)
    grant_universe_access(
        tmp_path,
        universe_id="universe-a",
        actor_id=subject_id,
        permission="write",
        granted_by=issuer_id,
    )
    issued_at = time.time() + 1
    server_clock(issued_at)
    grant = issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
    )
    store = RequestAdmissionStore(tmp_path)
    _commit_priority_admission(
        store,
        actor_id=subject_id,
        grant_generation=grant["generation"],
    )
    _authenticate_request(
        monkeypatch,
        base_path=tmp_path,
        actor_id=subject_id,
        capabilities=["write"],
    )
    assert revoke_universe_access(
        tmp_path,
        universe_id="universe-a",
        actor_id=subject_id,
    )
    monkeypatch.setattr(
        store,
        "lookup_replay",
        lambda **_kwargs: pytest.fail(
            "ACL-lost replay disclosed key existence"
        ),
    )

    result = _lookup_operator_request_replay(
        store,
        universe_id="universe-a",
        idempotency_key_hash="hmac-sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        body_digest="sha256:body-a",
        body_digest_version="rfc8785-v1",
    )

    assert result == {"error": "universe_access_denied"}
    with _connect(tmp_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM request_admissions"
        ).fetchone()[0] == 1


@pytest.mark.parametrize("priority_loss", ["revoked", "expired"])
def test_priority_only_loss_preserves_replay_but_blocks_new_priority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_clock: Callable[[float], None],
    priority_loss: str,
) -> None:
    from tinyassets.api.universe import _lookup_operator_request_replay

    issuer_id, subject_id = _authorized_fixture(tmp_path)
    grant_universe_access(
        tmp_path,
        universe_id="universe-a",
        actor_id=subject_id,
        permission="write",
        granted_by=issuer_id,
    )
    issued_at = time.time() + 1
    expires_at = issued_at + 2 if priority_loss == "expired" else None
    server_clock(issued_at)
    grant = issue_priority_grant(
        tmp_path,
        subject_id=subject_id,
        universe_id="universe-a",
        issuer_id=issuer_id,
        expires_at=expires_at,
    )
    store = RequestAdmissionStore(tmp_path)
    first = _commit_priority_admission(
        store,
        actor_id=subject_id,
        grant_generation=grant["generation"],
    )
    _authenticate_request(
        monkeypatch,
        base_path=tmp_path,
        actor_id=subject_id,
        capabilities=["write"],
    )
    evaluated_at = issued_at + 2
    server_clock(evaluated_at)
    monkeypatch.setattr(
        permissions_api,
        "_now",
        lambda: evaluated_at,
        raising=False,
    )
    if priority_loss == "revoked":
        revoke_priority_grant(
            tmp_path,
            subject_id=subject_id,
            universe_id="universe-a",
            issuer_id=issuer_id,
        )

    replay = _lookup_operator_request_replay(
        store,
        universe_id="universe-a",
        idempotency_key_hash="hmac-sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        body_digest="sha256:body-a",
        body_digest_version="rfc8785-v1",
    )
    new_admission = permissions_api.operator_request_admission_verdict(
        "universe-a",
        requested_priority_weight=50,
    )

    assert replay == {**first, "idempotent_replay": True}
    assert new_admission.allowed is False
    assert new_admission.error_code == "priority_authorization_required"
