"""The working indicator is the UNIVERSE's state, and a queued line is ordered last.

Live, 2026-09-26. The founder clicked Accept on a rail card while an earlier turn
was still running. The turn journal shows turn ``d1a01eec`` running 20:18:23Z to
about 20:22Z. Two things were wrong on screen for those four minutes:

1. No working indicator at all in the founder's view, so it read as if nothing
   had happened. The indicator was a fact about the PAGE -- ``sendTurn`` set a
   0.78rem grey line under the composer and its ``finally`` cleared it, and
   nothing else ever set it. A turn started by answering a request, by a queued
   line, from another window or device, or one still running across a reload
   showed nothing.
2. The queued "Approved: ..." bubble was appended at click time, so the earlier
   turn's reply -- composed BEFORE that click arrived -- landed underneath it.
   The thread read as though the agent had ignored the approval.

Everything here EXECUTES the page's own ``appendMessage``, ``sendTurn``,
``queueTurn``, ``flushSendQueue``, ``pollStatus``, ``loadHistory``,
``readServerTurn`` and ``renderWorking`` under node. The DOM shim is a real tree
(``parentNode``, ``insertBefore``, a ``remove`` that detaches) because thread
ORDER is the thing under test: a shim whose appendChild is the only insertion
would pass whatever the page did.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from functools import lru_cache

import pytest

from tests.test_onboarding_app import _js_function
from tinyassets import onboarding

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    _NODE is None, reason="node is required to execute the page's own source")

# Declarations the extracted functions close over. Optional ones are matched
# with `re.search` and skipped when absent, so a tree without the fix is RED on
# an assertion rather than on a missing declaration.
_DECLS = (
    r"const INFLIGHT_KEY=[^\n]*;", r"let turnStartedAt=[^\n]*;", r"let activeTurn=[^\n]*;",
    r"let liveInflight=[^\n]*;",
    r"let historyLoaded = [^\n]*;", r"let inflightRestored = [^\n]*;",
    r"const sendQueue=[^\n]*;", r"let sendQueueHeld=[^\n]*;", r"const SEND_QUEUE_MAX=[^\n]*;",
    r"const QUEUE_KEY=[^\n]*;", r"let queueRestored=[^\n]*;", r"const QUEUE_MAX_AGE_MS=[^\n]*;",
    r"let queueScope=[^\n]*;", r"let queueOwner=[^\n]*;", r"let queuePersisted=[^\n]*;",
    r"let retainedItems=[^\n]*;", r"let modelChoiceForNextTurn=[^\n]*;",
    r"const renderedConsumerTurns=[^\n]*;", r"const renderedConsumerFounders=[^\n]*;",
    r"let Uploads=[^\n]*;", r"let uploadsRestored=[^\n]*;",
    # `hasMessages` shares its declaration line with the two timer handles, which
    # the shim owns (it records timers instead of arming them), so it is declared
    # there rather than lifted from the page.
    # Introduced by the fix.
    r"const TURN_WORKING_STATES=[^\n]*;", r"let serverTurn=[^\n]*;",
    r"let deployPending=[^\n]*;",
    r"const STATUS_IDLE_MS=[^\n]*;", r"let statusBeatMs=[^\n]*;",
    r"let serverStatusLine=[^\n]*;",
    # Which step and model the turn waits on (turn-wait-visibility).
    r"let ownDetailLine=[^\n]*;", r"const TRY_MODEL_AFTER_MIN=[^\n]*;",
    # The Stop control's state (sendTurn's cleanup reads it).
    r"let interruptRequested=[^\n]*;", r"const STOP_REQUEST_MS=[^\n]*;",
    # The seat wait line (`universe_seats`).
    r"let seatWait=[^\n]*;", r"let seatLineShown=[^\n]*;",
    r"let PLAN = null;",
    # Lines steered into a running turn (harness S2).
    r"let steeredLines=[^\n]*;", r"let pendingSteers=[^\n]*;", r"let watchedActive=[^\n]*;",
    # The tool-activity suffix on the status line (harness S4).
    r"const TOOL_SEP=[^\n]*;", r"const THINKING_LINE=[^\n]*;",
)
_FUNCS = (
    "formatMessageTimestamp", "appendMessage", "setStatusLine",
    "turnInputMethod", "rememberInflight", "forgetInflight", "readInflight", "renderConverse",
    "sameInflight", "forgetInflightIf",
    "copyModelChoice", "captureTurnOptions", "sendConversationRequest",
    "executionLabel", "answerExecutionDetail", "servedFailureError", "appendFailureNotice",
    "offerResend", "noteHeldQueue", "offerSavedConversationCheck", "savedSendIndex",
    "sendTurn", "loadHistory",
    "drawHistoryTurns", "offerEarlier", "loadEarlier", "historyFailed",
    "restoreInflight", "pollStatus",
    "messageBody", "expansionHandle", "offerFullMessage", "loadFullMessage",
    "setQueueScope", "setQueueOwner", "ownsSavedRow",
    "flushSendQueue", "queueTurn", "saveQueue", "readSavedQueue", "stillSaved",
    "forgetSavedItem", "savedItem", "sameSavedLine", "restoreQueue", "claimedElsewhere",
    "offerSavedLine", "clearComposerState", "clearThread",
)
# The fix's own functions. Optional for the same reason the declarations are: a
# tree without them must fail on what the screen shows, not on an extraction.
_NEW_FUNCS = ("isQueuedBubble", "firstQueuedBubble", "markQueued", "unmarkQueued",
              "readServerTurn", "serverTurnLive", "workingSince", "workingElapsed",
              "renderWorking", "pulseHeartbeat", "canPurchase",
              "renderStop", "takeInterruptFlush", "flushAfterTurn",
              "drainAfterStop", "takeBatch", "flushBatch",
              "markSteered", "unmarkSteered", "steerOrQueue", "settleSteered",
              "adoptSteered", "markHeld", "restoreHeldSteers", "readServerTurnRow",
              "claimHeldLines", "pinLineAgent", "alreadyHandled", "showActiveTurn",
              "finishActiveTurn",
              "readPendingTurns", "sendBatch", "shortModelName", "waitMinutes", "waitDetail",
              "renderTryModel", "toolLine", "paintToolLine")

# A real tree. `insertBefore` and a detaching `remove` are the point: thread
# order is what the ordering half of this bug is about.
_SHIM = r"""
const InlineConnection={waiting:()=>false,hold:()=>{
  throw new Error('Unexpected connection request in the working-indicator harness');
}};
function refreshRail(){}
const store={};
const localStorage={ getItem:k=>(k in store?store[k]:null),
  setItem:(k,v)=>{store[k]=String(v);}, removeItem:k=>{delete store[k];} };
class El{
  constructor(tag){
    this.tagName=String(tag).toUpperCase(); this.children=[]; this.parentNode=null;
    this.className=""; this.textContent=""; this.value=""; this.style={};
    this.hidden=false; this.disabled=false; this.listeners={};
    this.scrollTop=0; this.scrollHeight=0;
  }
  appendChild(c){ this.detach(c); this.children.push(c); c.parentNode=this; return c; }
  insertBefore(c,ref){
    this.detach(c);
    const at=this.children.indexOf(ref);
    if(at<0) throw new Error("insertBefore reference is not a child");
    this.children.splice(at,0,c); c.parentNode=this; return c;
  }
  detach(c){ if(c&&c.parentNode){ c.parentNode.children=c.parentNode.children.filter(n=>n!==c);
    c.parentNode=null; } }
  remove(){ this.removed=true; if(this.parentNode) this.parentNode.detach(this); }
  setAttribute(){} addEventListener(n,f){ this.listeners[n]=f; }
  click(){ return (this.listeners.click||(()=>{}))(); }
}
const document={ createElement:t=>new El(t),
  createTextNode:t=>{ const e=new El("#text"); e.textContent=t; return e; },
  activeElement:null };
const els={};
for(const id of ["thread","thread-empty","status-line","deploy-pending-line",
                 "composer-input","btn-send","dot","universe-name"])
  els[id]=new El("div");
const $=id=>els[id];

// A clock the scenario can move, so "this page has not refreshed the row in
// three working-rate polls" is testable without sleeping through it.
const REAL_NOW=Date.now.bind(Date);
let clockSkew=0;
Date.now=()=>REAL_NOW()+clockSkew;

const Voice={isActive:()=>false,conversationSettled:()=>{},turnStarted:()=>{},stop:()=>{}};
const ModelPicker={observe(){},reset(){},snapshot:null};
function autoGrow(el){ if(el&&el.style) el.style.height="auto"; }
function sessionExpired(){ events.push("session-expired"); }
function enterSignedOut(){ events.push("signed-out"); }
function showConnect(){ events.push("connect"); }
function openConnectRequest(){ events.push("connect-request"); }
async function healServing(){}
function restoreUploadRecords(){}
function clearAccountScopedState(){}
const events=[];
const token=()=>"t";
const SCENARIO=__SCENARIO__;

// Timers: recorded, never armed. An armed interval would keep node alive and
// the scenario's own assertions are what advance time here.
const timers={set:[],cleared:[]};
let timerSeq=0;
function setInterval(fn,ms){ timers.set.push(ms); return ++timerSeq; }
function clearInterval(h){ timers.cleared.push(h); }
// The heartbeat is running, as it is on a live page.
let hasMessages=false, statusTimer=7, workingTimer=null;

// Pre-fix stand-ins, so a tree WITHOUT the fix fails on what the screen shows
// rather than on a ReferenceError. The page's own definitions are appended after
// this shim and a later function declaration is the one that runs, so on a tree
// WITH the fix none of these is ever called.
function readServerTurn(){}
function serverTurnLive(){ return false; }
function renderWorking(){}
function pulseHeartbeat(){}
function markQueued(el){ return el; }
function unmarkQueued(){}
function firstQueuedBubble(){ return null; }

const converseCalls=[], statusPolls=[];
const gates=[];
// The owner door (reads). This harness has ONE fake server, `MCP` below, so
// the owner door's reads are answered by it: a read the page makes is
// recorded and stubbed exactly where the scenario already records it.
const Owner={
  read(a){return MCP.callTool("read_graph",a,{idempotent:true});},
  status(a){return MCP.callTool("get_status",a||{},{idempotent:true});},
  getStatus(...x){return MCP.getStatus(...x);},
  getConversation(...x){return MCP.getConversation(...x);},
  readConversationChunk(...x){return MCP.readConversationChunk(...x);},
  getModelOptions(...x){return MCP.getModelOptions(...x);},
  listRequests(...x){return MCP.listRequests(...x);}};
const MCP={ _loginEpoch:0, invalidateSession(){},
  converse:async(m,inputMethod,modelChoice,consumerRequest)=>{
    converseCalls.push(m);
    return await new Promise((resolve,reject)=>gates.push({resolve,reject,m}));
  },
  callTool:async()=>({}),
  async getStatus(){
    statusPolls.push(1);
    if(SCENARIO.statusError){ const e=new Error("status down"); e.transport=true; throw e; }
    const s={active_host:"h",universe_id:SCENARIO.universe||"u-1",universe_name:"Home"};
    if(Object.prototype.hasOwnProperty.call(SCENARIO,"activeTurn"))
      s.active_turn=SCENARIO.activeTurn;
    if(Object.prototype.hasOwnProperty.call(SCENARIO,"seats")) s.seats=SCENARIO.seats;
    if(Object.prototype.hasOwnProperty.call(SCENARIO,"deployPending"))
      s.deploy_pending=SCENARIO.deployPending;
    return s;
  },
  async getConversation(){
    const r={universe_id:SCENARIO.universe||"u-1",
             recent_conversation:{turns:SCENARIO.history||[]}};
    if(Object.prototype.hasOwnProperty.call(SCENARIO,"historyActiveTurn"))
      r.active_turn=SCENARIO.historyActiveTurn;
    return r;
  },
};
const settle=()=>new Promise(r=>setTimeout(r,20));
function bubbles(){
  // Thread order, top to bottom, with what each bubble says about itself.
  return els.thread.children.map(n=>({
    cls:String(n.className||""),
    queued:String(n.className||"").split(" ").indexOf("msg--queued")>=0,
    text:(n.children.filter(c=>c.className==="msg-body").map(c=>c.textContent)[0])||"",
    note:(n.children.filter(c=>c.className==="msg-queued-note")
          .map(c=>c.textContent).join("")),
  }));
}
// THE one indicator: the original status line under the composer. There is no
// second element to check, by founder instruction -- so "shown" is a non-empty
// line and "hidden" is an empty one, read off the same element every other status
// message uses.
function indicator(){
  const line=els["status-line"].textContent||"";
  return {shown:line!=="", line:line};
}
"""

_TAIL = "\n})().catch(e=>{ console.error(e&&e.stack||e); process.exit(1); });"


@lru_cache(maxsize=1)
def _source(html: str) -> str:
    """Extract unchanged shipped functions once, without caching scenario state."""
    decls = [found.group(0) for found in
             (re.search(pat, html) for pat in _DECLS) if found]
    funcs = [_js_function(html, name) for name in _FUNCS]
    for name in _NEW_FUNCS:
        if re.search(r"function\s+" + name + r"\s*\(", html):
            funcs.append(_js_function(html, name))
    return "\n".join(decls) + "\n" + "\n".join(funcs)


def _program(html: str, scenario: dict, body: str) -> str:
    return (_SHIM.replace("__SCENARIO__", json.dumps(scenario)) +
            _source(html) + "\n(async()=>{\n" + body + _TAIL)


def _run(tmp_path, html: str, scenario: dict, body: str) -> dict:
    script = tmp_path / "working_indicator_case.js"
    script.write_text(_program(html, scenario, body), encoding="utf-8")
    proc = subprocess.run([_NODE, str(script)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60, env=dict(os.environ))
    assert proc.returncode == 0, f"working-indicator harness crashed:\n{proc.stderr}"
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def html() -> str:
    page, _csp = onboarding.render_app_html()
    yield page
    _source.cache_clear()


# ---------------------------------------------------------------------------
# The indicator reflects the SERVER's turn state, whatever started the turn.
# ---------------------------------------------------------------------------

_SERVER_TURN = r"""
setQueueOwner("p-1");
const before=indicator();
await pollStatus();
const after=indicator();
// `typeof` so a tree without the fix reports a null beat and fails on the
// assertion, instead of crashing on an undeclared identifier.
console.log(JSON.stringify({before, after,
  beat:(typeof statusBeatMs==="undefined"?null:statusBeatMs),
  armed:timers.set, polls:statusPolls.length}));
"""








_RELOAD = r"""
setQueueOwner("p-1");
await loadHistory();
console.log(JSON.stringify({indicator:indicator(), bubbles:bubbles()}));
"""




_STALLED_POLL = r"""
setQueueOwner("p-1");
await pollStatus();                       // the row is observed here
const observed=indicator();
SCENARIO.statusError=true;
await pollStatus();                       // ...and the connection drops
const afterFailure=indicator();
clockSkew=31000;                          // three working-rate polls later
renderWorking();
const abandoned=indicator();
console.log(JSON.stringify({observed, afterFailure, abandoned}));
"""




_LOCAL_ONLY = r"""
setQueueOwner("p-1");
const turn=sendTurn("what is the status of the run?");
await settle();
const during=indicator();
gates[0].resolve({reply:"Running."});
await turn; await settle();
const after=indicator();
console.log(JSON.stringify({during, after, served:!!SCENARIO.activeTurn}));
"""




_ACCOUNT_BOUNDARY = r"""
setQueueOwner("p-1");
await pollStatus();
const signedIn=indicator();
clearComposerState();
const signedOut=indicator();
console.log(JSON.stringify({signedIn, signedOut}));
"""




# ---------------------------------------------------------------------------
# A queued answer is ordered where the agent will actually read it.
# ---------------------------------------------------------------------------

_QUEUED_ORDER = r"""
setQueueOwner("p-1");
const first=sendTurn("summarise the run so far");
await settle();
// The founder clicks Accept on a rail card while that turn is still running.
// answerRail relays the click through the SAME send path as any message.
sendTurn('Approved: "grant read access to the notes wiki"', undefined,
  {relay:true, keepComposer:true, inputMethod:"app_action"});
const whileQueued=bubbles();
const queuedIndicator=indicator();
// ...and only now does the earlier turn's reply come back.
gates[0].resolve({reply:"Here is the summary of the run so far."});
await first; await settle();
const afterReply=bubbles();
gates[1].resolve({reply:"Access granted; starting."});
await settle();
console.log(JSON.stringify({whileQueued, queuedIndicator, afterReply, final:bubbles(),
  sent:converseCalls}));
"""




_TWO_QUEUED = r"""
setQueueOwner("p-1");
const first=sendTurn("kick off the run");
await settle();
sendTurn("first answer", undefined, {relay:true, keepComposer:true});
sendTurn("second answer", undefined, {relay:true, keepComposer:true});
const queued=bubbles();
gates[0].resolve({reply:"kicked off"});
await first; await settle();
console.log(JSON.stringify({queued, afterReply:bubbles()}));
"""




# ---------------------------------------------------------------------------
# Exactly ONE indicator on screen. Founder, 2026-09-26: "there are now 2
# indicators at once that my command center is thinking, i preferred only the original
# one at the bottom."
# ---------------------------------------------------------------------------




_UPDATE_LINE = r"""
setQueueOwner("p-1");
await pollStatus();
renderWorking();                         // repeated renders still use one line
const line=els["deploy-pending-line"];
const before={text:line.textContent,hidden:line.hidden};
if(SCENARIO.endTurn) SCENARIO.activeTurn=null;
if(SCENARIO.clearPending) SCENARIO.deployPending={pending:false};
if(SCENARIO.omitPending) delete SCENARIO.deployPending;
await pollStatus();
console.log(JSON.stringify({before,after:{text:line.textContent,hidden:line.hidden}}));
"""

_UPDATE_TEXT = "An update is waiting for this turn to finish; it installs right after."
_LIVE_TURN = {"turn_id": "t-update", "state": "inference_started", "age_s": 10.0}












_LOCAL_AND_SERVER = r"""
setQueueOwner("p-1");
// First show the remote-only line. That earlier turn finishes before this
// tab starts its own: never manufacture a competing send while it is live.
readServerTurnRow({active_turn:SCENARIO.activeTurn}); renderWorking();
const serverOnly=indicator();
readServerTurnRow({active_turn:null}); renderWorking();
// This tab's own send starts first; the server then reports a turn too. (A
// send made while ONLY another window's turn runs no longer starts a turn of
// its own -- it steers or waits; test_owner_steering covers that, P1 of
// 2026-10-02.)
const turn=sendTurn("and one from this tab");
await settle();
await pollStatus();                        // the server reports a turn as well
const both=indicator();
renderWorking();                           // the 1s repaint tick, mid-turn
const afterTick=indicator();
gates[0].resolve({reply:"done"});
await turn; await settle();
console.log(JSON.stringify({serverOnly, both, afterTick, after:indicator()}));
"""




# ---------------------------------------------------------------------------
# A chat waiting for one of the account's seats (`universe_seats`).
# ---------------------------------------------------------------------------

_SEAT_LINE = r"""
setQueueOwner("p-1");
await pollStatus();
const line=els["status-line"];
console.log(JSON.stringify({
  text:line.textContent,
  parts:line.children.map(c=>({text:c.textContent,href:c.href||""})),
}));
"""


def _seats(**extra):
    return {"running": 2, "waiting": 1, "chat_waiting": True,
            "upgrade_url": "https://tinyassets.io/app?upgrade=1", **extra}


_PURCHASE_ON = (
    "PLAN={can_purchase_on_surface:true,upgrade_available:true};\n" + _SEAT_LINE
)
_PURCHASE_OFF = (
    "PLAN={can_purchase_on_surface:false,upgrade_available:true,label:'Free'};\n"
    + _SEAT_LINE
)










# ---------------------------------------------------------------------------
# Which step, which model, how long (turn-wait-visibility, live 2026-10-02:
# turn c6ae56f9 waited ~10 minutes on one qwen request and the line could only
# say "thinking", which read exactly like a hang).
# ---------------------------------------------------------------------------

_OWN_TURN_WAIT = r"""
setQueueOwner("p-1");
els["btn-try-model"]=new El("button"); els["btn-try-model"].hidden=true;
const turn=sendTurn("build the office command center");
await settle();
await pollStatus();
const waiting=indicator();
const tryHidden=els["btn-try-model"].hidden;
gates[0].resolve({reply:"done"});
await turn; await settle();
console.log(JSON.stringify({waiting, tryHidden, after:indicator()}));
"""


def _step(round_age_s, state="inference_started"):
    return {"turn_id": "c6ae56f9", "state": state, "age_s": 900.0, "stale": False,
            "round": 4, "model": "qwen/qwen3.8-27b:free", "round_age_s": round_age_s}












_TRANSITIONS = r"""
setQueueOwner("p-1");
els["btn-try-model"]=new El("button"); els["btn-try-model"].hidden=true;
const turn=sendTurn("build it");
await settle();
SCENARIO.activeTurn=SCENARIO.first; await pollStatus();
const detailed=indicator();
interruptRequested=true; setStatusLine("Stopping your agent's turn...");
await pollStatus();
const stopping=indicator();
interruptRequested=false;
await pollStatus();
queueTurn("and also this", "and also this", {});
await settle();
const queued=indicator();
SCENARIO.activeTurn=SCENARIO.second; await pollStatus();
const afterTools=indicator();
SCENARIO.activeTurn=null; SCENARIO.seats={running:2,waiting:1,chat_waiting:true,upgrade_url:null};
await pollStatus();
const buttonAfterEnd=els["btn-try-model"].hidden;
gates[0].resolve({reply:"done"});
await turn; await settle();
console.log(JSON.stringify({detailed, queued, afterTools, stopping, buttonAfterEnd}));
"""




