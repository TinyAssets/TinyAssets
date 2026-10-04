"""A custom UI can show the viewer's agents WORKING: automations, runs, output.

Live acceptance run (2026-09-30): asked for a village of agents, a universe built
the screen but labelled it a preview in which "no agents run". The bridge could
only greet, list conversation agents, send a turn and read the conversation, so
no bundle could show an automation firing or what an agent wrote.

These run the shipped controller against the shared harness (a server double),
and assert what the bridge SENDS and RETURNS when a hostile bundle asks for
someone else's universe or for fields it should not see.
"""
# ruff: noqa: E501 -- embedded JavaScript fixture mirrors controller expressions
from __future__ import annotations

import re
from pathlib import Path

from tests.test_custom_ui_bridge import _run

LIVE_DOUBLE = r'''
// Live-state double. Each read answers only for the universe it is asked
// about, as the server does: a run id from another universe reads as not found.
const RUNS={
 'run-a':{universe:HOME,run_id:'run-a',branch_def_id:'br-scout',run_name:'scout',status:'completed',
  actor:'alice-principal-id',started_at:'2026-09-30T10:00:00',finished_at:'2026-09-30T10:01:00',last_node_id:'think',
  error:'',node_statuses:[{node_id:'think',status:'ran',detail:'x'}],
  output_catalog:{fields:[{name:'reply',type:'str',total_chars:12}]},mermaid:'graph',
  output:{reply:'I found two.'}},
 'run-bob':{universe:'u-bob',run_id:'run-bob',branch_def_id:'br-bob',run_name:'bob',status:'completed',
  actor:'bob',node_statuses:[],output_catalog:{fields:[{name:'secret',type:'str',total_chars:6}]},
  output:{secret:'BOBS SECRET OUTPUT'}},
};
let automationsUniverseOverride='';
let liveTurn=null, liveUniverseOverride='';
const baseCall=MCP.callTool.bind(MCP);
MCP.callTool=async function(tool,args,opts){
 if(tool==='get_status'&&liveTurn!==null){
  calls.push({tool,args:JSON.parse(JSON.stringify(args||{}))});
  return {universe_id:liveUniverseOverride||args.universe_id,active_turn:liveTurn,
    persona:{name:'Ada',purpose:'PRIVATE PERSONA'},recent_conversation:{turns:[{speaker:'founder',text:'SECRET'}]}};
 }
 if(tool==='read_graph'&&['automations','runs','run','run_output'].includes(args.target)){
  calls.push({tool,args:JSON.parse(JSON.stringify(args||{}))});
  allCalls.push({tool,args:JSON.parse(JSON.stringify(args||{}))});
  const scope=args.graph_id;
  if(args.target==='automations')
   return {universe_id:automationsUniverseOverride||scope,count:2,automations:[
    {automation_id:'a1',universe_id:scope,name:'Scout heartbeat',branch_def_id:'br-scout',
     trigger:{kind:'interval',interval_seconds:300,cron_expr:'',event_type:'',event_filter:{branch_def_id:'x'}},
     inputs:{api_note:'PRIVATE INPUT'},desired_state:'active',pause_reason:'',revision:3,
     last_run_id:'run-a',last_reason:'completed',last_finished_at:'2026-09-30T10:01:00',
     next_due_at:'2026-09-30T10:06:00',consecutive_failures:0,owner:{is_you:true}},
    {automation_id:'legacy-1',legacy:true,status:'retired_fleet_era',desired_state:'paused'}]};
  if(args.target==='runs')
   return {runs:Object.values(RUNS).filter(r=>r.universe===scope).map(r=>({run_id:r.run_id,
     branch_def_id:r.branch_def_id,run_name:r.run_name,status:r.status,actor:r.actor,
     started_at:r.started_at,finished_at:r.finished_at,last_node_id:r.last_node_id}))};
  const run=RUNS[args.run_id];
  if(!run||run.universe!==scope) return {error:"Run '"+args.run_id+"' not found."};
  if(args.target==='run'){const {universe,output,...rest}=run;return JSON.parse(JSON.stringify(rest));}
  const value=run.output[args.field_name];
  if(value===undefined) return {error:'no such field'};
  const chunk=value.slice(args.output_offset,args.output_offset+args.output_max_chars);
  return {field_name:args.field_name,encoding:'text',chunk,offset:args.output_offset,
   length:chunk.length,total_chars:value.length,truncated:false,next_offset:null,value};
 }
 return baseCall(tool,args,opts);
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
const hostile={graph_id:'u-bob',universe_id:'u-bob',limit:9999};

// ---- automations: pinned, checked, picked --------------------------------
calls=[];
const autos=await ask('list_automations',hostile);
assert.equal(autos.ok,true,autos.error);
const autoCalls=calls.filter(c=>c.args.target==='automations');
assert.equal(autoCalls.length,1);
assert.equal(autoCalls[0].args.graph_id,HOME,'graph_id came from the viewer, not the bundle');
assert(!('universe_id' in autoCalls[0].args));
assert.equal(autos.result.automations.length,1,'the legacy row names no universe and is skipped');
const a=autos.result.automations[0];
assert.deepEqual(Object.keys(a).sort(),['automation_id','branch_id','consecutive_failures','last_finished_at',
 'last_result','last_run_id','name','next_due_at','paused_because','state','trigger']);
assert.deepEqual(a.trigger,{kind:'interval',interval_seconds:300,cron:'',event:''});
assert.equal(a.state,'active'); assert.equal(a.last_run_id,'run-a');
assert.equal(JSON.stringify(autos).includes('PRIVATE INPUT'),false,'owner inputs never cross');
assert.equal(JSON.stringify(autos).includes('is_you'),false);

// An answer about another universe is refused, not rendered.
automationsUniverseOverride='u-bob';
const wrongAutos=await ask('list_automations',{});
assert.equal(wrongAutos.ok,false);
assert(/another command center/.test(wrongAutos.error),wrongAutos.error);
automationsUniverseOverride='';

// ---- runs: pinned, bounded, no principal id -------------------------------
calls=[];
const listed=await ask('list_runs',Object.assign({status:'completed'},hostile));
assert.equal(listed.ok,true,listed.error);
const runCalls=calls.filter(c=>c.args.target==='runs');
assert.equal(runCalls[0].args.graph_id,HOME);
// The bound plus ONE look-ahead row, which is how the page says older runs exist.
assert.equal(runCalls[0].args.limit,u.MAX_LIST_RUNS+1,'a bundle cannot raise the bound');
assert(listed.result.runs.length<=u.MAX_LIST_RUNS,'never more than the bound crosses');
assert.equal(typeof listed.result.has_more,'boolean','a cut page says so');
assert.equal(runCalls[0].args.run_status,'completed');
assert.deepEqual(listed.result.runs.map(r=>r.run_id),['run-a']);
assert.deepEqual(Object.keys(listed.result.runs[0]).sort(),
 ['branch_id','finished_at','last_node_id','name','run_id','started_at','status']);
assert.equal(JSON.stringify(listed).includes('alice-principal-id'),false,'the actor never crosses');

// ---- one run: its nodes and output field names, picked --------------------
const one=await ask('read_run',{run_id:'run-a',graph_id:'u-bob'});
assert.equal(one.ok,true,one.error);
assert.equal(calls.filter(c=>c.args.target==='run').pop().args.graph_id,HOME);
assert.deepEqual(one.result.nodes,[{node_id:'think',status:'ran'}]);
assert.deepEqual(one.result.output_fields,['reply']);
assert.equal(JSON.stringify(one).includes('alice-principal-id'),false);
assert.equal(JSON.stringify(one).includes('mermaid'),false);

// ---- what an agent wrote -------------------------------------------------
const said=await ask('read_run_output',{run_id:'run-a',field:'reply'});
assert.equal(said.ok,true,said.error);
const outCall=calls.filter(c=>c.args.target==='run_output').pop();
assert.equal(outCall.args.graph_id,HOME);
assert.equal(outCall.args.output_max_chars,u.MAX_OUTPUT_CHUNK);
assert.deepEqual(said.result,{field:'reply',encoding:'text',text:'I found two.',offset:0,total_chars:12,next_offset:null});

// ---- another universe's run id reaches nothing ---------------------------
for(const action of ['read_run','read_run_output']){
 const foreign=await ask(action,{run_id:'run-bob',field:'secret',graph_id:'u-bob',universe_id:'u-bob'});
 assert.equal(foreign.ok,false,action);
 assert.equal(JSON.stringify(foreign).includes('BOBS SECRET OUTPUT'),false,action);
}
// A missing id is refused before any call is made.
calls=[];
for(const action of ['read_run','read_run_output']){
 const blank=await ask(action,{run_id:'  '});
 assert.equal(blank.ok,false); assert(/run_id is required/.test(blank.error),blank.error);
}
const noField=await ask('read_run_output',{run_id:'run-a'});
assert.equal(noField.ok,false); assert(/field is required/.test(noField.error),noField.error);
assert.equal(calls.length,0,'refused arguments reach no tool');

// ---- live state: which agent is working, on what, keyed by agent ---------
binding=installed();
liveTurn={turn_id:'t1',state:'tool',started_at:'2026-10-01T10:00:00+00:00',age_s:4,stale:false,
 tools:[{tool:'bash',summary:'ran a command (git status)',state:'running',age_s:1.2,
   command:'cat ~/.ssh/id_rsa',arguments:{x:1},result:'SECRET RESULT'},
  {tool:'read',summary:'read notes/plan.md',state:'done',age_s:3,took_s:0.1}]};
calls=[];
const live=await ask('read_live',hostile);
const liveCall=calls.find(c=>c.tool==='get_status');
assert.equal(liveCall.args.universe_id,HOME,'pinned to the granted home');
const agentsLive=live.result.agents;
assert(agentsLive.length>=1);
const worker=agentsLive.find(a=>a.state==='working');
assert(worker,'the selected agent shows as working');
assert.equal(worker.since,'2026-10-01T10:00:00+00:00');
assert.deepEqual(worker.steps.map(s=>Object.keys(s).sort()),
 [['age_s','state','summary','tool'],['age_s','state','summary','tool']]);
assert(!JSON.stringify(live.result).includes('SECRET')&&!JSON.stringify(live.result).includes('id_rsa')
 &&!JSON.stringify(live.result).includes('PRIVATE PERSONA'),'only picked fields cross');
liveTurn={turn_id:'t1',state:'tool',started_at:'x',stale:true,tools:[]};
const staleLive=await ask('read_live',{});
assert(staleLive.result.agents.every(a=>a.state==='idle'),'a stale turn is not working');
liveUniverseOverride='u-bob';
const foreignLive=await ask('read_live',{});
assert(foreignLive.error,'another universe\'s state is refused');
liveUniverseOverride='';

// ---- the roster carries exactly one "main", selected or not --------------
// listAgents() already seeds the command center's own agent as "main", so
// readLive must never add a second one. It used to prepend its own, named from
// whoami(), whenever nothing was selected -- which drew that agent twice under
// two different names on a screen that animates one villager per agent.
binding={agent_binding_id:'b9',universe_id:HOME,agent_definition_id:'d1',
 status:'configured',revision:1,created_by:PRINCIPAL,updated_by:PRINCIPAL,
 configuration:{schema_version:1,name:'Weaver',role:'writer'}};
liveTurn={turn_id:'t2',state:'tool',started_at:'2026-10-01T11:00:00+00:00',age_s:1,stale:false,
 tools:[{tool:'read',summary:'read notes/plan.md',state:'done',age_s:1}]};
for(const [who,label] of [['main','the command center\'s own agent is selected'],
                          ['b9','an installed agent is selected'],
                          ['ghost-b0','nothing is selected']]){
 addressed=who;
 const roster=await ask('read_live',{});
 assert.equal(roster.ok,true,label);
 const ids=roster.result.agents.map(a=>a.agent_id);
 assert.equal(ids.filter(id=>id==='main').length,1,'exactly one "main": '+label);
 assert.equal(new Set(ids).size,ids.length,'no agent listed twice: '+label);
 assert.deepEqual(ids.slice().sort(),['b9','main'],'the roster is listAgents(): '+label);
 const own=roster.result.agents.find(a=>a.agent_id==='main');
 assert.equal(own.name,'Ada','the own agent keeps one label: '+label);
 const working=roster.result.agents.filter(a=>a.state==='working');
 assert(working.length<=1,'at most one agent holds the turn: '+label);
 assert.equal(working.map(a=>a.agent_id).join(','),who==='ghost-b0'?'':who,
  'the selected agent holds the turn, and nobody holds it when none is: '+label);
}
addressed='main'; binding=installed(); liveTurn=null;

// ---- a home change ends the grant for these reads too ---------------------
me={principal_id:PRINCIPAL,universe_id:'u-bob',setup:'connected'};
const moved=await ask('list_runs',{});
assert.equal(moved.ok,false);
assert(/identity or home changed/.test(moved.error),moved.error);
assert.equal(u.frame,null);
console.log('custom-ui live state checks passed');
})().catch(err=>{console.error(err);process.exit(1);});
'''


def test_a_bundle_reads_the_viewers_own_automations_runs_and_output(tmp_path):
    out = _run(tmp_path, "custom_ui_live_state.js", CHECKS, extra=LIVE_DOUBLE)
    assert "custom-ui live state checks passed" in out


def test_main_agent_live_name_is_its_own_name_not_the_command_center(tmp_path):
    out = _run(tmp_path, "main_agent_name.js", r'''
(async()=>{
 AppUI.home=HOME; AppUI.principal=PRINCIPAL;
 const original=Owner.status;
 let name='Ada';
 Owner.status=async args=>({...await original(args),persona:{name,secret:'private self-model'}});
 for(const [value,expected] of [['Ada','Ada'],['  Ada  ','Ada'],['','Your agent']]){
  name=value;
  const roster=await AppUI.listAgents();
  const live=await AppUI.readLive();
  assert.equal(roster.agents.find(a=>a.agent_id==='main').name,expected);
  assert.equal(live.agents.find(a=>a.agent_id==='main').name,expected);
  assert(!JSON.stringify(live).includes('private self-model'));
 }
 console.log('main agent name checks passed');
})().catch(e=>{console.error(e);process.exit(1);});
''')
    assert "main agent name checks passed" in out


def test_the_frame_client_exposes_exactly_the_allowlisted_actions():
    """The bundle-side sugar and the parent allowlist cannot drift apart."""
    controller = Path("tinyassets/onboarding/app_ui.js").read_text(encoding="utf-8")
    frame = Path("tinyassets/onboarding/ui_frame.py").read_text(encoding="utf-8")
    allowlist = re.search(r"ACTIONS:Object\.freeze\(\{(.*?)\}\)", controller, re.S)
    assert allowlist
    actions = set(re.findall(r"([a-z_]+):\"[A-Za-z]+\"", allowlist.group(1)))
    exposed = set(re.findall(r'return call\("([a-z_]+)"', frame))
    assert exposed == actions
