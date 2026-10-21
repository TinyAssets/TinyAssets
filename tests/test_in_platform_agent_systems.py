"""A universe's own multi-agent system: its files, its wakes, its consented publish.

Change `in-platform-agent-systems`. Every test goes through the connector's own
handlers (`read_graph`, `run_graph`, `request_from_user`, `answer_request`) as a
signed-in user, against real stores in a private data root. The cross-user cases
are the point: Alice's folder, Alice's agents and Alice's consent must be out of
Bob's reach whatever he names.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_automations import OWNER, UNIVERSE, _seed_branch, _seed_owner
from tests.test_background_budget_finalization_e2e import _seed_serving_assignment
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.automations import (
    TRIGGER_ONCE,
    AutomationStore,
    register_automation,
)

pytestmark = pytest.mark.usefixtures("cloud_runtime")

BOB = "acct_bob"
BOB_UNIVERSE = "universe_bob"
SCOUT = "branch_scout"
SCRIBE = "branch_scribe"
BOBS = "branch_bobs_own"
UI = {
    "kind": "tinyassets.app-ui.v1", "version": 1, "ui_id": "village",
    "name": "Village", "markup": "<div id=v></div>", "style": "#v{}",
    "script": "tinyassets.listAutomations()",
}


@pytest.fixture(autouse=True)
def _pin_data_dir(tmp_path: Path, monkeypatch):
    from tests.support.shared_catalog import install

    install(monkeypatch, tmp_path)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))


def _as(actor: str | None):
    if actor is None:
        return identity_context(None)
    return identity_context(Identity(
        user_id=actor, username=actor,
        capabilities=["tinyassets.universe.write", "read", "list", "write", "costly",
                      "tinyassets.extensions.write"],
    ))


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """Alice owns a home with two private workflows; Bob owns his own home."""
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path, branch_def_id=SCOUT, visibility="private")
    _seed_branch(tmp_path, branch_def_id=SCRIBE, visibility="private")
    _seed_owner(tmp_path, universe_id=BOB_UNIVERSE, owner=BOB)
    _seed_branch(tmp_path, branch_def_id=BOBS, author=BOB, visibility="private")
    folder = tmp_path / UNIVERSE
    (folder / "notes").mkdir(parents=True, exist_ok=True)
    (folder / "notes" / "board.md").write_bytes("# Board\n- scout: café ☕ found\n".encode())
    (folder / "notes" / "blob.bin").write_bytes(bytes(range(256)))
    (tmp_path / BOB_UNIVERSE / "secret.md").write_text("BOB ONLY", encoding="utf-8")
    return tmp_path


def _read(target: str, query: str, *, actor: str = OWNER, universe: str = UNIVERSE, **kw):
    from tinyassets.universe_server import read_graph

    with _as(actor):
        return json.loads(read_graph(target=target, graph_id=universe, query=query, **kw))


# ---------------------------------------------------------------------------
# 1. The owner reads their own folder, and nobody else can
# ---------------------------------------------------------------------------










def _link_dir(link: Path, target: Path) -> None:
    """A directory link the host can make without privilege: a junction on
    Windows, a symlink elsewhere. Skips when neither can be made."""
    import shutil
    import subprocess

    if link.exists() or link.is_symlink():
        shutil.rmtree(link) if link.is_dir() and not link.is_symlink() else link.unlink()
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, text=True)
        if made.returncode != 0:
            pytest.skip("this host cannot create a junction")
        return
    os.symlink(target, link, target_is_directory=True)








# ---------------------------------------------------------------------------
# 2. An app event wakes only the owner's own subscription to that name
# ---------------------------------------------------------------------------


def _subscribe(base: Path, name: str | None, *, owner=OWNER, universe=UNIVERSE, branch=SCRIBE):
    with _as(owner):
        return register_automation(
            base, universe_id=universe, owner_principal_id=owner, name="on-click",
            branch_def_id=branch, event_type="app_event",
            event_filter=({"name": name} if name else {}), inputs={},
        )


def _emit(name, data=None, *, actor=OWNER, universe=UNIVERSE):
    from tinyassets.universe_server import run_graph

    with _as(actor):
        return json.loads(run_graph(operation="emit_event", graph_id=universe,
                                    inputs_json=json.dumps({"name": name, "data": data or {}})))


def _wakes(base: Path, universe: str = UNIVERSE):
    return [r for r in AutomationStore(base).list(universe_id=universe)
            if r.trigger_kind == TRIGGER_ONCE]










# ---------------------------------------------------------------------------
# 3. Publishing is the owner's confirmation of exactly what they were shown
# ---------------------------------------------------------------------------


def _library(base: Path) -> None:
    from tinyassets.app_ui_store import save_app_ui

    save_app_ui(base, owner_user_id=OWNER, universe_id=UNIVERSE, expected_revision=0,
                changes={"ui_library": [UI]})


def _automations(base: Path):
    with _as(OWNER):
        beat = register_automation(
            base, universe_id=UNIVERSE, owner_principal_id=OWNER, name="scout heartbeat",
            branch_def_id=SCOUT, interval_seconds=300, inputs={"api_note": "PRIVATE INPUT"},
        )
        follow = register_automation(
            base, universe_id=UNIVERSE, owner_principal_id=OWNER, name="scribe follows",
            branch_def_id=SCRIBE, event_type="run_completed",
            event_filter={"branch_def_id": SCOUT}, inputs={},
        )
    return beat, follow


def _ask_publish(base: Path, **over):
    from tinyassets.api.pending_requests import request_from_user

    action = {"type": "publish", "name": "Village", "description": "Two agents and a screen",
              "branch_ids": [SCOUT, SCRIBE], "ui_id": "village", "automation_ids": []}
    action.update(over)
    with _as(OWNER):
        return request_from_user(universe_id=UNIVERSE, payload=json.dumps({
            "kind": "Just click", "title": "Harmless, just click yes",
            "body": "Nothing will be shared.", "action": action,
        }))


def _answer(request_id: str, *, actor: str = OWNER, **extra):
    from tests.owner_answer import answer_request

    with _as(actor):
        return answer_request(universe_id=UNIVERSE, payload=json.dumps(
            {"request_id": request_id, "values": {}, **extra}))


def _visibility(base: Path, branch: str) -> str:
    from tinyassets.daemon_server import get_branch_definition

    return get_branch_definition(base, branch_def_id=branch).get("visibility")


















def _raw_edit(base: Path, branch: str, mutate) -> None:
    """Rewrite ONE stored column, ``node_defs_json``, and nothing else.

    The shapes a model-level patch never produces are exactly the ones a
    remembered field list misses. A whole-row save would also re-derive other
    columns (``graph``), and the test would then pass because of THAT change
    rather than the one it names -- which is how the first version of this
    helper passed against a mutant that dropped unknown keys.
    """
    import sqlite3

    from tinyassets.daemon_server import get_branch_definition
    from tinyassets.storage import db_path

    raw = get_branch_definition(base, branch_def_id=branch)
    before = {k: v for k, v in raw.items() if k != "node_defs"}
    mutate(raw)
    with sqlite3.connect(db_path(base)) as conn:
        conn.execute("UPDATE branch_definitions SET node_defs_json = ? WHERE branch_def_id = ?",
                     (json.dumps(raw["node_defs"]), branch))
    after = get_branch_definition(base, branch_def_id=branch)
    assert {k: v for k, v in after.items() if k != "node_defs"} == before, "only node_defs moved"






def _bob_reads_version(version_id: str) -> str:
    from tinyassets.api.extensions import _extensions_impl

    with _as(BOB):
        return _extensions_impl(action="get_branch_version", branch_version_id=version_id)




















# ---------------------------------------------------------------------------
# 4. A second user installs: every copy is theirs and private
# ---------------------------------------------------------------------------


