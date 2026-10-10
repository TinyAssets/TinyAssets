"""Real database request correlation, no provider stubs or launch authority."""

import json
import multiprocessing
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from tinyassets import account_deletion, conversation_store
from tinyassets.daemon_server import grant_universe_access, set_founder_home
from tinyassets.runs import runs_db_path
from tinyassets.storage import db_path

OWNER, HOME = "user-consumer", "u-consumer"


@pytest.fixture
def store(tmp_path):
    from tinyassets.storage import conversation_run_admissions as cr

    (tmp_path / HOME).mkdir()
    set_founder_home(tmp_path, founder_sub=OWNER, universe_id=HOME, platform_generated=True)
    grant_universe_access(tmp_path, universe_id=HOME, actor_id=OWNER,
                          permission="admin", granted_by=OWNER)
    cr.initialize(tmp_path)
    return tmp_path


def reserve(base, key, *, message="hello", context=None, model_choice=None,
            owner=OWNER, home=HOME):
    from tinyassets.storage import conversation_run_admissions as cr

    with cr.authorized_scope(base, owner=owner, universe=home) as scope:
        with cr.runs_transaction(scope) as conn:
            return cr.reserve_in_transaction(
                conn, scope, request_key=key,
                intent={"version": 1, "message": message, "input_method": "typed",
                        "model_choice": model_choice, "binding_id": "b", "binding_revision": 1},
                context=context or {"version": 1, "history": [], "preferences": None},
                selection={"version": 1, "branch_def_id": "test-branch",
                           "branch_version_id": None, "reply_key": "reply"},
                inputs={"message": message},
            )


def complete(base, run_id, reply="answer"):
    with sqlite3.connect(runs_db_path(base)) as conn:
        conn.execute("UPDATE runs SET status='completed', output_json=? WHERE run_id=?",
                     (json.dumps({"reply": reply}), run_id))


def project(base, admission_id):
    from tinyassets.storage import conversation_run_admissions as cr

    with cr.authorized_scope(base, owner=OWNER, universe=HOME) as scope:
        return cr.project_terminal(scope, admission_id)


def test_same_key_race_has_one_atomic_run_and_admission(store):
    key = str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: reserve(store, key), range(4)))
    assert len({r["run_id"] for r in rows}) == 1
    assert len({r["admission_id"] for r in rows}) == 1
    with sqlite3.connect(runs_db_path(store)) as conn:
        assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM conversation_run_admissions").fetchone()[0] == 1


def test_changed_server_history_and_defaults_do_not_change_replay(store):
    key = str(uuid.uuid4())
    first = reserve(store, key)
    again = reserve(store, key, context={"version": 1, "history": ["later"],
                                       "preferences": {"changed": True}})
    assert again["run_id"] == first["run_id"]
    assert again["context_json"] == first["context_json"]


def test_changed_caller_intent_conflicts_without_run(store):
    from tinyassets.storage import conversation_run_admissions as cr

    key = str(uuid.uuid4())
    reserve(store, key)
    with pytest.raises(cr.IntentConflict):
        reserve(store, key, message="changed")
    with sqlite3.connect(runs_db_path(store)) as conn:
        assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 1


def test_rollback_removes_both_records(store):
    from tinyassets.storage import conversation_run_admissions as cr

    with cr.authorized_scope(store, owner=OWNER, universe=HOME) as scope:
        with pytest.raises(RuntimeError, match="crash"):
            with cr.runs_transaction(scope) as conn:
                cr.reserve_in_transaction(
                    conn, scope, request_key=str(uuid.uuid4()),
                    intent={"version": 1, "message": "hi", "input_method": "typed",
                            "model_choice": None, "binding_id": "b", "binding_revision": 1},
                    context={"version": 1, "history": [], "preferences": None},
                    selection={"version": 1, "branch_def_id": "test-branch",
                               "branch_version_id": None, "reply_key": "reply"}, inputs={})
                raise RuntimeError("crash")
    with sqlite3.connect(runs_db_path(store)) as conn:
        assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


def test_crash_after_pair_before_projection_flag_repairs_once(store, monkeypatch):
    from tinyassets.storage import conversation_run_admissions as cr

    row = reserve(store, str(uuid.uuid4()))
    complete(store, row["run_id"])
    original = cr._mark_projected
    monkeypatch.setattr(cr, "_mark_projected", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("crash gap")))
    with pytest.raises(RuntimeError, match="crash gap"):
        project(store, row["admission_id"])
    monkeypatch.setattr(cr, "_mark_projected", original)
    assert project(store, row["admission_id"])["projection_state"] == "committed"
    assert project(store, row["admission_id"])["projection_state"] == "committed"
    assert [m.text for m in conversation_store.load_recent(store / HOME, f"principal:{OWNER}")] == [
        "hello", "answer"]


def test_trimmed_projection_expires_details_without_reappend(store):
    row = reserve(store, str(uuid.uuid4()))
    complete(store, row["run_id"])
    project(store, row["admission_id"])
    with sqlite3.connect(store / HOME / ".conversation_memory.db") as conn:
        conn.execute("DELETE FROM conversation_turns")
    expired = project(store, row["admission_id"])
    assert expired["projection_state"] == "expired"
    assert expired["terminal_json"] is None
    assert expired["intent_json"] is None
    assert expired["selection_json"] == "{}"
    assert expired["conversation_turn_no"] is None
    assert conversation_store.load_recent(store / HOME, f"principal:{OWNER}") == []


@pytest.mark.parametrize("mutation", [
    "DELETE FROM founder_home", "UPDATE universe_acl SET permission='read'",
])
def test_revoked_scope_refuses_projection(store, mutation):
    row = reserve(store, str(uuid.uuid4()))
    complete(store, row["run_id"])
    with sqlite3.connect(db_path(store)) as conn:
        conn.execute(mutation)
    with pytest.raises((PermissionError, RuntimeError)):
        project(store, row["admission_id"])
    assert not (store / HOME / ".conversation_memory.db").exists()


def test_deleted_home_is_not_recreated_by_delayed_projection(store):
    row = reserve(store, str(uuid.uuid4()))
    complete(store, row["run_id"])
    result = account_deletion.delete_account(
        store, founder_sub=OWNER, cancel_billing=lambda _: "none",
        delete_identity=lambda _: "deleted")
    assert result["unfinished_phases"] == []
    with sqlite3.connect(runs_db_path(store)) as conn:
        assert conn.execute("SELECT count(*) FROM conversation_run_admissions").fetchone()[0] == 0
    with pytest.raises((PermissionError, RuntimeError)):
        project(store, row["admission_id"])
    assert not (store / HOME).exists()


def _projection_process(base, admission_id, entered, release, result):
    from tinyassets.storage import conversation_run_admissions as cr

    original = cr._write_pair
    def paused(*args, **kwargs):
        entered.set()
        if not release.wait(8):
            raise RuntimeError("test release missing")
        return original(*args, **kwargs)
    cr._write_pair = paused
    try:
        project(base, admission_id)
        result.put("projected")
    except Exception as exc:
        result.put(type(exc).__name__ + ": " + str(exc))


def _deletion_process(base, result):
    # Spawn does not inherit pytest's runtime doubles. Install the same venue
    # in this child; the real account-deletion/projection barrier remains intact.
    from tests.support.admission_double import install
    from tests.support.broker_double import _Registry
    from tinyassets.broker import supervisor

    registry = _Registry()
    patch = pytest.MonkeyPatch()
    install(patch)
    patch.setattr(supervisor, "get_supervisor", registry.supervisor_for)
    try:
        receipt = account_deletion.delete_account(
            base, founder_sub=OWNER, cancel_billing=lambda _: "none",
            delete_identity=lambda _: "deleted")
        if receipt["unfinished_phases"]:
            raise RuntimeError(str(receipt["unfinished_phases"]))
        result.put("deleted")
    except Exception as exc:
        result.put(type(exc).__name__ + ": " + str(exc))

    finally:
        registry.close()
        patch.undo()


def test_two_process_delete_cannot_overtake_terminal_projection(store):
    row = reserve(store, str(uuid.uuid4()))
    complete(store, row["run_id"])
    ctx = multiprocessing.get_context("spawn")
    entered, release, results = ctx.Event(), ctx.Event(), ctx.Queue()
    writer = ctx.Process(target=_projection_process,
                         args=(store, row["admission_id"], entered, release, results))
    deleting = ctx.Process(target=_deletion_process, args=(store, results))
    writer.start()
    try:
        assert entered.wait(8)
        deleting.start()
        deleting.join(0.3)
        assert deleting.is_alive(), "deletion overtook author-db projection fence"
    finally:
        release.set()
        writer.join(10)
        if deleting.pid:
            deleting.join(10)
        for process in (writer, deleting):
            if process.pid and process.is_alive():
                process.terminate()
                process.join(3)
    assert writer.exitcode == deleting.exitcode == 0
    assert {results.get(timeout=2), results.get(timeout=2)} == {"projected", "deleted"}
    assert not (store / HOME).exists()


def test_nonterminal_does_not_project_or_create_success(store):
    from tinyassets.storage import conversation_run_admissions as cr

    row = reserve(store, str(uuid.uuid4()))
    with pytest.raises(cr.TerminalUnavailable):
        project(store, row["admission_id"])
    assert not (store / HOME / ".conversation_memory.db").exists()


def test_explicit_model_choice_change_conflicts(store):
    from tinyassets.storage import conversation_run_admissions as cr

    key = str(uuid.uuid4())
    reserve(store, key)
    with pytest.raises(cr.IntentConflict):
        reserve(store, key, model_choice={"version": 1, "mode": "automatic"})


def test_another_owner_cannot_read_or_project_a_turn(store):
    from tinyassets.storage import conversation_run_admissions as cr

    row = reserve(store, str(uuid.uuid4()))
    complete(store, row["run_id"])
    (store / "u-other").mkdir()
    set_founder_home(store, founder_sub="other", universe_id="u-other", platform_generated=True)
    grant_universe_access(store, universe_id="u-other", actor_id="other",
                          permission="admin", granted_by="other")
    with cr.authorized_scope(store, owner="other", universe="u-other") as scope:
        with pytest.raises(PermissionError):
            cr.project_terminal(scope, row["admission_id"])
    assert not (store / "u-other" / ".conversation_memory.db").exists()


def test_changed_run_owner_refuses_private_projection(store):
    row = reserve(store, str(uuid.uuid4()))
    complete(store, row["run_id"])
    with sqlite3.connect(runs_db_path(store)) as conn:
        conn.execute("UPDATE runs SET owner_user_id='other' WHERE run_id=?", (row["run_id"],))
    with pytest.raises(PermissionError):
        project(store, row["admission_id"])


def test_frozen_terminal_is_not_replaced_by_later_run_output(store):
    row = reserve(store, str(uuid.uuid4()))
    complete(store, row["run_id"])
    before = project(store, row["admission_id"])
    complete(store, row["run_id"], "changed after terminal")
    assert project(store, row["admission_id"])["terminal_json"] == before["terminal_json"]


def test_caller_text_is_preserved_verbatim(store):
    text = "  exact\n\tcaller text\n"
    row = reserve(store, str(uuid.uuid4()), message=text)
    complete(store, row["run_id"])
    project(store, row["admission_id"])
    assert conversation_store.load_recent(store / HOME, f"principal:{OWNER}")[0].text == text


def test_deleted_owner_erases_former_home_admission_too(store):
    row = reserve(store, str(uuid.uuid4()))
    complete(store, row["run_id"])
    project(store, row["admission_id"])
    (store / "u-next").mkdir()
    set_founder_home(store, founder_sub=OWNER, universe_id="u-next", platform_generated=True)
    receipt = account_deletion.delete_account(
        store, founder_sub=OWNER, cancel_billing=lambda _: "none",
        delete_identity=lambda _: "deleted")
    assert receipt["unfinished_phases"] == []
    with sqlite3.connect(runs_db_path(store)) as conn:
        assert conn.execute("SELECT count(*) FROM conversation_run_admissions").fetchone()[0] == 0


def test_legacy_default_pair_behavior_is_unchanged(store):
    assert conversation_store.record_exchange(store / HOME, "legacy", "question", "answer")
    assert [m.text for m in conversation_store.load_recent(store / HOME, "legacy")] == [
        "question", "answer"]


def source_version(base, *, nested=False, public=True):
    from tinyassets.branch_versions import publish_branch_version

    with sqlite3.connect(db_path(base)) as conn:
        conn.execute("INSERT INTO branch_definitions "
                     "(branch_def_id,name,author,visibility,created_at,updated_at) "
                     "VALUES ('shared','Shared','creator','public',0,0)")
    node = {"node_id": "reply", "display_name": "Reply", "prompt_template": "hello"}
    if nested:
        node["invoke_branch_spec"] = {"branch_def_id": "mutable-child"}
    return publish_branch_version(base, {"branch_def_id": "shared", "author": "creator",
                                         "visibility": "public", "node_defs": [node]},
                                  public=public)


def test_public_foreign_source_is_not_blanket_banned(store):
    from tinyassets.storage import conversation_run_admissions as cr

    version = source_version(store)
    with cr.authorized_scope(store, owner=OWNER, universe=HOME) as scope:
        source = cr.authorize_source(scope, version.branch_version_id, version.content_hash)
        with cr.runs_transaction(scope) as conn:
            assert cr.load_source_in_transaction(conn, scope, source)["author"] == "creator"


@pytest.mark.parametrize("visibility", [None, ""])
def test_legacy_empty_visibility_is_private_here_too(store, visibility):
    """Private by default: a NULL or blank visibility is private for the
    conversation source gate exactly as for every other reader -- one rule
    (branch_versions.version_readable_by), not a second definition."""
    from tinyassets.storage import conversation_run_admissions as cr

    version = source_version(store)
    with sqlite3.connect(db_path(store)) as conn:
        if visibility is None:
            # Represent the legacy nullable table in this isolated fixture;
            # current fresh schemas correctly prohibit new NULL rows.
            conn.execute(
                "CREATE TABLE legacy_branch_definitions AS SELECT * FROM branch_definitions"
            )
            conn.execute("DROP TABLE branch_definitions")
            conn.execute("ALTER TABLE legacy_branch_definitions RENAME TO branch_definitions")
        conn.execute("UPDATE branch_definitions SET visibility=? WHERE branch_def_id='shared'",
                     (visibility,))
    with cr.authorized_scope(store, owner=OWNER, universe=HOME) as scope:
        with pytest.raises(PermissionError, match="source unavailable"):
            cr.authorize_source(scope, version.branch_version_id, version.content_hash)


def test_initialize_migrates_the_mark_before_any_source_read(store):
    """A runs DB from before the publication mark: deployment initialize adds
    the column and backfills, so the first admission neither crashes on the
    missing column nor loses a source the old rule exposed."""
    from tinyassets.storage import conversation_run_admissions as cr

    version = source_version(store)
    with sqlite3.connect(runs_db_path(store)) as conn:
        conn.execute("DROP TABLE branch_versions_migrations")
        conn.execute("ALTER TABLE branch_versions DROP COLUMN public")
    cr.initialize(store)
    with cr.authorized_scope(store, owner=OWNER, universe=HOME) as scope:
        source = cr.authorize_source(scope, version.branch_version_id, version.content_hash)
        with cr.runs_transaction(scope) as conn:
            assert cr.load_source_in_transaction(conn, scope, source)["author"] == "creator"


def test_unmarked_version_of_a_public_branch_is_not_a_foreign_source(store):
    """A public branch's unpublished history is its author's alone, here too."""
    from tinyassets.storage import conversation_run_admissions as cr

    version = source_version(store, public=False)
    with cr.authorized_scope(store, owner=OWNER, universe=HOME) as scope:
        with pytest.raises(PermissionError, match="source unavailable"):
            cr.authorize_source(scope, version.branch_version_id, version.content_hash)


def test_source_access_revoked_refuses_before_snapshot_read(store):
    from tinyassets.storage import conversation_run_admissions as cr

    version = source_version(store)
    with sqlite3.connect(db_path(store)) as conn:
        conn.execute("UPDATE branch_definitions SET visibility='private' "
                     "WHERE branch_def_id='shared'")
    # A corrupt hidden body must not reveal itself through JSON parsing.
    with sqlite3.connect(runs_db_path(store)) as conn:
        conn.execute("UPDATE branch_versions SET snapshot_json='broken'")
    with cr.authorized_scope(store, owner=OWNER, universe=HOME) as scope:
        with pytest.raises(PermissionError, match="source unavailable"):
            cr.authorize_source(scope, version.branch_version_id, version.content_hash)


@pytest.mark.parametrize("mutation", [
    "UPDATE branch_versions SET status='rolled_back'",
    "UPDATE branch_versions SET content_hash='changed'",
    "UPDATE branch_versions SET snapshot_json='{}'",
])
def test_source_pin_and_active_state_rechecked_in_runs_transaction(store, mutation):
    from tinyassets.storage import conversation_run_admissions as cr

    version = source_version(store)
    with cr.authorized_scope(store, owner=OWNER, universe=HOME) as scope:
        source = cr.authorize_source(scope, version.branch_version_id, version.content_hash)
        with cr.runs_transaction(scope) as conn:
            conn.execute(mutation)
            with pytest.raises(PermissionError, match="source unavailable"):
                cr.load_source_in_transaction(conn, scope, source)


def test_nested_execution_refused_for_self_contained_adapter(store):
    from tinyassets.storage import conversation_run_admissions as cr

    version = source_version(store, nested=True)
    with cr.authorized_scope(store, owner=OWNER, universe=HOME) as scope:
        source = cr.authorize_source(scope, version.branch_version_id, version.content_hash)
        with cr.runs_transaction(scope) as conn:
            with pytest.raises(ValueError, match="nested"):
                cr.load_source_in_transaction(conn, scope, source)


def test_source_authorization_cannot_open_another_connection_under_runs_writer(store):
    from tinyassets.storage import conversation_run_admissions as cr

    version = source_version(store)
    with cr.authorized_scope(store, owner=OWNER, universe=HOME) as scope:
        with cr.runs_transaction(scope):
            with pytest.raises(RuntimeError, match="precede"):
                cr.authorize_source(scope, version.branch_version_id, version.content_hash)


def test_source_proof_cannot_outlive_author_scope(store):
    from tinyassets.storage import conversation_run_admissions as cr

    version = source_version(store)
    with cr.authorized_scope(store, owner=OWNER, universe=HOME) as scope:
        proof = cr.authorize_source(scope, version.branch_version_id, version.content_hash)
    with cr.authorized_scope(store, owner=OWNER, universe=HOME) as fresh:
        with cr.runs_transaction(fresh) as conn:
            with pytest.raises(PermissionError, match="source unavailable"):
                cr.load_source_in_transaction(conn, fresh, proof)


def reset_plan(base):
    from tinyassets.daemon_server import ensure_universe_registered
    from tinyassets.scoped_reset import TestIdentityRoster, plan_test_identity_reset

    ensure_universe_registered(base, universe_id=HOME, universe_path=base / HOME)
    roster = TestIdentityRoster("consumer-reset-v1", {"receiver": OWNER}, frozenset({OWNER}))
    plan = plan_test_identity_reset(base, alias="receiver", roster=roster)
    return roster, plan


def test_scoped_reset_expires_canonical_payloads_but_preserves_identity(store):
    from tinyassets.scoped_reset import apply_test_identity_reset

    row = reserve(store, str(uuid.uuid4()))
    complete(store, row["run_id"])
    roster, plan = reset_plan(store)
    assert plan["blockers"] == []
    receipt = apply_test_identity_reset(store, alias="receiver", roster=roster,
                                        plan_id=plan["plan_id"])
    assert receipt["status"] == "completed"
    assert not (store / HOME).exists()
    with sqlite3.connect(runs_db_path(store)) as conn:
        conn.row_factory = sqlite3.Row
        expired = dict(conn.execute("SELECT * FROM conversation_run_admissions").fetchone())
        assert expired["admission_id"] == row["admission_id"]
        assert expired["run_id"] == row["run_id"]
        assert expired["intent_digest"] == row["intent_digest"]
        assert expired["projection_state"] == "expired"
        assert expired["intent_json"] is expired["context_json"] is expired["terminal_json"] is None
        assert expired["selection_json"] == "{}"
        assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 1


def test_existing_home_conversation_database_keeps_baseline_reset_refusal(store):
    row = reserve(store, str(uuid.uuid4()))
    complete(store, row["run_id"])
    project(store, row["admission_id"])
    _, plan = reset_plan(store)
    assert plan["blockers"] == [
        "home operational store has no scoped-reset adapter: .conversation_memory.db"]


def test_scoped_reset_still_refuses_active_canonical_run(store):
    reserve(store, str(uuid.uuid4()))
    _, plan = reset_plan(store)
    assert plan["blockers"] == ["active root run references test principal"]


@pytest.mark.parametrize("point,expired", [
    ("before_commit", False), ("after_commit", True), ("after_conversation_expiry", True),
])
def test_reset_crash_witness_decides_payload_expiry(store, point, expired):
    from tinyassets.scoped_reset import apply_test_identity_reset, recover_scoped_resets

    row = reserve(store, str(uuid.uuid4()))
    complete(store, row["run_id"])
    roster, plan = reset_plan(store)
    assert plan["blockers"] == []

    def crash(current):
        if current == point:
            raise RuntimeError("test reset crash")

    with pytest.raises(RuntimeError, match="test reset crash"):
        apply_test_identity_reset(store, alias="receiver", roster=roster,
                                  plan_id=plan["plan_id"], fault_injector=crash)
    recover_scoped_resets(store)
    with sqlite3.connect(runs_db_path(store)) as conn:
        actual = conn.execute("SELECT projection_state,intent_json FROM "
                               "conversation_run_admissions").fetchone()
    expected = ("expired", None) if expired else ("pending", row["intent_json"])
    assert actual == expected
    assert (store / HOME).exists() is not expired


def test_nested_service_writer_and_canonical_scope_use_same_maintenance_barrier(store):
    from tinyassets.scoped_reset import ScopedResetLeaseBusy, acquire_maintenance_barrier
    from tinyassets.storage import conversation_run_admissions as cr

    with acquire_maintenance_barrier(store, exclusive=False):
        with cr.authorized_scope(store, owner=OWNER, universe=HOME):
            with pytest.raises(ScopedResetLeaseBusy):
                acquire_maintenance_barrier(store, exclusive=True)


def test_reset_of_empty_canonical_table_preserves_previously_supported_home(store):
    from tinyassets.scoped_reset import apply_test_identity_reset

    roster, plan = reset_plan(store)
    assert plan["blockers"] == []
    assert plan["root_history_actions"][0]["admission_ids"] == []
    assert apply_test_identity_reset(store, alias="receiver", roster=roster,
                                     plan_id=plan["plan_id"])["status"] == "completed"


def test_reset_preserves_other_owner_and_same_key_cannot_restore_old_home(store):
    from tinyassets.scoped_reset import apply_test_identity_reset

    key = str(uuid.uuid4())
    row = reserve(store, key)
    complete(store, row["run_id"])
    (store / "u-other").mkdir()
    set_founder_home(store, founder_sub="other", universe_id="u-other", platform_generated=True)
    grant_universe_access(store, universe_id="u-other", actor_id="other",
                          permission="admin", granted_by="other")
    other = reserve(store, str(uuid.uuid4()), owner="other", home="u-other")
    roster, plan = reset_plan(store)
    assert plan["blockers"] == []
    assert OWNER not in json.dumps(plan)
    apply_test_identity_reset(store, alias="receiver", roster=roster, plan_id=plan["plan_id"])
    with sqlite3.connect(runs_db_path(store)) as conn:
        conn.row_factory = sqlite3.Row
        after = dict(conn.execute("SELECT * FROM conversation_run_admissions WHERE admission_id=?",
                                  (other["admission_id"],)).fetchone())
    assert after == other
    (store / HOME).mkdir()
    set_founder_home(store, founder_sub=OWNER, universe_id=HOME, platform_generated=True)
    grant_universe_access(store, universe_id=HOME, actor_id=OWNER,
                          permission="admin", granted_by=OWNER)
    replay = reserve(store, key)
    assert replay["run_id"] == row["run_id"]
    assert replay["projection_state"] == "expired"
    assert project(store, row["admission_id"])["projection_state"] == "expired"
    assert not (store / HOME / ".conversation_memory.db").exists()


@pytest.mark.parametrize("mutation", [
    "ALTER TABLE conversation_run_admissions ADD COLUMN unknown TEXT",
    "CREATE TRIGGER surprise AFTER UPDATE ON conversation_run_admissions BEGIN SELECT 1; END",
])
def test_reset_refuses_unclassified_canonical_schema(store, mutation):
    from tinyassets.scoped_reset import ScopedResetSchemaError

    with sqlite3.connect(runs_db_path(store)) as conn:
        conn.execute(mutation)
    with pytest.raises(ScopedResetSchemaError, match="unclassified"):
        reset_plan(store)


def _reset_lock_process(base, results):
    from tinyassets.scoped_reset import ScopedResetLeaseBusy, acquire_maintenance_barrier

    try:
        with acquire_maintenance_barrier(base, exclusive=True):
            results.put("unexpected exclusive lock")
    except ScopedResetLeaseBusy:
        results.put("reset blocked")


def test_two_process_reset_barrier_cannot_overtake_projection(store):
    row = reserve(store, str(uuid.uuid4()))
    complete(store, row["run_id"])
    ctx = multiprocessing.get_context("spawn")
    entered, release, results = ctx.Event(), ctx.Event(), ctx.Queue()
    writer = ctx.Process(target=_projection_process,
                         args=(store, row["admission_id"], entered, release, results))
    resetter = ctx.Process(target=_reset_lock_process, args=(store, results))
    writer.start()
    try:
        assert entered.wait(8)
        resetter.start()
        resetter.join(8)
        assert resetter.exitcode == 0
        assert results.get(timeout=2) == "reset blocked"
    finally:
        release.set()
        writer.join(10)
        for process in (writer, resetter):
            if process.pid and process.is_alive():
                process.terminate()
                process.join(3)
    assert writer.exitcode == 0
    assert results.get(timeout=2) == "projected"


def test_post_witness_identity_mismatch_blocks_recovery_and_serving(store):
    from tinyassets.scoped_reset import (
        ScopedResetRecoveryError,
        apply_test_identity_reset,
        recover_scoped_resets,
    )
    from tinyassets.storage import conversation_run_admissions as cr

    row = reserve(store, str(uuid.uuid4()))
    complete(store, row["run_id"])
    roster, plan = reset_plan(store)

    def crash(point):
        if point == "after_commit":
            with sqlite3.connect(runs_db_path(store)) as conn:
                conn.execute("UPDATE conversation_run_admissions SET owner_user_id='other'")
            raise RuntimeError("test reset crash")

    with pytest.raises(RuntimeError, match="test reset crash"):
        apply_test_identity_reset(store, alias="receiver", roster=roster,
                                  plan_id=plan["plan_id"], fault_injector=crash)
    with pytest.raises(ScopedResetRecoveryError, match="identity changed"):
        recover_scoped_resets(store)
    with pytest.raises(ScopedResetRecoveryError, match="recovery is pending"):
        with cr.authorized_scope(store, owner=OWNER, universe=HOME):
            pytest.fail("unrecovered reset admitted a consumer writer")
    with sqlite3.connect(runs_db_path(store)) as conn:
        assert conn.execute("SELECT intent_json FROM conversation_run_admissions").fetchone()[0]
