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
    r"let steeredLines=[^\n]*;", r"let pendingSteers=[^\n]*;",
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
                   "offerSavedConversationCheck",
                   # Collaborators `clearAccountScopedState` gained on
                   # 2026-09-30: rail card nodes are now kept across a refresh
                   # so a 15-second poll cannot delete what the user typed into
                   # a card, which makes clearing them an account-change step.
                   # Optional like their siblings, so these harnesses stay green
                   # against a tree without the change.
                   "clearRailCards", "clearTypedValues",
                   # The Stop control: a turn's cleanup asks whether the queue
                   # goes out as one batch.
                   "takeInterruptFlush", "flushAfterTurn", "drainAfterStop",
                   "takeBatch", "flushBatch",
                   # Harness S2: steering a running turn, and settling it.
                   "markSteered", "unmarkSteered", "steerOrQueue", "settleSteered",
                   "adoptSteered")

# The shim above stops at `__APP_FUNCTIONS__`; this test supplies the
# collaborators `pollStatus` reaches that the send/restore scenarios never did.
_EXTRA_SHIM = r"""
els["dot"]=new El("div"); els["universe-name"]=new El("div");
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
    funcs = [_js_function(html, f) for f in _FUNCS]
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


def test_a_status_poll_does_not_restore_the_turn_the_live_page_owns(tmp_path, html):
    out = _run(tmp_path, html, _LIVE_TURN)
    thinking, polled, done = out["whileThinking"], out["afterPolls"], out["done"]
    assert thinking["founderBubbles"] == 1 and thinking["sendDisabled"] is True
    assert thinking["status"] == "Your universe is thinking..."
    assert thinking["inflight"]["message"] == "Retest your workflow checklist", \
        "the live turn's durable recovery must be on disk while it is in flight"
    # The polls ran (the heartbeat is not disabled) ...
    assert polled["statusPolls"] == 3 and out["healed"] == 3
    # ... and changed nothing about the turn the page owns.
    assert polled["founderBubbles"] == 1, "the live message was drawn a second time"
    assert polled["unconfirmedNotes"] == 0, "a live turn was reported as never confirmed"
    assert polled["resendButtons"] == 0, "a resend was offered for a request still in flight"
    assert polled["sendDisabled"] is True and polled["status"] == "Your universe is thinking..."
    assert polled["inflight"]["message"] == "Retest your workflow checklist", \
        "a poll erased the live turn's recovery record"
    assert polled["converseCalls"] == ["Retest your workflow checklist"]
    # The original reply still lands once, and the record is cleared by its owner.
    assert done["messages"] == [
        {"role": "founder", "text": "Retest your workflow checklist"},
        {"role": "universe", "text": "Checklist retested."}]
    assert done["unconfirmedNotes"] == 0 and done["resendButtons"] == 0
    assert done["sendDisabled"] is False and done["inflight"] is None
    assert done["converseCalls"] == ["Retest your workflow checklist"], \
        "the workflow was invoked more than once"


def test_a_spoken_turn_is_not_restored_by_the_heartbeat_either(tmp_path, html):
    out = _run(tmp_path, html, r"""
    setQueueOwner("p-1");
    await loadHistory(); await pollStatus();
    const turn=sendVoiceTurn("read me the checklist");
    await settle();
    await pollStatus(); await pollStatus(); await settle();
    const polled=snapshot();
    gates[0].resolve({reply:"Reading it now."});
    const spoken=await turn; await settle();
    console.log(JSON.stringify({polled, spoken, done:snapshot()}));
    """)
    assert out["polled"]["founderBubbles"] == 1
    assert out["polled"]["unconfirmedNotes"] == 0 and out["polled"]["resendButtons"] == 0
    assert out["polled"]["inflight"]["inputMethod"] == "spoken"
    assert out["spoken"] == "Reading it now."
    assert out["done"]["converseCalls"] == ["read me the checklist"]
    assert out["done"]["inflight"] is None


def test_a_late_history_peek_does_not_restore_a_turn_started_meanwhile(tmp_path, html):
    """The peek was still in flight when the founder sent; its result must not
    read the record that send wrote as a previous page's abandoned message."""
    out = _run(tmp_path, html, r"""
    // Both halves come from /app/me before the composer is usable
    // (enterSignedIn); the peek is what is still in flight, not the pair.
    setQueueOwner("p-1"); setQueueScope("u-1");
    let release; Owner.getConversation=()=>new Promise(r=>{release=r;});
    const peek=loadHistory();                  // awaiting the connector
    await settle();
    const turn=sendTurn("first line of the day");
    await settle();
    release({universe_id:"u-1", recent_conversation:{turns:[]}});
    await peek; await pollStatus(); await settle();
    const polled=snapshot();
    gates[0].resolve({reply:"Good morning."});
    await turn; await settle();
    console.log(JSON.stringify({polled, done:snapshot()}));
    """)
    assert out["polled"]["founderBubbles"] == 1
    assert out["polled"]["unconfirmedNotes"] == 0 and out["polled"]["resendButtons"] == 0
    assert out["polled"]["inflight"]["message"] == "first line of the day"
    assert out["done"]["messages"] == [
        {"role": "founder", "text": "first line of the day"},
        {"role": "universe", "text": "Good morning."}]
    assert out["done"]["converseCalls"] == ["first line of the day"]
    assert out["done"]["inflight"] is None


def test_a_transport_failure_on_the_live_page_leaves_exactly_one_recovery(tmp_path, html):
    """The turn's own catch offers the resend and keeps the record. The
    heartbeat that follows must not draw the bubble and the offer again."""
    out = _run(tmp_path, html, r"""
    setQueueOwner("p-1");
    await loadHistory(); await pollStatus();
    const turn=sendTurn("deploy the fix");
    await settle();
    const e=new Error("offline"); e.transport=true; gates[0].reject(e);
    await turn; await settle();
    const failed=snapshot();
    await pollStatus(); await loadHistory(); await pollStatus(); await settle();
    const polled=snapshot();
    // The single offer still works and is the same send.
    const btn=els.thread.children.filter(n=>!n.removed).flatMap(n=>n.children)
      .find(c=>c.tagName==="BUTTON"&&c.textContent==="Send it again");
    btn.click(); await settle();
    gates[1].resolve({reply:"Deployed."}); await settle();
    console.log(JSON.stringify({failed, polled, done:snapshot()}));
    """)
    assert out["failed"]["founderBubbles"] == 1 and out["failed"]["resendButtons"] == 1
    assert out["failed"]["inflight"]["message"] == "deploy the fix"
    assert out["failed"]["sendDisabled"] is False
    assert out["polled"]["founderBubbles"] == 1, "the failed message was drawn again"
    assert out["polled"]["resendButtons"] == 1, "a second resend offer appeared"
    assert out["polled"]["unconfirmedNotes"] == 0
    assert out["polled"]["inflight"]["message"] == "deploy the fix", \
        "the durable recovery for a failed turn was erased"
    assert out["done"]["converseCalls"] == ["deploy the fix", "deploy the fix"]
    assert out["done"]["messages"][-1] == {"role": "universe", "text": "Deployed."}
    assert out["done"]["founderBubbles"] == 1 and out["done"]["inflight"] is None


# ---------------------------------------------------------------------------
# Real recovery is untouched: a record from a PREVIOUS page is still offered.
# ---------------------------------------------------------------------------

def _prior_page_record(message: str, **extra) -> str:
    record = {"message": message, "display": message, "inputMethod": "typed",
              "modelChoice": None, "consumerRequest": None, "owner": "p-1",
              "scope": "u-1", "ts": 1_700_000_000_000}
    record.update(extra)
    return "localStorage.setItem(INFLIGHT_KEY, %s);" % json.dumps(json.dumps(record))


def test_a_reload_still_offers_the_previous_pages_unconfirmed_message_once(tmp_path, html):
    out = _run(tmp_path, html, _prior_page_record("hello there") + r"""
    setQueueOwner("p-1");
    await loadHistory();
    await pollStatus(); await pollStatus(); await loadHistory(); await settle();
    const offered=snapshot();
    const btn=els.thread.children.filter(n=>!n.removed).flatMap(n=>n.children)
      .find(c=>c.tagName==="BUTTON"&&c.textContent==="Send it again");
    btn.click(); await settle();
    const resent=snapshot();
    await pollStatus(); await settle();
    const polledMidResend=snapshot();
    gates[0].resolve({reply:"Hello!"}); await settle();
    console.log(JSON.stringify({offered, resent, polledMidResend, done:snapshot()}));
    """)
    assert out["offered"]["founderBubbles"] == 1, "reload recovery was lost"
    assert out["offered"]["unconfirmedNotes"] == 1 and out["offered"]["resendButtons"] == 1
    assert out["offered"]["inflight"]["message"] == "hello there"
    assert out["offered"]["converseCalls"] == []
    # The resend is a same-page turn now: the heartbeat leaves it alone.
    assert out["resent"]["converseCalls"] == ["hello there"]
    assert out["resent"]["founderBubbles"] == 1 and out["resent"]["resendButtons"] == 0
    assert out["polledMidResend"]["founderBubbles"] == 1
    assert out["polledMidResend"]["unconfirmedNotes"] == 0
    assert out["polledMidResend"]["inflight"]["message"] == "hello there"
    assert out["done"]["messages"] == [{"role": "founder", "text": "hello there"},
                                       {"role": "universe", "text": "Hello!"}]
    assert out["done"]["inflight"] is None


def test_a_reload_after_a_failed_peek_is_offered_by_the_heartbeat_once(tmp_path, html):
    """Codex round 3 (P1) kept: the peek failed, so the page learns its universe
    from the heartbeat, and THAT pass offers the held message - once."""
    out = _run(tmp_path, html, _prior_page_record("hello") + r"""
    setQueueOwner("p-1");
    Owner.getConversation=async()=>{ throw new Error("peek failed"); };
    await loadHistory();
    const beforeStatus=snapshot();
    await pollStatus(); await pollStatus(); await settle();
    console.log(JSON.stringify({beforeStatus, polled:snapshot()}));
    """)
    assert out["beforeStatus"]["founderBubbles"] == 0 and out["beforeStatus"]["inflight"]
    assert out["polled"]["founderBubbles"] == 1
    assert out["polled"]["unconfirmedNotes"] == 1 and out["polled"]["resendButtons"] == 1
    assert out["polled"]["inflight"]["message"] == "hello"


def test_a_delivered_message_found_in_history_clears_the_stale_record(tmp_path, html):
    out = _run(tmp_path, html, _prior_page_record("continue") + r"""
    setQueueOwner("p-1");
    Owner.getConversation=async()=>({universe_id:"u-1", recent_conversation:{turns:[
      {speaker:"founder",text:"continue",ts:1700000100},
      {speaker:"universe",text:"Continuing.",ts:1700000101}]}});
    await loadHistory(); await pollStatus(); await settle();
    console.log(JSON.stringify(snapshot()));
    """)
    assert out["messages"] == [{"role": "founder", "text": "continue"},
                               {"role": "universe", "text": "Continuing."}]
    assert out["inflight"] is None and out["unconfirmedNotes"] == 0


# ---------------------------------------------------------------------------
# The account boundary: a retired turn's record becomes ordinary held state.
# ---------------------------------------------------------------------------

def test_an_account_change_retires_the_live_turn_and_fences_its_record(tmp_path, html):
    out = _run(tmp_path, html, r"""
    setQueueOwner("p-1");
    await loadHistory(); await pollStatus();
    const turn=sendTurn("account A private question");
    await settle();
    // Sign-out / other account on the same page: the page's own boundary code.
    // The shim logs bubbles separately from the thread it empties, so the log
    // is reset here to read what the NEXT account's screen shows.
    clearAccountScopedState(); MCP._loginEpoch++; messages.length=0;
    setQueueOwner("p-2"); setQueueScope("u-2");
    Owner.getStatus=async()=>({active_host:"h",universe_id:"u-2"});
    Owner.getConversation=async()=>({universe_id:"u-2", recent_conversation:{turns:[]}});
    await loadHistory(); await pollStatus(); await pollStatus(); await settle();
    const asB=snapshot();
    // The old turn's reply lands after the switch: it paints nothing.
    gates[0].resolve({reply:"account A private answer"}); await turn; await settle();
    const afterLateReply=snapshot();
    // Back as the original account on the same page: the retired turn's
    // message is unconfirmed and is offered - once.
    clearAccountScopedState(); MCP._loginEpoch++; messages.length=0;
    setQueueOwner("p-1"); setQueueScope("u-1");
    Owner.getStatus=async()=>({active_host:"h",universe_id:"u-1"});
    Owner.getConversation=async()=>({universe_id:"u-1", recent_conversation:{turns:[]}});
    await loadHistory(); await pollStatus(); await pollStatus(); await settle();
    console.log(JSON.stringify({asB, afterLateReply, backAsA:snapshot()}));
    """)
    as_b = out["asB"]
    assert as_b["founderBubbles"] == 0 and as_b["resendButtons"] == 0
    assert [n["text"] for n in as_b["notes"]] == [
        "An unconfirmed message from another universe's session on this browser "
        "is waiting there; open that universe to see it."]
    assert as_b["inflight"]["owner"] == "p-1", "the retired turn's record was erased"
    assert as_b["sendDisabled"] is False
    late = out["afterLateReply"]
    assert late["universeBubbles"] == 0, "the old account's reply painted under the new one"
    assert late["inflight"]["owner"] == "p-1"
    back = out["backAsA"]
    assert back["founderBubbles"] == 1 and back["unconfirmedNotes"] == 1
    assert back["resendButtons"] == 1
    assert back["converseCalls"] == ["account A private question"], "no silent resend"


# ---------------------------------------------------------------------------
# A late observation of a PREVIOUS page's request must not erase the record
# of a turn this page started while that observation was in flight.
# ---------------------------------------------------------------------------

def test_a_late_consumer_observation_keeps_the_newer_live_turns_record(tmp_path, html):
    request = {"version": 1, "request_key": "10cf4ccc-8528-48ae-a991-70cc35c1224f",
               "binding_id": "chosen", "binding_revision": 1}
    committed = {"reply": "Earlier answer", "consumer_turn": {
        "version": 1, "turn_id": "a" * 32, "run_id": "run", "state": "completed",
        "run_status": "completed", "projection": "committed"}}
    out = _run(tmp_path, html,
               _prior_page_record("earlier question", consumerRequest=request) + r"""
    setQueueOwner("p-1");
    let observe; MCP.callTool=(name,args)=>{ statusCalls.push({name,args});
      return new Promise(r=>{observe=r;}); };
    await loadHistory();                       // restoreInflight now awaits read_graph
    await settle();
    const turn=sendTurn("newer question");     // started while that await is pending
    await settle();
    observe(%s); await settle();
    const observed=snapshot();
    gates[0].resolve({reply:"Newer answer."}); await turn; await settle();
    console.log(JSON.stringify({observed, done:snapshot()}));
    """ % json.dumps(committed))
    obs = out["observed"]
    assert obs["messages"] == [{"role": "founder", "text": "newer question"},
                               {"role": "founder", "text": "earlier question"},
                               {"role": "universe", "text": "Earlier answer"}]
    assert obs["inflight"]["message"] == "newer question", \
        "the earlier request's outcome erased the live turn's recovery record"
    assert obs["resendButtons"] == 0 and obs["unconfirmedNotes"] == 0
    assert out["done"]["messages"][-1] == {"role": "universe", "text": "Newer answer."}
    assert out["done"]["inflight"] is None
    assert out["done"]["converseCalls"] == ["newer question"]


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
const silent = new Error("your universe stopped sending anything back");
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


def test_an_unconfirmed_turn_holds_the_queue_and_offers_a_read_only_check(tmp_path, html):
    """The failure the founder hit: a turn whose outcome nobody can confirm.

    Nothing queued behind it may be flushed -- the universe may be working on
    the first message right now, and the transport explicitly refused to say.
    The composer comes back, the local record survives, and the only thing on
    offer besides sending again is a READ: the existing idempotent conversation
    peek, labelled as an observation and attributed to nothing.
    """
    out = _run(tmp_path, html, _UNCONFIRMED_QUEUE)

    assert out["queuedBefore"] == 1, "the second line was supposed to queue"
    assert out["queuedAfter"] == 1, (
        "a queued message was auto-sent behind a turn whose outcome is unknown"
    )
    assert out["converseCalls"] == ["run the deploy"], "the queue drained itself"
    assert out["heldNote"], "the held queue was never mentioned to the user"

    # The composer is released by the turn's own owner-checked finally, and the
    # draft the founder was typing is still there.
    assert out["sendDisabled"] is False, "the composer stayed wedged"
    assert out["draft"] == "a draft I was typing"

    # An unconfirmed outcome keeps its local recovery record.
    assert out["inflight"] and out["inflight"]["message"] == "run the deploy"

    # The read-only action: one idempotent peek, no send, and no claim that any
    # saved turn is this message's answer.
    assert out["hadCheck"] is True, "no read-only way to look at saved progress"
    assert out["peeks"] == 2, "the check must reuse the existing history read once"
    assert "Saved conversation snapshot" in out["checkText"]
    assert "cannot tell which saved reply" in out["checkText"]
    assert out["savedTexts"] == ["an older line", "an older reply"]
    assert "Delivery could not be confirmed" in out["noteText"]
    assert out["converseCalls"] == ["run the deploy"], "the check sent something"


def test_a_new_manual_question_does_not_resume_held_commands(tmp_path, html):
    setup = _UNCONFIRMED_QUEUE.split("const afterFailure = snapshot();", 1)[0]
    out = _run(tmp_path, html, setup + r"""
const question=sendTurn("What finished?");await settle();
gates[1].resolve({reply:"Existing progress."});await question;await settle();
const beforeResume={calls:converseCalls.slice(),queued:sendQueue.length};
const resume=els.thread.children.filter(n=>!n.removed).flatMap(n=>n.children)
  .find(c=>c.tagName==="BUTTON"&&c.textContent==="Send queued messages");
if(!resume)throw new Error("No explicit resume control");
resume.click();await settle();
gates[2].resolve({reply:"Queued request completed."});await settle();
console.log(JSON.stringify({beforeResume,calls:converseCalls.slice(),queued:sendQueue.length}));
""")
    assert out["beforeResume"] == {"calls": ["run the deploy", "What finished?"], "queued": 1}
    assert out["calls"] == ["run the deploy", "What finished?", "and then tell me the result"]
    assert out["queued"] == 0


# ---------------------------------------------------------------------------
# Voice's stale "did not arrive" line is retired by the NEXT turn of the same
# account and home - the live sequence contained no resend at all.
# ---------------------------------------------------------------------------

def test_a_new_typed_turn_tells_voice_to_retire_the_older_turns_retry_line(tmp_path, html):
    """Live, 2026-09-23 PDT: the 17:39 turn failed at 17:46 and Voice said the
    pending reply did not arrive and the message was available to retry. The
    founder never clicked "Send it again" - they typed two NEW questions, at
    17:47 and 17:54, answered at 17:51 and 18:01, and the sentence was still
    there beside them. So the page's
    own sendTurn must tell Voice at the START of each new turn whose turn it is
    (owner and home, the same fence the reply uses), and hand it the settling
    turn's identity on the failure. Nothing here resends anything."""
    out = _run(tmp_path, html, r"""
    setQueueOwner("p-1");
    await loadHistory(); await pollStatus();
    const settled=[], started=[];
    Voice.conversationSettled=(delivered,record)=>settled.push({delivered,record});
    Voice.turnStarted=(owner,scope)=>started.push({owner,scope});
    // 17:39, failing at 17:46 - so Voice's retry sentence goes up.
    const failing=sendTurn("Retest my checklist");
    await settle();
    const e=new Error("offline"); e.transport=true; gates[0].reject(e);
    await failing; await settle();
    const afterFailure=snapshot();
    // 17:47 and 17:54 - NEW questions, typed from scratch, answered 17:51/18:01.
    const second=sendTurn("How is the progress going?"); await settle();
    gates[1].resolve({reply:"Two of three are done."}); await second; await settle();
    const third=sendTurn("Test cancellation for me"); await settle();
    gates[2].resolve({reply:"Cancellation works."}); await third; await settle();
    console.log(JSON.stringify({settled,started,afterFailure,done:snapshot(),
      resendClicks:0}));
    """)
    # The only resend offer on screen was never clicked: every send is an
    # original with its own text, exactly as the live sequence ran.
    assert out["afterFailure"]["resendButtons"] == 1
    assert out["done"]["converseCalls"] == [
        "Retest my checklist", "How is the progress going?", "Test cancellation for me"]
    assert len(set(out["done"]["converseCalls"])) == 3, "a message was sent twice"
    # Each new turn announces itself to Voice with the account and home fence.
    assert out["started"] == [{"owner": "p-1", "scope": "u-1"}] * 3
    # And the failed turn is the one Voice is told did not arrive.
    assert [s["delivered"] for s in out["settled"]] == [False, True, True]
    failed = out["settled"][0]["record"]
    assert failed["message"] == "Retest my checklist" and failed["ts"] > 0
    assert failed["owner"] == "p-1" and failed["scope"] == "u-1"
    assert failed["consumerRequest"] is None
    assert out["done"]["messages"][-1] == {"role": "universe", "text": "Cancellation works."}
    assert out["done"]["inflight"] is None and out["done"]["sendDisabled"] is False
