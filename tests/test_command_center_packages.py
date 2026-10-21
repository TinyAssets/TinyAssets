"""A whole command center as one package: Alice publishes, Bob installs and runs it.

Change `command-center-packages`. Every round trip goes through the real
handlers (`request_from_user`, `answer_request`, `list_requests`) as signed-in
users against real stores in a private data root. The cross-user cases are the
point: nothing private of Alice's reaches the package or Bob's command center,
and nothing of the package exists in Bob's until Bob confirms.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_automations import (
    OWNER,
    UNIVERSE,
    _seed_branch,
    _seed_owner,
)
from tests.test_background_budget_finalization_e2e import _seed_serving_assignment
from tests.test_in_platform_agent_systems import (
    BOB,
    BOB_UNIVERSE,
    SCOUT,
    SCRIBE,
    UI,
    _as,
)
from tinyassets import command_center_packages as ccp
from tinyassets.automations import register_automation

pytestmark = pytest.mark.usefixtures("cloud_runtime")

SECRET_KEY = "sk-live-" + "Zq8rT2vX9mK4pL7nB3wE6yH1jD5"
ALICE_EMAIL = "alice.private@example.com"


@pytest.fixture(autouse=True)
def _pin_data_dir(tmp_path: Path, monkeypatch):
    from tests.support.shared_catalog import install

    install(monkeypatch, tmp_path)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))


def _write(folder: Path, rel: str, data: str | bytes) -> None:
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


#: Alice's whole command center: what travels, and what must never.
TRAVELS = {
    "AGENTS.md": "# Village lead\n## Responsibility\nRun the GTM village.\n",
    "identity.md": "---\ntype: identity\n---\nI am the village lead.\n",
    "settings.yaml": "model: openrouter/free\n",
    "skills/scout/SKILL.md": "# Scout\nFind leads and write them to notes/board.md.\n",
    "agents/scribe/AGENTS.md": "# Scribe\nSummarise the board every hour.\n",
    "notes/board.md": "# Board\n- scout: three bakeries found\n",
    "wiki/pages/village.md": "# Village\nHow the village works.\n",
}
PRIVATE = {
    "founder.md": "Alice lives on Elm Street and hates mornings.\n",
    "soul.md": "---\nloop_branch_def_id: x\n---\nsoul\n",
    "log.md": "private brain history\n",
    "activity.log": "runtime\n",
    ".credentials.json": '{"token": "' + SECRET_KEY + '"}',
    ".runtime/agent-sessions/native/s.jsonl": "session transcript\n",
    "notes/keys.md": "my key: " + SECRET_KEY + "\n",
    "notes/leads.md": "write to " + ALICE_EMAIL + " tomorrow\n",
    "notes/photo.png": bytes(range(256)),
    "wiki/drafts/plan.md": "unfinished private plan\n",
    "agents/scribe/MEMORY.md": "- [m_s1] Alice's scribe memory\n",
    "workspaces/repo/README.md": "a managed checkout\n",
    "ledger.db": b"SQLite format 3\x00rest",
    f"notes/{ALICE_EMAIL}.md": "harmless body\n",
}
MEMORY = "# Memory\n- [m_pub] Prefers short summaries\n- [m_priv] Alice's bank is Acme\n"


def _seed_bob_serving(base: Path) -> None:
    """Bob's own HTTP grant runs his installed copies."""
    from tests.support.http_model import seed_http_assignment
    seed_http_assignment(base, universe_id=BOB_UNIVERSE, owner_user_id=BOB)


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """Alice's built village, and Bob's own small command center."""
    from tests.support.shared_catalog import install

    install(monkeypatch, tmp_path)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path, branch_def_id=SCOUT, visibility="private")
    _seed_branch(tmp_path, branch_def_id=SCRIBE, visibility="private")
    _seed_owner(tmp_path, universe_id=BOB_UNIVERSE, owner=BOB)
    _seed_bob_serving(tmp_path)
    alice = tmp_path / UNIVERSE
    for rel, data in {**TRAVELS, **PRIVATE, "MEMORY.md": MEMORY}.items():
        _write(alice, rel, data)
    bob = tmp_path / BOB_UNIVERSE
    _write(bob, "AGENTS.md", "# Bob's own agent\n")
    from tinyassets.app_ui_store import save_app_ui

    save_app_ui(tmp_path, owner_user_id=OWNER, universe_id=UNIVERSE, expected_revision=0,
                changes={"ui_library": [UI]})
    return tmp_path


def _automations(base: Path):
    with _as(OWNER):
        beat = register_automation(
            base, universe_id=UNIVERSE, owner_principal_id=OWNER, name="scout heartbeat",
            branch_def_id=SCOUT, interval_seconds=300, inputs={"api_note": "PRIVATE INPUT"})
        follow = register_automation(
            base, universe_id=UNIVERSE, owner_principal_id=OWNER, name="scribe follows",
            branch_def_id=SCRIBE, event_type="run_completed",
            event_filter={"branch_def_id": SCOUT}, inputs={})
    return beat, follow


def _ask(actor: str, universe: str, action: dict) -> dict:
    from tinyassets.api.pending_requests import request_from_user

    with _as(actor):
        return request_from_user(universe_id=universe, payload=json.dumps({
            "kind": "Just click", "title": "Harmless, just click yes",
            "body": "Nothing will be shared.", "action": action}))


def _answer(actor: str, universe: str, request_id: str, values: dict | None = None) -> dict:
    from tests.owner_answer import answer_request

    with _as(actor):
        return answer_request(universe_id=universe, payload=json.dumps(
            {"request_id": request_id, "values": values or {}}))


def _publish_action(**package) -> dict:
    return {"type": "publish", "name": "GTM Village", "description": "A village of agents",
            "branch_ids": [SCOUT, SCRIBE], "ui_id": "village", "automation_ids": [],
            "package": package}


def _published(home: Path, **package) -> dict:
    beat, follow = _automations(home)
    action = _publish_action(**package)
    action["automation_ids"] = [beat.automation_id, follow.automation_id]
    ask = _ask(OWNER, UNIVERSE, action)
    assert "request_id" in ask, ask
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert done.get("published") is True, done
    return {"ask": ask, "done": done}


def _blob_files(home: Path, definition_id: str) -> dict[str, bytes]:
    from tinyassets.commons_catalog import get_definition

    component = get_definition(home, definition_id)["components"]["package"]
    _, files = ccp.check_blob(ccp.read_blob(home, component["blob_sha256"]))
    return files


def _bob_files(home: Path) -> dict[str, bytes]:
    root = home / BOB_UNIVERSE
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file() and not any(
                part.startswith(".") for part in p.relative_to(root).parts)}


# ---------------------------------------------------------------------------
# 1. The scrub: what a published package carries
# ---------------------------------------------------------------------------


def test_publish_carries_the_command_center_and_none_of_its_private_items(home: Path):
    out = _published(home, memory_items=["m_pub"])
    files = _blob_files(home, out["done"]["agent_definition_id"])
    assert set(files) == set(TRAVELS) | {"MEMORY.md"}
    for rel, data in TRAVELS.items():
        assert files[rel] == data.encode("utf-8")
    assert b"m_pub" in files["MEMORY.md"] and b"m_priv" not in files["MEMORY.md"]
    everything = b"".join(files.values()) + json.dumps(out["done"]).encode()
    for needle in (SECRET_KEY, ALICE_EMAIL, "Elm Street", "session transcript",
                   "unfinished private plan", "Alice's scribe memory", "Acme"):
        assert needle.encode() not in everything, needle






def test_classify_owner_choices_never_lift_a_detection():
    leaked = ("- [m_a] key: " + SECRET_KEY).encode()
    # Naming the memory item cannot carry a credential out with it.
    assert ccp.classify("MEMORY.md", leaked, exclude=[],
                        memory_items={"MEMORY.md": ["m_a"]}) == (None, ccp.R_CREDENTIAL)
    assert ccp.classify("notes/a.md", b"fine", exclude=["notes"],
                        memory_items={}) == (None, ccp.R_EXCLUDED)
    assert ccp.classify("notes/ab.md", b"fine", exclude=["notes/a"],
                        memory_items={}) == (b"fine", "")




# ---------------------------------------------------------------------------
# 2. The consent record is the platform's, not the agent-writable row
# ---------------------------------------------------------------------------


def _rewrite_row(home: Path, universe: str, request_id: str, **changes) -> None:
    """What an agent with bash in its own folder can do to its pending row."""
    from tinyassets.agent_activities import store_path
    from tinyassets.storage.pending_requests import get_request
    db = store_path(home / universe)
    row = get_request(home / universe, request_id)
    row.update(changes)
    identity = [row["kind"], row["title"], row["body"], row["fields"], row["action"]]
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE pending_requests SET kind = ?, title = ?, body = ?, action_json = ?, "
            "dedupe_key = ? WHERE request_id = ?",
            (row["kind"], row["title"], row["body"], json.dumps(row["action"]),
             json.dumps(identity, sort_keys=True, separators=(",", ":")), request_id))




def test_a_rewritten_action_executes_the_pinned_one(home: Path):
    from tinyassets.commons_catalog import get_definition

    ask = _ask(OWNER, UNIVERSE, _publish_action())
    forged = dict(ask["action"])
    forged["name"] = "Something else entirely"
    _rewrite_row(home, UNIVERSE, ask["request_id"], action=forged)
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert done.get("published") is True, done
    assert get_definition(home, done["agent_definition_id"])["name"] == "GTM Village"




def test_a_publish_row_with_no_pin_cannot_be_confirmed(home: Path):
    ask = _ask(OWNER, UNIVERSE, _publish_action())
    with sqlite3.connect(ccp.store_dir(home) / "packages.db") as conn:
        conn.execute("DELETE FROM pins")
    out = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert "no longer be confirmed" in out.get("error", "") + out.get("detail", ""), out


# ---------------------------------------------------------------------------
# 3. Quota: the package is charged to its publisher, and refused with its size
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# 4. Install: discover, quarantine, activate, run -- as Bob
# ---------------------------------------------------------------------------


def _install(home: Path, definition_id: str) -> dict:
    return _ask(BOB, BOB_UNIVERSE, {"type": "install", "agent_definition_id": definition_id})




def _bobs_branches(home: Path) -> list[dict]:
    from tinyassets.daemon_server import list_branch_definitions

    return list_branch_definitions(home, author=BOB, include_private=True)












# ---------------------------------------------------------------------------
# 5. The ingestion boundary
# ---------------------------------------------------------------------------


def _hostile_package(home: Path, files: dict[str, bytes], *, listed=None) -> str:
    """A definition carrying a package whose blob was written by hand."""
    from tinyassets.commons_catalog import publish_definition

    manifest = ccp.build_manifest(profile=ccp.PROFILE_PUBLISH, name="Hostile",
                                  description="", files={}, workflows=[], ui="",
                                  automations=[], connections=[])
    manifest["files"] = listed if listed is not None else [
        {"path": p, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()}
        for p, b in files.items()]
    blob = json.dumps({"format_version": ccp.FORMAT_VERSION, "manifest": manifest,
                       "files": {p: base64.b64encode(b).decode() for p, b in files.items()}},
                      sort_keys=True).encode()
    sha = ccp.store_blob(home, author_id="acct_mallory", blob=blob)
    definition = publish_definition(home, author_id="acct_mallory", payload={
        "schema_version": 1, "name": "Hostile", "description": "",
        "tags": [ccp.PACKAGE_TAG], "components": {"package": {
            "kind": ccp.PACKAGE_KIND, "format_version": 1, "version": 1,
            "blob_sha256": sha, "size_bytes": len(blob), "file_count": len(files),
            "agents": [], "needs": {"model": "", "connections": []}}}})
    return definition["agent_definition_id"]








# ---------------------------------------------------------------------------
# 6. Activation is claimed once
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# 7. A cross-author remix copies the snapshot, never the live source
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# 8. The owner's switches, and the "worth a look" list (lead, 2026-10-01)
# ---------------------------------------------------------------------------


def _switch(ask: dict, label_start: str) -> str:
    return next(f["name"] for f in ask["fields"] if f["label"].startswith(label_start))








# ---------------------------------------------------------------------------
# 9. Code refute round 1
# ---------------------------------------------------------------------------
















# ---------------------------------------------------------------------------
# 10. Code refute round 2
# ---------------------------------------------------------------------------




def test_a_switch_publishes_the_verified_bytes_not_a_later_edit(home: Path, monkeypatch):
    from tinyassets.api import publish_requests

    ask = _ask(OWNER, UNIVERSE, _publish_action())
    real = publish_requests.build_snapshot
    calls = []

    def edit_after_the_check(uid, action):
        snap = real(uid, action)
        calls.append(1)
        # The board changes right after the verified read.
        _write(home / UNIVERSE, "notes/board.md", "# Board\n- EDITED AFTER CONSENT\n")
        return snap

    monkeypatch.setattr(publish_requests, "build_snapshot", edit_after_the_check)
    done = _answer(OWNER, UNIVERSE, ask["request_id"],
                   {_switch(ask, "wiki/"): "Leave out"})
    assert done.get("published") is True, done
    assert len(calls) == 1
    files = _blob_files(home, done["agent_definition_id"])
    assert files["notes/board.md"] == TRAVELS["notes/board.md"].encode()
    assert "wiki/pages/village.md" not in files








































# ---------------------------------------------------------------------------
# 11. Published key formats are certain anywhere; id-shaped hex is never flagged
# ---------------------------------------------------------------------------

KEY_FORMATS = [
    "sk-proj-" + "9dKq3fZmRvT8yXaLpQwE2nBcHjUiOsAb12",
    "sk-ant-api03-" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
    "sk-or-v1-" + "0123456789abcdef" * 4,
    "ghp_" + "16C7e42F292c6912E7710c838347Ae178B4a",
    "github_pat_" + "11ABCDEFG0" + "aBcDeFgHiJ" * 7 + "kL",
    "AKIA" + "IOSFODNN7EXAMPLE",
    "xoxb-" + "2345678901-2345678901234-AbCdEfGhIjKlMnOpQrStUvWx",
    "AIza" + "SyD-abcdefghijklmnopqrstuvwxyz01234",
    "glpat-" + "AbCdEfGhIjKlMnOpQrSt",
    "sk_live_" + "51H8ZqKLmNoPqRsTuVwXyZaBcDe",
    "rk_live_" + "51H8ZqKLmNoPqRsTuVwXyZaBcDe",
    "hf_" + "QwErTyUiOpAsDfGhJkLzXcVbNmQwErTyUi",
    "npm_" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
]












# ---------------------------------------------------------------------------
# 11. Install-side content screen (ClawHub poisoning wave)
# ---------------------------------------------------------------------------







































