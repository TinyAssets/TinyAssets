"""Real stores and publisher/recipient routes for current-version discovery."""
from __future__ import annotations

import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.engine_authority_helpers import mock_engine_admission
from tests.test_command_center_packages import (
    BOB,
    BOB_UNIVERSE,
    OWNER,
    UNIVERSE,
    _answer,
    _as,
    _ask,
    _blob_files,
    _bob_files,
    _install,
    _pin_data_dir,  # noqa: F401
    _publish_action,
    _write,
    cloud_runtime,  # noqa: F401
)
from tests.test_command_center_packages import home as home
from tinyassets import custom_agents as ca
from tinyassets.api.package_requests import list_packages
from tinyassets.command_center_packages import check_blob, read_blob
from tinyassets.commons_bundles import publication_bundle
from tinyassets.storage import db_path

pytestmark = pytest.mark.usefixtures("cloud_runtime")


def _publish(base, action=None):
    ask = _ask(OWNER, UNIVERSE, action or _publish_action())
    assert "request_id" in ask, ask
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert done.get("published"), done
    return ca.get_definition(base, done["agent_definition_id"], include_catalogue=True)


def test_republish_supersedes_with_history_and_installed_copy_unchanged(home):
    first = _publish(home)
    old_files = _blob_files(home, first["agent_definition_id"])
    ask = _install(home, first["agent_definition_id"])
    assert _answer(BOB, BOB_UNIVERSE, ask["request_id"])["installed"]
    installed = _bob_files(home)
    old_ui = ca.get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
    _write(home / UNIVERSE, "notes/board.md", "# A newly published board\n")
    second = _publish(home)
    assert first["bundle_id"] == second["bundle_id"]
    assert second["bundle_version"] == 2
    assert second["previous_definition_id"] == first["agent_definition_id"]
    with _as(BOB):
        assert [r["agent_definition_id"] for r in list_packages()] == [
            second["agent_definition_id"]]
        old = ca.get_definition(home, second["previous_definition_id"], include_catalogue=True)
    assert old["portable_definition"] == first["portable_definition"]
    assert old["content_fingerprint"] == first["content_fingerprint"]
    assert old["current_definition_id"] == second["agent_definition_id"]
    assert old["previous_definition_id"] == ""
    assert _blob_files(home, first["agent_definition_id"]) == old_files
    assert _bob_files(home) == installed
    assert ca.get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE) == old_ui
    manifest, _ = check_blob(read_blob(home, second["components"]["package"]["blob_sha256"]))
    assert manifest["bundle_id"] == second["bundle_id"]


def test_author_fence_and_remix_identity(home):
    first = _publish(home)
    payload = first["portable_definition"]
    with _as(BOB), pytest.raises(ca.AgentValidationError, match="original author"):
        ca.publish_definition(home, author_id=BOB, payload=payload)
    remix = ca.import_definition(home, author_id=BOB, portable_definition=payload,
                                 idempotency_key="remix")
    assert remix["portable_definition"]["bundle_id"] != first["bundle_id"]
    assert remix["author_id"] == BOB
    assert ca.import_definition(home, author_id=BOB, portable_definition=payload,
                                idempotency_key="remix") == remix
    assert ca.get_definition(home, first["agent_definition_id"], include_catalogue=True)[
        "current_definition_id"] == first["agent_definition_id"]


def test_rename_filters_and_pagination_consider_only_current_versions(home):
    first = _publish(home)
    renamed = {**_publish_action(), "name": "Renamed village", "description": "Current text"}
    second = _publish(home, renamed)
    assert second["bundle_id"] == first["bundle_id"]
    assert second["components"]["package"]["version"] == 2
    assert list_packages(query="GTM") == []
    assert list_packages(limit=1, offset=1) == []
    assert list_packages(query="Renamed")[0]["agent_definition_id"] == second["agent_definition_id"]


@pytest.mark.parametrize("package", [True, False])
def test_same_name_different_screen_and_different_author_are_distinct(home, package):
    from tinyassets.api.system_copy_requests import list_systems

    action = _publish_action()
    if not package:
        action.pop("package")
    first = _publish(home, action)
    doc = ca.get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)
    other = {**doc["ui_library"][0], "ui_id": "other-village"}
    ca.save_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE,
                   expected_revision=doc["revision"],
                   changes={"ui_library": doc["ui_library"] + [other]})
    second = _publish(home, {**action, "ui_id": "other-village"})
    bob_bundle = publication_bundle(home, author=BOB, universe_id=BOB_UNIVERSE,
                                    action=_publish_action())
    assert len({first["bundle_id"], second["bundle_id"], bob_bundle}) == 3
    assert len(list_packages() if package else list_systems()) == 2


@pytest.mark.parametrize("kind", ["packages", "agents", "systems"])
def test_second_account_discovers_foreign_current_version_through_engine(home, monkeypatch, kind):
    from tinyassets import engine_mcp_server as engine

    action = _publish_action()
    if kind == "systems":
        action.pop("package")
    _publish(home, action)
    _write(home / UNIVERSE, "notes/board.md", "# Changed\n")
    if kind == "systems":
        action["description"] = "A revised system"
    current = _publish(home, action)
    monkeypatch.setattr(engine, "_ACTOR_ID", BOB)
    monkeypatch.setattr(engine, "_GRAPH_ID", BOB_UNIVERSE)
    mock_engine_admission(monkeypatch, {BOB_UNIVERSE})
    out = json.loads(engine.browse_commons(kind=kind, query="village"))
    assert out["untrusted"] is True
    [row] = out["content"][kind]
    assert row["author_id"] == OWNER
    assert row["bundle_id"] == current["bundle_id"]
    assert row["agent_definition_id"] == current["agent_definition_id"]
    assert row["previous_definition_id"] == current["previous_definition_id"]
    assert "source_key" not in json.dumps(out)
    assert UNIVERSE not in json.dumps(out)
    history = json.loads(engine.read_commons_shape(
        agent_definition_id=row["previous_definition_id"]))
    assert history["content"]["agent"]["bundle_id"] == current["bundle_id"]
    narrowed = json.loads(engine.browse_commons(kind=kind, author=BOB, query="village"))
    assert narrowed["content"][kind] == []


@pytest.mark.parametrize("legacy_owner", [None, "", "wrong-owner"])
def test_legacy_activated_pins_backfill_without_rewriting_definitions(home, legacy_owner):
    from tinyassets.command_center_packages import database_path

    first = _publish(home)
    _write(home / UNIVERSE, "notes/board.md", "# Later legacy content\n")
    second = _publish(home)
    with sqlite3.connect(db_path(home)) as conn:
        # Model the pre-bundle schema and definition bytes exactly, not name inference.
        conn.execute("DELETE FROM commons_bundle_versions")
        conn.execute("DELETE FROM commons_bundle_sources")
        conn.execute("DELETE FROM commons_bundles")
        for definition in (first, second):
            portable = copy.deepcopy(definition["portable_definition"])
            portable.pop("bundle_id")
            portable.pop("content_fingerprint")
            portable["components"]["package"].pop("bundle_id")
            conn.execute(
                "UPDATE agent_definitions SET portable_json=?, components_json=?, "
                "content_fingerprint=? WHERE agent_definition_id=?",
                (json.dumps(portable), json.dumps(portable["components"]),
                 ca._fingerprint(portable), definition["agent_definition_id"]))
        before = conn.execute("SELECT portable_json, content_fingerprint FROM agent_definitions "
                              "ORDER BY agent_definition_id").fetchall()
    with sqlite3.connect(database_path(home)) as conn:
        conn.execute("UPDATE pins SET activated_at=-created_at WHERE kind='publish'")
        if legacy_owner is None:
            conn.execute("ALTER TABLE pins DROP COLUMN owner_id")
        else:
            conn.execute("UPDATE pins SET owner_id=?", (legacy_owner,))
    ca._SCHEMA_INITIALIZED.discard(str(db_path(home)))
    listed = list_packages()
    if legacy_owner == "wrong-owner":
        assert len(listed) == 2
        assert all("bundle_id" not in row for row in listed)
    else:
        assert len(listed) == 1
        assert listed[0]["agent_definition_id"] == second["agent_definition_id"]
        assert listed[0]["previous_definition_id"] == first["agent_definition_id"]
    with sqlite3.connect(db_path(home)) as conn:
        after = conn.execute("SELECT portable_json, content_fingerprint FROM agent_definitions "
                             "ORDER BY agent_definition_id").fetchall()
    assert after == before


def test_unproven_legacy_rows_are_not_grouped_by_name(home):
    payload = {"schema_version": 1, "name": "Same", "components": {"x": {"kind": "test"}}}
    a = ca.publish_definition(home, author_id=OWNER, payload=payload)
    b = ca.publish_definition(home, author_id=OWNER, payload=payload)
    assert len(ca.list_definitions(home, query="Same")) == 2
    assert "bundle_id" not in a and "bundle_id" not in b


def test_competing_writers_append_and_sql_fault_rolls_back(home):
    bundle = publication_bundle(home, author=OWNER, universe_id=UNIVERSE,
                                action={"ui_id": "system", "name": "System"})
    payload = {"schema_version": 1, "bundle_id": bundle, "name": "System",
               "components": {"x": {"kind": "test"}}}
    def publish(n):
        definition = ca.publish_definition(home, author_id=OWNER, payload=payload,
                                           idempotency_key=f"writer-{n}")
        return ca.get_definition(home, definition["agent_definition_id"], include_catalogue=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(publish, range(2)))
    assert sorted(r["bundle_version"] for r in rows) == [1, 2]
    [head] = ca.list_definitions(home, query="System")
    assert head["bundle_version"] == 2
    with sqlite3.connect(db_path(home)) as conn:
        conn.execute("CREATE TRIGGER refuse_bundle BEFORE INSERT ON commons_bundle_versions "
                     "BEGIN SELECT RAISE(ABORT, 'injected fault'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected fault"):
        publish(2)
    assert ca.list_definitions(home, query="System") == [head]
    with sqlite3.connect(db_path(home)) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM agent_definitions WHERE name='System'").fetchone()[0]
        assert count == 2


def test_stale_package_publish_refuses_instead_of_overwriting_current(home):
    ask = _ask(OWNER, UNIVERSE, _publish_action())
    # A distinct consent request pins the same version, before the first commits.
    other = copy.deepcopy(_publish_action())
    other["description"] = "Another concurrent publication"
    ask2 = _ask(OWNER, UNIVERSE, other)
    assert _answer(OWNER, UNIVERSE, ask["request_id"])["published"]
    refused = _answer(OWNER, UNIVERSE, ask2["request_id"])
    assert refused["error"] == "publish_refused"
    assert len(list_packages()) == 1


def test_pinned_system_install_survives_a_new_current_version(home):
    from tests.test_command_center_system_copy import _preview

    action = _publish_action()
    action.pop("package")
    first = _publish(home, action)
    immutable = ca.get_definition(home, first["agent_definition_id"])
    ask = _preview(first["agent_definition_id"])
    _publish(home, {**action, "description": "A revised system"})
    assert ca.get_definition(home, first["agent_definition_id"]) == immutable
    done = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert done["installed"], done
