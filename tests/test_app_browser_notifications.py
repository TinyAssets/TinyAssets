"""Execute the shipped browser code at its browser/HTTP boundaries."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.test_onboarding_app import _js_function
from tests.test_request_card_layout_and_links import _run_rail
from tinyassets.onboarding import render_app_html
from tinyassets.onboarding.notifications import SERVICE_WORKER


def run_js(source):
    node = shutil.which("node")
    assert node, "Node is required for browser behavior tests"
    result = subprocess.run([node, "-e", source], capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def functions(*names):
    html, _ = render_app_html()
    return "\n".join(_js_function(html, name) for name in names)


def test_page_csp_allows_its_worker_without_opening_page_scripts():
    _, csp = render_app_html()
    assert "worker-src 'self';" in csp
    assert "script-src 'nonce-" in csp
    assert "script-src 'self'" not in csp


@pytest.mark.parametrize("failure", [False, True])
def test_existing_subscription_moves_with_login_or_is_unsubscribed(failure):
    out = run_js(functions("notificationSession", "notificationAPI",
                          "rebindBrowserNotifications", "unsubscribeBrowserNotifications") + """
const calls=[], MCP={_loginEpoch:2}, queueOwner='bob', token=()=> 'bob-token';
const subscription={toJSON:()=>({endpoint:'https://push.example/shared'}),
 unsubscribe:async()=>calls.push('unsubscribed')};
const navigator={serviceWorker:{getRegistration:async()=>({
 pushManager:{getSubscription:async()=>subscription},
 getNotifications:async()=>[{close:()=>calls.push('closed')}]
})}};
const fetch=async(path,options)=>{calls.push({path,options});return {ok:!FAIL,
 json:async()=>({device_id:'dev_bob'})};};
(async()=>{await rebindBrowserNotifications();console.log(JSON.stringify(calls));})();
""".replace("FAIL", json.dumps(failure)))
    assert out[0]["path"] == "/app/devices"
    assert out[0]["options"]["headers"]["Authorization"] == "Bearer bob-token"
    assert ("unsubscribed" in out) is failure
    assert "closed" in out


@pytest.mark.parametrize("mode", ["accept", "deny", "reply"])
def test_item_verbs_use_the_existing_answer_path(mode):
    out = run_js(functions("answerRail", "frameTitle", "answerLine", "replyLine") + """
const sent=[], notes={textContent:''}, fields={
 'fb_req::one':{value:'my reply'}, 'f_req::one_note':{value:'answer'},
 'mute_req::one':{checked:true}, 'btn-send':{disabled:false}};
const $=id=>fields[id];
const MCP={answerRequest:async p=>{sent.push(p);return {request_status:'pending'};}};
let railOpen='req'; const refreshRail=async()=>{}, sendTurn=()=>{};
(async()=>{
 await answerRail({request_id:'req::one',parent_request_id:'req',item_id:'one',
 fields:[{name:'note'}]},MODE,notes,[]);
 console.log(JSON.stringify({sent,railOpen}));
})();
""".replace("MODE", json.dumps(mode)))
    [payload] = out["sent"]
    assert payload["request_id"] == "req"
    assert payload["item_id"] == "one"
    assert payload["feedback"] == "my reply"
    assert "dont_ask_again" not in payload
    assert out["railOpen"] == "req"
    if mode == "deny":
        assert payload["dismiss"] is True
    else:
        assert payload["values"] == {"note": "answer"}


def test_checklist_poll_keeps_other_item_draft_and_focus():
    request = {"request_id": "req", "title": "Today", "kind": "Tasks", "items": [
        {"item_id": "one", "title": "First", "status": "pending", "fields": []},
        {"item_id": "two", "title": "Second", "status": "pending", "fields": []},
    ]}
    out = _run_rail([request], """
railOpen='req'; renderRail(railCache);
const input=$('fb_req::two'), card=host.children[0]; input.value='draft'; input.focus();
railCache[0].items[0].status='answered'; renderRail(railCache);
const result={same:card===host.children[0],draft:$('fb_req::two').value,
 focus:focused===input,checked:$('check_req::one').checked,
 status:$('status_req::one').textContent,whole:!!$('fb_req'),
 noMute:!$('mute_req::one'),text:text(card)};
""")
    assert out["same"] and out["focus"] and out["checked"] and out["whole"]
    assert out["noMute"] and out["draft"] == "draft"
    assert out["status"] == "Answered"
    assert "Whole request" in out["text"]


def test_pending_item_poll_does_not_reenable_an_inflight_answer():
    out = run_js(functions("updateRailItems") + """
const button={disabled:true}, input={disabled:false}, check={id:'check_req::one',disabled:true};
const $=id=>id==='item_req::one'?{querySelectorAll:()=>[button,input,check]}:null;
updateRailItems({request_id:'req',items:[{item_id:'one',status:'pending'}]});
console.log(JSON.stringify({button:button.disabled,input:input.disabled,check:check.disabled}));
""")
    assert out == {"button": True, "input": False, "check": True}


def test_request_and_item_deep_link_opens_and_focuses_the_item():
    out = _run_rail([], """
globalThis.railLink='req'; globalThis.railDeepItem='one';
renderRail([{request_id:'req',title:'Today',items:[{item_id:'one',title:'First'}]}]);
const result={open:railOpen,focus:focused.id,consumed:railLink===null};
""")
    assert out == {"open": "req", "focus": "item_req::one", "consumed": True}


@pytest.mark.parametrize("scenario", [
    "on", "off", "denied", "missing_key", "subscribe_failed", "account_changed",
])
def test_browser_setting_permission_subscription_and_account_fence(scenario):
    out = run_js(functions("notificationSession", "notificationAPI", "toggleNotifications",
                          "subscribeBrowserPush") + """
const NATIVE=false, scenario=SCENARIO, calls=[], elements={};
const $=id=>elements[id] ||= {textContent:'',disabled:false};
let queueOwner='alice', notificationEnabled=scenario==='off', notificationBusy=false;
const MCP={_loginEpoch:1}, token=()=>queueOwner+'-token';
const window={Notification:{},PushManager:{}}, atob=s=>Buffer.from(s,'base64').toString('binary');
const Notification={requestPermission:async()=>{calls.push('permission');
 if(scenario==='account_changed') {queueOwner='bob';MCP._loginEpoch++;}
 return scenario==='denied'?'denied':'granted';}};
const registration={pushManager:{getSubscription:async()=>null,subscribe:async options=>{
 calls.push({subscribe:Array.from(options.applicationServerKey),visible:options.userVisibleOnly});
 if(scenario==='subscribe_failed') throw Error('subscribe failed');
 return {toJSON:()=>({endpoint:'https://push.example/sub',keys:{auth:'a',p256dh:'b'}})};
}}};
const navigator={serviceWorker:{register:async(path,options)=>{
 calls.push({register:path,scope:options.scope});return registration;},
 ready:Promise.resolve(registration)}};
const fetch=async(path,options)=>{calls.push({path,options});return {ok:true,json:async()=>({
 vapid_public_key:scenario==='missing_key'?'':'AQID',enabled:false})};};
const loadNotifications=async()=>calls.push('reload');
(async()=>{await toggleNotifications(false);console.log(JSON.stringify({calls,
 status:$('notification-status').textContent,busy:notificationBusy}));})();
""".replace("SCENARIO", json.dumps(scenario)))
    calls = out["calls"]
    writes = [c for c in calls if isinstance(c, dict)
              and c.get("options", {}).get("method") == "POST"]
    assert not out["busy"]
    if scenario == "on":
        assert calls[0] == "permission"
        assert {"register": "/app/sw.js", "scope": "/app"} in calls
        assert {"subscribe": [1, 2, 3], "visible": True} in calls
        assert [c["path"] for c in writes] == ["/app/devices", "/app/notify"]
        assert json.loads(writes[0]["options"]["body"])["platform"] == "web"
        assert writes[0]["options"]["headers"]["Authorization"] == "Bearer alice-token"
        assert json.loads(writes[1]["options"]["body"]) == {"enabled": True}
    elif scenario == "off":
        assert "permission" not in calls
        assert [c["path"] for c in writes] == ["/app/notify"]
        assert json.loads(writes[0]["options"]["body"]) == {"enabled": False}
    else:
        assert not writes


def test_worker_shows_clears_and_clicks_only_the_app_window():
    out = run_js("""
const handlers={}, shown=[], closed=[], navigated=[], focused=[], opened=[];
const self={location:{origin:'https://tinyassets.io'},addEventListener:(k,f)=>handlers[k]=f,
 registration:{showNotification:async(t,o)=>shown.push({title:t,...o}),
 getNotifications:async({tag})=>[{close:()=>closed.push(tag)}]}};
const clients={matchAll:async()=>[
 {url:'https://evil.example/app',focus:()=>focused.push('evil')},
 {url:'https://tinyassets.io/other?next=/app',focus:()=>focused.push('other')},
 {url:'https://tinyassets.io/app',focus(){},navigate:async u=>{
 navigated.push(u);return {focus:()=>focused.push('app')};}}
],openWindow:async u=>opened.push(u)};
""" + SERVICE_WORKER + """
(async()=>{
 let waiting; const event=data=>({data:{json:()=>data},waitUntil:p=>waiting=p});
 handlers.push(event({title:'My universe asks',body:'TODO: Today',data:{request_id:'r &1'}}));
 await waiting;
 handlers.push(event({silent:true,data:{kind:'clear',request_id:'r &1'}}));await waiting;
 handlers.notificationclick({notification:{data:{request_id:'r &1',item_id:'one'},close(){}},
 waitUntil:p=>waiting=p});await waiting;
 clients.matchAll=async()=>[];
 handlers.notificationclick({notification:{data:{request_id:'r2'},close(){}},
 waitUntil:p=>waiting=p});await waiting;
 console.log(JSON.stringify({shown,closed,navigated,focused,opened}));
})();
""")
    assert out["shown"][0]["title"] == "My universe asks"
    assert out["shown"][0]["body"] == "TODO: Today"
    assert len(out["shown"]) == 1
    assert out["closed"] == ["r &1"]
    assert out["navigated"] == ["/app?request=r%20%261&item=one"]
    assert out["focused"] == ["app"]
    assert out["opened"] == ["/app?request=r2"]


def test_worker_claims_new_tabs_and_falls_back_if_navigation_rejects():
    out = run_js("""
const handlers={}, opened=[];let claimed=false;
const self={location:{origin:'https://tinyassets.io'},addEventListener:(k,f)=>handlers[k]=f};
const clients={claim:async()=>{claimed=true;},matchAll:async()=>[
 {url:'https://tinyassets.io/app',focus(){},navigate:async()=>{throw TypeError('uncontrolled');}}
],openWindow:async u=>opened.push(u)};
""" + SERVICE_WORKER + """
(async()=>{
 let waiting;handlers.activate({waitUntil:p=>waiting=p});await waiting;
 handlers.notificationclick({notification:{data:{request_id:'req'},close(){}},
 waitUntil:p=>waiting=p});await waiting;
 console.log(JSON.stringify({claimed,opened}));
})();
""")
    assert out == {"claimed": True, "opened": ["/app?request=req"]}


def test_item_reply_is_described_as_an_answer_not_an_approval():
    out = run_js(functions("answerLine", "frameTitle") + """
console.log(JSON.stringify(answerLine({title:'First'},'reply',{feedback:'my reply'})));
""")
    assert out == 'Answered "First" — my reply'
