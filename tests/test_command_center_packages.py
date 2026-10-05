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
import os
import sqlite3
from pathlib import Path

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_automations import (
    OWNER,
    UNIVERSE,
    _real_providers,
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
from tinyassets.automations import STATE_PAUSED, AutomationStore, register_automation

pytestmark = pytest.mark.usefixtures("cloud_runtime")

SECRET_KEY = "sk-live-" + "Zq8rT2vX9mK4pL7nB3wE6yH1jD5"
ALICE_EMAIL = "alice.private@example.com"


@pytest.fixture(autouse=True)
def _pin_data_dir(tmp_path: Path, monkeypatch):
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
    """Bob's OWN connected model: his installed copies run on it, never Alice's."""
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.custom_agents import create_binding, publish_definition
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving

    folder = base / BOB_UNIVERSE
    folder.mkdir(exist_ok=True)
    write_credential_vault(
        folder, [{"credential_type": "llm_subscription", "service": "codex",
                  "auth_json_b64": "e30="}],
        owner_user_id=BOB, universe_id=BOB_UNIVERSE)
    definition = publish_definition(base, author_id=BOB, payload={
        "schema_version": 1, "name": "Bob's agent", "description": "",
        "tags": ["test"], "components": {"identity": {"kind": "soul", "config": {}}}})
    agent = create_binding(
        base, universe_id=BOB_UNIVERSE, definition_id=definition["agent_definition_id"],
        created_by=BOB, payload={"schema_version": 1, "name": "Bob's agent", "role": "writer"})
    connected = bind_serving_provider(
        base_path=base, universe_dir=folder, owner_user_id=BOB, universe_id=BOB_UNIVERSE,
        agent_binding_id=agent["agent_binding_id"], expected_revision=1, provider="codex")
    set_serving(
        base_path=base, universe_dir=folder, owner_user_id=BOB, universe_id=BOB_UNIVERSE,
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=connected["agent_binding"]["revision"], enabled=True)


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """Alice's built village, and Bob's own small command center."""
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
    from tinyassets.custom_agents import save_app_ui

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
    from tinyassets.custom_agents import get_definition

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


def test_the_tab_names_every_file_and_every_exclusion_with_its_reason(home: Path):
    ask = _ask(OWNER, UNIVERSE, _publish_action())
    body = ask["body"]
    assert "Nothing will be shared" not in body and "Harmless" not in ask["title"]
    for rel in TRAVELS:
        assert f"- {rel}" in body, rel
    for rel, reason in [("notes/keys.md", ccp.R_CREDENTIAL),
                        ("notes/leads.md", ccp.R_CONTACT),
                        ("notes/photo.png", ccp.R_BINARY),
                        ("founder.md", ccp.R_BRAIN),
                        ("MEMORY.md", ccp.R_MEMORY),
                        ("wiki/drafts/", ccp.R_WIKI),
                        ("workspaces/", ccp.R_CHECKOUT),
                        ("ledger.db", ccp.R_DATABASE),
                        (f"notes/{ALICE_EMAIL}.md", ccp.R_PATH)]:
        assert f"{rel}: {reason}" in body, rel
    assert "detection cannot prove" in body
    # Platform state is left out without burying the list in it.
    assert ".runtime" not in body and ".credentials" not in body


@pytest.mark.parametrize("rel,data,reason", [
    ("notes/a.md", "token: " + SECRET_KEY, ccp.R_CREDENTIAL),
    ("notes/a.md", "call +1 415 555 0132", ccp.R_CONTACT),
    ("notes/a.md", "mail " + ALICE_EMAIL, ccp.R_CONTACT),
    ("notes/a.bin", b"\xff\xfe\x00binary", ccp.R_BINARY),
    (".env", "X=1", ccp.R_DOT),
    ("notes/.hidden.md", "x", ccp.R_DOT),
    ("founder.md", "x", ccp.R_BRAIN),
    ("soul_versions/0001.md", "x", ccp.R_BRAIN),
    ("wiki/index.md", "x", ccp.R_WIKI),
    ("status.json", "{}", ccp.R_RUNTIME),
    ("MEMORY.md", "- [m_a] x", ccp.R_MEMORY),
    ("agents/x/MEMORY.md", "- [m_a] x", ccp.R_MEMORY),
    ("notes/x.sqlite", "x", ccp.R_DATABASE),
])
def test_classify_keeps_every_private_class_out(rel, data, reason):
    raw = data.encode("utf-8") if isinstance(data, str) else data
    assert ccp.classify(rel, raw, exclude=[], memory_items={}) == (None, reason)


def test_classify_owner_choices_never_lift_a_detection():
    leaked = ("- [m_a] key: " + SECRET_KEY).encode()
    # Naming the memory item cannot carry a credential out with it.
    assert ccp.classify("MEMORY.md", leaked, exclude=[],
                        memory_items={"MEMORY.md": ["m_a"]}) == (None, ccp.R_CREDENTIAL)
    assert ccp.classify("notes/a.md", b"fine", exclude=["notes"],
                        memory_items={}) == (None, ccp.R_EXCLUDED)
    assert ccp.classify("notes/ab.md", b"fine", exclude=["notes/a"],
                        memory_items={}) == (b"fine", "")


def test_a_detection_in_a_name_or_description_refuses_the_publish(home: Path):
    action = _publish_action()
    action["description"] = "ask me at " + ALICE_EMAIL
    out = _ask(OWNER, UNIVERSE, action)
    assert "contact details" in out.get("detail", out.get("error", "")), out


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


def test_the_rail_shows_the_pinned_tab_whatever_the_row_says(home: Path):
    from tinyassets.api.pending_requests import list_requests

    ask = _ask(OWNER, UNIVERSE, _publish_action())
    _rewrite_row(home, UNIVERSE, ask["request_id"], title="Nothing to see",
                 body="Nothing will be shared.")
    with _as(OWNER):
        rail = list_requests(universe_id=UNIVERSE)
    [shown] = [r for r in rail["pending"] if r.get("request_id") == ask["request_id"]]
    assert shown["title"] == ask["title"] and shown["body"] == ask["body"]


def test_a_rewritten_action_executes_the_pinned_one(home: Path):
    from tinyassets.custom_agents import get_definition

    ask = _ask(OWNER, UNIVERSE, _publish_action())
    forged = dict(ask["action"])
    forged["name"] = "Something else entirely"
    _rewrite_row(home, UNIVERSE, ask["request_id"], action=forged)
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert done.get("published") is True, done
    assert get_definition(home, done["agent_definition_id"])["name"] == "GTM Village"


def test_the_same_ask_raised_again_after_a_dismissal_is_confirmable(home: Path):
    from tests.owner_answer import answer_request

    first = _ask(OWNER, UNIVERSE, _publish_action())
    with _as(OWNER):
        answer_request(universe_id=UNIVERSE, payload=json.dumps(
            {"request_id": first["request_id"], "dismiss": True}))
    second = _ask(OWNER, UNIVERSE, _publish_action())
    assert second["request_id"] != first["request_id"]
    done = _answer(OWNER, UNIVERSE, second["request_id"])
    assert done.get("published") is True, done


def test_a_publish_row_with_no_pin_cannot_be_confirmed(home: Path):
    ask = _ask(OWNER, UNIVERSE, _publish_action())
    with sqlite3.connect(ccp.store_dir(home) / "packages.db") as conn:
        conn.execute("DELETE FROM pins")
    out = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert "no longer be confirmed" in out.get("error", "") + out.get("detail", ""), out


# ---------------------------------------------------------------------------
# 3. Quota: the package is charged to its publisher, and refused with its size
# ---------------------------------------------------------------------------


def test_an_over_quota_package_is_refused_with_its_size_and_nothing_goes_public(
        home: Path, monkeypatch):
    from tinyassets.custom_agents import list_definitions
    from tinyassets.daemon_server import get_branch_definition

    _write(home / UNIVERSE, "notes/big.md", "word " * 60000)
    ask = _ask(OWNER, UNIVERSE, _publish_action())
    monkeypatch.setenv("TINYASSETS_FREE_STORAGE_GIB", str(1024 / 1024**3))
    out = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert out.get("error") == "publish_refused" and out.get("request_pending"), out
    size = ask["action"]["shown"]["package"]["size"]
    assert f"This package is {size}" in out["detail"], out
    assert not [d for d in list_definitions(home, author_id=OWNER)
                if ccp.PACKAGE_TAG in d["tags"]]
    assert get_branch_definition(home, branch_def_id=SCOUT)["visibility"] == "private"


def test_the_packages_store_charges_the_publisher(home: Path):
    from tinyassets.storage_accounting import measure

    out = _published(home)
    size = out["done"]["package"]["size_bytes"]
    assert measure(home, OWNER, "packages") == size
    assert measure(home, BOB, "packages") == 0


# ---------------------------------------------------------------------------
# 4. Install: discover, quarantine, activate, run -- as Bob
# ---------------------------------------------------------------------------


def _install(home: Path, definition_id: str) -> dict:
    return _ask(BOB, BOB_UNIVERSE, {"type": "install", "agent_definition_id": definition_id})


def _bobs_branches(home: Path) -> list[dict]:
    from tinyassets.daemon_server import list_branch_definitions

    return list_branch_definitions(home, author=BOB, include_private=True)


def test_bob_finds_installs_and_runs_alices_village(home: Path):
    from tests.test_background_budget_finalization_e2e import _CountingProvider
    from tinyassets.api.package_requests import list_packages
    from tinyassets.automations import run_due_automation
    from tinyassets.custom_agents import get_app_ui
    from tinyassets.runs import get_run, wait_for

    published = _published(home, memory_items=["m_pub"])
    definition_id = published["done"]["agent_definition_id"]
    with _as(BOB):
        [listed] = list_packages(query="village")
    assert listed["agent_definition_id"] == definition_id
    assert listed["version"] == 1 and listed["needs"]["model"] == "openrouter/free"

    before = _bob_files(home)
    ask = _install(home, definition_id)
    assert "request_id" in ask, ask
    assert "paused" in ask["body"] and "agents/gtm-village/" in ask["body"]
    # Quarantine: nothing of the package exists in Bob's command center yet.
    assert _bob_files(home) == before
    assert _bobs_branches(home) == []
    assert AutomationStore(home).list(universe_id=BOB_UNIVERSE) == []

    done = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert done.get("installed") is True, done
    files = _bob_files(home)
    assert files["AGENTS.md"] == b"# Bob's own agent\n"
    assert files["agents/gtm-village/AGENTS.md"] == TRAVELS["AGENTS.md"].encode()
    assert files["agents/gtm-village/skills/scout/SKILL.md"]
    assert files["agents/gtm-village-scribe/AGENTS.md"] == TRAVELS[
        "agents/scribe/AGENTS.md"].encode()
    assert files["notes/board.md"] == TRAVELS["notes/board.md"].encode()
    assert files["wiki/pages/village.md"]
    everything = b"".join(files.values())
    for needle in (SECRET_KEY, ALICE_EMAIL, "Elm Street", "Acme", "session transcript"):
        assert needle.encode() not in everything, needle

    copies = _bobs_branches(home)
    assert len(copies) == 2
    assert all(c["visibility"] == "private" and c.get("default_llm_policy") is None
               for c in copies)
    rows = {r.name: r for r in AutomationStore(home).list(universe_id=BOB_UNIVERSE)}
    assert set(rows) == {"scout heartbeat", "scribe follows"}
    assert all(r.desired_state == STATE_PAUSED and r.owner_principal_id == BOB
               for r in rows.values())
    copy_ids = {c["branch_def_id"] for c in copies}
    assert rows["scout heartbeat"].branch_def_id in copy_ids
    assert rows["scribe follows"].event_filter["branch_def_id"] == rows[
        "scout heartbeat"].branch_def_id
    assert rows["scout heartbeat"].inputs == {}
    library = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"]
    assert [u["ui_id"] for u in library] == ["village"]

    # Bob resumes the heartbeat and it runs, in Bob's command center, as Bob's.
    from tinyassets.api.automations import automations

    beat = rows["scout heartbeat"]
    with _as(BOB):
        resumed = automations(action="resume", universe_id=BOB_UNIVERSE,
                              automation_id=beat.automation_id,
                              expected_revision=beat.revision)
    assert not resumed.get("error"), resumed
    beat = AutomationStore(home).get(beat.automation_id)
    fake = _CountingProvider()
    with _real_providers(codex=fake):
        outcome = run_due_automation(home, beat, "2026-10-01T12:05:00+00:00")
    run_id = str(outcome).rsplit(":", 1)[-1]
    wait_for(run_id, timeout=30)
    run = get_run(home, run_id) or {}
    assert run.get("status") == "completed", (outcome, run)
    assert run.get("branch_def_id") == beat.branch_def_id
    assert fake.calls, "the installed workflow never reached a model"

    # Alice's originals are untouched, and a second confirm installs nothing more.
    assert (home / UNIVERSE / "AGENTS.md").read_text() == TRAVELS["AGENTS.md"]
    again = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert again.get("error") == "already_resolved", again
    assert len(_bobs_branches(home)) == 2


def test_a_file_bob_already_has_is_kept_as_his(home: Path):
    published = _published(home)
    _write(home / BOB_UNIVERSE, "notes/board.md", "# Bob's board\n")
    ask = _install(home, published["done"]["agent_definition_id"])
    assert "kept as yours" in ask["body"] and "notes/board.md" in ask["body"]
    done = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert done.get("installed") is True, done
    assert (home / BOB_UNIVERSE / "notes/board.md").read_text() == "# Bob's board\n"
    assert "notes/board.md" in done["kept"]


def test_a_file_appearing_after_the_tab_installs_nothing(home: Path):
    published = _published(home)
    ask = _install(home, published["done"]["agent_definition_id"])
    _write(home / BOB_UNIVERSE, "notes/board.md", "# Bob made this meanwhile\n")
    out = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert out.get("error") == "install_refused" and out.get("request_pending"), out
    assert _bobs_branches(home) == []
    assert not (home / BOB_UNIVERSE / "agents").exists()


@pytest.mark.skipif(os.name == "nt", reason="a planted symlink is the POSIX case")
def test_a_link_planted_after_the_tab_is_never_written_through(home: Path, tmp_path: Path):
    published = _published(home)
    ask = _install(home, published["done"]["agent_definition_id"])
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / BOB_UNIVERSE / "wiki").symlink_to(outside, target_is_directory=True)
    out = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert "error" in out, out
    assert list(outside.iterdir()) == []


def test_only_the_owner_can_confirm_an_install(home: Path):
    published = _published(home)
    ask = _install(home, published["done"]["agent_definition_id"])
    out = _answer(OWNER, BOB_UNIVERSE, ask["request_id"])
    assert out.get("error") == "not_found", out
    assert _bobs_branches(home) == []


# ---------------------------------------------------------------------------
# 5. The ingestion boundary
# ---------------------------------------------------------------------------


def _hostile_package(home: Path, files: dict[str, bytes], *, listed=None) -> str:
    """A definition carrying a package whose blob was written by hand."""
    from tinyassets.custom_agents import publish_definition

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


@pytest.mark.parametrize("files", [
    {"../escape.md": b"x"},
    {"/etc/cron.d/x": b"x"},
    {"notes/../../x.md": b"x"},
    {"C:/x.md": b"x"},
    {"notes\\x.md": b"x"},
    {".runtime/x": b"x"},
    {"Notes.md": b"x", "notes.md": b"y"},
    {"notes": b"x", "notes/a.md": b"y"},
    {"caf\u00e9.md": b"x", "cafe\u0301.md": b"y"},
])
def test_an_escaping_or_colliding_package_is_refused_before_quarantine(home: Path, files):
    before = _bob_files(home)
    definition_id = _hostile_package(home, files)
    out = _install(home, definition_id)
    assert "request_id" not in out, out
    with sqlite3.connect(ccp.store_dir(home) / "packages.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM pins WHERE kind = 'install'"
                            ).fetchone()[0] == 0
    assert _bob_files(home) == before


def test_content_that_does_not_match_its_listing_is_refused(home: Path):
    definition_id = _hostile_package(home, {"notes/a.md": b"real"}, listed=[
        {"path": "notes/a.md", "size": 4, "sha256": "0" * 64}])
    out = _install(home, definition_id)
    assert "does not match" in json.dumps(out), out


def test_a_tampered_blob_is_refused(home: Path):
    published = _published(home)
    from tinyassets.custom_agents import get_definition

    sha = get_definition(home, published["done"]["agent_definition_id"])[
        "components"]["package"]["blob_sha256"]
    path = ccp.store_dir(home) / "blobs" / f"{sha}.json"
    path.write_bytes(path.read_bytes() + b" ")
    out = _install(home, published["done"]["agent_definition_id"])
    assert "does not match its id" in json.dumps(out), out


# ---------------------------------------------------------------------------
# 6. Activation is claimed once
# ---------------------------------------------------------------------------


def test_a_live_claim_refuses_a_second_activation(tmp_path: Path):
    request_id = ccp.pin(tmp_path, universe_id="u", kind="install", agent="main",
                         digest="d", record={})
    pin_id = ccp.pin_for_request(tmp_path, universe_id="u", request_id=request_id)["pin_id"]
    state, first = ccp.claim(tmp_path, universe_id="u", pin_id=pin_id, now=1000.0)
    assert state == "pinned"
    with pytest.raises(ccp.PackageError):
        ccp.claim(tmp_path, universe_id="u", pin_id=pin_id, now=1001.0)
    # A crashed activation's claim is taken over (resumed) once its lease lapses...
    later = 1000.0 + ccp.CLAIM_LEASE_S + 1
    state, second = ccp.claim(tmp_path, universe_id="u", pin_id=pin_id, now=later)
    assert state == "activating" and second != first
    # ...and the superseded holder is fenced out of every write.
    with pytest.raises(ccp.LostClaim):
        ccp.record_progress(tmp_path, universe_id="u", pin_id=pin_id, progress={"x": 1},
                            token=first)
    with pytest.raises(ccp.LostClaim):
        ccp.finish(tmp_path, universe_id="u", pin_id=pin_id, progress={}, token=first)
    ccp.unclaim(tmp_path, universe_id="u", pin_id=pin_id, token=first)
    with pytest.raises(ccp.PackageError):  # the stale release changed nothing
        ccp.claim(tmp_path, universe_id="u", pin_id=pin_id, now=later + 1)
    ccp.finish(tmp_path, universe_id="u", pin_id=pin_id, progress={}, token=second)
    assert ccp.claim(tmp_path, universe_id="u", pin_id=pin_id, now=later + 2)[0] == "activated"


# ---------------------------------------------------------------------------
# 7. A cross-author remix copies the snapshot, never the live source
# ---------------------------------------------------------------------------


def test_a_cross_author_remix_never_takes_the_sources_later_skills(home: Path):
    from tinyassets.api.extensions import _extensions_impl
    from tinyassets.branch_versions import mark_versions_public, publish_branch_version
    from tinyassets.daemon_server import get_branch_definition, save_branch_definition

    raw = get_branch_definition(home, branch_def_id=SCOUT)
    version = publish_branch_version(home, {**raw, "visibility": "public"},
                                     publisher=OWNER)
    mark_versions_public(home, [version.branch_version_id])
    later = {**raw, "visibility": "public",
             "skills": [{"name": "private-playbook", "body": "Alice only"}]}
    save_branch_definition(home, branch_def=later)
    with _as(BOB):
        made = json.loads(_extensions_impl(action="build_branch", spec_json=json.dumps(
            {"name": "copy", "fork_from": version.branch_version_id,
             "visibility": "private"})))
    bid = made.get("branch_def_id") or (made.get("branch") or {}).get("branch_def_id")
    assert bid, made
    assert "private-playbook" not in json.dumps(get_branch_definition(home, branch_def_id=bid))


# ---------------------------------------------------------------------------
# 8. The owner's switches, and the "worth a look" list (lead, 2026-10-01)
# ---------------------------------------------------------------------------


def _switch(ask: dict, label_start: str) -> str:
    return next(f["name"] for f in ask["fields"] if f["label"].startswith(label_start))


def test_the_owner_switches_a_folder_and_a_file_off_before_confirming(home: Path):
    ask = _ask(OWNER, UNIVERSE, _publish_action())
    assert any(f["type"] == "choice" and f["label"].startswith("notes/") for f in ask["fields"])
    done = _answer(OWNER, UNIVERSE, ask["request_id"], {
        _switch(ask, "notes/"): "Leave out", "leave_out": "wiki/pages/village.md"})
    assert done.get("published") is True, done
    files = _blob_files(home, done["agent_definition_id"])
    assert "notes/board.md" not in files and "wiki/pages/village.md" not in files
    assert "AGENTS.md" in files


@pytest.mark.parametrize("values,needle", [
    ({"leave_out": "notes/never-shown.md"}, "is not in this package"),
    ({"leave_out": "../x"}, "cannot leave out"),
    ({"surprise": "x"}, "has no field"),
])
def test_a_switch_can_only_narrow_what_was_shown(home: Path, values, needle):
    ask = _ask(OWNER, UNIVERSE, _publish_action())
    out = _answer(OWNER, UNIVERSE, ask["request_id"], values)
    assert needle in out.get("detail", "") and out.get("request_pending"), out


def test_often_private_words_are_flagged_for_review_not_hidden(home: Path):
    _write(home / UNIVERSE, "notes/pricing.md", "CONFIDENTIAL: launch pricing\n")
    ask = _ask(OWNER, UNIVERSE, _publish_action())
    assert "Worth a look before you confirm" in ask["body"]
    assert ('1 mentions an often-private word: notes/pricing.md ("CONFIDENTIAL")'
            in ask["body"])
    assert "  - notes/pricing.md\n" in ask["body"] + "\n"


# ---------------------------------------------------------------------------
# 9. Code refute round 1
# ---------------------------------------------------------------------------


def test_a_pinned_row_disguised_as_a_question_still_renders_as_the_pin(home: Path):
    from tinyassets.api.pending_requests import list_requests

    ask = _ask(OWNER, UNIVERSE, _publish_action())
    _rewrite_row(home, UNIVERSE, ask["request_id"], action={"type": "answer"},
                 title="Quick question", body="Is it sunny?", fields=[])
    with _as(OWNER):
        rail = list_requests(universe_id=UNIVERSE)
    [shown] = [r for r in rail["pending"] if r.get("request_id") == ask["request_id"]]
    assert shown["title"] == ask["title"] and shown["action"]["type"] == "publish"


def test_a_planted_row_is_never_adopted_as_a_consent(home: Path):
    from tinyassets.storage.pending_requests import create_request

    action = _publish_action()
    first = _ask(OWNER, UNIVERSE, action)
    # The agent plants a harmless row carrying the dedupe key the next ask would
    # compute; the platform must mint its own id rather than adopt the plant.
    from tinyassets.storage.pending_requests import get_request

    key = get_request(home / UNIVERSE, first["request_id"])["dedupe_key"]
    with sqlite3.connect(ccp.store_dir(home) / "packages.db") as conn:
        conn.execute("UPDATE pins SET state = 'activated'")
    from tinyassets.agent_activities import store_path
    with sqlite3.connect(store_path(home / UNIVERSE)) as conn:
        conn.execute("UPDATE pending_requests SET status = 'answered'")
    planted = create_request(home / UNIVERSE, kind="x", title="harmless", body="harmless",
                             fields=[], action={"type": "answer"}, dedupe_key=key)
    second = _ask(OWNER, UNIVERSE, action)
    assert second["request_id"] not in (planted["request_id"], first["request_id"])


def test_an_install_ask_needs_no_kind_or_title(home: Path):
    from tinyassets.api.pending_requests import request_from_user

    published = _published(home)
    with _as(BOB):
        out = request_from_user(universe_id=BOB_UNIVERSE, payload=json.dumps({"action": {
            "type": "install",
            "agent_definition_id": published["done"]["agent_definition_id"]}}))
    assert "request_id" in out and out["title"].startswith("Install"), out


def test_two_installers_each_get_their_own_automations(home: Path):
    from tinyassets.daemon_server import grant_universe_access, set_founder_home

    published = _published(home)
    definition_id = published["done"]["agent_definition_id"]
    done = _answer(BOB, BOB_UNIVERSE, _install(home, definition_id)["request_id"])
    assert done.get("installed") is True, done
    # Bob installs again into a second command center of his: new rows, his own.
    second = "universe_bob_two"
    (home / second).mkdir()
    grant_universe_access(home, universe_id=second, actor_id=BOB, permission="admin",
                          granted_by=BOB)
    set_founder_home(home, founder_sub=BOB, universe_id=second)
    from tests.test_automations import _copy_assignment_to

    _copy_assignment_to(home, universe_id=second, owner=BOB)
    ask = _ask(BOB, second, {"type": "install", "agent_definition_id": definition_id})
    again = _answer(BOB, second, ask["request_id"])
    assert again.get("installed") is True, again
    first_ids = set(done["automations"].values())
    assert first_ids and first_ids.isdisjoint(again["automations"].values())
    rows = AutomationStore(home).list(universe_id=second)
    assert {r.automation_id for r in rows} == set(again["automations"].values())


def test_a_credential_beside_contact_details_in_a_workflow_is_refused(home: Path):
    from tinyassets.daemon_server import get_branch_definition, save_branch_definition

    raw = get_branch_definition(home, branch_def_id=SCOUT)
    raw["description"] = "0123456789abcdef " + ALICE_EMAIL
    save_branch_definition(home, branch_def=raw)
    out = _ask(OWNER, UNIVERSE, _publish_action())
    assert "request_id" not in out and "workflow" in out.get("detail", ""), out


@pytest.mark.parametrize("rel", ["Founder.md", "SOUL.md", "memory.md", "Wiki/Drafts/x.md",
                                 "agents/scribe/Memory.md", "Workspaces/r/x.md"])
def test_protected_names_are_protected_in_any_case(rel):
    assert ccp.classify(rel, b"plain text", exclude=[], memory_items={})[0] is None


def test_a_resumed_ui_add_never_duplicates_the_screen(home: Path):
    from tinyassets.api.package_requests import _add_ui
    from tinyassets.custom_agents import get_app_ui

    with _as(BOB):
        _add_ui(BOB_UNIVERSE, UI, "village")
        _add_ui(BOB_UNIVERSE, UI, "village")
    library = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)["ui_library"]
    assert [u["ui_id"] for u in library] == ["village"]


# ---------------------------------------------------------------------------
# 10. Code refute round 2
# ---------------------------------------------------------------------------


def test_narrowing_keeps_every_remaining_byte_as_verified():
    files = {"AGENTS.md": b"lead", "notes/a.md": b"alpha", "notes/b.md": b"beta",
             "wiki/pages/w.md": b"wiki"}
    manifest = ccp.build_manifest(profile=ccp.PROFILE_PUBLISH, name="P", description="",
                                  files=files, workflows=[], ui="", automations=[],
                                  connections=[])
    narrowed = ccp.narrow_package(ccp.build_blob(manifest, files), ["notes", "wiki/pages/w.md"])
    _, kept = ccp.check_blob(narrowed["blob"])
    assert kept == {"AGENTS.md": b"lead"}
    with pytest.raises(ccp.PackageError):
        ccp.narrow_package(ccp.build_blob(manifest, files), ["AGENTS.md", "notes", "wiki"])


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


def test_an_id_named_field_is_not_a_blind_spot(home: Path):
    from tinyassets.daemon_server import get_branch_definition, save_branch_definition

    raw = get_branch_definition(home, branch_def_id=SCOUT)
    raw["node_defs"][0]["customer_id"] = "Zq8rT2vX9mK4pL7nB3wE6yH1"
    raw["state_schema"] = [{"name": "x", "type": "str",
                            "default": {"id": "Hq2Lp9XvB4nZm8KdRtW3yQ7k"}}]
    save_branch_definition(home, branch_def=raw)
    ask = _ask(OWNER, UNIVERSE, _publish_action())
    # A random-looking value is listed for review, wherever it sits; a schema
    # id key nested in user data is NOT exempt (gpt-6-astra, code r3).
    assert "node_defs[0].customer_id" in ask["body"]
    assert "state_schema[0].default.id" in ask["body"]
    raw["node_defs"][0]["customer_id"] = "415-555-1212"
    save_branch_definition(home, branch_def=raw)
    out = _ask(OWNER, UNIVERSE, _publish_action())
    assert "request_id" not in out and "contact details" in out["detail"], out


def test_a_secret_named_line_makes_an_opaque_run_certain():
    assert ccp.text_detection("api_key = 0123456789abcdef0123") == ccp.R_CREDENTIAL
    assert ccp.text_detection("run Zq8rT2vX9mK4pL7nB3wE6yH1 finished") is None
    assert ccp.text_suspect("run Zq8rT2vX9mK4pL7nB3wE6yH1 finished")


def test_a_platform_id_is_exempt_only_at_its_schema_location():
    notes: list[str] = []
    ccp.scan_public({"node_defs": [{"node_id": "Zq8rT2vX9mK4pL7nB3wE6yH1"}]}, "w", notes)
    assert notes == []
    ccp.scan_public({"state_schema": [{"default": {"node_id": "Zq8rT2vX9mK4pL7nB3wE6yH1"}}]},
                    "w", notes)
    assert notes == ["w.state_schema[0].default.node_id"]


def test_a_phone_number_under_a_schema_id_key_is_refused():
    with pytest.raises(ccp.PackageError):
        ccp.scan_public({"node_id": "415-555-1212"})
    ccp.scan_public({"node_id": "01m3x4ycgknx933dfx03y93qhe", "author": "u-01ky3zh1arr8qth8"})


def test_the_tab_lists_every_file_however_many(home: Path):
    for n in range(260):
        _write(home / UNIVERSE, f"notes/many/n{n:03d}.md", f"note {n}\n")
    ask = _ask(OWNER, UNIVERSE, _publish_action())
    for n in range(260):
        assert f"  - notes/many/n{n:03d}.md\n" in ask["body"], n
    assert "more" not in ask["body"].split("Left out")[0].split("These")[-1]


def test_a_blob_write_that_fails_records_no_ownership(tmp_path: Path, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("tinyassets.universe_files.write_data_path", boom)
    with pytest.raises(OSError):
        ccp.store_blob(tmp_path, author_id="acct_a", blob=b"{}")
    monkeypatch.undo()
    sha = hashlib.sha256(b"{}").hexdigest()
    assert not ccp.blob_owned(tmp_path, "acct_a", sha)
    assert ccp.measure_packages(tmp_path, ["acct_a"]) == 0


def test_an_unrelated_screen_at_the_intended_id_is_never_adopted(home: Path):
    from tinyassets.api.package_requests import _add_ui
    from tinyassets.custom_agents import get_app_ui, save_app_ui

    bobs = {**UI, "name": "Bob's own", "markup": "<p>mine</p>"}
    save_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE, expected_revision=0,
                changes={"ui_library": [bobs]})
    with _as(BOB):
        landed = _add_ui(BOB_UNIVERSE, UI, "village")
    assert landed != "village"
    library = {u["ui_id"]: u for u in get_app_ui(home, owner_user_id=BOB,
                                                 universe_id=BOB_UNIVERSE)["ui_library"]}
    assert library["village"]["markup"] == "<p>mine</p>"
    assert library[landed]["markup"] == UI["markup"]


def test_a_bare_opaque_run_is_listed_for_review_not_dropped():
    # Not assigned to a secret's name: the file stays in and the tab lists it.
    # This is the deliberate tradeoff that keeps a real village from being gutted
    # (live dry run, 2026-10-01); the owner reads the list before confirming.
    data = ("a note mentioning " + SECRET_KEY).encode()
    assert ccp.classify("notes/a.md", data, exclude=[], memory_items={}) == (data, "")
    assert ccp.review_note(data) == ccp.N_OPAQUE


def test_the_founders_private_grounding_never_travels(tmp_path):
    """``orgchart.md`` shipped in a published package (post-merge review of #4315).

    ``api/interlocutor.FOUNDER_PRIVATE_GROUNDING`` withholds these from every
    non-founder interlocutor *whatever* the command center's visibility level,
    and the publish confirmation says brain files were left out -- but the
    package's own brain list spelled out four names and omitted this one, so a
    published command center carried the founder's collaborators, delegations
    and reporting lines.
    """
    from tinyassets.api.interlocutor import FOUNDER_PRIVATE_GROUNDING
    from tinyassets.automation_context import BRAIN_FILES

    # The ratchet, over both authorities: the package's exclusions are DERIVED
    # from these, so a file added to either cannot start travelling without
    # this test failing.
    assert {ccp.fold(n) for n in FOUNDER_PRIVATE_GROUNDING} <= ccp._BRAIN_F
    governed = set(BRAIN_FILES) - set(ccp.HARNESS_ROOT_FILES)
    assert {ccp.fold(n) for n in governed} <= ccp._BRAIN_F

    universe = tmp_path / "cc"
    universe.mkdir()
    for name in ("orgchart.md", "origin.md", "body.md"):
        (universe / name).write_text(f"private {name}\n", encoding="utf-8")
    # The control lives in a subfolder: the ROOT is an allowlist, so a new
    # root file stays home by design (test_an_unlisted_root_file_stays_home).
    _write(universe, "notes/keep.md", "a shareable note\n")

    files, excluded = ccp.collect(universe, exclude=[], memory_items={})
    assert "notes/keep.md" in files
    for name in ("orgchart.md", "origin.md", "body.md"):
        assert name not in files
        assert any(row["path"] == name for row in excluded)


def test_the_published_roster_agents_identity_still_travels(tmp_path):
    """``identity.md`` is a brain file AND a harness root file, and the harness
    set travels on purpose: ``destination`` remaps those into
    ``agents/<slug>/`` so a published command center arrives as a roster agent.
    Excluding it with the rest of the governed set would install an agent with
    no identity, so the derivation subtracts ``HARNESS_ROOT_FILES``.
    """
    from tinyassets.automation_context import BRAIN_FILES

    assert "identity.md" in BRAIN_FILES and "identity.md" in ccp.HARNESS_ROOT_FILES
    assert ccp.fold("identity.md") not in ccp._BRAIN_F

    universe = tmp_path / "cc"
    universe.mkdir()
    (universe / "identity.md").write_text("I am the village keeper.\n", encoding="utf-8")

    files, _excluded = ccp.collect(universe, exclude=[], memory_items={})
    assert "identity.md" in files
    assert ccp.destination("identity.md", "alice-village") == "agents/alice-village/identity.md"


def test_the_publishers_own_request_queue_never_travels(tmp_path):
    """``requests.json`` shipped too, and it is not inert on arrival.

    It holds the publisher's pending request text, and the daemon turns pending
    rows into active work targets (``work_targets``), so an installed copy
    carried someone else's queue into the installer's command center.
    """
    from tinyassets.work_targets import REQUESTS_FILENAME

    assert ccp.fold(REQUESTS_FILENAME) in ccp._RUNTIME_F

    universe = tmp_path / "cc"
    universe.mkdir()
    (universe / REQUESTS_FILENAME).write_text(
        '[{"id":"demo","status":"pending","text":"Prepare the acquisition offer"}]',
        encoding="utf-8")
    _write(universe, "notes/keep.md", "a shareable note\n")

    files, excluded = ccp.collect(universe, exclude=[], memory_items={})
    assert "notes/keep.md" in files
    assert REQUESTS_FILENAME not in files
    assert any(row["path"] == REQUESTS_FILENAME for row in excluded)


def test_the_publish_sentence_names_what_travels(tmp_path):
    """The sentence describes the carried kinds, not a removal list.

    A sentence that lists what was removed can only ever be as complete as the
    removal list was, and the previous one promised "your brain files and
    platform state were left out" while orgchart.md, requests.json and 21
    other platform root files travelled. Two exactness points are pinned here
    because they are easy to "simplify" back into falsehood: "private" brain
    files (identity.md travels as the roster agent's identity) and memory being
    conditional (named entries do travel).
    """
    from tinyassets.api.publish_requests import PACKAGE_SENTENCE

    # Case-insensitive: these phrases may start a sentence, and which one does
    # is incidental to the claim being made.
    said = PACKAGE_SENTENCE.lower()
    assert "your private brain files" in said
    assert "your brain files" not in said.replace("your private brain files", "")
    assert "your memory unless you named entries" in said
    assert "anything else sitting in the top folder stay home" in said
    # The existing tab test asserts this phrase in lowercase; keep it so.
    assert "detection cannot prove" in PACKAGE_SENTENCE

    # identity.md really does travel, which is why the wording is qualified.
    assert ccp.structural_exclusion("identity.md") is None
    # Memory is conditional in both directions.
    memory = b"- [m_abc] a remembered line\n"
    assert ccp.classify(ccp.MEMORY_FILE, memory, exclude=[], memory_items={})[0] is None
    assert ccp.classify(ccp.MEMORY_FILE, memory, exclude=[],
                        memory_items={ccp.MEMORY_FILE: ["m_abc"]})[0] is not None


def test_an_unlisted_root_file_stays_home(tmp_path):
    """The root is an ALLOWLIST, which is the whole point of this change.

    Both real leaks were root files, and a grep of the root-level filenames
    platform code writes found 21 more that travelled -- including
    ``branch_tasks.json``, the work queue. Enumerating private names could
    never finish; the root being closed does.
    """
    universe = tmp_path / "cc"
    universe.mkdir()
    for name in ("README.md", "surprise.yaml", "a-feature-nobody-wrote-yet.json"):
        _write(universe, name, "content\n")
    _write(universe, "notes/keep.md", "a shareable note\n")

    files, excluded = ccp.collect(universe, exclude=[], memory_items={})
    assert "notes/keep.md" in files, "a user's own folder still travels"
    for name in ("README.md", "surprise.yaml", "a-feature-nobody-wrote-yet.json"):
        assert name not in files, name
        assert any(row["path"] == name and row["reason"] == ccp.R_ROOT_UNLISTED
                   for row in excluded), name


def test_every_platform_written_root_file_stays_home():
    """The 21 found after #4363, plus the two it closed.

    None of these is named in ``ROOT_FILES``, so each is already covered by the
    allowlist -- this pins that, so nobody has to keep a private-name list
    complete ever again. The four with a verified ``Path(universe_path) /
    FILENAME`` site are marked.
    """
    platform_root = [
        "branch_tasks.json",            # branch_tasks.py:36  (the work queue)
        "branch_tasks_archive.json",    # branch_tasks.py:37
        "enrichment_signals.json",      # enrichment_signals.py:19
        "hard_priorities.json",         # work_targets.py:134
        "requests.json", "orgchart.md", "onboarding.json", "preferences.json",
        "priorities.yaml", "goals.md", "plan.md", "progress.md", "projects.md",
        "proposals.md", "characters.md", "acquisition_presets.json",
        "bid_ledger.json", "bid_execution_log.json", "assignment.json",
        "host.json", "current.json", "output.json", "auth.json",
    ]
    for name in platform_root:
        assert ccp.structural_exclusion(name) is not None, name
        assert ccp.fold(name) not in ccp._ROOT_FILES_F, name


def test_the_platform_folders_are_denied_from_their_writers_constants(tmp_path):
    """Root FOLDERS cannot be a closed allowlist -- a user may make any folder,
    and their content is most of what sharing a command center means. So the
    platform's own folders are DERIVED from the constants their writers use,
    not hand-listed.

    The first version of this test hand-listed six names, and the review
    pointed out that adding ``artifacts/`` would leave it green -- which is
    exactly what had happened: ``artifacts/reviews``,
    ``artifacts/executions`` and ``artifacts/discarded_targets`` were
    published, and the discard archive preserves a whole work target including
    its request text. A hand-list cannot guard against the omission that
    produced it.
    """
    from tinyassets.work_targets import (
        ARTIFACTS_DIRNAME,
        DISCARD_ARCHIVE_DIRNAME,
        EXECUTIONS_DIRNAME,
        REVIEWS_DIRNAME,
    )

    # The writers' own constants, so a new artifact subtree is covered the day
    # it is added and a renamed one fails here instead of leaking.
    assert ccp.fold(ARTIFACTS_DIRNAME) in {ccp.fold(n) for n in ccp.NEVER_DIRS}
    for sub in (REVIEWS_DIRNAME, EXECUTIONS_DIRNAME, DISCARD_ARCHIVE_DIRNAME):
        rel = f"{ARTIFACTS_DIRNAME}/{sub}"
        assert ccp.dir_exclusion(rel) is not None, rel
        assert ccp.structural_exclusion(f"{rel}/record.json") == ccp.R_WORK_RECORDS

    # Every never-folder names which kind of state it is, asserted at import.
    for name in ccp.NEVER_DIRS:
        assert ccp.dir_exclusion(name) is not None, name

    for rel_dir in (".runtime", ".credentials", "__pycache__", "node_modules"):
        assert ccp.dir_exclusion(rel_dir) is not None, rel_dir

    # And a user's own folder is not denied, which is the line being held.
    assert ccp.dir_exclusion("notes") is None
    assert ccp.dir_exclusion("data") is None


def test_the_owners_uploads_stay_home(tmp_path):
    """``canon/`` holds uploads. Private by default (host decision 2026-10-03):
    an upload can be anything personal, and Hard Rule 9 makes it authoritative
    content the platform never reshapes -- so it is not the platform's to
    publish on the owner's behalf.

    This also pins the one place the folder is named. Unlike ``artifacts/``,
    this is a name MATCH not a derivation: every writer spells the folder as a
    bare literal (``api/universe.py``, ``work_targets.py``), so
    ``canon_io.CANON_DIRNAME`` holds it once. If someone renames the folder at
    those call sites without changing the constant, this test is what notices.
    """
    from tinyassets.ingestion.canon_io import CANON_DIRNAME

    assert CANON_DIRNAME == "canon", "the writers spell it this way as a literal"
    assert ccp.fold(CANON_DIRNAME) in {ccp.fold(n) for n in ccp.NEVER_DIRS}

    universe = tmp_path / "cc"
    universe.mkdir()
    secret = "the acquisition term sheet, uploaded by Alice"
    _write(universe, f"{CANON_DIRNAME}/sources/termsheet.md", secret + "\n")
    _write(universe, f"{CANON_DIRNAME}/index.json", '{"sources": 1}')
    _write(universe, "notes/keep.md", "a shareable note\n")

    files, excluded = ccp.collect(universe, exclude=[], memory_items={})
    assert "notes/keep.md" in files
    assert not [p for p in files if p.startswith(CANON_DIRNAME)]
    assert secret not in b"".join(files.values()).decode("utf-8", "replace")
    assert any(row["reason"] == ccp.R_UPLOADS for row in excluded)


def test_a_discarded_work_target_does_not_travel(tmp_path):
    """The P1 the review found, as a writer-to-package regression.

    ``work_targets.discard_archive_dir`` preserves the whole target under
    ``artifacts/discarded_targets/``, including the request text a published
    package must never carry -- the same class as ``requests.json``, two
    folders down where the root allowlist could not see it.
    """
    from tinyassets.work_targets import ARTIFACTS_DIRNAME, DISCARD_ARCHIVE_DIRNAME

    universe = tmp_path / "cc"
    universe.mkdir()
    secret = "Prepare the acquisition offer for Acme"
    _write(universe, f"{ARTIFACTS_DIRNAME}/{DISCARD_ARCHIVE_DIRNAME}/req_demo.json",
           '{"title": "' + secret + '"}')
    _write(universe, "notes/keep.md", "a shareable note\n")

    files, _excluded = ccp.collect(universe, exclude=[], memory_items={})
    assert "notes/keep.md" in files
    assert not [p for p in files if p.startswith(ARTIFACTS_DIRNAME)]
    assert secret not in b"".join(files.values()).decode("utf-8", "replace")


def test_the_listing_the_owner_reads_is_the_bundle_that_ships(tmp_path):
    """Every kind that travelled before this change still travels, and the
    paths the owner confirms against are the paths and BYTES in the blob.

    The first version of this only called ``collect`` and asserted the two
    sets were disjoint, which the review correctly said proves nothing about
    preview-versus-bundle. This goes through ``build_publish_package`` and
    decodes the blob with ``check_blob``, so the manifest the tab renders from
    and the bytes an installer receives are compared directly.
    """
    universe = tmp_path / "cc"
    universe.mkdir()
    for rel, body in TRAVELS.items():
        _write(universe, rel, body)
    _write(universe, "app.html", "<main>ui</main>\n")
    # An excluded control, so the comparison is not vacuous.
    _write(universe, "founder.md", "Alice lives on Elm Street.\n")

    built = ccp.build_publish_package(
        universe, name="GTM Village", description="A village",
        options={"exclude": [], "memory_items": {}}, branch_rows=[],
        workflows=[], ui="", automations=[])

    _manifest, carried = ccp.check_blob(built["blob"])
    rows = built["manifest"]["files"]
    listed = {row["path"] for row in rows}
    excluded = {row["path"] for row in built["excluded"]}

    for rel, body in TRAVELS.items():
        assert rel in carried, rel
        assert carried[rel] == body.encode("utf-8"), rel
    assert "app.html" in carried
    # The listing IS the bundle: same paths, and the digests the tab shows are
    # the digests of the bytes an installer decodes.
    assert listed == set(carried)
    for row in rows:
        assert row["sha256"] == hashlib.sha256(carried[row["path"]]).hexdigest(), row["path"]
        assert row["size"] == len(carried[row["path"]]), row["path"]
    assert not (excluded & listed)
    assert "founder.md" in excluded and "founder.md" not in carried


def test_a_one_class_value_is_neither_excluded_nor_flagged():
    # A one-class run the parser reads as opaque, even assigned to a secret
    # name: in the live village these were minified-code identifiers, not keys.
    line = 'token: "qwzx-plmk-vbtr-hgfd"'
    assert ccp.text_detection(line) is None and not ccp.text_suspect(line)
    assert ccp.text_detection('token: "Punc9Before7Expression3"') == ccp.R_CREDENTIAL


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


@pytest.mark.parametrize("key", KEY_FORMATS)
def test_a_published_key_format_pasted_bare_is_certain(key):
    data = f"remember to rotate {key} next week\n".encode()
    assert ccp.classify("notes/todo.md", data, exclude=[], memory_items={}) == (
        None, ccp.R_CREDENTIAL)


@pytest.mark.parametrize("line", [
    "run_id: 0123456789abcdef",
    "commit 0123456789abcdef0123456789abcdef01234567",
    "sha256 9f6463eefbeb574f3f803e643307ff7b88ece9decc0bf8caaeebfc9d70b5a8de",
    "uuid4 hex c5a83ff1d2f0475ea9c232101dc4cbce",
])
def test_id_shaped_hex_is_not_even_flagged(line):
    assert ccp.text_detection(line) is None and not ccp.text_suspect(line)


def test_the_review_list_is_grouped_and_short():
    flagged = [{"path": f"notes/n{i}.md", "note": ccp.N_OPAQUE} for i in range(40)]
    flagged += [{"path": "notes/pay.md", "note": 'mentions "salary"'}]
    groups = ccp.review_groups(flagged)
    assert [g["count"] for g in groups] == [1, 40]
    assert all(len(g["shown"]) <= ccp.REVIEW_SHOWN for g in groups)


@pytest.mark.parametrize("line", ["sk-skeleton-loader-component-header-title-extra-long",
                                  "sk-proj-settings-panel-header-title-row"])
def test_a_kebab_identifier_starting_sk_is_not_a_key(line):
    assert ccp.text_detection(line) is None



@pytest.mark.parametrize("run,expected", [
    ("Zq8rT2vX9mK4pL7nB3wE6yH1jD5Q", True),        # random, mixed, long
    ("getVillageSnapshotAdapter2Check", False),     # camelCase identifier
    ("village_panel_header_title_v2", False),       # snake identifier
    ("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6", False),    # id-length hex
    ("Zq8rT2vX9mK4pL7nB3wE", False),                # under the length floor
])
def test_the_suspect_tier_is_key_like_runs_only(run, expected):
    assert ccp.key_like(run) is expected
