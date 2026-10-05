"""Renderer units run in Node without a browser/network dependency."""

import base64
import hashlib
import json
import subprocess
from pathlib import Path


def test_links_charts_fences_and_file_chip():
    source = Path("tinyassets/onboarding/chat_render.js").read_text(encoding="utf-8")
    harness = r"""
const assert=require('node:assert/strict');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.attrs={};this.events={};this._text='';}
  appendChild(child){this.children.push(child);child.parentNode=this;return child;}
  set textContent(v){this.children=[];this._text=String(v);}
  get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}
  setAttribute(k,v){this.attrs[k]=v;}
  addEventListener(k,v){this.events[k]=v;}
  replaceWith(v){const p=this.parentNode;p.children[p.children.indexOf(this)]=v;v.parentNode=p;}
}
const document={currentScript:{nonce:'test'},createElement:t=>new Element(t),
createElementNS:(_,t)=>new Element(t),
createTextNode:t=>{const n=new Element('#text');n.textContent=t;return n;}};
"""
    checks = r"""
const root=new Element('div');
ChatRender.render(root,'[site](https://example.org/a?x=1&y=2) http://example.org. '+
'[bad](javascript:alert(1)) `<img onerror=x>`');
const links=root.children.filter(n=>n.tag==='a');
assert.equal(links.length,2);assert.equal(links[0].href,'https://example.org/a?x=1&y=2');
assert.equal(links[0].target,'_blank');assert.equal(links[0].rel,'noopener noreferrer');
assert.equal(links[1].href,'http://example.org/');
assert(root.textContent.includes('[bad](javascript:alert(1))'));
for(const bad of ['javascript:alert(1)','data:text/html,x','file:///x','//example.org','https://x\n.test'])assert.equal(ChatRender.httpURL(bad),null);
for(const type of ['bar','line','stacked']){
const svg=ChatRender.chart(JSON.stringify({type,labels:['A','B'],
series:[{name:'One',values:[-2,4]},{name:'Two',values:[1,2]}]}));
assert.equal(svg.tag,'svg');assert(svg.textContent.includes('One: A = -2'));
}
ChatRender.render(root,'```chart\n{"type":"bar","labels":["A"],"series":[{"name":"X","values":["bad"]}]}\n```\n```js\nhttps://example.org\n```');
assert.equal(root.children.filter(n=>n.tag==='pre').length,2);assert.equal(root.children.filter(n=>n.tag==='a').length,0);
ChatRender.render(root,'```mermaid\n%%{init: {}}%%\ngraph TD; A-->B\n```');
assert(root.children.some(n=>n.tag==='pre'));
let file;
ChatRender.render(root,'```file\n{"path":"exports/a.csv"}\n```',async(path,name)=>{file=[path,name];});
const chip=root.children.find(n=>n.tag==='button');
(async()=>{await chip.events.click();assert.deepEqual(file,['exports/a.csv','a.csv']);
ChatRender.render(root,'```file\n{"path":"../secret"}\n```',()=>{});assert(root.children.some(n=>n.tag==='pre'));
console.log(JSON.stringify({ok:true}));})();
"""
    result = subprocess.run(
        ["node", "-e", harness + source + checks], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) == {"ok": True}


def test_desktop_hands_only_http_s_to_system_browser():
    result = subprocess.run(
        [
            "node",
            "-e",
            """
const {isSafeExternal}=require('./desktop-app/config.js');
console.log(JSON.stringify(['https://example.org','http://example.org','javascript:alert(1)','file:///x','data:x'].map(isSafeExternal)));
""",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == [True, True, False, False, False]


def test_vendored_mermaid_matches_renderer_integrity():
    root = Path("tinyassets/onboarding")
    pin = "sha384-" + base64.b64encode(
        hashlib.sha384((root / "app/mermaid_vendor.js").read_bytes()).digest()
    ).decode()
    assert pin in (root / "chat_render.js").read_text(encoding="utf-8")
    assert "MIT" in (root / "app/LICENSE-mermaid.txt").read_text(encoding="utf-8")
