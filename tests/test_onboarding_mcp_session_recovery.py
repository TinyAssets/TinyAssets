"""The onboarding app's MCP client must recover from a dead session.

The session id is a server-side handle that dies for ordinary reasons: the
daemon restarts on every deploy, the bearer rotates every ~5 min, an idle
session is evicted, a stream is cut mid-flight. Until 2026-08-28 only ONE of
those (HTTP 404) reset it client-side, so any other failure left the dead handle
in place and every later call reused it -- the page stayed broken until a manual
reload, surfacing the parser internal ``no JSON or SSE frame`` each time.
Founder, 2026-08-28: *"the mcp session going stale and giving the json issue is
a bug to fix"*.

These are BEHAVIOURAL tests, not string tripwires. They extract the real
transport source from ``app.html`` and drive it in Node against a scripted
``fetch``, so they assert what the client actually does -- which requests it
sends, what it retries, and what it leaves behind -- and can genuinely go red.

The second invariant they guard is the one that makes recovery safe: a call is
replayed automatically ONLY when it provably never ran. A session rejection is
pre-dispatch (the transport refused the envelope), so replaying is free. A cut
stream is NOT -- the universe may have taken the turn and be answering it -- so
those surface to the user instead of being silently double-sent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import tinyassets.onboarding as onboarding

_APP_HTML = Path(onboarding.__file__).parent / "app.html"


def _transport_source() -> str:
    """The real MCP client source, lifted from the page it ships in."""
    html = _APP_HTML.read_text(encoding="utf-8")
    head = "// ---- MCP client"
    tail = "// ---- UI ----"
    assert head in html and tail in html, "MCP client block markers moved"
    return head + html.split(head, 1)[1].split(tail, 1)[0]


_HARNESS = """
'use strict';
// Scripted transport. Each entry is one response, consumed in order; `null`
// means "the connection failed before any response" (offline / dropped).
const PLAN = __PLAN__;
const SENT = [];
let refreshes = 0;
let bearer = "test-bearer";

function token(){ return bearer; }
async function ensureFreshToken(){ }
// Mirrors the real refreshAccessToken's effect on the session. The real one
// lives OUTSIDE the extracted transport block, so the behavioural tests can only
// prove that this effect produces the right outcome; a companion structural test
// pins the real call site to the same call.
async function refreshAccessToken(){ refreshes++; MCP.invalidateSession(); return true; }

// Every scripted response echoes the id of the request it is answering. The
// client correlates a terminal frame by that id, so a fixture pinned to a
// literal id would answer only the first request of a case by accident. A
// fixture that WANTS to be somebody else's answer sets `foreignId`.
function echoId(body, requestId){
  const target = requestId === null || requestId === undefined ? null : requestId;
  return String(body === undefined ? "" : body)
    .replace(/"__ECHO_ID__"/g, JSON.stringify(target));
}

function makeResponse(spec, requestId){
  const headers = spec.headers || {};
  const answering = spec.foreignId !== undefined ? spec.foreignId : requestId;
  return {
    status: spec.status,
    ok: spec.status >= 200 && spec.status < 300,
    headers: {get(name){ return headers[String(name).toLowerCase()] || null; }},
    async text(){
      // `delayMs` holds the BODY open while the response headers have already
      // landed - exactly the shape of a slow turn, and the only way to get two
      // calls genuinely in flight against one shared session.
      if(spec.delayMs) await new Promise(r=>setTimeout(r, spec.delayMs));
      if(spec.readFails) throw new Error("stream closed");
      return echoId(spec.body, answering);
    },
  };
}

async function fetch(url, init){
  const frame = JSON.parse(init.body);
  SENT.push({
    method: frame.method,
    tool: (frame.params && frame.params.name) || null,
    sessionId: init.headers["mcp-session-id"] || null,
    bearer: init.headers["Authorization"] || null,
  });
  const spec = PLAN.shift();
  if(spec === undefined) throw new Error("harness ran out of scripted responses");
  if(spec === null) throw new TypeError("Failed to fetch");
  // `headersDelayMs` holds the RESPONSE ITSELF back, not just its body. The
  // session id is adopted the moment the headers land, so this is the only way
  // to place that adoption AFTER a concurrent call has already rebuilt the
  // session - which is the race being tested. The queue is consumed at call
  // time (above), so request ORDER stays deterministic regardless.
  if(spec.headersDelayMs) await new Promise(r=>setTimeout(r, spec.headersDelayMs));
  return makeResponse(spec, frame.id === undefined ? null : frame.id);
}

__TRANSPORT__

// Recovery pauses are real seconds in production; here they only slow the test.
MCP._pause = function(){ return Promise.resolve(); };

// A wedged client is a real failure mode (awaiting the promise you are inside),
// and it must read as a clean assertion rather than a harness timeout.
const DEADLOCK = Symbol("deadlock");
function guard(promise){
  let timer;
  return Promise.race([
    promise,
    new Promise(r=>{timer=setTimeout(()=>r(DEADLOCK), 5000);}),
  ]).finally(()=>clearTimeout(timer));
}

(async () => {
  const out = {sent: SENT, refreshes: 0, sessionIdAfter: null};
  try{
    const settled = await guard(Promise.resolve().then(()=>(__CALL__)));
    if(settled === DEADLOCK){
      out.ok = false;
      out.error = {message: "client wedged - never settled", transport: "DEADLOCK",
                   replayable: false, authRequired: false};
      out.refreshes = refreshes;
      out.sessionIdAfter = MCP.sessionId;
      out.unusedResponses = PLAN.length;
      console.log(JSON.stringify(out));
      return;
    }
    out.result = settled;
    out.ok = true;
  }catch(err){
    out.ok = false;
    out.error = {
      message: String(err && err.message),
      transport: (err && err.transport) || null,
      replayable: !!(err && err.replayable),
      authRequired: !!(err && err.authRequired),
    };
  }
  out.refreshes = refreshes;
  out.sessionIdAfter = MCP.sessionId;
  out.unusedResponses = PLAN.length;
  console.log(JSON.stringify(out));
})();
"""


def _node() -> str:
    node = shutil.which("node")
    if not node:  # pragma: no cover - environment dependent
        # Fail loudly rather than skip: these are the only tests that prove the
        # client actually recovers, and the behaviour is JavaScript. A silent
        # skip would let the stale-session regression land unnoticed.
        if os.environ.get("TINYASSETS_SKIP_JS_PROBE_TESTS"):
            pytest.skip("node absent; skip explicitly requested via env")
        pytest.fail(
            "node executable not found -- the MCP session-recovery behaviour is "
            "JavaScript and cannot be verified without it. Install Node, or set "
            "TINYASSETS_SKIP_JS_PROBE_TESTS=1 to accept the coverage gap."
        )
    return node


def _drive(tmp_path, plan, call):
    """Run ``call`` against the real client with ``plan`` as the wire."""
    program = (
        _HARNESS.replace("__PLAN__", json.dumps(plan))
        .replace("__TRANSPORT__", _transport_source())
        .replace("__CALL__", call)
    )
    script = tmp_path / "session_case.js"
    script.write_text(program, encoding="utf-8")
    proc = subprocess.run(
        [_node(), str(script)], capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"harness crashed:\n{proc.stderr}"
    return json.loads(proc.stdout)


@pytest.mark.parametrize("wire_case", ["open_sse", "wrong_id_json"])
def test_response_reader_correlates_terminal_frame_without_waiting_for_eof(tmp_path, wire_case):
    """Actual Fetch streams: a terminal frame is sufficient; a foreign id is not.

    The open-stream case deliberately never closes the connection. Notifications
    and an unrelated response must not become this turn's answer. The short
    guard is a test liveness assertion, not a proposed production timeout.
    """
    script = tmp_path / "terminal_frame.js"
    program = r"""
const assert = require('node:assert/strict');
function token(){return 'test-bearer';}
async function ensureFreshToken(){}
async function refreshAccessToken(){throw new Error('unexpected refresh');}
let calls=0, cancelled=false;
const wireCase=__CASE__;
async function fetch(_url, init){
  calls++;
  const request=JSON.parse(init.body);
  const response=id=>({jsonrpc:'2.0',id,result:{structuredContent:{reply:'finished'}}});
  if(wireCase==='wrong_id_json')return new Response(JSON.stringify(response(request.id+1)),
    {headers:{'content-type':'application/json'}});
  const encoder=new TextEncoder();
  const stream=new ReadableStream({
    start(controller){
      const frames=[
        ': keepalive\n\n',
        'data: '+JSON.stringify({jsonrpc:'2.0',method:'notifications/progress',
          params:{progress:1}})+'\n\n',
        'data: '+JSON.stringify(response(request.id+1))+'\n\n',
        'data: '+JSON.stringify(response(request.id))+'\n\n'
      ];
      // Split both the SSE prefix and JSON token across chunks.
      for(const frame of frames){
        controller.enqueue(encoder.encode(frame.slice(0,7)));
        controller.enqueue(encoder.encode(frame.slice(7)));
      }
    },cancel(){cancelled=true;}
  });
  return new Response(stream,{headers:{'content-type':'text/event-stream'}});
}
__TRANSPORT__
MCP.sessionId='existing-session';
(async()=>{
  let timer;
  try{
    const operation=MCP.converse('report existing progress');
    const bounded=Promise.race([operation,new Promise((_,reject)=>{
      timer=setTimeout(()=>reject(new Error('reader still waiting for EOF')),250);
    })]);
    if(wireCase==='wrong_id_json')await assert.rejects(bounded,
      error=>!!error.transport && error.message!=='reader still waiting for EOF');
    else{
      assert.deepEqual(await bounded,{reply:'finished'});
      assert.equal(cancelled,true,'release stream after the matching terminal frame');
    }
    assert.equal(calls,1,'never replay a state-changing call');
  }finally{clearTimeout(timer);}
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    script.write_text(program.replace("__CASE__", json.dumps(wire_case)).replace(
        "__TRANSPORT__", _transport_source()), encoding="utf-8")
    proc = subprocess.run([_node(), str(script)], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr


# --- wire fixtures -----------------------------------------------------------

_SID = {"mcp-session-id": "sess-1"}
_SID2 = {"mcp-session-id": "sess-2"}


def _ok(payload, headers=None):
    """A successful tools/call, framed the way the server actually frames it."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "__ECHO_ID__",
            "result": {"structuredContent": payload},
        }
    )
    return {"status": 200, "body": "event: message\ndata: " + body + "\n\n",
            "headers": headers or {}}


def _handshake_with(headers):
    """initialize + notifications/initialized, the pair ensureInit sends."""
    return [
        {"status": 200, "headers": headers,
         "body": json.dumps({"jsonrpc": "2.0", "id": "__ECHO_ID__", "result": {}})},
        {"status": 202, "body": "", "headers": headers},
    ]


def _handshake():
    return _handshake_with(_SID)


def _session_gone():
    """Exactly what the MCP transport sends for an unknown/expired session."""
    return {
        "status": 404,
        "headers": _SID,
        "body": json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "server-error",
                "error": {"code": -32600, "message": "Session not found"},
            }
        ),
    }


# --- the regression the founder reported -------------------------------------


def test_a_dead_session_is_never_carried_forward(tmp_path):
    """The bug: one failure poisoned the page until a manual reload.

    A 5xx used to throw while LEAVING ``sessionId`` set, so every later call
    reused the handle the server had already forgotten and failed identically.
    """
    out = _drive(
        tmp_path,
        _handshake() + [{"status": 500, "body": "upstream boom"}],
        'MCP.converse("hello")',
    )
    assert out["ok"] is False
    assert out["sessionIdAfter"] is None, (
        "a failed call left the dead session id in place -- the next call will "
        "reuse it and fail the same way, which is the reload-to-fix bug"
    )


def test_the_user_never_sees_the_parser_internal(tmp_path):
    """``no JSON or SSE frame`` is a parser detail, not something to show."""
    out = _drive(
        tmp_path,
        # A 200 whose SSE stream was cut after the event line, before its data
        # line -- what a mid-flight disconnect looks like from the client.
        _handshake() + [{"status": 200, "body": "event: message\n"}],
        'MCP.converse("hello")',
    )
    assert out["ok"] is False
    assert "no JSON or SSE frame" not in out["error"]["message"]
    assert out["error"]["transport"] == "stream_truncated"


def test_expired_session_is_recovered_and_the_turn_still_runs(tmp_path):
    """A session rejection is pre-dispatch, so the turn is replayed for real."""
    out = _drive(
        tmp_path,
        _handshake()
        + [_session_gone()]
        + _handshake()          # the client re-initializes...
        + [_ok({"reply": "hi there"}, _SID2)],   # ...then runs the turn
        'MCP.converse("hello")',
    )
    assert out["ok"] is True, out.get("error")
    assert out["result"] == {"reply": "hi there"}
    methods = [s["method"] for s in out["sent"]]
    assert methods.count("initialize") == 2, methods
    assert methods.count("tools/call") == 2, methods
    # The replay must carry the NEW session, never the handle that was rejected.
    calls = [s for s in out["sent"] if s["method"] == "tools/call"]
    assert calls[0]["sessionId"] == "sess-1"
    assert calls[1]["sessionId"] == "sess-1"  # re-issued by the second handshake
    assert out["sessionIdAfter"] == "sess-2"


def test_session_recovery_does_not_loop_forever(tmp_path):
    """Bounded at one attempt: a server stuck on 404 must surface, not spin."""
    out = _drive(
        tmp_path,
        _handshake() + [_session_gone()] + _handshake() + [_session_gone()],
        'MCP.converse("hello")',
    )
    assert out["ok"] is False
    assert [s["method"] for s in out["sent"]].count("tools/call") == 2


# --- the invariant that makes recovery safe ----------------------------------


def test_a_cut_stream_never_silently_resends_a_turn(tmp_path):
    """The universe may already be answering -- replaying would double-send."""
    out = _drive(
        tmp_path,
        _handshake() + [{"status": 200, "body": "event: message\n"}],
        'MCP.converse("hello")',
    )
    assert out["ok"] is False
    sent = [s for s in out["sent"] if s["tool"] == "converse"]
    assert len(sent) == 1, "a state-changing turn was replayed after a cut stream"
    assert out["error"]["replayable"] is False


def test_a_gateway_timeout_never_silently_resends_a_turn(tmp_path):
    """504 means the gateway timed out WAITING on the origin.

    So the request was delivered and the turn may be running right now.
    Replaying it would double-send, which is the one thing this must not do.
    """
    out = _drive(
        tmp_path,
        _handshake() + [{"status": 504, "body": "<html>gateway timeout</html>"}],
        'MCP.converse("hello")',
    )
    assert out["ok"] is False
    assert len([s for s in out["sent"] if s["tool"] == "converse"]) == 1
    assert out["error"]["transport"] == "unavailable"
    assert out["sessionIdAfter"] is None


def test_no_5xx_ever_replays_a_turn(tmp_path):
    """This test previously asserted the opposite, and was wrong.

    Round 1 flagged the click as a UX regression, so 502/503 were made to replay
    on the theory that they mean "the origin never took it". Round 2 proved that
    is false and a P0: the deploy recreates the container mid-flight
    (deploy/deploy_fail_safe.sh), so an ACCEPTED converse can do minutes of work
    and then have its connection die - emitting exactly that 502. The edge cannot
    discriminate either.

    A duplicate turn is worse than a click. Removing the dilemma needs an
    idempotency key on `converse`; until then, ambiguity means ask.
    """
    for status in (500, 502, 503, 504):
        out = _drive(
            tmp_path,
            _handshake()
            + [{"status": status, "body": "<html>origin down</html>"}]
            + _handshake()
            + [_ok({"reply": "hi there"}, _SID2)],
            'MCP.converse("hello")',
        )
        assert out["ok"] is False, f"HTTP {status} replayed a state-changing turn"
        assert len([x for x in out["sent"] if x["tool"] == "converse"]) == 1, (
            f"HTTP {status} sent the turn twice"
        )
        assert out["error"]["transport"] == "unavailable"


def test_an_idempotent_read_still_rides_through_a_deploy_blip(tmp_path):
    """The click is only paid where it buys something: a read is safe to repeat."""
    for status in (502, 503):
        out = _drive(
            tmp_path,
            _handshake()
            + [{"status": status, "body": "<html>origin down</html>"}]
            + _handshake()
            + [_ok({"active_host": "codex"}, _SID2)],
            "MCP.callTool(`get_status`,{},{idempotent:true})",
        )
        assert out["ok"] is True, (status, out.get("error"))


def test_an_idempotent_read_does_retry_a_cut_stream(tmp_path):
    """get_status is safe to run twice, so the user never sees the blip."""
    out = _drive(
        tmp_path,
        _handshake()
        + [{"status": 200, "body": "event: message\n"}]
        + _handshake()
        + [_ok({"active_host": "codex"}, _SID2)],
        "MCP.callTool(`get_status`,{},{idempotent:true})",
    )
    assert out["ok"] is True, out.get("error")
    assert out["result"] == {"active_host": "codex"}


# --- narrower faults must not be mistaken for a dead session -----------------


def test_a_protocol_version_400_is_not_treated_as_a_dead_session(tmp_path):
    """Re-initializing on it would loop; it is a different, fatal fault."""
    out = _drive(
        tmp_path,
        _handshake()
        + [
            {
                "status": 400,
                "body": json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "server-error",
                        "error": {
                            "code": -32600,
                            "message": "Bad Request: Unsupported protocol version: 1",
                        },
                    }
                ),
            }
        ],
        'MCP.converse("hello")',
    )
    assert out["ok"] is False
    assert [s["method"] for s in out["sent"]].count("initialize") == 1
    assert out["error"]["transport"] == "http_error"


def test_a_400_naming_the_session_is_recovered(tmp_path):
    """The transport also rejects a missing/invalid session with 400."""
    out = _drive(
        tmp_path,
        _handshake()
        + [
            {
                "status": 400,
                "body": json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "server-error",
                        "error": {
                            "code": -32600,
                            "message": "Bad Request: Missing session ID",
                        },
                    }
                ),
            }
        ]
        + _handshake()
        + [_ok({"reply": "recovered"}, _SID2)],
        'MCP.converse("hello")',
    )
    assert out["ok"] is True, out.get("error")
    assert out["result"] == {"reply": "recovered"}


def test_an_application_error_leaves_a_healthy_session_alone(tmp_path):
    """A JSON-RPC error is a WORKING session reporting a fault. Don't churn it."""
    out = _drive(
        tmp_path,
        _handshake()
        + [
            {
                "status": 200,
                "headers": _SID,
                "body": "data: "
                + json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "__ECHO_ID__",
                        "error": {"code": -32602, "message": "Invalid params"},
                    }
                ),
            }
        ],
        'MCP.converse("hello")',
    )
    assert out["ok"] is False
    assert out["error"]["message"] == "Invalid params"
    assert out["sessionIdAfter"] == "sess-1", "a healthy session was discarded"
    assert [s["method"] for s in out["sent"]].count("tools/call") == 1


def test_an_error_response_cannot_re_arm_the_failed_session(tmp_path):
    """Error bodies echo a session header; adopting it re-arms the dead handle."""
    out = _drive(
        tmp_path,
        _handshake() + [{"status": 500, "body": "boom", "headers": _SID2}],
        "MCP.callTool(`get_status`,{},{idempotent:true})",
    )
    assert out["ok"] is False
    assert out["sessionIdAfter"] is None


# --- recovery budgets are per-kind, not one shared flag ----------------------


def test_a_gateway_retry_does_not_spend_the_session_retry(tmp_path):
    """One boolean used to serve both, so the second fault could never recover."""
    out = _drive(
        tmp_path,
        _handshake()
        + [{"status": 502, "body": "<html>gw</html>"}]   # gateway budget
        + _handshake()
        + [_session_gone()]                              # session budget
        + _handshake()
        + [_ok({"active_host": "codex"}, _SID2)],
        "MCP.callTool(`get_status`,{},{idempotent:true})",
    )
    assert out["ok"] is True, out.get("error")
    assert out["result"] == {"active_host": "codex"}


def test_a_dropped_connection_is_reported_as_offline(tmp_path):
    """fetch rejecting must not surface as a raw TypeError."""
    out = _drive(
        tmp_path, _handshake() + [None], 'MCP.converse("hello")',
    )
    assert out["ok"] is False
    assert out["error"]["transport"] == "offline"
    assert "Failed to fetch" not in out["error"]["message"]
    assert out["sessionIdAfter"] is None


# --- half a handshake is worthless; repair it, do not wedge on it ------------


def test_a_blip_on_the_second_handshake_step_keeps_the_session_it_just_got(tmp_path):
    """`initialize` hands back a session; the notification then blips.

    Retiring the session there discards the id `initialize` just produced, so the
    retry goes out with no session at all and the server answers "Missing session
    ID" - the handshake destroys its own result. Reproduced by Codex.
    """
    out = _drive(
        tmp_path,
        [
            {"status": 200, "headers": _SID,
             "body": json.dumps({"jsonrpc": "2.0", "id": "__ECHO_ID__", "result": {}})},
            {"status": 503, "body": "<html>blip</html>"},   # the notification
            {"status": 202, "body": "", "headers": _SID},   # its retry
        ]
        + [_ok({"reply": "hi"}, _SID)],
        'MCP.converse("hello")',
    )
    assert out["ok"] is True, out.get("error")
    notifications = [s for s in out["sent"]
                     if s["method"] == "notifications/initialized"]
    assert len(notifications) == 2
    assert notifications[1]["sessionId"] == "sess-1", (
        "the handshake retried without the session initialize had just given it"
    )


def test_a_token_rotation_mid_handshake_does_not_wedge_the_client(tmp_path):
    """The bearer can rotate BETWEEN the two handshake steps.

    Repairing that from inside `_rpc` awaits the `_initing` promise it is already
    inside: the client wedges permanently, with no timeout and no further
    request. Codex reproduced exactly that. `ensureInit` restarts the handshake
    instead.
    """
    out = _drive(
        tmp_path,
        [
            {"status": 200, "headers": _SID,
             "body": json.dumps({"jsonrpc": "2.0", "id": "__ECHO_ID__", "result": {}})},
            {"status": 401, "body": ""},                    # the notification
            {"status": 400, "body": json.dumps(             # its session-less retry
                {"jsonrpc": "2.0", "id": "server-error",
                 "error": {"code": -32600,
                           "message": "Bad Request: Missing session ID"}})},
        ]
        + _handshake_with(_SID2)                            # a clean handshake
        + [_ok({"reply": "recovered"}, _SID2)],
        'MCP.converse("hello")',
    )
    assert out["error"] != {"transport": "DEADLOCK"} if not out["ok"] else True
    assert out.get("error", {}).get("transport") != "DEADLOCK", "the client wedged"
    assert out["ok"] is True, out.get("error")
    assert out["result"] == {"reply": "recovered"}


# --- two calls share one session; late replies must not fight ---------------


def test_a_late_reply_cannot_drag_the_client_back_to_an_older_session(tmp_path):
    """The 30s poll and a multi-minute turn overlap constantly.

    Codex held a `converse` on sess-1, let the poll rebuild the session as
    sess-2, then resolved the older 2xx - whose header still said sess-1 - and
    the client adopted it, orphaning sess-2.
    """
    out = _drive(
        tmp_path,
        _handshake()
        # the turn: its whole RESPONSE lands after the poll has rebuilt the
        # session, so the id it carries is already one generation stale
        + [dict(_ok({"reply": "slow"}, _SID), headersDelayMs=300)]
        + [_session_gone()]                      # the poll, on the same session
        + _handshake_with(_SID2)
        + [_ok({"active_host": "codex"}, _SID2)],
        '(async()=>{ const turn=MCP.converse("hello");'
        '  const poll=MCP.callTool(`get_status`,{},{idempotent:true});'
        '  return {turn: await turn, poll: await poll}; })()',
    )
    assert out["ok"] is True, out.get("error")
    assert out["result"]["turn"] == {"reply": "slow"}
    assert out["sessionIdAfter"] == "sess-2", (
        "a late success re-adopted the session it had been sent on, orphaning "
        "the one the poll had already rebuilt"
    )


def test_concurrent_rejections_rebuild_the_session_once_not_once_each(tmp_path):
    """Three calls share a dead session; their 404s arrive one after another.

    If each tore the session down, every late arrival would discard the session
    the previous one had just built. Codex measured three handshakes producing
    sess-2, sess-3 and sess-4.
    """
    out = _drive(
        tmp_path,
        _handshake()
        + [dict(_session_gone(), delayMs=50),
           dict(_session_gone(), delayMs=250),
           dict(_session_gone(), delayMs=450)]
        + _handshake_with(_SID2)
        + [_ok({"active_host": "a"}, _SID2),
           _ok({"active_host": "b"}, _SID2),
           _ok({"active_host": "c"}, _SID2)],
        '(async()=>{ const read=()=>MCP.callTool(`get_status`,{},{idempotent:true});'
        ' const a=read(), b=read(), c=read();'
        '  return [await a, await b, await c]; })()',
    )
    assert out["ok"] is True, out.get("error")
    handshakes = [s for s in out["sent"] if s["method"] == "initialize"]
    assert len(handshakes) == 2, (
        f"expected one recovery handshake, got {len(handshakes) - 1}: each late "
        "rejection rebuilt the session again"
    )
    assert out["sessionIdAfter"] == "sess-2"


# --- a 400 that is not about our session must not trigger a replay ----------


def test_a_400_that_merely_mentions_a_session_is_not_a_session_rejection(tmp_path):
    """An edge page or auth interstitial can say "session" and mean something else."""
    out = _drive(
        tmp_path,
        _handshake()
        + [{"status": 400,
            "body": "<html><body>Your session has expired, please sign in"
                    "</body></html>"}],
        'MCP.converse("hello")',
    )
    assert out["ok"] is False
    assert [s["method"] for s in out["sent"]].count("initialize") == 1, (
        "a non-JSON-RPC 400 was treated as a session rejection and replayed a "
        "state-changing call"
    )
    assert len([s for s in out["sent"] if s["tool"] == "converse"]) == 1


def test_a_bearer_refresh_also_bumps_the_session_generation(tmp_path):
    """Codex round 2: the refresh cleared the id without moving the generation.

    A slow turn in flight had captured the old generation, so its late 2xx
    re-adopted the session it was sent on and orphaned the one built after the
    refresh - the same defect the generation counter was added to prevent,
    reached by a path that bypassed it.
    """
    out = _drive(
        tmp_path,
        _handshake()
        # the turn, landing after the poll's refresh has rebuilt the session
        + [dict(_ok({"reply": "slow"}, _SID), headersDelayMs=300)]
        + [{"status": 401, "body": ""}]          # the poll: bearer expired
        + _handshake_with(_SID2)
        + [_ok({"active_host": "codex"}, _SID2)],
        '(async()=>{ const turn=MCP.converse("hello");'
        '  const poll=MCP.callTool(`get_status`,{},{idempotent:true});'
        '  return {turn: await turn, poll: await poll}; })()',
    )
    assert out["ok"] is True, out.get("error")
    assert out["refreshes"] == 1
    assert out["sessionIdAfter"] == "sess-2", (
        "clearing the session on refresh without bumping the generation lets a "
        "late reply re-adopt the pre-refresh session"
    )


def test_the_real_refresh_bumps_the_generation_not_just_the_id():
    """The call-site half of the test above.

    `refreshAccessToken` lives outside the extracted transport block, so the
    harness has to stub it and the behavioural test can only prove that
    invalidating-on-refresh yields the right outcome. This pins the real code to
    actually do that, rather than assigning null and silently skipping the
    generation bump - which is the bug Codex found.
    """
    html = _APP_HTML.read_text(encoding="utf-8")
    assert "MCP.invalidateSession();   // the MCP session was bound to the old bearer" in html
    assert "MCP.sessionId = null;   // the MCP session was bound to the old bearer" not in html


def test_a_second_rejection_still_disarms_the_dead_session(tmp_path):
    """Codex round 2: with the retry budget spent, the dead handle was kept.

    That is precisely the state this whole change exists to prevent - the page
    carrying a session the server has already disowned.
    """
    out = _drive(
        tmp_path,
        _handshake()
        + [_session_gone()]
        + _handshake_with(_SID2)
        + [_session_gone()],
        'MCP.converse("hello")',
    )
    assert out["ok"] is False
    assert out["sessionIdAfter"] is None, (
        "a session the server rejected twice was left armed for the next call"
    )
    assert out["error"]["transport"] == "session_lost"


def test_two_evictions_in_a_row_do_not_leave_a_session_armed(tmp_path):
    """Codex round 2: `_retire` is disabled during a handshake, by design.

    So when the repair handshake is ALSO evicted, nothing disarmed its session
    and the client kept a handle that had just been disowned.
    """
    out = _drive(
        tmp_path,
        [
            {"status": 200, "headers": _SID,
             "body": json.dumps({"jsonrpc": "2.0", "id": "__ECHO_ID__", "result": {}})},
            _session_gone(),                      # notification evicted
            {"status": 200, "headers": _SID2,
             "body": json.dumps({"jsonrpc": "2.0", "id": "__ECHO_ID__", "result": {}})},
            _session_gone(),                      # repair notification too
        ],
        'MCP.converse("hello")',
    )
    assert out["ok"] is False
    assert out["sessionIdAfter"] is None, (
        "the repair handshake's session survived its own eviction"
    )


def test_a_non_jsonrpc_body_naming_a_session_is_not_a_session_rejection(tmp_path):
    """Codex round 2: valid JSON was enough, so any layer could trigger a replay.

    `{"error":{"message":"browser session expired"}}` has no jsonrpc member and
    no error code. It is not the MCP transport disowning us.
    """
    out = _drive(
        tmp_path,
        _handshake()
        + [{"status": 400,
            "body": json.dumps({"error": {"message": "browser session expired"}})}],
        'MCP.converse("hello")',
    )
    assert out["ok"] is False
    assert len([x for x in out["sent"] if x["tool"] == "converse"]) == 1, (
        "a non-JSON-RPC 400 replayed a state-changing call"
    )
    assert [x["method"] for x in out["sent"]].count("initialize") == 1


# --- the UI must offer the retry, not just report the failure ----------------


def test_a_failed_turn_offers_to_send_again():
    """Structural, unlike the rest of this file.

    The resend path is DOM wiring, and there is no DOM shim here to drive it, so
    this checks the wiring exists rather than that clicking it works. The
    behavioural proof is the live surface.
    """
    html = _APP_HTML.read_text(encoding="utf-8")
    # The failure path hands back a working button rather than a dead sentence.
    assert "Send it again" in html
    # ...and a resend reuses the bubble already on screen instead of drawing the
    # same message twice, which would read as two sends.
    # the retry is the same send with the same options (a side send stays a
    # side send), echoed because the bubble is already on screen
    assert "sendTurn(message, display, Object.assign({}, opts||{}, {echoed:true}))" in html


@pytest.mark.parametrize("status,body_delay", [(401, False), (404, False), (404, True)])
def test_deposit_cannot_replay_under_a_different_login(tmp_path, status, body_delay):
    rejection = {"status": status, "body": "", "delayMs" if body_delay else "headersDelayMs": 80}
    out = _drive(
        tmp_path,
        _handshake() + [rejection] + _handshake_with(_SID2)
        + [_ok({"status": "deposited"}, _SID2)],
        '(async()=>{ const pending=MCP.connectHTTP("synthetic-dest","synthetic-secret",[]);'
        ' while(!SENT.some(s=>s.tool==="write_graph")) await new Promise(r=>setTimeout(r,1));'
        ' if(MCP.endLogin) MCP.endLogin(); else MCP.invalidateSession();'
        ' bearer="different-login"; return await pending; })()',
    )
    assert out["ok"] is False
    assert out["error"]["transport"] == "login_changed"
    assert len([s for s in out["sent"] if s["tool"] == "write_graph"]) == 1
    assert all(s["bearer"] == "Bearer test-bearer" for s in out["sent"])
    assert out["refreshes"] == 0
    assert out["sessionIdAfter"] is None


def test_delayed_handshake_cannot_send_a_secret_under_the_next_login(tmp_path):
    plan = _handshake()
    plan[0]["headersDelayMs"] = 80
    out = _drive(
        tmp_path, plan + [_ok({"status": "deposited"})],
        '(async()=>{ const pending=MCP.connectHTTP("synthetic-dest","synthetic-secret",[]);'
        ' while(!SENT.length) await new Promise(r=>setTimeout(r,1));'
        ' if(MCP.endLogin) MCP.endLogin(); else MCP.invalidateSession();'
        ' bearer="different-login"; return await pending; })()',
    )
    assert out["ok"] is False
    assert out["error"]["transport"] == "login_changed"
    assert len(out["sent"]) == 1
    assert out["sessionIdAfter"] is None


def test_stale_repair_handshake_cannot_discard_the_new_login_session(tmp_path):
    repair = _handshake_with(_SID2)
    repair[0]["headersDelayMs"] = 80
    out = _drive(
        tmp_path, [_handshake()[0], _session_gone()] + repair,
        '(async()=>{ const pending=MCP.connectHTTP("synthetic-dest","synthetic-secret",[]);'
        ' while(SENT.length<3) await new Promise(r=>setTimeout(r,1));'
        ' MCP.endLogin(); bearer="different-login"; MCP.sessionId="new-login-session";'
        ' return await pending; })()',
    )
    assert out["ok"] is False
    assert out["error"]["transport"] == "login_changed"
    assert out["sessionIdAfter"] == "new-login-session"
    assert len(out["sent"]) == 3


def test_gateway_pause_cannot_resume_under_another_login(tmp_path):
    out = _drive(
        tmp_path, _handshake() + [{"status": 503, "body": "unavailable"}],
        '(async()=>{ MCP._pause=async()=>{MCP.endLogin(); bearer="different-login";};'
        ' return await MCP.callTool(`get_status`,{},{idempotent:true}); })()',
    )
    assert out["ok"] is False
    assert out["error"]["transport"] == "login_changed"
    assert len(out["sent"]) == 3
    assert out["sessionIdAfter"] is None
