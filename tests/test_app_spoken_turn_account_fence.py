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

from tests.app_sheet_harness import sheet_source
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
    funcs = [sheet_source(html)] + [_js_function(html, f) for f in _FUNCS]
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














# ---------------------------------------------------------------------------
# The awaited callers: a late reply never reaches speech in another account.
# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------
# Same owner, same home: nothing of the intended recovery is lost.
# ---------------------------------------------------------------------------







