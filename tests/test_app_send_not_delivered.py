"""A send that did not reach the universe must say so, and must not vanish.

Live, 2026-09-24 18:31-18:39 UTC, founder account, Chrome: a tab resumed after
the machine had slept since 07:22 UTC (its last token renewal in the daemon
access log). Two typed sends showed no reply and nothing was delivered. The
browser logged ``POST /mcp`` 503s, and the daemon logged no 5xx. In the droplet
log, the tab's first renewal after the sleep (``POST /app/token``) appears
only at the 18:33:38 reload, so its requests between the resume and that reload
never reached the droplet. The 503 was authored in front of the origin.

This file pins what the page does with those failures. Each test EXECUTES the
page's own source under node, the transport against a scripted ``fetch`` and
the UI against the DOM shim the other app tests use:

* A renewal that failed because the renewal endpoint was UNAVAILABLE (5xx,
  network) is not an ended sign-in. Treating it as one called
  ``sessionExpired()``, and ``enterSignedOut`` then removed the bubble AND
  emptied the composer, under a notice claiming "anything you typed is still in
  the box".
* A call that failed BEFORE the tool request was sent (handshake down, renewal
  down, session disowned twice) provably did not run, so it says "Not sent"
  instead of "may already have acted".
* A 5xx on the tool request itself stays UNCONFIRMED (never replayed), and it
  now carries what the edge said (status, ``cf-ray``, ``X-TA-Origin-Status``,
  a body sample) so the next occurrence can be attributed.
* A build-check reload that was cleared to run before a send started must not
  fire after it. That reload is the other path that silently empties the
  composer and the thread mid-turn.
"""
from __future__ import annotations

import json
import re
import subprocess

import pytest

from tests import test_app_live_turn_recovery as live
from tests import test_onboarding_mcp_session_recovery as transport
from tests.test_onboarding_app import _APP_SHIM, _js_function
from tinyassets import onboarding


@pytest.fixture(scope="module")
def html() -> str:
    page, _csp = onboarding.render_app_html()
    return page


# ---------------------------------------------------------------------------
# Transport: the real MCP client block against a scripted wire.
# ---------------------------------------------------------------------------

_STUB_REFRESH = (
    "async function refreshAccessToken(){ refreshes++; MCP.invalidateSession(); return true; }")
_ERROR_FIELDS = "authRequired: !!(err && err.authRequired),"


def _drive(tmp_path, plan, call, refresh_result="true", session="null"):
    harness = transport._HARNESS
    assert _STUB_REFRESH in harness and _ERROR_FIELDS in harness, "transport harness moved"
    harness = harness.replace(
        _STUB_REFRESH,
        # The renewal outcome is the scenario's: true (renewed), false (the
        # sign-in ended) or null (the renewal endpoint could not answer). The
        # contract for null is pinned against the REAL function further down.
        "async function refreshAccessToken(){ refreshes++; "
        "const r=(" + refresh_result + "); if(r) MCP.invalidateSession(); return r; }",
    ).replace(
        _ERROR_FIELDS,
        _ERROR_FIELDS + " notSent: !!(err && err.notSent), http: (err && err.http) || null,",
    )
    program = (harness.replace("__PLAN__", json.dumps(plan))
               .replace("__TRANSPORT__", transport._transport_source())
               .replace("__CALL__", "(MCP.sessionId=" + session + ", " + call + ")"))
    script = tmp_path / "send_case.js"
    script.write_text(program, encoding="utf-8")
    proc = subprocess.run([transport._node(), str(script)], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, f"harness crashed:\n{proc.stderr}"
    return json.loads(proc.stdout)


_CONVERSE = 'MCP.callTool("converse",{message:"Retest your workflow checklist"})'
_INIT_OK = {"status": 200, "headers": {"content-type": "application/json",
                                       "mcp-session-id": "s2"},
            "body": '{"jsonrpc":"2.0","id":"__ECHO_ID__","result":{}}'}
_NOTIFIED = {"status": 202, "body": ""}
_EDGE_503 = {"status": 503, "headers": {"content-type": "text/html", "cf-ray": "a403ed64-SEA"},
             "body": "<html><title>503 Service Temporarily Unavailable</title></html>"}


def _tool_calls(out):
    return [s for s in out["sent"] if s["method"] == "tools/call"]


def test_unavailable_renewal_on_a_401_is_not_a_sign_out(tmp_path):
    """A 401 is refused before the tool runs; a renewal endpoint answering 5xx
    has not ended the sign-in. The page must not sign the user out over it."""
    out = _drive(tmp_path, [{"status": 401, "body": ""}], _CONVERSE,
                 refresh_result="null", session='"s1"')
    assert out["ok"] is False
    assert out["error"]["authRequired"] is False, \
        "a renewal that could not be reached signed the user out"
    assert out["error"]["transport"] == "auth_unavailable"
    assert out["error"]["notSent"] is True, "a 401 is pre-dispatch: nothing ran"
    assert len(_tool_calls(out)) == 1, "the refused turn was replayed"


def test_a_renewal_the_server_refused_still_requires_sign_in(tmp_path):
    out = _drive(tmp_path, [{"status": 401, "body": ""}], _CONVERSE,
                 refresh_result="false", session='"s1"')
    assert out["error"]["authRequired"] is True


def test_a_handshake_that_never_completed_marks_the_turn_not_sent(tmp_path):
    """The live resume: no session, and every initialize answers 503. The
    converse request itself is never put on the wire."""
    out = _drive(tmp_path, [_EDGE_503] * 4, _CONVERSE)
    assert out["ok"] is False
    assert _tool_calls(out) == [], "the turn went out without a session"
    assert out["error"]["notSent"] is True, \
        "a turn that never left the browser was reported as possibly delivered"


def test_a_session_disowned_twice_is_not_sent(tmp_path):
    gone = {"status": 404, "headers": {"content-type": "application/json"},
            "body": '{"jsonrpc":"2.0","id":"server-error",'
                    '"error":{"code":-32600,"message":"Session not found"}}'}
    out = _drive(tmp_path, [gone, _INIT_OK, _NOTIFIED, gone], _CONVERSE, session='"s1"')
    assert out["error"]["transport"] == "session_lost"
    assert out["error"]["notSent"] is True


def test_a_5xx_on_the_turn_itself_stays_unconfirmed_and_says_what_the_edge_said(tmp_path):
    out = _drive(tmp_path, [_EDGE_503], _CONVERSE, session='"s1"')
    err = out["error"]
    assert err["transport"] == "unavailable"
    assert err["notSent"] is False, "a 5xx on the turn itself cannot prove it never ran"
    assert len(_tool_calls(out)) == 1, "an unconfirmed turn was replayed"
    assert err["http"] == {
        "status": 503, "ray": "a403ed64-SEA", "originStatus": None,
        "contentType": "text/html",
        "body": "<html><title>503 Service Temporarily Unavailable</title></html>"}
    assert "HTTP 503" in err["message"] and "a403ed64-SEA" in err["message"]
    assert "restarting" not in err["message"], \
        "an edge 503 was diagnosed as the universe restarting"


def test_an_idempotent_read_still_retries_a_5xx_once(tmp_path):
    ok = {"status": 200, "headers": {"content-type": "application/json"},
          "body": '{"jsonrpc":"2.0","id":"__ECHO_ID__","result":{"structuredContent":{"a":1}}}'}
    out = _drive(tmp_path, [_EDGE_503, _INIT_OK, _NOTIFIED, ok],
                 'MCP.callTool("get_status",{},{idempotent:true})', session='"s1"')
    assert out["ok"] is True


# ---------------------------------------------------------------------------
# The REAL renewal: which failures mean "signed out" and which mean "not now".
# ---------------------------------------------------------------------------

_REFRESH_PROGRAM = r"""
const store={}; const localStorage={getItem:k=>(k in store?store[k]:null),
  setItem:(k,v)=>{store[k]=String(v);}, removeItem:k=>{delete store[k];}};
const sessionStorage={getItem:()=>null,setItem(){},removeItem(){}};
const navigator={};
let invalidated=0; const MCP={invalidateSession(){ invalidated++; }};
function storeAccessToken(){} function storeSessionRef(){} function sessionRef(){ return "sref"; }
async function endServerSession(){ return true; }
let MODE=null;
async function fetch(){
  if(MODE==="throw") throw new TypeError("Failed to fetch");
  if(MODE==="html200") return {ok:true,status:200,json:async()=>{ throw new SyntaxError("<"); }};
  return {ok:MODE>=200&&MODE<300, status:MODE, json:async()=>({access_token:"t",expires_in:300})};
}
__DECLS__
__FUNCS__
(async()=>{
  const out={};
  for(const m of [200,400,401,403,429,500,502,503,"throw","html200"]){
    MODE=m; out[String(m)]=await refreshAccessToken();
  }
  console.log(JSON.stringify(out));
})().catch(e=>{ console.error(e&&e.stack||e); process.exit(1); });
"""


def test_the_real_renewal_separates_unavailable_from_ended(tmp_path, html):
    decls = "\n".join(re.search(p, html).group(0)
                      for p in (r"let refreshing = [^\n]*;", r"let logoutPending = [^\n]*;"))
    funcs = "\n".join(_js_function(html, f) for f in ("withRefreshLock", "refreshAccessToken"))
    script = tmp_path / "refresh_case.js"
    script.write_text(_REFRESH_PROGRAM.replace("__DECLS__", decls).replace("__FUNCS__", funcs),
                      encoding="utf-8")
    proc = subprocess.run([transport._node(), str(script)], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["200"] is True
    # The server looked at the handle and said no: the sign-in has ended.
    assert out["400"] is False and out["401"] is False and out["403"] is False
    # Nobody said no. The endpoint could not answer, so it is not a sign-out.
    for unavailable in ("429", "500", "502", "503", "throw", "html200"):
        assert out[unavailable] is None, f"{unavailable} was reported as an ended sign-in"


# ---------------------------------------------------------------------------
# UI: the page's own sendTurn / offerResend / sessionExpired / checkForNewBuild.
# ---------------------------------------------------------------------------

def _real_converse(html: str) -> str:
    """The page's own ``MCP.converse`` method, as a plain function.

    Installed as ``MCP.converse`` so a failure scripted at ``MCP.callTool`` (the
    layer the transport tags) travels through the real converse wrapper, the
    real ``sendConversationRequest`` and the real ``sendTurn``.
    """
    at = html.index("    converse(message,inputMethod")
    return _js_function("function " + html[at:].lstrip(), "converse")


def _ui(tmp_path, html, body, extra_funcs=()):
    funcs = list(live._FUNCS) + list(extra_funcs)
    decls = [m.group(0) for m in (re.search(p, html) for p in live._DECLS) if m]
    srcs = [_js_function(html, f) for f in funcs] + [_real_converse(html)]
    for name in live._OPTIONAL_FUNCS:
        if re.search(r"function\s+" + name + r"\s*\(", html):
            srcs.append(_js_function(html, name))
    head = _APP_SHIM.split("__APP_FUNCTIONS__", 1)[0].replace("__SCENARIO__", "{}")
    program = (head + live._EXTRA_SHIM + "\n".join(decls) + "\n" + "\n".join(srcs) +
               "\n(async()=>{\nsetQueueOwner('p-1'); setQueueScope('u-1');\n" + body +
               "\n})().catch(e=>{ console.error(e&&e.stack||e); process.exit(1); });")
    script = tmp_path / "ui_case.js"
    script.write_text(program, encoding="utf-8")
    proc = subprocess.run([transport._node(), str(script)], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, f"app harness crashed:\n{proc.stderr}"
    return json.loads(proc.stdout)


_FAIL_SEND = r"""
MCP.converse=converse;
MCP.callTool=async()=>{ const e=new Error(__MESSAGE__); e.transport=__CODE__;
  if(__NOT_SENT__) e.notSent=true; throw e; };
await sendTurn("Retest your workflow checklist",undefined,{inputMethod:"typed"});
await settle();
console.log(JSON.stringify(snapshot()));
"""


def _fail_send(tmp_path, html, message, code, not_sent):
    return _ui(tmp_path, html, _FAIL_SEND.replace("__MESSAGE__", json.dumps(message))
               .replace("__CODE__", json.dumps(code))
               .replace("__NOT_SENT__", "true" if not_sent else "false"))


def test_a_send_that_never_left_says_not_sent_and_keeps_the_text(tmp_path, html):
    out = _fail_send(tmp_path, html, "couldn’t reach your universe just then",
                     "unavailable", True)
    assert out["founderBubbles"] == 1, "the typed message disappeared"
    [note] = [n for n in out["notes"] if n["cls"] == "msg msg--system"]
    assert note["text"].startswith("Not sent"), note["text"]
    assert "may already have acted" not in note["text"]
    assert note["buttons"] == ["Send again"], \
        "a turn that provably never ran needs no progress check, only a resend"
    assert out["inflight"]["message"] == "Retest your workflow checklist", \
        "the unsent text must survive a reload"
    assert out["sendDisabled"] is False


def test_an_unconfirmed_send_keeps_its_cautious_notice(tmp_path, html):
    out = _fail_send(tmp_path, html, "the connection to your universe failed (HTTP 503)",
                     "unavailable", False)
    [note] = [n for n in out["notes"] if n["cls"] == "msg msg--system"]
    assert note["text"].startswith("Delivery could not be confirmed")
    assert note["buttons"] == ["Check saved conversation", "Dismiss"]


def test_a_sign_out_during_a_send_does_not_claim_the_text_is_in_the_box(tmp_path, html):
    """The real sessionExpired + the real composer clear enterSignedOut runs."""
    out = _ui(tmp_path, html, r"""
    els["signin-notice"]=new El("div");
    document.getElementById=id=>els[id];
    enterSignedOut=()=>{ clearComposerState(); };
    MCP.converse=async()=>{ const e=new Error("authentication_required");
      e.authRequired=true; throw e; };
    els["composer-input"].value="Retest your workflow checklist";
    await sendTurn("Retest your workflow checklist",undefined,{inputMethod:"typed"});
    const composer=els["composer-input"].value, notice=els["signin-notice"].textContent;
    const inflight=JSON.parse(localStorage.getItem(INFLIGHT_KEY)||"null");
    sessionExpired();
    console.log(JSON.stringify({composer, notice, inflight,
      idleNotice: els["signin-notice"].textContent}));
    """, extra_funcs=("sessionExpired",))
    for text in (out["notice"], out["idleNotice"]):
        assert "still in the box" not in text or out["composer"], \
            "the sign-in notice promises typed text the sign-out just erased"
    assert out["inflight"]["message"] == "Retest your workflow checklist"
    assert "offered" in out["notice"], \
        "the held message must be named, since it comes back only after sign-in"


def test_a_build_reload_cleared_before_a_send_does_not_fire_during_it(tmp_path, html):
    out = _ui(tmp_path, html, r"""
    let release; fetch=()=>new Promise(r=>{ release=r; });
    const check=checkForNewBuild();          // the tab just became visible: nothing held
    await settle();
    els["btn-send"].disabled=true; turnStartedAt=Date.now();   // then the founder sends
    release({headers:{get:()=>"b2"}});       // and the slow HEAD finally lands
    await check;
    console.log(JSON.stringify({reloaded}));
    """, extra_funcs=("checkForNewBuild",))
    assert out["reloaded"] is False, "a new-build reload wiped a turn in flight"


def test_a_failed_poll_after_an_accepted_conversation_turn_is_not_called_unsent(tmp_path, html):
    """Review finding on #3948: once a chosen-conversation `converse` has been
    ACCEPTED (a pending turn came back), a later poll whose handshake fails is
    not proof of anything about the turn. It is running. The page must keep
    "Check this conversation" and hold what is queued behind it."""
    out = _ui(tmp_path, html, r"""
    MCP.converse=converse;
    const calls=[];
    MCP.callTool=async(name,args)=>{
      calls.push({name, target:args.target||null, consumer:!!args.consumer_request,
        message:args.message||null});
      if(name==="converse"&&!args.consumer_request) return {error:"consumer_request_required",
        consumer_selection:{version:1,binding_id:"binding-1",binding_revision:3}};
      if(name==="converse") return {consumer_turn:{turn_id:"turn-1",state:"pending"}};
      // The poll: its session is gone and the rebuild fails, exactly as the
      // transport reports a handshake that never completed.
      const e=new Error("the connection to your universe failed (HTTP 503)");
      e.transport="unavailable"; e.notSent=true; throw e;
    };
    const turn=sendTurn("Retest your workflow checklist",undefined,{inputMethod:"typed"});
    await settle();
    sendTurn("and then the next item",undefined,{inputMethod:"typed"});   // queued behind it
    await turn; await settle();
    const snap=snapshot();
    console.log(JSON.stringify({snap, calls, queued:sendQueue.length, held:sendQueueHeld}));
    """)
    snap = out["snap"]
    assert [c["name"] for c in out["calls"][:3]] == ["converse", "converse", "read_graph"]
    notes = [n for n in snap["notes"] if n["cls"] == "msg msg--system"]
    assert notes, "the failure was not reported at all"
    note = notes[0]
    assert "nothing ran" not in note["text"] and not note["text"].startswith("Not sent"), \
        "an accepted, running turn was reported as never sent"
    assert "Send again" not in note["buttons"]
    assert note["buttons"][0] == "Check this conversation"
    assert out["queued"] == 1 and out["held"] is True, \
        "a line queued behind a still-running turn was released"
    assert len(notes) == 2 and "being held, not sent" in notes[1]["text"], \
        "the held queue must be announced"
    assert not any(c["message"] == "and then the next item" for c in out["calls"])
    assert len(out["calls"]) == 3
