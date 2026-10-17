"""Account file erasure follows custody owner, not a mutable home binding."""

import pytest

from tests.test_account_deletion import _seed_user
from tinyassets import account_deletion, daemon_server, runs, scoped_reset
from tinyassets.authoring import service
from tinyassets.authoring.store import AuthoringStore
from tinyassets.run_file_capture import capture_authoring_files
from tinyassets.storage import run_files
from tinyassets.storage.run_file_lock import try_file_operation_lock


@pytest.fixture
def two_owners(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_RUN_FILE_CUSTODY_MAX_BYTES", "1048576")
    store = AuthoringStore(tmp_path)
    refs = {}
    for owner, home in (("owner", "u-old"), ("peer", "u-peer")):
        _seed_user(tmp_path, owner, home)
        store.initialize()
        session = service.start_session(
            actor_id=owner, artifact_kind="node", sketch="files", store=store
        )["session_id"]
        handle = store.put_file_handle(
            session_id=session,
            owner_id=owner,
            input_name="file",
            filename="original.bin",
            media_type="application/octet-stream",
            content=b"same independent bytes",
            lifetime_seconds=3600,
        )
        refs[owner] = capture_authoring_files(
            tmp_path,
            owner_id=owner,
            universe_id=home,
            label="capture",
            sources=[{"session_id": session, "handle_id": handle["handle_id"]}],
        )[0]
        run_id = runs.create_run(
            tmp_path,
            branch_def_id="branch",
            thread_id="",
            inputs={},
            actor=owner,
            owner_user_id=owner,
            queue_universe_id=home,
        )
        with runs._connect(tmp_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            run_files.bind_in_transaction(
                conn,
                run_id=run_id,
                owner_id=owner,
                universe_id=home,
                field_name="file",
                file_ids=[refs[owner]["file_id"]],
            )
        runs.update_run_status(tmp_path, run_id, status="completed")
    (tmp_path / "u-next").mkdir()
    daemon_server.set_founder_home(
        tmp_path, founder_sub="owner", universe_id="u-next", platform_generated=True
    )
    return tmp_path, refs


def erase(base):
    return account_deletion.delete_account(base, founder_sub="owner")


def test_real_erasure_removes_only_owned_bodies_and_rows_after_rebind(two_owners):
    base, refs = two_owners
    receipt = erase(base)
    assert receipt["unfinished_phases"] == []
    assert not (base / ".run-file-custody" / (refs["owner"]["file_id"] + ".body")).exists()
    assert (base / ".run-file-custody" / (refs["peer"]["file_id"] + ".body")).read_bytes() == (
        b"same independent bytes"
    )
    with runs._connect(base) as conn:
        assert [row[0] for row in conn.execute("SELECT owner_id FROM run_file_operations")] == [
            "peer"
        ]
        assert [row[0] for row in conn.execute("SELECT owner_id FROM run_file_objects")] == ["peer"]
        assert conn.execute("SELECT COUNT(*) FROM run_file_bindings").fetchone()[0] == 1


def test_failed_erasure_preserves_inventory_and_debt_until_tick_and_retry(two_owners, monkeypatch):
    from tinyassets import run_file_erasure, run_file_retention

    base, refs = two_owners

    def fail(*args, **kwargs):
        raise OSError("fixture physical cleanup unavailable")

    with monkeypatch.context() as changes:
        changes.setattr(run_file_erasure, "cleanup_file_operation", fail)
        assert "store:runs" in erase(base)["unfinished_phases"]
    with runs._connect(base) as conn:
        row = conn.execute("SELECT * FROM run_file_operations WHERE owner_id='owner'").fetchone()
        assert row["state"] == "cleanup" and refs["owner"]["file_id"] in row["inventory_json"]
        assert (
            conn.execute(
                "SELECT amount FROM run_file_allocations WHERE operation_id=?",
                (row["operation_id"],),
            ).fetchone()[0]
            > 0
        )
    run_file_retention.reconcile_run_files(base)
    assert not (base / ".run-file-custody" / (refs["owner"]["file_id"] + ".body")).exists()
    assert erase(base)["unfinished_phases"] == []
    assert (base / ".run-file-custody" / (refs["peer"]["file_id"] + ".body")).exists()


def test_busy_live_operation_cannot_be_deleted_by_erasure(two_owners):
    from tinyassets.run_file_retention import reconcile_run_files

    base, refs = two_owners
    with runs._connect(base) as conn:
        operation = conn.execute(
            "SELECT operation_id FROM run_file_operations WHERE owner_id='owner'"
        ).fetchone()[0]
    with try_file_operation_lock(base, operation_id=operation):
        assert "store:runs" in erase(base)["unfinished_phases"]
        reconcile_run_files(base)
        assert (base / ".run-file-custody" / (refs["owner"]["file_id"] + ".body")).exists()
    reconcile_run_files(base)
    assert not (base / ".run-file-custody" / (refs["owner"]["file_id"] + ".body")).exists()
    assert erase(base)["unfinished_phases"] == []


def test_file_tables_classified_as_preserved_root_history(two_owners):
    base, _ = two_owners
    assert not any(
        "run_file_" in row for row in scoped_reset._inspect_root_runs(base, principal="owner")
    )


def test_erasure_helper_requires_actual_account_tombstone(two_owners):
    from tinyassets.run_file_erasure import erase_owner_files

    base, refs = two_owners
    with pytest.raises(run_files.FileCustodyRefused, match="requires_tombstone"):
        erase_owner_files(base, owner_id="owner")
    assert (base / ".run-file-custody" / (refs["owner"]["file_id"] + ".body")).exists()
