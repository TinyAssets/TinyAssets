"""The chat cloud's state: where it starts, what it remembers, where it may go.

Founder, 2026-10-02: the chat with the main agent floats over the command
center, "starts out big but gets small if and when you have a command center
or how you last had it". The shipped functions are pulled out of ``app.html``
and run under Node with storage and the DOM replaced.
"""
from __future__ import annotations

import json

from tests.test_app_browser_notifications import functions, run_js

PURE = ("cloudViewportClass", "cloudStorageKey", "cloudClamp", "cloudDefaultState",
        "cloudParseSaved", "cloudResolveState")


def _shipped_constants() -> str:
    """The page's own constants, so a changed bound is tested as shipped."""
    import re

    from tinyassets.onboarding import render_app_html

    html, _ = render_app_html()
    match = re.search(r"const CLOUD_KEY_PREFIX=.*?;", html, re.S)
    assert match, "app.html must declare the chat-cloud constants"
    return match.group(0) + ";\n"


CONSTS = _shipped_constants()

WIDE = {"w": 1280, "h": 740}
PHONE = {"w": 390, "h": 700}


def call(expr: str):
    return run_js(functions(*PURE) + CONSTS + f"console.log(JSON.stringify({expr}));")


def test_no_layout_starts_big():
    state = call(f"cloudDefaultState(false, {json.dumps(WIDE)})")

    assert state["mode"] == "open" and state["userSet"] is False
    assert state["open"] == {"x": 828, "y": 108, "w": 440, "h": 620}


def test_no_layout_on_a_phone_starts_as_the_whole_stage():
    state = call(f"cloudDefaultState(false, {json.dumps(PHONE)})")

    assert state["open"] == {"x": 0, "y": 0, "w": 390, "h": 700}


def test_a_layout_starts_small_in_the_corner():
    state = call(f"cloudDefaultState(true, {json.dumps(WIDE)})")

    assert state["mode"] == "bubble"
    assert state["bubble"] == {"x": 1280 - 56 - 12, "y": 740 - 56 - 12}
    # Expanding it opens the same medium rectangle as without a layout.
    assert state["open"] == {"x": 828, "y": 108, "w": 440, "h": 620}
    assert call("cloudDefaultState(true, {w:390,h:700})")["open"] == {
        "x": 0, "y": 280, "w": 390, "h": 420}


def test_the_owners_last_state_wins_over_either_default():
    saved = {"v": 1, "mode": "open", "open": {"x": 300, "y": 40, "w": 500, "h": 400},
             "bubble": {"x": 20, "y": 30}}
    raw = json.dumps(json.dumps(saved))
    for has_layout in ("true", "false"):
        state = call(f"cloudResolveState(cloudParseSaved({raw}), {has_layout}, {json.dumps(WIDE)})")
        assert state["mode"] == "open" and state["userSet"] is True
        assert state["open"] == saved["open"] and state["bubble"] == saved["bubble"]


def test_an_unreadable_saved_state_falls_back_to_the_default():
    for raw in ("not json", json.dumps({"v": 2}),
                json.dumps({"v": 1, "mode": "huge", "open": {}, "bubble": {}}),
                json.dumps({"v": 1, "mode": "open",
                            "open": {"x": "1", "y": 0, "w": 9, "h": 9},
                            "bubble": {"x": 0, "y": 0}})):
        assert call(f"cloudParseSaved({json.dumps(raw)})") is None
    state = call(f"cloudResolveState(null, true, {json.dumps(WIDE)})")
    assert state["mode"] == "bubble"


def test_clamping_keeps_it_wholly_on_the_stage():
    off = {"mode": "open", "open": {"x": 5000, "y": -300, "w": 9000, "h": 50},
           "bubble": {"x": -80, "y": 9000}, "userSet": True}
    state = call(f"cloudClamp({json.dumps(off)}, {json.dumps(WIDE)})")

    assert state["open"] == {"x": 0, "y": 0, "w": 1280, "h": 360}   # min height, full width
    assert state["bubble"] == {"x": 0, "y": 740 - 56}
    moved = {**off, "open": {"x": 1200, "y": 700, "w": 400, "h": 400}}
    state = call(f"cloudClamp({json.dumps(moved)}, {json.dumps(WIDE)})")
    assert state["open"] == {"x": 880, "y": 340, "w": 400, "h": 400}


def test_a_stage_smaller_than_the_minimum_gets_the_whole_stage():
    tiny = {"w": 200, "h": 150}
    state = call(f"cloudDefaultState(false, {json.dumps(tiny)})")

    assert state["open"]["w"] == 200 and state["open"]["h"] == 150
    assert state["open"]["x"] == 0 and state["open"]["y"] == 0


def test_saved_per_owner_agent_and_viewport_class():
    keys = call("""[cloudStorageKey('alice','main',cloudViewportClass(1280)),
                    cloudStorageKey('alice','main',cloudViewportClass(390)),
                    cloudStorageKey('alice','scout','wide'),
                    cloudStorageKey('bob','main','wide'),
                    cloudStorageKey('alice','','wide')]""")

    assert keys[0] == "app.chatCloud.v1:alice:main:wide"
    assert keys[1] == "app.chatCloud.v1:alice:main:phone"
    assert len(set(keys[:4])) == 4
    assert keys[4] == keys[0]          # no agent id means the main agent


# --- the controller, with a fake DOM ----------------------------------------

CONTROLLER = PURE + ("cloudStageSize", "cloudHasLayout", "refreshChatCloud", "applyChatCloud",
                     "saveChatCloud", "setChatCloudMode", "cloudShifted", "cloudKeydown",
                     "setCloudUnread", "paintCloudBubbleLabel")

DOM = """
let cloudState=null, cloudStoreKey="", cloudWired=true, cloudSuppressClick=false;
let queueOwner='alice', stage={clientWidth:1280, clientHeight:740}, layout=false;
const store={}, localStorage={getItem:k=>k in store?store[k]:null,
  setItem:(k,v)=>{store[k]=String(v);}};
function el(){ return {hidden:false, style:{}, dataset:{}, attrs:{}, focused:false,
  classList:{set:new Set(), add(c){this.set.add(c)}, remove(c){this.set.delete(c)},
             contains(c){return this.set.has(c)},
             toggle(c,on){on?this.set.add(c):this.set.delete(c)}},
  setAttribute(k,v){this.attrs[k]=v}, focus(){this.focused=true; document.activeElement=this},
  contains(node){return node===this.child}}; }
const nodes={'chat-stage':stage, 'chat-cloud':el(), 'chat-cloud-bubble':el(),
             'btn-cloud-shrink':el(),
             'chat-cloud-badge':el(), 'composer-input':el(),
             'view-chat':{classList:{contains:c=>c==='ui-custom-active'&&layout}}};
nodes['chat-cloud'].dataset.agent='main';
const $=id=>nodes[id];
const document={activeElement:null};
const snap=()=>({state:cloudState, store, mode:nodes['chat-cloud'].dataset.mode,
  bubbleHidden:nodes['chat-cloud-bubble'].hidden, left:nodes['chat-cloud'].style.left});
"""


def controller(body: str):
    return run_js(functions(*CONTROLLER) + CONSTS + DOM + body)


def test_shrinking_is_remembered_and_restored_on_the_next_load():
    out = controller("""
refreshChatCloud(); const first=snap().mode;
setChatCloudMode('bubble');
cloudState=null; refreshChatCloud();
console.log(JSON.stringify({first, after:snap()}));""")

    assert out["first"] == "open"
    assert out["after"]["mode"] == "bubble" and out["after"]["bubbleHidden"] is False
    assert out["after"]["state"]["userSet"] is True
    assert list(out["after"]["store"]) == ["app.chatCloud.v1:alice:main:wide"]


def test_a_phone_does_not_inherit_the_desktop_window():
    out = controller("""
refreshChatCloud(); setChatCloudMode('bubble');
stage.clientWidth=390; stage.clientHeight=700; refreshChatCloud();
console.log(JSON.stringify(snap()));""")

    assert out["mode"] == "open"                     # the phone's own default
    assert out["state"]["open"]["w"] == 390


def test_a_layout_arriving_shrinks_an_untouched_cloud_but_not_a_placed_one():
    out = controller("""
refreshChatCloud(); layout=true; refreshChatCloud(); const untouched=snap().mode;
layout=false; refreshChatCloud();
cloudKeydown({key:'ArrowLeft', shiftKey:true, preventDefault(){}});   // the owner resizes it
layout=true; refreshChatCloud();
console.log(JSON.stringify({untouched, placed:snap().mode}));""")

    assert out == {"untouched": "bubble", "placed": "open"}


def test_keyboard_moves_and_resizes_within_the_stage():
    out = controller("""
stage.clientWidth=1280; refreshChatCloud();
cloudState=cloudClamp({mode:'open',open:{x:100,y:100,w:400,h:400},bubble:{x:0,y:0}},
                      {w:1280,h:740});
cloudKeydown({key:'ArrowRight', shiftKey:false, preventDefault(){}});
const moved=Object.assign({}, cloudState.open);
cloudKeydown({key:'ArrowDown', shiftKey:true, preventDefault(){}});
for(let i=0;i<200;i++) cloudKeydown({key:'ArrowUp', shiftKey:false, preventDefault(){}});
console.log(JSON.stringify({moved, after:cloudState.open}));""")

    assert out["moved"] == {"x": 116, "y": 100, "w": 400, "h": 400}
    assert out["after"] == {"x": 116, "y": 0, "w": 400, "h": 416}


def test_a_placed_cloud_survives_a_refresh_even_when_storage_is_off():
    """Storage can refuse writes; the owner's placement must still hold until
    the page goes away, rather than snapping back on the next resize."""
    out = controller("""
localStorage.setItem=()=>{ throw new Error('quota'); };
refreshChatCloud();
cloudKeydown({key:'ArrowLeft', shiftKey:true, preventDefault(){}});
layout=true; refreshChatCloud();
console.log(JSON.stringify({mode:snap().mode, w:cloudState.open.w}));""")

    assert out["mode"] == "open"
    assert out["w"] == 440 - 16
