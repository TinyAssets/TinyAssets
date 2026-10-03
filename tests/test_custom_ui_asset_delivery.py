"""The app fetches a UI's bytes, checks every pin, and only then hands them over.

Runs the shipped controller (app_ui.js) in Node against the bridge harness. The
frame has no network, so whatever reaches it was fetched HERE with the viewer's
bearer; these assert what is requested, what is refused, and what is posted.
"""
# ruff: noqa: E501 -- embedded JavaScript fixture mirrors controller expressions
from __future__ import annotations

from tests.test_custom_ui_bridge import _run

BYTES_DOUBLE = r'''
const enc=s=>new TextEncoder().encode(s).buffer;
const LIB={three:enc('export const REVISION="170";'),
 'three/addons/controls/OrbitControls.js':enc('import * as T from "three"; export class OrbitControls{}')};
const FILES={};
let fetches=[], tamper='';
const token=()=> 'alice-token';
const ensureFreshToken=async()=>{};
const sha=async(alg,buf)=>new Uint8Array(await crypto.subtle.digest(alg,buf));
const hex=b=>Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');
const fetch=async(path,options)=>{
 const body=JSON.parse(options.body);
 fetches.push({path,auth:options.headers.Authorization,body});
 let buf=null;
 if(body.library) buf=LIB[body.library];
 else if(body.graph_id===HOME) buf=FILES[body.sha256];
 if(buf&&tamper&&(body.library===tamper||body.sha256===tamper)) buf=enc('tampered');
 await new Promise(r=>setImmediate(r));
 if(!buf) return {ok:false,status:404,json:async()=>({error:'app_ui_asset_not_found'})};
 return {ok:true,status:200,arrayBuffer:async()=>buf};
};
'''

CHECKS = r'''
(async()=>{
const u=AppUI;
// The ready event starts asynchronous hashing. Observe its real completion;
// a fixed number of event-loop turns cannot establish that bytes were checked.
let delivery=Promise.resolve();
const originalDelivery=u.deliverWithFiles;
u.deliverWithFiles=function(...args){
 delivery=originalDelivery.apply(this,args);
 return delivery;
};
// Pins for the double's library bytes, in the controller's own table shape.
const pin=async b=>'sha384-'+Buffer.from(await sha('SHA-384',b)).toString('base64');
u.LIBRARIES={three:{format:'module',sha384:await pin(LIB.three),requires:[]},
 'three/addons/controls/OrbitControls.js':{format:'module',
   sha384:await pin(LIB['three/addons/controls/OrbitControls.js']),requires:['three']}};
const png=enc('PNG-BYTES'),png2=enc('ROOF-BYTES');
const ref=async b=>({sha256:hex(await sha('SHA-256',b)),size:b.byteLength,media_type:'image/png'});
const grass=await ref(png),roof=await ref(png2);
FILES[grass.sha256]=png; FILES[roof.sha256]=png2;
const village=bundleOf({ui_id:'village',script_type:'module',
 libraries:['three/addons/controls/OrbitControls.js'],
 assets:{'img/grass.png':grass,'img/roof.png':roof}});

const mountReady=async(entry)=>{
 appUi=stored([entry],{version:1,state:'active',ui_id:entry.ui_id});
 u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;u.adopt(clone(appUi));
 const win=u.frame.contentWindow;
 emit({source:win,data:{ta_ui:1,type:'ready'}});
 return win;
};

// ---- the happy path: bearer, viewer's home, requirements first -----------
let win=await mountReady(village);
assert.equal(win.posts.length,0,'nothing reaches the frame before its bytes are checked');
await delivery;
assert.equal(win.posts.length,1);
const b=win.posts[0].bundle;
assert.equal(b.script_type,'module');
assert.deepEqual(b.libraries.map(l=>l.name),['three','three/addons/controls/OrbitControls.js'],'requirements load first');
assert.deepEqual(b.libraries.map(l=>l.format),['module','module']);
assert.deepEqual(b.files.map(f=>f.path).sort(),['img/grass.png','img/roof.png']);
assert.equal(Buffer.from(b.files.find(f=>f.path==='img/grass.png').bytes).toString(),'PNG-BYTES');
assert.equal(b.files[0].media_type,'image/png');
for(const f of fetches){
 assert.equal(f.path,'/app/api/ui-asset');
 assert.equal(f.auth,'Bearer alice-token');
 if(!f.body.library) assert.equal(f.body.graph_id,HOME,'the home is the viewer\'s, never the bundle\'s');
}

// ---- a library is fetched once per page life ------------------------------
fetches=[];
win=await mountReady(bundleOf({ui_id:'v2',libraries:['three']}));
await delivery;
assert.equal(win.posts.length,1);
assert.equal(fetches.filter(f=>f.body.library).length,0,'a verified library is not refetched');

// ---- a tampered library or asset never reaches the frame -----------------
u.libCache.clear();
tamper='three';
win=await mountReady(village);
await delivery;
assert.equal(win.posts.length,0,'a library failing its pin is not posted');
assert(/integrity check/.test($('ui-status').textContent),$('ui-status').textContent);
tamper=grass.sha256;
win=await mountReady(village);
await delivery;
assert.equal(win.posts.length,0,'an asset failing its hash is not posted');
assert(/asset img\/grass\.png failed its integrity check/.test($('ui-status').textContent),$('ui-status').textContent);
tamper='';

// ---- a missing blob is reported, not rendered half-loaded ----------------
win=await mountReady(bundleOf({ui_id:'gone',assets:{'x.png':{sha256:'f'.repeat(64),size:1,media_type:'image/png'}}}));
await delivery;
assert.equal(win.posts.length,0);
assert(/app_ui_asset_not_found/.test($('ui-status').textContent),$('ui-status').textContent);

// ---- a remount while bytes download drops the stale delivery -------------
u.libCache.clear();
const old=await mountReady(village);
// The person switches to a plain UI before the village's bytes arrive.
const plain=await mountReady(bundleOf({ui_id:'plain',markup:'<p>plain</p>'}));
await delivery;
assert.equal(old.posts.length,0,'bytes fetched for a frame that is gone are dropped');
assert.equal(plain.posts.length,1,'the new frame gets its own bundle and nothing else');
assert.equal(plain.posts[0].bundle.markup,'<p>plain</p>');
assert(!('files' in plain.posts[0].bundle));

console.log('asset delivery checks passed');
})().catch(err=>{console.error(err);process.exit(1);});
'''


def test_the_app_checks_every_pin_before_the_frame_sees_a_byte(tmp_path):
    out = _run(tmp_path, "custom_ui_assets.js", CHECKS, extra=BYTES_DOUBLE)
    assert "asset delivery checks passed" in out


def test_delivery_completion_waits_for_hash_and_integrity_refusal_posts_no_bytes(tmp_path):
    checks = r'''
(async()=>{
const u=AppUI;
const bytes=enc('PNG-BYTES');
const ref={sha256:hex(await sha('SHA-256',bytes)),size:bytes.byteLength,media_type:'image/png'};
FILES[ref.sha256]=bytes; tamper=ref.sha256;
const entry=bundleOf({ui_id:'held-hash',assets:{'img/grass.png':ref}});

let releaseHash,hashStarted;
const hashGate=new Promise(resolve=>{releaseHash=resolve;});
const started=new Promise(resolve=>{hashStarted=resolve;});
const originalDigest=u.digest;
u.digest=async function(...args){
 hashStarted();
 await hashGate;
 return originalDigest.apply(this,args);
};
let delivery;
const originalDelivery=u.deliverWithFiles;
u.deliverWithFiles=function(...args){
 delivery=originalDelivery.apply(this,args);
 return delivery;
};
appUi=stored([entry],{version:1,state:'active',ui_id:entry.ui_id});
u.enabled=true;u.home=HOME;u.principal=PRINCIPAL;u.adopt(clone(appUi));
const win=u.frame.contentWindow;
emit({source:win,data:{ta_ui:1,type:'ready'}});
assert(delivery instanceof Promise,'the ready event exposes the actual delivery work');
let verified=false;
const verification=delivery.then(()=>{
 assert(/asset img\/grass\.png failed its integrity check/.test($('ui-status').textContent),
        $('ui-status').textContent);
 assert.equal(win.posts.length,0,'a tampered asset never reaches the frame');
 verified=true;
});
await started;
await settle(40); // Deliberately exhaust the old wait with hashing still held.
assert.equal(verified,false,'verification cannot race ahead of the held hash');
assert.equal(win.posts.length,0,'no bytes reach the frame while integrity is undecided');
releaseHash();
await verification;
assert.equal(verified,true,'verification runs after integrity refusal completes');
console.log('held hash checks passed');
})().catch(err=>{console.error(err);process.exit(1);});
'''
    out = _run(tmp_path, "custom_ui_held_hash.js", checks, extra=BYTES_DOUBLE)
    assert "held hash checks passed" in out
