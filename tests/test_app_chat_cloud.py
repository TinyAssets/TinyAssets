"""The chat cloud's state: where it starts, what it remembers, where it may go.

Founder, 2026-10-02: the chat with the main agent floats over the command
center, "starts out big but gets small if and when you have a command center
or how you last had it". The shipped functions are pulled out of ``app.html``
and run under Node with storage and the DOM replaced.
"""
from __future__ import annotations

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


















# --- the controller, with a fake DOM ----------------------------------------

CONTROLLER = PURE + ("cloudStageSize", "cloudHasLayout", "refreshChatCloud", "applyChatCloud",
                     "saveChatCloud", "setChatCloudMode", "cloudShifted", "cloudKeydown",
                     "setCloudUnread", "paintCloudBubbleLabel", "cloudRecord",
                     "markChatCloudGesture")

DOM = """
let cloudState=null, cloudStoreKey="", cloudWired=true, cloudSuppressClick=false;
const MCP={_loginEpoch:1};
const cloudPrefs={epoch:0,scope:'',synced:'',gestured:false,gestures:new Set(),
  cancelHydration:null,writeTail:Promise.resolve()};
let queueScope='home-alice';
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
const document={activeElement:null,events:{},
  addEventListener(k,v){this.events[k]=v;},removeEventListener(k){delete this.events[k];}};
const snap=()=>({state:cloudState, store, mode:nodes['chat-cloud'].dataset.mode,
  bubbleHidden:nodes['chat-cloud-bubble'].hidden, left:nodes['chat-cloud'].style.left});
"""


def controller(body: str):
    return run_js(functions(*CONTROLLER) + CONSTS + DOM + body)










# --- the owner's record on the server (openspec/changes/owner-ui-prefs) -----

SYNC = CONTROLLER + ("syncChatCloudFromServer", "postChatCloud", "cloudPrefsQuery", "cloudSession",
                     "isTypingTarget")

NET = """
let cloudSynced="", cloudGestured=false, answer=null, failRead=false, onRefresh=null;
const posts=[], reads=[];
const ensureFreshToken=async()=>{ if(onRefresh) onRefresh(); };
const authHeaders=()=>({Authorization:'Bearer '+queueOwner});
const fetch=async(path,opts)=>{
  if(opts&&opts.method==='POST'){ posts.push(Object.assign(JSON.parse(opts.body),
      {bearer:opts.headers.Authorization}));
    return {ok:true,json:async()=>({saved:true})}; }
  reads.push(path);
  if(failRead) throw new Error('offline');
  return {ok:true, json:async()=>(typeof answer==='function' ? answer(opts) : answer)};
};
const settle=()=>new Promise(r=>setTimeout(r,10));
const SERVER={v:1,mode:'open',open:{x:300,y:120,w:500,h:400},bubble:{x:10,y:10}};
"""


def sync(body: str):
    return run_js(functions(*SYNC) + CONSTS + DOM + NET + "(async()=>{" + body + "})();")
