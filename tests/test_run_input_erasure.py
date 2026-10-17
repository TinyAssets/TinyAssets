"""Common run envelope participates in existing reset/account lifecycle."""

from tinyassets import account_deletion, daemon_server, runs, scoped_reset
from tinyassets.storage import run_input_admissions as admissions


def test_empty_admission_table_is_classified_root_run_history(tmp_path):
    runs.initialize_runs_db(tmp_path)
    with runs._connect(tmp_path) as conn:
        admissions.ensure_schema(conn)
    blockers = scoped_reset._inspect_root_runs(tmp_path, principal="owner")
    assert not any("run_input_admissions" in item for item in blockers)


def test_real_account_erasure_follows_envelope_owner_after_home_rebind(tmp_path):
    from tests.test_account_deletion import _seed_user

    for owner, home in [("owner", "u-old"), ("peer", "u-peer")]:
        _seed_user(tmp_path, owner, home)
        run_id = runs.create_run(
            tmp_path,
            branch_def_id="branch",
            thread_id="",
            inputs={"private": owner},
            actor=owner,
            owner_user_id=owner,
        )
        with runs._connect(tmp_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE runs SET queue_universe_id=? WHERE run_id=?", (home, run_id))
            admissions.ensure_schema(conn)
            admissions.accept_in_transaction(
                conn,
                run_id=run_id,
                owner_id=owner,
                universe_id=home,
                snapshot={"branch_def_id": "branch"},
            )
        runs.update_run_status(tmp_path, run_id, status="completed")
    (tmp_path / "u-next").mkdir()
    daemon_server.set_founder_home(
        tmp_path, founder_sub="owner", universe_id="u-next", platform_generated=True
    )
    receipt = account_deletion.delete_account(tmp_path, founder_sub="owner")
    assert receipt["unfinished_phases"] == []
    with runs._connect(tmp_path) as conn:
        assert [row[0] for row in conn.execute("SELECT owner_id FROM run_input_admissions")] == [
            "peer"
        ]
        assert [row[0] for row in conn.execute("SELECT owner_user_id FROM runs")] == ["peer"]
