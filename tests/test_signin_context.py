"""Sign-in navigation and callback transport preserve the initiating surface."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.datastructures import QueryParams

from tests.test_inline_owner_sessions import setup  # noqa: F401
from tinyassets.onboarding import inline_model_connect as inline
from tinyassets.onboarding import owner_sessions

pytestmark = pytest.mark.verifies('owner-daemon-in-box:T2')


@pytest.mark.parametrize("client", ["web", "ios", "android", "android-debug", "desktop"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_model_callback_returns_to_originating_surface(
        setup, monkeypatch, client, cancelled):  # noqa: F811
    handle = "a" * 43
    inline.stage(owner="alice", result={"flow": handle, "expires_in": 300,
                 "authorize_url": "https://provider.example/authorize"}, verifier="v" * 43,
                 owner_session={"identity_json": json.dumps({"user_id": "alice"})},
                 exchange="exchange", client=client)
    request = SimpleNamespace(url=SimpleNamespace(path="/app/model-callback/" + handle),
                              query_params=QueryParams("launch=1"),
                              cookies={owner_sessions.COOKIE: "protected"},
                              headers={"sec-fetch-dest": "document"})
    launch = inline.callback(request)
    assert launch.headers["location"] == "https://provider.example/authorize"
    from http.cookies import SimpleCookie
    cookie = SimpleCookie(launch.headers["set-cookie"])
    request.cookies.update({key: value.value for key, value in cookie.items()})
    request.query_params = QueryParams("error=access_denied" if cancelled else "code=provider-code")
    monkeypatch.setattr(inline, "discard_hosted", lambda *args: None)
    response = inline.callback(request)
    if client == "web":
        assert response.status_code == 303
        assert response.headers["location"] == "/app"
    else:
        from tinyassets.onboarding.approval_handoff import completion_url
        assert completion_url(handle, client).encode() in response.body
        assert b"location.replace(" in response.body
        assert "script-src 'nonce-" in response.headers["content-security-policy"]
    assert b"provider-code" not in response.body
    state = inline.take(owner="alice", handle=handle)
    assert state["status"] == ("cancelled" if cancelled else "ready")


def test_surface_launch_helpers(tmp_path):
    source = Path("tinyassets/onboarding/app/main.js").read_text("utf-8")
    helpers = source[source.index("  async function openExternal("):
                     source.index("  async function closeExternal(")]
    script = tmp_path / "signin.cjs"
    script.write_text("""
const assert=require('node:assert/strict');
let NATIVE=false,DESKTOP_SHELL=false,platform='android',install='io.tinyassets.app';
let calls=[];
const location={origin:'https://app.example'};
const plugins={App:{getInfo:async()=>({id:install})},
 Browser:{open:async options=>calls.push(['sheet',options.url])},
 SystemAuth:{open:async options=>{calls.push(['auth',options.url]);return {url:'tinyassets://auth?signin=return'};}}};
const nativePlugin=name=>plugins[name];
const window={location:{assign:url=>calls.push(['tab',url])},
 Capacitor:{getPlatform:()=>platform},
 tinyassetsDesktop:{openExternal:async url=>calls.push(['desktop',url])},
 open:()=>{throw Error('Unexpected popup');}};
async function handleAppReturn(url){calls.push(['return',url]);return true;}
""" + helpers + """
(async()=>{
 assert.equal(await signInClient(),'web');
 await openSignIn('/authorize');assert.deepEqual(calls.pop(),['tab','https://app.example/authorize']);
 DESKTOP_SHELL=true;assert.equal(await signInClient(),'desktop');
 await openSignIn('/authorize');assert.equal(calls.pop()[0],'desktop');
 DESKTOP_SHELL=false;NATIVE=true;
 for(const os of ['android','ios']){
  platform=os;assert.equal(await signInClient(),os);
  await openSignIn('/authorize');assert.equal(calls.pop()[0],'sheet');
 }
 await openSignIn('/authorize',{appLogin:true});
 assert.equal(calls.pop()[0],'return');assert.equal(calls.pop()[0],'auth');
 platform='android';install='io.tinyassets.app.debug';
 assert.equal(await signInClient(),'android-debug');
 for(const url of ['javascript:alert(1)','http://provider.example','https://user:pass@provider.example'])
  await assert.rejects(openSignIn(url),/secure sign-in/);
 assert.equal(calls.length,0);
})().catch(error=>{console.error(error);process.exit(1);});
""", encoding="utf-8")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_desktop_scheme_returns_wait_for_app_frame(tmp_path):
    source = Path("desktop-app/src/main.js").read_text("utf-8")
    transport = source[source.index("function isApprovalReturn("):
                       source.index("ipcMain.handle('tinyassets:open-external'")]
    script = tmp_path / "desktop-return.cjs"
    script.write_text("""
const assert=require('node:assert/strict');
const APP_URL='https://tinyassets.io/app';
const sent=[];
const frame={url:APP_URL};
const contents={mainFrame:frame,send:(channel,url)=>sent.push([channel,url])};
const mainWindow={webContents:contents};
let focused=0;
function focusApp(){focused++;}
""" + transport + """
const url='tinyassets-desktop://auth?completion='+'a'.repeat(43);
receiveReturn(url);assert.equal(focused,1);assert.equal(sent.length,0);
returnReceiver={sender:contents,senderFrame:frame};
deliverReturn();assert.deepEqual(sent,[['tinyassets:app-return',url]]);
deliverReturn();assert.equal(sent.length,1);
receiveReturn('tinyassets://auth?completion='+'a'.repeat(43));
receiveReturn(url+'&extra=1');assert.equal(sent.length,1);
frame.url='https://other.example/app';receiveReturn(url);assert.equal(sent.length,1);
frame.url=APP_URL;deliverReturn();assert.equal(sent.length,2);
""", encoding="utf-8")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
