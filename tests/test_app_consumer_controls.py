"""The conversation design through the custom-UI bridge, on the shipped controller.

A custom UI can read which conversation design answers the person and ASK to
change it; the change happens only when the person approves in the page's own
prompt, and it is a revision-guarded write read back before it is reported.
Recovery (restore default, restore previous) stays in the trusted Switch UI
dialog, outside any custom UI. The harness is the bridge test's: the real
AppUI against a server double that enforces the binding's compare-and-set.
"""
# ruff: noqa: E501 -- embedded JavaScript fixture mirrors controller expressions
from tests.test_custom_ui_bridge import _run

EXTRA = r'''
let prompts=[],approve=true;
const confirm=message=>{prompts.push(message);return approve;};
// The binding store as it really answers: canonical JSON with sorted keys
// (commons_catalog._canonical_json), so {version,state,...} reads back
// {component_key,...,version}. A text compare called every save a failure.
const sortKeys=v=>Array.isArray(v)?v.map(sortKeys):(v&&typeof v==='object')?
 Object.fromEntries(Object.keys(v).sort().map(k=>[k,sortKeys(v[k])])):v;
// Rows an older client could have left beside `binding`: a second
// installation of the same role, which makes the conversation ambiguous.
let extra=[];
const harnessCall=MCP.callTool;
MCP.callTool=async function(tool,args){
 const other=extra.find(r=>args&&r.agent_binding_id===args.agent_binding_id);
 if(other&&tool==='write_graph'){
  calls.push({tool,args:JSON.parse(JSON.stringify(args))});
  if(args.expected_revision!==other.revision) return {error:'agent_conflict'};
  Object.assign(other,{configuration:sortKeys(JSON.parse(args.payload_json)),revision:other.revision+1,updated_by:PRINCIPAL});
  return {status:'configured',binding:JSON.parse(JSON.stringify(other))};
 }
 if(other&&tool==='read_graph') return {binding:JSON.parse(JSON.stringify(other))};
 const result=await harnessCall.call(this,tool,args);
 if(tool==='read_graph'&&args.target==='agent_bindings') result.bindings=result.bindings.concat(JSON.parse(JSON.stringify(extra)));
 if(tool==='write_graph'&&args.target==='agent_binding'&&result&&result.binding){
  binding={...binding,configuration:sortKeys(binding.configuration)}; result.binding=binding;
 }
 return result;
};
'''

CHECKS = r'''
(async()=>{
const u=AppUI;
const turn={kind:'tinyassets.turn-graph.v1',version:1,branch_version_id:'bv-7',
 content_hash:'a'.repeat(64),input_map:{message:'question'},reply_key:'answer'};
definitions['d2']={agent_definition_id:'d2',name:'Shared design',content_fingerprint:'b'.repeat(64),
 components:{turn,ui:{kind:'tinyassets.app-ui.v1'}}};
definitions['d3']={agent_definition_id:'d3',name:'Unsupported',content_fingerprint:'c'.repeat(64),
 components:{turn:{...turn,code:'unsafe'}}};
binding=installed();
appUi=stored([bundleOf()],{version:1,state:'active',ui_id:'office'});
u.enable(HOME,PRINCIPAL);
await settle(40);
assert(u.active,'the remembered UI must be mounted: '+$('ui-status').textContent);
const win=u.frame.contentWindow;
emit({source:win,data:{ta_ui:1,type:'ready'}});
let n=0;
const ask=async(action,params)=>{
 const id='c'+(n++);
 emit({source:win,data:{ta_ui:1,type:'call',id,action,params:params||{}}});
 for(let i=0;i<60;i++){
  const hit=win.posts.find(p=>p.type==='result'&&p.id===id);
  if(hit)return hit;
  await new Promise(r=>setImmediate(r));
 }
 throw Error('the bridge never answered '+action);
};
const writes=()=>calls.filter(c=>c.tool==='write_graph'&&c.args.target==='agent_binding');
// Every save below reads back from the sorted-key store: the selection is
// written {version,state,component_key,...} and returned in key order.

// ---- the trusted panel shows the default before anything is chosen --------
assert.equal($('ui-conversation').children[0].textContent,'Conversation design: default');
assert.deepEqual((await ask('conversation_design')).result,{state:'default'});

// ---- the ask is not the approval: a declined prompt writes nothing --------
calls=[];approve=false;
let r=await ask('set_conversation_design',{agent_definition_id:'d2',component_key:'turn'});
assert.equal(r.ok,false);assert(/did not approve/.test(r.error),r.error);
assert.equal(prompts.length,1);
assert(prompts[0].includes('Office building')&&prompts[0].includes('Shared design')&&prompts[0].includes('bv-7'),prompts[0]);
assert.equal(writes().length,0,'a declined change must not write');

// ---- an unsupported component is refused before the person is asked ------
prompts=[];approve=true;
r=await ask('set_conversation_design',{agent_definition_id:'d3',component_key:'turn'});
assert.equal(r.ok,false);assert(/no supported conversation component/.test(r.error),r.error);
r=await ask('set_conversation_design',{agent_definition_id:'d2',component_key:'ui'});
assert.equal(r.ok,false);
assert.equal(prompts.length,0);assert.equal(writes().length,0);

// ---- approved: one CAS write, private configuration kept, read back -------
r=await ask('set_conversation_design',{agent_definition_id:'d2',component_key:'turn'});
assert.equal(r.ok,true,r.error);
assert.deepEqual(r.result,{state:'active',agent_definition_id:'d2',component_key:'turn'});
assert.equal(writes().length,1);
assert.equal(writes()[0].args.operation,'update');
assert.equal(writes()[0].args.expected_revision,1);
assert.equal(writes()[0].args.graph_id,HOME);
assert.deepEqual(binding.configuration.private,{keep:1});
assert.deepEqual(binding.configuration.turn_consumer,
 {version:1,state:'active',component_key:'turn',definition_fingerprint:'b'.repeat(64)});
assert.equal(JSON.stringify(r).includes('keep'),false,'the configuration never crosses into a bundle');
assert.deepEqual((await ask('conversation_design')).result,r.result);
const agents=(await ask('list_agents')).result.agents;
assert.equal(agents.length,1);assert.equal(agents[0].selected,true);
assert(/d2 \/ turn/.test($('ui-conversation').children[0].textContent));

// ---- a bundle cannot name a universe: the write is pinned to the viewer ---
calls=[];
r=await ask('set_conversation_design',{state:'default',graph_id:'u-bob',universe_id:'u-bob'});
assert.equal(r.ok,true,r.error);
assert.equal(writes()[0].args.graph_id,HOME);
assert.deepEqual(binding.configuration.turn_consumer,{version:1,state:'disabled'});

// ---- trusted recovery: previous, then default, no prompt, no bundle -------
prompts=[];calls=[];
await u.restorePreviousConversation();
assert.equal(prompts.length,0,'a click in the page\'s own dialog is the approval');
assert.equal(writes().length,1);
assert.equal(binding.configuration.turn_consumer.state,'active');
await u.restoreDefaultConversation();
assert.equal(writes().length,2);
assert.deepEqual(binding.configuration.turn_consumer,{version:1,state:'disabled'});
await u.restoreDefaultConversation();
assert.equal(writes().length,2,'restoring an already-default conversation writes nothing');

// ---- a change made elsewhere is not overwritten from a stale revision ----
binding={...binding,updated_by:'collaborator'};
calls=[];
r=await ask('set_conversation_design',{agent_definition_id:'d2',component_key:'turn'});
assert.equal(r.ok,false);assert(/not owner-controlled/.test(r.error),r.error);
assert.equal(writes().length,0);

// ---- a session that moved during the prompt does not write ---------------
binding={...binding,updated_by:PRINCIPAL};
calls=[];
me={...me,universe_id:'u-other'};
r=await ask('set_conversation_design',{agent_definition_id:'d2',component_key:'turn'});
assert.equal(r.ok,false);
assert.equal(writes().length,0,'a changed home must not receive the write');

// ---- a UI swapped in mid-request is never named as the asker -------------
me={...me,universe_id:HOME};
u.enable(HOME,PRINCIPAL); await settle(40);
assert(u.active,'the remembered UI is mounted again');
const office=u.frame.contentWindow;
emit({source:office,data:{ta_ui:1,type:'ready'}});
calls=[];prompts=[];
let release;const gate=new Promise(res=>{release=res;});
const ownerRead=Owner.read;
Owner.read=async a=>{ if(a.target==='agent') await gate; return ownerRead(a); };
emit({source:office,data:{ta_ui:1,type:'call',id:'swap',action:'set_conversation_design',
 params:{agent_definition_id:'d2',component_key:'turn'}}});
await settle(20);
u.mount(u.parseBundle(bundleOf({ui_id:'other',name:'Other UI'})).bundle);
release(); await settle(60);
Owner.read=ownerRead;
assert.equal(prompts.length,0,'the prompt named a UI that did not ask: '+prompts.join(' | '));
assert.equal(writes().length,0,'a request from a UI that left the screen must not write');

// ---- more than one installation: refused for a UI, recoverable in Switch UI
const other=u.frame.contentWindow;
emit({source:other,data:{ta_ui:1,type:'ready'}});
binding={...binding,configuration:{...binding.configuration,
 turn_consumer:{version:1,state:'active',component_key:'turn',definition_fingerprint:'b'.repeat(64)}}};
extra=[{...installed(),agent_binding_id:'b2',revision:3,configuration:{schema_version:1,name:'App experience',
 role:'app_experience',turn_consumer:{version:1,state:'active',component_key:'turn',definition_fingerprint:'b'.repeat(64)}}}];
await u.readConversationDesign();
assert(u.ambiguous,'two installations must read as ambiguous');
assert(/More than one/.test($('ui-conversation').children[1].textContent));
const restore=$('ui-conversation').children.find(c=>c.textContent==='Restore default conversation');
assert.equal(restore.disabled,false,'the Switch UI dialog must offer recovery from ambiguity');
calls=[];prompts=[];
const askOther=async(action,params)=>{
 const id='o'+(n++);
 emit({source:other,data:{ta_ui:1,type:'call',id,action,params:params||{}}});
 for(let i=0;i<60;i++){ const hit=other.posts.find(p=>p.type==='result'&&p.id===id); if(hit)return hit; await new Promise(r=>setImmediate(r)); }
 throw Error('no answer to '+action);
};
assert.deepEqual((await askOther('conversation_design')).result,{state:'ambiguous'});
r=await askOther('set_conversation_design',{agent_definition_id:'d2',component_key:'turn'});
assert.equal(r.ok,false);assert(/more than one/.test(r.error),r.error);
r=await askOther('set_conversation_design',{state:'default'});
assert.equal(r.ok,false,'a UI cannot run the recovery; it is the trusted dialog\'s');
assert.equal(writes().length,0);assert.equal(prompts.length,0);
await u.restoreDefaultConversation();
assert.equal(writes().length,2);
assert.deepEqual(binding.configuration.turn_consumer,{state:'disabled',version:1});
assert.equal(extra[0].configuration.role,'app_experience_retired');
assert.equal(extra[0].configuration.turn_consumer.state,'disabled');
assert.equal(u.ambiguous,false);
assert.deepEqual((await askOther('conversation_design')).result,{state:'default'});
console.log('consumer controls passed');
})().catch(err=>{console.error(err);process.exit(1);});
'''


def test_a_custom_ui_asks_and_the_person_approves_the_conversation_design(tmp_path):
    out = _run(tmp_path, "consumer_controls.js", CHECKS, extra=EXTRA)
    assert "consumer controls passed" in out
