"""Execute app and shell return boundaries, including browser launch failures."""

import json
from pathlib import Path

import pytest

from tests.test_android_app_identity import _SHIM, _app_html, _js_function, _run_node


@pytest.mark.parametrize("scheme", ["tinyassets", "tinyassets-desktop"])
def test_return_requires_exact_pending_ref_and_carries_secret(tmp_path, scheme):
    result = _run_node(
        tmp_path,
        _SHIM
        + """
const NATIVE=true, DESKTOP_SHELL=false;
async function closeExternal(){out.closed=(out.closed||0)+1;}
async function resumeNativeSignIn(){out.redeemed=JSON.parse(store.pkce);}
function token(){return null;}
"""
        + _js_function(_app_html(), "handleAppReturn")
        + """
(async()=>{
  store.pkce=JSON.stringify({native_ref:'r'.repeat(43),verifier:'v'.repeat(64)});
  const base=SCHEME+'://auth?signin=';
  out.foreign=await handleAppReturn(base+'x'.repeat(43)+'&return_secret='+'s'.repeat(43));
  out.missing=await handleAppReturn(base+'r'.repeat(43));
  out.before=out.closed||0;
  out.valid=await handleAppReturn(base+'r'.repeat(43)+'&return_secret='+'s'.repeat(43));
  console.log(JSON.stringify(out));
})();
""".replace("SCHEME", json.dumps(scheme)),
    )
    assert result["foreign"] is False and result["missing"] is False
    assert result["before"] == 0 and result["valid"] is True
    assert result["redeemed"]["return_secret"] == "s" * 43


def test_focus_alone_never_redeems_native_flow(tmp_path):
    result = _run_node(
        tmp_path,
        _SHIM
        + """
const NATIVE=true, DESKTOP_SHELL=false;
let nativeSignInBusy=false,nativeSignInTimer=null;
async function withRefreshLock(fn){return fn();}
async function fetch(){out.polled=true;return {status:202,json:async()=>({})};}
function setTimeout(){return 1;}
"""
        + _js_function(_app_html(), "resumeNativeSignIn")
        + """
(async()=>{
 store.pkce=JSON.stringify({native_ref:'r'.repeat(43),verifier:'v'.repeat(64),expires:Date.now()+600000});
 await resumeNativeSignIn();console.log(JSON.stringify(out));
})();
""",
    )
    assert "polled" not in result


def test_desktop_browser_failure_rejects_to_caller(tmp_path):
    source = Path("desktop-app/src/main.js").read_text(encoding="utf-8")
    result = _run_node(
        tmp_path,
        """
const out={};function isSafeExternal(){return true;}function noteHandedOff(){}
const shell={openExternal:async()=>{throw new Error('no browser handler');}};
"""
        + _js_function(source, "openExternalIfSafe")
        + """
(async()=>{try{await openExternalIfSafe('https://id.example');out.success=true;}
catch(e){out.error=e.message;}console.log(JSON.stringify(out));})();
""",
    )
    assert "success" not in result
    assert "browser" in result["error"]


def test_ios_passes_callback_url_to_return_handler(tmp_path):
    source = (
        _SHIM
        + """
const NATIVE=true,DESKTOP_SHELL=false;
window.Capacitor={getPlatform:()=> 'ios'};
const callback='tinyassets://auth?signin='+'r'.repeat(43)+'&return_secret='+'s'.repeat(43);
function nativePlugin(name){return name==='App'?{getInfo:async()=>({id:'io.tinyassets.app'})}:
 {open:async()=>({url:callback})};}
async function fetch(){return {ok:true,json:async()=>({ref:'r'.repeat(43),url:'https://id.example'})};}
async function handleAppReturn(url){out.returned=url;return true;}
"""
        + _js_function(_app_html(), "beginSignIn")
        + """
(async()=>{await beginSignIn();console.log(JSON.stringify(out));})();
"""
    )
    result = _run_node(tmp_path, source)
    assert result["returned"].endswith("return_secret=" + "s" * 43)
    swift = Path(
        "mobile/native/system-auth/ios/Sources/SystemAuthPlugin/SystemAuthPlugin.swift"
    ).read_text()
    assert 'call.resolve(["url": callbackURL.absoluteString])' in swift


_LEGACY = """
const NATIVE=true,DESKTOP_SHELL=false;let nativeSignInBusy=false;
async function closeExternal(){}async function enterSignedIn(){out.signedIn=(out.signedIn||0)+1;}
function token(){return out.token||null;}
async function withRefreshLock(fn){return fn();}
function storeAccessToken(t){out.token=t;}function storeSessionRef(r){out.ref=r;}
out.bodies=[];let respond=async()=>({ok:true,status:200,
  json:async()=>({access_token:'legacy-token',expires_in:300,session_ref:'h'.repeat(43)})});
async function fetch(url,init){out.bodies.push([url,JSON.parse(init.body)]);return respond();}
"""


def _legacy_program(body):
    # The production exchange, never a stub: strip the shared shim's fake.
    fake = "async function finishExchange(){ out.exchanged = true; return true; }"
    shim = _SHIM.replace(fake, "")
    assert "finishExchange" not in shim
    html = _app_html()
    return (
        shim
        + _LEGACY
        + "".join(_js_function(html, name) for name in ("handleAppReturn", "finishLegacyExchange"))
        + "(async()=>{"
        + body
        + "console.log(JSON.stringify(out));})();"
    )


def test_legacy_return_requires_exact_saved_state_and_rollout_window(tmp_path):
    result = _run_node(
        tmp_path,
        _legacy_program(
            """
 Date.now=()=>1791676800000;
 store.pkce=JSON.stringify({state:'app.'+'r'.repeat(24),verifier:'v'.repeat(64)});
 const base='tinyassets://auth?code=legacy&state=app.';
 out.wrong=await handleAppReturn(base+'w'.repeat(24));
 out.extra=await handleAppReturn(base+'r'.repeat(24)+'&x=1');
 out.calls=out.bodies.length;
 out.valid=await handleAppReturn(base+'r'.repeat(24));
 out.left=store.pkce===undefined;
 store.pkce=JSON.stringify({state:'app.'+'r'.repeat(24),verifier:'v'.repeat(64)});
 Date.now=()=>1792800000000;
 out.expired=await handleAppReturn(base+'r'.repeat(24));
"""
        ),
    )
    assert result["wrong"] is False and result["extra"] is False and result["calls"] == 0
    assert result["expired"] is False and result["signedIn"] == 1
    assert result["valid"] is True and result["token"] == "legacy-token" and result["left"]
    assert result["bodies"] == [
        [
            "/app/token",
            {"code": "legacy", "code_verifier": "v" * 64, "redirect_uri": "https://tinyassets.io/app"},
        ]
    ]


def test_legacy_return_never_installs_a_replaced_or_failed_flow(tmp_path):
    result = _run_node(
        tmp_path,
        _legacy_program(
            """
 Date.now=()=>1791676800000;
 const old=JSON.stringify({state:'app.'+'r'.repeat(24),verifier:'v'.repeat(64)});
 const url='tinyassets://auth?code=legacy&state=app.'+'r'.repeat(24);
 store.pkce=old;
 respond=async()=>{store.pkce=JSON.stringify({verifier:'n'.repeat(64),native_ref:'n'.repeat(43)});
   return {ok:true,status:200,json:async()=>({access_token:'late-token'})};};
 out.replaced=await handleAppReturn(url);
 out.kept=JSON.parse(store.pkce).native_ref;
 store.pkce=old;
 respond=async()=>({ok:false,status:502,json:async()=>({error:'token_endpoint_unreachable'})});
 out.failed=await handleAppReturn(url);out.cleared=store.pkce===undefined;
 store.pkce=old;let release;
 respond=()=>new Promise(r=>release=()=>r({ok:true,status:200,
   json:async()=>({access_token:'one'})}));
 const first=handleAppReturn(url);out.duplicate=await handleAppReturn(url);
 release();out.first=await first;out.notice=notice.textContent;
"""
        ),
    )
    assert result["replaced"] is False and result["kept"] == "n" * 43
    assert result["failed"] is False and result["cleared"]
    assert result["duplicate"] is False and result["first"] is True
    assert result["token"] == "one" and result["signedIn"] == 1
    assert "could not finish" in result["notice"]


def test_desktop_return_bridge_bounds_origin_frame_and_protocol(tmp_path):
    source = Path("desktop-app/src/main.js").read_text(encoding="utf-8")
    result = _run_node(
        tmp_path,
        """
const out={};const APP_URL='https://tinyassets.io/app';
const mainWindow={webContents:{mainFrame:{url:APP_URL}}};
const frame=mainWindow.webContents.mainFrame;
"""
        + _js_function(source, "isAppFrame")
        + _js_function(source, "isApprovalReturn")
        + """
const good='tinyassets-desktop://auth?signin='+'r'.repeat(43)+'&return_secret='+'s'.repeat(43);
out.valid=isApprovalReturn(good);
out.bad=[good+'&extra=1',good+'#fragment',good.replace('auth?','auth/path?'),
 good.replace('tinyassets-desktop:','https:'),good.split('&')[0]].map(isApprovalReturn);
out.app=isAppFrame({sender:mainWindow.webContents,senderFrame:frame});
out.iframe=isAppFrame({sender:mainWindow.webContents,senderFrame:{url:APP_URL}});
frame.url='https://tinyassets.io.evil/app';
out.foreign=isAppFrame({sender:mainWindow.webContents,senderFrame:frame});
console.log(JSON.stringify(out));
""",
    )
    assert result["valid"] and not any(result["bad"])
    assert result["app"] and not result["iframe"] and not result["foreign"]
