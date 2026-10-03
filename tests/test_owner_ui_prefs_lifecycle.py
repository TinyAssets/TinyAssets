"""Execute the shipped preference controller across real asynchronous boundaries."""
from __future__ import annotations

from tests.test_app_browser_notifications import functions, run_js
from tests.test_app_chat_cloud import CONSTS, DOM, NET, SYNC


def execute(body: str, network: str = NET):
    source = functions(*SYNC, "invalidateChatCloudPrefs", "setQueueOwner", "setQueueScope",
                       "wireCloudDrag")
    lifecycle = """
const Uploads=null;
let uploadsRestored=false;
const AppUI={homeChanged(){}};
"""
    return run_js(source + CONSTS + DOM + network + lifecycle + "(async()=>{" + body + "})();")


def test_server_hydration_overrides_a_saved_local_copy_before_any_new_gesture():
    out = execute("""
store['app.chatCloud.v1:alice:main:wide']=JSON.stringify({...SERVER,mode:'bubble'});
answer={prefs:{chat_cloud:SERVER}};
refreshChatCloud(); await settle();
console.log(JSON.stringify({mode:cloudState.mode,posts}));
""")
    assert out == {"mode": "open", "posts": []}


def test_read_refresh_cannot_issue_a_request_with_the_next_accounts_bearer():
    out = execute("""
onRefresh=()=>{queueOwner='bob'; MCP._loginEpoch++;};
refreshChatCloud(); await settle();
console.log(JSON.stringify({reads,posts,store}));
""")
    assert out == {"reads": [], "posts": [], "store": {}}


def test_old_home_response_is_dropped_even_after_returning_to_the_same_home():
    out = execute("""
let release;
answer=()=>new Promise(r=>{release=r;});
refreshChatCloud(); await settle();
setQueueScope('other-home'); setQueueScope('home-alice');
answer={prefs:{chat_cloud:{...SERVER,mode:'bubble'}}};
await settle();
release({prefs:{chat_cloud:SERVER}}); await settle();
console.log(JSON.stringify({mode:cloudState.mode,
  cached:JSON.parse(store['app.chatCloud.v1:alice:main:wide']).mode,reads,posts}));
""")
    assert out["mode"] == out["cached"] == "bubble"
    assert len(out["reads"]) == 2
    assert out["posts"] == []


def test_same_owner_new_login_reads_again_and_does_not_keep_old_gesture_flag():
    out = execute("""
answer={prefs:{}}; refreshChatCloud(); await settle();
setChatCloudMode('bubble'); await settle();
MCP._loginEpoch++; answer={prefs:{chat_cloud:SERVER}};
refreshChatCloud(); await settle();
console.log(JSON.stringify({mode:cloudState.mode,reads}));
""")
    assert out["mode"] == "open"
    assert len(out["reads"]) == 2


def test_home_switch_during_write_token_refresh_drops_the_old_write():
    out = execute("""
answer={prefs:{}}; refreshChatCloud(); await settle();
onRefresh=()=>{queueScope='other-home';};
setChatCloudMode('bubble'); await settle();
console.log(JSON.stringify(posts));
""")
    assert out == []


def test_phone_and_wide_hydration_and_local_copies_stay_separate():
    out = execute("""
answer={prefs:{chat_cloud:SERVER}}; refreshChatCloud(); await settle();
stage.clientWidth=390;
answer={prefs:{chat_cloud:{...SERVER,mode:'bubble',bubble:{x:30,y:40}}}};
refreshChatCloud(); await settle();
const phone={mode:cloudState.mode,bubble:cloudState.bubble};
stage.clientWidth=1280; answer={prefs:{chat_cloud:SERVER}};
refreshChatCloud(); await settle();
console.log(JSON.stringify({phone,wide:cloudState.open,reads,posts,keys:Object.keys(store)}));
""")
    assert out["phone"] == {"mode": "bubble", "bubble": {"x": 30, "y": 40}}
    assert out["wide"]["x"] == 300
    assert out["reads"] == ["/app/ui-prefs?agent=main&viewport=wide",
                            "/app/ui-prefs?agent=main&viewport=phone",
                            "/app/ui-prefs?agent=main&viewport=wide"]
    assert out["posts"] == []
    assert len(out["keys"]) == 2


def test_custom_agents_keep_local_placement_without_using_main_server_record():
    out = execute("""
nodes['chat-cloud'].dataset.agent='custom';
answer={prefs:{chat_cloud:SERVER}};
refreshChatCloud(); setChatCloudMode('bubble'); await settle();
console.log(JSON.stringify({reads,posts,
  saved:JSON.parse(store['app.chatCloud.v1:alice:custom:wide']).mode}));
""")
    assert out == {"reads": [], "posts": [], "saved": "bubble"}


def test_malformed_server_record_does_not_trigger_migration_or_destroy_local_copy():
    out = execute("""
store['app.chatCloud.v1:alice:main:wide']=JSON.stringify({...SERVER,mode:'bubble'});
answer={prefs:{chat_cloud:{mode:'open'}}};
refreshChatCloud(); await settle();
console.log(JSON.stringify({mode:cloudState.mode,posts}));
""")
    assert out == {"mode": "bubble", "posts": []}


def test_repeated_placements_reach_server_in_order_and_snapshot_their_geometry():
    network = NET.replace("const fetch=async(path,opts)=>{",
                          "let release;\nconst fetch=async(path,opts)=>{")
    network = network.replace("return {ok:true,json:async()=>({saved:true})};",
                              "if(posts.length===1) await new Promise(r=>{release=r;});\n"
                              "return {ok:true,json:async()=>({saved:true})};")
    out = execute("""
answer={prefs:{}}; refreshChatCloud(); await settle();
setChatCloudMode('bubble'); await settle();
cloudState.open.x=123; setChatCloudMode('open'); await settle();
const waiting=posts.length;
release(); await settle();
console.log(JSON.stringify({waiting,posts}));
""", network)
    assert out["waiting"] == 1
    assert [p["value"]["mode"] for p in out["posts"]] == ["bubble", "open"]
    assert out["posts"][0]["value"]["open"]["x"] != 123
    assert out["posts"][1]["value"]["open"]["x"] == 123


def test_queued_old_owner_write_is_dropped_and_new_owner_keeps_their_own_bearer():
    network = NET.replace("const fetch=async(path,opts)=>{",
                          "let release;\nconst fetch=async(path,opts)=>{")
    network = network.replace("return {ok:true,json:async()=>({saved:true})};",
                              "if(posts.length===1) await new Promise(r=>{release=r;});\n"
                              "return {ok:true,json:async()=>({saved:true})};")
    out = execute("""
answer={prefs:{}}; refreshChatCloud(); await settle();
setChatCloudMode('bubble'); await settle();
setChatCloudMode('open');
setQueueOwner('bob'); MCP._loginEpoch++; await settle();
setChatCloudMode('bubble'); release(); await settle();
console.log(JSON.stringify(posts.map(p=>({bearer:p.bearer,mode:p.value.mode}))));
""", network)
    assert out == [{"bearer": "Bearer alice", "mode": "bubble"},
                   {"bearer": "Bearer bob", "mode": "bubble"}]


def test_drag_callbacks_from_previous_owner_cannot_move_or_save_next_owners_cloud():
    out = execute("""
answer={prefs:{}}; refreshChatCloud(); await settle();
const handlers={};
const handle={addEventListener(k,v){handlers[k]=v;},removeEventListener(k){delete handlers[k];},
  setPointerCapture(){}};
wireCloudDrag(handle,'resize');
handlers.pointerdown({button:0,clientX:500,clientY:500,pointerId:1});
setQueueOwner('bob'); MCP._loginEpoch++; await settle();
const before=JSON.stringify(cloudState);
handlers.pointermove({clientX:200,clientY:200,preventDefault(){}});
handlers.pointerup({type:'pointerup'}); await settle();
console.log(JSON.stringify({same:before===JSON.stringify(cloudState),posts,store,
  remaining:Object.keys(handlers)}));
""")
    assert out == {"same": True, "posts": [], "store": {}, "remaining": ["pointerdown"]}
