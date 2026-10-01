"""Leaving or changing the verified account, EXECUTED under node.

`enterSignedOut` / `loadHistory` / `flushSendQueue` are lifted out of the
rendered page and run for real. What is asserted is the account boundary: the
previous account's visible thread and in-memory holdings are gone, a peek that
resolves after the switch paints nothing under the new identity, and the new
account can still load and restore its own state. What is on DISK is asserted
UNCHANGED - the signed-out account keeps its saved lines, its in-flight record
and its upload recovery.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

_NODE = shutil.which("node")

# Real page source, no reimplementation. The helper introduced by the account
# boundary fix is lifted when present so the test is RED (assertions, not an
# import error) against a tree that does not have it yet.
_LIFT = ("setQueueScope", "setQueueOwner", "ownsSavedRow", "savedItem",
         "flushSendQueue", "enterSignedOut", "loadHistory", "drawHistoryTurns",
         "offerEarlier", "loadEarlier", "historyFailed")
_OPTIONAL = ("clearAccountScopedState", "clearThread", "clearComposerState",
             # Added 2026-09-26 with the sign-out credential fix: this harness
             # runs the page's REAL `enterSignedOut`, so a collaborator it gained
             # has to be lifted here too. Optional, like its siblings, so this
             # file stays green against a tree without the fix.
             "clearCredentialFields",
             # Added 2026-09-30: rail card nodes are now KEPT across a refresh
             # so a 15-second poll cannot delete what the user typed into a
             # card, which makes clearing them an account-change step rather
             # than a side effect of the next rebuild.
             "clearRailCards", "clearTypedValues")


def _run_node(script: str):
    """The script is a FILE, not argv: Windows caps a command line at 32 KiB and
    the lifted app source passed it. Written outside the repo - a temp root
    inside it is refused by conftest, for good reason."""
    with tempfile.TemporaryDirectory(prefix="ta-acct-") as box:
        path = os.path.join(box, "harness.cjs")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(script)
        run = subprocess.run([_NODE, path], capture_output=True, text=True,
                             encoding="utf-8", timeout=120, check=False)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def _function_source(html: str, name: str, required: bool = True):
    try:
        start = html.index("function " + name + "(")
    except ValueError:
        if required:
            raise AssertionError(name + " not found in the rendered app")
        return None
    if html[max(0, start - 6):start] == "async ":
        start -= 6
    paren = html.index("(", start)
    level = 0
    for k in range(paren, len(html)):
        if html[k] == "(":
            level += 1
        elif html[k] == ")":
            level -= 1
            if level == 0:
                break
    i = html.index("{", k)
    depth = 0
    for j in range(i, len(html)):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                return html[start:j + 1]
    raise AssertionError("unbalanced braces in " + name)


def _app_html() -> str:
    from tinyassets.onboarding import render_app_html

    html, _csp = render_app_html()
    return html


def _upload_records_key(html: str) -> str:
    """The real recovery key, taken from the page. A literal copied into the
    harness would keep the durability assertion green against a key the page
    had stopped using."""
    match = re.search(r'const\s+UPLOAD_RECORDS_KEY\s*=\s*"([^"]+)"', html)
    assert match, "UPLOAD_RECORDS_KEY not found in the rendered app"
    return 'const UPLOAD_RECORDS_KEY="%s";' % match.group(1)


def _lifted(html: str) -> str:
    out = [_upload_records_key(html)]
    out += [_function_source(html, name) for name in _LIFT]
    for name in _OPTIONAL:
        src = _function_source(html, name, required=False)
        if src:
            out.append(src)
    return "\n".join(out)


_HARNESS = r"""
'use strict';
// ---- the page's own module-scope state, declared exactly as the page does ----
let queueScope="", queueOwner="", uploadsRestored=false, queueRestored=false;
let historyLoaded=false, inflightRestored=false, hasMessages=false;
let retainedItems=[], modelChoiceForNextTurn=null, statusTimer=null, workingTimer=null;
let queuePersisted=true;
// The working indicator's own state, declared as the page declares it. Its
// rendering is a collaborator here (see tests/test_app_working_indicator.py for
// the harness that runs the page's own version); what matters at THIS boundary is
// that the account switch drops it.
let serverTurn=null;
// Steering a running turn (harness S2): flushSendQueue holds while a steer
// request is on the wire.
let steeredLines=[], pendingSteers=0, steerHeldBatch=false, lastSettlement=null;
const sendQueue=[];
const renderedConsumerTurns=new Set();
const renderedConsumerFounders=new Set();
const QUEUE_KEY="ta_send_queue", TOKEN_KEY="ta_token", EXP_KEY="ta_exp";
const SEND_QUEUE_MAX=8, QUEUE_MAX_AGE_MS=3*60*60*1000;

// ---- collaborators the boundary talks to ----
const LOG=[];
function el(id){ return {id, className:"", text:"",
  remove(){ const t=DOM.thread, k=t.children.indexOf(this); if(k>=0)t.children.splice(k,1); },
  appendChild(){}}; }
// Distinct elements per id: the composer, the Send button and the fallback are
// three different objects, so a test cannot pass by writing one and reading
// another. An id the page asks for that is not modelled here is its own node.
const DOM={thread:{id:"thread",children:[]}, send:{id:"btn-send",disabled:false},
  composer:{id:"composer-input",value:"",style:{height:""},focus(){}},
  other:{}};
DOM.empty=el("thread-empty");
DOM.thread.children.push(DOM.empty);
function $(id){
  if(id==="thread")return DOM.thread;
  if(id==="thread-empty")return DOM.thread.children.indexOf(DOM.empty)>=0?DOM.empty:null;
  if(id==="btn-send")return DOM.send;
  if(id==="composer-input")return DOM.composer;
  // `children` because every element has some: the page now walks a node to
  // clear the controls under it (clearRailCards), and an element without the
  // property is not a DOM element at all.
  if(!DOM.other[id]) DOM.other[id]={id, value:"", textContent:"", style:{}, children:[]};
  return DOM.other[id];
}
let activeTurn=null, turnStartedAt=0, liveInflight=null;
let interruptRequested=false, flushAfterInterrupt=false;
let interruptPending=null, batchAfterStop=false;
function appendMessage(role,text){
  if(!hasMessages){ const e=$("thread-empty"); if(e)e.remove(); hasMessages=true; }
  const m=el("msg"); m.className="msg msg--"+role; m.text=String(text||"");
  DOM.thread.children.push(m); return m;
}
function appendFailureNotice(text){ return appendMessage("platform",text); }
function answerExecutionDetail(){ return null; }
function setStatusLine(s){ LOG.push(["status",s]); }
function readServerTurn(){}
function renderWorking(){ LOG.push(["working",serverTurn?"on":"off"]); }
function unmarkQueued(){}
function autoGrow(){}
function copyModelChoice(v){ return v===undefined?null:v; }
function turnInputMethod(v){ return v||"typed"; }
function resetClaudeConnection(){ LOG.push(["resetClaude"]); }
function cancelOpenAIFlow(){}
function resetOpenAIUI(){}
function showView(v){ LOG.push(["view",v]); }
function readInflight(){ return STORE.inflight; }
function forgetInflight(){ STORE.inflight=null; }
function sameSavedLine(a,b){ return !!a&&!!b&&a.message===b.message&&a.ts===b.ts; }
function claimedElsewhere(item){
  if(!queuePersisted) return false;
  const raw=STORE.local[QUEUE_KEY]; let items=[];
  try{ items=raw?JSON.parse(raw):[]; }catch(_e){ return false; }
  return !(Array.isArray(items)&&items.some(i=>i&&sameSavedLine(i,item)));
}
function saveQueue(){
  const items=retainedItems.map(savedItem).concat(sendQueue.map(savedItem));
  if(!items.length){ delete STORE.local[QUEUE_KEY]; queuePersisted=true; return; }
  STORE.local[QUEUE_KEY]=JSON.stringify(items); queuePersisted=true;
}
function readSavedQueue(){
  const raw=STORE.local[QUEUE_KEY];
  try{ const i=raw?JSON.parse(raw):[];
    return Array.isArray(i)?i.filter(x=>x&&typeof x.message==="string"&&x.message):[];
  }catch(_e){ return []; }
}
function restoreQueue(){
  if(queueRestored||!queueScope||!queueOwner) return; queueRestored=true;
  retainedItems=readSavedQueue();
  retainedItems.forEach(i=>LOG.push(["offer",i.message,ownsSavedRow(i)]));
}
async function restoreInflight(turns){ restoreQueue(); }
const SENT=[];
async function sendTurn(message,display,opts){
  SENT.push({message,owner:queueOwner,scope:queueScope});
}
const STORE={local:{}, session:{}, inflight:null};
const sessionStorage={ removeItem(k){ delete STORE.session[k]; },
  setItem(k,v){ STORE.session[k]=String(v); }, getItem(k){ return STORE.session[k]||null; } };
// Real binding, not a blank: without it the page could not erase a durable row
// even if it tried, and "recovery survived sign-out" would prove nothing.
const localStorage={ removeItem(k){ delete STORE.local[k]; },
  setItem(k,v){ STORE.local[k]=String(v); }, getItem(k){ return STORE.local[k]||null; },
  clear(){ for(const k of Object.keys(STORE.local)) delete STORE.local[k]; } };
function token(){ return STORE.session[TOKEN_KEY]||null; }
const Uploads={ aborted:0, abort(){ this.aborted++; } };
const Voice={ stop(){}, conversationSettled(){} };
const ModelPicker={ reset(){} };
const HostedModelConnect={ reset(){}, setup:"connected" };
const AppUI={ reset(){ LOG.push(["uiReset"]); }, homeChanged(){} };
// The credential fields the sign-out clears. Modelled so the account
// boundary test runs the page's real `enterSignedOut` end to end; what those
// fields hold afterwards is asserted in
// tests/test_app_signout_clears_typed_credentials.py.
const CREDENTIAL_FIELDS=[{id:"hosted-key-input",value:""},{id:"endpoint-key",value:""}];
const document={ querySelectorAll(selector){
  return selector==='input[type="password"]' ? CREDENTIAL_FIELDS : [];
} };
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
const MCP={ _loginEpoch:0, endLogin(){ this._loginEpoch++; }, invalidateSession(){},
  _conv:null, getConversation(){ return this._conv; } };
function threadText(){
  return DOM.thread.children.filter(c=>String(c.className).indexOf("msg")===0).map(c=>c.text);
}
// The page's own recovery key, LIFTED - a hand-written literal here would let
// the durability assertion pass against a key the page never writes.
const UPLOAD_KEY_A=UPLOAD_RECORDS_KEY+":"+JSON.stringify(["principal-a","universe-a"]);
"""


def _script(html: str, body: str) -> str:
    # Lifted page source FIRST: the harness below builds its recovery key from
    # the page's own UPLOAD_RECORDS_KEY, so that constant has to exist by then.
    return _lifted(html) + "\n" + _HARNESS + "\n" + body


pytestmark = pytest.mark.skipif(
    _NODE is None, reason="node is required to execute the page's own source")


@pytest.fixture(scope="module")
def html() -> str:
    return _app_html()


def test_sign_out_drops_the_previous_account_view_and_memory(html):
    """The first account's thread, queued lines and 'already restored' marks do
    not survive into the signed-out page - and nothing of theirs leaves disk."""
    out = _run_node(_script(html, r"""
    (async()=>{
      STORE.session[TOKEN_KEY]="t1";
      setQueueScope("universe-a"); setQueueOwner("principal-a");
      appendMessage("founder","my private salary spreadsheet");
      appendMessage("universe","here is what it says");
      historyLoaded=true; queueRestored=true; inflightRestored=true; uploadsRestored=true;
      sendQueue.push({message:"private queued line",display:"private queued line",
        opts:{echoed:true},ts:1000});
      saveQueue();
      STORE.inflight={message:"held",owner:"principal-a",scope:"universe-a"};
      STORE.local[UPLOAD_KEY_A]=JSON.stringify(
        {version:1,owner:"principal-a",home:"universe-a",saved:[{file_id:"f1"}]});

      enterSignedOut();

      console.log(JSON.stringify({
        thread:threadText(), queueScope, queueOwner, historyLoaded,
        queueRestored, inflightRestored, uploadsRestored,
        queued:sendQueue.length, retained:retainedItems.length,
        savedRowsOnDisk:JSON.parse(STORE.local[QUEUE_KEY]||"[]").length,
        inflightOnDisk:!!STORE.inflight,
        recoveryOnDisk:!!STORE.local[UPLOAD_KEY_A]}));
    })();
    """))
    # Visible + in-memory private state is gone.
    assert out["thread"] == [], "the previous account's conversation is still rendered"
    assert out["queueScope"] == "" and out["queueOwner"] == ""
    assert out["historyLoaded"] is False, "next account's loadHistory would return immediately"
    assert out["queueRestored"] is False and out["inflightRestored"] is False
    assert out["uploadsRestored"] is False
    assert out["queued"] == 0 and out["retained"] == 0
    # ...and the signed-out account keeps everything durable.
    assert out["savedRowsOnDisk"] == 1, "sign-out erased the owner's saved line"
    assert out["inflightOnDisk"] is True, "sign-out erased the owner's in-flight record"
    assert out["recoveryOnDisk"] is True, "sign-out erased the owner's upload recovery"


def test_late_history_from_the_old_account_paints_nothing_after_a_switch(html):
    """A getConversation still in flight when the account changes must not draw
    the old account's turns, and must not rename the new account's home."""
    out = _run_node(_script(html, r"""
    (async()=>{
      STORE.session[TOKEN_KEY]="t1";
      setQueueScope("universe-a"); setQueueOwner("principal-a");
      let release; MCP._conv=new Promise(r=>{release=r;});
      const pending=loadHistory();                 // awaits the peek
      enterSignedOut();                            // same page, sign out
      STORE.session[TOKEN_KEY]="t2";
      setQueueScope("universe-b"); setQueueOwner("principal-b");
      release({universe_id:"universe-a", recent_conversation:{turns:[
        {speaker:"founder",text:"account A private question",ts:10},
        {speaker:"universe",text:"account A private answer",ts:11}]}});
      await pending;
      console.log(JSON.stringify({thread:threadText(), queueScope, queueOwner, historyLoaded}));
    })();
    """))
    assert out["thread"] == [], "old account's history painted under the new identity"
    assert out["queueScope"] == "universe-b", "a stale peek renamed the new account's home"
    assert out["queueOwner"] == "principal-b"
    assert out["historyLoaded"] is False, "stale peek marked the new account's history loaded"


def test_new_account_loads_and_restores_its_own_state(html):
    """The boundary must not be a lock-out: the second account loads its own
    history and is offered its own saved line."""
    out = _run_node(_script(html, r"""
    (async()=>{
      STORE.session[TOKEN_KEY]="t1";
      setQueueScope("universe-a"); setQueueOwner("principal-a");
      appendMessage("founder","account A line"); historyLoaded=true; queueRestored=true;
      enterSignedOut();
      STORE.session[TOKEN_KEY]="t2";
      setQueueScope("universe-b"); setQueueOwner("principal-b");
      STORE.local[QUEUE_KEY]=JSON.stringify([
        {message:"B own waiting line",display:"B own waiting line",ts:Date.now(),
         owner:"principal-b",scope:"universe-b"}]);
      MCP._conv=Promise.resolve({universe_id:"universe-b",recent_conversation:{turns:[
        {speaker:"founder",text:"account B question",ts:20},
        {speaker:"universe",text:"account B answer",ts:21}]}});
      await loadHistory();
      console.log(JSON.stringify({thread:threadText(), historyLoaded,
        offers:LOG.filter(l=>l[0]==="offer")}));
    })();
    """))
    assert out["thread"] == ["account B question", "account B answer"], \
        "the new account could not load its own history"
    assert out["historyLoaded"] is True
    assert out["offers"] == [["offer", "B own waiting line", True]], \
        "the new account was not offered its own saved line"


def test_queued_send_does_not_ride_out_under_the_next_account(html):
    """A line queued by account A is never flushed onto account B's wire."""
    out = _run_node(_script(html, r"""
    (async()=>{
      STORE.session[TOKEN_KEY]="t1";
      setQueueScope("universe-a"); setQueueOwner("principal-a");
      sendQueue.push({message:"A private queued line",display:"A private queued line",
        opts:{echoed:true},ts:1000, owner:"principal-a", scope:"universe-a"});
      saveQueue();
      enterSignedOut();
      STORE.session[TOKEN_KEY]="t2";
      setQueueScope("universe-b"); setQueueOwner("principal-b");
      DOM.send.disabled=false;
      flushSendQueue();
      console.log(JSON.stringify({sent:SENT, queued:sendQueue.length,
        savedRowsOnDisk:JSON.parse(STORE.local[QUEUE_KEY]||"[]").length}));
    })();
    """))
    assert out["sent"] == [], "account A queued line was sent under account B"
    assert out["queued"] == 0
    assert out["savedRowsOnDisk"] == 1, "account A saved line was destroyed"


def test_sign_out_takes_the_composer_with_the_rest_of_the_account(html):
    """An unsent private draft and a Send left disabled by A's in-flight turn
    are account-scoped state: B gets neither A's words nor a dead button. On
    DISK nothing of A's is touched."""
    out = _run_node(_script(html, r"""
    (async()=>{
      STORE.session[TOKEN_KEY]="t1";
      setQueueScope("universe-a"); setQueueOwner("principal-a");
      // A is mid-turn: Send is disabled, a turn is on the clock, and there is
      // an unsent private draft sitting in the composer behind it.
      DOM.composer.value="my unsent private draft: severance terms";
      DOM.composer.style.height="96px";
      DOM.send.disabled=true; turnStartedAt=1000; activeTurn={};
      setStatusLine("Your universe is thinking...");
      STORE.local[UPLOAD_KEY_A]=JSON.stringify(
        {version:1,owner:"principal-a",home:"universe-a",saved:[{file_id:"f1"}]});
      sendQueue.push({message:"A queued line",display:"A queued line",
        opts:{echoed:true},ts:1000,owner:"principal-a",scope:"universe-a"});
      saveQueue();

      enterSignedOut();
      // B signs in on the same page.
      STORE.session[TOKEN_KEY]="t2";
      setQueueScope("universe-b"); setQueueOwner("principal-b");

      console.log(JSON.stringify({
        draft:DOM.composer.value, height:DOM.composer.style.height,
        sendDisabled:DOM.send.disabled, turnStartedAt,
        activeTurn:activeTurn!==null,
        lastStatus:(LOG.filter(l=>l[0]==="status").pop()||[null,null])[1],
        recoveryOnDisk:!!STORE.local[UPLOAD_KEY_A],
        savedRowsOnDisk:JSON.parse(STORE.local[QUEUE_KEY]||"[]").length}));
    })();
    """))
    assert out["draft"] == "", "account A's unsent draft was left in B's composer"
    assert out["height"] == "auto", "the composer kept A's grown height"
    assert out["sendDisabled"] is False, "B inherited a Send disabled by A's turn"
    assert out["turnStartedAt"] == 0, "A's turn clock still runs under B"
    assert out["activeTurn"] is False, "A's turn still holds the composer under B"
    assert out["lastStatus"] == "", "A's status line is still on B's screen"
    # ...and nothing durable of A's was erased to do it.
    assert out["recoveryOnDisk"] is True, "the composer reset erased A's upload recovery"
    assert out["savedRowsOnDisk"] == 1, "the composer reset erased A's saved line"


def test_a_plain_draft_with_no_pending_send_is_cleared_too(html):
    """No in-flight turn, just typed text. It is still A's private text and it
    still does not survive into B's composer."""
    out = _run_node(_script(html, r"""
    (async()=>{
      STORE.session[TOKEN_KEY]="t1";
      setQueueScope("universe-a"); setQueueOwner("principal-a");
      DOM.composer.value="draft nobody sent";
      DOM.composer.style.height="64px";
      DOM.send.disabled=false;                 // nothing pending
      enterSignedOut();
      console.log(JSON.stringify({draft:DOM.composer.value,
        height:DOM.composer.style.height, sendDisabled:DOM.send.disabled}));
    })();
    """))
    assert out["draft"] == "", "a plain unsent draft crossed the account boundary"
    assert out["height"] == "auto"
    assert out["sendDisabled"] is False


def test_the_previous_turns_cleanup_cannot_touch_the_next_account(html):
    """A's turn reaches its `finally` after the boundary. The page's own test -
    "is this still MY composer" - has to say no, or A's cleanup re-enables,
    re-times and rewrites a composer that is now B's."""
    out = _run_node(_script(html, r"""
    (async()=>{
      STORE.session[TOKEN_KEY]="t1";
      setQueueScope("universe-a"); setQueueOwner("principal-a");
      // Exactly what sendTurn holds across its await.
      const myTurn={}; activeTurn=myTurn;
      DOM.send.disabled=true; turnStartedAt=1000;
      setStatusLine("Your universe is thinking...");

      enterSignedOut();                            // the boundary
      STORE.session[TOKEN_KEY]="t2";
      setQueueScope("universe-b"); setQueueOwner("principal-b");
      // B is simply sitting there, typing. No turn of its own yet.
      DOM.composer.value="B is part way through a sentence";
      DOM.send.disabled=true;                      // B's own Send state
      turnStartedAt=2000; setStatusLine("B's own line");

      // ...and NOW A's finally runs, with the page's own ownership test.
      const mine=(activeTurn===myTurn);
      if(mine){ activeTurn=null; DOM.send.disabled=false; turnStartedAt=0;
                setStatusLine(""); }

      console.log(JSON.stringify({mine, sendDisabled:DOM.send.disabled,
        turnStartedAt, draft:DOM.composer.value,
        lastStatus:(LOG.filter(l=>l[0]==="status").pop()||[null,null])[1]}));
    })();
    """))
    assert out["mine"] is False, (
        "the retired turn still holds the composer and its cleanup runs on B")
    assert out["sendDisabled"] is True, "A's cleanup re-enabled B's Send"
    assert out["turnStartedAt"] == 2000, "A's cleanup cleared B's turn clock"
    assert out["draft"] == "B is part way through a sentence"
    assert out["lastStatus"] == "B's own line", "A's cleanup wiped B's status line"


def test_the_same_account_moving_home_is_offered_the_new_homes_attachments(html):
    """The recovery row is keyed by owner AND home. One account with two homes
    must not have the first home's restore mark suppress the second's."""
    out = _run_node(_script(html, r"""
    (async()=>{
      setQueueOwner("principal-a"); setQueueScope("universe-a");
      uploadsRestored=true;                       // home A already offered
      setQueueScope("universe-b");                // same account, other home
      console.log(JSON.stringify({uploadsRestored, aborted:Uploads.aborted}));
    })();
    """))
    assert out["uploadsRestored"] is False, (
        "the second home's saved attachments would never be offered")
    assert out["aborted"] == 1, "a home change no longer stops a transfer in flight"


# ---------------------------------------------------------------------------
# First session: an unpowered universe lands in its own chat with the connect
# request first (vendor-neutral slice 6, founder 2026-09-24). No separate gate.
# ---------------------------------------------------------------------------

_GATE_HARNESS = r"""
'use strict';
let queueScope="", queueOwner="", uploadsRestored=false, queueRestored=false;
let historyLoaded=false, inflightRestored=false, engineConnected=null;
const LOG=[];
const NATIVE=false;
const DOM={};
function $(id){ if(!DOM[id]) DOM[id]={id, textContent:"", value:"",
  style:{}, focus(){ LOG.push(["focus",id]); }}; return DOM[id]; }
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
const MCP={ _loginEpoch:0, endLogin(){ this._loginEpoch++; } };
const Uploads={ aborted:0, abort(){ this.aborted++; } };
const Voice={ refreshCapability(){} };
const ModelPicker={ reset(){} };
const AppUI={ reset(){ LOG.push(["uiReset"]); }, enable(u,p){ LOG.push(["ui",u,p]); } };
const HostedModelConnect={ setup:"empty", busy:false, request:null,
  async begin(){ LOG.push(["begin"]); }, paint(){}, status(t){ LOG.push(["status",t]); } };
function token(){ return "t1"; }
function showView(v){ LOG.push(["view",v]); }
function openConnectRequest(){ LOG.push(["connect"]); }
function refreshRail(){ LOG.push(["rail"]); }
function startHeartbeat(){ LOG.push(["heartbeat"]); }
function warmSession(){}
function loadPlan(){ LOG.push(["loadPlan"]); }
function loadHistory(){ LOG.push(["loadHistory",queueOwner,queueScope]); }
function sessionExpired(){ LOG.push(["expired"]); }
// Sign-in reports the browser's IANA zone so a cron automation can run in the
// owner's clock rather than the container's (`automation-schedule-timezone`).
// Stubbed like every other collaborator here; the real one POSTs and is
// deliberately not awaited.
function reportTimezone(){ LOG.push(["timezone"]); }
"""


def _gate_script(html: str, body: str) -> str:
    lifted = [_function_source(html, "setQueueScope"),
              _function_source(html, "setQueueOwner"),
              _function_source(html, "enterSignedIn")]
    return _GATE_HARNESS + "\n" + "\n".join(lifted) + "\n" + body


def test_an_unpowered_first_session_lands_in_chat_with_the_request_first(html):
    """The first session's normal path is its own chat, with the account and
    home learned from the verified /app/me before anything is restored,
    and the connect request opened - never a separate full-page screen."""
    out = _run_node(_gate_script(html, r"""
    (async()=>{
      globalThis.fetchMe=async()=>({setup:"empty",
        universe_id:"universe-a", principal_id:"principal-a"});
      await enterSignedIn();
      console.log(JSON.stringify({queueOwner, queueScope, engineConnected, log:LOG}));
    })();
    """))
    log = out["log"]
    assert ["view", "chat"] in log, "an unpowered universe did not land in its chat"
    assert ["connect"] in log, "the connect request was not opened"
    assert out["queueOwner"] == "principal-a" and out["queueScope"] == "universe-a"
    assert ["loadHistory", "principal-a", "universe-a"] in log, \
        "the first session reached chat without asking for its own history"
    assert out["engineConnected"] is False
    assert not any(entry[0] == "ui" for entry in log), \
        "an unpowered universe enabled the custom-UI switcher"
    assert ["timezone"] in log, \
        "sign-in did not report the browser's zone, so a schedule would run on " \
        "the container's clock"


def test_a_powered_session_lands_in_chat_without_opening_setup(html):
    out = _run_node(_gate_script(html, r"""
    (async()=>{
      globalThis.fetchMe=async()=>({setup:"connected",
        universe_id:"universe-a", principal_id:"principal-a"});
      await enterSignedIn();
      console.log(JSON.stringify({engineConnected, log:LOG}));
    })();
    """))
    assert ["view", "chat"] in out["log"] and ["connect"] not in out["log"]
    assert ["ui", "universe-a", "principal-a"] in out["log"]
    assert out["engineConnected"] is True


def test_a_me_that_lands_after_the_login_changed_stamps_no_identity(html):
    """A /app/me still in flight when the account changes describes
    somebody else. It must not write that identity onto the page."""
    out = _run_node(_gate_script(html, r"""
    (async()=>{
      let release;
      globalThis.fetchMe=async()=>{
        await new Promise(r=>{release=r;});
        return {setup:"connected", universe_id:"universe-a", principal_id:"principal-a"};
      };
      const pending=enterSignedIn();
      MCP.endLogin();                            // signed out mid-flight
      setQueueScope("universe-b"); setQueueOwner("principal-b");
      release();
      await pending;
      console.log(JSON.stringify({queueOwner, queueScope,
        painted:LOG.filter(l=>l[0]==="view"||l[0]==="connect")}));
    })();
    """))
    assert out["queueOwner"] == "principal-b", \
        "a stale /app/me renamed the account now on screen"
    assert out["queueScope"] == "universe-b", \
        "a stale /app/me renamed the home now on screen"
    assert out["painted"] == [], "a stale /app/me painted a view"
