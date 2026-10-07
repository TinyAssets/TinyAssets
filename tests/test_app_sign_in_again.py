"""The sign-in shape's client, run as the page runs it.

The daemon has had `/app/openai/device/start` and `/poll` for weeks and NO web
client called them, so a source whose saved sign-in died had no way back through the
app. These drive the real `SignInConnect` object sliced out of the served page, with
a stub `fetch` standing in for the routes, and assert the whole sequence: start, show
the code, poll while pending, and take the card away on success.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tinyassets.onboarding import render_app_html

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(_NODE is None, reason="node is required to run page JS")

_HARNESS = r"""
const els=new Map();
function mk(id){return {id,textContent:'',hidden:false,value:'',href:'',placeholder:'',
 children:[],dataset:{},attrs:{},
 setAttribute(k,v){this.attrs[k]=v;},appendChild(c){this.children.push(c);return c;},
 replaceChildren(){this.children=[];},addEventListener(){},};}
const $=id=>{if(!els.has(id))els.set(id,mk(id));return els.get(id);};
function authHeaders(){return {};}
let queueScope='u-owner';
let railRefreshed=0;
function refreshRail(){railRefreshed++;}
// Timers are driven by the test, not by the clock: a real setTimeout would make
// this either slow or racy, and what is under test is the SEQUENCE. `clearTimeout`
// really cancels, as the DOM's does -- a no-op version left cancelled work sitting in
// the queue and made a correct cancellation look like a leak.
const pending=new Map();
let timerId=0;
function setTimeout(fn){pending.set(++timerId,fn);return timerId;}
function clearTimeout(id){pending.delete(id);}
const queue={get length(){return pending.size;},
  shift(){const k=pending.keys().next().value;
    if(k===undefined)return undefined;const fn=pending.get(k);pending.delete(k);return fn;}};
const calls=[];
let script=null;
function fetch(url,opts){
  calls.push({url,body:opts&&opts.body?JSON.parse(opts.body):null});
  const next=script.shift();
  return Promise.resolve({ok:next.ok!==false,status:next.status||200,
    json:()=>Promise.resolve(next.doc)});
}
__SOURCE__
async function drain(limit){
  // Bounded: an unbounded drain hangs on a client that keeps rescheduling, which
  // is exactly the defect the blip test found.
  let steps=0;
  while(queue.length && steps++ < (limit||20)){ const fn=queue.shift(); await fn(); }
}
"""


def _sign_in_source() -> str:
    html, _ = render_app_html()
    start = html.index("  const SignInConnect={")
    end = html.index("  // A declared model list needs a context size")
    return html[start:end]


def _run(script_steps, body, *, service="codex"):
    """Drive the client with a source already named, as a card always does.

    `service=""` is the exception a test asks for explicitly: the client refuses to
    start without one, because the route would otherwise complete its own service in
    the owner's home rather than the connection the card is for.
    """
    harness = _HARNESS.replace("__SOURCE__", _sign_in_source())
    prelude = (
        f"SignInConnect.configure({json.dumps(service)},'u-owner');\n" if service else ""
    )
    program = (
        harness
        + "script=" + json.dumps(script_steps) + ";\n"
        + "(async()=>{\n" + prelude + body
        + "\n})().catch(e=>{console.error(e);process.exit(1);});\n"
    )
    run = subprocess.run([_NODE, "-e", program], capture_output=True, text=True,
                         encoding="utf-8", timeout=30, check=False)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


_STARTED = {"doc": {"flow": "h-1", "user_code": "WXYZ-1234",
                    "verification_url": "https://sign-in.example.net/device",
                    "interval": 5}}


def test_start_shows_the_code_and_the_link_and_then_polls():
    out = _run([_STARTED, {"doc": {"status": "pending"}},
                {"doc": {"status": "connected", "service": "codex"}}], """
    await SignInConnect.start();
    const afterStart={code:$('signin-code-value').textContent,
      link:$('signin-code-link').href,hidden:$('signin-code').hidden,
      result:$('signin-result').textContent,queued:queue.length};
    await drain();
    console.log(JSON.stringify({afterStart,calls,railRefreshed,
      result:$('signin-result').textContent,codeHidden:$('signin-code').hidden}));
    """)
    after = out["afterStart"]
    assert after["code"] == "Your code: WXYZ-1234"
    assert after["link"] == "https://sign-in.example.net/device"
    assert after["hidden"] is False and after["queued"] == 1
    assert "Enter the code" in after["result"]

    # start, then a pending poll, then the connected poll -- and each poll carries
    # the OPAQUE handle, never the code the owner typed.
    assert [c["url"] for c in out["calls"]] == [
        "/app/openai/device/start",
        "/app/openai/device/poll",
        "/app/openai/device/poll",
    ]
    assert out["calls"][1]["body"] == {"flow": "h-1"}
    assert out["calls"][2]["body"] == {"flow": "h-1"}

    # On success the rail is reloaded, which is what removes the card: the card is
    # derived from the stored rejection the deposit just cleared.
    assert out["railRefreshed"] == 1
    assert "Signed in" in out["result"]
    assert out["codeHidden"] is True


def test_a_failed_start_says_the_routes_own_reason_and_polls_nothing():
    out = _run([{"ok": False, "status": 503, "doc": {"error": "device_login_unavailable"}}], """
    await SignInConnect.start();
    console.log(JSON.stringify({result:$('signin-result').textContent,
      calls:calls.length,queued:queue.length,flow:SignInConnect.flow}));
    """)
    assert out["calls"] == 1 and out["queued"] == 0, "a failed start must not poll"
    assert out["flow"] == ""
    assert "device_login_unavailable" in out["result"]


def test_a_refused_approval_stops_and_does_not_claim_success():
    out = _run([_STARTED, {"doc": {"status": "failed", "error": "device_poll_failed"}}], """
    await SignInConnect.start();
    await drain();
    console.log(JSON.stringify({result:$('signin-result').textContent,
      railRefreshed,queued:queue.length,flow:SignInConnect.flow}));
    """)
    assert out["railRefreshed"] == 0, "a failed sign-in must not report the card fixed"
    assert out["queued"] == 0 and out["flow"] == ""
    assert "did not complete" in out["result"] and "device_poll_failed" in out["result"]


def test_a_transport_blip_mid_poll_keeps_waiting():
    """One failed poll is not a failed sign-in."""
    out = _run([_STARTED], """
    await SignInConnect.start();
    // The next fetch throws, as a dropped connection does.
    fetch = () => Promise.reject(new Error('offline'));
    await drain(1);
    const afterOne={queued:queue.length,flow:SignInConnect.flow,
      result:$('signin-result').textContent};
    // ...and it does NOT retry forever: past the cap it stops and says so.
    await drain(20);
    console.log(JSON.stringify({afterOne,queued:queue.length,flow:SignInConnect.flow,
      result:$('signin-result').textContent}));
    """)
    assert out["afterOne"]["queued"] == 1, "the poll must be rescheduled, not abandoned"
    assert out["afterOne"]["flow"] == "h-1"
    assert "did not complete" not in out["afterOne"]["result"]
    # Bounded, though: an offline client must not poll for as long as the page lives.
    assert out["queued"] == 0, "the retry never stops"
    assert out["flow"] == ""
    assert "Check your connection" in out["result"]


@pytest.mark.parametrize("interval,expected", [(5, 5000), (0, 5000), (999, 30000), (1, 2000)])
def test_the_poll_interval_is_bounded_to_what_the_source_asked_for(interval, expected):
    """A source-supplied number is clamped: 0 or a missing value must not spin."""
    out = _run([{"doc": {**_STARTED["doc"], "interval": interval}}], """
    const waits=[];
    const realSetTimeout=setTimeout;
    setTimeout=(fn,ms)=>{waits.push(ms);return realSetTimeout(fn);};
    await SignInConnect.start();
    console.log(JSON.stringify({waits}));
    """)
    assert out["waits"] == [expected]


def test_the_shape_renders_a_button_and_offers_nothing_to_paste():
    """The card's shape has to be one the page actually draws.

    An unknown shape is filtered out of the rendered list, so a card offering a
    shape the page does not know renders a tab with no way to answer it.
    """
    html, _ = render_app_html()
    shapes = html[html.index("  const ConnectShapes={"):
                  html.index("  // A declared model list needs")]
    assert "sign_in:" in shapes, "the page does not know the shape the card offers"
    from tinyassets.api.pending_requests import SIGN_IN_SHAPE

    assert f"{SIGN_IN_SHAPE}:" in shapes, "the card's shape id and the page's disagree"


def test_a_reset_mid_start_cancels_the_flow_it_was_creating():
    """Codex refute-review, P1 #6: clearing the timer is not cancelling the work.

    A `start` whose response had not arrived yet came back AFTER the reset, restored
    the abandoned flow and scheduled polling against it, with `busy` false so a second
    start could run alongside it.
    """
    out = _run([_STARTED], """
    const started=SignInConnect.start();
    SignInConnect.reset();          // the owner leaves the shape mid-request
    await started;
    console.log(JSON.stringify({flow:SignInConnect.flow,queued:queue.length,
      busy:SignInConnect.busy,result:$('signin-result').textContent,
      codeHidden:$('signin-code').hidden}));
    """)
    assert out["flow"] == "", "the abandoned flow was restored"
    assert out["queued"] == 0, "polling was scheduled against an abandoned flow"
    assert out["codeHidden"] is True and out["result"] == ""


def test_a_reset_mid_poll_stops_that_poll_for_good():
    out = _run([_STARTED, {"doc": {"status": "connected", "service": "codex"}}], """
    await SignInConnect.start();
    const polling=queue.shift()();  // the poll is now in flight
    SignInConnect.reset();
    await polling;
    console.log(JSON.stringify({railRefreshed,queued:queue.length,
      result:$('signin-result').textContent}));
    """)
    # A reset means the owner walked away: a success arriving afterwards must not
    # repaint the panel or reload the rail on their behalf.
    assert out["railRefreshed"] == 0
    assert out["queued"] == 0 and out["result"] == ""


def test_a_second_start_while_one_is_live_is_ignored():
    out = _run([_STARTED], """
    await SignInConnect.start();
    await SignInConnect.start();     // a second tap on the button
    console.log(JSON.stringify({calls:calls.length,flow:SignInConnect.flow}));
    """)
    assert out["calls"] == 1, "a second start ran a second device flow"
    assert out["flow"] == "h-1"


def test_the_reconnect_card_reaches_the_connect_panel():
    """Codex refute-review, P1 #5: the card rendered, its control did not.

    `isSetupRequest` matched only the one setup request id, so a reconnect card fell
    through to the generic Accept/Deny controls and `connectBody` -- which is what
    draws the sign-in shape -- was never called for it.
    """
    from tests.test_onboarding_app import _js_function

    html, _ = render_app_html()
    source = _js_function(html, "isSetupRequest")
    program = (
        "const CONNECT_REQUEST_ID='sys_connect_llm';\n"
        + html[html.index('  const RECONNECT_REQUEST_PREFIX='):
               html.index("  function isSetupRequest(req){")]
        + source
        + """
const setup={request_id:'sys_connect_llm',action:{type:'connect',setup:{}}};
const card={request_id:'reconnect-source:codex',action:{type:'connect',setup:{}}};
const other={request_id:'req_x',action:{type:'connect',setup:{}}};
const noSetup={request_id:'reconnect-source:codex',action:{type:'connect'}};
console.log(JSON.stringify({setup:isSetupRequest(setup),card:isSetupRequest(card),
  other:isSetupRequest(other),noSetup:isSetupRequest(noSetup)}));
"""
    )
    run = subprocess.run([_NODE, "-e", program], capture_output=True, text=True,
                         encoding="utf-8", timeout=30, check=False)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["card"] is True, "the reconnect card still cannot reach the panel"
    assert out["setup"] is True, "the original setup card must keep working"
    # An unrelated request with a connect action is NOT the panel's: widening the
    # match must not swallow every card that happens to carry one.
    assert out["other"] is False
    assert out["noSetup"] is False


def test_the_client_refuses_to_start_without_a_named_source():
    """The route picks the home universe and its own service, so a start with no
    source named is a start against something the owner may not be looking at."""
    out = _run([], """
    await SignInConnect.start();
    console.log(JSON.stringify({calls:calls.length,result:$('signin-result').textContent}));
    """, service="")
    assert out["calls"] == 0, "a flow started with no source named"
    assert "needs signing in again" in out["result"]


def test_the_start_names_the_source_the_card_is_for():
    out = _run([_STARTED], """
    await SignInConnect.start();
    console.log(JSON.stringify({body:calls[0].body}));
    """)
    assert out["body"]["service"] == "codex"
    # ...and the universe the card named, which the route validates rather than trusts.
    assert out["body"]["universe_id"] == "u-owner"


def test_switching_cards_cancels_the_previous_source_flow():
    """Two reconnect cards must not share a flow."""
    out = _run([_STARTED], """
    await SignInConnect.start();
    const before={flow:SignInConnect.flow,queued:queue.length};
    SignInConnect.configure("other-source","u-owner");   // a different card
    console.log(JSON.stringify({before,flow:SignInConnect.flow,
      service:SignInConnect.service,queued:queue.length}));
    """)
    assert out["before"]["flow"] == "h-1" and out["before"]["queued"] == 1
    assert out["flow"] == "", "the previous source's flow survived the switch"
    assert out["queued"] == 0
    assert out["service"] == "other-source"
