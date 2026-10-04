"""Real SQLite/manual-adoption update proofs; synthetic owners, no model calls."""

import copy

import pytest

from tinyassets.api import command_center_updates as api
from tinyassets.custom_agents import get_app_ui, get_definition, publish_definition, save_app_ui


@pytest.fixture
def installed(tmp_path, monkeypatch):
    from tests.cloud_runtime_fixture import cloud_runtime
    from tests.test_command_center_packages import BOB, BOB_UNIVERSE, OWNER, _answer, _as, home
    from tests.test_command_center_system_copy import _legacy, _preview

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    cloud_runtime.__wrapped__(monkeypatch)
    base = home.__wrapped__(tmp_path, monkeypatch)
    source_id = _legacy(base)
    ask = _preview(source_id)
    done = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert done.get("installed"), done
    with _as(BOB):
        adoption = api.record_install(universe_id=BOB_UNIVERSE, request_id=ask["request_id"])
    return dict(
        base=base,
        owner=BOB,
        uid=BOB_UNIVERSE,
        publisher=OWNER,
        source_id=source_id,
        request_id=ask["request_id"],
        adoption=adoption,
        done=done,
    )


def replacement(installed, edit=None):
    source = get_definition(installed["base"], installed["source_id"])
    components = copy.deepcopy(source["components"])
    ui = next(c for c in components.values() if c["kind"] == "tinyassets.app-ui.v1")
    ui["markup"] = '<main data-testid="updated-village">Updated village</main>'
    if edit:
        edit(components, ui)
    return publish_definition(
        installed["base"],
        author_id=installed["publisher"],
        payload={
            "schema_version": 1,
            "name": "Village second edition",
            "description": "A clearer layout",
            "tags": ["tinyassets.system.v1"],
            "components": components,
        },
    )["agent_definition_id"]


def preview(installed, definition_id):
    from tests.test_command_center_packages import _as

    with _as(installed["owner"]):
        return api.preview_update(
            universe_id=installed["uid"],
            adoption_id=installed["adoption"]["adoption_id"],
            definition_id=definition_id,
        )


def accept(installed, request, **kwargs):
    from tests.test_command_center_packages import _as

    with _as(installed["owner"]):
        return api.commit_update(
            universe_id=installed["uid"],
            request_id=request["request_id"],
            plan_digest=request["plan_digest"],
            decision=kwargs.get("decision", "accepted"),
        )


def document(installed):
    return get_app_ui(
        installed["base"], owner_user_id=installed["owner"], universe_id=installed["uid"]
    )


def edit_recipient(installed, edit):
    row = document(installed)
    library = copy.deepcopy(row["ui_library"])
    edit(library)
    return save_app_ui(
        installed["base"],
        owner_user_id=installed["owner"],
        universe_id=installed["uid"],
        expected_revision=row["revision"],
        changes={"ui_library": library},
    )


def test_existing_install_hook_is_idempotent_and_owner_scoped(installed):
    from tests.test_command_center_packages import _as

    with _as(installed["owner"]):
        again = api.record_install(universe_id=installed["uid"], request_id=installed["request_id"])
        rows = api.inspect_adoptions(universe_id=installed["uid"])["adoptions"]
    assert again == installed["adoption"] == rows[0]
    assert len(rows) == 1 and not rows[0]["automatic_updates"]
    with _as(installed["publisher"]), pytest.raises(PermissionError):
        api.inspect_adoptions(universe_id=installed["uid"])


def test_manual_ui_update_is_atomic_preserves_selection_other_content_and_mixed_provenance(
    installed,
):
    from tinyassets.automations import AutomationStore

    before = document(installed)
    autos = AutomationStore(installed["base"]).list(universe_id=installed["uid"])
    source = get_definition(installed["base"], installed["source_id"])
    target = replacement(installed)
    request = preview(installed, target)
    assert document(installed) == before
    assert request["plan"]["link_kind"] == "user-selected-replacement"
    assert request["plan"]["retained_definition_id"] == installed["source_id"]
    assert not request["plan"]["automatic_updates"]
    done = accept(installed, request)
    after = document(installed)
    assert done["applied"] and not done["already_applied"]
    assert after["ui_selection"] == before["ui_selection"]
    assert after["revision"] == before["revision"] + 1
    assert "Updated village" in after["ui_library"][0]["markup"]
    assert done["adoption"]["ui_definition_id"] == target
    assert done["adoption"]["retained_definition_id"] == installed["source_id"]
    assert AutomationStore(installed["base"]).list(universe_id=installed["uid"]) == autos
    assert get_definition(installed["base"], installed["source_id"]) == source
    assert accept(installed, request)["already_applied"]
    assert document(installed) == after


def test_declining_does_not_apply_and_cannot_replay_accept(installed):
    before = document(installed)
    request = preview(installed, replacement(installed))
    assert accept(installed, request, decision="declined") == {
        "applied": False,
        "decision": "declined",
    }
    assert document(installed) == before
    with pytest.raises(LookupError):
        accept(installed, request)


@pytest.mark.parametrize("when", ["before_preview", "after_preview", "after_reservation"])
def test_private_edits_block_update_without_loss(installed, monkeypatch, when):
    target = replacement(installed)
    if when == "before_preview":
        edit_recipient(installed, lambda rows: rows[0].update(markup="MY PRIVATE EDIT"))
        with pytest.raises(ValueError, match="edited or deleted"):
            preview(installed, target)
    else:
        request = preview(installed, target)
        if when == "after_reservation":
            from tinyassets import storage_accounting

            reserve = storage_accounting.reserve

            def race(*args, **kwargs):
                reservation = reserve(*args, **kwargs)
                row = document(installed)
                library = copy.deepcopy(row["ui_library"])
                library[0]["markup"] = "MY PRIVATE EDIT"
                from tinyassets.custom_agents import _canonical_json, _save_app_ui_row

                _save_app_ui_row(
                    installed["base"],
                    owner=installed["owner"],
                    uid=installed["uid"],
                    expected_revision=row["revision"],
                    library=_canonical_json(library),
                    selection=None,
                    library_doc=library,
                )
                return reservation

            monkeypatch.setattr(storage_accounting, "reserve", race)
        else:
            edit_recipient(installed, lambda rows: rows[0].update(markup="MY PRIVATE EDIT"))
        with pytest.raises(ValueError, match="edited or deleted"):
            accept(installed, request)
    assert document(installed)["ui_library"][0]["markup"] == "MY PRIVATE EDIT"


def test_dependency_changes_are_refused_before_any_effect(installed):
    def edit(components, ui):
        ui["workflow_refs"] = {}

    before = document(installed)
    with pytest.raises(ValueError, match="dependency bindings"):
        preview(installed, replacement(installed, edit))
    assert document(installed) == before


def test_wrong_digest_or_owner_cannot_accept(installed):
    from tests.test_command_center_packages import _as

    request = preview(installed, replacement(installed))
    before = document(installed)
    wrong = {**request, "plan_digest": "0" * 64}
    with pytest.raises(ValueError, match="digest mismatch"):
        accept(installed, wrong)
    with _as(installed["publisher"]), pytest.raises(PermissionError):
        api.commit_update(
            universe_id=installed["uid"],
            request_id=request["request_id"],
            plan_digest=request["plan_digest"],
            decision="accepted",
        )
    assert document(installed) == before


def test_failure_after_ui_statement_rolls_back_ui_baseline_and_request(installed, monkeypatch):
    before = document(installed)
    request = preview(installed, replacement(installed))
    real = api._inspect

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic crash before transaction commit")

    monkeypatch.setattr(api, "_inspect", fail)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        accept(installed, request)
    assert document(installed) == before
    monkeypatch.setattr(api, "_inspect", real)
    done = accept(installed, request)
    assert done["applied"] and done["adoption"]["revision"] == 2


def test_concurrent_workflow_policy_change_invalidates_preview(installed):
    request = preview(installed, replacement(installed))
    from tinyassets.custom_agents import _agent_connect

    with _agent_connect(installed["base"]) as conn:
        conn.execute(
            "UPDATE branch_definitions SET default_llm_policy_json=? WHERE branch_def_id=?",
            (
                '{"provider":"private-owner-choice"}',
                next(iter(installed["done"]["workflows"].values())),
            ),
        )
    before = document(installed)
    with pytest.raises(ValueError, match="changed since preview"):
        accept(installed, request)
    assert document(installed) == before


def test_source_withdrawal_after_preview_blocks_apply(installed):
    request = preview(installed, replacement(installed))
    from tinyassets.branch_versions import mark_versions_public

    source = get_definition(installed["base"], installed["source_id"])
    ids = [
        c["published_version_id"]
        for c in source["components"].values()
        if c["kind"] == "tinyassets.branch-ref.v1"
    ]
    mark_versions_public(installed["base"], ids, public=False)
    before = document(installed)
    with pytest.raises(ValueError, match="no longer public"):
        accept(installed, request)
    assert document(installed) == before


def test_storage_refusal_occurs_before_mutation(installed, monkeypatch):
    request = preview(installed, replacement(installed))
    before = document(installed)
    from tinyassets import storage_accounting

    def refuse(*args, **kwargs):
        raise RuntimeError("synthetic full account")

    monkeypatch.setattr(storage_accounting, "reserve", refuse)
    with pytest.raises(RuntimeError, match="full account"):
        accept(installed, request)
    assert document(installed) == before


def test_owner_revoked_between_reservation_and_commit_cannot_write(installed, monkeypatch):
    request = preview(installed, replacement(installed))
    before = document(installed)
    from tinyassets import storage_accounting
    from tinyassets.custom_agents import _agent_connect

    reserve = storage_accounting.reserve

    def revoke(*args, **kwargs):
        reservation = reserve(*args, **kwargs)
        with _agent_connect(installed["base"]) as conn:
            conn.execute(
                "DELETE FROM universe_acl WHERE universe_id=? AND actor_id=?",
                (installed["uid"], installed["owner"]),
            )
        return reservation

    monkeypatch.setattr(storage_accounting, "reserve", revoke)
    with pytest.raises(PermissionError):
        accept(installed, request)
    assert document(installed) == before


def test_missing_target_refuses_even_with_matching_source_name(installed):
    edit_recipient(installed, lambda rows: rows.clear())
    with pytest.raises(ValueError, match="edited or deleted"):
        preview(installed, replacement(installed))


def test_legacy_private_edit_cannot_be_registered_as_clean(installed):
    from tests.test_command_center_packages import _as
    from tinyassets.custom_agents import _agent_connect

    with _agent_connect(installed["base"]) as conn:
        conn.execute("DELETE FROM command_center_adoptions")
    edit_recipient(installed, lambda rows: rows[0].update(markup="PRIVATE LEGACY EDIT"))
    with _as(installed["owner"]), pytest.raises(ValueError, match="edited or deleted"):
        api.record_install(universe_id=installed["uid"], request_id=installed["request_id"])
    assert document(installed)["ui_library"][0]["markup"] == "PRIVATE LEGACY EDIT"


def test_other_home_and_non_ui_selection_refused(installed):
    from tests.test_command_center_packages import _as, _seed_owner

    _seed_owner(installed["base"], universe_id="other-home", owner=installed["owner"])
    (installed["base"] / "other-home").mkdir(exist_ok=True)
    request = preview(installed, replacement(installed))
    before = document(installed)
    with _as(installed["owner"]):
        with pytest.raises(LookupError):
            api.commit_update(
                universe_id="other-home",
                request_id=request["request_id"],
                plan_digest=request["plan_digest"],
                decision="accepted",
            )
        with pytest.raises(ValueError, match="existing UI only"):
            api.preview_update(
                universe_id=installed["uid"],
                adoption_id=installed["adoption"]["adoption_id"],
                definition_id=installed["source_id"],
                selected=("workflows",),
            )
    assert document(installed) == before


def test_replacement_preserves_an_unrelated_private_screen(installed):
    edit_recipient(
        installed,
        lambda rows: rows.append(
            {**rows[0], "ui_id": "private-extra", "name": "My private notes", "markup": "MY DATA"}
        ),
    )
    before = document(installed)
    request = preview(installed, replacement(installed))
    accept(installed, request)
    assert document(installed)["ui_library"][1] == before["ui_library"][1]


def test_manual_adapter_never_calls_install_or_inference(installed, monkeypatch):
    from tinyassets import provider_serving_binding
    from tinyassets.api import package_requests

    def forbidden(*args, **kwargs):
        raise AssertionError("manual UI update must not install components or invoke a model")

    from tinyassets.providers.router import ProviderRouter

    monkeypatch.setattr(ProviderRouter, "call_sync", forbidden)
    monkeypatch.setattr(ProviderRouter, "call_with_policy_sync", forbidden)
    monkeypatch.setattr(package_requests, "_materialise", forbidden)
    monkeypatch.setattr(provider_serving_binding, "bind_serving_provider", forbidden)
    # Removing all synthetic recipient provider bindings makes this an unpowered
    # recipient. The UI-only adapter has no provider dependency.
    from tinyassets.custom_agents import _agent_connect

    with _agent_connect(installed["base"]) as conn:
        conn.execute("DELETE FROM agent_bindings WHERE universe_id=?", (installed["uid"],))
    request = preview(installed, replacement(installed))
    assert accept(installed, request)["applied"]


def _change_retained_automation(installed, mutation):
    from datetime import datetime, timezone

    from tinyassets.automations import AutomationStore

    store = AutomationStore(installed["base"])
    row = store.list(universe_id=installed["uid"])[0]
    if mutation == "retire":
        store.retire(
            row.automation_id, expected_revision=row.revision, now=datetime.now(timezone.utc)
        )
    else:
        conn = store._connect(create=False)
        try:
            if mutation == "config":
                conn.execute(
                    "UPDATE automations SET inputs_json=? WHERE automation_id=?",
                    ('{"private_input":"edited"}', row.automation_id),
                )
            elif mutation == "owner":
                conn.execute(
                    "UPDATE automations SET owner_principal_id=? WHERE automation_id=?",
                    ("foreign-owner", row.automation_id),
                )
            elif mutation == "home":
                conn.execute(
                    "UPDATE automations SET universe_id=? WHERE automation_id=?",
                    ("foreign-home", row.automation_id),
                )
            elif mutation == "missing":
                conn.execute("DELETE FROM automations WHERE automation_id=?", (row.automation_id,))
            elif mutation == "runtime":
                conn.execute(
                    "UPDATE automations SET last_run_id=?,last_due_at=? WHERE automation_id=?",
                    ("new-runtime-run", "2026-10-04T01:00:00Z", row.automation_id),
                )
        finally:
            conn.close()


@pytest.mark.parametrize("mutation", ["retire", "config", "owner", "home", "missing"])
@pytest.mark.parametrize("when", ["after_preview", "after_reservation"])
def test_retained_automation_mutation_refuses_update(installed, monkeypatch, mutation, when):
    request = preview(installed, replacement(installed))
    before = document(installed)
    if when == "after_reservation":
        from tinyassets import storage_accounting

        reserve = storage_accounting.reserve

        def mutate(*args, **kwargs):
            reservation = reserve(*args, **kwargs)
            _change_retained_automation(installed, mutation)
            return reservation

        monkeypatch.setattr(storage_accounting, "reserve", mutate)
    else:
        _change_retained_automation(installed, mutation)
    with pytest.raises(ValueError):
        accept(installed, request)
    assert document(installed) == before


def test_runtime_only_automation_counters_do_not_invalidate_consent(installed):
    request = preview(installed, replacement(installed))
    _change_retained_automation(installed, "runtime")
    assert accept(installed, request)["applied"]


def test_automation_writer_is_blocked_until_ui_commit(installed, monkeypatch):
    from datetime import datetime, timezone
    from threading import Event, Thread

    from tinyassets.automations import AutomationStore

    request = preview(installed, replacement(installed))
    before = document(installed)
    row = AutomationStore(installed["base"]).list(universe_id=installed["uid"])[0]
    ready, start, attempted, done = Event(), Event(), Event(), Event()
    outcomes = {}

    def writer():
        store = AutomationStore(installed["base"])
        conn = store._connect(create=False)
        # Open/migrate on this thread before the main transaction. The real
        # retire method must then contend on its own BEGIN IMMEDIATE statement.
        store._connect = lambda **kwargs: conn
        conn.set_trace_callback(lambda sql: attempted.set() if sql == "BEGIN IMMEDIATE" else None)
        ready.set()
        try:
            if not start.wait(5):
                raise AssertionError("UI transaction did not reach the competing writer")
            outcomes["retired"] = store.retire(
                row.automation_id, expected_revision=row.revision, now=datetime.now(timezone.utc)
            )
            outcomes["ui_revision"] = document(installed)["revision"]
        except BaseException as exc:
            outcomes["error"] = exc
        finally:
            conn.close()
            done.set()

    thread = Thread(target=writer)
    thread.start()
    assert ready.wait(5)
    inspect = api._inspect

    def inspect_while_writer_waits(*args, **kwargs):
        start.set()
        assert attempted.wait(5), "actual automation writer did not attempt its transaction"
        assert not done.wait(0.05), "automation writer escaped the attached-database fence"
        return inspect(*args, **kwargs)

    monkeypatch.setattr(api, "_inspect", inspect_while_writer_waits)
    try:
        assert accept(installed, request)["applied"]
    finally:
        start.set()
        thread.join(5)
    assert done.is_set() and "error" not in outcomes, outcomes
    assert outcomes["retired"].retired_at
    assert outcomes["ui_revision"] == before["revision"] + 1


def test_no_automation_database_is_created_for_empty_store(tmp_path):
    from tinyassets.automations import automations_db_path
    from tinyassets.command_center_update_registry import connect

    path = automations_db_path(tmp_path)
    assert not path.exists()
    with connect(tmp_path) as conn:
        assert "retained_automations" not in {
            row[1] for row in conn.execute("PRAGMA database_list")
        }
    assert not path.exists()


def test_missing_automation_database_with_dependencies_refuses_without_recreation(installed):
    from tinyassets.automations import automations_db_path

    request = preview(installed, replacement(installed))
    path = automations_db_path(installed["base"])
    # Connections are closed; SQLite has checkpointed its fixture WAL.
    path.rename(path.with_suffix(".held-for-test"))
    before = document(installed)
    with pytest.raises(ValueError, match="automation store is missing"):
        accept(installed, request)
    assert not path.exists() and document(installed) == before


def test_automation_lock_released_after_rollback(installed, monkeypatch):
    from datetime import datetime, timezone

    from tinyassets.automations import AutomationStore

    request = preview(installed, replacement(installed))
    before = document(installed)
    monkeypatch.setattr(
        api, "_inspect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rollback"))
    )
    with pytest.raises(RuntimeError, match="rollback"):
        accept(installed, request)
    store = AutomationStore(installed["base"])
    row = store.list(universe_id=installed["uid"])[0]
    assert store.retire(
        row.automation_id, expected_revision=row.revision, now=datetime.now(timezone.utc)
    ).retired_at
    assert document(installed) == before


def test_missing_automation_schema_fails_closed(installed):
    import sqlite3

    from tinyassets.automations import automations_db_path

    request = preview(installed, replacement(installed))
    before = document(installed)
    with sqlite3.connect(automations_db_path(installed["base"])) as conn:
        conn.execute("ALTER TABLE automations RENAME TO unavailable_automations")
    with pytest.raises(ValueError, match="automation store schema is unavailable"):
        accept(installed, request)
    assert document(installed) == before
