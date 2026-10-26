"""A pending-request card the user can actually read, click and reply from.

Live 2026-09-30 on the "Waiting on you" rail at https://tinyassets.io/mcp/app
(the app's URL at the time; it moved to https://tinyassets.io/app in #4112):

1. The action row ``[Accept][Deny][Clear][Send reply]`` overflowed the 16rem
   rail. At innerWidth 1509 "Send reply" was cut to "S"/"r"; at 2400 it spanned
   x=2358..2409 past the viewport, and the only clickable sliver sat over the
   rail's scrollbar, so **a reply could not be sent from a card at all**. The
   grant blockquote quoting a hook URL was clipped mid-URL the same way.
2. The card rendered a field help link "Get it from tinyassets.io" pointing at
   ``https://tinyassets.io/settings`` -- a 404 the agent invented. An
   agent-chosen destination styled like platform help, beside a box asking for a
   secret, is a dead end and a phishing shape.
3. Text set into "Reply or note (optional)" did not stick, and a typed Deny note
   reached the thread as a bare ``Denied: "<title>"``. Both were the same cause:
   the rail re-renders on a 15-second poll and rebuilt every card, deleting what
   the user had typed into it.

The layout half is asserted against the stylesheet the page ships, because no
headless DOM here computes layout; the live proof is a screenshot at 1509px and
390px after deploy. The behaviour halves are EXECUTED under node.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from tests.app_sheet_harness import rail_source
from tests.test_onboarding_app import _js_function
from tinyassets.onboarding import render_app_html

_NODE = shutil.which("node")




#: Comments have to go before the rules are read. This stylesheet is heavily
#: commented, and a `/* ... */` block sitting above a rule lands INSIDE the
#: selector text of a naive `([^{}]+)\{...\}` scan -- so an exact selector match
#: silently finds nothing and the rule reads as absent. It cost a real
#: false negative on `.rtab-link.rtab-link--agent`, which was present the whole
#: time.
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)






# --------------------------------------------------------------------------- #
# 1 + 7: nothing in a card reaches past the rail.
# --------------------------------------------------------------------------- #










# --------------------------------------------------------------------------- #
# 2: an agent-supplied field link is the universe's suggestion, not platform help.
# --------------------------------------------------------------------------- #

_LINK_HARNESS = r"""
function mk(t){return {tag:t,textContent:'',className:'',attrs:{},children:[],
  href:'',target:'',rel:'',
  appendChild(c){this.children.push(c);return c;},
  setAttribute(k,v){this.attrs[k]=v;}};}
const document={createElement:mk};
__SOURCE__
const out=[];
for(const raw of __URLS__){
  const lab=mk('label');
  railFieldLink(raw, lab);
  out.push(lab.children.map(c=>({tag:c.tag,cls:c.className,text:c.textContent,
    href:c.href,rel:c.rel})));
}
console.log(JSON.stringify(out));
"""














# --------------------------------------------------------------------------- #
# 2 (server half): the ask is refused at ask time, cheaply and locally.
# --------------------------------------------------------------------------- #






# --------------------------------------------------------------------------- #
# 3 + 6: what the user typed survives the poll, and Deny carries it.
# --------------------------------------------------------------------------- #

_ASK = {"request_id": "req_gtm", "kind": "API", "status": "pending",
        "title": "Replace the rejected GitHub token to publish GTM Village",
        "body": "The stored token was refused.",
        "fields": [{"name": "token", "label": "GitHub token", "type": "secret",
                    "url": "https://github.com/settings/tokens"}],
        "action": {"type": "connect_http"}, "grant_sentence": "POST api.github.com"}

_NOTE = "do we still need this? you said people can use it through the commons now"


# A harness of its own, NOT the shared one, and the reason is the bug. The
# shared stub's `$` memoises one node per id forever, so a value written through
# it survives any number of rebuilds -- which is precisely the thing under test,
# and a test written on that stub passes with the fix reverted (checked). Here
# `$` walks the live tree the way getElementById does: a node dropped from the
# document is NOT found, and a rebuilt card yields a NEW empty control.
_LIVE_DOM_HARNESS = r"""
function mk(id){return {id:id||'',textContent:'',hidden:false,value:'',open:false,
 rows:0,placeholder:'',type:'',href:'',target:'',rel:'',
 showModal(){this.open=true;},close(){this.open=false;},
 remove(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(c=>c!==this);blurSubtree(this);this.parentNode=null;}},
 children:[],parentNode:null,attrs:{},dataset:{},
 classList:{set:new Set(),toggle(c,on){on?this.set.add(c):this.set.delete(c);},
  contains(c){return this.set.has(c);}},
 setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},
 appendChild(c){if(c.parentNode){const p=c.parentNode;p.children=p.children.filter(x=>x!==c);
     blurSubtree(c);}
   c.parentNode=this;this.children.push(c);return c;},
 replaceChildren(){this.children.forEach(c=>{c.parentNode=null;blurSubtree(c);});
   this.children=[];},
 querySelectorAll(){return [];},
 contains(node){return node===this || this.children.some(child=>child.contains(node));},
 addEventListener(e,f){this['on'+e]=f;},scrollIntoView(){},
 // Focus follows the DOM: a browser blurs an element that leaves the tree,
 // and re-appending it does NOT give focus back. Modelling that is the whole
 // point of the settled-rail early return.
 focus(){focused=this;}};}
let focused=null;
// A browser blurs an element the moment it leaves the document, and moving one
// within the document blurs it too. Without this the harness cannot tell a
// detach-and-reattach from an untouched node, and a focus assertion written on
// it passes against the reverted fix (checked).
function blurSubtree(node){
  if(node===focused) focused=null;
  for(const child of node.children||[]) blurSubtree(child);
}
const document={createElement:t=>{const e=mk('');e.tag=t;return e;},
  createTextNode:t=>{const e=mk('');e.tag='#text';e.textContent=t;return e;}};
// The page's own fixed elements, which exist whatever the rail is showing.
const els=new Map();
for(const id of ['request-rail','rail-items','rail-head','connect-panel',
                 'connect-other','connect-shapes','hosted-model-status','needs-you','needs-you-items',
                 'rail-add-panel','btn-rail-add'])
  els.set(id, mk(id));
const rail=els.get('request-rail'), host=els.get('rail-items');
els.get('rail-head').textContent='Request history';
rail.appendChild(host);
rail.appendChild(els.get('connect-panel'));
Object.defineProperty(host,'textContent',{get(){return '';},set(v){this.replaceChildren();}});
function inTree(node){for(let at=node;at;at=at.parentNode) if(at===rail) return true; return false;}
function findById(node,id){
  if(node.id===id) return node;
  for(const child of node.children){const hit=findById(child,id); if(hit) return hit;}
  return null;
}
// Live tree first, then the fixed elements. Nothing is invented: an id that is
// neither in the document nor a fixed element resolves to null, as in a browser.
const $=id=>findById(rail,id)||els.get(id)||null;
function text(node){return (node.textContent||'')+node.children.map(text).join(' ');}
let railOpen=null, railCache=[], NATIVE=false, connectWasBlocking=null;
let connectOtherOpen=false, railNodes=new Map();
const CONNECT_REQUEST_ID="sys_connect_model";
const RECONNECT_REQUEST_PREFIX="reconnect-source:";
const HostedModelConnect={setup:'empty',busy:false,request:null,primary:null,
 configure(p){this.primary=p;}, adopt(r){this.adopt_seen=r;}, paint(){}};
const ConnectOAuth={decorate(){}};
function answerRail(){}
__SOURCE__
"""


def _run_rail(rows, extra):
    html, _ = render_app_html()
    source = rail_source(html) + "\n" + "\n".join(
        _js_function(html, name) for name in (
            "railFieldLink", "railFieldControl", "railBody", "frameTitle",
            "answerLine", "clearRailCards"))
    script = (_LIVE_DOM_HARNESS.replace("__SOURCE__", source)
              + "\nrenderRail(" + json.dumps(rows) + ");\n" + extra + r"""
console.log(JSON.stringify(result));
""")
    run = subprocess.run([_NODE], input=script, capture_output=True, text=True,
                         encoding="utf-8", timeout=30, check=False)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)
