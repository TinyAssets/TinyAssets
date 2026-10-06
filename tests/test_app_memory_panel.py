"""Exercise the Account memory panel's own JavaScript under Node."""
import json
import shutil
import subprocess

import pytest

from tests.test_onboarding_app import _js_function
from tinyassets import onboarding

_NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    _NODE is None,
    reason="node is required; owner=Jonnyton; runs-in=GitHub-Actions/required-tests",
)


def test_memory_panel_load_edit_delete_undo_and_conflict(tmp_path):
    page, _ = onboarding.render_app_html()
    assert "loadMemory();" in _js_function(page, "showAccount")
    funcs = "\n".join(_js_function(page, name) for name in (
        "memoryContext", "ownsMemory", "clearTypedValues",
        "memoryRequest", "renderMemory", "loadMemory", "saveMemory"))
    script = tmp_path / "memory.js"
    script.write_text(r'''
const elements={}, calls=[];
const MCP={_loginEpoch:1}; let queueOwner="owner",queueScope="home";
function element(){return {children:[],value:"",disabled:false,
  set textContent(v){this.text=v;this.children=[];},get textContent(){return this.text||"";},
  appendChild(e){this.children.push(e);},setAttribute(){},
  addEventListener(event,fn){this[event]=fn;}};}
function $(id){return elements[id]||(elements[id]=element());}
const document={createElement:element};
function authHeaders(){return {Authorization:"Bearer owner"};}
let conflict=false;
async function fetch(path,init){
  calls.push({path,...init});
  return {ok:!conflict,status:conflict?409:200,json:async()=>conflict
    ?{detail:"MEMORY.md: conflict"}
    :{items:[{id:"m_abcd",text:"<script>plain text</script>"}],
      history:[{id:7,path:"MEMORY.md",prior_state:"present"}]}};
}
''' + funcs + r'''
(async()=>{
  await loadMemory();
  let row=$("memory-list").children[0];
  const original=row.children[0].value;
  row.children[0].value="Changed";
  await row.children[1].click();
  await $("memory-list").children[0].children[2].click();
  await $("btn-memory-undo").onclick();
  conflict=true;
  await saveMemory({undo:7});
  console.log(JSON.stringify({original,calls,status:$("memory-status").textContent}));
})();
''', encoding="utf-8")
    proc = subprocess.run([_NODE, str(script)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["original"] == "<script>plain text</script>"
    assert result["status"] == "MEMORY.md: conflict"
    calls = result["calls"]
    assert all(c["path"] == "/app/memory" and c["credentials"] == "same-origin" for c in calls)
    assert calls[0]["method"] == "GET"
    assert [json.loads(c["body"]) for c in calls[1:]] == [
        {"id": "m_abcd", "text": "Changed"}, {"delete": "m_abcd"}, {"undo": 7}, {"undo": 7}]


def _run_memory(tmp_path, scenario):
    page, _ = onboarding.render_app_html()
    funcs = "\n".join(_js_function(page, name) for name in (
        "memoryContext", "ownsMemory", "clearTypedValues", "clearMemoryState",
        "clearAccountScopedState", "memoryRequest", "renderMemory", "loadMemory", "saveMemory"))
    script = tmp_path / "memory-fence.js"
    script.write_text(r'''
const assert=require("node:assert/strict");
const elements={},calls=[],MCP={_loginEpoch:1};
let queueOwner="A",queueScope="home-A";
let historyLoaded=true,queueRestored=true,inflightRestored=true,uploadsRestored=true;
let sendQueue=[],sendQueueHeld=false,retainedItems=[];
const renderedConsumerTurns=new Set(),renderedConsumerFounders=new Set();
function clearComposerState(){} function clearThread(){} function clearRailCards(){}
function element(){return {children:[],value:"",disabled:false,onclick:null,
  set textContent(v){this.text=v;this.children=[];},get textContent(){return this.text||"";},
  appendChild(e){this.children.push(e);},setAttribute(){},
  addEventListener(event,fn){this[event]=fn;}};}
function $(id){return elements[id]||(elements[id]=element());}
const document={createElement:element};
function authHeaders(){return {Authorization:"Bearer "+queueOwner};}
// Both owners deliberately have the same row/history IDs: a stale Undo must
// never be reinterpreted as the new owner's valid history operation.
function doc(owner){return {items:[{id:"m_same",text:owner+" private memory"}],
  history:[{id:7,path:owner+"-private.md",prior_state:"present"}]};}
let nextResponse=null;
async function fetch(path,init){
  calls.push({path,...init});
  if(nextResponse){const pending=nextResponse;nextResponse=null;return pending;}
  const result=doc(queueOwner);
  return {ok:true,status:200,json:async()=>result};
}
function switchAccount(){
  MCP._loginEpoch++; clearAccountScopedState();
  queueOwner="B";queueScope="home-B";
}
''' + funcs + "\n(async()=>{\n" + scenario + r'''
console.log("ok");
})().catch(e=>{console.error(e);process.exitCode=1;});
''', encoding="utf-8")
    proc = subprocess.run([_NODE, str(script)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


@pytest.mark.parametrize("operation", ["loadMemory()", 'saveMemory({text:"A draft"})'])
@pytest.mark.parametrize("outcome", ["success", "error", "reject"])
def test_delayed_previous_account_response_cannot_touch_new_account(tmp_path, operation, outcome):
    _run_memory(tmp_path, r'''
await loadMemory();
let release,reject;
const delayed=new Promise((resolve,fail)=>{release=resolve;reject=fail;});
const outcome=OUTCOME;
// Success/error hold JSON decoding; rejection holds the fetch itself.
nextResponse=outcome==="reject"?delayed:
  {ok:outcome==="success",status:outcome==="success"?200:409,json:()=>delayed};
const pending=OPERATION;
await Promise.resolve();
switchAccount();
await loadMemory();
$("memory-new").value="B unsent draft";
$("memory-status").textContent="B status";
const currentUndo=$("btn-memory-undo").onclick;
if(outcome==="reject") reject(new Error("A network failure"));
else release(outcome==="success"?doc("A"):{detail:"A private conflict"});
await pending;
assert.equal($("memory-list").children[0].children[0].value,"B private memory");
assert.equal($("memory-new").value,"B unsent draft");
assert.equal($("memory-status").textContent,"B status");
assert.equal($("btn-memory-undo").textContent,"Undo latest change to B-private.md");
assert.equal($("btn-memory-undo").onclick,currentUndo);
assert.deepEqual(calls.map(c=>c.headers.Authorization),["Bearer A","Bearer A","Bearer B"]);
await currentUndo();
assert.equal(calls.length,4);
assert.equal(calls[3].headers.Authorization,"Bearer B");
assert.deepEqual(JSON.parse(calls[3].body),{undo:7});
'''.replace("OPERATION", operation).replace("OUTCOME", json.dumps(outcome)))


@pytest.mark.parametrize("transition", [
    "switchAccount();",
    'queueOwner="B";',
    'queueScope="home-B";',
    "MCP._loginEpoch++;",
])
def test_retained_controls_are_bound_to_login_owner_and_home(tmp_path, transition):
    _run_memory(tmp_path, r'''
await loadMemory();
const oldRow=$("memory-list").children[0];
const retainedSave=oldRow.children[1].click,retainedDelete=oldRow.children[2].click;
const retainedUndo=$("btn-memory-undo").onclick;
oldRow.children[0].value="A edited draft";
TRANSITION
await loadMemory();
const currentUndo=$("btn-memory-undo").onclick;
const before=calls.length;
await retainedSave(); await retainedDelete(); await retainedUndo();
assert.equal(calls.length,before,"stale controls must not read new auth or send a request");
assert.equal($("btn-memory-undo").onclick,currentUndo);
await currentUndo();
assert.equal(calls.length,before+1,"the current account's controls still work");
assert.deepEqual(JSON.parse(calls.at(-1).body),{undo:7});
'''.replace("TRANSITION", transition))


def test_account_reset_erases_memory_and_drafts_and_disarms_undo(tmp_path):
    _run_memory(tmp_path, r'''
await loadMemory();
const oldRow=$("memory-list").children[0];
const retainedUndo=$("btn-memory-undo").onclick;
oldRow.children[0].value="A edited private memory";
$("memory-new").value="A unsent private draft";
$("memory-status").textContent="A private error";
clearAccountScopedState();
assert.equal(oldRow.children[0].value,"");
assert.equal($("memory-list").textContent,"");
assert.equal($("memory-list").children.length,0);
assert.equal($("memory-new").value,"");
assert.equal($("memory-status").textContent,"");
assert.equal($("btn-memory-undo").disabled,true);
assert.equal($("btn-memory-undo").onclick,null);
assert.equal($("btn-memory-undo").textContent,"Undo latest change");
await retainedUndo();
await saveMemory({text:"signed out"});
assert.equal(calls.length,1);
''')
