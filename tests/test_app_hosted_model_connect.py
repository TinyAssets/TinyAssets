"""Execute the shipped guided-sign-in controller; synthetic DOM/transport, not live proof.

Vendor-neutral slice 6 (founder 2026-09-24): the guided sign-in lives INSIDE the
unpowered universe's connect request. Its preset arrives as data on that request,
the owner taps once, approves at the provider, and the page finishes the
free-model request it gets back - no second approval screen.
"""

import json
import shutil
import subprocess

import pytest

from tests.app_sheet_harness import recovery_source
from tests.test_onboarding_app import _js_function
from tinyassets.onboarding import render_app_html

PRESET = {"preset_id": "guided_models_v1", "name": "Example", "label": "Continue with Example",
          "manage_url": "https://provider.example/keys", "manual_key": True}


def run_browser(steps):
    html, _ = render_app_html()
    controller = html[html.index("  const HostedModelConnect={"):
                      html.index("  // End hosted model connection controller.")]
    program = r"""
const elements=new Map(),storage=new Map(),requests=[],navigations=[],answers=[];
const system=[],opened=[];
const $=id=>{if(!elements.has(id)) elements.set(id,{textContent:'',hidden:false,value:'',
 disabled:false,attrs:{},focus(){this.focused=true;},scrollIntoView(){this.scrolled=true;},
 setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},
 addEventListener(event,handler){this.listeners=this.listeners||{};this.listeners[event]=handler;}
 });return elements.get(id);};
const sessionStorage={getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),
 removeItem:k=>storage.delete(k)};
let NATIVE=false,me={setup:'empty'},auth='owner-token',viewGeneration=0;
let queueScope='',queueOwner='',uploadsRestored=false;const Uploads=null;
let exchangeResult=null,answerResult={status:'answered'},answerThrows=null;
let signedInNow=false,workosCalls=0,chatCount=0,refreshes=0,engineConnected=null;
const window={location:{pathname:'/app',search:'',assign:url=>navigations.push(url)}};
window.addEventListener=()=>{};
const document={getElementById:$,addEventListener(){}};
// The harness owns the clock; browser proofs exercise scheduled recovery.
const timers=[];
const setInterval=fn=>{timers.push(fn);return timers.length;};
const setTimeout=fn=>{timers.push(fn);return timers.length;};
const history={replaceState:(a,b,url)=>{window.location.pathname=url;window.location.search='';}};
const token=()=>auth,authHeaders=()=>({Authorization:auth});
let ensureFreshToken=async()=>{};
const randToken=()=> 'v'.repeat(43),challengeFor=async()=> 'c'.repeat(43);
let fetch=async(url,options)=>{
 requests.push({url,options,body:JSON.parse(options.body)});
 let result=url.endsWith('/begin') ? {flow:'f'.repeat(43),
  authorize_url:'https://provider.example/auth',expires_in:600} : exchangeResult;
 return {ok:!!result&&!result.error,json:async()=>result||{error:'incomplete'}};
};
const fetchMe=async()=>me,ModelPicker={reset(){}},Voice={refreshCapability(){},stop(){}};
const showView=v=>{if(v==='chat')chatCount++;};
const openConnectRequest=g=>{opened.push(g||'');if(g)HostedModelConnect.status(g);};
const refreshRail=()=>{},appendMessage=(who,text)=>system.push(text);
const startHeartbeat=()=>{},warmSession=()=>{},loadPlan=()=>{},loadHistory=()=>{};
// Sign-in reports the browser's IANA zone (`automation-schedule-timezone`), so
// a cron automation runs in the owner's clock and not the container's. Stubbed
// like the collaborators above; the real one POSTs and is not awaited.
const reportTimezone=()=>{};
const wire=()=>{},wireNativeReturn=()=>{},startSessionKeepAlive=()=>{};
const localStorage={removeItem(){}};
const completeSignInIfCallback=async()=>{workosCalls++;return signedInNow;};
const refreshAccessToken=async()=>{refreshes++;};
const enterSignedOut=()=>{HostedModelConnect.reset();};
const MCP={_loginEpoch:0,answerRequest:async p=>{answers.push(p);if(answerThrows)throw answerThrows;
 const r=typeof answerResult==='function'?answerResult():answerResult;return r;}};
__SOURCE__
(async()=>{
 HostedModelConnect.configure(__PRESET__);
 __STEPS__
 console.log(JSON.stringify({requests,navigations,answers,workosCalls,chatCount,refreshes,system,opened,
   path:window.location.pathname,search:window.location.search,stored:[...storage.values()],
   setup:HostedModelConnect.setup,request:HostedModelConnect.request,busy:HostedModelConnect.busy,
   preset:HostedModelConnect.preset,status:$('hosted-model-status').textContent,
   primaryHidden:$('connect-primary').hidden,finishHidden:$('btn-hosted-resume').hidden,
   primaryLabel:$('btn-hosted-model').textContent,keyHidden:$('connect-key').hidden,
   grant:$('hosted-model-grant').textContent,engineConnected}));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    source = (recovery_source(html) + "\nconst AppRecovery=window.AppRecovery;\n"
              + controller + _js_function(html, "setQueueScope")
              + _js_function(html, "setQueueOwner")
              + _js_function(html, "enterSignedIn") + _js_function(html, "boot"))
    node = shutil.which("node")
    assert node, "Node is required to execute browser tests"
    result = subprocess.run([node, "-"], input=program.replace("__SOURCE__", source)
                             .replace("__PRESET__", json.dumps(PRESET))
                             .replace("__STEPS__", steps),
                            capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def saved_callback(query="?code=synthetic-code"):
    return """
sessionStorage.setItem(HostedModelConnect.storageKey,JSON.stringify({flow:'f'.repeat(43),
 verifier:'v'.repeat(43),preset:'guided_models_v1',expires:Date.now()+60000}));
window.location.pathname='/app/model-callback/'+'f'.repeat(43);
window.location.search=__QUERY__;
    """.replace("__QUERY__", json.dumps(query))


_GRANT = "Your universe will think with the models this connection offers, free models only."


def confirmation(grant=_GRANT):
    return """
exchangeResult={status:'confirmation_required',request_id:'request-a',request:{
 request_id:'request-a',title:'Power free models',body:'Free only',grant_sentence:%s,
 action:{type:'bind_model_access'}}};
""" % json.dumps(grant)


# --- the button comes from the request, and names what the SERVER said ------


def test_the_primary_button_is_the_requests_preset_not_page_code():
    result = run_browser("HostedModelConnect.setup='empty';HostedModelConnect.paint();")
    assert result["primaryLabel"] == "Continue with Example"
    assert result["primaryHidden"] is False and result["finishHidden"] is True
    assert result["preset"] == "guided_models_v1"
    html, _ = render_app_html()
    assert "openrouter" not in html.lower(), "the page names a provider itself"


@pytest.mark.parametrize("bad", ["null", "{preset_id:'../x',label:'x'}", "{preset_id:'ok'}"])
def test_a_malformed_preset_offers_no_button_and_cannot_start(bad):
    result = run_browser("HostedModelConnect.preset='';HostedModelConnect.configure(" + bad + ");"
                         "HostedModelConnect.setup='empty';await HostedModelConnect.begin();")
    assert result["primaryHidden"] is True
    assert not result["requests"] and not result["navigations"]


def test_deliberate_disconnect_allows_explicit_guided_reconnect():
    result = run_browser(
        "HostedModelConnect.setup='disconnected'; await HostedModelConnect.begin();")
    assert len(result["requests"]) == 1
    assert result["requests"][0]["url"].endswith("/begin")
    assert result["requests"][0]["body"] == {"preset_id": "guided_models_v1",
                                             "code_challenge": "c" * 43}
    assert result["navigations"] == ["https://provider.example/auth"]


def test_a_powered_universe_sees_no_guided_sign_in():
    result = run_browser("HostedModelConnect.setup='connected';HostedModelConnect.paint();")
    assert result["primaryHidden"] is True and result["finishHidden"] is True
    assert result["keyHidden"] is True


# --- sign-in lands in chat with the request; nothing starts on its own -------


@pytest.mark.parametrize("state", ["empty", "disconnected", "recovery", "unavailable", None])
def test_sign_in_lands_in_chat_with_the_request_and_never_auto_starts(state):
    result = run_browser("signedInNow=true;me={setup:" + json.dumps(state)
                         + ",engine_connected:false};await boot();")
    assert not result["requests"] and not result["navigations"]
    assert result["chatCount"] == 1, "an unpowered universe did not land in its chat"
    assert len(result["opened"]) == 1, "the connect request was not opened"


def test_a_powered_sign_in_opens_no_setup():
    result = run_browser("signedInNow=true;me={setup:'connected'};await boot();")
    assert result["chatCount"] == 1 and result["opened"] == []
    assert result["engineConnected"] is True


def test_native_does_not_start_web_callback_flow():
    result = run_browser("NATIVE=true;HostedModelConnect.setup='empty';"
                         "await HostedModelConnect.begin();")
    assert not result["requests"]


# --- the return from the provider finishes by itself: 2 taps, not 3 ----------


def test_return_from_the_provider_finishes_the_free_model_request_itself():
    result = run_browser(saved_callback() + confirmation() + """
answerResult=()=>{me={setup:'connected',universe_id:'u',principal_id:'p'};
 return {status:'answered'};};
await boot();""")
    assert result["workosCalls"] == 0
    assert result["path"] == "/app" and not result["search"]
    assert not result["stored"] and not result["navigations"]
    assert len(result["requests"]) == 1
    assert result["requests"][0]["url"].endswith("/exchange")
    assert result["requests"][0]["options"]["referrerPolicy"] == "no-referrer"
    assert result["answers"] == [{"request_id": "request-a", "values": {}}], \
        "the owner had to approve twice"
    assert result["request"] is None and result["setup"] == "connected"
    assert any("connected" in line for line in result["system"])
    assert result["chatCount"] >= 1


@pytest.mark.parametrize("lost", [
    "answerResult={};",                                     # empty tool result
    "answerResult={reply:'Error executing tool'};",         # non-JSON tool text
    "answerThrows=new Error('the connection to your universe failed (HTTP 503)');",
    "answerResult={error:'provider_authority_denied',request_pending:true};",
])
def test_an_unconfirmed_answer_is_not_success_and_stays_one_tap_away(lost):
    """Live 2026-09-24: a deploy restarted the daemon mid-approval, the page read
    the non-answer as done, and the universe stayed unpowered."""
    result = run_browser(saved_callback() + confirmation() + lost + "await boot();")
    assert len(result["answers"]) == 1
    assert result["request"]["request_id"] == "request-a", "the request was dropped"
    assert result["finishHidden"] is False, "no one-tap way to finish"
    assert "nothing was lost" in result["status"]
    assert "already has connection setup" not in result["status"]
    assert result["busy"] is False and result["system"] == []


def test_an_answer_whose_reply_was_lost_but_landed_reads_as_connected():
    result = run_browser(saved_callback() + confirmation() + """
answerResult=()=>{me={setup:'connected',universe_id:'u',principal_id:'p'};return {};};
await boot();""")
    assert result["setup"] == "connected" and result["request"] is None
    assert any("connected" in line for line in result["system"])


def test_finish_connecting_retries_the_same_request_once_per_tap():
    result = run_browser(saved_callback() + confirmation() + """
answerResult={};await boot();
answerResult=()=>{me={setup:'connected'};return {status:'answered'};};
await HostedModelConnect.finish();""")
    assert result["answers"] == [{"request_id": "request-a", "values": {}}] * 2
    assert result["setup"] == "connected"
    assert len(result["requests"]) == 1, "finishing re-ran the exchange"


def test_the_grant_shown_is_the_servers_words():
    result = run_browser(saved_callback() + confirmation() + "answerResult={};await boot();")
    assert result["grant"].startswith("Your universe will think with")


@pytest.mark.parametrize("query", ["?error=access_denied", "", "?code=one&code=two"])
def test_cancelled_or_invalid_return_never_exchanges_or_restarts(query):
    result = run_browser(saved_callback(query) + "await boot();await boot();")
    assert not result["requests"] and not result["navigations"] and not result["answers"]
    assert not result["stored"]
    assert "cancelled or expired" in result["status"]


def test_foreign_or_expired_browser_flow_cannot_redeem():
    for mutation in ["saved.flow='x'.repeat(43);", "saved.expires=1;", "saved.preset='../x';"]:
        result = run_browser(saved_callback() + """
let saved=JSON.parse(sessionStorage.getItem(HostedModelConnect.storageKey));
""" + mutation + """
sessionStorage.setItem(HostedModelConnect.storageKey,JSON.stringify(saved));await boot();
""")
        assert not result["requests"]


def test_uncertain_exchange_is_not_retried_and_offers_finish():
    result = run_browser(saved_callback() + "await boot();await boot();")
    assert len(result["requests"]) == 1
    assert "Finish connecting" in result["status"]
    assert not result["stored"] and not result["navigations"] and not result["answers"]


@pytest.mark.parametrize("code,expected", [
    ("no_eligible_free_agent_model", "no free model"),
    ("model_authorization_required", "start again"),
    ("model_connection_expired", "start again"),
    ("unknown_model_connection", "start again"),
    ("model_setup_changed", "needs a look"),
    ("current_home_changed", "needs a look"),
    ("model_confirmation_requires_review", "needs a look"),
    ("model_connection_incomplete", "not ready yet"),
    ("raw upstream error must not be shown", "not ready yet"),
])
def test_recovery_message_matches_safe_error_without_automatic_retry(code, expected):
    result = run_browser("exchangeResult={error:" + json.dumps(code) + "};await boot();"
                         "await HostedModelConnect.complete();")
    assert expected in result["status"]
    assert len(result["requests"]) == 1
    assert not result["answers"] and not result["navigations"]
    assert result["busy"] is False
    assert "raw upstream error" not in result["status"]


def test_missing_authorization_on_existing_setup_keeps_it():
    result = run_browser("me={setup:'recovery'};"
                         "exchangeResult={error:'model_authorization_required'};await boot();"
                         "await HostedModelConnect.complete();")
    assert "existing setup was kept" in result["status"]
    assert result["setup"] == "recovery"
    assert not result["answers"] and not result["navigations"]


def test_resume_reads_the_saved_setup_then_finishes_it_in_the_same_tap():
    result = run_browser(confirmation() + "me={setup:'recovery'};await boot();"
                         "answerResult=()=>{me={setup:'connected'};return {status:'answered'};};"
                         "await HostedModelConnect.finish();")
    assert len(result["requests"]) == 1
    assert result["requests"][0]["url"].endswith("/resume")
    assert result["requests"][0]["body"] == {"preset_id": "guided_models_v1"}
    assert result["answers"] == [{"request_id": "request-a", "values": {}}]
    assert not result["navigations"]


# --- the pending free-model request folds into the setup it finishes ---------


def test_an_adopted_rail_request_is_finished_by_one_tap_not_a_second_card():
    result = run_browser("""
me={setup:'recovery'};await boot();
HostedModelConnect.adopt({request_id:'request-r',grant_sentence:'Your universe will think with x.',
 action:{type:'bind_model_access'}});HostedModelConnect.paint();
answerResult=()=>{me={setup:'connected'};return {status:'answered'};};
await HostedModelConnect.finish();""")
    assert result["answers"] == [{"request_id": "request-r", "values": {}}]
    assert not result["requests"], "an adopted request re-ran setup"
    assert result["setup"] == "connected"


def test_a_rail_refresh_without_the_request_clears_only_an_adopted_one():
    result = run_browser(saved_callback() + confirmation() + """
answerResult={};await boot();HostedModelConnect.adopt(null);""")
    assert result["request"]["request_id"] == "request-a", \
        "a stale rail read dropped the request this tab is finishing"


# --- the key shortcut: in the same request, one paste + one tap ---------------


def test_key_shortcut_posts_once_after_clearing_then_finishes():
    result = run_browser(confirmation() + r"""
const assert=require('node:assert/strict');
answerResult=()=>{me={setup:'connected'};return {status:'answered'};};
HostedModelConnect.setup='empty';HostedModelConnect.toggleKey();
$('hosted-key-input').value='synthetic-private-key';
const realFetch=fetch;fetch=async(...args)=>{
 assert.equal($('hosted-key-input').value,'');return realFetch(...args);
};
await HostedModelConnect.depositKey();await HostedModelConnect.depositKey();
assert.equal($('hosted-key-input').value,'');
""")
    assert len(result["requests"]) == 1
    call = result["requests"][0]
    assert call["url"] == "/app/model-connect/deposit_key"
    assert call["body"] == {"preset_id": "guided_models_v1", "key": "synthetic-private-key"}
    assert result["answers"] == [{"request_id": "request-a", "values": {}}]
    assert not result["navigations"] and not result["stored"]
    assert "synthetic-private-key" not in json.dumps(
        {k: v for k, v in result.items() if k != "requests"})


def test_key_toggle_never_reads_a_credential():
    result = run_browser(r"""
HostedModelConnect.setup='empty';
Object.defineProperty($('hosted-key-input'),'value',
 {get(){throw Error('credential read');},set(){}});
HostedModelConnect.toggleKey();HostedModelConnect.toggleKey();""")
    assert not result["requests"] and not result["answers"]


@pytest.mark.parametrize("key", ["", "key with space", "key\n", "é", "x" * 2049])
def test_manual_key_validation_clears_without_request(key):
    result = run_browser("HostedModelConnect.setup='empty';HostedModelConnect.toggleKey();"
                         "$('hosted-key-input').value=" + json.dumps(key) + ";"
                         "await HostedModelConnect.depositKey();"
                         "if($('hosted-key-input').value!=='')throw Error('not cleared');")
    assert not result["requests"] and not result["stored"]
    assert result["setup"] == "empty"


def test_manual_key_login_change_during_refresh_never_sends_under_new_login():
    result = run_browser(r"""
HostedModelConnect.setup='empty';HostedModelConnect.toggleKey();
$('hosted-key-input').value='synthetic-private-key';
ensureFreshToken=async()=>{HostedModelConnect.reset();auth='new-login';};
await HostedModelConnect.depositKey();
""")
    assert not result["requests"] and not result["answers"] and not result["stored"]
    assert "synthetic-private-key" not in json.dumps(result)


@pytest.mark.parametrize("change", ["HostedModelConnect.reset();", "viewGeneration++;"])
def test_manual_late_response_does_not_finish_in_changed_login_or_view(change):
    result = run_browser(confirmation() + r"""
HostedModelConnect.setup='empty';HostedModelConnect.toggleKey();
$('hosted-key-input').value='synthetic-private-key';
const original=fetch;
fetch=async(...args)=>{const result=await original(...args);__CHANGE__return result;};
await HostedModelConnect.depositKey();
""".replace("__CHANGE__", change))
    assert len(result["requests"]) == 1
    assert not result["request"] and not result["answers"] and not result["stored"]


def test_manual_unknown_failure_is_redacted_and_not_replayed():
    result = run_browser(r"""
HostedModelConnect.setup='empty';HostedModelConnect.toggleKey();
$('hosted-key-input').value='synthetic-private-key';
exchangeResult={error:'synthetic-private-key'};
await HostedModelConnect.depositKey();await HostedModelConnect.depositKey();
""")
    assert len(result["requests"]) == 1
    assert "synthetic-private-key" not in result["status"]
    assert not result["answers"] and not result["stored"]


def test_finishing_keeps_custom_ui_current_owner_hooks():
    result = run_browser(saved_callback() + confirmation() + r"""
const assert=require('node:assert/strict'),layoutCalls=[];
globalThis.AppUI={init(){layoutCalls.push(['init']);},
 reset(){layoutCalls.push(['reset']);},
 enable(home,principal){layoutCalls.push(['enable',home,principal]);}};
answerResult=()=>{me={setup:'connected',universe_id:'u-owner',principal_id:'owner'};
 return {status:'answered'};};
await boot();
assert(layoutCalls.some(call=>call[0]==='init'));
assert(layoutCalls.some(call=>call[0]==='reset'));
assert.deepEqual(layoutCalls.filter(call=>call[0]==='enable'),[['enable','u-owner','owner']]);
""")
    assert len(result["answers"]) == 1
    assert result["setup"] == "connected"
