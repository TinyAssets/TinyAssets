"""The primary connect path stands alone until the user asks for the others.

Live 2026-09-26, unpowered free account: the "Connect the model your universe runs
on" ask rendered with "Other ways to connect" already OPEN, so a first-time user met
the one-tap primary button AND an API key / Model URL / Model id form at the same
time — three ways to do one thing, on the screen that decides whether they get a
working universe at all.

The cause was not the markup (``<details id="connect-other">`` ships closed). The
setup panel node is PARKED and reused across every rail refresh, and ``render`` only
ever assigned ``open = true``: once anything had opened it, nothing closed it again.

So the element's state is now DERIVED from a remembered user intent on every render.
Executes the REAL renderer sliced out of the shipped ``app.html`` — only the DOM and
the transport are synthetic.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from tinyassets.onboarding import render_app_html

_SLICE_START = "  // ---- Pending-request rail ---"
#: Stops before the endpoint-connect call, which needs the MCP transport. Everything
#: this test drives -- the rail renderer, `connectBody`, `ConnectShapes` and
#: `forgetFinishedSetup` -- is inside the slice.
_SLICE_END = "  // A declared model list needs a context size"

#: A DOM that records exactly what the renderer set, and nothing else — plus the one
#: browser behaviour this code depends on: `open` is an ACCESSOR that fires a QUEUED
#: `toggle` on every change, programmatic assignment included. A plain property here
#: made the suite blind to the renderer's own toggles, which is how the review of
#: PR #4002 found the renderer recording its own open as the user's intent.
HARNESS = r"""
const elements=new Map();
function node(tag){
  const n={tag,children:[],attrs:{},textContent:'',id:'',className:'',type:'',
    hidden:false,disabled:false,dataset:{},listeners:{},
    replaceChildren(){this.children=[];},
    showModal(){this.open=true;},close(){this.open=false;},
    remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(c=>c!==this);},
    querySelectorAll(){return [];},
    append(...k){this.children.push(...k);},
    appendChild(c){
      if(c.parentNode)c.parentNode.children=c.parentNode.children.filter(x=>x!==c);
      c.parentNode=this;this.children.push(c);return c;},
    setAttribute(k,v){this.attrs[k]=String(v);},
    getAttribute(k){return Object.prototype.hasOwnProperty.call(this.attrs,k)?this.attrs[k]:null;},
    addEventListener(e,h){this.listeners[e]=h;},
    closest(){return null;},
    classList:{toggle(){},add(){},remove(){}},
  };
  let open=false;
  Object.defineProperty(n,'open',{
    get(){return open;},
    set(value){
      const next=!!value;
      if(next===open) return;          // no change, no event, as in a browser
      open=next;
      queueMicrotask(()=>{ if(n.listeners.toggle) n.listeners.toggle(); });
    },
    enumerable:true,
  });
  return n;
}
//: Let every queued toggle run, the way the browser's task queue would between
//: renders. Every assertion is made after this.
const settle=()=>new Promise(r=>setTimeout(r,0));
const $=id=>{if(!elements.has(id)){const e=node(id);e.id=id;elements.set(id,e);}
  return elements.get(id);};
const document={createElement:node,addEventListener(){}};
const window={};
const location={search:'',href:'https://tinyassets.io/app',pathname:'/app'};
const HostedModelConnect={adopt(){},configure(){},setup:null};
// The real ids the page uses, declared above the slice.
const CONNECT_REQUEST_ID='sys_connect_llm';
function foldedModelAccess(){return null;}
function railBody(){return node('body');}
function autoGrow(){}
function clearTypedValues(node){node.value="";for(const c of node.children)clearTypedValues(c);}
__SOURCE__
// Tap the disclosure the way a user does: the browser flips `open` and the queued
// `toggle` follows on its own. Awaited, so the listener has actually run.
async function tapOther(){
  const d=$('connect-other');
  d.open=!d.open;
  await settle();
}
function ask(over){
  return Object.assign({
    request_id:CONNECT_REQUEST_ID, kind:'Setup', title:'Connect the model your universe runs on',
    body:'Your universe has no model yet.', sticky:true,
    // `type:'connect'` + a `setup` block is what isSetupRequest recognises. Without
    // the type it is an ordinary rail row, the setup panel is never rendered, and
    // every assertion below would pass for the wrong reason.
    action:{type:'connect',
      setup:{primary:{provider:'a-source',label:'Continue'},shapes:['api_key','local']}},
  }, over||{});
}
(async()=>{
__STEPS__
await settle();
console.log(JSON.stringify({open:$('connect-other').open,
  hidden:$('connect-other').hidden, remembered:connectOtherOpen}));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""


def _slice() -> str:
    html, _ = render_app_html()
    return html[html.index(_SLICE_START) : html.index(_SLICE_END)]


def run(steps: str) -> dict:
    node = shutil.which("node")
    if not node:  # pragma: no cover - the shipped page is JavaScript
        pytest.skip("node is required to execute the shipped renderer")
    program = HARNESS.replace("__SOURCE__", _slice()).replace("__STEPS__", steps)
    # A file, not `node -e`: the slice outgrew Windows' 32K command line
    # (WinError 206). Written outside the repo, like every other harness.
    import tempfile

    with tempfile.TemporaryDirectory() as scratch:
        script = os.path.join(scratch, "connect_disclosure.cjs")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(program)
        result = subprocess.run(
            [node, script],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


STICKY = "renderRail([ask()]);"


def test_the_primary_path_stands_alone_on_a_first_render():
    """The regression: an unpowered account met both paths at once."""
    state = run(STICKY)
    assert state["open"] is False
    assert state["hidden"] is False  # discoverable, just not unfolded


def test_it_opens_on_the_users_tap():
    state = run(STICKY + " await tapOther();")
    assert state["open"] is True
    assert state["remembered"] is True


def test_a_rail_refresh_does_not_fold_the_users_tap_back_shut():
    """The failure mode of a naive fix: closing it on every poll."""
    state = run(STICKY + " await tapOther(); renderRail([ask()]); renderRail([ask()]);")
    assert state["open"] is True


def test_a_stale_open_on_the_parked_node_is_not_inherited():
    """The actual cause. The node survives refreshes; its state must not."""
    state = run("$('connect-other').open=true; renderRail([ask()]);")
    assert state["open"] is False
    assert state["remembered"] is False


def test_the_renderers_own_open_is_not_recorded_as_the_users_intent():
    """The reviewer's probe, PR #4002 round 1 (DISAGREE_CONCERN).

    A real <details> fires `toggle` for a PROGRAMMATIC assignment too, and fires it
    queued. So the listener was recording the renderer's legitimate open — the
    no-primary case — as the user's intent, and every later sticky-with-primary card
    came up unfolded. That is the founder's exact first-time path.
    """
    no_primary = "renderRail([ask({action:{type:'connect',setup:{shapes:['api_key']}}})]); "
    opened = run(no_primary)
    assert opened["open"] is True, "the no-primary card must show the fields"
    assert opened["remembered"] is False, "the renderer's own open was taken as a tap"
    # Now the same conversation gets a card that HAS a primary path.
    state = run(no_primary + "await settle(); " + STICKY)
    assert state["open"] is False
    assert state["remembered"] is False


#: The user on an OPTIONAL row, which the renderer opens for them: they fold it, then
#: unfold it again. The second tap is a genuine user open, so intent is remembered.
OPTIONAL_THEN_REOPENED = (
    "railOpen=CONNECT_REQUEST_ID; renderRail([ask({sticky:false})]); "
    "await tapOther(); await tapOther(); "
)


def test_a_reopen_after_a_close_is_still_the_users_intent():
    """Re-baselining matters: without it the second tap looked like our own echo."""
    state = run(OPTIONAL_THEN_REOPENED)
    assert state["open"] is True
    assert state["remembered"] is True


def test_losing_your_model_again_folds_the_card_back():
    """The live path in the same finding: optional -> sticky must also reset.

    The user opened "Other ways to connect" on the optional "Connect another LLM"
    row; later they disconnect and the card becomes a first-time precondition again.
    With only the blocking -> optional reset, their hours-old tap served the unfolded
    manual form to the naive-user path.
    """
    state = run(OPTIONAL_THEN_REOPENED + "renderRail([ask()]);")
    assert state["open"] is False
    assert state["remembered"] is False


def test_with_no_primary_path_the_fields_are_the_path_and_stay_open():
    """Never hide the only way through: no one-tap source means these fields ARE it."""
    no_primary = "renderRail([ask({action:{type:'connect',setup:{shapes:['api_key']}}})]);"
    assert run(no_primary)["open"] is True


def test_an_optional_row_the_user_opened_shows_the_fields():
    """A non-sticky 'connect another' row is the user asking for exactly this."""
    optional = "railOpen=CONNECT_REQUEST_ID; renderRail([ask({sticky:false})]);"
    assert run(optional)["open"] is True


def test_finishing_setup_forgets_the_tap():
    """Same transition the connected-optional collapse uses: blocking -> optional."""
    state = run(
        "renderRail([ask()]); await tapOther();"
        " renderRail([ask({sticky:false})]);"
    )
    assert state["open"] is False
    assert state["remembered"] is False


