"""The app's Stop: interrupt the running turn, then send everything queued as ONE turn.

Founder, 2026-09-30: "it should work just like it works in claude code where i
can press escape to interrupt and send all pending messages".

Executes the page's own ``sendTurn``, ``queueTurn``, ``flushSendQueue``,
``interruptTurn`` and the batch helpers under node, in the same DOM shim as
``tests/test_app_working_indicator.py``: the order and number of ``converse``
calls the page makes is the thing under test.
"""
from __future__ import annotations

import pytest

from tests.test_app_working_indicator import _NODE, _run, html  # noqa: F401
from tests.test_onboarding_app import _js_function

pytestmark = pytest.mark.skipif(
    _NODE is None, reason="node is required to execute the page's own source")

_INTERRUPTED = r"""{error:"Interrupted — you stopped this turn.", interrupted:true,
  turn_failure:{version:1,kind:"turn_failed",code:"interrupted",effects:"none"},
  failure_notice:"Interrupted — you stopped this turn.", history_saved:true}"""

# A Stop request the scenario answers by hand: `posts[i].resolve(response)`.
_HELD_FETCH = r"""
setQueueOwner("p-1"); setQueueScope("u-1");
const posts=[];
globalThis.authHeaders=()=>({});
globalThis.refreshAccessToken=async()=>false;
globalThis.fetch=(url,init)=>url==="/app/turn/steer"
  // No running turn to steer on the server's side here: the line queues,
  // which is the behaviour these Stop cases are about.
  ? Promise.resolve({ok:true,status:200,json:async()=>({steered:false})})
  : new Promise(resolve=>posts.push({url, body:JSON.parse(init.body), resolve}));
const ok=()=>({ok:true,status:200,json:async()=>({interrupted:1,universe_id:"u-1"})});
"""


def _run_with_interrupt(tmp_path, page, scenario, body, *, stop_ms=None):
    """The shared harness, plus the page's own ``interruptTurn``.

    ``stop_ms`` shortens the page's Stop-request deadline for a test that has
    to watch it expire; the constant is otherwise lifted exactly as shipped.
    """
    import tests.test_app_working_indicator as harness

    original = harness._program

    def with_interrupt(html_, scenario_, body_):
        program = original(html_, scenario_, body_)
        if stop_ms is not None:
            assert "const STOP_REQUEST_MS=10000;" in program
            program = program.replace(
                "const STOP_REQUEST_MS=10000;", f"const STOP_REQUEST_MS={stop_ms};")
        head, sep, tail = program.partition("\n(async()=>{\n")
        return head + "\n" + _js_function(html_, "interruptTurn") + sep + tail

    harness._program = with_interrupt
    try:
        return _run(tmp_path, page, scenario, _HELD_FETCH + body)
    finally:
        harness._program = original


_STOP = r"""
const first=sendTurn("start the long job");
await settle();
sendTurn("one"); sendTurn("two"); sendTurn("three");
const queued=bubbles().filter(b=>b.queued).length;
let stoppingLine="";
if(SCENARIO.stop){
  const stop=interruptTurn(); await settle();
  stoppingLine=indicator().line;
  posts[0].resolve(ok()); await stop;
}
gates[0].resolve(SCENARIO.stop ? __INTERRUPTED__ : {reply:"Done with the long job."});
await first; await settle();
console.log(JSON.stringify({posts:posts.map(p=>({url:p.url,body:p.body})), queued,
  stoppingLine, sent:converseCalls,
  stillQueued:bubbles().filter(b=>b.queued).length, texts:bubbles().map(b=>b.text)}));
""".replace("__INTERRUPTED__", _INTERRUPTED)


def test_stop_sends_every_queued_line_as_one_turn_in_order(tmp_path, html):  # noqa: F811
    out = _run_with_interrupt(tmp_path, html, {"stop": True}, _STOP)
    assert out["queued"] == 3
    assert out["posts"] == [{"url": "/app/turn/interrupt", "body": {"universe_id": "u-1"}}]
    assert "then sending the 3 waiting messages together" in out["stoppingLine"]
    # Exactly two turns: the interrupted one, then ONE carrying all three lines.
    assert out["sent"] == ["start the long job", "one\n\ntwo\n\nthree"], out["sent"]
    assert out["stillQueued"] == 0, "a line still claims to be waiting"
    assert any(text.startswith("Interrupted") for text in out["texts"])


def test_without_a_stop_the_queue_still_goes_one_line_at_a_time(tmp_path, html):  # noqa: F811
    """The positive control: batching is what the stop does, not a new default."""
    out = _run_with_interrupt(tmp_path, html, {"stop": False}, _STOP)
    assert out["posts"] == []
    assert out["sent"] == ["start the long job", "one"], out["sent"]


def test_stop_is_wired_to_the_button_escape_and_every_entry_point(html):  # noqa: F811
    assert 'id="btn-stop"' in html
    assert '$("btn-stop").addEventListener("click",()=>interruptTurn());' in html
    assert 'if(e.key!=="Escape"||e.defaultPrevented||e.isComposing) return;' in html
    # The phone layout gives Stop the send button's cell, so it is always a
    # visible tap target there too.
    assert ".composer .btn--stop{grid-column:4;grid-row:2}" in html
    # A spoken line waits for a Stop request exactly as a typed one does.
    assert 'if($("btn-send").disabled || interruptPending) throw new Error("voice_turn_busy");' \
        in html


_LATE_STOP = r"""
const a=sendTurn("A");
await settle();
sendTurn("B"); sendTurn("C");
const stopA=interruptTurn();               // A's Stop request is still on the wire...
await settle();
gates[0].resolve(__INTERRUPTED__);
await a; await settle();
const whilePending=converseCalls.slice();  // ...so nothing queued may start yet,
sendTurn("typed while it was pending");    // ...and neither may a manual send,
flushSendQueue();                          // ...nor any other flush of the queue.
await settle();
const afterManual=converseCalls.slice();
posts[0].resolve({ok:false,status:500,json:async()=>({})});   // late failure for A
await stopA; await settle();
const afterLate=converseCalls.slice(), lateLine=indicator().line;
sendTurn("D"); sendTurn("E");
const stopB=interruptTurn();
await settle();
posts[1].resolve(ok());
await stopB; await settle();
gates[1].resolve({reply:"B and C answered."});
await settle(); await settle();
console.log(JSON.stringify({whilePending, afterManual, afterLate, lateLine,
  sent:converseCalls}));
""".replace("__INTERRUPTED__", _INTERRUPTED)


def test_nothing_starts_while_a_stop_request_is_on_the_wire(tmp_path, html):  # noqa: F811
    """astra rounds 1-2: a Stop landing after the next turn began would stop THAT turn."""
    out = _run_with_interrupt(tmp_path, html, {}, _LATE_STOP)
    assert out["whilePending"] == ["A"], (
        "the queue went out while A's Stop request was still on the wire")
    assert out["afterManual"] == ["A"], "a manual send bypassed the pending Stop"
    # The manual line joined the queue, in order, and went out with it.
    assert out["afterLate"] == ["A", "B\n\nC\n\ntyped while it was pending"]
    # A's answer arrived after A ended: it describes nothing on screen now.
    assert "Could not stop" not in out["lateLine"], out["lateLine"]
    assert out["sent"] == ["A", "B\n\nC\n\ntyped while it was pending", "D\n\nE"], out["sent"]


_NEVER_ANSWERED = r"""
const a=sendTurn("A");
await settle();
sendTurn("B");
const stopA=interruptTurn();               // this request is never answered
await settle();
gates[0].resolve(__INTERRUPTED__);
await a; await settle();
const held=converseCalls.slice();
await stopA; await settle();               // ...until the page's own deadline
console.log(JSON.stringify({held, sent:converseCalls,
  pending:interruptPending!==null, line:indicator().line}));
""".replace("__INTERRUPTED__", _INTERRUPTED)


def test_a_stop_request_that_never_answers_does_not_hold_the_queue(tmp_path, html):  # noqa: F811
    out = _run_with_interrupt(tmp_path, html, {}, _NEVER_ANSWERED, stop_ms=200)
    assert out["held"] == ["A"]
    assert out["sent"] == ["A", "B"], "the queue stayed stranded behind a dead request"
    assert out["pending"] is False


_STOP_ACROSS_ACCOUNTS = r"""
const a=sendTurn("A private line");
await settle();
sendTurn("B"); sendTurn("C");
const stopA=interruptTurn();
await settle();
// The account changes while that Stop request is still on the wire.
MCP._loginEpoch++; clearComposerState(); setQueueOwner("p-2");
// The new account is not held behind the previous account's request.
sendTurn("new account line");
await settle();
const beforeOld=converseCalls.slice();
posts[0].resolve({ok:false,status:500,json:async()=>({})});
await stopA; await settle();
console.log(JSON.stringify({line:indicator().line, sent:converseCalls, beforeOld,
  flags:{interruptRequested, flushAfterInterrupt, pending:interruptPending!==null}}));
"""


_RETRY_ACROSS_ACCOUNTS = r"""
globalThis.fetch=async(url,init)=>{
  posts.push({url, body:JSON.parse(init.body)});
  return {ok:false,status:401,json:async()=>({})};
};
globalThis.refreshAccessToken=async()=>{
  // The page signs in as someone else while the renewal is in flight.
  MCP._loginEpoch++; clearComposerState(); setQueueOwner("p-2");
  return true;
};
const a=sendTurn("A");
await settle();
await interruptTurn(); await settle();
console.log(JSON.stringify({posts:posts.length}));
"""


def test_a_stop_is_never_retried_under_another_accounts_credential(tmp_path, html):  # noqa: F811
    """astra round 3: the 401 retry re-read authHeaders() after an account switch."""
    out = _run_with_interrupt(tmp_path, html, {}, _RETRY_ACROSS_ACCOUNTS)
    assert out["posts"] == 1, "the Stop was retried with the next account's credential"


def test_a_stop_answer_for_another_account_is_never_painted(tmp_path, html):  # noqa: F811
    out = _run_with_interrupt(tmp_path, html, {}, _STOP_ACROSS_ACCOUNTS)
    assert "Could not stop" not in out["line"], (
        "the previous account's Stop answer was painted on the next account's screen")
    assert out["flags"] == {"interruptRequested": False, "flushAfterInterrupt": False,
                            "pending": False}
    # The old account's queue never went out; the new account was not held.
    assert out["beforeOld"] == ["A private line", "new account line"], out["beforeOld"]
    assert out["sent"] == ["A private line", "new account line"], out["sent"]
