"""The phone app's notification code, executed at its native and HTTP boundaries.

The shipped functions are pulled out of ``app.html`` and run under Node with
the Capacitor plugins and ``fetch`` replaced -- the two edges of the page.
Nothing between them is stubbed, so these fail if the code posts the wrong
platform, names an owner, forgets the bearer, or leaves a registration behind
for the next person to sign in.
"""
from __future__ import annotations

from tests.test_app_browser_notifications import functions, run_js

NAMES = (
    "notificationSession", "notificationAPI", "nativePush", "setNativeActive",
    "armNativeFor",
    "wireNativePush",
    "nativeFcmToken", "registerNativeNotifications", "rebindNativeNotifications",
    "unregisterNativeNotifications", "removeNativeRegistration",
)

# The page's own globals the functions close over, then a fake push plugin.
PRELUDE = """
const NATIVE=true, NATIVE_PUSH_FLAG="app.push.fcm", NATIVE_PUSH_OWNER="app.push.owner",
  NATIVE_PUSH_RECIPIENT="app.push.recipient";
let nativePushWired=false, nativeTokenWaiter=null, pendingReply=null;
let nativeTeardown=Promise.resolve();
const store={}, localStorage={getItem:k=>k in store?store[k]:null,
  setItem:(k,v)=>{store[k]=String(v);},removeItem:k=>{delete store[k];}};
const MCP={_loginEpoch:1}; let queueOwner='alice'; const token=()=>'alice-token';
const calls=[], posts=[], listeners={};
let fetchHook=null, permission='granted', registerBehavior='token', postOk=true, FCM='fcm-token-1';
const plugin={
  checkPermissions:async()=>({receive:permission}),
  requestPermissions:async()=>{calls.push('requestPermissions');return {receive:permission};},
  addListener:(name,fn)=>{(listeners[name]=listeners[name]||[]).push(fn);},
  register:async()=>{
    calls.push('register');
    if(registerBehavior==='reject') throw new Error('FirebaseApp is not initialized');
    setTimeout(()=>(listeners.registration||[]).forEach(f=>f({value:FCM})),0);
  },
  unregister:async()=>calls.push('unregister'),
  removeAllDeliveredNotifications:async()=>calls.push('removeAll'),
};
let replyPlugin={setActive:async({active,recipient})=>{
    calls.push('active:'+active+(active?':'+recipient:''));},
  consume:async()=>{calls.push('consume');return {};}};
const nativePlugin=name=>name==='PushNotifications'?plugin
  :(name==='NotificationReply'?replyPlugin:null);
const fetch=async(path,options)=>{posts.push({path,options,body:JSON.parse(options.body||'null')});
  return {ok:postOk,status:postOk?200:500,json:async()=>{if(fetchHook) fetchHook();
    return {device_id:'dev_1',recipient:'rTAG'};}};};
const out=()=>console.log(JSON.stringify({calls,posts,store}));
"""


def run(body: str, *, names=NAMES):
    return run_js(functions(*names) + PRELUDE + "(async()=>{" + body + "})();")


def test_turning_notifications_on_registers_the_phone_under_the_session():
    out = run("""
await registerNativeNotifications(notificationSession());
out();""")

    [post] = out["posts"]
    assert post["path"] == "/app/devices"
    assert post["options"]["headers"]["Authorization"] == "Bearer alice-token"
    # Nothing in the body names an owner: the server takes it from the bearer.
    assert post["body"] == {"platform": "fcm", "token": "fcm-token-1",
                            "label": "This phone"}
    assert out["store"]["app.push.fcm"] == "1"
    # The native message service is armed, for THIS owner only.
    assert out["store"]["app.push.owner"] == "alice"
    assert out["store"]["app.push.recipient"] == "rTAG"
    assert out["calls"][-1] == "active:true:rTAG"


def test_a_denied_permission_registers_nothing():
    out = run("""
permission='denied'; let error='';
try{ await registerNativeNotifications(notificationSession()); }catch(e){ error=e.message; }
console.log(JSON.stringify({calls,posts,store,error}));""")

    assert out["posts"] == []
    assert "register" not in out["calls"]
    assert "blocked" in out["error"]
    assert "app.push.fcm" not in out["store"]


def test_a_build_without_firebase_says_so_instead_of_hanging():
    out = run("""
registerBehavior='reject'; let error='';
try{ await registerNativeNotifications(notificationSession()); }catch(e){ error=e.message; }
console.log(JSON.stringify({posts,store,error}));""")

    assert "aren't set up" in out["error"]
    assert out["posts"] == [] and "app.push.fcm" not in out["store"]


def test_a_token_refresh_is_posted_only_for_a_phone_that_opted_in():
    out = run("""
wireNativePush();
(listeners.registration||[]).forEach(f=>f({value:'rotated-1'}));
await new Promise(r=>setTimeout(r,5));
const before=posts.length;
localStorage.setItem('app.push.fcm','1');
(listeners.registration||[]).forEach(f=>f({value:'rotated-2'}));
await new Promise(r=>setTimeout(r,5));
console.log(JSON.stringify({before,posts}));""")

    assert out["before"] == 0          # never opted in: nothing leaves the phone
    [post] = out["posts"]
    assert post["body"]["token"] == "rotated-2" and post["body"]["platform"] == "fcm"


def test_sign_in_reposts_the_current_token_so_a_rotation_while_closed_is_not_lost():
    out = run("""
localStorage.setItem('app.push.fcm','1'); localStorage.setItem('app.push.owner','alice');
FCM='token-after-rotation';
await rebindNativeNotifications();
out();""")

    [post] = out["posts"]
    assert post["body"]["token"] == "token-after-rotation"
    # Same owner: nothing of theirs is cleared, and display is never interrupted.
    assert "removeAll" not in out["calls"] and "active:false" not in out["calls"]


def test_sign_in_does_nothing_for_a_phone_that_never_opted_in():
    out = run("""
await rebindNativeNotifications();
out();""")

    assert out["posts"] == [] and out["calls"] == []


def test_a_registration_that_cannot_move_to_the_new_owner_is_removed_from_the_phone():
    out = run("""
localStorage.setItem('app.push.fcm','1'); postOk=false;
await rebindNativeNotifications();
out();""")

    # Otherwise this phone keeps receiving the PREVIOUS owner's requests.
    assert "unregister" in out["calls"] and "removeAll" in out["calls"]
    assert "app.push.fcm" not in out["store"]


def test_signing_out_drops_the_registration_and_the_notifications_on_screen():
    out = run("""
localStorage.setItem('app.push.fcm','1');
await unregisterNativeNotifications();
out();""")

    # Display is switched off FIRST and synchronously: deleteToken may not have
    # finished, and a late message must not reach the next person.
    assert out["calls"][0] == "active:false"
    assert out["calls"][-2:] == ["unregister", "removeAll"]
    assert "app.push.fcm" not in out["store"]


def test_a_registration_made_by_another_owner_is_disarmed_and_cleared_before_the_move():
    out = run("""
localStorage.setItem('app.push.fcm','1'); localStorage.setItem('app.push.owner','bob');
await rebindNativeNotifications();
out();""")

    calls = out["calls"]
    # Bob's notifications come off the screen, his parked reply is discarded, and
    # display stays off until the token has moved to the new owner.
    assert calls.index("active:false") < calls.index("removeAll") < calls.index("consume")
    assert calls[-1] == "active:true:rTAG"
    assert out["posts"][0]["body"]["platform"] == "fcm"
    assert out["store"]["app.push.owner"] == "alice"


def test_a_login_that_ended_while_registering_arms_nothing():
    """The continuation outlived its sign-in: sign-out landed while the POST's
    response was still being read. It must not re-arm the phone for the owner
    who just left (round 2, race on `await response.json()`)."""
    out = run("""
fetchHook=()=>{MCP._loginEpoch++;};   // sign-out lands while the response is read
let error='';
try{ await registerNativeNotifications(notificationSession()); }catch(e){ error=e.message; }
console.log(JSON.stringify({calls,store,error}));""")

    assert not any(c.startswith("active:true") for c in out["calls"])
    assert "app.push.fcm" not in out["store"] and "app.push.recipient" not in out["store"]
    assert "Account changed" in out["error"]


def test_a_failed_move_leaves_display_off_and_the_phone_unregistered():
    out = run("""
localStorage.setItem('app.push.fcm','1'); localStorage.setItem('app.push.owner','bob');
postOk=false;
await rebindNativeNotifications();
out();""")

    assert "active:true" not in out["calls"]
    assert out["calls"][-1] == "removeAll" and "unregister" in out["calls"]
    assert "app.push.fcm" not in out["store"]


def test_signing_out_discards_a_reply_the_previous_owner_never_submitted():
    out = run("""
localStorage.setItem('app.push.fcm','1');
pendingReply={request_id:'req_1',item_id:'',text:'alice private words',seen:0};
let consumedNatively=0;
replyPlugin={consume:async()=>{consumedNatively++;return {};},setActive:async()=>{}};
await unregisterNativeNotifications();
console.log(JSON.stringify({pending:pendingReply,consumedNatively,calls}));""")

    # Both halves: the page's copy and the one parked natively.
    assert out["pending"] is None and out["consumedNatively"] == 1


# --- the inline Reply hand-off --------------------------------------------------

REPLY_NAMES = ("collectNotificationReply", "applyPendingReply")

REPLY_PRELUDE = """
const NATIVE=true, NATIVE_PUSH_RECIPIENT="app.push.recipient";
let pendingReply=null, railCache=[];
const store={'app.push.recipient':'rTAG'};
const localStorage={getItem:k=>k in store?store[k]:null};
const fields={'fb_req_1::one':{value:''},'note_req_1::one':{},'fb_req_1':{value:''},
  'note_req_1':{},'composer-input':{value:''}};
const $=id=>fields[id]; const answered=[], refreshed=[];
const replies=[],MCP={_loginEpoch:0,answerRequest:async p=>{
  replies.push(p);return {status:'reply_queued'};}};
const appendMessage=()=>{};
const answerRail=(target,mode,note,buttons)=>answered.push(
  {target,mode,text:fields['fb_'+target.request_id].value});
const refreshRail=()=>refreshed.push(1);
let consumed=null;
const nativePlugin=name=>name==='NotificationReply'?{consume:async()=>consumed}:null;
"""


def run_reply(body: str):
    return run_js(functions(*REPLY_NAMES) + REPLY_PRELUDE + "(async()=>{" + body + "})();")


def test_a_reply_collected_natively_is_submitted_as_an_item_answer():
    out = run_reply("""
consumed={request_id:'req_1',item_id:'one',text:'Yes, at noon',recipient:'rTAG'};
railCache=[{request_id:'req_1',items:[{item_id:'one',status:'pending',fields:[]}]}];
await collectNotificationReply();
applyPendingReply();
console.log(JSON.stringify({answered,pending:pendingReply}));""")

    [sent] = out["answered"]
    assert sent["mode"] == "reply" and sent["text"] == "Yes, at noon"
    assert sent["target"]["request_id"] == "req_1::one"
    assert sent["target"]["parent_request_id"] == "req_1"
    assert out["pending"] is None       # once: never submitted twice


def test_a_reply_to_a_whole_request_goes_through_the_same_answer_path():
    out = run_reply("""
pendingReply={request_id:'req_1',item_id:'',text:'Looks good',seen:0};
railCache=[{request_id:'req_1'}];
applyPendingReply();
console.log(JSON.stringify({answered}));""")

    assert out["answered"][0]["target"]["request_id"] == "req_1"
    assert out["answered"][0]["text"] == "Looks good"


def test_a_reply_parked_for_another_account_is_dropped_not_rehomed():
    out = run_reply("""
consumed={request_id:'req_1',item_id:'one',text:'alices words',recipient:'rOTHER'};
await collectNotificationReply();
console.log(JSON.stringify({pending:pendingReply,refreshed}));""")

    assert out["pending"] is None and out["refreshed"] == []


def test_a_reply_is_dropped_when_no_account_is_armed_on_this_phone():
    out = run_reply("""
delete store['app.push.recipient'];
consumed={request_id:'req_1',text:'words',recipient:'rTAG'};
await collectNotificationReply();
console.log(JSON.stringify({pending:pendingReply}));""")

    assert out["pending"] is None


def test_no_reply_waiting_changes_nothing():
    out = run_reply("""
consumed={};
await collectNotificationReply();
console.log(JSON.stringify({pending:pendingReply,refreshed}));""")

    assert out["pending"] is None and out["refreshed"] == []


def test_a_reply_to_a_request_already_answered_elsewhere_is_kept_not_lost():
    out = run_reply("""
pendingReply={request_id:'req_1',item_id:'one',text:'too late',seen:0};
railCache=[{request_id:'req_1',items:[{item_id:'one',status:'answered'}]}];
await applyPendingReply(); const mid=pendingReply!==null; await applyPendingReply();
console.log(JSON.stringify({answered,replies,mid,pending:pendingReply,
  composer:fields['composer-input'].value}));""")

    assert out["answered"] == []          # never re-answers a resolved item
    assert out["mid"] is True and out["pending"] is None
    assert out["composer"] == ""
    assert out["replies"][0]["request_id"] == "req_1"
    assert out["replies"][0]["item_id"] == "one" and out["replies"][0]["reply"] == "too late"


def test_a_reply_never_overwrites_what_the_owner_is_typing():
    out = run_reply("""
fields['composer-input'].value='my own draft';
pendingReply={request_id:'gone',item_id:'',text:'reply text',seen:1};
railCache=[];
applyPendingReply();
console.log(JSON.stringify({composer:fields['composer-input'].value,pending:pendingReply}));""")

    assert out["composer"] == "my own draft"


def test_the_reply_waits_for_the_card_rather_than_submitting_into_nothing():
    out = run_reply("""
pendingReply={request_id:'req_1',item_id:'',text:'hi',seen:0};
railCache=[{request_id:'req_1'}];
delete fields['note_req_1'];
applyPendingReply();
console.log(JSON.stringify({answered,pending:pendingReply!==null}));""")

    assert out["answered"] == [] and out["pending"] is True
