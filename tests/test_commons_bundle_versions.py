"""Real stores and publisher/recipient routes for current-version discovery."""
from __future__ import annotations

import copy
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

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
from tinyassets import app_ui_store
from tinyassets import commons_catalog as ca
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
    old_ui = app_ui_store.get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
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
    assert app_ui_store.get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE) == old_ui
    manifest, _ = check_blob(read_blob(home, second["components"]["package"]["blob_sha256"]))
    assert manifest["bundle_id"] == second["bundle_id"]


def test_publish_ask_creates_no_bundle_row_before_approval(home):
    """The catalogue row is created when the owner approves, not when asked."""
    import sqlite3

    from tinyassets.storage import db_path

    ask = _ask(OWNER, UNIVERSE, _publish_action())
    assert "request_id" in ask, ask
    with sqlite3.connect(db_path(home)) as conn:
        bundles = conn.execute("SELECT COUNT(*) FROM commons_bundles").fetchone()[0]
        sources = conn.execute(
            "SELECT COUNT(*) FROM commons_bundle_sources"
        ).fetchone()[0]
    assert bundles == 0
    assert sources == 0
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert done.get("published"), done
    with sqlite3.connect(db_path(home)) as conn:
        bundles = conn.execute("SELECT COUNT(*) FROM commons_bundles").fetchone()[0]
    assert bundles == 1












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
