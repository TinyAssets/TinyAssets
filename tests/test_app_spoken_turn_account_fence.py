"""A spoken turn that outlives its account or home paints nothing there.

Source finding, 2026-09-20 (docs/concerns/2026-09-20-spoken-turn-account-
transition.md): `sendTurn` captures the login epoch, owner and home at send
time and checks them before painting a reply, offering a retry or settling
Voice. `sendVoiceTurn` had no such fence: a late failure offered the OLD
account's spoken text with a "Send it again" button under the NEW account, a
late auth failure signed the new account out, a late saved failure erased
whichever record was on disk, and `finally` always settled Voice.

Everything here EXECUTES the page's own `sendVoiceTurn` (and, for the awaited
callers, the page's own `Voice` object) under node against the DOM shim
`tests/test_onboarding_app.py` uses. Nothing is reimplemented. The account
boundary is driven the way `tests/test_app_live_turn_recovery.py` drives it:
the page's own `clearAccountScopedState`, a login epoch bump, and the new
pair - plus `Voice.stop`, which is what `enterSignedOut` calls.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from tests.test_app_live_turn_recovery import _DECLS, _EXTRA_SHIM, _FUNCS, _NODE, _OPTIONAL_FUNCS
from tests.test_onboarding_app import _APP_SHIM, _js_function
from tinyassets import onboarding

pytestmark = pytest.mark.skipif(
    _NODE is None, reason="node is required to execute the page's own source")

# The shim's two-line Voice stub is replaced by the page's OWN Voice object so
# the awaited callers (`_handleBrowserUtterance`, `handleToolCall`) and the
# account boundary's `Voice.stop` run for real. Media, rendering and speech
# output are recorded, not performed: no microphone, no synthesis, no bridge.
_VOICE_STUB = ('const Voice={isActive:()=>!!SCENARIO.voiceActive,'
               'conversationSettled:()=>{},turnStarted:()=>{}};')

_VOICE_SHIM = r"""
globalThis.window={};
els["voice-disclosure"]=new El("div");
const settled=[], spoken=[], failed=[], bridgeSent=[];
Voice._render=()=>{};
Voice._teardownTransport=function(){ this.browserTurnSerial++; };
Voice._speakBrowser=async(reply)=>{ spoken.push(reply); };
Voice.fail=function(error){
  failed.push(String(error&&error.message)); this.epoch++; this.state="error"; };
const realSettled=Voice.conversationSettled;
Voice.conversationSettled=function(delivered,record){
  settled.push(delivered); return realSettled.call(this,delivered,record); };
function setVoiceStatusLine(t){ els["status-line"].voice=t||""; }
// The account boundary as `enterSignedOut` performs it, in the same order:
// Voice stops (its own generation bump) BEFORE the page forgets the pair.
function accountBoundary(owner,scope){
  Voice.stop(false);
  MCP._loginEpoch++; clearAccountScopedState(); messages.length=0;
  setQueueOwner(owner); setQueueScope(scope);
  Owner.getStatus=async()=>({active_host:"h",universe_id:scope});
  Owner.getConversation=async()=>({universe_id:scope, recent_conversation:{turns:[]}});
}
function fence(){
  const live=els.thread.children.filter(n=>!n.removed);
  return Object.assign(snapshot(), {
    settled:settled.slice(), spoken:spoken.slice(), failed:failed.slice(),
    bridgeSent:bridgeSent.slice(),
    sessionExpired:messages.some(m=>m.role==="session-expired"),
    composer:els["composer-input"].value,
    voiceStatus:els["status-line"].voice||"",
    noteTexts:live.map(n=>n.textContent),
    turnStartedAt, voiceEpoch:Voice.epoch, voiceState:Voice.state,
  });
}
"""


def _program(html: str, body: str) -> str:
    decls = [m.group(0) for pat in _DECLS if (m := re.search(pat, html))]
    funcs = [_js_function(html, f) for f in _FUNCS]
    for name in _OPTIONAL_FUNCS:
        if re.search(r"function\s+" + name + r"\s*\(", html):
            funcs.append(_js_function(html, name))
    funcs.append(_js_function(html, "voiceFriendlyError"))
    voice = re.search(r"const Voice=\{.*?\n  \};", html, re.DOTALL)
    assert voice, "the page's Voice object was not found"
    head = _APP_SHIM.split("__APP_FUNCTIONS__", 1)[0]
    assert _VOICE_STUB in head, "the shim's Voice stub moved; update _VOICE_STUB"
    head = head.replace(_VOICE_STUB, "").replace("__SCENARIO__", json.dumps({}))
    tail = "\n})().catch(e=>{ console.error(e&&e.stack||e); process.exit(1); });"
    return (head + _EXTRA_SHIM + "\n".join(decls) + "\n" + "\n".join(funcs) + "\n" +
            voice.group(0) + "\n" + _VOICE_SHIM + "(async()=>{\n" + body + tail)


def _run(tmp_path, html: str, body: str) -> dict:
    script = tmp_path / "spoken_fence_case.js"
    script.write_text(_program(html, body), encoding="utf-8")
    proc = subprocess.run([_NODE, str(script)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60, env=dict(os.environ))
    assert proc.returncode == 0, f"app harness crashed:\n{proc.stderr}"
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def html() -> str:
    page, _csp = onboarding.render_app_html()
    return page


# A spoken turn of account A is in flight; B signs in on the same page; the
# old request then finishes in the way the scenario chooses.
_SPOKEN_THEN_SWITCH = r"""
setQueueOwner("p-1"); setQueueScope("u-1");
await loadHistory(); await pollStatus();
Voice.epoch=7; Voice.state="listening";
let outcome=null;
const turn=sendVoiceTurn("account A private spoken line")
  .then(reply=>{ outcome={reply}; }, error=>{ outcome={error:String(error&&error.message)}; });
await settle();
const inflightA=snapshot().inflight;
accountBoundary("p-2","u-2");
await loadHistory(); await pollStatus(); await settle();
const asB=fence();
"""


def _late(tmp_path, html, finish: str) -> dict:
    return _run(tmp_path, html, _SPOKEN_THEN_SWITCH + finish + r"""
    await turn; await settle();
    console.log(JSON.stringify({inflightA, asB, after:fence(), outcome}));
    """)


def _assert_nothing_painted_under_b(out: dict) -> None:
    before, after = out["asB"], out["after"]
    assert out["inflightA"]["owner"] == "p-1" and out["inflightA"]["inputMethod"] == "spoken"
    assert before["founderBubbles"] == 0 and before["resendButtons"] == 0
    assert after["messages"] == [], "the old account's spoken turn painted under the new one"
    assert after["resendButtons"] == 0, "a retry of the old spoken line was offered under B"
    assert after["noteTexts"] == before["noteTexts"], \
        "a notice about A's turn appeared on B's screen"
    assert after["sessionExpired"] is False, "A's late failure signed B out"
    assert after["inflight"]["owner"] == "p-1", "A's durable spoken record was erased"
    assert after["settled"] == [], "A's turn settled B's voice session"
    assert after["spoken"] == [] and after["bridgeSent"] == []
    assert after["sendDisabled"] is False and after["composer"] == ""
    assert after["converseCalls"] == ["account A private spoken line"], "the old line was re-sent"


def test_a_late_spoken_reply_after_an_account_change_paints_nothing(tmp_path, html):
    out = _late(tmp_path, html, r"""
    gates[0].resolve({reply:"account A private answer"});
    """)
    _assert_nothing_painted_under_b(out)
    assert out["after"]["universeBubbles"] == 0
    assert "reply" not in out["outcome"], "the old reply text was handed back to be spoken"


def test_a_late_spoken_transport_failure_after_an_account_change_offers_no_retry(tmp_path, html):
    out = _late(tmp_path, html, r"""
    const e=new Error("offline"); e.transport=true; gates[0].reject(e);
    """)
    _assert_nothing_painted_under_b(out)
    assert "error" in out["outcome"]


def test_a_late_spoken_auth_failure_after_an_account_change_signs_nobody_out(tmp_path, html):
    out = _late(tmp_path, html, r"""
    const e=new Error("unauthorized"); e.authRequired=true; gates[0].reject(e);
    """)
    _assert_nothing_painted_under_b(out)
    assert out["after"]["composer"] == "", "A's spoken text was put into B's composer"


def test_a_late_spoken_saved_failure_after_an_account_change_erases_no_record(tmp_path, html):
    out = _late(tmp_path, html, r"""
    gates[0].resolve({error:"The workflow failed.", history_saved:true,
      failure_notice:"The workflow failed.",
      turn_failure:{version:1, kind:"turn_failed", code:"workflow_failed"}});
    """)
    _assert_nothing_painted_under_b(out)


def test_a_late_spoken_reply_after_a_home_change_paints_nothing_and_speaks_nothing(tmp_path, html):
    """Same login, other home: the pair changed, so the turn is fenced the same
    way a typed one is. Voice is still running here (no sign-out stopped it),
    so the caller's own guard sees a retired turn and stops, speaking nothing."""
    out = _run(tmp_path, html, r"""
    setQueueOwner("p-1"); setQueueScope("u-1");
    await loadHistory(); await pollStatus();
    Voice.epoch=3; Voice.state="listening";
    const turn=Voice._handleBrowserUtterance("account A home one line",3,++Voice.browserTurnSerial);
    await settle();
    setQueueScope("u-other"); messages.length=0;
    gates[0].resolve({reply:"home one answer"}); await turn; await settle();
    console.log(JSON.stringify({after:fence(),
      friendly:voiceFriendlyError("voice_turn_retired")}));
    """)
    after = out["after"]
    assert after["messages"] == [] and after["resendButtons"] == 0
    assert after["spoken"] == [], "a reply from another home was spoken"
    assert after["inflight"]["scope"] == "u-1", "the other home's record was erased"
    assert after["settled"] == []
    assert after["failed"] == ["voice_turn_retired"], \
        "the caller did not learn the turn was retired"
    assert "changed" in out["friendly"] and "Typed chat still works" in out["friendly"]


def test_a_newer_turn_of_the_next_account_is_not_unlocked_by_the_old_spoken_turn(tmp_path, html):
    """B has already started a typed turn of its own when A's spoken turn
    finishes: A's cleanup must not hand B's composer back, flush B's queue or
    settle B's voice session."""
    out = _run(tmp_path, html, _SPOKEN_THEN_SWITCH + r"""
    Voice.epoch=20; Voice.state="listening"; Voice.stoppedWithPending=true;
    const turnB=sendTurn("account B own question");
    await settle();
    sendTurn("account B queued line"); await settle();
    const midB=fence();
    gates[0].resolve({reply:"account A private answer"}); await turn; await settle();
    const afterA=fence();
    gates[1].resolve({reply:"account B own answer"}); await turnB; await settle();
    console.log(JSON.stringify({midB, afterA, done:fence(), outcome}));
    """)
    mid, after_a, done = out["midB"], out["afterA"], out["done"]
    assert mid["sendDisabled"] is True
    assert mid["status"].startswith("Your agent is thinking")
    assert after_a["sendDisabled"] is True, "A's spoken turn re-enabled B's composer"
    assert after_a["status"] == mid["status"], "A's cleanup wiped B's status"
    assert after_a["turnStartedAt"] == mid["turnStartedAt"], "A's cleanup reset B's turn clock"
    assert after_a["activeTurnHeld"] is True
    assert after_a["converseCalls"] == [
        "account A private spoken line", "account B own question"], \
        "A's cleanup flushed B's queued line early"
    assert after_a["settled"] == [], "A's spoken turn settled B's voice session"
    assert after_a["voiceEpoch"] == 20 and after_a["voiceState"] == "listening"
    assert after_a["inflight"]["owner"] == "p-2", "A's turn erased B's live record"
    assert after_a["messages"] == [{"role": "founder", "text": "account B own question"},
                                   {"role": "founder", "text": "account B queued line"}]
    assert done["messages"][-1] == {"role": "universe", "text": "account B own answer"}
    assert done["converseCalls"][-1] == "account B queued line"
    assert done["settled"] == [True], "B's own turn no longer settles B's voice"


# ---------------------------------------------------------------------------
# The awaited callers: a late reply never reaches speech in another account.
# ---------------------------------------------------------------------------

def test_the_browser_voice_caller_speaks_nothing_after_the_account_boundary(tmp_path, html):
    out = _run(tmp_path, html, r"""
    setQueueOwner("p-1"); setQueueScope("u-1");
    await loadHistory(); await pollStatus();
    Voice.epoch=5; Voice.state="listening";
    const turn=Voice._handleBrowserUtterance("account A spoken",5,++Voice.browserTurnSerial);
    await settle();
    accountBoundary("p-2","u-2");
    const epochAfterStop=Voice.epoch;
    gates[0].resolve({reply:"account A private answer"}); await turn; await settle();
    console.log(JSON.stringify({epochAfterStop, after:fence()}));
    """)
    after = out["after"]
    assert out["epochAfterStop"] == 6, "enterSignedOut's Voice.stop no longer retires the session"
    assert after["spoken"] == [], "A's reply was spoken after B signed in"
    assert after["failed"] == [], "A's retired turn put B's voice into an error state"
    assert after["messages"] == [] and after["settled"] == []


def test_the_bridge_voice_caller_sends_nothing_after_the_account_boundary(tmp_path, html):
    out = _run(tmp_path, html, r"""
    setQueueOwner("p-1"); setQueueScope("u-1");
    await loadHistory(); await pollStatus();
    Voice.epoch=9; Voice.state="listening";
    Voice.dc={readyState:"open", send:v=>bridgeSent.push(JSON.parse(v))};
    Voice.audio={muted:true};
    const call=Voice.handleToolCall({type:"tool_call",call_id:"c1",name:"converse",
      arguments:JSON.stringify({message:"account A spoken"})});
    await settle();
    accountBoundary("p-2","u-2");
    Voice.dc={readyState:"open", send:v=>bridgeSent.push(JSON.parse(v))};
    gates[0].resolve({reply:"account A private answer"}); await call; await settle();
    console.log(JSON.stringify({after:fence(), pending:Voice.canonicalResponsePending,
      expected:Voice.expectedReply}));
    """)
    assert out["after"]["bridgeSent"] == [], "A's reply was handed to B's voice bridge"
    assert out["pending"] is False and out["expected"] == ""
    assert out["after"]["failed"] == [] and out["after"]["messages"] == []


# ---------------------------------------------------------------------------
# Same owner, same home: nothing of the intended recovery is lost.
# ---------------------------------------------------------------------------

def test_a_same_owner_spoken_reply_still_renders_and_settles_voice(tmp_path, html):
    out = _run(tmp_path, html, r"""
    setQueueOwner("p-1"); setQueueScope("u-1");
    await loadHistory(); await pollStatus();
    Voice.epoch=2; Voice.state="listening";
    const turn=Voice._handleBrowserUtterance("read me the checklist",2,++Voice.browserTurnSerial);
    await settle();
    gates[0].resolve({reply:"Reading it now."}); await turn; await settle();
    console.log(JSON.stringify(fence()));
    """)
    assert out["messages"] == [{"role": "founder", "text": "read me the checklist"},
                               {"role": "universe", "text": "Reading it now."}]
    assert out["spoken"] == ["Reading it now."], "the same owner's reply was not spoken"
    assert out["inflight"] is None and out["sendDisabled"] is False
    assert out["settled"] == [True] and out["failed"] == []


def test_a_same_owner_spoken_failure_still_offers_the_retry_once(tmp_path, html):
    out = _run(tmp_path, html, r"""
    setQueueOwner("p-1"); setQueueScope("u-1");
    await loadHistory(); await pollStatus();
    Voice.stoppedWithPending=true;                 // Voice was stopped mid-turn
    const turn=sendVoiceTurn("deploy the fix").catch(e=>String(e.message));
    await settle();
    const e=new Error("offline"); e.transport=true; gates[0].reject(e);
    const error=await turn; await settle();
    const failed=fence();
    const btn=els.thread.children.filter(n=>!n.removed).flatMap(n=>n.children)
      .find(c=>c.tagName==="BUTTON"&&c.textContent==="Send it again");
    btn.click(); await settle();
    gates[1].resolve({reply:"Deployed."}); await settle();
    console.log(JSON.stringify({error, failed, done:fence()}));
    """)
    failed, done = out["failed"], out["done"]
    assert out["error"] == "offline"
    assert failed["founderBubbles"] == 1 and failed["resendButtons"] == 1
    assert failed["inflight"]["message"] == "deploy the fix"
    assert failed["sendDisabled"] is False
    assert failed["settled"] == [False]
    assert "available to retry" in failed["voiceStatus"]
    assert done["converseCalls"] == ["deploy the fix", "deploy the fix"]
    assert done["messages"][-1] == {"role": "universe", "text": "Deployed."}
    assert done["inflight"] is None


def test_a_same_owner_spoken_saved_failure_still_clears_the_record(tmp_path, html):
    out = _run(tmp_path, html, r"""
    setQueueOwner("p-1"); setQueueScope("u-1");
    await loadHistory(); await pollStatus();
    const turn=sendVoiceTurn("run the report").catch(e=>String(e.message));
    await settle();
    gates[0].resolve({error:"The workflow failed.", history_saved:true,
      failure_notice:"The workflow failed.",
      turn_failure:{version:1, kind:"turn_failed", code:"workflow_failed"}});
    const error=await turn; await settle();
    console.log(JSON.stringify({error, done:fence()}));
    """)
    assert out["error"].startswith("The workflow failed.")
    assert out["done"]["inflight"] is None, "a saved failure left a record to be re-offered"
    # The saved-failure notice keeps its own "Send it again" (a NEW turn).
    assert out["done"]["resendButtons"] == 1 and out["done"]["sendDisabled"] is False
    assert out["done"]["settled"] == [False]


def test_the_original_owner_is_still_offered_the_retired_spoken_turn(tmp_path, html):
    """A's spoken turn was retired by B's sign-in and its reply never landed
    on A's screen. Back as A on the same page, the record is offered once -
    and never re-sent by itself."""
    out = _late(tmp_path, html, r"""
    const e=new Error("offline"); e.transport=true; gates[0].reject(e);
    """)
    _assert_nothing_painted_under_b(out)
    back = _run(tmp_path, html, _SPOKEN_THEN_SWITCH + r"""
    const e=new Error("offline"); e.transport=true; gates[0].reject(e);
    await turn; await settle();
    accountBoundary("p-1","u-1");
    await loadHistory(); await pollStatus(); await pollStatus(); await settle();
    console.log(JSON.stringify(fence()));
    """)
    assert back["founderBubbles"] == 1 and back["unconfirmedNotes"] == 1
    assert back["resendButtons"] == 1
    assert back["inflight"]["message"] == "account A private spoken line"
    assert back["converseCalls"] == ["account A private spoken line"], "no silent resend"
