"""Real stored opt-in, held stores and quota; never activate a background worker."""

import json
import sqlite3

import pytest

from tests.test_command_center_release_policy import enable
from tests.test_command_center_release_policy import published as _published
from tinyassets import command_center_update_executor as executor
from tinyassets import command_center_update_policy as policy
from tinyassets.custom_agents import get_app_ui


@pytest.fixture
def published(tmp_path, monkeypatch):
    from tinyassets.custom_agents import _agent_connect
    from tinyassets.universe_owner import record_creation

    data = _published.__wrapped__(tmp_path, monkeypatch)
    with _agent_connect(tmp_path) as conn:
        record_creation(conn, universe_id=data["uid"], owner_id=data["owner"])
    return data


def apply(data):
    return executor.apply_next(
        data["base"],
        owner_id=data["owner"],
        universe_id=data["uid"],
        adoption_id=data["adoption"]["adoption_id"],
    )


def ui(data):
    return get_app_ui(data["base"], owner_user_id=data["owner"], universe_id=data["uid"])


def release(data, **edit):
    return data["publish"](parent=data["first"], edit=lambda value: value.update(**edit))


def disable(data):
    from tests.test_command_center_packages import _as

    with _as(data["owner"]):
        req = policy.preview_policy(
            universe_id=data["uid"], adoption_id=data["adoption"]["adoption_id"], enabled=False
        )
        return policy.commit_policy(
            universe_id=data["uid"],
            request_id=req["request_id"],
            plan_digest=req["plan_digest"],
            decision="accepted",
        )


def test_real_grant_updates_once_without_request_identity(published, monkeypatch):
    from tinyassets.api import command_center_updates, permissions
    from tinyassets.providers.router import ProviderRouter

    enable(published)
    target = release(published, style="main { color: teal; }")
    before = ui(published)

    def forbidden(*args, **kwargs):
        raise AssertionError("no owner impersonation, manual consent or provider execution")

    monkeypatch.setattr(permissions, "owner_run_identity", forbidden)
    monkeypatch.setattr(command_center_updates, "_scope", forbidden)
    monkeypatch.setattr(command_center_updates, "commit_update", forbidden)
    monkeypatch.setattr(ProviderRouter, "call_sync", forbidden)
    result = apply(published)
    assert result["applied"], result
    assert result["to_release_id"] == target["release_id"]
    assert result["settlement"] in {"settled", "measured"}
    assert ui(published)["revision"] == before["revision"] + 1
    assert apply(published)["status"] == "up_to_date"
    assert (
        executor.settle_receipt(published["base"], operation_id=result["operation_id"])[
            "operation_id"
        ]
        == result["operation_id"]
    )
    with executor._connect(published["base"], published["owner"]) as (conn, _):
        grant = policy._policy(
            conn, published["owner"], published["uid"], published["adoption"]["adoption_id"]
        )
    snapshot = json.loads(grant["snapshot_json"])
    assert snapshot["link"]["release_id"] == published["first"]["release_id"]
    assert snapshot["progress"]["release_id"] == target["release_id"]
    assert grant["last_request_id"] == result["grant_request_id"]


def test_disabled_or_preview_only_has_no_authority(published):
    release(published, name="new")
    before = ui(published)
    assert apply(published)["status"] == "disabled"
    enable(published, accepted=False)
    assert apply(published)["status"] == "disabled"
    assert ui(published) == before


@pytest.mark.parametrize(
    "edit", [{"script": "alert(1)"}, {"markup": "<button>new</button>"}, {"style": '@import "x";'}]
)
def test_new_execution_or_resources_require_decision(published, edit):
    enable(published)
    release(published, **edit)
    before = ui(published)
    assert apply(published)["status"] == "requires_decision"
    assert ui(published) == before


@pytest.mark.parametrize(
    "change",
    [
        "disable",
        "private_edit",
        "retire",
        "config",
        "withdraw",
        "acl",
        "tombstone",
        "pin",
        "home",
        "owner",
    ],
)
def test_change_after_reservation_refuses(published, monkeypatch, change):
    from tests.test_command_center_update_adapter import _change_retained_automation
    from tinyassets import storage_accounting
    from tinyassets.account_deletion import principal_digest
    from tinyassets.custom_agents import _agent_connect, save_app_ui

    enable(published)
    release(published, name="new")
    original = storage_accounting.reserve

    def reserve(*args, **kwargs):
        reservation = original(*args, **kwargs)
        monkeypatch.setattr(storage_accounting, "reserve", original)
        if change == "disable":
            disable(published)
        elif change == "private_edit":
            row = ui(published)
            row["ui_library"][0]["markup"] = "PRIVATE"
            save_app_ui(
                published["base"],
                owner_user_id=published["owner"],
                universe_id=published["uid"],
                expected_revision=row["revision"],
                changes={"ui_library": row["ui_library"]},
            )
        elif change in {"retire", "config"}:
            _change_retained_automation(published, change)
        elif change == "withdraw":
            with sqlite3.connect(published["base"] / ".runs.db") as conn:
                conn.execute("UPDATE branch_versions SET public=0")
        elif change == "pin":
            with sqlite3.connect(
                published["base"] / ".command-center-packages/packages.db"
            ) as conn:
                conn.execute(
                    "UPDATE pins SET state='pinned' WHERE universe_id=?", (published["uid"],)
                )
        else:
            with _agent_connect(published["base"]) as conn:
                if change == "home":
                    conn.execute(
                        "UPDATE founder_home SET universe_id=? WHERE founder_sub=?",
                        (published["publisher_home"], published["owner"]),
                    )
                elif change == "owner":
                    conn.execute(
                        "UPDATE universe_owner SET owner_id='other' WHERE universe_id=?",
                        (published["uid"],),
                    )
                elif change == "acl":
                    conn.execute("DELETE FROM universe_acl WHERE actor_id=?", (published["owner"],))
                else:
                    conn.execute(
                        "INSERT INTO deleted_principals VALUES (?, 0)",
                        (principal_digest(published["owner"]),),
                    )
        return reservation

    monkeypatch.setattr(storage_accounting, "reserve", reserve)
    before = ui(published)
    result = apply(published)
    assert result["status"] == "requires_decision", result
    assert not result["applied"]
    assert ui(published)["ui_library"][0]["name"] == before["ui_library"][0]["name"]


def test_identical_screen_advances_source_without_ui_write_then_next_release(published):
    enable(published)
    second = published["publish"](
        parent=published["first"], action_edit=lambda a: a.update(description="New release notes")
    )
    third = published["publish"](parent=second, edit=lambda screen: screen.update(name="Third"))
    before = ui(published)
    result = apply(published)
    assert result["status"] == "advanced_without_content_change", result
    assert result["to_release_id"] == second["release_id"]
    assert ui(published) == before
    result = apply(published)
    assert result["applied"] and result["to_release_id"] == third["release_id"], result
    disable(published)
    assert apply(published)["status"] == "disabled"


def test_cannot_skip_a_blocked_intermediate_release(published):
    enable(published)
    before = ui(published)
    bad = release(published, markup="changed markup")
    published["publish"](
        parent=bad,
        edit=lambda screen: screen.update(
            markup=before["ui_library"][0]["markup"], name="Later safe-looking release"
        ),
    )
    assert apply(published)["status"] == "requires_decision"
    assert ui(published) == before


@pytest.mark.parametrize(
    "table,statement",
    [
        ("universe_app_ui", "UPDATE"),
        ("command_center_adoptions", "UPDATE"),
        ("command_center_update_policies", "UPDATE"),
        ("command_center_auto_receipts", "INSERT"),
        ("command_center_auto_status", "INSERT"),
    ],
)
def test_sql_failure_rolls_back_ui_progress_and_receipt(published, table, statement):
    enable(published)
    release(published, name="new")
    before = ui(published)
    with executor._connect(published["base"], published["owner"]) as (conn, _):
        old_grant = policy._policy(
            conn, published["owner"], published["uid"], published["adoption"]["adoption_id"]
        )
        conn.execute(
            f"CREATE TRIGGER fault BEFORE {statement} ON {table} "
            "BEGIN SELECT RAISE(ABORT,'injected fault'); END"
        )
    result = apply(published)
    assert not result["applied"], result
    assert ui(published) == before
    with executor._connect(published["base"], published["owner"]) as (conn, _):
        assert (
            policy._policy(
                conn, published["owner"], published["uid"], published["adoption"]["adoption_id"]
            )
            == old_grant
        )
        assert conn.execute("SELECT COUNT(*) FROM command_center_auto_receipts").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fault")
    assert apply(published)["applied"]


@pytest.mark.parametrize(
    "store,sql",
    [
        (".tinyassets.db", "UPDATE universe_acl SET permission='read' WHERE actor_id='acct_bob'"),
        (".runs.db", "UPDATE branch_versions SET public=0"),
        (".automations.db", "UPDATE automations SET retired_at='2026-10-04T00:00:00Z'"),
        (".command-center-packages/packages.db", "UPDATE pins SET state='pinned'"),
        (".storage_accounting.db", "DELETE FROM pending WHERE state='reserved'"),
    ],
)
def test_actual_competing_store_writer_is_fenced_until_commit(published, monkeypatch, store, sql):
    enable(published)
    release(published, name="new")
    actual = executor._write
    attempted = []

    def write(*args, **kwargs):
        other = sqlite3.connect(published["base"] / store, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute(sql)
            attempted.append(True)
        finally:
            other.close()
        return actual(*args, **kwargs)

    monkeypatch.setattr(executor, "_write", write)
    assert apply(published)["applied"]
    assert attempted == [True]
    with sqlite3.connect(published["base"] / store, timeout=0) as other:
        other.execute(sql)


def test_real_automation_retire_waits_for_ui_transaction(published, monkeypatch):
    import threading
    from datetime import datetime, timezone

    from tinyassets.automations import AutomationStore

    enable(published)
    release(published, name="new")
    store = AutomationStore(published["base"])
    automation = store.list(universe_id=published["uid"])[0]
    started, finished = threading.Event(), threading.Event()
    results = []
    actual = executor._write

    def retire():
        started.set()
        store.retire(
            automation.automation_id,
            expected_revision=automation.revision,
            now=datetime.now(timezone.utc),
        )
        results.append(ui(published)["revision"])
        finished.set()

    thread = threading.Thread(target=retire)

    def write(*args, **kwargs):
        thread.start()
        assert started.wait(2)
        assert not finished.wait(0.1)
        return actual(*args, **kwargs)

    monkeypatch.setattr(executor, "_write", write)
    before = ui(published)["revision"]
    result = apply(published)
    thread.join(5)
    assert result["applied"] and not thread.is_alive(), result
    assert results == [before + 1]


def test_duplicate_preparers_apply_once_and_release_only_own_reservation(published, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    enable(published)
    release(published, name="new")
    before = ui(published)
    barrier = threading.Barrier(2)
    actual = executor.accounting.reserve

    def reserve(*args, **kwargs):
        value = actual(*args, **kwargs)
        barrier.wait(timeout=10)
        return value

    monkeypatch.setattr(executor.accounting, "reserve", reserve)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(apply, published), pool.submit(apply, published)
        results = [a.result(timeout=20), b.result(timeout=20)]
    assert all(r["applied"] for r in results), results
    assert len({r["operation_id"] for r in results}) == 1
    assert ui(published)["revision"] == before["revision"] + 1
    with executor._connect(published["base"], published["owner"]) as (conn, _):
        assert conn.execute("SELECT COUNT(*) FROM command_center_auto_receipts").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM storage_ledger.pending "
                "WHERE state='reserved' AND account_id=?",
                (published["owner"],),
            ).fetchone()[0]
            == 0
        )


def test_postcommit_settlement_failure_remains_applied_and_retry_never_rewrites(
    published, monkeypatch
):
    enable(published)
    release(published, name="new")
    before = ui(published)
    actual = executor.accounting.commit

    def failure(*args, **kwargs):
        raise sqlite3.OperationalError("ledger unavailable")

    monkeypatch.setattr(executor.accounting, "commit", failure)
    result = apply(published)
    assert result["applied"] and result["settlement"] == "pending", result
    monkeypatch.setattr(executor.accounting, "commit", actual)
    settled = executor.settle_receipt(published["base"], operation_id=result["operation_id"])
    assert settled["settlement"] == "settled"
    assert ui(published)["revision"] == before["revision"] + 1


def test_zero_row_accounting_commit_needs_real_measurement(published, monkeypatch):
    enable(published)
    release(published, name="new")
    actual_measure = executor.accounting.measure

    def expired(reservation, *args, **kwargs):
        with sqlite3.connect(published["base"] / ".storage_accounting.db") as conn:
            conn.execute("DELETE FROM pending WHERE id=?", (reservation.id,))

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("measurement failed")

    actual_write = executor._write

    def write(*args, **kwargs):
        result = actual_write(*args, **kwargs)
        monkeypatch.setattr(executor.accounting, "measure", unavailable)
        return result

    monkeypatch.setattr(executor, "_write", write)
    monkeypatch.setattr(executor.accounting, "commit", expired)
    result = apply(published)
    assert result["applied"] and result["settlement"] == "pending", result
    monkeypatch.setattr(executor.accounting, "measure", actual_measure)
    assert (
        executor.settle_receipt(published["base"], operation_id=result["operation_id"])[
            "settlement"
        ]
        == "measured"
    )


@pytest.mark.parametrize("mutation", ["expire", "delete", "account", "scope", "size", "tier"])
def test_actual_reservation_or_tier_change_prevents_write(published, monkeypatch, mutation):
    enable(published)
    release(published, name="new")
    before = ui(published)
    actual = executor.accounting.reserve

    def reserve(*args, **kwargs):
        value = actual(*args, **kwargs)
        if mutation == "tier":
            path = published["base"] / published["uid"] / ".subscription_state.db"
            with sqlite3.connect(path) as conn:
                conn.execute(
                    "CREATE TABLE subscription_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)"
                )
                conn.execute("INSERT INTO subscription_meta VALUES ('tier','paid')")
        else:
            sql = {
                "expire": "UPDATE pending SET created_at=0 WHERE id=?",
                "delete": "DELETE FROM pending WHERE id=?",
                "account": "UPDATE pending SET account_id='other' WHERE id=?",
                "scope": "UPDATE pending SET scope_id='other' WHERE id=?",
                "size": "UPDATE pending SET bytes=0 WHERE id=?",
            }[mutation]
            with sqlite3.connect(published["base"] / ".storage_accounting.db") as conn:
                conn.execute(sql, (value.id,))
        return value

    monkeypatch.setattr(executor.accounting, "reserve", reserve)
    result = apply(published)
    assert not result["applied"], result
    assert ui(published) == before


def test_actual_quota_refusal_does_not_advance(published):
    import time

    enable(published)
    release(published, name="new")
    value = executor.accounting.reserve(
        published["base"],
        account_id=published["owner"],
        scope_id=published["owner"],
        store="ui_library",
        nbytes=0,
    )
    executor.accounting.release(value)
    quota, _ = executor.accounting._quota(published["base"], published["owner"])
    with sqlite3.connect(published["base"] / ".storage_accounting.db") as conn:
        conn.execute(
            "UPDATE measurements SET bytes=?,measured_at=?,dirty=0 "
            "WHERE scope_id=? AND store='ui_library'",
            (quota + 1, time.time(), published["owner"]),
        )
    before = ui(published)
    result = apply(published)
    assert not result["applied"] and result["status"] == "retryable_error", result
    assert ui(published) == before


def test_held_workflow_validation_keeps_nested_dependency_refusal(published):
    enable(published)
    release(published, name="new")
    with sqlite3.connect(published["base"] / ".runs.db") as conn:
        row = conn.execute(
            "SELECT branch_version_id,snapshot_json FROM branch_versions LIMIT 1"
        ).fetchone()
        body = json.loads(row[1])
        body["invoke_branch_version_spec"] = {"branch_version_id": "hidden-private-child"}
        conn.execute(
            "UPDATE branch_versions SET snapshot_json=? WHERE branch_version_id=?",
            (json.dumps(body), row[0]),
        )
    result = apply(published)
    assert result["status"] == "requires_decision" and "Nested" in result["reason"], result


def test_held_templates_use_same_instruction_and_fingerprint_validation(published):
    from tests.test_command_center_agent_templates import _publish
    from tinyassets.api.system_copy_requests import _plan

    source, definition, _ = _publish(published["base"])
    expected = _plan({"agent_definition_id": source})
    with executor._connect(published["base"], published["owner"]) as (conn, reads):
        assert _plan({"agent_definition_id": source}, readers=reads) == expected
        conn.execute(
            "UPDATE agent_definitions SET components_json=? WHERE agent_definition_id=?",
            (
                json.dumps(
                    {
                        "identity": {
                            "kind": "instructions",
                            "config": {"instructions": "Safe", "tools": ["hidden"]},
                        }
                    }
                ),
                definition["agent_definition_id"],
            ),
        )
        with pytest.raises(ValueError, match="instruction-only"):
            _plan({"agent_definition_id": source}, readers=reads)


def test_held_existing_quota_resolvers_never_initialize_and_tier_writer_is_fenced(
    published, monkeypatch
):
    from tinyassets import daemon_server

    path = published["base"] / published["uid"] / ".subscription_state.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE subscription_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        conn.execute("INSERT INTO subscription_meta VALUES ('tier','paid')")
    enable(published)
    release(published, name="new")

    def forbidden(*args, **kwargs):
        raise AssertionError("no schema migration while authority stores are held")

    monkeypatch.setattr(daemon_server, "_initialize_author_server_locked", forbidden)
    actual = executor._validate_reservation
    probes = []

    def validate(*args, **kwargs):
        other = sqlite3.connect(path, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("UPDATE subscription_meta SET value='free' WHERE key='tier'")
            probes.append(True)
        finally:
            other.close()
        return actual(*args, **kwargs)

    monkeypatch.setattr(executor, "_validate_reservation", validate)
    result = apply(published)
    assert result["applied"], result
    assert probes == [True, True]
    with sqlite3.connect(path, timeout=0) as conn:
        conn.execute("UPDATE subscription_meta SET value='free' WHERE key='tier'")


def test_quota_resolver_aba_result_cannot_overauthorize(published, monkeypatch):
    enable(published)
    release(published, name="new")
    actual = executor.accounting._quota
    count = 0

    def transient(*args, **kwargs):
        nonlocal count
        count += 1
        quota, tier = actual(*args, **kwargs)
        # First admission-side read observes a transient different result.
        return (quota * 2, tier) if count == 1 else (quota, tier)

    monkeypatch.setattr(executor.accounting, "_quota", transient)
    before = ui(published)
    result = apply(published)
    assert not result["applied"] and result["reason_code"] == "storage_changed", result
    assert ui(published) == before


def test_optin_replay_after_auto_progress_cannot_undo_later_optout(published):
    from tests.test_command_center_packages import _as

    request = enable(published, accepted=False)
    with _as(published["owner"]):
        policy.commit_policy(
            universe_id=published["uid"],
            request_id=request["request_id"],
            plan_digest=request["plan_digest"],
            decision="accepted",
        )
    release(published, name="new")
    assert apply(published)["applied"]
    disable(published)
    with _as(published["owner"]), pytest.raises(LookupError):
        policy.commit_policy(
            universe_id=published["uid"],
            request_id=request["request_id"],
            plan_digest=request["plan_digest"],
            decision="accepted",
        )
    assert apply(published)["status"] == "disabled"


def test_bounded_policy_cursor_requires_no_model_context(published):
    enable(published)
    release(published, name="new")
    result = executor.process_policies(published["base"], limit=1)
    assert len(result["results"]) == 1 and result["results"][0]["applied"], result
    assert (
        executor.process_policies(published["base"], after=result["after"], limit=1)["results"]
        == []
    )


def test_settlement_sweep_runs_after_optout_and_keeps_applied_receipt(published, monkeypatch):
    enable(published)
    release(published, name="new")
    actual = executor.accounting.commit

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("/private/host/path.db unavailable")

    monkeypatch.setattr(executor.accounting, "commit", unavailable)
    result = apply(published)
    assert result["applied"] and result["settlement"] == "pending", result
    assert "/private/" not in json.dumps(result)
    disable(published)
    monkeypatch.setattr(executor.accounting, "commit", actual)
    outcome = executor.settle_pending(published["base"], limit=1)
    assert outcome["results"][0]["settlement"] == "settled"
    assert outcome["after"] == result["operation_id"]
    assert executor.settle_pending(published["base"], after=outcome["after"])["results"] == []


def test_durable_owner_status_explains_block_and_hides_foreign_home(published):
    from tests.test_command_center_packages import _as

    enable(published)
    release(published, script="new code")
    result = apply(published)
    assert result["status"] == "requires_decision"
    with _as(published["owner"]):
        assert (
            executor.inspect_status(
                universe_id=published["uid"], adoption_id=published["adoption"]["adoption_id"]
            )
            == result
        )
    with _as(published["publisher"]), pytest.raises(PermissionError):
        executor.inspect_status(
            universe_id=published["uid"], adoption_id=published["adoption"]["adoption_id"]
        )


def test_policy_sweep_isolates_failure_and_persists_sanitized_status(published, monkeypatch):
    from tests.test_command_center_packages import _as
    from tinyassets.custom_agents import _agent_connect

    enable(published)
    real = executor.apply_next
    with _agent_connect(published["base"]) as conn:
        conn.execute(
            "INSERT INTO command_center_update_policies "
            "SELECT owner_id,universe_id,'zz-next',revision,enabled,snapshot_json,"
            "last_request_id,last_digest FROM command_center_update_policies"
        )
    called = []

    def fail_one(base, **kwargs):
        called.append(kwargs["adoption_id"])
        if len(called) == 1:
            raise RuntimeError("/private/path credential SECRET implementation failure")
        return real(base, **kwargs)

    monkeypatch.setattr(executor, "apply_next", fail_one)
    outcome = executor.process_policies(published["base"], limit=2)
    assert len(called) == 2 and len(outcome["results"]) == 2
    assert "/private/" not in json.dumps(outcome) and "SECRET" not in json.dumps(outcome)
    with _as(published["owner"]):
        saved = executor.inspect_status(
            universe_id=published["uid"], adoption_id=published["adoption"]["adoption_id"]
        )
    assert saved["reason_code"] == "temporarily_unavailable"


def test_settlement_sweep_failure_does_not_starve_next_receipt(published, monkeypatch):
    enable(published)
    second = release(published, name="second")
    actual = executor.accounting.commit

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("unavailable")

    published["publish"](parent=second, edit=lambda screen: screen.update(name="third"))
    monkeypatch.setattr(executor.accounting, "commit", unavailable)
    first = apply(published)
    second_result = apply(published)
    monkeypatch.setattr(executor.accounting, "commit", actual)
    real_settle = executor.settle_receipt
    called = []

    def settle(base, *, operation_id):
        called.append(operation_id)
        if len(called) == 1:
            raise RuntimeError("/private/secret.db")
        return real_settle(base, operation_id=operation_id)

    monkeypatch.setattr(executor, "settle_receipt", settle)
    result = executor.settle_pending(published["base"], limit=2)
    assert set(called) == {first["operation_id"], second_result["operation_id"]}
    assert result["results"][0]["settlement"] == "pending"
    assert result["results"][1]["settlement"] == "settled"
    assert "/private/" not in json.dumps(result)


def test_stored_grant_digest_tampering_never_authorizes_update(published):
    from tinyassets.custom_agents import _agent_connect

    enable(published)
    release(published, name="new")
    with _agent_connect(published["base"]) as conn:
        row = conn.execute("SELECT snapshot_json FROM command_center_update_policies").fetchone()
        body = json.loads(row[0])
        body["explanation"] = "Unaccepted replacement consent"
        conn.execute(
            "UPDATE command_center_update_policies SET snapshot_json=?", (json.dumps(body),)
        )
    before = ui(published)
    assert not apply(published)["applied"]
    assert ui(published) == before


@pytest.mark.parametrize("advance_newer", [False, True])
def test_late_failed_settler_returns_and_displays_winners_canonical_receipt(
    published, monkeypatch, advance_newer
):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from tests.test_command_center_packages import _as

    enable(published)
    target = release(published, name="new")
    real_commit = executor.accounting.commit

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("initial debt")

    monkeypatch.setattr(executor.accounting, "commit", unavailable)
    applied = apply(published)
    assert applied["applied"] and applied["settlement"] == "pending"
    entered, finish_late = threading.Event(), threading.Event()

    def competing_commit(*args, **kwargs):
        if threading.current_thread().name.startswith("stale-settler"):
            entered.set()
            assert finish_late.wait(5)
            raise sqlite3.OperationalError("late failed accounting attempt")
        return real_commit(*args, **kwargs)

    monkeypatch.setattr(executor.accounting, "commit", competing_commit)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="stale-settler") as pool:
        stale = pool.submit(
            executor.settle_receipt, published["base"], operation_id=applied["operation_id"]
        )
        try:
            assert entered.wait(5)
            winner = executor.settle_receipt(
                published["base"], operation_id=applied["operation_id"]
            )
            assert winner["settlement"] == "settled"
            latest_operation = winner["operation_id"]
            if advance_newer:
                published["publish"](parent=target, edit=lambda screen: screen.update(name="newer"))
                newer = apply(published)
                assert newer["applied"] and newer["settlement"] == "settled"
                latest_operation = newer["operation_id"]
            with _as(published["owner"]):
                assert (
                    executor.inspect_status(
                        universe_id=published["uid"],
                        adoption_id=published["adoption"]["adoption_id"],
                    )["settlement"]
                    == "settled"
                )
        finally:
            finish_late.set()
        loser = stale.result(timeout=5)
    assert loser == winner
    with _as(published["owner"]):
        status = executor.inspect_status(
            universe_id=published["uid"], adoption_id=published["adoption"]["adoption_id"]
        )
    assert status["operation_id"] == latest_operation
    assert status["settlement"] == "settled"
    assert status["settlement_error"] == ""
    assert status.get("pending_settlements", 0) == 0


def test_deleted_receipt_during_settlement_is_not_reported_or_resurrected(published, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from tinyassets.custom_agents import _agent_connect

    enable(published)
    release(published, name="new")

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("initial debt")

    monkeypatch.setattr(executor.accounting, "commit", unavailable)
    applied = apply(published)
    assert applied["applied"] and applied["settlement"] == "pending"
    entered, finish_late = threading.Event(), threading.Event()

    def late_failure(*args, **kwargs):
        entered.set()
        assert finish_late.wait(5)
        raise sqlite3.OperationalError("late attempt after deletion")

    monkeypatch.setattr(executor.accounting, "commit", late_failure)
    with ThreadPoolExecutor(max_workers=1) as pool:
        stale = pool.submit(
            executor.settle_receipt, published["base"], operation_id=applied["operation_id"]
        )
        try:
            assert entered.wait(5)
            with _agent_connect(published["base"]) as conn:
                conn.execute(
                    "DELETE FROM command_center_auto_receipts WHERE operation_id=?",
                    (applied["operation_id"],),
                )
                conn.execute(
                    "DELETE FROM command_center_auto_status WHERE operation_id=?",
                    (applied["operation_id"],),
                )
        finally:
            finish_late.set()
        with pytest.raises(LookupError, match="receipt not found"):
            stale.result(timeout=5)
    with _agent_connect(published["base"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM command_center_auto_receipts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM command_center_auto_status").fetchone()[0] == 0
