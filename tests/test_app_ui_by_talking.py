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

from tinyassets.app_ui_store import get_app_ui, save_app_ui
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




def _rows(base) -> list[tuple[str, str]]:
    with sqlite3.connect(base / DB_FILENAME) as conn:
        return conn.execute(
            "SELECT owner_user_id, universe_id FROM universe_app_ui ORDER BY 1, 2").fetchall()








# --------------------------------------------------------------------------- #
# atomicity: a change never lands on top of one it did not see
# --------------------------------------------------------------------------- #








# --------------------------------------------------------------------------- #
# the harness: one brain section at a time
# --------------------------------------------------------------------------- #




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






