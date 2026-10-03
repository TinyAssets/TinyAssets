"""The blank bundle offers packages; only the owner's existing ask can install."""
# ruff: noqa: F811 -- imported pytest fixtures are injected as test parameters

import json
import re
import sqlite3
from pathlib import Path

import pytest

from tests.test_command_center_packages import (  # noqa: F401
    BOB,
    BOB_UNIVERSE,
    OWNER,
    UNIVERSE,
    _answer,
    _as,
    _ask,
    _bob_files,
    _bobs_branches,
    _pin_data_dir,
    _publish_action,
    cloud_runtime,
    home,
)
from tinyassets.api.graph_reads import read_graph
from tinyassets.api.pending_requests import list_requests, try_package
from tinyassets.command_center_picker import BUILD_PROMPT, PLATFORM_DEFAULT_UI, working_packages

pytestmark = pytest.mark.usefixtures("cloud_runtime")


def _publish(name="First"):
    action = {**_publish_action(), "name": name}
    ask = _ask(OWNER, UNIVERSE, action)
    assert "request_id" in ask, ask
    done = _answer(OWNER, UNIVERSE, ask["request_id"])
    assert done.get("published"), done
    return done["agent_definition_id"]


def _read(actor=BOB, uid=BOB_UNIVERSE, door=read_graph):
    with _as(actor):
        return json.loads(door(target="command_center_packages", graph_id=uid))


def test_both_doors_offer_two_latest_packages(home):
    from tinyassets.universe_server import read_graph as model_read

    first = _publish()
    assert _read()["can_try"] is True
    second = _publish("Second")
    for door in (read_graph, model_read):
        result = _read(door=door)
        assert {p["agent_definition_id"] for p in result["packages"]} == {first, second}
        assert result["can_try"] is True
        assert result["build_prompt"] == BUILD_PROMPT
    # A newer publication of a name replaces its older version, not another card.
    newer = _publish()
    result = _read()
    assert {p["agent_definition_id"] for p in result["packages"]} == {newer, second}
    assert result["packages"][0]["agent_definition_id"] == newer


@pytest.mark.parametrize("broken", ["blob", "private", "missing"])
def test_unhealthy_packages_are_not_offered(home, broken, caplog):
    from tinyassets.command_center_packages import _blob_path
    from tinyassets.custom_agents import get_definition

    definition = get_definition(home, _publish())
    if broken == "blob":
        _blob_path(home, definition["components"]["package"]["blob_sha256"]).unlink()
    else:
        version = next(c["published_version_id"] for c in definition["components"].values()
                       if c["kind"] == "tinyassets.branch-ref.v1")
        from tinyassets.branch_versions import _connect

        with _connect(home) as conn:
            if broken == "private":
                conn.execute("UPDATE branch_versions SET public = 0 WHERE branch_version_id = ?",
                             (version,))
            else:
                conn.execute("DELETE FROM branch_versions WHERE branch_version_id = ?", (version,))
    assert _read()["packages"] == []
    assert "Skipping unhealthy" in caplog.text


def test_read_requires_the_named_centers_owner(home):
    from tinyassets.api.http_connection import _NOT_FOUND
    from tinyassets.universe_server import read_graph as model_read

    for door in (read_graph, model_read):
        assert _read(actor=OWNER, door=door) == _NOT_FOUND
        assert _read(uid="missing", door=door) == _NOT_FOUND


def test_default_is_a_valid_bundle_and_is_not_in_storage(home):
    from tinyassets.custom_agents import _check_app_ui_fields, get_app_ui

    with _as(BOB):
        doc = json.loads(read_graph(target="app_ui", graph_id=BOB_UNIVERSE))
        index = json.loads(read_graph(target="app_ui", graph_id=BOB_UNIVERSE, query="index"))
    bundle = doc["app_ui"]["platform_default"]
    assert bundle == PLATFORM_DEFAULT_UI
    assert bundle["ui_id"] == "platform:blank"
    _check_app_ui_fields({"ui_library": [bundle]})
    stored = get_app_ui(home, owner_user_id=BOB, universe_id=BOB_UNIVERSE)
    assert stored["ui_library"] == [] and stored["revision"] == 0
    assert "platform_default" not in stored
    assert "platform_default" not in index["app_ui"]


def test_try_only_asks_until_the_owner_confirms(home):
    from tinyassets.universe_server import write_graph

    definition_id = _publish()
    before = _bob_files(home)
    assert not _bobs_branches(home)
    with _as(BOB):
        ask = json.loads(write_graph(target="connection", operation="try_package",
                                    graph_id=BOB_UNIVERSE, payload_json=json.dumps(
                                        {"agent_definition_id": definition_id})))
        requests = list_requests(universe_id=BOB_UNIVERSE)["pending"]
    assert set(ask) == {"request_id", "title"}
    pending = next(r for r in requests if r["request_id"] == ask["request_id"])
    assert pending["action"]["type"] == "install"
    assert not _bobs_branches(home) and _bob_files(home) == before
    done = _answer(BOB, BOB_UNIVERSE, ask["request_id"])
    assert done.get("installed"), done
    assert _bobs_branches(home) and _bob_files(home) != before


def test_try_refuses_unknown_package_and_other_owners_center(home):
    from tinyassets.api.http_connection import _NOT_FOUND

    definition_id = _publish()
    with _as(BOB):
        assert "error" in try_package(universe_id=BOB_UNIVERSE,
                                      payload={"agent_definition_id": "not-working"})
        assert try_package(universe_id=UNIVERSE,
                           payload={"agent_definition_id": definition_id}) == _NOT_FOUND


def test_list_failure_warns_and_returns_no_offers(monkeypatch, caplog):
    def fail(**kwargs):
        raise sqlite3.OperationalError("unavailable")

    monkeypatch.setattr("tinyassets.api.package_requests.list_packages", fail)
    assert working_packages() == []
    assert "Could not list" in caplog.text


def test_static_bridge_contract():
    source = Path("tinyassets/onboarding/app_ui.js").read_text(encoding="utf-8")
    actions = source.split("ACTIONS:Object.freeze({", 1)[1].split("})", 1)[0]
    for action in ("packages.list_tryable", "packages.try", "chat.prefill"):
        assert f'"{action}":' in actions
    values = re.findall(r':"([^"]+)"', actions)
    assert not any(word in value.lower() for value in values
                   for word in ("answer", "approve", "withdraw"))
    prefill = source.split("prefillChat(args){", 1)[1].split("\n    },", 1)[0]
    assert "chatCloudPrefill" in prefill and "document." not in prefill
    assert "MAX_MESSAGE" in prefill and "mountDefault(){" in source
    bundle = json.dumps(PLATFORM_DEFAULT_UI)
    for text in ("Build your own", "Try someone else's"):
        assert text in bundle
    assert "Not now" not in bundle
    assert "No thanks" not in bundle and 'id="dismiss"' not in PLATFORM_DEFAULT_UI["markup"]


def test_executed_bridge_scopes_asks_and_only_prefills(tmp_path):
    from tests.test_custom_ui_bridge import _run

    checks = r'''
(async()=>{
const u=AppUI;
u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;
u.platformDefault=DEFAULT_BUNDLE;u.mountDefault();
assert(u.parseBundle(DEFAULT_BUNDLE).ok);
assert.equal(u.active.ui_id,'platform:blank');
assert.equal(u.frame.attrs.sandbox,u.SANDBOX);
assert.equal(u.library.length,0);
u.deliver();assert.deepEqual(u.frame.contentWindow.posts[0].bundle,
 {markup:DEFAULT_BUNDLE.markup,style:DEFAULT_BUNDLE.style,script:DEFAULT_BUNDLE.script});
assert.throws(()=>u.prefillChat({text:'build'}),/chat is not available/);
let text='';global.chatCloudPrefill=t=>{text=t;};
u.prefillChat({text:'build'});assert.equal(text,'build');assert.equal(sends.length,0);
assert.throws(()=>u.prefillChat({text:'x'.repeat(u.MAX_MESSAGE+1)}),/too long/);
let release;
MCP.callTool=async(tool,args)=>{
 assert.equal(tool,'write_graph');assert.equal(args.target,'connection');
 assert.equal(args.operation,'try_package');assert.equal(args.graph_id,HOME);
 assert.deepEqual(JSON.parse(args.payload_json),{agent_definition_id:'d1'});
 await new Promise(r=>release=r);return {request_id:'r1',title:'Install',secret:'hidden'};
};
const ask=u.tryPackage({agent_definition_id:'d1',graph_id:'other',operation:'answer_request'});
await assert.rejects(u.tryPackage({agent_definition_id:'d1'}),/already in flight/);
release();assert.deepEqual(await ask,{request_id:'r1'});
Owner.read=async args=>{
 assert.deepEqual(args,{target:'command_center_packages',graph_id:HOME});
 return {packages:[{agent_definition_id:'d1',name:'One',description:'',author_id:'Alice',
 version:1,size:'1 KB',file_count:1,needs:{model:'',connections:[],secret:'hidden'},
 secret:'hidden'}],build_prompt:'build',can_try:true,secret:'hidden'};
};
const list=await u.listTryablePackages();assert.equal(list.can_try,true);
assert(!JSON.stringify(list).includes('secret'));
Owner.read=async()=>({packages:[],build_prompt:'build',can_try:true});
await assert.rejects(u.listTryablePackages(),/unavailable/);
console.log('picker bridge passed');
})().catch(e=>{console.error(e);process.exit(1);});
'''
    out = _run(tmp_path, "picker.js", checks,
               extra="const DEFAULT_BUNDLE=" + json.dumps(PLATFORM_DEFAULT_UI) + ";\n")
    assert "picker bridge passed" in out
# -- lead decision: installing and composing are the PLATFORM's, not a UI's ----


def test_only_the_platform_bundle_may_install_or_prefill(tmp_path):
    """``packages.try`` and ``chat.prefill`` are refused to a third-party UI.

    They are the app's own offer to the owner: one installs software as them,
    the other composes a message as them. A UI someone else wrote must not be
    able to reach either, and the check is on the bundle MOUNTED NOW -- a
    bundle cannot name itself ``platform:blank`` to acquire them, because
    ID_RE forbids the colon everywhere except the row the server sends.
    ``packages.list_tryable`` stays open: it only reads what is published.
    """
    from tests.test_custom_ui_bridge import _run

    checks = r"""
(async()=>{
const u=AppUI;
u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;
let prefilled=0;global.chatCloudPrefill=()=>{prefilled++;};
let installs=0;
MCP.callTool=async()=>{installs++;return {request_id:'r1'};};
Owner.read=async()=>({packages:[],build_prompt:'build',can_try:false});

const ask=async(action,params)=>{
 const win=u.frame.contentWindow,before=win.posts.length;
 u.receive({source:win,data:{ta_ui:1,type:'call',id:'q'+before,action,params:params||{}}});
 for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));
 assert(win.posts.length>before,'the bridge always answers '+action);
 return win.posts[win.posts.length-1];
};

// A UI the owner installed. It may read the offers and nothing more.
u.mount({kind:u.KIND,version:1,ui_id:'third-party',name:'Theirs',
 markup:'<p>x</p>',style:'',script:''});
assert.equal(u.isPlatformDefault(),false);
const listed=await ask('packages.list_tryable',{});
assert.equal(listed.ok,true,'reading the offers stays open to any UI');
for(const action of ['packages.try','chat.prefill']){
 const answer=await ask(action,{agent_definition_id:'d1',text:'build'});
 assert.equal(answer.ok,false,action+' must be refused to a third-party UI');
 assert.match(answer.error,/action not available/);
}
assert.equal(installs,0,'nothing was installed');
assert.equal(prefilled,0,'nothing was composed');

// Naming itself the platform's does not make it so: parseBundle refuses the
// colon, so such a bundle can never become `active` through install.
assert.equal(u.parseBundle({kind:u.KIND,version:1,ui_id:'platform:blank',
 name:'Impostor',markup:'',style:'',script:''}).ok,true,
 'the server-sent row parses');
u.mount({kind:u.KIND,version:1,ui_id:'platform:blank',name:'Impostor',
 markup:'',style:'',script:''});
assert.equal(u.isPlatformDefault(),false,
 'only mountDefault grants the platform identity, not the id alone');
const stolen=await ask('chat.prefill',{text:'build'});
assert.equal(stolen.ok,false);
assert.equal(prefilled,0);

// The platform's own blank command center may ask.
u.platformDefault=DEFAULT_BUNDLE;u.mountDefault();
assert.equal(u.isPlatformDefault(),true);
const allowed=await ask('chat.prefill',{text:'build'});
assert.equal(allowed.ok,true);
assert.equal(prefilled,1);
const install=await ask('packages.try',{agent_definition_id:'d1'});
assert.equal(install.ok,true);
assert.equal(installs,1);

// ...and loses it the moment another bundle takes the screen.
u.mount({kind:u.KIND,version:1,ui_id:'third-party',name:'Theirs',
 markup:'<p>x</p>',style:'',script:''});
assert.equal(u.isPlatformDefault(),false);
const after=await ask('packages.try',{agent_definition_id:'d1'});
assert.equal(after.ok,false);
assert.equal(installs,1);
console.log('picker authority passed');
})().catch(e=>{console.error(e);process.exit(1);});
"""
    out = _run(tmp_path, "picker_authority.js", checks,
               extra="const DEFAULT_BUNDLE=" + json.dumps(PLATFORM_DEFAULT_UI) + ";\n")
    assert "picker authority passed" in out


def test_the_allowlist_names_exactly_the_platform_only_actions():
    """The gate's list is pinned: adding a platform action to ACTIONS without
    adding it here would hand it to every UI."""
    source = Path("tinyassets/onboarding/app_ui.js").read_text(encoding="utf-8")
    block = source.split("PLATFORM_ONLY:[", 1)[1].split("]", 1)[0]
    assert sorted(re.findall(r'"([^"]+)"', block)) == ["chat.prefill", "packages.try"]
    assert 'PLATFORM_UI_ID:"platform:blank"' in source
    # Enforced in serve(), the one place every call passes through.
    served = source.split("async serve(id,action,params){", 1)[1].split("\n    },", 1)[0]
    assert "this.PLATFORM_ONLY.indexOf(action)>=0 && !this.isPlatformDefault()" in served
def test_a_platform_request_does_nothing_once_another_bundle_takes_the_screen(tmp_path):
    """verify() is a server round-trip. The owner can replace the bundle while
    it is in flight, and the generation checks used to run only on the way
    OUT: the reply was discarded but the WORK had already happened.
    gpt-6-astra reproduced chat.prefill running with a third-party bundle on
    screen. The effect belongs to the bundle that asked, so serve() re-checks
    after the await and before the method.
    """
    from tests.test_custom_ui_bridge import _run

    checks = r"""
(async()=>{
const u=AppUI;
u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;
let prefilled=0;global.chatCloudPrefill=()=>{prefilled++;};
let installs=0;
MCP.callTool=async()=>{installs++;return {request_id:'r1'};};

// verify() is held open so the swap lands between the gate and the method.
let releaseVerify;
const held=new Promise(r=>{releaseVerify=r;});
u.verify=async()=>{ await held; };

u.platformDefault=DEFAULT_BUNDLE;u.mountDefault();
assert.equal(u.isPlatformDefault(),true);
const win=u.frame.contentWindow,before=win.posts.length;

// The platform asks for both privileged actions...
u.receive({source:win,data:{ta_ui:1,type:'call',id:'p1',action:'chat.prefill',
 params:{text:'build'}}});
u.receive({source:win,data:{ta_ui:1,type:'call',id:'p2',action:'packages.try',
 params:{agent_definition_id:'d1'}}});
for(let i=0;i<4;i++)await new Promise(r=>setImmediate(r));
assert.equal(prefilled,0,'nothing runs while verify is in flight');
assert.equal(installs,0);

// ...and the owner switches bundles before verify answers.
u.mount({kind:u.KIND,version:1,ui_id:'third-party',name:'Theirs',
 markup:'<p>x</p>',style:'',script:''});
assert.equal(u.isPlatformDefault(),false);
releaseVerify();
for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));

assert.equal(prefilled,0,'the composer was never filled for the replaced bundle');
assert.equal(installs,0,'no install was requested for the replaced bundle');
// Neither frame is told anything: the asker is gone and the new frame never asked.
assert.equal(win.posts.length,before,'the replaced frame gets no reply');
assert.equal(u.frame.contentWindow.posts.length,0,'the new frame gets no reply');
console.log('picker fence passed');
})().catch(e=>{console.error(e);process.exit(1);});
"""
    out = _run(tmp_path, "picker_fence.js", checks,
               extra="const DEFAULT_BUNDLE=" + json.dumps(PLATFORM_DEFAULT_UI) + ";\n")
    assert "picker fence passed" in out


def test_every_branch_that_says_default_chat_mounts_it(tmp_path):
    """"Default chat is in use" has to be true.

    An unreadable library, an unreadable selection and a selection naming a
    missing entry all claimed it while mounting nothing, which left the stage
    empty with the explanation inside a dialog that is normally closed
    (gpt-6-astra on #4358, reproduced). The recovery button was disabled in
    exactly that state too.
    """
    from tests.test_custom_ui_bridge import _run

    checks = r"""
(async()=>{
const u=AppUI;
const row=extra=>Object.assign({universe_id:HOME,revision:1,
 platform_default:DEFAULT_BUNDLE},extra);
const cases={
 'unreadable library':row({ui_library:'nonsense',ui_selection:null}),
 'unreadable selection':row({ui_library:[],ui_selection:'nonsense'}),
 'missing entry':row({ui_library:[],ui_selection:{version:1,state:'active',ui_id:'gone'}}),
};
for(const [name,doc] of Object.entries(cases)){
 u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;u.busy=false;
 u.adopt(doc);
 assert(u.frame,name+': the stage must hold the default, not nothing');
 assert.equal(u.isPlatformDefault(),true,name+': and it is the platform bundle');
 assert(/Default chat is in use/.test($('ui-status').textContent),
  name+': and it says so, got '+$('ui-status').textContent);
}
console.log('picker fallback passed');
})().catch(e=>{console.error(e);process.exit(1);});
"""
    out = _run(tmp_path, "picker_fallback.js", checks,
               extra="const DEFAULT_BUNDLE=" + json.dumps(PLATFORM_DEFAULT_UI) + ";\n")
    assert "picker fallback passed" in out


def test_an_unrenderable_choice_mounts_default_and_preserves_the_library(tmp_path):
    """A broken selected UI leaves the platform offer and usable UIs available."""
    from tests.test_custom_ui_bridge import _run

    checks = r"""
(async()=>{
const u=AppUI;
u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;
const broken={...bundleOf({ui_id:'broken',name:'Broken screen'}),version:1791005187};
const good=bundleOf({ui_id:'office',name:'Office'});
appUi={...stored([broken,good],{version:1,state:'active',ui_id:'broken'}),
 platform_default:DEFAULT_BUNDLE};
const before=clone(appUi);
u.adopt(clone(appUi));
assert(u.frame,'the stage mounts the fallback instead of staying empty');
assert.equal(u.isPlatformDefault(),true,'the fallback is the platform bundle');
assert.equal($('ui-frame-host').hidden,false,'the fallback is visible');
assert.equal(u.active.ui_id,'platform:blank');
const why=$('ui-status').textContent;
assert.match(why,/Broken screen cannot be shown/);
assert.match(why,/1791005187/);
assert.match(why,/Default chat is in use/);
assert.match(why,/your other UIs still work/);
assert(!/no longer installed/.test(why),'the broken screen is still installed');
assert.deepEqual(u.library.map(entry=>entry.ui_id),['office']);
assert.equal(u.broken.length,1);
assert.equal(u.selection.ui_id,'broken','fallback does not rewrite the saved choice');
assert.deepEqual(appUi,before,'displaying the fallback does not write storage');
await u.choose('office');
assert.equal(u.active.ui_id,'office','the working UI remains selectable');
assert.equal(u.isPlatformDefault(),false,'the replacement does not gain platform authority');
assert.deepEqual(appUi.ui_library.find(entry=>entry.ui_id==='broken'),broken,
 'switching leaves the broken component intact');
console.log('spoiled picker fallback passed');
})().catch(e=>{console.error(e);process.exit(1);});
"""
    out = _run(tmp_path, "picker_spoiled_fallback.js", checks,
               extra="const DEFAULT_BUNDLE=" + json.dumps(PLATFORM_DEFAULT_UI) + ";\n")
    assert "spoiled picker fallback passed" in out


@pytest.mark.parametrize("count", [0, 1, 2])
def test_try_offer_tracks_usable_package_count_through_both_doors(home, count):
    from tinyassets.universe_server import read_graph as model_read

    ids = {_publish(f"Package {i}") for i in range(count)}
    for door in (read_graph, model_read):
        result = _read(door=door)
        assert {p["agent_definition_id"] for p in result["packages"]} == ids
        assert result["can_try"] is bool(count)
        assert result["build_prompt"] == BUILD_PROMPT


def test_package_discovery_filters_author_and_search_without_changing_publications(home):
    from tinyassets.api.package_requests import list_packages

    village, office = _publish("Zebra Observatory"), _publish("Office")
    with _as(BOB):
        assert {p["agent_definition_id"] for p in list_packages(author=OWNER)} == {village, office}
        assert list_packages(author=BOB) == []
        matches = list_packages(query="Zebra Observatory")
        assert [p["agent_definition_id"] for p in matches] == [village]
        assert list_packages(query="no-such-package") == []
        assert list_packages(author=BOB, query="Zebra Observatory") == []
        assert {p["agent_definition_id"] for p in list_packages()} == {village, office}


def test_broken_latest_publication_does_not_resurrect_stale_version(home):
    from tinyassets.command_center_packages import _blob_path
    from tinyassets.custom_agents import get_definition

    old = _publish("Village")
    newest = _publish("Village")
    assert newest != old
    definition = get_definition(home, newest)
    _blob_path(home, definition["components"]["package"]["blob_sha256"]).unlink()
    result = _read()
    assert result["packages"] == []
    assert result["can_try"] is False


def test_picker_bridge_validates_counts_and_malformed_summaries(tmp_path):
    from tests.test_custom_ui_bridge import _run

    checks = r"""
(async()=>{
const u=AppUI;u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;
const row={agent_definition_id:'d1',name:'One',description:'',author_id:'Alice',
 version:1,size:'1 KB',file_count:1,needs:{model:'',connections:[]}};
for(const count of [0,1,2]){
 const packages=Array.from({length:count},(_,i)=>({...row,agent_definition_id:'d'+i}));
 Owner.read=async()=>({packages,build_prompt:'build',can_try:count>0});
 const doc=await u.listTryablePackages();
 assert.equal(doc.can_try,count>0);assert.equal(doc.packages.length,count);
 Owner.read=async()=>({packages,build_prompt:'build',can_try:count===0});
 await assert.rejects(u.listTryablePackages(),/unavailable/);
}
for(const bad of [null,{...row,version:0},{...row,needs:{model:'',connections:[1]}},
 {...row,agent_definition_id:''},{...row,file_count:-1}]){
 Owner.read=async()=>({packages:[bad],build_prompt:'build',can_try:true});
 await assert.rejects(u.listTryablePackages(),/invalid command-center package/);
}
console.log('picker counts and malformed summaries passed');
})().catch(e=>{console.error(e);process.exit(1);});
"""
    out = _run(tmp_path, "picker_counts.js", checks)
    assert "picker counts and malformed summaries passed" in out


@pytest.mark.parametrize("changed", ["frame", "home", "epoch"])
def test_package_reply_does_not_cross_a_changed_view(tmp_path, changed):
    from tests.test_custom_ui_bridge import _run

    checks = r"""
(async()=>{
const u=AppUI;u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;
u.platformDefault=DEFAULT_BUNDLE;u.mountDefault();u.verify=async()=>{};
const win=u.frame.contentWindow,before=win.posts.length;
let release;
Owner.read=async args=>{
 assert.equal(args.graph_id,HOME);
 return await new Promise(resolve=>{release=resolve;});
};
const reply=u.serve('late','packages.list_tryable',{});
await settle();assert.equal(typeof release,'function');
if(CHANGED==='frame')u.mount({kind:u.KIND,version:1,ui_id:'other',name:'Other',
 markup:'',style:'',script:''});
if(CHANGED==='home')u.home='u-other';
if(CHANGED==='epoch')u.epoch++;
release({packages:[],build_prompt:'build',can_try:false});await reply;
assert.equal(win.posts.length,before,'no stale reply reaches the asking frame');
assert.equal(u.frame.contentWindow.posts.length,0,'no stale reply reaches the current frame');
console.log('stale picker reply refused');
})().catch(e=>{console.error(e);process.exit(1);});
"""
    out = _run(tmp_path, "picker_stale.js", checks,
               extra="const DEFAULT_BUNDLE=" + json.dumps(PLATFORM_DEFAULT_UI)
               + ";const CHANGED=" + json.dumps(changed) + ";\n")
    assert "stale picker reply refused" in out


def test_workflow_references_are_validated_and_belong_only_to_the_active_bundle(tmp_path):
    from tests.test_custom_ui_bridge import _run

    checks = r"""
(async()=>{
const u=AppUI;u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;
const bundle={kind:u.KIND,version:1,ui_id:'refs',name:'References',
 markup:'',style:'',script:'const unchanged="workflow-old";'};
assert(u.parseBundle(bundle).ok);
for(const refs of [null,[],{'1bad':'id'},{'x.y':'id'},{['a'.repeat(65)]:'id'},
 {x:''},{x:1},{x:'a'.repeat(201)},Object.fromEntries(Array.from({length:101},(_,i)=>['x'+i,'id']))]){
 const parsed=u.parseBundle({...bundle,workflow_refs:refs});
 assert.equal(parsed.ok,false,JSON.stringify(refs));assert.match(parsed.reason,/workflow_refs/);
}
const refs={Scout:'copied-scout',daily_report:'copied-report','run-2':'copied-two'};
const parsed=u.parseBundle({...bundle,workflow_refs:refs});assert(parsed.ok);
assert.equal(parsed.bundle.script,bundle.script,'workflow metadata never rewrites script bytes');
refs.Scout='changed';assert.equal(parsed.bundle.workflow_refs.Scout,'copied-scout');
u.mount(parsed.bundle);
const answer=await u.whoami();
assert.deepEqual(answer.workflow_refs,{Scout:'copied-scout',daily_report:'copied-report','run-2':'copied-two'});
answer.workflow_refs.Scout='stolen';
assert.equal((await u.whoami()).workflow_refs.Scout,'copied-scout');
assert.equal(u.isPlatformDefault(),false,'metadata grants no platform privilege');
assert.equal(u.ACTIONS['workflows.run'],undefined,'metadata adds no workflow execution action');
u.mount({...bundle,ui_id:'second',workflow_refs:{Other:'second-id'}});
assert.deepEqual((await u.whoami()).workflow_refs,{Other:'second-id'});
u.mount({...bundle,ui_id:'plain'});assert.deepEqual((await u.whoami()).workflow_refs,{});
const max=Object.fromEntries(Array.from({length:100},(_,i)=>['x'+i,'a'.repeat(200)]));
assert(u.parseBundle({...bundle,workflow_refs:max}).ok);
console.log('active workflow metadata passed');
})().catch(e=>{console.error(e);process.exit(1);});
"""
    assert "active workflow metadata passed" in _run(tmp_path, "picker_workflow_refs.js", checks)


def test_default_bundle_build_executes_shipped_script_and_composer_prefill(tmp_path):
    """Execute the actual bundle and page prefill function; Node supplies the DOM.

    The separate browser test proves native iframe clicks, focus and selection.
    Here the actual bridge still verifies identity and refuses a replacement UI.
    """
    from tests.test_app_browser_notifications import functions
    from tests.test_custom_ui_bridge import _run

    checks = r"""
(async()=>{
const u=AppUI;u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;
u.platformDefault=DEFAULT_BUNDLE;u.mountDefault();
let focused='',selection=null;
const input=$('composer-input');input.value='existing draft';
input.focus=()=>{focused='composer-input';document.activeElement=input;};
input.setSelectionRange=(start,end)=>{selection=[start,end];};
Owner.read=async args=>{
 assert.deepEqual(args,{target:'command_center_packages',graph_id:HOME});
 return {packages:[],build_prompt:BUILD_PROMPT,can_try:false};
};
const nodes={};
for(const id of ['offer','message','build','try-one','packages','dismiss'])
 nodes[id]={hidden:false,textContent:''};
const actions=[];
const tinyassets={call:async(action,params)=>{
 actions.push({action,params});
 const win=u.frame.contentWindow,before=win.posts.length;
 await u.serve('default-'+actions.length,action,params);
 assert.equal(win.posts.length,before+1,'actual bridge returns to its asking frame');
 const reply=win.posts[before];
 if(!reply.ok)throw new Error(reply.error);
 return reply.result;
}};
await require('node:vm').runInNewContext(DEFAULT_BUNDLE.script,{
 document:{getElementById:id=>nodes[id]},tinyassets});
assert.equal(nodes['try-one'].hidden,false);
nodes['try-one'].onclick();
assert.equal(nodes.packages.hidden,false);
assert.match(nodes.packages.textContent,/No shared command centers are available yet/);
assert.equal(typeof nodes.build.onclick,'function','shipped script registered Build');
await nodes.build.onclick();
assert.equal(input.value,BUILD_PROMPT);
assert.equal(focused,'composer-input');
assert.deepEqual(selection,[BUILD_PROMPT.length,BUILD_PROMPT.length]);
assert.equal(cloudState.mode,'open');
assert.equal(sends.length,0,'Build composes without calling sendTurn');
assert.deepEqual(actions.map(a=>a.action),['packages.list_tryable','chat.prefill']);
assert.equal(actions[1].params.text,BUILD_PROMPT);
assert.equal(nodes.message.textContent,'');
console.log('actual default Build and composer passed');
})().catch(e=>{console.error(e);process.exit(1);});
"""
    extra = (
        "const DEFAULT_BUNDLE=" + json.dumps(PLATFORM_DEFAULT_UI)
        + ";const BUILD_PROMPT=" + json.dumps(BUILD_PROMPT) + ";\n"
        + "let cloudState={mode:'bubble'};\n"
        + "function setChatCloudMode(mode){cloudState.mode=mode;}\n"
        + functions("chatCloudPrefill") + "\n"
    )
    out = _run(tmp_path, "actual_default_build.js", checks, extra=extra)
    assert "actual default Build and composer passed" in out


def test_trusted_switcher_keeps_build_browse_and_own_choices_across_reload_and_accounts(tmp_path):
    from tests.test_custom_ui_bridge import _run

    checks = r"""
(async()=>{
const u=AppUI;u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;
u.platformDefault=DEFAULT_BUNDLE;u.mountDefault();
const button=u.button;u.button=function(text,fn,disabled){
 const node=button.call(this,text,fn,disabled);node.onclick=fn;return node;
};
const all=node=>[node,...node.children.flatMap(all)];
const find=text=>all($('ui-list')).find(n=>n.tag==='button'&&n.textContent===text);
const navigation=()=>{
 for(const label of ['Build your own',"Try someone else's",'Blank command center']){
  const b=find(label);assert(b,label);assert.equal(b.disabled,false);
 }
};
const system={agent_definition_id:'legacy',publication_kind:'system',name:'Village',
 description:'Legacy public system',author_id:'alice',workflow_count:2,automation_count:2,
 available:true,unavailable_reason:'',secret:'must not escape'};
let catalogue={packages:[],systems:[],build_prompt:BUILD_PROMPT,can_try:false};
const ownerRead=Owner.read;
Owner.read=async args=>{
 if(args.target==='command_center_packages')return clone(catalogue);
 const reply=await ownerRead(args);
 if(args.target==='app_ui')reply.app_ui.platform_default=DEFAULT_BUNDLE;
 return reply;
};
u.open();navigation();await find("Try someone else's").onclick();
assert.match(u.sharedState,/No shared command centers/);navigation();
let composed='';global.chatCloudPrefill=text=>{
 assert.equal($('ui-dialog').open,false);composed=text;
};
find('Build your own').onclick();assert.equal(composed,BUILD_PROMPT);assert.equal(sends.length,0);
u.open();navigation();
const own={kind:u.KIND,version:1,ui_id:'own',name:'My own',markup:'<p>Mine</p>',style:'',script:''};
appUi=stored([own],null);await u.load();navigation();
await find('Use My own').onclick();assert.equal(u.active.ui_id,'own');navigation();
await u.load();assert.equal(u.active.ui_id,'own');navigation();
u.open();catalogue={...catalogue,systems:[system],can_try:true};
await find("Try someone else's").onclick();navigation();
assert.equal(u.sharedCatalogue.systems[0].secret,undefined);
assert(all($('ui-list')).some(n=>n.textContent.includes('Components only; no files')));
let preview=[];const call=MCP.callTool;
MCP.callTool=async(tool,args)=>{
 if(tool==='write_graph'&&args.operation==='try_package'){
  preview.push(args);return {request_id:'copy-1',title:'Copy Village'};
 }return call(tool,args);
};
await find('Preview component copy').onclick();assert.equal(preview.length,1);
assert.equal(preview[0].graph_id,HOME);assert.equal(sends.length,0);
assert.match($('ui-status').textContent,/Nothing installs before you confirm/);
await find('Blank command center').onclick();assert(u.isPlatformDefault());navigation();
u.open();navigation();
let release;Owner.read=async()=>new Promise(resolve=>release=resolve);
const pending=u.browseShared();await settle();u.reset();
u.enabled=true;u.home='bob-home';u.principal='bob';u.paint();
release(catalogue);await pending;
assert.equal(u.sharedCatalogue,null);assert.equal(u.sharedState,'');navigation();
assert(!all($('ui-list')).some(n=>n.textContent.includes('Village')));
Owner.read=async()=>{throw Error('failed');};await u.browseShared();
assert.match(u.sharedState,/could not be loaded/);navigation();
console.log('persistent trusted discovery passed');
})().catch(e=>{console.error(e);process.exit(1);});
"""
    extra = "const DEFAULT_BUNDLE=" + json.dumps(PLATFORM_DEFAULT_UI) + ";\n"
    extra += "const BUILD_PROMPT=" + json.dumps(BUILD_PROMPT) + ";\n"
    assert "persistent trusted discovery passed" in _run(
        tmp_path, "persistent_discovery.js", extra + checks)
