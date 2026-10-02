"""Execute the shipped custom-UI controller and bridge, not a reimplementation.

The bundle side is untrusted by construction, so the interesting assertions are
what the bridge *sends* and *returns* when a hostile bundle asks for more than it
has: the recorded tool arguments, the refusal text, and the fields that reach the
frame. Asserting only that "nothing happened" would pass against a bridge that
silently did the wrong thing.

Both real controllers run together against a server double that enforces the
app_ui store's compare-and-set, so the write path under test is the shipped one.
"""
# ruff: noqa: E501 -- embedded JavaScript fixture mirrors controller expressions
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = r'''
const assert=require('node:assert/strict');
const registry={};
class FrameWindow{ constructor(){ this.posts=[]; } postMessage(data){ this.posts.push(data); } }
class Element {
 constructor(id='',tag=''){this.id=id;this.tag=tag;this.children=[];this.parentNode=null;this.value='';
  this.open=false;this.hidden=false;this.textContent='';this.disabled=false;this.attrs={};
  this.classes=new Set();
  this.classList={add:c=>this.classes.add(c),remove:c=>this.classes.delete(c),contains:c=>this.classes.has(c)};
  if(tag==='iframe')this.contentWindow=new FrameWindow();
  if(id)registry[id]=this;}
 appendChild(n){if(n.parentNode)n.parentNode.removeChild(n);this.children.push(n);n.parentNode=this;return n;}
 insertBefore(n,ref){if(n.parentNode)n.parentNode.removeChild(n);const i=this.children.indexOf(ref);assert(i>=0);this.children.splice(i,0,n);n.parentNode=this;}
 removeChild(n){const i=this.children.indexOf(n);assert(i>=0);this.children.splice(i,1);n.parentNode=null;}
 replaceChildren(...nodes){for(const n of [...this.children])this.removeChild(n);for(const n of nodes)this.appendChild(n);}
 setAttribute(name,value){this.attrs[name]=String(value);} addEventListener(){}
 close(){this.open=false;} showModal(){this.open=true;}
}
const $=id=>registry[id]||(registry[id]=new Element(id));
const document={createElement:tag=>new Element('',tag),createComment:()=>new Element()};
const host=$('view-chat'),body=new Element();host.appendChild(body);
for(const id of ['thread','request-rail'])body.appendChild($(id));
for(const id of ['attachments','model-bar','composer','status-line'])host.appendChild($(id));
$('universe-name').textContent='Alice universe';

// --- the window the bridge listens on ---------------------------------------
let listeners=[];
const window={addEventListener:(name,fn)=>{if(name==='message')listeners.push(fn);},
 removeEventListener:(name,fn)=>{if(name==='message')listeners=listeners.filter(f=>f!==fn);}};
const emit=event=>{for(const fn of [...listeners])fn(event);};

// --- server double: one binding (agents) + the viewer's app_ui row ----------
// The app_ui double enforces what the store does: compare-and-set on revision,
// 0 meaning "no row yet", and only the named fields written.
const HOME='u-alice',PRINCIPAL='alice';
let me={principal_id:PRINCIPAL,universe_id:HOME,setup:'connected'};
let binding=null, definitions={}, calls=[], allCalls=[], sends=[], conversation=[];
let appUi=null, raceNext=false;
const clone=v=>JSON.parse(JSON.stringify(v));
// What the store hands back: canonical JSON (custom_agents._canonical_json,
// sort_keys=True) parsed again, so every object comes back with SORTED keys.
// A double that echoed the client's own key order hid a live bug: the app
// compared its save by JSON.stringify and called its own saved choice a
// mismatch (founder, 2026-10-01: "UI choice save did not match").
const canonical=v=>Array.isArray(v)?v.map(canonical):(v&&typeof v==='object')?
 Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
const stored=(library,selection)=>({universe_id:HOME,ui_library:library||[],
 ui_selection:selection||null,revision:1,updated_at:1});
const settle=async(n)=>{for(let i=0;i<(n||10);i++)await new Promise(r=>setImmediate(r));};
let otherConversation=[{speaker:'universe',text:'BOBS PRIVATE TURN',ts:1,truncated:false}];
let statusUniverseOverride='';
const fetchMe=async()=>me;
const sessionExpired=()=>{throw Error('expired');};
const sendTurn=async(message,display,opts)=>{sends.push({message,display,opts});};
// The owner door (reads). This harness has ONE fake server, `MCP` below, so
// the owner door's reads are answered by it: a read the page makes is
// recorded and stubbed exactly where the scenario already records it.
const Owner={
  read(a){return MCP.callTool("read_graph",a,{idempotent:true});},
  status(a){return MCP.callTool("get_status",a||{},{idempotent:true});},
  getStatus(...x){return MCP.getStatus(...x);},
  getConversation(...x){return MCP.getConversation(...x);},
  readConversationChunk(...x){return MCP.readConversationChunk(...x);},
  getModelOptions(...x){return MCP.getModelOptions(...x);},
  listRequests(...x){return MCP.listRequests(...x);}};
const MCP={
 async callTool(tool,args){
  calls.push({tool,args:JSON.parse(JSON.stringify(args||{}))});
  allCalls.push({tool,args:JSON.parse(JSON.stringify(args||{}))});
  if(tool==='read_graph'&&args.target==='app_ui'){
   assert.equal(args.graph_id,HOME);
   return {app_ui:appUi?clone(appUi):{universe_id:args.graph_id,ui_library:[],ui_selection:null,revision:0,updated_at:null}};
  }
  if(tool==='write_graph'&&args.target==='app_ui'){
   assert.equal(args.operation,'save');
   // Someone else saved between this client's read and its write.
   if(raceNext){ raceNext=false; appUi=appUi?{...appUi,revision:appUi.revision+1}:stored([],null); }
   const current=appUi?appUi.revision:0;
   if(args.expected_revision!==current)
    return {error:'app_ui_conflict',detail:'app UI changed: expected revision '+args.expected_revision+', current is '+current};
   const changes=JSON.parse(args.payload_json);
   for(const key of Object.keys(changes)) assert(['ui_library','ui_selection'].includes(key),key);
   appUi={...(appUi||stored([],null)),...canonical(changes),revision:current+1,updated_at:2};
   return {status:'saved',app_ui:clone(appUi)};
  }
  if(tool==='read_graph'&&args.target==='agent_bindings')return {bindings:binding?[binding]:[]};
  if(tool==='read_graph'&&args.target==='agent_binding')return {binding};
  if(tool==='read_graph'&&args.target==='agent')return {agent:definitions[args.agent_definition_id]||{agent_definition_id:args.agent_definition_id,components:{}}};
  if(tool==='get_status'){
   const scope=statusUniverseOverride||args.universe_id||me.universe_id;
   return {universe_id:scope,
     recent_conversation:{turns:scope===HOME?conversation:otherConversation}};
  }
  if(tool==='write_graph'&&args.target==='agent_binding'){
   const config=JSON.parse(args.payload_json);
   if(args.operation==='update'){
    assert.equal(args.agent_binding_id,binding.agent_binding_id);
    assert.equal(args.expected_revision,binding.revision);
    binding={...binding,configuration:config,revision:binding.revision+1,
      agent_definition_id:args.agent_definition_id,updated_by:PRINCIPAL};
   }else{
    binding={agent_binding_id:'b1',universe_id:args.graph_id,agent_definition_id:args.agent_definition_id,
      configuration:config,revision:1,status:'configured',created_by:PRINCIPAL,updated_by:PRINCIPAL};
   }
   return {status:'configured',binding};
  }
  return {};
 },
 getStatus(){return this.callTool('get_status',{},{idempotent:true});},
 getConversation(){return this.callTool('get_status',{include_conversation:true},{idempotent:true});},
};
const bundleOf=over=>Object.assign({kind:'tinyassets.app-ui.v1',version:1,ui_id:'office',
 name:'Office building',markup:'<div id="lobby">Lobby</div>',style:'#lobby{color:red}',
 script:'tinyassets.whoami()'},over||{});
// An agent binding for list_agents to find. It carries no UI: the UI lives in
// the app_ui row, and nothing in these checks may read or write it here.
const installed=()=>({agent_binding_id:'b1',universe_id:HOME,agent_definition_id:'d1',
 status:'configured',revision:1,created_by:PRINCIPAL,updated_by:PRINCIPAL,
 configuration:{schema_version:1,name:'App experience',role:'app_experience',private:{keep:1}}});
'''

CHECKS = r'''
(async()=>{
const u=AppUI;

// ---- the reader refuses anything it cannot render, by reason ---------------
assert(u.parseBundle(bundleOf()).ok);
for(const [over,needle] of [
 [{extra:'x'},/does not render: extra/],
 [{kind:'other'},/not a tinyassets\.app-ui\.v1/],
 [{version:2},/version 2 is not supported/],
 [{ui_id:'Office'},/ui_id must be lowercase/],
 [{ui_id:''},/ui_id must be lowercase/],
 [{name:'  '},/name must be a non-empty string/],
 [{markup:{}},/markup must be a string/],
 [{script:null},/script must be a string/],
 [{markup:'x'.repeat(u.MAX_TEXT_BYTES+1)},/bytes of text; the limit is/],
 [{assets:[]},/assets must be an object/],
 [{assets:{'../x.png':{sha256:'a'.repeat(64),size:1,media_type:'image/png'}}},/is not a bundle path/],
 [{assets:{'x.png':{sha256:'nothex',size:1,media_type:'image/png'}}},/is not a stored blob/],
 [{libraries:['jquery']},/library jquery is not one this app provides/],
 [{libraries:['three','three']},/listed twice/],
 [{script_type:'wasm'},/script_type must be classic or module/],
]){ const r=u.parseBundle(bundleOf(over)); assert(!r.ok,JSON.stringify(over)); assert(needle.test(r.reason),r.reason); }
assert(!u.parseBundle({...bundleOf(),ui_id:undefined}).ok);
// Past the old 49,152-byte bound is fine now: a game's script is 200 KB.
assert(u.parseBundle(bundleOf({script:'j'.repeat(200000)})).ok);
// The optional fields pass through as stored, so an install that rebuilds the
// library from parsed entries never strips another UI's assets.
const rich=bundleOf({libraries:['three'],script_type:'module',
 assets:{'img/a.png':{sha256:'a'.repeat(64),size:3,media_type:'image/png'}}});
const richParsed=u.parseBundle(rich);
assert(richParsed.ok,richParsed.reason);
assert.deepEqual(richParsed.bundle.assets,rich.assets);
assert.deepEqual(richParsed.bundle.libraries,['three']);
assert.equal(richParsed.bundle.script_type,'module');
assert(!u.publishPayload(rich,'').ok,'a UI with its own files is not published without them');

assert.deepEqual(u.readLibrary(null).entries,[]);
assert(!u.readLibrary({ui_library:{}}).ok);
assert(!u.readLibrary({ui_library:[bundleOf(),bundleOf()]}).ok);            // duplicate ui_id
// No count cap: a long library reads, every entry kept in order.
const longLibrary=u.readLibrary({ui_library:Array.from({length:40},(_,i)=>bundleOf({ui_id:'ui-'+i}))});
assert(longLibrary.ok&&longLibrary.entries.length===40,longLibrary.reason);
assert(!u.readSelection({ui_selection:{version:1,state:'active'}}).ok);      // no ui_id
assert(!u.readSelection({ui_selection:{version:1,state:'active',ui_id:'office',extra:1}}).ok);
assert(!u.readSelection({ui_selection:{version:1,state:'default',ui_id:'office'}}).ok);
assert(!u.readSelection({ui_selection:{version:2,state:'default'}}).ok);
assert.deepEqual(u.readSelection({ui_selection:{version:1,state:'active',ui_id:'office'}}).selection,
 {version:1,state:'active',ui_id:'office'});

// ---- its own row is read once, and the saved choice is applied ------------
binding=installed();
appUi=stored([bundleOf()],{version:1,state:'active',ui_id:'office'});
definitions['d1']={agent_definition_id:'d1',content_fingerprint:'f'.repeat(64),components:{}};
u.enable(HOME,PRINCIPAL);
await settle();
assert(u.active&&u.active.ui_id==='office','the remembered UI must be applied: '+$('ui-status').textContent);
assert.equal(calls.filter(c=>c.tool==='read_graph'&&c.args.target==='app_ui').length,1);
assert.equal(u.revision,1);

// ---- the frame is created with the isolation the boundary depends on ------
const frame=u.frame;
assert.equal(frame.tag,'iframe');
assert.equal(frame.attrs.sandbox,'allow-scripts allow-forms');   // no allow-same-origin, ever
assert.equal(frame.attrs.src,'/app/ui-frame');
assert.equal(frame.attrs.referrerpolicy,'no-referrer');
assert.equal($('ui-frame-host').hidden,false);
assert($('view-chat').classes.has('ui-custom-active'));

// ---- the bundle is handed over only after the frame says ready ------------
const win=frame.contentWindow;
assert.equal(win.posts.length,0,'nothing is posted before the frame reports ready');
emit({source:win,data:{ta_ui:1,type:'ready'}});
assert.equal(win.posts.length,1);
assert.deepEqual(Object.keys(win.posts[0].bundle).sort(),['markup','script','style']);
assert.equal(win.posts[0].bundle.markup,'<div id="lobby">Lobby</div>');
emit({source:win,data:{ta_ui:1,type:'ready'}});
assert.equal(win.posts.length,1,'a replayed ready does not re-deliver the bundle');

// ---- a message from anything but this frame is not heard ------------------
const stranger=new FrameWindow();
emit({source:stranger,data:{ta_ui:1,type:'call',id:'x',action:'whoami',params:{}}});
await new Promise(r=>setImmediate(r));
assert.equal(stranger.posts.length,0);
assert.equal(win.posts.length,1,'a foreign window cannot make this frame receive a result');

const ask=async(action,params)=>{
 const before=win.posts.length;
 emit({source:win,data:{ta_ui:1,type:'call',id:'q'+before,action,params:params||{}}});
 for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));
 assert(win.posts.length>before,'the bridge always answers '+action);
 return win.posts[win.posts.length-1];
};

// ---- an unlisted action does not exist -----------------------------------
for(const action of ['write_graph','connectHTTP','whoami ','WHOAMI','constructor','__proto__','toString']){
 const before=calls.length;
 const reply=await ask(action,{});
 assert.equal(reply.ok,false,action);
 assert(reply.error.includes(action),reply.error);
 assert.equal(calls.length,before,action+' must reach no tool');
}

// ---- the viewer's identity, and nothing else ------------------------------
const who=(await ask('whoami',{universe_id:'u-bob'})).result;
assert.deepEqual(Object.keys(who).sort(),['command_center_id','command_center_name','protocol']);
assert.equal(who.command_center_id,HOME);
assert.equal(who.command_center_name,'Alice universe');

// ---- a bundle cannot name a universe: the argument is pinned -------------
calls=[];
const agents=(await ask('list_agents',{graph_id:'u-bob',universe_id:'u-bob',limit:9999})).result;
const listed=calls.filter(c=>c.tool==='read_graph'&&c.args.target==='agent_bindings');
assert.equal(listed.length,1);
assert.equal(listed[0].args.graph_id,HOME,'graph_id came from the viewer, not the bundle');
assert(!('universe_id' in listed[0].args));
assert.deepEqual(Object.keys(agents.agents[0]).sort(),['agent_id','name','selected']);
assert.equal(agents.agents[0].name,'App experience');

// ---- private operational data does not cross into a bundle ---------------
binding={...binding,configuration:{...binding.configuration,provider_secret_note:'never'}};
const again=(await ask('list_agents',{})).result;
assert.equal(JSON.stringify(again).includes('never'),false);

// ---- reading the conversation returns picked fields only -----------------
conversation=[{speaker:'founder',text:'hello',ts:100,truncated:false,
 access_token:'leak',internal:{credential:'leak'}}];
const read=(await ask('read_conversation',{limit:1})).result;
assert.deepEqual(read.turns,[{speaker:'founder',text:'hello',at:100,truncated:false}]);
assert.equal(JSON.stringify(read).includes('leak'),false);

// ---- send goes through the app's own turn path ---------------------------
sends=[];
const blank=await ask('send_message',{text:'   '});
assert.equal(blank.ok,false); assert.equal(sends.length,0);
const long=await ask('send_message',{text:'x'.repeat(u.MAX_MESSAGE+1)});
assert.equal(long.ok,false); assert.equal(sends.length,0);
const sent=await ask('send_message',{text:'open the lobby door'});
assert.equal(sent.ok,true); assert.deepEqual(sent.result,{sent:true});
assert.equal(sends.length,1);
assert.equal(sends[0].message,'open the lobby door');
assert.equal(sends[0].opts.inputMethod,'app_action');

// ---- naming an agent the server will not accept is refused, not redirected
const wrong=await ask('send_message',{text:'hi',agent:'not-an-agent-of-mine'});
assert.equal(wrong.ok,false);
assert(/no agent of yours is named not-an-agent-of-mine/.test(wrong.error),wrong.error);
assert.equal(sends.length,1,'a refused agent must not fall back to the default conversation');
const unselected=await ask('send_message',{text:'hi',agent:'b1'});
assert.equal(unselected.ok,false);
assert(/selected conversation only/.test(unselected.error),unselected.error);
assert.equal(sends.length,1);

// ---- switching persists through the ONE revision-guarded write ----------
const startRevision=appUi.revision;
const bindingBefore=JSON.stringify(binding);
calls=[];
await u.chooseDefault();
assert.equal(u.active,null,'default chat is applied immediately');
assert.equal($('ui-frame-host').hidden,true);
assert(!$('view-chat').classes.has('ui-custom-active'));
const wrote=calls.filter(c=>c.tool==='write_graph');
assert.equal(wrote.length,1);
assert.equal(wrote[0].args.target,'app_ui');
assert.equal(wrote[0].args.graph_id,HOME);
assert.equal(wrote[0].args.expected_revision,startRevision);
assert.deepEqual(JSON.parse(wrote[0].args.payload_json),{ui_selection:{version:1,state:'default'}},
 'a choice writes the choice and nothing else');
assert.deepEqual(appUi.ui_library,[bundleOf()],'the library survives a selection write');
assert.equal(JSON.stringify(binding),bindingBefore,'no agent binding is touched by a UI choice');
assert.equal(u.revision,startRevision+1);

await u.choose('office');
assert(u.active&&u.active.ui_id==='office');
assert.deepEqual(appUi.ui_selection,{version:1,state:'active',ui_id:'office'});

// ---- a save that loses a race is refused, never an overwrite ------------
raceNext=true;
const raced=appUi.ui_selection;
await u.chooseDefault();
assert.deepEqual(appUi.ui_selection,raced,'a stale write must not land');
assert(/not saved \(app UI changed/.test($('ui-status').textContent),$('ui-status').textContent);
assert.equal(calls.filter(c=>c.tool==='write_graph'&&c.args.target==='app_ui').length,3,
 'the conflict is reported, not retried');

// ---- a remixed bundle acts as the REMIXER ------------------------------
// Bob authored it; Alice installs the component into her own library. Nothing
// about Bob travels into the calls it can make.
const bobs=bundleOf({ui_id:'bob-tower',name:'Bob tower',
 script:'tinyassets.call("list_agents",{graph_id:"u-bob"})'});
const outcome=await u.install(bobs);
assert(outcome.ok,JSON.stringify(outcome));
assert.equal(appUi.ui_library.length,2);
assert.deepEqual(appUi.ui_selection,raced,'an install leaves the choice alone');
await u.choose('bob-tower');
assert(u.active&&u.active.ui_id==='bob-tower');
const bobFrame=u.frame.contentWindow;
emit({source:bobFrame,data:{ta_ui:1,type:'ready'}});
calls=[];
emit({source:bobFrame,data:{ta_ui:1,type:'call',id:'z',action:'list_agents',params:{graph_id:'u-bob'}}});
for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));
const remixCalls=calls.filter(c=>c.tool==='read_graph'&&c.args.target==='agent_bindings');
assert.equal(remixCalls.length,1);
assert.equal(remixCalls[0].args.graph_id,HOME,'a remix reaches the remixer, never the author');
const remixWho=await (async()=>{const before=bobFrame.posts.length;
 emit({source:bobFrame,data:{ta_ui:1,type:'call',id:'w',action:'whoami',params:{}}});
 for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));
 return bobFrame.posts[bobFrame.posts.length-1];})();
assert.equal(remixWho.result.command_center_id,HOME);

// ---- sharing produces a public component, and only that ----------------
const published=u.publishPayload(bobs,'A tower');
assert(published.ok);
assert.deepEqual(Object.keys(published.payload.components),['ui']);
assert.equal(published.payload.components.ui.kind,u.KIND);
assert.equal(published.payload.name,'Bob tower');
assert(!u.publishPayload({...bobs,extra:1},'').ok);
// One UI component per definition, so there is never a question which one runs.
assert(!u.readDefinition({components:{a:bobs,b:bundleOf()}}).ok);
assert(!u.readDefinition({components:{}}).ok);
assert.equal(u.readDefinition({components:{only:bobs}}).bundle.ui_id,'bob-tower');

// ---- the read is pinned, and its answer is checked (Codex P1) ---------
// `verify()` sees nothing wrong here: `fetchMe` still reports this home. Only
// the status answer disagrees, which is the window between the two.
const pinnedFrame=u.frame.contentWindow;
calls=[];
const pinnedRead=await (async()=>{const before=pinnedFrame.posts.length;
 emit({source:pinnedFrame,data:{ta_ui:1,type:'call',id:'pin',action:'read_conversation',params:{}}});
 for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));
 return pinnedFrame.posts[pinnedFrame.posts.length-1];})();
const statusCalls=calls.filter(c=>c.tool==='get_status');
assert.equal(statusCalls.length,1);
assert.equal(statusCalls[0].args.universe_id,HOME,'the read names the granted home');
assert.equal(pinnedRead.ok,true);

// Now the server answers about a DIFFERENT universe while fetchMe still says
// this one. The reply must be refused, not rendered.
statusUniverseOverride='u-bob';
const mismatched=await (async()=>{const before=pinnedFrame.posts.length;
 emit({source:pinnedFrame,data:{ta_ui:1,type:'call',id:'mis',action:'read_conversation',params:{}}});
 for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));
 return pinnedFrame.posts[pinnedFrame.posts.length-1];})();
assert.equal(mismatched.ok,false,'an answer about another universe is refused');
assert(/another command center/.test(mismatched.error),mismatched.error);
assert.equal(JSON.stringify(mismatched).includes('BOBS PRIVATE TURN'),false);
statusUniverseOverride='';

// ---- a home change under the same login ends the grant (Codex P1) ------
// The account moves home while a bundle is mounted. `get_status` with no
// universe would hand it the NEW home's conversation.
conversation=[{speaker:'founder',text:'ALICE PRIVATE TURN',ts:2,truncated:false}];
const mountedFrame=u.frame.contentWindow;
me={principal_id:PRINCIPAL,universe_id:'u-bob',setup:'connected'};
const leak=await (async()=>{const before=mountedFrame.posts.length;
 emit({source:mountedFrame,data:{ta_ui:1,type:'call',id:'leak',action:'read_conversation',params:{}}});
 for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));
 return mountedFrame.posts[mountedFrame.posts.length-1];})();
assert.equal(leak.ok,false,'a moved home must not be served to the old bundle');
assert(/identity or home changed/.test(leak.error),leak.error);
assert.equal(JSON.stringify(leak).includes('BOBS PRIVATE TURN'),false);
assert.equal(u.frame,null,'the bridge is revoked, not merely refused once');
assert.equal(u.enabled,false);

// The funnel the app actually calls must revoke too, not just the handler.
me={principal_id:PRINCIPAL,universe_id:HOME,setup:'connected'};
appUi=stored([bundleOf()],{version:1,state:'active',ui_id:'office'});
u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;u.adopt(clone(appUi));
assert(u.active,'the saved UI is mounted again');
u.homeChanged('u-carol');
assert.equal(u.frame,null,'homeChanged tears the frame down');
assert.equal(u.enabled,false);
u.homeChanged('u-carol');   // idempotent on an already-reset controller

// ---- a reply is owed to the frame that asked (Codex P2) ----------------
me={principal_id:PRINCIPAL,universe_id:HOME,setup:'connected'};
appUi=stored([bundleOf(),bundleOf({ui_id:'second',name:'Second'})],null);
u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;u.adopt(clone(appUi));
u.mount(u.library[0]);
const frameA=u.frame.contentWindow;
emit({source:frameA,data:{ta_ui:1,type:'ready'}});
const postsA=frameA.posts.length;
// Ask, then swap the bundle before the answer lands. Both bootstraps number
// their requests from r1, so a misrouted reply would settle B's own promise.
emit({source:frameA,data:{ta_ui:1,type:'call',id:'r1',action:'read_conversation',params:{}}});
u.mount(u.library[1]);
const frameB=u.frame.contentWindow;
await settle();
assert.equal(frameB.posts.filter(m=>m.type==='result'&&m.id==='r1').length,0,
 "bundle A's answer must not reach bundle B");
assert.equal(frameA.posts.length,postsA,'and it is not delivered to a torn-down frame either');
assert.equal(u.pending,0,"a stale completion must not decrement the new frame's counter");

// ---- installing next to an unreadable library refuses (Codex P1) -------
// One stored bundle is a future version this app cannot parse. Installing must
// not rebuild the library from a cache that dropped it.
const future={...bundleOf({ui_id:'from-tomorrow'}),version:2};
appUi=stored([bundleOf(),future],null);
u.adopt(clone(appUi));
assert(u.unreadable,'an unreadable library is remembered as unreadable, not as empty');
assert.deepEqual(u.library,[]);
const storedBefore=JSON.stringify(appUi);
calls=[];
const refused=await u.install(bundleOf({ui_id:'newcomer'}));
assert(!refused.ok,'install must refuse rather than overwrite');
assert.equal(calls.length,0,'and neither reads nor writes');
assert.equal(JSON.stringify(appUi),storedBefore,'the bundle it could not parse is still stored');

// The re-read inside the save is the backstop: even with a clean cache, a
// library that is unreadable NOW is refused before anything is written.
u.unreadable='';u.library=[bundleOf()];
calls=[];
const sneaky=await u.install(bundleOf({ui_id:'newcomer'}));
assert(!sneaky.ok,'the mutation re-checks what the save actually read');
assert.equal(calls.filter(c=>c.tool==='write_graph').length,0,'and writes nothing');
assert.equal(JSON.stringify(appUi),storedBefore);
assert(/cannot be read/.test($('ui-status').textContent),$('ui-status').textContent);

// ---- size is measured in UTF-8 bytes, not UTF-16 units (Codex P2) ------
// Characters that cost three bytes each. A character-counting limit accepts
// this; the server, which caps bytes, would not.
const cjkChars=Math.ceil(u.MAX_TEXT_BYTES/3)+16;
const cjk=bundleOf({ui_id:'cjk',markup:'漢'.repeat(cjkChars)});
assert(cjk.markup.length<u.MAX_TEXT_BYTES,'under the bound in characters');
assert(u.bytes(cjk.markup)>u.MAX_TEXT_BYTES,'the fixture really is over it in bytes');
const cjkRead=u.parseBundle(cjk);
assert(!cjkRead.ok,'a bundle over the BYTE limit is refused');
assert(/bytes/.test(cjkRead.reason),cjkRead.reason);

// ---- the one limit is total size, checked against the stored row -------
// Many UIs install: there is no count to run into.
appUi=stored(Array.from({length:40},(_,i)=>bundleOf({ui_id:'many-'+i})),null);
u.adopt(clone(appUi));
const fortyFirst=await u.install(bundleOf({ui_id:'many-40'}));
assert(fortyFirst.ok,'a 41st UI installs: '+JSON.stringify(fortyFirst));
assert.equal(appUi.ui_library.length,41);

// A large stored library installs one more, whatever its size. The 4 MiB
// MAX_LIBRARY_BYTES refusal ("remove one first") is gone: those bytes are the
// universe's storage, which is one of an account's two limits, not a UI quota
// (founder, 2026-09-30).
const heavy=i=>bundleOf({ui_id:'heavy-'+i,markup:'x'.repeat(32768)});
const nearFull=[];
while(u.bytes(JSON.stringify(nearFull))<=4194304) nearFull.push(heavy(nearFull.length));
assert(nearFull.length>=50,'past the old ceiling: '+nearFull.length+' max-size UIs');
assert(u.MAX_LIBRARY_BYTES===undefined,'the library byte ceiling is gone');
appUi=stored(nearFull,null);
u.adopt(stored([],null));
calls=[];
const overflow=await u.install(heavy('one-more'));
assert(overflow.ok,'a UI past the old byte limit installs: '+JSON.stringify(overflow));
assert.equal(calls.filter(c=>c.tool==='write_graph').length,1,'and it is written once');
assert.equal(appUi.ui_library.length,nearFull.length+1);
assert(!/remove one first/.test($('ui-status').textContent),$('ui-status').textContent);
// Re-installing a ui_id already there replaces it rather than adding to the size.
u.adopt(clone(appUi));
const replaced=await u.install(bundleOf({ui_id:'heavy-0',name:'Renamed'}));
assert(replaced.ok,JSON.stringify(replaced));
assert.equal(appUi.ui_library.length,nearFull.length+1,'a replace adds no row');
assert.equal(appUi.ui_library.find(b=>b.ui_id==='heavy-0').name,'Renamed');

// ---- a brand-new account installs from nothing (lead, 2026-09-26) -----
// No row, no binding, no published definition. The first save creates the row
// at revision 0 -> 1; there is no setup step and nothing is published.
appUi=null; binding=null;
u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;u.library=[];u.unreadable='';u.selection=null;
calls=[];
const fresh=await u.install(bundleOf({ui_id:'from-nothing',name:'From nothing'}));
assert(fresh.ok,'a fresh account can install: '+JSON.stringify(fresh));
const firstSaves=calls.filter(c=>c.tool==='write_graph');
assert.equal(firstSaves.length,1,'exactly one write creates the row');
assert.equal(firstSaves[0].args.target,'app_ui');
assert.equal(firstSaves[0].args.expected_revision,0);
assert.deepEqual(appUi.ui_library.map(b=>b.ui_id),['from-nothing']);
assert.equal(appUi.revision,1);
assert.equal(u.library.length,1);

// And it can then be switched to and remembered, which is the whole journey.
await u.choose('from-nothing');
assert(u.active&&u.active.ui_id==='from-nothing');
assert.deepEqual(appUi.ui_selection,{version:1,state:'active',ui_id:'from-nothing'});
const secondUI=await u.install(bundleOf({ui_id:'second-one',name:'Second'}));
assert(secondUI.ok,JSON.stringify(secondUI));
assert.deepEqual(appUi.ui_library.map(b=>b.ui_id).sort(),['from-nothing','second-one']);
assert.equal(appUi.revision,3);
assert.equal(binding,null,'no agent binding was created for any of it');

// Across EVERY step above, the UI controller never wrote an agent binding.
assert.equal(allCalls.filter(c=>c.tool==='write_graph'&&c.args.target!=='app_ui').length,0,
 'every UI write goes to app_ui: '+JSON.stringify(allCalls.filter(c=>c.tool==='write_graph').map(c=>c.args.target)));

// ---- sign-out tears the bridge down ----------------------------------
u.reset();
assert.equal(u.frame,null);
assert.equal(listeners.length,0,'the message listener is removed with the frame');
assert.equal($('btn-ui-switch').hidden,true);

console.log('custom-ui bridge checks passed');
})().catch(err=>{console.error(err);process.exit(1);});
'''


# The sample the founder named: an office building whose rooms are agents. It
# lives here and only here -- the platform ships no bundle, because a shipped one
# would be a fixed archetype and the point is that a universe writes its own.
#
# Its markup is deliberately the kind a sanitizer would mangle (an inline handler,
# a data URI, an entity, a CDATA-looking string). Nothing sanitizes it, so the
# assertion below is that it arrives at the frame byte for byte.
OFFICE_BUNDLE = r'''
const OFFICE={kind:'tinyassets.app-ui.v1',version:1,ui_id:'office-tower',
 name:'Office tower',
 markup:'<div class="floor" data-room="lobby">'+
  '<img src="data:image/gif;base64,R0lGOD" alt="lobby &amp; desk" onerror="this.hidden=true">'+
  '<button id="room-lobby" onclick="enter(\'lobby\')">Lobby &rarr;</button>'+
  '<pre><![CDATA[ not really cdata ]]></pre></div>',
 style:'.floor{display:grid}.floor[data-room="lobby"]::after{content:"\\2318"}',
 script:"async function enter(room){const who=await tinyassets.whoami();"+
  "const mine=await tinyassets.listAgents();"+
  "const agent=mine.agents.find(a=>a.name.toLowerCase().includes(room));"+
  "await tinyassets.sendMessage('I walked into the '+room,agent&&agent.agent_id);"+
  "document.title=who.command_center_name;}"};
'''

SAMPLE_CHECKS = r'''
(async()=>{
const u=AppUI;
// Install it the way a universe's own agent would, then select it.
appUi=null;
u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;

const installOutcome=await u.install(OFFICE);
assert(installOutcome.ok,JSON.stringify(installOutcome));
await u.choose('office-tower');
assert(u.active&&u.active.ui_id==='office-tower');

const win=u.frame.contentWindow;
emit({source:win,data:{ta_ui:1,type:'ready'}});
const delivered=win.posts[0].bundle;

// Hard Rule 9: what the user wrote is what runs. A markup sanitizer, an entity
// re-encode, or a CSS rewrite would all show up right here.
assert.strictEqual(delivered.markup,OFFICE.markup);
assert.strictEqual(delivered.style,OFFICE.style);
assert.strictEqual(delivered.script,OFFICE.script);
assert(delivered.markup.includes('onerror='),'an inline handler survives verbatim');
assert(delivered.markup.includes('&amp;'),'entities are not re-encoded');
assert(delivered.markup.includes('<![CDATA['),'nothing is parsed and re-serialised');

// And it survives the round trip through the row it is stored in: what the
// store holds parses back to the same bundle.
const reread=u.readLibrary(appUi);
assert(reread.ok,reread.reason);
assert.strictEqual(reread.entries[0].script,OFFICE.script);

// Every action its script calls is one the bridge actually has. A sample that
// programmed against an action the allowlist lacks would be a broken example.
for(const action of ['whoami','listAgents','sendMessage'])
 assert(Object.values(u.ACTIONS).includes(action),action+' must exist for the sample to work');

console.log('office sample checks passed');
})().catch(err=>{console.error(err);process.exit(1);});
'''


def _run(tmp_path, name, checks, extra=""):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for actual JavaScript controller")
    controller = Path("tinyassets/onboarding/app_ui.js").read_text(encoding="utf-8")
    script = tmp_path / name
    script.write_text(HARNESS + extra + controller + checks, encoding="utf-8")
    result = subprocess.run(
        [node, str(script)], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_bundle_bridge_is_a_closed_allowlist_acting_as_the_viewer(tmp_path):
    out = _run(tmp_path, "custom_ui_bridge.js", CHECKS)
    assert "custom-ui bridge checks passed" in out


def test_a_real_bundle_reaches_the_frame_exactly_as_its_author_wrote_it(tmp_path):
    out = _run(tmp_path, "custom_ui_sample.js", SAMPLE_CHECKS, extra=OFFICE_BUNDLE)
    assert "office sample checks passed" in out
