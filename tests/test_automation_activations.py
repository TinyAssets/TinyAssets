from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tinyassets.execution_subject import (
    ExecutionSubject,
    ExecutionSubjectKind,
)
from tinyassets.storage import db_path
from tinyassets.storage.automation_activations import (
    _CREATE_TABLE,
    AutomationActivation,
    AutomationActivationExecutor,
    AutomationActivationState,
    AutomationActivationStore,
)

NOW = datetime(2026, 7, 30, 20, 0, tzinfo=timezone.utc)


def _branch_subject(
    ref: str = "branch-spec-drain@abc12345",
    digest: str = f"sha256:{'a' * 64}",
) -> ExecutionSubject:
    return ExecutionSubject(
        kind=ExecutionSubjectKind.BRANCH_VERSION,
        ref=ref,
        digest=digest,
    )


def _store(base_path: Path) -> AutomationActivationStore:
    return AutomationActivationStore(base_path, clock=lambda: NOW)


def _cloud_activation(
    store: AutomationActivationStore,
) -> AutomationActivation:
    stopped = store.create_stopped(
        universe_id="universe-main",
        automation_id="automation-spec-drain",
    )
    activated = store.activate(
        expected=stopped,
        executor_class=AutomationActivationExecutor.CLOUD,
        subject=_branch_subject(),
        lease_id="activation-lease-cloud-1",
    )
    assert activated is not None
    return activated


def test_create_stopped_is_idempotent_and_server_authoritative(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    first = store.create_stopped(
        universe_id="universe-main",
        automation_id="automation-spec-drain",
    )
    replay = store.create_stopped(
        universe_id="universe-main",
        automation_id="automation-spec-drain",
    )

    assert first == replay
    assert first == AutomationActivation(
        universe_id="universe-main",
        automation_id="automation-spec-drain",
        epoch=0,
        executor_class=None,
        subject=None,
        lease_id=None,
        state=AutomationActivationState.STOPPED,
        updated_at="2026-07-30T20:00:00.000000Z",
    )


def test_transactional_activation_runs_authority_fence_before_transition(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    stopped = store.create_stopped(
        universe_id="universe-main",
        automation_id="automation-spec-drain",
    )
    observed: list[sqlite3.Connection] = []

    with store.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")

        def deny(connection: sqlite3.Connection) -> bool:
            observed.append(connection)
            assert connection is conn
            assert connection.in_transaction
            return False

        activated = store.activate_in_transaction(
            conn,
            expected=stopped,
            executor_class=AutomationActivationExecutor.CLOUD,
            subject=_branch_subject(),
            lease_id="activation-lease-cloud-1",
            authority_check=deny,
        )
        conn.commit()

    assert activated is None
    assert len(observed) == 1
    assert store.get(stopped.universe_id, stopped.automation_id) == stopped


def test_active_record_requires_exact_executor_version_and_lease(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    active = _cloud_activation(store)

    assert active.epoch == 1
    assert active.state is AutomationActivationState.ACTIVE
    assert store.validate_claim(
        universe_id=active.universe_id,
        automation_id=active.automation_id,
        epoch=active.epoch,
        executor_class=active.executor_class,
        subject=active.subject,
        lease_id=active.lease_id,
    )
    for changed in (
        {"universe_id": "universe-other"},
        {"automation_id": "automation-other"},
        {"epoch": active.epoch + 1},
        {"executor_class": AutomationActivationExecutor.TRAY},
        {"subject": _branch_subject(ref="branch-spec-drain@def67890")},
        {"lease_id": "activation-lease-other"},
    ):
        claim = {
            "universe_id": active.universe_id,
            "automation_id": active.automation_id,
            "epoch": active.epoch,
            "executor_class": active.executor_class,
            "subject": active.subject,
            "lease_id": active.lease_id,
        }
        claim.update(changed)
        assert not store.validate_claim(**claim)


def test_cloud_cannot_activate_while_tray_activation_is_current(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    stopped = store.create_stopped(
        universe_id="universe-main",
        automation_id="automation-spec-drain",
    )
    tray = store.activate(
        expected=stopped,
        executor_class=AutomationActivationExecutor.TRAY,
        subject=_branch_subject(),
        lease_id="activation-lease-tray-1",
    )
    assert tray is not None

    cloud = store.activate(
        expected=tray,
        executor_class=AutomationActivationExecutor.CLOUD,
        subject=_branch_subject(ref="branch-spec-drain@def67890"),
        lease_id="activation-lease-cloud-1",
    )

    assert cloud is None
    assert store.get(tray.universe_id, tray.automation_id) == tray


def test_tray_rollback_requires_cloud_to_be_durably_stopped(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    cloud = _cloud_activation(store)

    assert store.activate(
        expected=cloud,
        executor_class=AutomationActivationExecutor.TRAY,
        subject=_branch_subject(),
        lease_id="activation-lease-tray-1",
    ) is None

    stopped = store.stop(expected=cloud)
    assert stopped is not None
    tray = store.activate(
        expected=stopped,
        executor_class=AutomationActivationExecutor.TRAY,
        subject=_branch_subject(),
        lease_id="activation-lease-tray-1",
    )

    assert tray is not None
    assert tray.epoch == cloud.epoch + 2
    assert tray.executor_class is AutomationActivationExecutor.TRAY


def test_stop_fences_cached_claim_and_clears_active_identity(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    active = _cloud_activation(store)

    stopped = store.stop(expected=active)

    assert stopped is not None
    assert stopped.epoch == active.epoch + 1
    assert stopped.state is AutomationActivationState.STOPPED
    assert stopped.executor_class is None
    assert stopped.subject is None
    assert stopped.lease_id is None
    assert not store.validate_claim(
        universe_id=active.universe_id,
        automation_id=active.automation_id,
        epoch=active.epoch,
        executor_class=active.executor_class,
        subject=active.subject,
        lease_id=active.lease_id,
    )


def test_rebind_advances_epoch_and_fences_old_version(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    active = _cloud_activation(store)

    rebound = store.rebind(
        expected=active,
        subject=_branch_subject(ref="branch-spec-drain@def67890"),
        lease_id="activation-lease-cloud-2",
    )

    assert rebound is not None
    assert rebound.epoch == active.epoch + 1
    assert rebound.executor_class is active.executor_class
    assert not store.validate_claim(
        universe_id=active.universe_id,
        automation_id=active.automation_id,
        epoch=active.epoch,
        executor_class=active.executor_class,
        subject=active.subject,
        lease_id=active.lease_id,
    )


def test_competing_cloud_versions_have_one_cas_winner(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    stopped = store.create_stopped(
        universe_id="universe-main",
        automation_id="automation-spec-drain",
    )

    def activate(version: str) -> AutomationActivation | None:
        return store.activate(
            expected=stopped,
            executor_class=AutomationActivationExecutor.CLOUD,
            subject=_branch_subject(ref=version),
            lease_id=f"lease-{version}",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                activate,
                (
                    "branch-spec-drain@abc12345",
                    "branch-spec-drain@def67890",
                ),
            )
        )

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert store.get("universe-main", "automation-spec-drain") == winners[0]


def test_stale_expected_record_cannot_stop_or_rebind(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    active = _cloud_activation(store)
    rebound = store.rebind(
        expected=active,
        subject=_branch_subject(ref="branch-spec-drain@def67890"),
        lease_id="activation-lease-cloud-2",
    )
    assert rebound is not None

    assert store.stop(expected=active) is None
    assert store.rebind(
        expected=active,
        subject=_branch_subject(ref="branch-spec-drain@fedcba98"),
        lease_id="activation-lease-cloud-3",
    ) is None
    assert store.get(active.universe_id, active.automation_id) == rebound


@pytest.mark.parametrize(
    "field",
    ("subject", "lease_id"),
)
def test_activate_rejects_blank_active_identity(
    tmp_path: Path,
    field: str,
) -> None:
    store = _store(tmp_path)
    stopped = store.create_stopped(
        universe_id="universe-main",
        automation_id="automation-spec-drain",
    )

    with pytest.raises(ValueError, match=field):
        store.activate(
            expected=stopped,
            executor_class=AutomationActivationExecutor.CLOUD,
            subject=("" if field == "subject" else _branch_subject()),  # type: ignore[arg-type]
            lease_id=(
                "" if field == "lease_id" else "activation-lease-cloud-1"
            ),
        )


def test_create_rejects_blank_keys(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="universe_id"):
        store.create_stopped(universe_id="", automation_id="automation")
    with pytest.raises(ValueError, match="automation_id"):
        store.create_stopped(universe_id="universe", automation_id="")


def test_legacy_active_activation_migration_fails_without_rewriting_row(
    tmp_path: Path,
) -> None:
    path = db_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE automation_activations (
                universe_id TEXT NOT NULL,
                automation_id TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                executor_class TEXT,
                immutable_branch_version TEXT,
                lease_id TEXT,
                state TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (universe_id, automation_id)
            );
            INSERT INTO automation_activations VALUES (
                'universe-main', 'automation-spec-drain', 7, 'cloud',
                'branch-spec-drain@legacy', 'lease-legacy', 'active',
                '2026-07-30T20:00:00.000000Z'
            );
            """
        )

    with pytest.raises(RuntimeError, match="legacy active automation activation"):
        _store(tmp_path).get("universe-main", "automation-spec-drain")

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT epoch, state, immutable_branch_version FROM automation_activations"
        ).fetchone()
        columns = {
            str(item[1])
            for item in connection.execute("PRAGMA table_info(automation_activations)")
        }
    assert row == (7, "active", "branch-spec-drain@legacy")
    assert "subject_kind" not in columns


def test_legacy_stopped_activation_migrates_without_changing_epoch(
    tmp_path: Path,
) -> None:
    path = db_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE automation_activations (
                universe_id TEXT NOT NULL,
                automation_id TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                executor_class TEXT,
                immutable_branch_version TEXT,
                lease_id TEXT,
                state TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (universe_id, automation_id)
            );
            INSERT INTO automation_activations VALUES (
                'universe-main', 'automation-spec-drain', 8, NULL,
                NULL, NULL, 'stopped', '2026-07-30T20:00:00.000000Z'
            );
            """
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        migrated_records = tuple(
            pool.map(
                lambda _index: _store(tmp_path).get(
                    "universe-main",
                    "automation-spec-drain",
                ),
                range(8),
            )
        )
    assert len(set(migrated_records)) == 1
    migrated = migrated_records[0]
    assert migrated is not None
    assert migrated.epoch == 8
    assert migrated.state is AutomationActivationState.STOPPED
    assert migrated.subject is None
    active = _store(tmp_path).activate(
        expected=migrated,
        executor_class=AutomationActivationExecutor.CLOUD,
        subject=_branch_subject(),
        lease_id="activation-lease-cloud-9",
    )
    assert active is not None
    assert active.epoch == 9
    assert active.subject == _branch_subject()


def test_retired_agent_subject_check_is_rebuilt_without_losing_rows(tmp_path: Path) -> None:
    """C8: the ``agent_runtime_manifest`` subject leaves the CHECK; rows and epochs stay."""
    path = db_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old_ddl = _CREATE_TABLE.replace(
        "CHECK (subject_kind IN ('branch_version')),",
        "CHECK (subject_kind IN ('branch_version', 'agent_runtime_manifest')),",
    ).replace(
        "            AND subject_kind = 'branch_version'\n"
        "            AND immutable_branch_version = subject_ref\n",
        "            AND ((subject_kind = 'branch_version'"
        " AND immutable_branch_version = subject_ref)"
        " OR (subject_kind = 'agent_runtime_manifest' AND immutable_branch_version IS NULL))\n",
    )
    assert "agent_runtime_manifest" in old_ddl
    with sqlite3.connect(path) as connection:
        connection.executescript(old_ddl + ";" + """
            CREATE INDEX idx_activation_state ON automation_activations(state);
            INSERT INTO automation_activations VALUES (
                'universe-main', 'automation-spec-drain', 8, 'cloud', 'branch_version',
                'branch-version-1', 'sha256:""" + "a" * 64 + """', 'branch-version-1',
                'lease-1', 'active', '2026-07-30T20:00:00.000000Z'
            );
            INSERT INTO automation_activations VALUES (
                'universe-main', 'automation-idle', 3, NULL, NULL, NULL, NULL, NULL, NULL,
                'stopped', '2026-07-30T20:00:00.000000Z'
            );
            """)

    active = _store(tmp_path).get("universe-main", "automation-spec-drain")
    assert active is not None and active.epoch == 8 and active.state.value == "active"
    assert _store(tmp_path).get("universe-main", "automation-idle").epoch == 3
    with sqlite3.connect(path) as connection:
        ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'automation_activations'"
        ).fetchone()[0]
        indexes = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}
    assert "agent_runtime_manifest" not in ddl
    assert "idx_activation_state" in indexes
    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE automation_activations SET subject_kind = 'agent_runtime_manifest', "
            "immutable_branch_version = NULL WHERE automation_id = 'automation-spec-drain'")


def test_an_active_retired_agent_subject_blocks_the_rebuild(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old_ddl = _CREATE_TABLE.replace(
        "CHECK (subject_kind IN ('branch_version')),",
        "CHECK (subject_kind IN ('branch_version', 'agent_runtime_manifest')),",
    ).replace(
        "            AND subject_kind = 'branch_version'\n"
        "            AND immutable_branch_version = subject_ref\n",
        "            AND ((subject_kind = 'branch_version'"
        " AND immutable_branch_version = subject_ref)"
        " OR (subject_kind = 'agent_runtime_manifest' AND immutable_branch_version IS NULL))\n",
    )
    with sqlite3.connect(path) as connection:
        connection.executescript(old_ddl + ";" + """
            INSERT INTO automation_activations VALUES (
                'universe-main', 'automation_agent_x', 1, 'cloud', 'agent_runtime_manifest',
                'manifest-1', 'sha256:""" + "b" * 64 + """', NULL,
                'lease-1', 'active', '2026-07-30T20:00:00.000000Z'
            );
            """)
    with pytest.raises(RuntimeError, match="agent-manifest activations remain"):
        _store(tmp_path).get("universe-main", "automation_agent_x")
