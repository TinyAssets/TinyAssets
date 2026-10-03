"""Notifications come on by default: the app asks, instead of waiting for the
owner to find a switch in Account.

The shipped functions are pulled out of ``app.html`` and run under Node with the
Capacitor plugin, the browser push APIs, ``fetch`` and the DOM replaced -- the
edges of the page. Nothing between them is stubbed.
"""
from __future__ import annotations

import json

from tests.test_app_browser_notifications import functions, run_js
from tests.test_onboarding_app import _js_function
from tinyassets.onboarding import render_app_html

PROMPT = ("showNotifyPrompt", "hideNotifyPrompt", "dismissNotifyPrompt")

# The page's globals the functions close over: storage, the session, the DOM
# nodes of the prompt line, and `fetch` answering the two routes involved.
COMMON = """
const NATIVE_PUSH_FLAG="app.push.fcm", NATIVE_PUSH_OWNER="app.push.owner",
  NATIVE_PUSH_RECIPIENT="app.push.recipient", NATIVE_PUSH_ASKED="app.push.asked",
  NOTIFY_PROMPT_DISMISSED="app.push.prompt.dismissed";
const NOTIFY_PROMPT_COPY={offer:"OFFER", blocked:"BLOCKED"};
let nativePushWired=false, nativeTokenWaiter=null, pendingReply=null;
let nativeTeardown=Promise.resolve();
const store={}, localStorage={getItem:k=>k in store?store[k]:null,
  setItem:(k,v)=>{store[k]=String(v);},removeItem:k=>{delete store[k];}};
const MCP={_loginEpoch:1}; let queueOwner='alice'; const token=()=>'alice-token';
const calls=[], posts=[];
const node=()=>({hidden:true,disabled:false,textContent:''});
const dom={'notify-prompt':node(),'notify-prompt-text':node(),
  'btn-notify-prompt-on':node(),'btn-notify-prompt-dismiss':node()};
const $=id=>dom[id];
let serverEnabled=true, vapid='AAAA';
const fetch=async(path,options)=>{
  const body=JSON.parse((options&&options.body)||'null');
  posts.push({path,method:options.method,body});
  return {ok:true,status:200,json:async()=>path==='/app/notify'
    ? {enabled:serverEnabled,vapid_public_key:vapid}
    : {device_id:'dev_1',recipient:'rTAG'}};
};
const devicePosts=()=>posts.filter(p=>p.path==='/app/devices');
const prompt=()=>({hidden:dom['notify-prompt'].hidden,text:dom['notify-prompt-text'].textContent,
  on:!dom['btn-notify-prompt-on'].hidden});
const out=extra=>console.log(JSON.stringify(
  {calls,posts:devicePosts(),store,prompt:prompt(),...(extra||{})}));
"""

NATIVE_NAMES = (
    "notificationSession", "notificationAPI", "nativePush", "setNativeActive",
    "armNativeFor", "wireNativePush", "nativeFcmToken",
    "registerNativeNotifications", "autoEnableNativeNotifications",
    "unregisterNativeNotifications", "removeNativeRegistration",
) + PROMPT

NATIVE = """
const NATIVE=true;
const listeners={};
let permission='prompt', answer='granted', onCheck=null, unregisterDelay=0;
const plugin={
  checkPermissions:async()=>{if(onCheck) onCheck();return {receive:permission};},
  requestPermissions:async()=>{calls.push('requestPermissions');permission=answer;
    return {receive:permission};},
  addListener:(name,fn)=>{(listeners[name]=listeners[name]||[]).push(fn);},
  register:async()=>{calls.push('register');
    setTimeout(()=>(listeners.registration||[]).forEach(f=>f({value:'fcm-1'})),0);},
  unregister:async()=>{await new Promise(r=>setTimeout(r,unregisterDelay));
    calls.push('unregister');},
  removeAllDeliveredNotifications:async()=>calls.push('removeAll'),
};
const replyPlugin={setActive:async({active})=>calls.push('active:'+active),consume:async()=>({})};
const nativePlugin=name=>name==='PushNotifications'?plugin
  :(name==='NotificationReply'?replyPlugin:null);
"""


def native(body: str) -> dict:
    return run_js(functions(*NATIVE_NAMES) + COMMON + NATIVE
                  + "(async()=>{" + body + "})();")


def test_a_phone_never_asked_is_asked_once_and_registers_on_yes():
    out = native("await autoEnableNativeNotifications(); out();")

    assert out["calls"].count("requestPermissions") == 1
    [post] = out["posts"]
    assert post["body"] == {"platform": "fcm", "token": "fcm-1", "label": "This phone"}
    assert out["store"]["app.push.fcm"] == "1"
    assert out["store"]["app.push.asked"] == "1"
    assert out["prompt"]["hidden"] is True


def test_a_no_in_the_dialog_is_remembered_and_explained_not_registered():
    out = native("answer='denied'; await autoEnableNativeNotifications(); out();")

    assert out["posts"] == []
    assert "app.push.fcm" not in out["store"]
    assert out["store"]["app.push.asked"] == "1"
    assert out["prompt"] == {"hidden": False, "text": "BLOCKED", "on": False}


def test_a_phone_that_already_said_no_is_not_asked_again_on_the_next_launch():
    out = native("""
localStorage.setItem('app.push.asked','1'); permission='prompt-with-rationale';
await autoEnableNativeNotifications(); out();""")

    assert "requestPermissions" not in out["calls"]
    assert out["posts"] == []
    assert out["prompt"]["hidden"] is False and out["prompt"]["text"] == "BLOCKED"


def test_a_permanently_denied_phone_gets_the_line_and_no_dialog():
    out = native("permission='denied'; await autoEnableNativeNotifications(); out();")

    assert "requestPermissions" not in out["calls"]
    assert out["posts"] == [] and out["prompt"]["text"] == "BLOCKED"


def test_a_phone_that_already_allows_them_registers_silently():
    out = native("permission='granted'; await autoEnableNativeNotifications(); out();")

    assert "requestPermissions" not in out["calls"]
    assert len(out["posts"]) == 1 and out["store"]["app.push.fcm"] == "1"


def test_an_owner_who_turned_them_off_is_never_asked_or_registered():
    out = native("serverEnabled=false; await autoEnableNativeNotifications(); out();")

    assert out["calls"] == []
    assert out["posts"] == []
    assert "app.push.asked" not in out["store"]
    assert out["prompt"]["hidden"] is True


def test_a_phone_already_on_is_left_to_the_rebind():
    out = native("""
localStorage.setItem('app.push.fcm','1'); await autoEnableNativeNotifications();
console.log(JSON.stringify({calls,n:posts.length}));""")

    assert out == {"calls": [], "n": 0}


def test_a_dismissed_line_stays_dismissed():
    out = native("""
dismissNotifyPrompt(); permission='denied';
await autoEnableNativeNotifications(); out();""")

    assert out["prompt"]["hidden"] is True


def test_a_sign_out_while_checking_permission_asks_and_registers_no_one():
    """Alice's prompt must not run on for Bob, who may have turned them off."""
    out = native("""
onCheck=()=>{MCP._loginEpoch++;};
await autoEnableNativeNotifications(); out();""")

    assert "requestPermissions" not in out["calls"] and "register" not in out["calls"]
    assert out["posts"] == [] and "app.push.asked" not in out["store"]


def test_a_registration_waits_for_the_last_sign_outs_teardown():
    """Otherwise the old owner's late unregister() deletes the new token."""
    out = native("""
unregisterDelay=30; permission='granted';
unregisterNativeNotifications();
await autoEnableNativeNotifications(); out();""")

    calls = out["calls"]
    assert calls.index("unregister") < calls.index("register")
    assert out["store"]["app.push.fcm"] == "1"


WEB_NAMES = (
    "notificationSession", "notificationAPI", "subscribeBrowserPush",
    "offerBrowserNotifications", "enableFromPrompt",
) + PROMPT

WEB = """
const NATIVE=false; let DESKTOP_SHELL=false;
let existing=null, answer='granted', onSubscribe=null;
const Notification={permission:'default',
  requestPermission:async()=>{calls.push('requestPermission');Notification.permission=answer;
    return answer;}};
const subscription={toJSON:()=>({endpoint:'https://push.example/alice'}),
  unsubscribe:async()=>{calls.push('unsubscribed');existing=null;}};
const registration={pushManager:{getSubscription:async()=>existing,
  subscribe:async()=>{calls.push('subscribe');if(onSubscribe) onSubscribe();
    existing=subscription;return subscription;}}};
const navigator={serviceWorker:{getRegistration:async()=>existing?registration:null,
  register:async()=>registration, ready:Promise.resolve(registration)}};
const window={Notification, PushManager:function(){}};
"""


def web(body: str) -> dict:
    return run_js(functions(*WEB_NAMES) + COMMON + WEB + "(async()=>{" + body + "})();")


def test_a_browser_gets_a_one_tap_offer_and_no_unprompted_dialog():
    out = web("await offerBrowserNotifications(); out();")

    # A browser only shows its dialog from a tap: the card asks, the page does not.
    assert out["calls"] == []
    assert out["posts"] == []
    assert out["prompt"] == {"hidden": False, "text": "OFFER", "on": True}


def test_one_tap_on_the_offer_subscribes_this_browser_under_the_session():
    out = web("await offerBrowserNotifications(); await enableFromPrompt(); out();")

    assert out["calls"] == ["requestPermission", "subscribe"]
    [post] = out["posts"]
    assert post["body"] == {"platform": "web", "label": "Browser",
                            "token": {"endpoint": "https://push.example/alice"}}
    assert "Notifications are on" in out["prompt"]["text"]
    assert out["prompt"]["on"] is False


def test_a_no_to_the_browser_dialog_subscribes_nothing_and_says_where_to_fix_it():
    out = web("""
answer='denied'; await offerBrowserNotifications(); await enableFromPrompt(); out();""")

    assert "subscribe" not in out["calls"] and out["posts"] == []
    assert "blocked" in out["prompt"]["text"] and out["prompt"]["on"] is False


def test_a_browser_that_already_allows_them_is_subscribed_without_a_card():
    out = web("Notification.permission='granted'; await offerBrowserNotifications(); out();")

    assert out["calls"] == ["subscribe"]
    assert len(out["posts"]) == 1 and out["prompt"]["hidden"] is True


def test_no_offer_when_the_owner_turned_them_off():
    out = web("serverEnabled=false; await offerBrowserNotifications(); out();")

    assert out["calls"] == [] and out["posts"] == [] and out["prompt"]["hidden"] is True


def test_no_offer_in_the_desktop_app_where_push_cannot_work():
    out = web("""
DESKTOP_SHELL=true; await offerBrowserNotifications();
console.log(JSON.stringify({n:posts.length,prompt:prompt()}));""")

    assert out["n"] == 0 and out["prompt"]["hidden"] is True


def test_no_offer_without_web_push_configured_or_once_dismissed():
    unconfigured = web("vapid=''; await offerBrowserNotifications(); out();")
    dismissed = web("dismissNotifyPrompt(); await offerBrowserNotifications(); out();")

    assert unconfigured["prompt"]["hidden"] is True
    assert dismissed["prompt"]["hidden"] is True


def test_an_existing_subscription_is_left_to_the_rebind():
    out = web("""
existing=subscription; await offerBrowserNotifications();
console.log(JSON.stringify({calls,n:posts.length,prompt:prompt()}));""")

    assert out["n"] == 0 and out["calls"] == [] and out["prompt"]["hidden"] is True


def test_a_subscription_that_outlives_its_login_is_removed():
    out = web("""
Notification.permission='granted'; onSubscribe=()=>{MCP._loginEpoch++;};
await offerBrowserNotifications(); out();""")

    assert out["calls"] == ["subscribe", "unsubscribed"]
    assert out["posts"] == []


def test_a_card_left_on_screen_after_turning_them_off_does_not_turn_them_on():
    out = web("""
await offerBrowserNotifications(); serverEnabled=false;
await enableFromPrompt(); out();""")

    assert "subscribe" not in out["calls"] and out["posts"] == []
    assert "off for your account" in out["prompt"]["text"]
    assert out["prompt"]["on"] is False


def test_sign_in_runs_the_default_on_paths_and_sign_out_takes_the_line_down():
    html, _ = render_app_html()
    signed_in = _js_function(html, "enterSignedIn")
    rebind = signed_in.index("rebindNativeNotifications()")
    # After the rebind, so a phone already on is moved, not asked again.
    assert signed_in.index("autoEnableNativeNotifications()") > rebind
    assert "offerBrowserNotifications()" in signed_in
    assert "hideNotifyPrompt()" in _js_function(html, "enterSignedOut")
    assert 'indexOf("Electron/")' in html
    assert json.dumps("app.push.asked")[1:-1] in html
