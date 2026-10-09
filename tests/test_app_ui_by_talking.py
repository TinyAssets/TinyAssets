"""A universe changes its person's screen and harness by talking.

Live 2026-09-30: the founder asked their universe "is the GTM Village done? lets
try it out", and it answered that it could not switch the screen because its UI
read "still cuts off before the revision needed to save that change safely". A
whole-library read-modify-write cannot work through a model door whose results
are bounded. These tests drive the SERVED path (``mcp.call_tool``, the result
ceiling included) against a library bigger than that ceiling, and pin the
targeted operations that replace the whole-row round trip: no revision named,
one UI touched, nobody else's row reachable, no concurrent change lost.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from tinyassets import custom_agents
from tinyassets.custom_agents import (
    AgentConflictError,
    change_app_ui_entry,
    get_app_ui,
    save_app_ui,
)
from tinyassets.storage import DB_FILENAME

ALICE = "acct_alice"
BOB = "acct_bob"
HOME = "u-alice"
VILLAGE = "gtm-village"


def _ui(ui_id: str, *, name: str = "", filler: int = 0) -> dict:
    return {"kind": "tinyassets.app-ui.v1", "version": 1, "ui_id": ui_id,
            "name": name or ui_id.title(), "markup": "<div id=v>" + "x" * filler + "</div>",
            "style": "#v{color:red}", "script": "tinyassets.whoami()"}


def _large_library() -> list[dict]:
    """Forty 40 KB UIs and the village: far past any single-result ceiling."""
    library = [_ui(f"screen-{n}", filler=40_000) for n in range(40)]
    library.insert(17, _ui(VILLAGE, name="GTM Village — design preview", filler=40_000))
    return library


def _row(base) -> dict:
    return get_app_ui(base, owner_user_id=ALICE, universe_id=HOME)


# --------------------------------------------------------------------------- #
# the served path, with a library bigger than the model ceiling
# --------------------------------------------------------------------------- #


@pytest.fixture
def served(tmp_path, monkeypatch):
    from tests.engine_authority_helpers import seed_bound_engine
    from tinyassets import engine_mcp_server as engine
    from tinyassets import engine_result_bounds as bounds

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv(bounds.CEILING_ENV, raising=False)
    monkeypatch.delenv(bounds.CONTEXT_TOKENS_ENV, raising=False)
    monkeypatch.setattr(engine, "_ACTOR_ID", ALICE)
    monkeypatch.setattr(engine, "_GRAPH_ID", HOME)
    (tmp_path / HOME).mkdir()
    seed_bound_engine(monkeypatch)
    from tests.test_ta_capabilities import signed_launch
    signed_launch(monkeypatch)
    save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME, expected_revision=0,
                changes={"ui_library": _large_library()})
    return engine


def _call(engine, tool: str, **arguments) -> dict:
    """One served call; a refusal reaches the model as a tool error, read here."""
    from fastmcp.exceptions import ToolError

    try:
        text = asyncio.run(engine.mcp.call_tool(tool, arguments)).content[0].text
    except ToolError as refused:
        text = str(refused)
    document = json.loads(text)
    assert document.get("truncated") is not True, f"{tool} was cut: {text[:300]}"
    return document


def test_a_large_library_is_switched_and_edited_one_ui_at_a_time(served, tmp_path) -> None:
    from tinyassets import engine_result_bounds as bounds

    before = _row(tmp_path)
    whole = json.dumps({"app_ui": before})
    assert len(whole.encode()) > 20 * bounds.DEFAULT_CEILING_BYTES, "not past the ceiling"

    # What the universe reads first: every UI by id and name, no bodies.
    index = _call(served, "read_graph", target="app_ui")["app_ui"]
    village = next(u for u in index["uis"] if u["name"] == "GTM Village — design preview")
    assert village["ui_id"] == VILLAGE and len(index["uis"]) == 41

    # "let's try it out": one call, no revision.
    activated = _call(served, "write_graph", target="app_ui", operation="activate",
                      payload_json=json.dumps({"ui_id": VILLAGE}))
    assert activated["status"] == "saved", activated
    assert _row(tmp_path)["ui_selection"] == {"version": 1, "state": "active", "ui_id": VILLAGE}

    # Edit one component: read just the field it changes, then an exact edit.
    style = _call(served, "read_graph", target="app_ui", query=VILLAGE, field_name="style")
    assert style["chunk"] == "#v{color:red}" and style["next_offset"] is None
    edited = _call(served, "write_graph", target="app_ui", operation="edit_ui",
                   payload_json=json.dumps({
                       "ui_id": VILLAGE, "expected_etag": style["etag"],
                       "edits": [{"field": "style", "old": "red", "new": "teal"}]}))
    assert edited["status"] == "saved", edited

    after = _row(tmp_path)
    assert after["revision"] == before["revision"] + 2
    changed = [(b, a) for b, a in zip(before["ui_library"], after["ui_library"]) if b != a]
    assert len(changed) == 1, "exactly one component changed"
    old, new = changed[0]
    assert new["ui_id"] == VILLAGE and new["style"] == "#v{color:teal}"
    assert {k: v for k, v in new.items() if k != "style"} == \
        {k: v for k, v in old.items() if k != "style"}
    assert [u["ui_id"] for u in after["ui_library"]] == [u["ui_id"] for u in before["ui_library"]]

    # And back to ordinary chat.
    _call(served, "write_graph", target="app_ui", operation="use_default", payload_json="{}")
    assert _row(tmp_path)["ui_selection"] == {"version": 1, "state": "default"}
    assert _row(tmp_path)["ui_library"] == after["ui_library"]


def test_add_replace_and_remove_touch_only_the_named_ui(served, tmp_path) -> None:
    before = _row(tmp_path)["ui_library"]
    _call(served, "write_graph", target="app_ui", operation="add_ui",
          payload_json=json.dumps({"component": _ui("den")}))
    dup = _call(served, "write_graph", target="app_ui", operation="add_ui",
                payload_json=json.dumps({"component": _ui("den", name="Other")}))
    assert dup["error"] == "app_ui_conflict", dup
    _call(served, "write_graph", target="app_ui", operation="replace_ui",
          payload_json=json.dumps({"component": _ui("den", name="Den 2")}))
    assert _row(tmp_path)["ui_library"] == [*before, _ui("den", name="Den 2")]

    _call(served, "write_graph", target="app_ui", operation="activate",
          payload_json=json.dumps({"ui_id": "den"}))
    _call(served, "write_graph", target="app_ui", operation="remove_ui",
          payload_json=json.dumps({"ui_id": "den"}))
    after = _row(tmp_path)
    assert after["ui_library"] == before
    assert after["ui_selection"] == {"version": 1, "state": "default"}, \
        "removing the active UI falls back to chat, not a dangling choice"

    missing = _call(served, "write_graph", target="app_ui", operation="activate",
                    payload_json=json.dumps({"ui_id": "nope"}))
    assert missing["error"] == "app_ui_not_found" and VILLAGE in missing["detail"]


def test_an_edit_whose_old_text_is_not_there_once_changes_nothing(served, tmp_path) -> None:
    before = _row(tmp_path)
    for old in ("absent", "x"):  # zero occurrences, many occurrences
        refused = _call(served, "write_graph", target="app_ui", operation="edit_ui",
                        payload_json=json.dumps({"ui_id": VILLAGE, "edits": [
                            {"field": "markup", "old": old, "new": "y"}]}))
        assert refused["error"] == "app_ui_validation_error", refused
    assert _row(tmp_path) == before


# --------------------------------------------------------------------------- #
# authority: the row is the caller's own, whatever is named
# --------------------------------------------------------------------------- #


@pytest.fixture
def homes(tmp_path, monkeypatch):
    from tinyassets.daemon_server import grant_universe_access, set_founder_home

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    for owner, uid in ((ALICE, HOME), (BOB, "u-bob")):
        (tmp_path / uid).mkdir()
        set_founder_home(tmp_path, founder_sub=owner, universe_id=uid, platform_generated=True)
        grant_universe_access(tmp_path, universe_id=uid, actor_id=owner, permission="admin")
    save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME, expected_revision=0,
                changes={"ui_library": [_ui(VILLAGE), _ui("den")]})
    return tmp_path


def _as(name: str):
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity

    return identity_context(Identity(user_id=name, username=name, capabilities=["write"]))


def _op(universe: str, operation: str, payload: dict) -> dict:
    from tinyassets.universe_server import write_graph

    return json.loads(write_graph(target="app_ui", operation=operation, graph_id=universe,
                                  payload_json=json.dumps(payload)))


def _rows(base) -> list[tuple[str, str]]:
    with sqlite3.connect(base / DB_FILENAME) as conn:
        return conn.execute(
            "SELECT owner_user_id, universe_id FROM universe_app_ui ORDER BY 1, 2").fetchall()


def test_another_user_cannot_activate_or_edit_your_ui(homes) -> None:
    before = _row(homes)
    with _as(BOB):
        # Naming Alice's universe: refused at the universe, nothing written.
        for operation, payload in (
            ("activate", {"ui_id": VILLAGE}),
            ("edit_ui", {"ui_id": VILLAGE, "set": {"style": "hacked"}}),
            ("remove_ui", {"ui_id": VILLAGE}),
            ("add_ui", {"component": _ui("planted")}),
            ("use_default", {}),
        ):
            refused = _op(HOME, operation, payload)
            assert refused.get("error") and refused.get("status") != "saved", refused
        # In his own universe the ids name HIS row, which has no village.
        assert _op("u-bob", "activate", {"ui_id": VILLAGE})["error"] == "app_ui_not_found"
        assert _op("u-bob", "edit_ui", {"ui_id": VILLAGE, "set": {"style": "x"}})["error"] \
            == "app_ui_not_found"
    assert _row(homes) == before
    assert _rows(homes) == [(ALICE, HOME)]


def test_a_collaborator_in_your_universe_only_reaches_their_own_row(homes) -> None:
    from tinyassets.daemon_server import grant_universe_access

    grant_universe_access(homes, universe_id=HOME, actor_id=BOB, permission="write")
    before = _row(homes)
    with _as(BOB):
        assert _op(HOME, "activate", {"ui_id": VILLAGE})["error"] == "app_ui_not_found"
        assert _op(HOME, "remove_ui", {"ui_id": VILLAGE})["error"] == "app_ui_not_found"
        assert _op(HOME, "add_ui", {"component": _ui("bobs")})["status"] == "saved"
    assert _row(homes) == before, "Alice's row untouched"
    assert get_app_ui(homes, owner_user_id=BOB, universe_id=HOME)["ui_library"] == [_ui("bobs")]


def test_the_compact_reads_are_the_callers_own(homes) -> None:
    from tinyassets.universe_server import read_graph

    with _as(BOB):
        read = json.loads(read_graph(target="app_ui", graph_id=HOME, query="index"))
        assert "app_ui" not in read and read.get("error"), read
        read = json.loads(read_graph(target="app_ui", graph_id=HOME, query=VILLAGE))
        assert "ui" not in read and read.get("error"), read
    with _as(ALICE):
        index = json.loads(read_graph(target="app_ui", graph_id=HOME, query="index"))["app_ui"]
    assert [u["ui_id"] for u in index["uis"]] == [VILLAGE, "den"]
    assert "ui_library" not in index


# --------------------------------------------------------------------------- #
# atomicity: a change never lands on top of one it did not see
# --------------------------------------------------------------------------- #


def test_a_concurrent_save_between_read_and_write_is_not_lost(tmp_path, monkeypatch) -> None:
    """Another writer saves after this change read the row and before it wrote.

    The change must re-apply to what is stored now: both the other writer's UI
    and this edit are present afterwards.
    """
    save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME, expected_revision=0,
                changes={"ui_library": [_ui(VILLAGE)]})
    real = custom_agents._apply_app_ui_entry_operation
    calls = []

    def racing(library, selection, operation, payload):
        calls.append(len(library))
        if len(calls) == 1:  # the app saves its whole library in between
            save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME, expected_revision=1,
                        changes={"ui_library": [*library, _ui("den")]})
        return real(library, selection, operation, payload)

    monkeypatch.setattr(custom_agents, "_apply_app_ui_entry_operation", racing)
    change_app_ui_entry(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                        operation="edit_ui",
                        payload={"ui_id": VILLAGE, "set": {"style": "#v{}"}})
    row = _row(tmp_path)
    assert calls == [1, 2], "the change re-read after losing the race"
    assert [u["ui_id"] for u in row["ui_library"]] == [VILLAGE, "den"], "the other save was lost"
    assert row["ui_library"][0]["style"] == "#v{}"
    assert row["revision"] == 3


def test_a_stale_etag_is_refused_and_changes_nothing(tmp_path) -> None:
    save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME, expected_revision=0,
                changes={"ui_library": [_ui(VILLAGE)]})
    etag = custom_agents.app_ui_etag(_ui(VILLAGE))
    change_app_ui_entry(tmp_path, owner_user_id=ALICE, universe_id=HOME, operation="edit_ui",
                        payload={"ui_id": VILLAGE, "set": {"name": "Renamed"}})
    before = _row(tmp_path)
    for operation, extra in (("edit_ui", {"set": {"style": "x"}}), ("remove_ui", {}),
                             ("replace_ui", {})):
        payload = {"ui_id": VILLAGE, "expected_etag": etag, **extra}
        if operation == "replace_ui":
            payload = {"component": _ui(VILLAGE), "expected_etag": etag}
        with pytest.raises(AgentConflictError):
            change_app_ui_entry(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                                operation=operation, payload=payload)
    assert _row(tmp_path) == before


def test_a_selection_change_never_rewrites_the_library(tmp_path) -> None:
    library = [_ui(VILLAGE)]
    save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME, expected_revision=0,
                changes={"ui_library": library})
    with sqlite3.connect(tmp_path / DB_FILENAME) as conn:
        stored = conn.execute("SELECT ui_library_json FROM universe_app_ui").fetchone()[0]
    change_app_ui_entry(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                        operation="activate", payload={"ui_id": VILLAGE})
    with sqlite3.connect(tmp_path / DB_FILENAME) as conn:
        assert conn.execute("SELECT ui_library_json FROM universe_app_ui").fetchone()[0] == stored


# --------------------------------------------------------------------------- #
# the harness: one brain section at a time
# --------------------------------------------------------------------------- #


def test_the_harness_reads_and_writes_one_section_past_the_ceiling(monkeypatch, tmp_path) -> None:
    """The brain is the universe's harness (read_brain / write_brain). Five full
    sections outgrow one tool result; one section never does, and a write of it
    leaves the others as they were."""
    from tests.test_engine_mcp_server import _seed_brain_universe
    from tinyassets import engine_mcp_server as engine
    from tinyassets import engine_result_bounds as bounds

    monkeypatch.delenv(bounds.CEILING_ENV, raising=False)
    monkeypatch.delenv(bounds.CONTEXT_TOKENS_ENV, raising=False)
    _seed_brain_universe(monkeypatch, tmp_path)
    sections = ("identity", "founder", "origin", "body", "orgchart")
    written = {name: f"{name}: " + "w" * 14_000 for name in sections}
    assert json.loads(engine.write_brain(**written)).get("ok") is True

    whole = asyncio.run(engine.mcp.call_tool("read_brain", {})).content[0].text
    assert json.loads(whole).get("truncated") is True, "the whole brain no longer fits"

    body = _call(engine, "read_brain", section="body")
    assert body["brain"] == {"body": written["body"]}
    assert json.loads(engine.write_brain(body=body["brain"]["body"] + "\nlearned"))["ok"]
    for name in sections:
        now = _call(engine, "read_brain", section=name)["brain"][name]
        assert now == (written[name] + "\nlearned" if name == "body" else written[name])

    unknown = json.loads(engine.read_brain(section="soul"))
    assert "unknown brain section" in unknown["error"]


# --------------------------------------------------------------------------- #
# the app shows the switch after the turn, without a reload
# --------------------------------------------------------------------------- #

TURN_SETTLED_CHECKS = r'''
(async()=>{
const u=AppUI;
appUi=stored([bundleOf()],{version:1,state:'default'});
u.enable(HOME,PRINCIPAL);
await settle();
assert.equal(u.active,null);
assert.equal(u.revision,1);

// The universe activated a UI during the turn (server row moved to revision 2).
appUi={...appUi,ui_selection:{version:1,state:'active',ui_id:'office'},revision:2};
calls=[];
await u.turnSettled(); await settle();
assert(u.active&&u.active.ui_id==='office','the activation shows after the turn');
assert.equal(u.revision,2);
assert.equal(calls[0].args.query,'index','the check is the bodiless index read');

// Nothing changed during the next turn: one index read, no remount.
const frame=u.frame; calls=[];
await u.turnSettled(); await settle();
assert.strictEqual(u.frame,frame,'an unchanged row never remounts the UI in use');
assert.equal(calls.length,1);

// Back to chat, by talking.
appUi={...appUi,ui_selection:{version:1,state:'default'},revision:3};
await u.turnSettled(); await settle();
assert.equal(u.active,null);

// The person's own Switch UI save persists. The double returns the row the way
// the store does (sorted keys), which a text compare called a mismatch: live
// 2026-10-01, "the choice was not saved (UI choice save did not match)".
await u.choose('office'); await settle();
assert.deepEqual(appUi.ui_selection,{state:'active',ui_id:'office',version:1});
assert(!/not saved/.test($('ui-status').textContent),$('ui-status').textContent);
assert.equal(u.revision,appUi.revision);
console.log('turn settled checks passed');
})().catch(err=>{console.error(err);process.exit(1);});
'''


def test_the_app_shows_an_activation_after_the_turn(tmp_path) -> None:
    from tests.test_custom_ui_bridge import _run

    out = _run(tmp_path, "custom_ui_turn_settled.js", TURN_SETTLED_CHECKS)
    assert "turn settled checks passed" in out


def test_every_finished_turn_asks_the_ui_controller() -> None:
    from pathlib import Path

    app = Path("tinyassets/onboarding/app.html").read_text(encoding="utf-8")
    delivered = app.index("renderConverse(answer);")
    settled = app.index("}catch(err)", delivered)
    assert "AppUI.turnSettled()" in app[delivered:settled]


def test_ta_build_and_activate_my_dashboard_reaches_real_owner_ui(served, tmp_path):
    from tests.ta_task_helpers import call

    before = _row(tmp_path)
    installed = call(served, "write_graph", target="app_ui", operation="add_ui",
                     payload_json=json.dumps({"component": _ui("orchard")}))
    assert installed["status"] == "saved", installed
    activated = call(served, "write_graph", target="app_ui", operation="activate",
                     payload_json=json.dumps({"ui_id": "orchard"}))
    assert activated["status"] == "saved", activated
    after = _row(tmp_path)
    assert after["revision"] == before["revision"] + 2
    assert after["ui_selection"]["ui_id"] == "orchard"
    assert after["ui_library"] == [*before["ui_library"], _ui("orchard")]
