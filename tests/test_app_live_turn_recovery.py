"""A status or history pass must never restore a turn the live page owns.

Live, 2026-09-20 23:30 UTC: ONE typed message. Its bubble appeared, Send
disabled, "thinking". Thirty seconds later the same message was drawn AGAIN
with "This message was never confirmed - the reply did not arrive." and a
"Send it again" button - while the original request was still in flight and
Send was still disabled. No reload, no retry clicked.

Cause: the first `restoreInflight` pass found nothing on disk and returned
without marking itself done, so the 30-second `pollStatus` kept calling it.
The page's own `sendTurn` had meanwhile written the in-flight record for the
live turn, and the next poll read that record back as abandoned work from a
previous page: a second founder bubble, a false notice, and a resend offer
that would have run the workflow twice.

Everything here EXECUTES the page's own `sendTurn` / `sendVoiceTurn` /
`pollStatus` / `loadHistory` / `restoreInflight` under node against the DOM
shim `tests/test_onboarding_app.py` uses. Nothing is reimplemented.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

from tests.app_sheet_harness import sheet_source
from tests.test_onboarding_app import _APP_SHIM, _js_function
from tinyassets import onboarding

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    _NODE is None, reason="node is required to execute the page's own source")

_DECLS = (
    r"const INFLIGHT_KEY=[^\n]*;", r"let turnStartedAt=[^\n]*;", r"let activeTurn=[^\n]*;",
    r"let historyLoaded = [^\n]*;", r"let inflightRestored = [^\n]*;",
    r"let railOpen = [^\n]*;", r"const sendQueue=[^\n]*;", r"let sendQueueHeld=[^\n]*;",
    r"const SEND_QUEUE_MAX=[^\n]*;", r"const QUEUE_KEY=[^\n]*;",
    r"let queueRestored=[^\n]*;", r"const QUEUE_MAX_AGE_MS=[^\n]*;",
    r"let queueScope=[^\n]*;", r"let queueOwner=[^\n]*;",
    r"let queuePersisted=[^\n]*;", r"let retainedItems=[^\n]*;",
    r"let modelChoiceForNextTurn=[^\n]*;",
    r"const renderedConsumerTurns=[^\n]*;", r"const renderedConsumerFounders=[^\n]*;",
    r"let Uploads=[^\n]*;", r"let uploadsRestored=[^\n]*;", r"let hasMessages=[^\n]*;",
    r"let statusTimer=[^\n]*;",
    # Introduced by the fix. Optional so the test is RED on assertions, not on
    # a missing declaration, against a tree that does not have it yet.
    r"let liveInflight=[^\n]*;",
    # The Stop control's state (a turn's cleanup reads it).
    r"let interruptRequested=[^\n]*;",
    # Lines steered into a running turn (harness S2).
    r"let steeredLines=[^\n]*;", r"let pendingSteers=[^\n]*;", r"let watchedActive=[^\n]*;",
)
_FUNCS = (
    "turnInputMethod", "rememberInflight", "forgetInflight", "readInflight", "renderConverse",
    "copyModelChoice", "captureTurnOptions", "sendConversationRequest",
    "executionLabel", "answerExecutionDetail", "servedFailureError", "appendFailureNotice",
    "offerResend", "sendTurn", "sendVoiceTurn", "loadHistory",
    "drawHistoryTurns", "offerEarlier", "loadEarlier", "historyFailed", "restoreInflight",
    "pollStatus", "setQueueScope", "setQueueOwner", "ownsSavedRow",
    "flushSendQueue", "queueTurn", "saveQueue", "readSavedQueue", "stillSaved",
    "forgetSavedItem", "savedItem", "sameSavedLine", "restoreQueue", "claimedElsewhere",
    "offerSavedLine", "clearComposerState", "clearAccountScopedState", "clearThread",
)
_OPTIONAL_FUNCS = ("sameInflight", "forgetInflightIf", "noteHeldQueue",
                   "offerSavedConversationCheck", "savedSendIndex", "attachSavedConversationCheck",
                   # Collaborators `clearAccountScopedState` gained on
                   # 2026-09-30: rail card nodes are now kept across a refresh
                   # so a 15-second poll cannot delete what the user typed into
                   # a card, which makes clearing them an account-change step.
                   # Optional like their siblings, so these harnesses stay green
                   # against a tree without the change.
                   "clearRailCards", "clearTypedValues", "clearMemoryState",
                   # The Stop control: a turn's cleanup asks whether the queue
                   # goes out as one batch.
                   "takeInterruptFlush", "flushAfterTurn", "drainAfterStop",
                   "takeBatch", "flushBatch",
                   # Harness S2: steering a running turn, and settling it.
                   "markSteered", "unmarkSteered", "steerOrQueue", "settleSteered",
                   "adoptSteered", "markHeld", "restoreHeldSteers", "readServerTurnRow",
                   "claimHeldLines", "pinLineAgent", "alreadyHandled", "showActiveTurn",
                   "finishActiveTurn", "readPendingTurns", "sendBatch")

# The shim above stops at `__APP_FUNCTIONS__`; this test supplies the
# collaborators `pollStatus` reaches that the send/restore scenarios never did.
_EXTRA_SHIM = r"""
const appendShim=appendMessage;
appendMessage=(...args)=>{const el=appendShim(...args);
  if(!el.parentNode) els.thread.appendChild(el);return el;};
const InlineConnection={waiting:()=>false,hold:()=>{
  throw new Error('Unexpected connection request in the live-turn harness');
}};
els["dot"]=new El("div"); els["universe-name"]=new El("div");
for(const id of ["profile-name","profile-responsibility","profile-status",
                 "wakes-list","wakes-status"])
  els[id]=new El("div");
let healed=[]; async function healServing(s){ healed.push(s); }
let uploadRestores=0; function restoreUploadRecords(){ uploadRestores++; }
ModelPicker.reset=()=>{}; ModelPicker.snapshot=null;
let statusPolls=0;
Owner.getStatus=async()=>{ statusPolls++;
  if(SCENARIO.statusError){ const e=new Error("status down"); e.transport=true; throw e; }
  return {active_host:"h", universe_id:SCENARIO.universe||"u-1", universe_name:"Home"}; };
MCP.invalidateSession=()=>{};
MCP._loginEpoch=0;
// A converse the scenario releases by hand: the live request stays in flight
// for exactly as long as the test needs the page to be "thinking".
const gates=[];
MCP.converse=async(m,inputMethod,modelChoice,consumerRequest)=>{
  converseCalls.push(m); converseMethods.push(inputMethod);
  consumerRequests.push(consumerRequest?JSON.parse(JSON.stringify(consumerRequest)):null);
  active++; maxActive=Math.max(maxActive,active);
  try{
    const reply=await new Promise((resolve,reject)=>gates.push({resolve,reject,m}));
    return reply;
  } finally { active--; }
};
const settle=()=>new Promise(r=>setTimeout(r,20));
function snapshot(){
  const live=els.thread.children.filter(n=>!n.removed);
  return {
    founderBubbles:messages.filter(m=>m.role==="founder").length,
    universeBubbles:messages.filter(m=>m.role==="universe").length,
    messages:messages.slice(),
    notes:live.map(n=>({cls:n.className,text:n.textContent,
      buttons:n.children.filter(c=>c.tagName==="BUTTON").map(b=>b.textContent)})),
    resendButtons:live.flatMap(n=>n.children).filter(c=>c.tagName==="BUTTON"&&
      (c.textContent==="Send it again"||c.textContent==="Check this conversation")).length,
    unconfirmedNotes:live.filter(n=>/never confirmed/.test(n.textContent)).length,
    sendDisabled:els["btn-send"].disabled,
    status:els["status-line"].textContent,
    inflight:JSON.parse(localStorage.getItem(INFLIGHT_KEY)||"null"),
    converseCalls:converseCalls.slice(),
    statusPolls, inflightRestored, activeTurnHeld:activeTurn!==null,
  };
}
"""


def _program(html: str, body: str) -> str:
    decls = []
    for pat in _DECLS:
        found = re.search(pat, html)
        if found:
            decls.append(found.group(0))
    funcs = [sheet_source(html)] + [_js_function(html, f) for f in _FUNCS]
    for name in _OPTIONAL_FUNCS:
        if re.search(r"function\s+" + name + r"\s*\(", html):
            funcs.append(_js_function(html, name))
    head = _APP_SHIM.split("__APP_FUNCTIONS__", 1)[0]
    tail = "\n})().catch(e=>{ console.error(e&&e.stack||e); process.exit(1); });"
    return (head.replace("__SCENARIO__", json.dumps({})) + _EXTRA_SHIM +
            "\n".join(decls) + "\n" + "\n".join(funcs) + "\n" +
            "(async()=>{\n" + body + tail)


def _run(tmp_path, html: str, body: str) -> dict:
    script = tmp_path / "live_turn_case.js"
    script.write_text(_program(html, body), encoding="utf-8")
    proc = subprocess.run([_NODE, str(script)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60, env=dict(os.environ))
    assert proc.returncode == 0, f"app harness crashed:\n{proc.stderr}"
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def html() -> str:
    page, _csp = onboarding.render_app_html()
    return page


# ---------------------------------------------------------------------------
# The live regression: a status poll while the page's own turn is in flight.
# ---------------------------------------------------------------------------

_LIVE_TURN = r"""
setQueueOwner("p-1");
await loadHistory();                          // empty thread: the first-message case
await pollStatus();                           // heartbeat before anything is sent
const turn=sendTurn("Retest your workflow checklist");
await settle();
const whileThinking=snapshot();
await pollStatus();                           // the 30-second heartbeat, mid-turn
await loadHistory();                          // a late history pass, mid-turn
await pollStatus();
await settle();
const afterPolls=snapshot();
gates[0].resolve({reply:"Checklist retested."});
await turn; await settle();
const done=snapshot();
console.log(JSON.stringify({whileThinking, afterPolls, done, healed:healed.length}));
"""










# ---------------------------------------------------------------------------
# Real recovery is untouched: a record from a PREVIOUS page is still offered.
# ---------------------------------------------------------------------------

def _prior_page_record(message: str, **extra) -> str:
    record = {"message": message, "display": message, "inputMethod": "typed",
              "modelChoice": None, "consumerRequest": None, "owner": "p-1",
              "scope": "u-1", "ts": 1_700_000_000_000}
    record.update(extra)
    return "localStorage.setItem(INFLIGHT_KEY, %s);" % json.dumps(json.dumps(record))








# ---------------------------------------------------------------------------
# The account boundary: a retired turn's record becomes ordinary held state.
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# A late observation of a PREVIOUS page's request must not erase the record
# of a turn this page started while that observation was in flight.
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# An outcome the transport could not confirm is not a finished turn.
# ---------------------------------------------------------------------------

_UNCONFIRMED_QUEUE = r"""
let savedTurns = [{speaker:"founder", text:"an older line", ts: 1000},
                  {speaker:"universe", text:"an older reply", ts: 1001}];
let peeks = 0;
Owner.getConversation = async () => { peeks++;
  return {universe_id:"u-1", recent_conversation:{turns: savedTurns}}; };
setQueueOwner("p-1");
await loadHistory();
await pollStatus();
const turn = sendTurn("run the deploy");
await settle();
sendTurn("and then tell me the result");        // queued behind the live turn
await settle();
// A draft typed while the turn is still running: nothing about the recovery
// path may take it away (queueTurn's own consumption of the composer is a
// separate, deliberate behaviour and happens above).
$("composer-input").value = "a draft I was typing";
const queuedBefore = sendQueue.length;
const silent = new Error("your agent stopped sending anything back");
silent.transport = "stream_silent"; silent.replayable = false;
gates[0].reject(silent);
await turn; await settle(); await settle();
const afterFailure = snapshot();
const live = els.thread.children.filter(n=>!n.removed);
const note = live.filter(n=>n.children.some(c=>c.tagName==="BUTTON" &&
  c.textContent==="Check saved conversation"))[0] || live[live.length-1];
const check = note.children.filter(c=>c.tagName==="BUTTON" &&
  c.textContent==="Check saved conversation")[0];
const hadCheck = !!check;
if(check) check.click();
await settle(); await settle();
console.log(JSON.stringify({
  queuedBefore, queuedAfter: sendQueue.length, hadCheck, peeks,
  converseCalls: converseCalls.slice(),
  draft: $("composer-input").value,
  sendDisabled: els["btn-send"].disabled,
  inflight: afterFailure.inflight,
  noteText: note.textContent,
  checkText: note.children.filter(c=>c.className==="muted").map(c=>c.textContent).join(" "),
  savedTexts: note.children.filter(c=>c.className==="muted").flatMap(c=>c.children)
    .filter(c=>c.tagName==="PRE").map(c=>c.textContent),
  heldNote: live.map(n=>n.textContent).filter(t=>/being held/.test(t)),
}));
"""








# ---------------------------------------------------------------------------
# Voice's stale "did not arrive" line is retired by the NEXT turn of the same
# account and home - the live sequence contained no resend at all.
# ---------------------------------------------------------------------------



