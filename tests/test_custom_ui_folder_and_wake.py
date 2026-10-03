"""A custom UI reads its viewer's shared folder and wakes their subscribed agent.

Change `in-platform-agent-systems`. The shipped controller runs against the
shared server double. What is asserted is what the bridge SENDS for a hostile
bundle that names another universe or pushes past a bound, and what it RETURNS.
"""
# ruff: noqa: E501 -- embedded JavaScript fixture mirrors controller expressions
from __future__ import annotations

from tests.test_custom_ui_bridge import _run

FOLDER_DOUBLE = r'''
const FILES={'u-alice':{'notes/board.md':'# Board\n- scout: working'},
             'u-bob':{'notes/board.md':'BOBS PRIVATE BOARD'}};
let folderUniverseOverride='', emits=[], emitReply=null;
const baseCall2=MCP.callTool.bind(MCP);
MCP.callTool=async function(tool,args,opts){
 if(tool==='read_graph'&&(args.target==='command_center_files'||args.target==='command_center_file')){
  calls.push({tool,args:JSON.parse(JSON.stringify(args||{}))});
  const scope=folderUniverseOverride||args.graph_id,files=FILES[args.graph_id]||{};
  if(args.target==='command_center_files')
   return {universe_id:scope,path:args.query,entries:[{name:'board.md',kind:'file',size_bytes:24,owner:'x'},
     {name:'sub',kind:'dir',size_bytes:4096}],truncated:false};
  const text=files[args.query];
  if(text===undefined) return {error:'not_found',resource:'command_center_file'};
  return {universe_id:scope,path:args.query,size_bytes:text.length,offset:args.file_offset,
   length:text.length,next_offset:null,eof:true,encoding:'text',text,inode:42};
 }
 if(tool==='run_graph'&&args.operation==='emit_event'){
  calls.push({tool,args:JSON.parse(JSON.stringify(args||{}))});
  emits.push(JSON.parse(args.inputs_json));
  return emitReply||{emitted:true,name:JSON.parse(args.inputs_json).name,woke:1,subscription_ids:['leak']};
 }
 return baseCall2(tool,args,opts);
};
'''

CHECKS = r'''
(async()=>{
const u=AppUI;
appUi=stored([bundleOf()],{version:1,state:'active',ui_id:'office'});
u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;u.adopt(clone(appUi));
const win=u.frame.contentWindow;
emit({source:win,data:{ta_ui:1,type:'ready'}});
const ask=async(action,params)=>{
 const before=win.posts.length;
 emit({source:win,data:{ta_ui:1,type:'call',id:'q'+before,action,params:params||{}}});
 for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));
 assert(win.posts.length>before,'the bridge always answers '+action);
 return win.posts[win.posts.length-1];
};

// ---- the folder: pinned, picked, checked ---------------------------------
calls=[];
const listed=await ask('list_files',{path:'notes',graph_id:'u-bob',universe_id:'u-bob'});
assert.equal(listed.ok,true,listed.error);
const listCall=calls.filter(c=>c.args.target==='command_center_files').pop();
assert.equal(listCall.args.graph_id,HOME,'graph_id came from the viewer, not the bundle');
assert(!('universe_id' in listCall.args));
assert.deepEqual(listed.result.entries,[{name:'board.md',kind:'file',size_bytes:24},{name:'sub',kind:'dir'}]);

const read=await ask('read_file',{path:'notes/board.md',graph_id:'u-bob'});
assert.equal(read.ok,true,read.error);
const readCall=calls.filter(c=>c.args.target==='command_center_file').pop();
assert.equal(readCall.args.graph_id,HOME);
assert.equal(readCall.args.file_max_bytes,u.MAX_FILE_CHUNK,'a bundle cannot raise the chunk');
assert.equal(read.result.content,'# Board\n- scout: working');
assert.deepEqual(Object.keys(read.result).sort(),['content','encoding','next_offset','offset','path','size_bytes']);
assert.equal(JSON.stringify(read).includes('BOBS PRIVATE BOARD'),false);

folderUniverseOverride='u-bob';
for(const action of ['list_files','read_file']){
 const wrong=await ask(action,{path:'notes/board.md'});
 assert.equal(wrong.ok,false,action);
 assert(/another command center/.test(wrong.error),wrong.error);
}
folderUniverseOverride='';
calls=[];
const noPath=await ask('read_file',{path:'  '});
assert.equal(noPath.ok,false); assert(/path is required/.test(noPath.error));
const longPath=await ask('read_file',{path:'x'.repeat(u.MAX_PATH+1)});
assert.equal(longPath.ok,false);
assert.equal(calls.length,0,'refused arguments reach no tool');

// ---- the wake: the viewer's own home, a name, and nothing it can probe ----
calls=[];
const woke=await ask('emit',{name:'visit',data:{who:'baker'},graph_id:'u-bob',branch_def_id:'br-anything'});
assert.equal(woke.ok,true,woke.error);
const emitCall=calls.filter(c=>c.tool==='run_graph').pop();
assert.equal(emitCall.args.graph_id,HOME);
assert.deepEqual(Object.keys(emitCall.args).sort(),['graph_id','inputs_json','operation']);
assert.deepEqual(emits.pop(),{name:'visit',data:{who:'baker'}},'only name and data cross');
assert.deepEqual(woke.result,{emitted:true,woke:1});
assert.equal(JSON.stringify(woke).includes('leak'),false,'which subscription woke never crosses');

for(const bad of [{name:''},{name:'visit',data:[1,2]},{name:'visit',data:'text'}]){
 const before=calls.length;
 const refused=await ask('emit',bad);
 assert.equal(refused.ok,false,JSON.stringify(bad));
 assert.equal(calls.length,before,'a refused event reaches no tool');
}
emitReply={error:'usage_limit',detail:'This universe reached its usage limit'};
const limited=await ask('emit',{name:'visit'});
assert.equal(limited.ok,false); assert(/usage limit/.test(limited.error),limited.error);
emitReply=null;

// One event in flight per frame: a loop in a bundle cannot fan out.
let release;const gate=new Promise(r=>release=r);
const slow=MCP.callTool;
MCP.callTool=async function(tool,args,opts){ if(tool==='run_graph'){await gate;} return slow.call(this,tool,args,opts); };
emit({source:win,data:{ta_ui:1,type:'call',id:'e1',action:'emit',params:{name:'visit'}}});
for(let i=0;i<4;i++)await new Promise(r=>setImmediate(r));
const second=await ask('emit',{name:'visit'});
assert.equal(second.ok,false); assert(/already in flight/.test(second.error),second.error);
release(); await settle();
MCP.callTool=slow;

// ---- a home change ends these too -----------------------------------------
me={principal_id:PRINCIPAL,universe_id:'u-bob',setup:'connected'};
const moved=await ask('emit',{name:'visit'});
assert.equal(moved.ok,false); assert(/identity or home changed/.test(moved.error),moved.error);
assert.equal(u.frame,null);
console.log('custom-ui folder and wake checks passed');
})().catch(err=>{console.error(err);process.exit(1);});
'''


def test_a_bundle_reads_the_viewers_folder_and_wakes_only_their_agent(tmp_path):
    out = _run(tmp_path, "custom_ui_folder_wake.js", CHECKS, extra=FOLDER_DOUBLE)
    assert "custom-ui folder and wake checks passed" in out
