"""The "Connect free AI" screen and the daily-cap card, executed from the shipped page.

Founder, 2026-10-02: "build the connect screen with the $10 prompt and make sure
its clear to the user that its to extend their openrouter daily limit or that
they could connect another llm". The provider's name, its daily limit and what
its credit buys are installed data on the setup request; the page names none of
them. Only the DOM and the transport here are synthetic.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from tests.test_onboarding_app import _js_function
from tinyassets.onboarding import render_app_html

_START = "  // End connect OAuth controller.\n"
_END = "  // End connect screen sources."

HARNESS = r"""
function node(tag){
  const n={tag,children:[],attrs:{},textContent:'',className:'',type:'',hidden:false,
    disabled:false,listeners:{},href:'',target:'',rel:'',
    replaceChildren(){this.children=[];},
    appendChild(c){this.children.push(c);return c;},
    setAttribute(k,v){this.attrs[k]=String(v);},
    addEventListener(e,h){this.listeners[e]=h;}};
  return n;
}
const els=new Map();
const $=id=>{if(!els.has(id)){const e=node('div');e.id=id;els.set(id,e);}return els.get(id);};
const document={createElement:node};
const NATIVE=false, CONNECT_REQUEST_ID='sys_connect_llm';
let opened=0; function openConnectRequest(){opened++;}
const calls=[];
const ConnectOAuth={
  reply:null,
  async post(op,payload){calls.push({op,payload});
    if(this.reply instanceof Error) throw this.reply; return this.reply;},
  async begin(req,note,button,opts){
    calls.push({begin:req.request_id,source:!!(opts&&opts.source)});},
};
const SignInConnect={ids:{},configure(s,u){calls.push({configure:s,universe:u});},
  start(){calls.push({start:this.ids.result});}};
let railCache=[{request_id:'sys_connect_llm',action:{type:'connect',setup:__SETUP__}}];
function text(n){return [n.textContent].concat(n.children.map(text)).join(' ').trim();}
function find(n,pred){if(pred(n))return n;
  for(const c of n.children){const f=find(c,pred);if(f)return f;}return null;}
__SOURCE__
(async()=>{
__STEPS__
})().catch(e=>{console.error(e);process.exitCode=1;});
"""

SETUP = {
    "sign_in_sources": [{"id": "huggingface", "name": "Hugging Face",
                         "offer": "A small free monthly allowance.",
                         "billing_note": "Paid credits may be billed. Check your billing settings.",
                         "label": "Sign in with Hugging Face"}],
    "subscriptions": [{"service": "codex", "name": "ChatGPT",
                       "label": "Use your ChatGPT subscription",
                       "note": "For more volume: your agent runs on your ChatGPT plan's usage."}],
    "daily_caps": [{"host": "openrouter.ai", "name": "OpenRouter", "free_requests_per_day": 50,
                    "credit_requests_per_day": 1000, "credit_amount": "$10",
                    "credit_url": "https://openrouter.ai/settings/credits"}],
}
OPENROUTER_DETAIL = ("Daily quota exhausted. Reset: 2026-10-02 00:00 UTC. "
                     "Add credit: https://openrouter.ai/settings/credits")


def _run(steps: str, setup=None, extra_source: str = "") -> dict:
    node = shutil.which("node")
    assert node, "node is required to execute the shipped page"
    html, _ = render_app_html()
    source = html[html.index(_START) + len(_START):html.index(_END)] + extra_source
    program = (HARNESS.replace("__SETUP__", json.dumps(SETUP if setup is None else setup))
               .replace("__SOURCE__", source).replace("__STEPS__", steps))
    with tempfile.TemporaryDirectory() as scratch:
        script = os.path.join(scratch, "connect_free_ai.cjs")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(program)
        result = subprocess.run([node, script], capture_output=True, text=True,
                                encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


CARD = """
const card=DailyCapCard.build({provider_detail:__DETAIL__});
const buttons=[];
(function walk(n){if(n.tag==='button'||n.tag==='a')buttons.push(n);
  n.children.forEach(walk);})(card);
const out={head:card.children[0].textContent,body:card.children[1].textContent,
  label:card.attrs['aria-label'],
  buttons:buttons.map(b=>({tag:b.tag,text:b.textContent,href:b.href,target:b.target,rel:b.rel}))};
buttons.find(b=>b.textContent==='Connect another AI').listeners.click();
buttons.find(b=>b.textContent==='Wait until tomorrow').listeners.click();
out.opened=opened;out.hidden=card.hidden;
console.log(JSON.stringify(out));
"""


def test_daily_cap_card_says_the_credit_extends_the_openrouter_daily_limit():
    out = _run(CARD.replace("__DETAIL__", json.dumps(OPENROUTER_DETAIL)))
    assert out["head"] == "You've used today's free OpenRouter requests."
    assert out["label"] == out["head"]
    body = out["body"]
    assert body.startswith("On a free OpenRouter account that is 50/day. ")
    assert ("Adding $10 credit to your own OpenRouter account once raises your OpenRouter daily "
            "limit to 1,000 requests") in body
    assert "the money goes to OpenRouter, not TinyAssets." in body
    assert "It can be used again after the daily reset (they reset at about " in body
    assert "continues" not in body
    assert body.endswith("Or connect another AI: Hugging Face, your ChatGPT subscription, "
                         "or a key.")
    assert [b["text"] for b in out["buttons"]] == [
        "Add credit on OpenRouter", "Connect another AI", "Wait until tomorrow"]
    credit = out["buttons"][0]
    assert credit == {"tag": "a", "text": "Add credit on OpenRouter",
                      "href": "https://openrouter.ai/settings/credits", "target": "_blank",
                      "rel": "noopener noreferrer"}
    assert out["opened"] == 1 and out["hidden"] is True  # dismissable, no modal
    lowered = json.dumps(out).lower()
    assert "not now" not in lowered and "upgrade" not in lowered


def test_another_sources_daily_cap_claims_no_openrouter_numbers():
    detail = ("Daily quota exhausted. Reset time not supplied. "
              "Add credit: https://console.groq.com/settings/billing")
    out = _run(CARD.replace("__DETAIL__", json.dumps(detail)))
    assert out["head"] == "This AI source reached its daily limit."
    assert "50" not in out["body"] and "1,000" not in out["body"]
    assert "OpenRouter" not in json.dumps(out)
    assert "add credit at console.groq.com (the money goes to that provider" in out["body"]
    assert [b["text"] for b in out["buttons"]] == [
        "Add credit at console.groq.com", "Connect another AI", "Wait until tomorrow"]


def test_a_daily_cap_without_a_credit_page_offers_no_credit_button():
    out = _run(CARD.replace("__DETAIL__", json.dumps("Daily quota exhausted.")),
               setup={"daily_caps": []})
    assert [b["text"] for b in out["buttons"]] == ["Connect another AI", "Wait until tomorrow"]
    assert out["body"] == ("It can be used again after the daily reset. "
                           "Or connect another AI: a key.")


def test_a_hostile_credit_link_is_never_rendered():
    detail = "Daily quota exhausted. Add credit: javascript:alert(1)"
    out = _run(CARD.replace("__DETAIL__", json.dumps(detail)))
    assert all(b["tag"] != "a" for b in out["buttons"])


def test_sign_in_source_card_is_one_tap_to_the_platforms_own_ask():
    out = _run("""
SignInSourceCards.render(railCache[0].action.setup.sign_in_sources);
const host=$('connect-sign-in-sources');
const button=find(host,n=>n.tag==='button');
ConnectOAuth.reply={status:'sign_in_required',request:{request_id:'req-1',
  action:{type:'connect',oauth:{authorize_url:'https://huggingface.co/oauth/authorize'}}}};
await button.listeners.click();
const ok={text:text(host),label:button.textContent,calls:calls.slice(),
  children:host.children[0].children.map(n=>({tag:n.tag,text:n.textContent,cls:n.className}))};
calls.length=0;
ConnectOAuth.reply={request:{request_id:'req-2',action:{type:'connect'}}};
await button.listeners.click();
const status=find(host,n=>n.attrs.role==='status');
console.log(JSON.stringify({ok,refused:{calls,status:status.textContent,disabled:button.disabled}}));
""")
    assert out["ok"]["label"] == "Sign in with Hugging Face"
    assert "Hugging Face" in out["ok"]["text"]
    children = out["ok"]["children"]
    assert children[1]["text"] == SETUP["sign_in_sources"][0]["offer"]
    assert children[2] == {"tag": "p", "cls": "connect-terms",
                           "text": SETUP["sign_in_sources"][0]["billing_note"]}
    assert children[3]["tag"] == "button"
    assert out["ok"]["calls"] == [
        {"op": "source_sign_in", "payload": {"preset_id": "huggingface"}},
        {"begin": "req-1", "source": True}]
    # A request without a discovered sign-in is never handed to the sign-in client.
    assert out["refused"]["calls"] == [
        {"op": "source_sign_in", "payload": {"preset_id": "huggingface"}}]
    assert "sign_in_unavailable" in out["refused"]["status"]
    assert out["refused"]["disabled"] is False


def test_subscription_card_starts_its_own_device_sign_in():
    out = _run("""
SubscriptionCards.render(railCache[0].action.setup.subscriptions);
const host=$('connect-subscriptions');
find(host,n=>n.tag==='button').listeners.click();
console.log(JSON.stringify({text:text(host),hidden:$('sub-signin').hidden,calls}));
""")
    assert out["text"].startswith("For more volume")
    assert "Use your ChatGPT subscription" in out["text"]
    assert out["hidden"] is False
    # Its own elements, never the reconnect card's.
    assert out["calls"] == [{"configure": "codex", "universe": ""},
                            {"start": "sub-signin-result"}]


def test_daily_quota_failure_notice_carries_the_card():
    html, _ = render_app_html()
    notice = _js_function(html, "appendFailureNotice")
    out = _run("""
const quota=appendFailureNotice('stopped',null,undefined,'provider_daily_quota',
  {provider_detail:'__DETAIL__'});
const other=appendFailureNotice('stopped',null,undefined,'provider_rate_limited',
  {provider_detail:'x'});
const missing=appendFailureNotice('stopped',null,undefined,'provider_daily_quota',null);
console.log(JSON.stringify({quota:quota.children.map(c=>c.className),
  other:other.children.map(c=>c.className),missing:missing.children.map(c=>c.className)}));
""".replace("__DETAIL__", OPENROUTER_DETAIL), extra_source=(
        "\nfunction appendMessage(){return node('div');}\nfunction sendTurn(){}\n" + notice))
    assert "daily-cap" in out["quota"]
    assert "daily-cap" not in out["other"] and "daily-cap" not in out["missing"]


def test_every_live_and_history_failure_path_passes_the_record():
    html, _ = render_app_html()
    assert "error.failure=recorded?failure:null;" in html
    assert ",err.failureCode,err.failure);" in html
    assert ",error.failureCode,error.failure);" in html
    assert "observed.turn_failure.code,\n                  observed.turn_failure);" in html
    assert "index===ordered.length-1?t.failure:null" in html


def test_connect_screen_markup_and_entry_points():
    html, _ = render_app_html()
    panel = html[html.index('id="connect-panel"'):html.index('<details id="connect-other"')]
    for piece in ('Connect free AI', 'id="connect-primary"', 'id="connect-sign-in-sources"',
                  'id="connect-subscriptions"', '<details id="connect-paste"',
                  'id="free-source-cards"'):
        assert piece in panel
    # Lead 2026-10-02: by real usefulness -- guided sign-in, pasted keys (Groq,
    # Gemini first), the small monthly sign-in source, then a subscription.
    order = [panel.index(p) for p in ('id="connect-primary"', 'id="connect-paste"',
                                      'id="connect-sign-in-sources"',
                                      'id="connect-subscriptions"')]
    assert order == sorted(order)
    # The pasted-key cards are folded away until asked for.
    paste = panel[panel.index('<details id="connect-paste"'):]
    assert " open" not in paste[:paste.index(">")]
    # Settings reaches the same screen as onboarding and the rail.
    assert 'id="btn-connect-free-ai"' in html
    assert '$("btn-connect-free-ai").addEventListener("click",()=>openConnectRequest());' in html
    # The page still names no provider: names come from the setup request.
    assert "openrouter" not in html.lower() and "hugging face" not in html.lower()


def test_subscription_completion_only_claims_ready_when_serving():
    html, _ = render_app_html()
    client = html[html.index("  const SignInConnect={"):html.index("  const ENDPOINT_CONTEXT=")]
    client = client[:client.rfind("  // A declared model list")]
    client = client.replace("const SignInConnect=", "const TestedSignInConnect=", 1)
    extra = """
function authHeaders(){return {};}
function frameTitle(row){return row.title;}
function refreshRail(){}
let reply;
async function fetch(){return {ok:true,status:200,json:async()=>reply};}
""" + client
    out = _run("""
const messages=[];
for(const doc of [
  {status:'connected',serving:{status:'held'},confirmation:{title:'Use subscription'}},
  {status:'connected',serving:{status:'held'}},
  {status:'connected',serving:{status:'serving'}}]){
  reply=doc;
  const c=Object.assign({},TestedSignInConnect,{flow:'f',connectedText:'READY'});
  await c.poll(1000,c.epoch);
  messages.push($(c.ids.result).textContent);
}
console.log(JSON.stringify({messages}));
""", extra_source=extra)
    assert 'Confirm "Use subscription"' in out["messages"][0]
    assert "model setup still needs review" in out["messages"][1]
    assert out["messages"][2] == "READY"
