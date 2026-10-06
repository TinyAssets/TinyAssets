"""Vendor-neutral slice 6: the connect request IS the model setup.

Founder, 2026-09-24: "the connect model flow should all be happening within the
notification request thing ... users should be able to handle all the connection
from those notification requests with as few clicks as possible".

Live the same day, a free-only account (u-01ky3zh1...) showed every defect
these tests pin:
- clicking "Connect a model" in the request switched to a FULL-PAGE screen with
  vendor cards;
- the free-model approval read "Update model access for agent agent_binding_...,
  root source api_key_http:provdef_..." (internal ids);
- the approval never landed (a deploy restarted the daemon mid-click), the page
  treated the non-answer as done, and the universe stayed unpowered with a second
  "Power your universe with free models" card waiting;
- the next message failed with "connect your provider: exactly one founder
  serving binding is required. Actions may already have occurred." - nothing had
  run;
- the Account page listed the connection as "model:openrouter_user_models_v1".

Server halves run the real stores. Browser halves run the functions the page
ships, lifted out of the rendered app and executed under node.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests import test_model_bootstrap as _bootstrap
from tests.app_sheet_harness import rail_source
from tests.inference_usage_helpers import accounting_resolver
from tests.test_onboarding_app import _js_function
from tinyassets.onboarding import render_app_html

_NODE = shutil.which("node")
rig = _bootstrap.rig
finish = _bootstrap.finish


def _answer(request_id):
    from tests.owner_answer import answer_request

    return answer_request(universe_id="u-owner", payload=json.dumps(
        {"request_id": request_id, "values": {}}))


def _rail():
    from tinyassets.api.pending_requests import list_requests

    return list_requests(universe_id="u-owner")["pending"]


# --------------------------------------------------------------------------- #
# The synthesized request carries the setup, and names no provider in code.
# --------------------------------------------------------------------------- #


def test_the_unpowered_request_is_the_setup_with_the_installed_preset_first(rig):
    first = _rail()[0]
    assert first["request_id"] == "sys_connect_llm" and first["sticky"] is True
    action = first["action"]
    assert action["type"] == "connect" and action["use"] == "model"
    primary = action["setup"]["primary"]
    # Display data from the INSTALLED preset, not strings in the page.
    from tinyassets.onboarding.hosted_model_auth import load_preset

    preset = load_preset(primary["preset_id"])
    assert primary["label"] == f"Continue with {preset.display_name}"
    assert primary["manage_url"] == preset.manage_url
    assert action["setup"]["shapes"] == ["api_key", "local"]
    assert first["grant_sentence"] == ""


def test_a_powered_universe_sees_only_an_optional_connect_another(rig, monkeypatch):
    monkeypatch.setattr("tinyassets.api.pending_requests._serving_llm_bound",
                        lambda *a, **k: True)
    row = _rail()[-1]
    assert row["request_id"] == "sys_connect_llm"
    assert row["title"] == "Connect another LLM" and row["sticky"] is False
    assert "primary" not in row["action"]["setup"], "a powered universe was offered first power"


# --------------------------------------------------------------------------- #
# The guided sign-in, round-tripped: deposit -> one answer -> the universe
# serves on ITS OWN connection, with no LLM call on the way.
# --------------------------------------------------------------------------- #


@pytest.fixture
def no_llm(monkeypatch):
    """Record every model call; setup must make none (Hard Rule 15).

    Every HTTP model call resolves the credential-blind broker proxy first, so
    counting that seam counts calls on any connection.
    """
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider

    calls = []

    def forbidden(self, *args, **kwargs):
        calls.append("model call")
        raise AssertionError("a model was called during setup")

    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy", accounting_resolver(forbidden))
    return calls


def _assert_serves_on_its_own_connection(base):
    from tinyassets.custom_agents import get_binding
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_serving_binding import resolve_serving_agent_binding
    from tinyassets.providers.definition import get_definition
    from tinyassets.storage.outbound_connections import ConnectionLedger

    # Exactly the check the live turn failed ("exactly one founder serving binding").
    agent = resolve_serving_agent_binding(base, universe_id="u-owner", owner_user_id="owner")
    assert get_binding(base, universe_id="u-owner",
                       binding_id=agent["agent_binding_id"])["status"] == "serving"
    assignment = load_provider_assignment(base, universe_id="u-owner")
    assert assignment.state == "ready" and assignment.owner_user_id == "owner"
    assert assignment.candidates, "serving has no model source"
    ledger = ConnectionLedger(base / "outbound.db")
    for member in assignment.candidates:
        kind, _, did = member.provider.partition(":")
        assert kind == "api_key_http"
        definition = get_definition("u-owner", did)
        assert definition.owner_user_id == "owner"
        grant = ledger.get_grant(definition.ref)
        connection = ledger.get_connection(grant.connection_id)
        # The universe's OWN connection, owned by its owner: never a platform
        # or host credential (Hard Rule 15).
        assert grant.owner_user_id == "owner" and grant.universe_id == "u-owner"
        assert connection.owner_user_id == "owner"
        assert connection.destination.startswith("model:")
        assert member.access.cost_caps is None, "setup approved spending"


def test_the_guided_sign_in_round_trips_to_a_serving_universe_with_no_llm_call(rig, no_llm):
    from tinyassets.api.pending_requests import _serving_llm_bound

    assert _serving_llm_bound(rig, "u-owner", "owner") is False
    started = finish(rig, key="exchanged-test-key")      # what the callback's exchange yields
    assert started["status"] == "confirmation_required"
    answered = _answer(started["request_id"])
    assert answered["status"] == "answered", answered
    assert _serving_llm_bound(rig, "u-owner", "owner") is True
    _assert_serves_on_its_own_connection(rig)
    assert no_llm == [], "setup called a model before the first reply"
    # Nothing left waiting: the connect entry became the optional one and the
    # free-model request resolved.
    rows = _rail()
    assert [r["request_id"] for r in rows] == ["sys_connect_llm"]
    assert rows[0]["sticky"] is False


def test_reconnect_after_a_product_disconnect_serves_again(rig, no_llm):
    """The live account's path: connected, disconnected through the product,
    then the guided sign-in again from the request."""
    from tinyassets.api.http_connection import remove_http
    from tinyassets.api.pending_requests import _serving_llm_bound

    first = finish(rig, key="first-test-key")
    assert _answer(first["request_id"])["status"] == "answered"
    removed = remove_http(universe_id="u-owner",
                          payload={"destination": "model:openrouter_user_models_v1"})
    assert removed["status"] == "removed"
    assert _serving_llm_bound(rig, "u-owner", "owner") is False
    assert _rail()[0]["sticky"] is True, "a disconnected universe lost its setup request"
    again = finish(rig, key="second-test-key")
    assert _answer(again["request_id"])["status"] == "answered"
    _assert_serves_on_its_own_connection(rig)
    assert no_llm == []


def test_after_the_approval_a_turn_selects_a_free_tool_model_on_its_own_connection(rig):
    """The live account's turn failed at the serving-binding check. After the
    approval, the served plan must find what a turn needs - a free,
    tool-capable model - on the universe's own connection, with no fetch.

    (The in-process converse needs a request-scoped verified principal to mint
    its provider carrier, which this suite cannot construct; the live acceptance
    run covers the reply itself.)"""
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.providers.served_model_plan import _http_models

    started = finish(rig, key="exchanged-test-key")
    assert _answer(started["request_id"])["status"] == "answered"
    _assert_serves_on_its_own_connection(rig)
    (member,) = load_provider_assignment(rig, universe_id="u-owner").candidates
    _snapshot, models, _interaction, _caps, rejected = _http_models("owner", "u-owner", member)
    assert [m.model_id for m in models.models] == ["new-provider/new-free-model"]
    assert not rejected


# --------------------------------------------------------------------------- #
# What the owner reads.
# --------------------------------------------------------------------------- #


def test_the_free_model_approval_names_no_internal_ids(rig):
    started = finish(rig, key="exchanged-test-key")
    sentence = started["request"]["grant_sentence"]
    for handle in ("agent_binding", "provdef", "api_key_http", "http_grant", "root source"):
        assert handle not in sentence, f"internal handle in the owner's text: {handle}"
    assert "free models only" in sentence
    rail_row = next(r for r in _rail() if r["request_id"] == started["request_id"])
    assert "agent_binding" not in rail_row["grant_sentence"]


def test_an_unpowered_turn_says_nothing_ran_and_points_at_the_request(rig, monkeypatch):
    from tinyassets import universe_server
    from tinyassets.providers import call as provider_calls

    monkeypatch.setattr(provider_calls, "_force_mock", False)
    out = json.loads(universe_server.converse(message="hi! what can you do?", graph_id="u-owner"))
    assert out["status"] == "held" and out["reason"] == "setup_required"
    assert out["turn_failure"]["code"] == "setup_required"
    text = out["note"] + " " + out["failure_notice"]
    assert "Waiting on you" in text
    assert "Actions may already have occurred" not in text
    assert "serving binding" not in text


def test_a_disconnected_universe_turn_is_setup_not_a_mystery(rig, monkeypatch):
    """The live account's exact state: a configured agent, an unassigned model."""
    from tinyassets import universe_server
    from tinyassets.api.http_connection import remove_http
    from tinyassets.providers import call as provider_calls

    first = finish(rig, key="first-test-key")
    assert _answer(first["request_id"])["status"] == "answered"
    remove_http(universe_id="u-owner", payload={"destination": "model:openrouter_user_models_v1"})
    monkeypatch.setattr(provider_calls, "_force_mock", False)
    out = json.loads(universe_server.converse(message="hello?", graph_id="u-owner"))
    assert out.get("reason") == "setup_required", out
    assert "Actions may already have occurred" not in json.dumps(out)


def test_the_setup_failure_notice_carries_no_retry_caution():
    from tinyassets.conversation_failure import failure_notice

    assert "may already have occurred" not in failure_notice("setup_required").lower()
    assert "may already have occurred" in failure_notice("timed_out").lower()


def test_the_account_page_names_a_guided_connection_by_its_preset():
    from tinyassets.onboarding.connections import connection_label
    from tinyassets.onboarding.hosted_model_auth import load_preset

    name = load_preset("openrouter_user_models_v1").display_name
    assert connection_label({"destination": "model:openrouter_user_models_v1"}) == \
        f"{name} (free models)"
    assert connection_label({"destination": "my-todo-app"}) == "my-todo-app"


# --------------------------------------------------------------------------- #
# The page: no full-page setup; the request holds it; powered sees "another".
# --------------------------------------------------------------------------- #


_RAIL_HARNESS = r"""
const els=new Map();
function mk(id){return {id,textContent:'',hidden:false,value:'',open:false,
 showModal(){this.open=true;},close(){this.open=false;},
 remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(c=>c!==this);this.parentNode=null;},
 querySelectorAll(){return [];},
 children:[],parentNode:null,
 classList:{set:new Set(),toggle(c,on){on?this.set.add(c):this.set.delete(c);},
  contains(c){return this.set.has(c);}},
 dataset:{},attrs:{},placeholder:'',
 setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},
 appendChild(c){if(c.parentNode){const p=c.parentNode;p.children=p.children.filter(x=>x!==c);}
   c.parentNode=this;this.children.push(c);return c;},
 replaceChildren(){this.children.forEach(c=>c.parentNode=null);this.children=[];},
 addEventListener(e,f){this['on'+e]=f;},scrollIntoView(){},focus(){}};}
const $=id=>{if(!els.has(id))els.set(id,mk(id));return els.get(id);};
const document={createElement:t=>{const e=mk('');e.tag=t;return e;}};
function text(node){return (node.textContent||'')+node.children.map(text).join(' ');}
$('rail-items'); const rail=$('request-rail'); rail.appendChild($('connect-panel'));
// textContent="" on a host clears children, as the DOM does.
const host=$('rail-items');
Object.defineProperty(host,'textContent',{get(){return '';},set(v){this.replaceChildren();}});
let railOpen=null, railCache=[], NATIVE=false, connectWasBlocking=null;
$('rail-head').textContent='Request history';$('connect-panel').hidden=true;
// renderRail now REUSES a card node whose row and open state are unchanged, so
// a 15-second poll cannot delete what the user typed into it.
let railNodes=new Map();
// The module-level flags the sliced functions close over. `connectOtherOpen` joined
// them on 2026-09-26: "Other ways to connect" is now CLOSED unless the user tapped
// it, and the renderer derives the element's state from this rather than reading it
// back off a node that survives every refresh.
let connectOtherOpen=false;
const CONNECT_REQUEST_ID="sys_connect_llm";
// `isSetupRequest` closes over this too, since a reconnect card is answered by the
// same connect panel: the sliced function needs every module-level name it reads.
const RECONNECT_REQUEST_PREFIX="reconnect-source:";
const answered=[];
const HostedModelConnect={setup:'empty',busy:false,request:null,primary:null,
 configure(p){this.primary=p;}, adopt(r){this.adopt_seen=r;}, paint(){}};
function railBody(req){const b=document.createElement('div');
 b.textContent='GENERIC:'+req.request_id+' Accept Deny Clear';return b;}
__SOURCE__
"""


def _run_rail(rows, extra=""):
    html, _ = render_app_html()
    source = rail_source(html)
    script = (_RAIL_HARNESS.replace("__SOURCE__", source)
              + "\nrenderRail(" + json.dumps(rows) + ");\n" + extra + r"""
const tabs=host.children.map(t=>({text:text(t),
  hasPanel:t.children.some(c=>c.children.includes($('connect-panel')))}));
console.log(JSON.stringify({tabs,panelHidden:$('connect-panel').hidden,
  sheetOpen:rail.open,primary:HostedModelConnect.primary,
  adopted:HostedModelConnect.adopt_seen||null,otherOpen:$('connect-other').open,
  shapes:$('connect-shapes').children.map(b=>b.textContent)}));
""")
    run = subprocess.run([_NODE, "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=30, check=False)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


_SETUP = {"request_id": "sys_connect_llm", "kind": "LLM", "sticky": True,
          "title": "Connect the model your universe runs on", "body": "Needs a model.",
          "fields": [], "action": {"type": "connect", "use": "model", "setup": {
              "primary": {"preset_id": "guided_models_v1", "label": "Continue with Example",
                          "name": "Example", "manage_url": "https://provider.example/keys",
                          "manual_key": True},
              "shapes": ["api_key", "local"]}}}
_ACCESS = {"request_id": "req_a", "kind": "Models", "title": "Power your universe with free models",
           "fields": [], "action": {"type": "bind_model_access"}, "grant_sentence": "x"}


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_an_unpowered_user_sees_the_setup_inside_the_request_first():
    out = _run_rail([_SETUP, {"request_id": "req_b", "kind": "API", "title": "Key please",
                               "fields": [], "action": {"type": "answer"}}])
    assert out["tabs"][0]["hasPanel"] is True, "the setup is not inside the first request"
    assert out["panelHidden"] is False and out["sheetOpen"] is True
    assert out["primary"]["label"] == "Continue with Example"
    assert out["shapes"] == ["API key", "Your own server"]
    assert "Accept" not in out["tabs"][0]["text"], "the setup rendered as a generic ask"
    assert "Key please" in out["tabs"][1]["text"] and not out["tabs"][1]["hasPanel"]


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_the_pending_free_model_request_folds_into_the_setup_while_unpowered():
    out = _run_rail([_SETUP, _ACCESS])
    assert len(out["tabs"]) == 1, "a second Models card sat beside the setup"
    assert out["adopted"]["request_id"] == "req_a"


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_a_powered_user_sees_only_a_collapsed_connect_another():
    powered = dict(_SETUP, sticky=False, title="Connect another LLM",
                   action={"type": "connect", "use": "model",
                           "setup": {"shapes": ["api_key", "local"]}})
    out = _run_rail([_ACCESS, powered])
    assert [t["hasPanel"] for t in out["tabs"]] == [False, False], \
        "setup expanded for a powered user"
    assert out["panelHidden"] is True and out["sheetOpen"] is False
    assert len(out["tabs"]) == 2, "a powered universe's own model request was hidden"
    opened = _run_rail([powered], "railOpen='sys_connect_llm';renderRail(railCache);")
    assert opened["tabs"][0]["hasPanel"] is True
    assert opened["primary"] is None, "a powered universe was offered first power"
    assert opened["otherOpen"] is True, "the ways to add another were not shown"


# --------------------------------------------------------------------------- #
# "Other ways to connect": one connect ask, answered in the same tap.
# --------------------------------------------------------------------------- #

_ENDPOINT_HARNESS = r"""
const els=new Map();
const $=id=>{if(!els.has(id))els.set(id,{id,value:'',textContent:'',disabled:false});
  return els.get(id);};
const calls=[];let signedIn=0,appended=[],refreshed=0;
const MCP={_loginEpoch:0,
  async requestFromUser(p){
    calls.push(['ask',JSON.parse(JSON.stringify(p)),$('endpoint-key').value]);return ASK;},
  async answerRequest(p){calls.push(['answer',p]);return ANSWER;}};
async function enterSignedIn(){signedIn++;}
function enterSignedOut(){}
function appendMessage(w,t){appended.push(t);}
function refreshRail(){refreshed++;}
const ENDPOINT_CONTEXT=32768;let endpointBusy=false;
__SOURCE__
$('endpoint-url').value=URL_IN;$('endpoint-key').value='synthetic-endpoint-key';
$('endpoint-model').value='quill-large';$('endpoint-wire').value='chat_messages';
connectEndpoint().then(()=>console.log(JSON.stringify({calls,signedIn,appended,refreshed,
  key:$('endpoint-key').value,result:$('endpoint-result').textContent})));
"""


def _run_endpoint(url, ask, answer):
    html, _ = render_app_html()
    script = (_ENDPOINT_HARNESS.replace("__SOURCE__", _js_function(html, "connectEndpoint"))
              .replace("URL_IN", json.dumps(url)).replace("ASK", json.dumps(ask))
              .replace("ANSWER", json.dumps(answer)))
    run = subprocess.run([_NODE, "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=30, check=False)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_an_api_key_endpoint_is_one_connect_ask_answered_in_the_same_tap():
    out = _run_endpoint("https://api.quillmind.dev/v2/converse",
                        {"status": "pending", "request_id": "req_q"},
                        {"status": "answered", "serving": {"status": "serving"}})
    (kind, ask, key_in_dom), (kind2, answer) = out["calls"]
    assert kind == "ask" and kind2 == "answer"
    assert key_in_dom == "", "the key was still in the page when the ask went out"
    assert "synthetic-endpoint-key" not in json.dumps(ask), "the key rode the ask"
    action = ask["action"]
    assert action["type"] == "connect" and action["host"] == "api.quillmind.dev"
    assert action["path_template"] == "/v2/converse" and action["methods"] == ["POST"]
    assert action["uses"]["model"]["models"][0]["id"] == "quill-large"
    assert [f["type"] for f in ask["fields"]] == ["secret"]
    assert answer == {"request_id": "req_q", "values": {"key": "synthetic-endpoint-key"}}
    assert out["signedIn"] == 1 and out["key"] == ""


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
@pytest.mark.parametrize("url", ["http://api.example.com/v1/chat", "not a url",
                                 "https://user:pw@api.example.com/v1/chat"])
def test_an_endpoint_key_never_leaves_over_an_unsafe_url(url):
    out = _run_endpoint(url, {}, {})
    assert out["calls"] == [] and out["key"] == ""


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_an_unconfirmed_endpoint_answer_leaves_an_honest_request_to_finish():
    out = _run_endpoint("https://api.example.com/v1/chat/completions",
                        {"status": "pending", "request_id": "req_q"},
                        {"error": "model_setup_unavailable", "request_pending": True})
    assert out["signedIn"] == 0 and out["appended"] == []
    assert "waiting in this list" in out["result"]
    assert out["refreshed"] == 1
