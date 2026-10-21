"""A universe's own multi-agent system: its files, its wakes, its consented publish.

Change `in-platform-agent-systems`. Every test goes through the connector's own
handlers (`read_graph`, `run_graph`, `request_from_user`, `answer_request`) as a
signed-in user, against real stores in a private data root. The cross-user cases
are the point: Alice's folder, Alice's agents and Alice's consent must be out of
Bob's reach whatever he names.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_automations import OWNER, UNIVERSE, _seed_branch, _seed_owner
from tests.test_background_budget_finalization_e2e import _seed_serving_assignment
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.automations import (
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


















# ---------------------------------------------------------------------------
# 2. An app event wakes only the owner's own subscription to that name
# ---------------------------------------------------------------------------
















# ---------------------------------------------------------------------------
# 3. Publishing is the owner's confirmation of exactly what they were shown
# ---------------------------------------------------------------------------




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




def _answer(request_id: str, *, actor: str = OWNER, **extra):
    from tests.owner_answer import answer_request

    with _as(actor):
        return answer_request(universe_id=UNIVERSE, payload=json.dumps(
            {"request_id": request_id, "values": {}, **extra}))














































# ---------------------------------------------------------------------------
# 4. A second user installs: every copy is theirs and private
# ---------------------------------------------------------------------------


