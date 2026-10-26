"""Execute the shipped send/journal/reload code in independent browser contexts."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_onboarding_app import _js_function

pytestmark = pytest.mark.verifies("owner-daemon-in-box:One durable owner send intake (S24)")

APP = Path(__file__).resolve().parents[1] / 'tinyassets/onboarding/app'


def test_saved_admission_is_not_a_terminal_receipt(tmp_path):
    main = (APP / 'main.js').read_text(encoding='utf-8')
    source = '\n'.join(_js_function(main, name) for name in (
        'savedSendIndex', 'offerSavedConversationCheck'))
    script = '''
const assert=require('node:assert/strict');
const MCP={_loginEpoch:1},queueOwner='owner',queueScope='home';
const addressedAgentId=()=> 'main',readPendingTurns=async()=>null,drawHistoryTurns=()=>{};
let forgotten=0,removed=0;
const forgetInflightIf=()=>forgotten++;
const turns=[{speaker:'founder',client_send_id:'pending'}];
const Owner={getConversation:async()=>({recent_conversation:{turns}})};
const note={parentNode:true,savedConversationLine:{parentNode:true},
    unconfirmed:{client_send_id:'pending',record:{}},remove:()=>removed++};
''' + source + '''
(async()=>{
await offerSavedConversationCheck(note);assert.equal(forgotten,0);assert.equal(removed,0);
turns.push({speaker:'universe',reply_to_client_send_id:'another'});
await offerSavedConversationCheck(note);assert.equal(forgotten,0);
turns.push({speaker:'platform',reply_to_client_send_id:'pending'});
await offerSavedConversationCheck(note);assert.equal(forgotten,1);assert.equal(removed,1);
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    path = tmp_path / 'receipt.cjs'
    path.write_text(script, encoding='utf-8')
    result = subprocess.run([shutil.which('node'), str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_restore_loads_every_agent(tmp_path):
    source = _js_function((APP / 'main.js').read_text(encoding='utf-8'), 'restoreInflight')
    script = '''
const assert=require('node:assert/strict');
let inflightRestored=false,queueOwner='owner',queueScope='home';
const activeSends=new Map(),sent=[];
const importSavedQueue=async()=>{},ownsSavedRow=r=>r.owner==='owner';
const addressedAgentId=()=> 'main',$=()=>({children:[]});
const readInflights=()=>['main','helper'].map(agent=>({owner:'owner',agent,
    client_send_id:agent,message:'hello'}));
const sendTurn=(message,display,opts)=>sent.push(opts.agentId);
''' + source + '''
(async()=>{await restoreInflight([]);await restoreInflight([]);
assert.deepEqual(sent,['main','helper']);})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    path = tmp_path / 'restore.cjs'
    path.write_text(script, encoding='utf-8')
    result = subprocess.run([shutil.which('node'), str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_busy_send_reload_and_storage_failure(tmp_path):
    main = (APP / 'main.js').read_text(encoding='utf-8')
    source = '\n'.join([
        (APP / 'send_journal.js').read_text(encoding='utf-8'),
        (APP / 'durable_send.js').read_text(encoding='utf-8'),
        *(_js_function(main, name) for name in (
            'restoreInflight', 'ownsSavedRow')),
    ])
    script = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const crypto=require('node:crypto').webcrypto;
const values=new Map(),calls=[],pending=[];
let failStorage=false;
const storage={
  get length(){return values.size;},key(i){return [...values.keys()][i];},
  getItem(k){return values.get(k)||null;},removeItem(k){values.delete(k);},
  setItem(k,v){if(failStorage)throw Error('quota');values.set(k,v);}
};
function page(){
  const nodes=[],input={value:'draft',style:{}},button={disabled:false};
  const context=vm.createContext({crypto,TextEncoder,console,localStorage:storage,
    setTimeout,clearTimeout,
    MCP:{_loginEpoch:1,converse(...args){calls.push(args);return new Promise(r=>pending.push(r));}},
    Owner:{getConversation:async()=>({recent_conversation:{turns:[]}})},
    $:id=>id==='thread'?{children:nodes}:id==='composer-input'?input:button,
    appendMessage:()=>{const n={className:'msg--founder'};nodes.push(n);return n;},
    addressedAgentId:()=> 'main',turnInputMethod:x=>x||'typed',copyModelChoice:x=>x||null,
    setStatusLine:()=>{},renderWorking:()=>{},refreshRail:()=>{},takeInterruptFlush:()=>{},
    renderConverse:()=>{},drawHistoryTurns:()=>{},offerResend:()=>{},
    InlineConnection:{waiting:()=>false},
    Voice:{turnStarted:()=>{},conversationSettled:()=>{}},
  });
  vm.runInContext(`const INFLIGHT_KEY='inflight',QUEUE_KEY='queue';
    const activeSends=new Map();let activeTurn=null,liveInflight=null,turnStartedAt=0;
    let queueOwner='owner',queueScope='home',inflightRestored=false;
    let modelChoiceForNextTurn=null;`,context);
  vm.runInContext(SOURCE,context);
  return {context,input,nodes,run:s=>vm.runInContext(s,context)};
}
(async()=>{
  const first=page();
  const one=first.run("sendTurn('first')");
  const two=first.run("sendTurn('second')");
  assert.equal(calls.length,2,'busy send reaches the same transport immediately');
  assert.equal(values.size,2,'each send has its own recovery record');
  const ids=calls.map(c=>c[5]);assert.notEqual(ids[0],ids[1]);
  pending[0]({reply:'one',history_saved:true});await one;
  assert.equal(values.size,1,'first completion cannot erase the second');
  const reload=page();
  await reload.run('restoreInflight([])');
  assert.equal(calls[2][5],ids[1],'reload preserves send identity');
  await reload.run('restoreInflight([])');
  assert.equal(calls.length,3,'one reload cannot publish duplicate notices/sends');
  pending[1]({reply:'two',history_saved:true});await two;
  pending[2]({reply:'two',history_saved:true});
  await new Promise(r=>setTimeout(r,0));assert.equal(values.size,0);
  failStorage=true;reload.input.value='keep me';
  await assert.rejects(reload.run("sendTurn('keep me')"));
  assert.equal(reload.input.value,'keep me');assert.equal(calls.length,3);
  failStorage=false;
  const scoped=page(),choice=scoped.run("sendTurn('selected conversation')");
  scoped.run("queueScope='different-home'");
  pending[3]({error:'consumer_request_required',consumer_selection:{
    version:1,binding_id:'binding',binding_revision:1}});
  await new Promise(r=>setTimeout(r,0));
  assert.equal(calls[4][7],'home','negotiation cannot retarget a send after a home switch');
  assert.equal(JSON.parse([...values.values()][0]).scope,'home');
  pending[4]({reply:'selected',history_saved:true});await choice;
  const held=page(),setup=held.run("sendTurn('connect later')");
  pending[5]({retryable_setup:true,history_saved:true,error:'Connection setup unavailable'});
  await setup;assert.equal(values.size,1,'a no-effect setup hold remains recoverable');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    path = tmp_path / 'send.cjs'
    path.write_text('const SOURCE=' + json.dumps(source) + ';\n' + script, encoding='utf-8')
    node = shutil.which('node')
    assert node, 'Node is required for the send proof'
    result = subprocess.run([node, str(path)], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
