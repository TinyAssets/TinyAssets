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
    AutomationUnavailable,
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


def test_the_owner_lists_and_pages_their_own_files(home: Path) -> None:
    listing = _read("command_center_files", "notes")
    assert listing["universe_id"] == UNIVERSE
    assert {(e["name"], e["kind"]) for e in listing["entries"]} == {
        ("board.md", "file"), ("blob.bin", "file")}
    root = _read("command_center_files", "/u")
    assert {"name": "notes", "kind": "dir"} in root["entries"]

    whole = (home / UNIVERSE / "notes" / "board.md").read_bytes().decode()
    # A window smaller than one multi-byte character still makes progress, and
    # every chunk decodes: the pages concatenate to the file exactly.
    pieces, offset = [], 0
    while offset is not None:
        page = _read(
            "command_center_file", "/u/notes/board.md", file_offset=offset, file_max_bytes=3,
        )
        assert page["encoding"] == "text", page
        pieces.append(page["text"])
        offset = page["next_offset"]
    assert "".join(pieces) == whole
    assert page["eof"] is True

    blob = _read("command_center_file", "notes/blob.bin")
    assert blob["encoding"] == "base64" and blob["size_bytes"] == 256


@pytest.mark.parametrize("actor,universe,query", [
    (BOB, UNIVERSE, "notes/board.md"),          # another user names Alice's home
    (OWNER, UNIVERSE, "notes/absent.md"),         # the reference: an absent path
    (OWNER, UNIVERSE, "../universe_bob/secret.md"),
    (OWNER, UNIVERSE, "/data/universe_bob/secret.md"),
    (OWNER, UNIVERSE, "notes\\board.md"),
    (OWNER, BOB_UNIVERSE, "secret.md"),         # Alice names Bob's home
    (None, UNIVERSE, "notes/board.md"),
])
def test_every_refusal_is_the_same_not_found(home: Path, actor, universe, query) -> None:
    for target in ("command_center_file", "command_center_files"):
        out = _read(target, query, actor=actor, universe=universe)
        assert out == {"error": "not_found", "resource": "command_center_file"}, (target, out)


def test_a_reader_without_admin_is_refused(home: Path) -> None:
    from tinyassets.daemon_server import grant_universe_access

    grant_universe_access(home, universe_id=UNIVERSE, actor_id=BOB, permission="write")
    assert _read("command_center_file", "notes/board.md", actor=BOB)["error"] == "not_found"


def test_a_link_in_the_folder_is_never_followed(home: Path) -> None:
    link = home / UNIVERSE / "notes" / "stolen.md"
    try:
        os.symlink(home / BOB_UNIVERSE / "secret.md", link)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a symlink")
    assert _read("command_center_file", "notes/stolen.md")["error"] == "not_found"
    names = [e["name"] for e in _read("command_center_files", "notes")["entries"]]
    assert "stolen.md" not in names


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


def test_a_universe_root_that_is_a_link_is_refused(home: Path) -> None:
    """Astra round 2, P1: resolving the root path stripped a root link, so an
    owned universe whose root pointed at Bob's folder served BOB ONLY."""
    import shutil

    root = home / UNIVERSE
    shutil.rmtree(root)
    _link_dir(root, home / BOB_UNIVERSE)
    for target, query in (("command_center_file", "secret.md"), ("command_center_files", "")):
        out = _read(target, query)
        assert out == {"error": "not_found", "resource": "command_center_file"}, (target, out)
        assert "BOB ONLY" not in json.dumps(out)


@pytest.mark.skipif(os.name == "nt", reason="the anchored listing is the POSIX path")
def test_a_directory_swapped_for_a_link_after_listing_discloses_nothing(
    home: Path, monkeypatch,
) -> None:
    """Astra round 2, P1: a path-based lstat after the listing followed a swap
    into Bob's folder and disclosed a file size. Entries are now stat()ed
    through the directory descriptor, which the swap cannot re-point."""
    import shutil

    notes = home / UNIVERSE / "notes"
    (home / BOB_UNIVERSE / "board.md").write_text("B" * 777, encoding="utf-8")
    real_listdir = os.listdir

    def listdir_then_swap(target):
        names = real_listdir(target)
        shutil.move(str(notes), str(home / UNIVERSE / "notes-moved"))
        _link_dir(notes, home / BOB_UNIVERSE)
        return names

    monkeypatch.setattr(os, "listdir", listdir_then_swap)
    listing = _read("command_center_files", "notes")
    sizes = {e["name"]: e.get("size_bytes") for e in listing["entries"]}
    assert sizes.get("board.md") != 777, listing
    assert "secret.md" not in sizes


@pytest.mark.skipif(os.name != "nt", reason="a directory junction is a Windows link")
def test_a_directory_junction_is_never_followed(home: Path) -> None:
    """Symlinks need privilege on Windows; junctions do not, and a reader that
    only checks is_symlink() follows them into another universe."""
    import subprocess

    junction = home / UNIVERSE / "notes" / "elsewhere"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(home / BOB_UNIVERSE)],
                          capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("this host cannot create a junction")
    assert _read("command_center_file", "notes/elsewhere/secret.md")["error"] == "not_found"
    assert _read("command_center_files", "notes/elsewhere")["error"] == "not_found"
    names = [e["name"] for e in _read("command_center_files", "notes")["entries"]]
    assert "elsewhere" not in names


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


def test_an_app_event_subscription_must_name_the_event(home: Path) -> None:
    with pytest.raises(AutomationUnavailable):
        _subscribe(home, None)


def test_the_owner_emits_and_only_the_named_subscription_wakes(home: Path) -> None:
    _subscribe(home, "visit")
    assert _emit("other") == {"emitted": True, "name": "other", "woke": 0}
    assert _wakes(home) == []
    assert _emit("visit", {"who": "baker"})["woke"] == 1
    [wake] = _wakes(home)
    assert wake.branch_def_id == SCRIBE
    assert wake.inputs["event"]["type"] == "app_event"
    assert wake.inputs["event"]["data"] == {"who": "baker"}


def test_another_user_cannot_wake_the_owners_agents(home: Path) -> None:
    _subscribe(home, "visit")
    # Naming Alice's home: refused outright, identical for any universe not his.
    assert _emit("visit", actor=BOB) == {"error": "not_found", "resource": "universe"}
    # From his own home: his event, his subscriptions (none) -- never Alice's.
    assert _emit("visit", actor=BOB, universe=BOB_UNIVERSE)["woke"] == 0
    assert _wakes(home) == []


@pytest.mark.parametrize("payload", [
    {"name": "Visit"}, {"name": ""}, {"name": "x" * 65},
    {"name": "visit", "data": ["not", "an", "object"]},
    {"name": "visit", "data": {"big": "x" * 9000}},
    {"name": "visit", "branch_def_id": SCRIBE},
])
def test_a_malformed_event_stores_nothing(home: Path, payload) -> None:
    from tinyassets.universe_server import run_graph

    _subscribe(home, "visit")
    with _as(OWNER):
        out = json.loads(run_graph(operation="emit_event", graph_id=UNIVERSE,
                                   inputs_json=json.dumps(payload)))
    assert "error" in out, out
    assert _wakes(home) == []


# ---------------------------------------------------------------------------
# 3. Publishing is the owner's confirmation of exactly what they were shown
# ---------------------------------------------------------------------------


def _library(base: Path) -> None:
    from tinyassets.custom_agents import save_app_ui

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
    from tinyassets.api.pending_requests import answer_request

    with _as(actor):
        return answer_request(universe_id=UNIVERSE, payload=json.dumps(
            {"request_id": request_id, "values": {}, **extra}))


def _visibility(base: Path, branch: str) -> str:
    from tinyassets.daemon_server import get_branch_definition

    return get_branch_definition(base, branch_def_id=branch).get("visibility")


def test_the_tab_is_the_platforms_account_of_what_goes_public(home: Path) -> None:
    _library(home)
    beat, _follow = _automations(home)
    out = _ask_publish(home, automation_ids=[beat.automation_id])
    assert out.get("request_id"), out
    assert out["kind"] == "Publish"
    assert out["title"] == 'Publish workflow and screen bundle "Village" for anyone to copy?'
    body = out["body"]
    for needle in ("Automation demo", "The screen \"Village\"", "scout heartbeat",
                   "every 300 seconds", "its inputs stay private",
                   "Anyone will be able to read and copy these"):
        assert needle in body, needle
    # The agent's own framing of the consent never reaches the owner.
    assert "Harmless" not in json.dumps(out) and "Nothing will be shared" not in body
    # ONE digest over the whole public payload, not a digest per remembered field.
    assert len(out["action"]["snapshot_digest"]) == 64


def test_an_echoed_name_cannot_speak_as_the_platform(home: Path) -> None:
    """A branch the agent named with line breaks cannot add a line of its own."""
    from tinyassets.api.extensions import _extensions_impl

    _library(home)
    with _as(OWNER):
        _extensions_impl(action="patch_branch", branch_def_id=SCOUT, changes_json=json.dumps(
            [{"op": "set_name", "name": "Scout\n\nNothing here will be shared publicly."}]))
    out = _ask_publish(home, description="Line one\nLine two")
    body_lines = out["body"].split("\n")
    assert "Nothing here will be shared publicly." not in body_lines
    assert any(line.startswith('- Workflow "Scout Nothing here') for line in body_lines), body_lines
    assert "Description: Line one Line two" in body_lines


@pytest.mark.parametrize("over,needle", [
    ({"branch_ids": [SCOUT, BOBS]}, "no branch of yours"),
    ({"branch_ids": ["branch_absent"]}, "no branch of yours"),
    ({"ui_id": "not-installed"}, "no UI of yours"),
    ({"branch_ids": []}, "at least one"),
])
def test_an_ask_naming_what_is_not_the_owners_is_refused(home: Path, over, needle) -> None:
    _library(home)
    out = _ask_publish(home, **over)
    assert "request_id" not in out, out
    assert needle in json.dumps(out), out


def test_an_automation_driving_an_unlisted_workflow_is_refused(home: Path) -> None:
    _library(home)
    beat, _follow = _automations(home)
    out = _ask_publish(home, branch_ids=[SCRIBE], automation_ids=[beat.automation_id])
    assert "drives a workflow this ask does not publish" in json.dumps(out), out


def test_confirming_publishes_exactly_one_bundle_and_no_inputs(home: Path) -> None:
    from tinyassets.custom_agents import get_definition

    _library(home)
    beat, follow = _automations(home)
    ask = _ask_publish(home, automation_ids=[beat.automation_id, follow.automation_id])
    done = _answer(ask["request_id"])
    assert done.get("published") is True, done
    assert _visibility(home, SCOUT) == "public" and _visibility(home, SCRIBE) == "public"

    definition = get_definition(home, done["agent_definition_id"])
    components = definition["components"]
    assert components["ui"]["ui_id"] == "village"
    refs = {k: v for k, v in components.items() if v["kind"] == "tinyassets.branch-ref.v1"}
    minted = set(done["branch_versions"].values())
    assert {v["published_version_id"] for v in refs.values()} == minted
    specs = [v for v in components.values() if v["kind"] == "tinyassets.automation-spec.v1"]
    assert len(specs) == 2
    assert "PRIVATE INPUT" not in json.dumps(definition)
    assert all("inputs" not in s for s in specs)
    followed = next(s for s in specs if s["trigger"]["event_type"] == "run_completed")
    # The copy's filter names the WORKFLOW, not the author's branch id.
    assert followed["trigger"]["event_filter"]["branch_def_id"] in refs
    assert SCOUT not in json.dumps(specs)

    # A second confirm of the same ask publishes nothing more.
    again = _answer(ask["request_id"])
    assert again.get("error") == "already_resolved", again


def test_work_changed_after_the_tab_was_shown_publishes_nothing(home: Path) -> None:
    from tinyassets.api.extensions import _extensions_impl
    from tinyassets.custom_agents import list_definitions

    _library(home)
    before = len(list_definitions(home, author_id=OWNER))
    ask = _ask_publish(home)
    with _as(OWNER):
        patched = json.loads(_extensions_impl(
            action="patch_branch", branch_def_id=SCRIBE,
            changes_json=json.dumps([{"op": "set_name", "name": "Something else"}])))
    assert not patched.get("error"), patched
    out = _answer(ask["request_id"])
    assert out.get("error") == "publish_refused" and out.get("request_pending") is True, out
    assert "changed after you were shown it" in out["detail"]
    assert _visibility(home, SCOUT) == "private" and _visibility(home, SCRIBE) == "private"
    assert len(list_definitions(home, author_id=OWNER)) == before


@pytest.mark.parametrize("edit", [
    [{"op": "set_tags", "tags": ["PRIVATE customer account 12345"]}],
    [{"op": "set_description", "description": "private text"}],
])
def test_any_public_field_changed_after_the_tab_publishes_nothing(home: Path, edit) -> None:
    """Codex refute 2026-09-29 P1: tags sat outside the digest. The digest now
    covers every field that becomes public, not a remembered list of them."""
    from tinyassets.api.extensions import _extensions_impl

    _library(home)
    ask = _ask_publish(home)
    with _as(OWNER):
        patched = json.loads(_extensions_impl(
            action="patch_branch", branch_def_id=SCOUT, changes_json=json.dumps(edit)))
    assert not patched.get("error"), patched
    out = _answer(ask["request_id"])
    assert out.get("error") == "publish_refused", out
    assert _visibility(home, SCOUT) == "private"


def test_an_edit_racing_the_confirm_cannot_go_public(home: Path, monkeypatch) -> None:
    """Codex refute 2026-09-29 P1: the edit lands AFTER the up-front check --
    here, while the versions are being minted. The flip re-checks every row
    against the snapshot in its own transaction, so it refuses by itself."""
    from tinyassets import branch_versions
    from tinyassets.api.extensions import _extensions_impl
    from tinyassets.custom_agents import list_definitions

    _library(home)
    before = len(list_definitions(home, author_id=OWNER))
    ask = _ask_publish(home)
    real_mint = branch_versions.publish_branch_version
    edited = []

    def mint_then_edit(*args, **kwargs):
        version = real_mint(*args, **kwargs)
        if not edited:
            edited.append(True)
            with _as(OWNER):
                _extensions_impl(
                    action="patch_branch", branch_def_id=SCRIBE, changes_json=json.dumps(
                        [{"op": "set_description", "description": "PRIVATE DESCRIPTION"}]))
        return version

    monkeypatch.setattr(branch_versions, "publish_branch_version", mint_then_edit)
    out = _answer(ask["request_id"])
    assert edited, "the edit really landed inside the window"
    assert out.get("error") == "publish_refused", out
    assert len(list_definitions(home, author_id=OWNER)) == before, "no bundle was published"
    # The set goes public whole or not at all: the unchanged branch listed
    # before the changed one did not go public either.
    assert _visibility(home, SCRIBE) == "private", "the changed branch never went public"
    assert _visibility(home, SCOUT) == "private", "nor did the rest of the set"
    assert "Already public" not in out["detail"], out


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


def test_a_nested_field_added_after_the_tab_publishes_nothing(home: Path) -> None:
    """Astra round 2, P1: normalizing the row dropped unknown nested keys, so
    `node_defs[0].private_note` added after consent was outside the digest and
    published. The snapshot is the stored row itself."""
    from tinyassets.daemon_server import get_branch_definition

    _library(home)
    ask = _ask_publish(home)
    _raw_edit(home, SCOUT,
              lambda raw: raw["node_defs"][0].__setitem__("private_note", "BOB MUST NOT"))
    assert get_branch_definition(home, branch_def_id=SCOUT)["node_defs"][0].get("private_note"), \
        "the stored row really carries the nested field"
    out = _answer(ask["request_id"])
    assert out.get("error") == "publish_refused", out
    assert _visibility(home, SCOUT) == "private"


def test_a_credential_anywhere_in_a_workflow_is_refused(home: Path) -> None:
    """Astra round 2, P1: a credential in a prompt_template reached a public
    version while the same value was refused in bundle text. One scanner now
    covers every branch row as well as the bundle."""
    token = "sk-" + "live" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
    _library(home)
    _raw_edit(home, SCRIBE, lambda raw: raw["node_defs"][0].__setitem__(
        "prompt_template", f"Call the API with {token}"))
    out = _ask_publish(home)
    assert "request_id" not in out, out
    assert "cannot be made public" in json.dumps(out), out
    assert token not in json.dumps(out), "the refusal names the place, never the value"
    assert _visibility(home, SCRIBE) == "private"


def _bob_reads_version(version_id: str) -> str:
    from tinyassets.api.extensions import _extensions_impl

    with _as(BOB):
        return _extensions_impl(action="get_branch_version", branch_version_id=version_id)


def test_publishing_exposes_the_confirmed_version_never_the_history(home: Path) -> None:
    """Founder 2026-09-30 (astra round 3, P1): publishing made a branch's whole
    private edit history readable, including a credential the owner had since
    removed. Only the version the owner confirmed is marked published."""
    from tinyassets.api.extensions import _extensions_impl
    from tinyassets.custom_agents import get_definition

    token = "sk-" + "live" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6"
    _library(home)
    with _as(OWNER):
        for prompt in (f"Use {token}", "the clean prompt"):
            patched = json.loads(_extensions_impl(
                action="patch_branch", branch_def_id=SCOUT, changes_json=json.dumps(
                    [{"op": "update_node", "node_id": "n1", "prompt_template": prompt}])))
            assert not patched.get("error"), patched
        history = json.loads(_extensions_impl(action="list_branch_versions",
                                               branch_def_id=SCOUT))["versions"]
    assert any(token in json.dumps(v) for v in history), "the history really holds it"

    done = _answer(_ask_publish(home)["request_id"])
    assert done.get("published") is True, done
    confirmed = set(done["branch_versions"].values())
    refs = {c["published_version_id"] for c in get_definition(
        home, done["agent_definition_id"])["components"].values()
        if c["kind"] == "tinyassets.branch-ref.v1"}
    assert refs == confirmed
    for version in history:
        seen = _bob_reads_version(version["branch_version_id"])
        if version["branch_version_id"] not in confirmed:
            assert "not found" in seen and token not in seen, seen
    for version_id in confirmed:
        assert version_id in _bob_reads_version(version_id)
    with _as(BOB):
        listed = _extensions_impl(action="list_branch_versions", branch_def_id=SCOUT)
    assert token not in listed
    assert {v["branch_version_id"] for v in json.loads(listed)["versions"]} <= confirmed


def test_a_refused_accept_leaves_its_minted_versions_unreadable(home: Path, monkeypatch) -> None:
    """Astra round 3, P1: a version minted during an accept that was then
    refused was readable. Minted versions stay unmarked until the commit point."""
    from tinyassets import branch_versions
    from tinyassets.api.extensions import _extensions_impl

    _library(home)
    ask = _ask_publish(home)
    real_mint = branch_versions.publish_branch_version
    minted: list[str] = []

    def mint_then_edit(*args, **kwargs):
        version = real_mint(*args, **kwargs)
        minted.append(version.branch_version_id)
        if len(minted) == 1:
            with _as(OWNER):
                _extensions_impl(
                    action="patch_branch", branch_def_id=SCRIBE, changes_json=json.dumps(
                        [{"op": "set_description", "description": "changed"}]))
        return version

    monkeypatch.setattr(branch_versions, "publish_branch_version", mint_then_edit)
    out = _answer(ask["request_id"])
    assert out.get("error") == "publish_refused", out
    assert minted
    for version_id in minted:
        assert "not found" in _bob_reads_version(version_id)
    # And they stay unreadable if the owner later makes the branch public some
    # other way: an unconsented version was never marked, so a branch going
    # public does not publish it (the branch's privacy alone would hide the
    # mint-time-marking mistake).
    from tinyassets.daemon_server import update_branch_definition

    for branch in (SCOUT, SCRIBE):
        update_branch_definition(home, branch_def_id=branch, updates={"visibility": "public"})
    for version_id in minted:
        assert "not found" in _bob_reads_version(version_id), version_id


@pytest.mark.parametrize("updates", [
    {"stats": {"note": "OWNER PRIVATE"}},
    {"version": 7},
])
def test_stats_and_version_are_pinned_too(home: Path, updates) -> None:
    """Astra round 3, P1: `stats` and `version` sat outside the snapshot while a
    public read returns both. Only visibility, published and updated_at are
    exempt now."""
    from tinyassets.daemon_server import update_branch_definition

    _library(home)
    ask = _ask_publish(home)
    update_branch_definition(home, branch_def_id=SCOUT, updates=updates)
    out = _answer(ask["request_id"])
    assert out.get("error") == "publish_refused", out
    assert _visibility(home, SCOUT) == "private"


def test_a_bundle_that_fails_to_publish_leaves_nothing_public(home: Path, monkeypatch) -> None:
    """Astra round 2, P2: branches went public before the bundle was validated.
    The bundle is now validated before any write, and a storage failure after
    the flip flips the branches back."""
    import tinyassets.api.custom_agents as api_custom_agents

    _library(home)
    ask = _ask_publish(home)
    monkeypatch.setattr(api_custom_agents, "custom_agents",
                        lambda **_kw: {"error": "agent_storage_unavailable"})
    out = _answer(ask["request_id"])
    assert out.get("error") == "publish_refused", out
    assert "nothing was left public" in out["detail"], out
    assert _visibility(home, SCOUT) == "private" and _visibility(home, SCRIBE) == "private"
    from tinyassets.branch_versions import list_branch_versions
    from tinyassets.daemon_server import update_branch_definition

    for bid in (SCOUT, SCRIBE):
        versions = list_branch_versions(home, bid)
        assert versions
        update_branch_definition(home, branch_def_id=bid, updates={"visibility": "public"})
        for version in versions:
            assert "not found" in _bob_reads_version(version.branch_version_id)


def test_a_failed_publish_does_not_withdraw_an_earlier_publication(
    home: Path, monkeypatch,
) -> None:
    """Astra refute 2026-09-30 (round 2 on the publication-mark backfill): a
    failed bundle cleared every version mark and made every branch private,
    including a branch and version that were public BEFORE this request. It now
    takes back only what this request did."""
    import tinyassets.api.custom_agents as api_custom_agents
    from tinyassets import branch_versions
    from tinyassets.api.publish_requests import _flipped
    from tinyassets.daemon_server import get_branch_definition, update_branch_definition

    _library(home)
    update_branch_definition(home, branch_def_id=SCOUT,
                             updates={"visibility": "public", "published": True})
    earlier = branch_versions.publish_branch_version(
        home, _flipped(get_branch_definition(home, branch_def_id=SCOUT)),
        publisher=OWNER, public=True).branch_version_id
    ask = _ask_publish(home)
    real_mint = branch_versions.publish_branch_version
    minted: list[str] = []

    def recording_mint(*args, **kwargs):
        version = real_mint(*args, **kwargs)
        minted.append(version.branch_version_id)
        return version

    monkeypatch.setattr(branch_versions, "publish_branch_version", recording_mint)
    monkeypatch.setattr(api_custom_agents, "custom_agents",
                        lambda **_kw: {"error": "agent_storage_unavailable"})
    out = _answer(ask["request_id"])
    assert out.get("error") == "publish_refused", out
    assert earlier in minted, "the mint must dedupe onto the earlier publication"

    assert _visibility(home, SCOUT) == "public", "an earlier publication stays"
    assert json.loads(_bob_reads_version(earlier))["branch_version_id"] == earlier
    assert _visibility(home, SCRIBE) == "private", "this request's own flip is undone"
    for version_id in minted:
        if version_id != earlier:
            assert "not found" in _bob_reads_version(version_id), version_id


def test_a_failed_publish_does_not_undo_a_privacy_change_made_meanwhile(
    home: Path, monkeypatch,
) -> None:
    """Astra round 3: compensation restored the pre-flip "public" even after the
    owner made the branch private while the bundle write was pending."""
    import tinyassets.api.custom_agents as api_custom_agents
    from tinyassets.daemon_server import update_branch_definition

    _library(home)
    update_branch_definition(home, branch_def_id=SCOUT,
                             updates={"visibility": "public", "published": True})
    ask = _ask_publish(home)

    def owner_withdraws_then_storage_fails(**_kw):
        update_branch_definition(home, branch_def_id=SCOUT, updates={"visibility": "private"})
        return {"error": "agent_storage_unavailable"}

    monkeypatch.setattr(api_custom_agents, "custom_agents", owner_withdraws_then_storage_fails)
    out = _answer(ask["request_id"])
    assert out.get("error") == "publish_refused", out
    assert _visibility(home, SCOUT) == "private", "the owner's later withdrawal stands"


def test_only_the_portable_ui_fields_are_published(home: Path) -> None:
    """Codex refute 2026-09-29 P1: a stored component carrying `inputs` rode
    into the public definition. Only the seven fields the app renders go."""
    from tinyassets.custom_agents import get_definition, save_app_ui

    save_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE, expected_revision=0,
                changes={"ui_library": [{**UI, "inputs": {"customer_note": "PRIVATE INPUT"}}]})
    done = _answer(_ask_publish(home)["request_id"])
    assert done.get("published") is True, done
    ui = get_definition(home, done["agent_definition_id"])["components"]["ui"]
    assert sorted(ui) == sorted(UI)
    assert "PRIVATE INPUT" not in json.dumps(ui)


def test_nobody_but_the_owner_can_confirm(home: Path) -> None:
    _library(home)
    ask = _ask_publish(home)
    out = _answer(ask["request_id"], actor=BOB)
    assert out.get("error") == "not_found", out
    assert _visibility(home, SCOUT) == "private"


def test_the_served_surface_has_no_way_to_answer_its_own_ask(monkeypatch) -> None:
    from tinyassets import engine_mcp_server as engine

    monkeypatch.setattr(engine, "_binding_error", lambda: None)
    for op in ("answer", "answer_request", "confirm", "accept"):
        out = json.loads(engine.write_graph(target="pending_request", operation=op,
                                            payload_json="{}"))
        assert "belong to the person you asked" in out.get("error", ""), (op, out)


# ---------------------------------------------------------------------------
# 4. A second user installs: every copy is theirs and private
# ---------------------------------------------------------------------------


def test_a_second_user_installs_private_copies(home: Path) -> None:
    from tinyassets.custom_agents import get_app_ui, get_definition, save_app_ui
    from tinyassets.daemon_server import get_branch_definition
    from tinyassets.universe_server import write_graph

    _library(home)
    done = _answer(_ask_publish(home)["request_id"])
    definition = get_definition(home, done["agent_definition_id"])
    copies = []
    for component in definition["components"].values():
        if component["kind"] != "tinyassets.branch-ref.v1":
            continue
        with _as(BOB):
            made = json.loads(write_graph(
                target="branch", operation="remix", payload_json=json.dumps(
                    {"name": component["name"], "fork_from": component["published_version_id"],
                     "visibility": "private"})))
        bid = made.get("branch_def_id") or (made.get("branch") or {}).get("branch_def_id")
        assert bid, made
        copies.append(get_branch_definition(home, branch_def_id=bid))
    assert len(copies) == 2
    assert all(c["author"] == BOB and c["visibility"] == "private" for c in copies)
    save_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE, expected_revision=0,
                changes={"ui_library": [definition["components"]["ui"]]})
    bobs = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
    assert bobs["ui_library"][0]["ui_id"] == "village"
    # Alice's own row is untouched by Bob's install.
    assert len(get_app_ui(home, owner_user_id=OWNER, universe_id=UNIVERSE)["ui_library"]) == 1
