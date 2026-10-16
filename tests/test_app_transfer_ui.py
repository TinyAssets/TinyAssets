"""Execute the transfer module: private selections, text-only rendering and stale results."""
from __future__ import annotations

import base64
from pathlib import Path

from tests.test_app_account_transition import _function_source, _run_node
from tinyassets.onboarding import render_app_html

SOURCE = Path(__file__).resolve().parents[1] / "tinyassets/onboarding/app/transfer.js"


def test_private_transfer_ui_boundaries():
    module = base64.b64encode(SOURCE.read_bytes()).decode()
    result = _run_node(r'''
const assert = require('node:assert/strict');
class Node {
  constructor(tag) { this.tag=tag; this.children=[]; this.style={};
    this.events={}; this.checked=false; }
  set innerHTML(value) { throw new Error('untrusted HTML insertion'); }
  append(...nodes) { this.children.push(...nodes); nodes.forEach(n=>n.parent=this); }
  replaceChildren(...nodes) { this.children=[]; this.append(...nodes); }
  setAttribute() {}
  addEventListener(name, fn) { this.events[name]=fn; }
  showModal() { this.open=true; }
  close() { this.open=false; this.events.close?.(); }
  remove() { if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this); }
  click() { return this.onclick?.(); }
}
global.document={body:new Node('body'),createElement:tag=>new Node(tag),
  createTextNode:text=>Object.assign(new Node('text'),{textContent:text})};
function all(root, predicate) {
  return [root,...root.children.flatMap(n=>all(n,()=>true))].filter(predicate); }
const button = text => all(document.body,n=>n.tag==='button'&&n.textContent===text)[0];
const inputs = () => all(document.body,n=>n.type==='checkbox');
const tick = () => new Promise(r=>setImmediate(r));
(async()=>{
 const {openTransfer}=await import('data:text/javascript;base64,''' + module + r'''');
 const calls=[]; let current=true, delayed=null, downloads=0;
 URL.createObjectURL=()=>{downloads++;return 'blob:test';}; URL.revokeObjectURL=()=>{};
 const request=async body=>{
   calls.push(body);
   if(body.operation==='list')return {ok:true,json:async()=>({files:[
     {path:'workspace/note',size:2}]})};
   if(body.operation==='preview')return {ok:true,json:async()=>({
     preview:'p',limitations:'review',rules:[{path:'agents/main/rules.json',content:'<rule>'}],files:[
     {path:'workspace/note',binary:false,blocked:null,
      content:'<img src=x onerror=steal()>',contacts:[{index:0,value:'person@example.com'}]},
     {path:'workspace/blocked',binary:false,blocked:'credential',content:'secret',contacts:[]},
     {path:'workspace/image',binary:true,blocked:null,content:null,contacts:[]}]})};
   if(body.operation==='export')return {ok:true,blob:()=>new Promise(resolve=>{delayed=resolve;})};
   throw new Error('unexpected request');
 };
 openTransfer({commandCenter:'home-a',request,isCurrent:()=>current}); await tick();
 assert(inputs().every(n=>!n.checked));
 inputs()[0].checked=true; await button('Preview selected files').click();
 assert(inputs().slice(1).every(n=>!n.checked));
 assert(all(document.body,n=>n.tag==='pre').some(n=>n.textContent.includes('<img')));
 assert(all(document.body,n=>n.tag==='pre').some(n=>n.textContent==='<rule>'));
 const check=inputs(); check[1].checked=true; check[2].checked=true;
 assert(check[3].disabled); check[3].checked=true; check[4].checked=true;
 const pending=button('Download private zip').click(); await tick();
 assert.deepEqual(calls.at(-1).selected,['workspace/note','workspace/image']);
 assert.deepEqual(calls.at(-1).named_binaries,['workspace/image']);
 assert.deepEqual(calls.at(-1).included_contacts,{'workspace/note':[0]});
 current=false; delayed(new Blob(['private'])); await pending;
 assert.equal(downloads,0); assert.equal(document.body.children.length,0);
 console.log(JSON.stringify({passed:true}));
})().catch(error=>{console.error(error);process.exitCode=1;});
''')
    assert result == {"passed": True}


def test_account_clear_closes_private_preview_immediately():
    html, _ = render_app_html()
    source = _function_source(html, "clearAccountScopedState")
    # Run the real synchronous first boundary before unrelated app collaborators.
    source = source[:source.index('if(typeof CredentialRequests')] + "}"
    result = _run_node('let closed=false; const $=id=>({close(){closed=true;}});\n'
                       + source + '\nclearAccountScopedState();'
                       + 'console.log(JSON.stringify(closed));')
    assert result is True
    assert "__TA_TRANSFER_URL__" not in html


def test_import_review_is_separate_and_binary_copy_needs_confirmation():
    module = base64.b64encode(SOURCE.read_bytes()).decode()
    result = _run_node(r'''
const assert = require('node:assert/strict');
class Node {
 constructor(tag){this.tag=tag;this.children=[];this.style={};this.events={};this.checked=false;}
 set innerHTML(value){throw new Error('untrusted HTML insertion');}
 append(...nodes){this.children.push(...nodes);nodes.forEach(n=>n.parent=this);}
 replaceChildren(...nodes){this.children=[];this.append(...nodes);}
 setAttribute(){} addEventListener(name,fn){this.events[name]=fn;}
 showModal(){this.open=true;} close(){this.open=false;this.events.close?.();}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);}
 click(){return this.onclick?.();}
}
global.document={body:new Node('body'),createElement:tag=>new Node(tag),
 createTextNode:text=>Object.assign(new Node('text'),{textContent:text})};
function all(root){return [root,...root.children.flatMap(all)];}
const button=text=>all(document.body).find(n=>n.tag==='button'&&n.textContent===text);
const tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
 const {openTransfer}=await import('data:text/javascript;base64,''' + module + r'''');
 const calls=[];
 const request=async body=>{
  calls.push(body);
  let result;
  if(body.operation==='list') result={files:[]};
  else if(body.operation==='import') result={import_id:'import-one'};
  else if(body.operation==='import-preview') result={request:'pin-one',destination:'imports/one',
   agents:['main'],
   files:[{path:'note.md',to:'imports/one/note.md',size:3,binary:false,content:'<script>bad()</script>'},
          {path:'image.bin',to:'imports/one/image.bin',size:2,binary:true,content:null}]};
  else if(body.operation==='import-adopt-preview') result={request:'pin-adopt',
   destination:'agents/import-one',
   adopts_agent:true, notice:'Uses your default model and policy',
   files:[{path:'AGENTS.md',to:'agents/import-one/AGENTS.md',size:3,binary:false,
    content:'Agent instructions'}]};
  else if(body.operation==='import-activate') result={destination:'imports/one',
   state:body.request==='pin-adopt'?'adopted':'copied'};
  else throw new Error('unexpected operation');
  return {ok:true,json:async()=>result};
 };
 openTransfer({commandCenter:'home',request,isCurrent:()=>true}); await tick();
 const file=all(document.body).find(n=>n.type==='file');
 file.files=[{size:3,arrayBuffer:async()=>new Uint8Array([1,2,3]).buffer}];
 await button('Import into quarantine').click();
 assert.equal(calls.at(-1).operation,'import');
 assert(button('Copy reviewed files into this box').disabled);
 await button('Review imported files').click();
 assert(all(document.body).some(n=>n.tag==='pre'&&n.textContent==='<script>bad()</script>'));
 const count=calls.length;
 await button('Copy reviewed files into this box').click();
 assert.equal(calls.length,count,'unnamed binary crossed confirmation');
 all(document.body).find(n=>n.type==='checkbox').checked=true;
 await button('Copy reviewed files into this box').click();
 assert.equal(calls.at(-1).operation,'import-activate');
 assert.deepEqual(calls.at(-1).named_binaries,['image.bin']);
 assert.equal(calls.at(-1).request,'pin-one');
 assert(button('Copy reviewed files into this box').disabled);
 all(document.body).find(n=>n.tag==='select').value='main';
 await button('Review native agent adoption').click();
 assert.equal(calls.at(-1).operation,'import-adopt-preview');
 assert.equal(calls.at(-1).agent,'main');
 assert(all(document.body).some(n=>n.tag==='pre'&&n.textContent==='Agent instructions'));
 await button('Adopt reviewed agent').click();
 assert.equal(calls.at(-1).operation,'import-activate');
 assert.equal(calls.at(-1).request,'pin-adopt');
 assert.deepEqual(calls.at(-1).named_binaries,[]);
 button('Close').click(); assert.equal(document.body.children.length,0);
 console.log(JSON.stringify({passed:true}));
})().catch(error=>{console.error(error);process.exitCode=1;});
''')
    assert result == {"passed": True}
