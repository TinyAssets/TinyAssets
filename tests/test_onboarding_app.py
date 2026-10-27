"""Tests for the daemon-served onboarding app (tinyassets/onboarding).

Unit-level: exercises the dark flag, the route handler, config injection, the
per-request CSP nonce, and secret-safety. Final onboarding acceptance is a real
user against the DEPLOYED cloud daemon (tinyassets.io) — never a local run.
"""

from __future__ import annotations

import time

import pytest

from tinyassets import onboarding


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("TINYASSETS_ONBOARDING_APP", raising=False)
    monkeypatch.delenv("TINYASSETS_ONBOARDING_APP_CLIENT_ID", raising=False)
    yield


def _fake_prm(resource="https://tinyassets.io/mcp",
              issuer="https://inventive-van-62-staging.authkit.app"):
    return {
        "resource": resource,
        "authorization_servers": [issuer] if issuer else [],
        "scopes_supported": ["openid", "profile", "email", "offline_access"],
    }


def _render(monkeypatch, *, enabled=True, client_id="client_ABC", prm=None):
    if enabled:
        monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    if client_id is not None:
        monkeypatch.setenv("TINYASSETS_ONBOARDING_APP_CLIENT_ID", client_id)
    monkeypatch.setattr(
        "tinyassets.auth.wellknown.protected_resource_metadata",
        lambda: prm or _fake_prm(),
    )


# --------------------------------------------------------------------------- #
# dark flag
# --------------------------------------------------------------------------- #










# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #








# --------------------------------------------------------------------------- #
# CSP + nonce + injection safety
# --------------------------------------------------------------------------- #














# --------------------------------------------------------------------------- #
# route
# --------------------------------------------------------------------------- #


def test_route_is_apex_app_get(monkeypatch):
    routes = onboarding.onboarding_routes()
    by_path = {r.path: r for r in routes}
    # The SPA page (GET) + its same-origin PKCE token-exchange proxy (POST) +
    # the one-tap OpenAI device-auth broker (POST only, identity-gated) + the
    # fixed, unauthenticated bundle host a custom UI runs inside (GET).
    assert set(by_path) == {
        "/app", "/app/token", "/app/me", "/app/apps", "/app/ui-frame", "/app/native-sign-in",
        "/app/run/{listing}", "/app/run/{listing}/preview.png", "/app/unread",
        "/app/owner-sign-in", "/app/owner-sign-in/complete", "/app/approvals/{operation}",
        "/app/approval-handoff", "/app/approval-handoff/{ref}",
        # K1: owner-session-only grant/revoke control for outside clients.
        "/app/outside-clients", "/app/api-keys",
        "/app/model-connect/{operation}", "/app/subscription-connect/{operation}",
        "/app/model-discovery", "/app/connection-discovery",
        "/app/model-callback/{flow}",
        # The public OAuth client metadata document a sign-in source names.
        "/app/oauth/client-metadata.json",

        "/app/trace",
        "/app/session/realtime",
        "/app/serving/bind", "/app/models/preferences",
        "/app/billing/status", "/app/billing/checkout",
        "/app/billing/cancel", "/app/billing/webhook",
        "/app/account/delete", "/app/account/deletion/{key}/{token}",
        "/app/owner/restore", "/app/owner/checkpoint", "/app/owner/restore-points",
        "/app/account/timezone", "/app/ui-prefs",
        "/app/api/policy", "/app/profile", "/app/memory", "/app/soul", "/app/wakes",
        # S30: the owner's outside agents and their dm rooms (router-served).
        "/app/api/outside-agents", "/app/api/agent-messages",
        "/app/turn/interrupt", "/app/turn/pending",
        # The activities live projection (harness D2a).
        "/app/live",
        "/app/connections", "/app/files",
        "/app/devices", "/app/notify", "/app/sw.js",
        # The app's own ES modules (app_modules.py), static and allowlisted.
        "/app/m/{build}/{name}",
        # The owner door: every read the app renders, complete.
        "/app/api/read", "/app/api/status",
        # The bytes a custom UI loads, fetched by the app for its sealed frame.
        "/app/api/ui-asset",
        "/app/api/file",
        # One published app in its owner's box: document, room, and publication.
        "/app/{slug}", "/app/{slug}/state", "/app/{slug}/publish",
    }
    assert by_path["/app/outside-clients"].methods == {"POST"}
    assert by_path["/app/wakes"].methods == {"GET", "HEAD", "POST"}
    assert "/app/browser-login" not in by_path
    assert by_path["/app/files"].methods == {"POST"}
    assert by_path["/app/api/read"].methods == {"POST"}
    assert by_path["/app/api/ui-asset"].methods == {"POST"}
    # The owner's clock is a WRITE from their client, never a readable setting.
    assert by_path["/app/account/timezone"].methods == {"POST"}
    assert "GET" in by_path["/app"].methods
    # The bundle host is read-only and takes no input: it carries no user content,
    # which is why it needs no authentication (tinyassets/onboarding/ui_frame.py).
    assert by_path["/app/ui-frame"].methods == {"GET", "HEAD"}
    assert "GET" in by_path["/app/billing/status"].methods
    assert "GET" in by_path["/app/me"].methods
    assert by_path["/app/profile"].methods == {"GET", "HEAD"}
    assert {"GET", "POST"} <= by_path["/app/session/realtime"].methods
    assert {"GET", "HEAD", "POST"} <= by_path["/app/{slug}"].methods
    assert {"GET", "POST"} <= by_path["/app/{slug}/state"].methods
    assert by_path["/app/{slug}/publish"].methods == {"POST"}
    assert {"GET", "POST"} <= by_path["/app/models/preferences"].methods
    assert by_path["/app/connections"].methods == {"GET", "HEAD", "POST"}
    for path in ("/app/api/policy", "/app/memory"):
        assert by_path[path].methods == {"GET", "HEAD", "POST"}
    for post_only in (
        "/app/token",
        "/app/trace",
        "/app/serving/bind",
        "/app/billing/checkout", "/app/billing/cancel",
        "/app/billing/webhook", "/app/account/delete",
        "/app/turn/interrupt", "/app/turn/pending",
        "/app/live",
    ):
        assert "POST" in by_path[post_only].methods
        assert "GET" not in by_path[post_only].methods


def test_no_route_is_mounted_under_the_retired_mcp_app_prefix():
    """The 2026-09-30 move is clean: `/mcp/app` is not mounted, aliased or
    redirected. Nothing under the old prefix exists to serve.

    Founder directive: no back-compat. A route left behind — even a redirect —
    would be the back-compat the move was meant to avoid.
    """
    paths = {r.path for r in onboarding.onboarding_routes()}
    assert not [p for p in paths if p.startswith("/mcp")], sorted(paths)
    assert all(p == "/app" or p.startswith("/app/") for p in paths), sorted(paths)


def test_the_app_path_constant_is_the_single_source_of_truth():
    """Route table, refresh-cookie scope and redirect-URI check must agree.

    They disagreed once in spirit already: the cookie path was a separate
    literal. One constant is what makes "the app lives at /app" checkable.
    """
    assert onboarding.APP_PATH == "/app"
    assert onboarding._REFRESH_COOKIE_PATH == "/app/token"
    paths = {r.path for r in onboarding.onboarding_routes()}
    assert onboarding.APP_PATH in paths
    assert onboarding._REFRESH_COOKIE_PATH in paths




























def _js_function(html: str, name: str) -> str:
    """Source of ``function NAME(`` / ``async function NAME(`` from the app's
    script, by brace matching that skips strings and comments."""
    import re

    # A literal prefix lets the regex engine skip directly to "function".
    # An optional async prefix makes it retry at every character of this large page.
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\(", html)
    assert m, f"app.html has no function {name}"
    start = m.start()
    prefix = html[:start].rstrip()
    if len(prefix) < start and prefix.endswith("async"):
        start = len(prefix) - len("async")
    i = html.index("{", m.end())
    depth, j, n = 0, i, len(html)
    while j < n:
        c = html[j]
        if c in "\"'`":
            j += 1
            while j < n and html[j] != c:
                if html[j] == "\\":
                    j += 1
                j += 1
        elif html.startswith("//", j):
            j = html.index("\n", j)
        elif html.startswith("/*", j):
            j = html.index("*/", j) + 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return html[start: j + 1]
        j += 1
    raise AssertionError(f"unbalanced braces in {name}")
    # No regex-literal or nested-template lexing (Codex round 2, P2): the
    # functions extracted today contain neither, and a mis-cut span is a
    # syntax error that crashes the node harness - loud, never a silent pass.


def _run_voice_states(tmp_path, events: list[str]) -> list[str]:
    """Run the shipped transition table, rather than copying it into Python."""
    import json
    import os
    import re
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:  # pragma: no cover - environment dependent
        if os.environ.get("TINYASSETS_SKIP_JS_PROBE_TESTS"):
            pytest.skip("node absent; skip explicitly requested via env")
        pytest.fail("node executable not found - voice transitions are JavaScript")
    html, _csp = onboarding.render_app_html()
    table = re.search(r"const VOICE_TRANSITIONS=\{.*?\n  \};", html, re.DOTALL)
    assert table, "app.html has no voice transition table"
    program = "\n".join(
        (
            table.group(0),
            _js_function(html, "voiceNextState"),
            f"const events={json.dumps(events)};",
            'let state="idle"; const seen=[state];',
            "for(const event of events){state=voiceNextState(state,event);seen.push(state);}",
            "console.log(JSON.stringify(seen));",
        )
    )
    script = tmp_path / "voice_states.js"
    script.write_text(program, encoding="utf-8")
    proc = subprocess.run(
        [node, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert proc.returncode == 0, f"voice state harness crashed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def _run_voice_adapter(tmp_path) -> dict:
    """Drive the shipped Voice object with fake media/Realtime boundaries."""
    import json
    import os
    import re
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:  # pragma: no cover - environment dependent
        if os.environ.get("TINYASSETS_SKIP_JS_PROBE_TESTS"):
            pytest.skip("node absent; skip explicitly requested via env")
        pytest.fail("node executable not found - voice adapter is JavaScript")
    html, _csp = onboarding.render_app_html()
    table = re.search(r"const VOICE_(?:LABELS|TRANSITIONS)=\{.*?\n  \};", html, re.DOTALL)
    assert table
    # LABELS precedes TRANSITIONS; collect both independently.
    labels = re.search(r"const VOICE_LABELS=\{.*?\n  \};", html, re.DOTALL)
    transitions = re.search(r"const VOICE_TRANSITIONS=\{.*?\n  \};", html, re.DOTALL)
    voice = re.search(r"const Voice=\{.*?\n  \};", html, re.DOTALL)
    assert labels and transitions and voice
    functions = "\n".join(
        _js_function(html, name)
        for name in (
                "voiceNextState",
                "voiceNormalize",
                "voiceRecognitionKey",
                "browserSpeechConstructor",
            "browserSpeechAvailable",
            "browserSpeechCapability",
            "voiceDisclosureKey",
            "voiceFriendlyError",
            "voiceConnectionGuidance",
        )
    )
    shim = r"""
const CFG={voice:{enabled:true,disclosure_version:1,max_session_seconds:1800}};
const store={}; const localStorage={getItem:k=>store[k]||null,setItem:(k,v)=>store[k]=String(v)};
const timers=[]; function setTimeout(fn,ms){const timer={fn,ms};timers.push(timer);return timer;}
const intervals=[];
function setInterval(fn,ms){const timer={fn,ms};intervals.push(timer);return timer;}
function clearTimeout(){} function clearInterval(){}
class El{constructor(){this.hidden=true;this.disabled=false;this._textContent="";this.attrs={};
this.children=[];this.value="";}
set textContent(value){this._textContent=String(value);if(value==="")this.children=[];}
get textContent(){return this._textContent;}
appendChild(child){this.children.push(child);return child;}
setAttribute(k,v){this.attrs[k]=v;} removeAttribute(k){delete this.attrs[k];}
focus(){this.focused=true;} pause(){this.paused=true;}}
const els={"btn-voice":new El(),"btn-send":new El(),
  "voice-disclosure":new El(),"btn-voice-accept":new El(),
  "voice-service-name":new El(),"voice-privacy-link":new El(),
  "voice-disclosure-browser":new El(),"voice-disclosure-bridge":new El(),
  "voice-output-select":new El(),"voice-status-line":new El()};
const $=id=>els[id]; let status="",conversationStatus="",turnStartedAt=0;
function setStatusLine(v){conversationStatus=v||"";}
function setVoiceStatusLine(v){status=v||"";els["voice-status-line"].textContent=status;}
const document={createElement:()=>new El(),documentElement:{lang:"en-US"}};
const window={};
let mediaRequests=0;
const navigator={mediaDevices:{getUserMedia:async()=>{mediaRequests++;return {getTracks:()=>[]};}}};
let RTCPeerConnection;
let traces=[]; function trace(...args){traces.push(args);}
let turns=[];
let capabilityDoc={available:false,state:"unpowered",reason:"provider_not_configured",
  remediation:"existing_connection_surface"};
let fetched=[],fetchError=null;
async function fetch(url){
  fetched.push(url);if(fetchError)throw fetchError;
  return {ok:true,status:200,json:async()=>capabilityDoc};
}
let voiceTurnImpl=async()=>"Exact universe reply.";
async function sendVoiceTurn(message){turns.push(message);return await voiceTurnImpl(message);}
async function ensureFreshToken(){} async function refreshAccessToken(){return false;}
function authHeaders(){return {Authorization:"Bearer app"};} async function sleep(){}
let connectCalls=[]; function openConnectRequest(guidance){connectCalls.push({guidance});}
"""
    scenario = r"""
(async()=>{
  const out={}; Voice.init(); await Voice.refreshCapability();
  await Voice.requestStart();
  out.unpowered={state:Voice.state,label:els["btn-voice"].textContent,
    disclosureShown:!els["voice-disclosure"].hidden,
    mediaRequests,fetched:fetched.slice(),status,connectCalls:connectCalls.slice()};
  capabilityDoc={available:false,state:"incompatible",reason:"capability_not_declared",
    remediation:"existing_connection_surface"};connectCalls=[];
  await Voice.refreshCapability();await Voice.requestStart();
  out.remediableIncompatible={state:Voice.state,disabled:els["btn-voice"].disabled,
    mediaRequests,connectCalls:connectCalls.slice(),status};
  capabilityDoc={available:false,state:"unpowered",reason:"no_home_universe",
    remediation:"none"};connectCalls=[];
  await Voice.refreshCapability();await Voice.requestStart();
  out.unremediableIncompatible={state:Voice.state,disabled:els["btn-voice"].disabled,
    mediaRequests,connectCalls:connectCalls.slice(),status};
  capabilityDoc={available:false,state:"incompatible",reason:"provider_voice_unsupported",
    remediation:"existing_connection_surface"};connectCalls=[];
  CFG.voice.enabled=true;Voice.init();await Voice.refreshCapability();await Voice.requestStart();
  out.unsupportedWithTransportEnabled={state:Voice.state,
    disabled:els["btn-voice"].disabled,mediaRequests,
    connectCalls:connectCalls.slice(),status};
  CFG.voice.enabled=false;
  capabilityDoc={available:false,state:"disabled",reason:"voice_disabled",
    remediation:"none"};connectCalls=[];
  Voice.init();await Voice.refreshCapability();await Voice.requestStart();
  out.transportDisabled={state:Voice.state,
    disabled:els["btn-voice"].disabled,mediaRequests,
    connectCalls:connectCalls.slice(),status,
    disclosureShown:!els["voice-disclosure"].hidden};
  CFG.voice.enabled=true;
  capabilityDoc={available:true,state:"ready",resource:"user_bound_voice_connection",
    remediation:"none",
    disclosure_id:"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    service_name:"My bridge",privacy_url:"https://bridge.example/privacy"};connectCalls=[];
  Voice.init();await Voice.refreshCapability();await Voice.requestStart();
  out.readyWithoutLegacyFlags={state:Voice.state,
    disabled:els["btn-voice"].disabled,mediaRequests,
    connectCalls:connectCalls.slice(),status,
    disclosureShown:!els["voice-disclosure"].hidden};
  const realEnsureFreshToken=ensureFreshToken,fetchesBeforeDeadline=fetched.length;
  ensureFreshToken=()=>new Promise(()=>{});
  const deadlineResult=Voice._readCapability(5000).then(()=>"resolved",error=>error.message);
  timers[timers.length-1].fn();
  out.authorityDeadline={result:await deadlineResult,
    fetches:fetched.length-fetchesBeforeDeadline};
  ensureFreshToken=realEnsureFreshToken;
  capabilityDoc={available:false,state:"unpowered",reason:"provider_not_configured",
    remediation:"existing_connection_surface"};connectCalls=[];
  Voice.capability=null;Voice.state="checking";Voice._render();
  await Voice.requestStart();
  out.checkingRetry={state:Voice.state,disabled:els["btn-voice"].disabled,
    mediaRequests,connectCalls:connectCalls.slice()};
  capabilityDoc={available:true,state:"ready",resource:"user_bound_voice_connection",
    remediation:"none",
    disclosure_id:"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    service_name:"My bridge",privacy_url:"https://bridge.example/privacy"};
  await Voice.refreshCapability(); out.initial=Voice.state;
  await Voice.requestStart(); out.disclosureShown=!els["voice-disclosure"].hidden;
  const realStart=Voice.start;Voice.start=()=>{out.disclosureStarted=true;};
  capabilityDoc=Object.assign({},capabilityDoc,
    {disclosure_id:"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
     service_name:"Changed bridge"});
  await Voice.acceptDisclosure();
  out.changedDuringDisclosure={started:!!out.disclosureStarted,state:Voice.state,
    disclosureHidden:els["voice-disclosure"].hidden,accepted:Voice._accepted(),mediaRequests,status};
  await Voice.requestStart();out.freshDisclosureShown=!els["voice-disclosure"].hidden;
  await Voice.acceptDisclosure();
  out.acceptedFirst=Voice._accepted();
  Voice.capability=Object.assign({},capabilityDoc,
    {disclosure_id:"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"});
  out.acceptedAfterRebind=Voice._accepted(); Voice.capability=capabilityDoc;
  const sent=[];
  Voice.dc={readyState:"open",send:v=>sent.push(JSON.parse(v)),
    close:()=>{out.dcClosed=true;}};
  Voice.audio={muted:true};
  await Voice.handleToolCall({type:"tool_call",
    call_id:"c1",name:"converse",arguments:'{"message":" hello "}'});
  await Voice.handleToolCall({type:"tool_call",
    call_id:"c1",name:"converse",arguments:'{"message":" duplicate "}'});
  Voice.handleServerEvent({type:"audio_started"});
  out.activeButton={disabled:els["btn-voice"].disabled,
    label:els["btn-voice"].textContent,
    ariaPressed:els["btn-voice"].attrs["aria-pressed"]};
  Voice.handleServerEvent({type:"speech_started"});
  Voice.handleServerEvent({type:"output_transcript",transcript:"Exact"});
  out.afterBargeIn=Voice.state; out.mutedAfterBargeIn=Voice.audio.muted;
  out.bargeInInterrupted=Voice.canonicalResponseInterrupted;
  out.turns=turns.slice(); out.toolEvents=sent.slice();
  let stopped=0,pcClosed=0,audioPaused=0;
  Voice.stream={getTracks:()=>[{stop:()=>stopped++}]}; Voice.pc={close:()=>pcClosed++};
  Voice.audio={pause:()=>audioPaused++,srcObject:{}}; Voice.stop(false);
  out.teardown={stopped,pcClosed,audioPaused,state:Voice.state};
  let revokedStopped=0,revokedClosed=0;
  Voice.capability=capabilityDoc;Voice.epoch=50;Voice.state="listening";
  Voice.stream={getTracks:()=>[{stop:()=>revokedStopped++}]};
  Voice.pc={close:()=>revokedClosed++};Voice.audio={pause:()=>{},srcObject:{}};
  capabilityDoc={available:false,state:"incompatible",reason:"capability_not_declared",
    remediation:"existing_connection_surface"};
  await Voice._verifyAuthority(50);
  out.authorityRevocation={stopped:revokedStopped,closed:revokedClosed,state:Voice.state,status};
  capabilityDoc={available:true,state:"ready",resource:"user_bound_voice_connection",
    remediation:"none",disclosure_id:"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    service_name:"My bridge",privacy_url:"https://bridge.example/privacy"};
  Voice.capability=capabilityDoc;
  Voice._armSessionLimit(9999);
  out.sessionLimitDelays=timers.slice(-2).map(timer=>timer.ms);
  let resolveStale;
  voiceTurnImpl=()=>new Promise(resolve=>{resolveStale=resolve;});
  Voice.epoch=20; Voice.state="listening";
  const oldChannel={readyState:"open",send:()=>{},close:()=>{}};
  const freshSent=[]; Voice.dc=oldChannel; Voice.audio={muted:true};
  const staleSuccess=Voice.handleToolCall({type:"tool_call",call_id:"c2",name:"converse",
    arguments:'{"message":" late success "}'});
  Voice.dc={readyState:"open",send:v=>freshSent.push(JSON.parse(v)),close:()=>{}};
  Voice.state="listening"; resolveStale("Late universe reply."); await staleSuccess;
  out.staleSuccess={state:Voice.state,pending:Voice.canonicalResponsePending,
    expectedReply:Voice.expectedReply,freshSent};
  let rejectStale;
  voiceTurnImpl=()=>new Promise((_resolve,reject)=>{rejectStale=reject;});
  const staleFailure=Voice.handleToolCall({type:"tool_call",call_id:"c3",name:"converse",
    arguments:'{"message":" late failure "}'});
  const liveChannel={readyState:"open",send:()=>{},close:()=>{}};
  Voice.dc=liveChannel; Voice.state="listening"; rejectStale(new Error("offline"));
  await staleFailure;
  out.staleFailure={state:Voice.state,sameChannel:Voice.dc===liveChannel};
  voiceTurnImpl=async()=>"Exact universe reply.";
  const realConnect=Voice._connect,realTeardown=Voice._teardownTransport;
  let attempts=0; Voice.epoch=10; Voice.reconnecting=false; Voice.reconnectAttempts=0;
  Voice._teardownTransport=()=>{};
  Voice._connect=async()=>{
    attempts++;if(attempts<3)throw new Error("offline");
    Voice.reconnecting=false;Voice.state="listening";
  };
  capabilityDoc={available:false,state:"incompatible",reason:"capability_not_declared",
    remediation:"existing_connection_surface"};
  const mediaBeforeRevokedReconnect=mediaRequests;
  await Voice.reconnect(10);
  out.revokedReconnect={attempts,mediaRequests:mediaRequests-mediaBeforeRevokedReconnect,
    state:Voice.state,status};
  capabilityDoc={available:true,state:"ready",resource:"user_bound_voice_connection",
    remediation:"none",disclosure_id:"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    service_name:"My bridge",privacy_url:"https://bridge.example/privacy"};
  Voice.capability=capabilityDoc;attempts=0;Voice.epoch=11;Voice.reconnecting=false;
  Voice.reconnectAttempts=0;Voice.state="listening";
  await Voice.reconnect(11); out.reconnect={attempts,state:Voice.state};
  Voice._connect=realConnect;Voice._teardownTransport=realTeardown;
  Voice.canonicalResponsePending=false; Voice.audio={muted:true};
  Voice.handleServerEvent({type:"audio_started"});
  out.untrusted={state:Voice.state,status};
  Voice.state="speaking"; Voice.canonicalResponsePending=true;
  Voice.expectedReply="Exact universe reply."; Voice.audio={muted:false};
  Voice.handleServerEvent({type:"output_transcript",transcript:"Different"});
  out.mismatch={state:Voice.state,status,traces};
  Voice.state="speaking"; Voice.canonicalResponsePending=true;
  Voice.canonicalResponseInterrupted=false; Voice.expectedReply="Exact universe reply.";
  Voice.audio={muted:false}; Voice.handleServerEvent({type:"speech_started"});
  Voice.handleServerEvent({type:"output_transcript",transcript:"Altered answer"});
  out.interruptedMismatch={state:Voice.state,status};
  const raceStreams=[],racePcs=[],sessionResolvers=[];
  navigator.mediaDevices.getUserMedia=async()=>{
    const track={stopped:false,stop(){this.stopped=true;}};
    const stream={track,getTracks:()=>[track]}; raceStreams.push(stream); return stream;
  };
  RTCPeerConnection=class{
    constructor(){this.closed=false;this.iceGatheringState="complete";racePcs.push(this);}
    addEventListener(){} addTrack(){}
    createDataChannel(){
      const dc={readyState:"open",closed:false,send(){},close(){this.closed=true;},
        addEventListener(name,callback){if(name==="open")queueMicrotask(callback);}};
      this.dataChannel=dc;return dc;
    }
    async createOffer(){return {type:"offer",sdp:"v=0\\r\\n"};}
    async setLocalDescription(offer){this.localDescription=offer;}
    async setRemoteDescription(){if(this.closed)throw new Error("closed peer");}
    close(){this.closed=true;}
  };
  Voice._session=()=>new Promise(resolve=>sessionResolvers.push(resolve));
  Voice.epoch=30;Voice.state="requesting_permission";
  const firstConnect=Voice._connect(30,false);
  while(sessionResolvers.length<1)await Promise.resolve();
  Voice.epoch=31;Voice.state="requesting_permission";
  const secondConnect=Voice._connect(31,false);
  while(sessionResolvers.length<2)await Promise.resolve();
  const livePc=Voice.pc,liveDc=Voice.dc,liveStream=Voice.stream;
  sessionResolvers[0]({answer_sdp:"v=0\\r\\n",max_session_seconds:1800});
  await firstConnect;
  out.connectRace={currentPc:Voice.pc===livePc,currentDc:Voice.dc===liveDc,
    currentStream:Voice.stream===liveStream,oldPcClosed:racePcs[0].closed,
    oldDcClosed:racePcs[0].dataChannel.closed,oldTrackStopped:raceStreams[0].track.stopped,
    livePcClosed:livePc.closed,liveTrackStopped:liveStream.track.stopped};
  sessionResolvers[1]({answer_sdp:"v=0\\r\\n",max_session_seconds:1800});
  await secondConnect;out.connectRace.finalState=Voice.state;
  const recognitionInstances=[],spoken=[],spokenVoices=[],utterances=[],speechCancels=[];
  class FakeSpeechRecognition{
    constructor(){this.started=0;this.stopped=0;this.aborted=0;recognitionInstances.push(this);}
    start(){this.started++;if(this.onstart)this.onstart();}
    stop(){this.stopped++;if(this.onend)this.onend();}
    abort(){this.aborted++;if(this.onend)this.onend();}
  }
  window.webkitSpeechRecognition=FakeSpeechRecognition;
  window.SpeechSynthesisUtterance=class{constructor(text){this.text=text;}};
  const browserVoices=[
    {voiceURI:"voice-default",name:"Default voice",lang:"en-US",default:true},
    {voiceURI:"voice-choice",name:"Chosen voice",lang:"en-GB",default:false}
  ];
  window.speechSynthesis={
    autoEnd:true,
    getVoices(){return browserVoices;},
    cancel(){speechCancels.push("cancel");},
    speak(utterance){spoken.push(utterance.text);
      utterances.push(utterance);
      spokenVoices.push(utterance.voice&&utterance.voice.voiceURI||"");
      if(this.autoEnd)queueMicrotask(()=>utterance.onend());}
  };
  navigator.language="en-US";
  Voice.start=realStart;
  fetchError=new Error("offline");connectCalls=[];Voice.stop(false);Voice.init();
  await Voice.refreshCapability();await Voice.requestStart();
  out.browserStatusFailure={state:Voice.state,label:els["btn-voice"].textContent,
    disclosureShown:!els["voice-disclosure"].hidden,connectCalls:connectCalls.slice(),status};
  fetchError=null;
  CFG.voice.enabled=false;
  capabilityDoc={available:false,state:"incompatible",reason:"provider_voice_unsupported",
    remediation:"existing_connection_surface"};connectCalls=[];turns=[];
  Voice.stop(false);Voice.init();await Voice.refreshCapability();
  const fetchesBeforeBrowser=fetched.length;
  await Voice.requestStart();
  out.browserFallbackBeforeAccept={
    state:Voice.state,label:els["btn-voice"].textContent,
    disclosureShown:!els["voice-disclosure"].hidden,
    browserDisclosure:!els["voice-disclosure-browser"].hidden,
    bridgeDisclosure:!els["voice-disclosure-bridge"].hidden,
    connectCalls:connectCalls.slice(),mediaRequests,
    recognitionInstances:recognitionInstances.length
  };
  Voice.cancelDisclosure();
  out.browserFallbackDecline={
    disclosureShown:!els["voice-disclosure"].hidden,
    recognitionInstances:recognitionInstances.length,
    sessionFetches:fetched.slice(fetchesBeforeBrowser).filter(url=>url==="/app/voice/session").length
  };
  await Voice.requestStart();
  Voice.selectBrowserVoice("voice-choice");
  await Voice.acceptDisclosure();await Promise.resolve();
  const recognition=recognitionInstances[recognitionInstances.length-1];
  const commitBrowserSpeech=()=>timers.slice().reverse().find(timer=>timer.ms===900).fn();
  const finalResult=[{transcript:"Hello,"}];finalResult.isFinal=true;
  recognition.onresult({resultIndex:0,results:[finalResult]});
  const duplicateResult=[{transcript:"Hello."}];duplicateResult.isFinal=true;
  recognition.onresult({resultIndex:0,results:[duplicateResult]});
  const continuedResult=[{transcript:"same universe"}];continuedResult.isFinal=true;
  recognition.onresult({resultIndex:0,results:[continuedResult]});
  out.browserEndpointingGrace={turnsBeforeCommit:turns.slice(),
    recognitionStopsBeforeCommit:recognition.stopped,
    draft:Voice.browserDraftUtterance,status};
  commitBrowserSpeech();
  await Voice.browserTurn;
  const trailingEcho=[{transcript:"Exact universe reply"}];trailingEcho.isFinal=true;
  const turnsBeforeTrailingEcho=turns.length;
  recognition.onresult({resultIndex:0,results:[trailingEcho]});
  out.browserFallbackTurn={
    state:Voice.state,turns:turns.slice(),spoken:spoken.slice(),
    spokenVoices:spokenVoices.slice(),
    trailingEchoSuppressed:turns.length===turnsBeforeTrailingEcho,
    recognitionStarts:recognition.started,recognitionStops:recognition.stopped,
    sessionFetches:fetched.slice(fetchesBeforeBrowser).filter(url=>url==="/app/voice/session").length,
    connectCalls:connectCalls.slice()
  };
  out.browserVoiceChoice={
    hidden:els["voice-output-select"].hidden,
    selected:els["voice-output-select"].value,
    options:els["voice-output-select"].children.map(option=>[option.value,option.textContent])
  };
  timers.slice().reverse().find(timer=>timer.ms===250).fn();
  let resolveThinking;
  voiceTurnImpl=()=>new Promise(resolve=>{resolveThinking=resolve;});
  const thinkingResult=[{transcript:"Start a queued thought"}];thinkingResult.isFinal=true;
  recognition.onresult({resultIndex:0,results:[thinkingResult]});
  commitBrowserSpeech();
  timers.slice().reverse().find(timer=>timer.ms===250).fn();
  const queuedResult=[{transcript:"Add this when ready"}];queuedResult.isFinal=true;
  recognition.onresult({resultIndex:0,results:[queuedResult]});
  const queuedDuplicate=[{transcript:"Add this when ready."}];queuedDuplicate.isFinal=true;
  recognition.onresult({resultIndex:0,results:[queuedDuplicate]});
  const queuedContinuation=[{transcript:"and keep it together"}];queuedContinuation.isFinal=true;
  recognition.onresult({resultIndex:0,results:[queuedContinuation]});
  out.browserThinkingFragments={pending:Voice.browserPendingUtterance,
    lastSegment:Voice.browserPendingLastSegment};
  const firstThinkingTurn=Voice.browserTurn;
  voiceTurnImpl=async()=>"Exact universe reply.";
  resolveThinking("Exact universe reply.");await firstThinkingTurn;
  await Promise.resolve();await Voice.browserTurn;
  out.browserThinkingQueue={state:Voice.state,turns:turns.slice(),spoken:spoken.slice()};
  timers.slice().reverse().find(timer=>timer.ms===250).fn();
  window.speechSynthesis.autoEnd=false;
  const speakingResult=[{transcript:"Please explain the next step"}];speakingResult.isFinal=true;
  recognition.onresult({resultIndex:0,results:[speakingResult]});
  commitBrowserSpeech();
  await Promise.resolve();await Promise.resolve();await Promise.resolve();
  const restartWhileSpeaking=timers.slice().reverse().find(timer=>timer.ms===250);
  restartWhileSpeaking.fn();
  const turnsBeforeEcho=turns.length;
  const echoResult=[{transcript:"Exact universe reply"}];echoResult.isFinal=true;
  recognition.onresult({resultIndex:0,results:[echoResult]});
  const echoSuppressed=turns.length===turnsBeforeEcho;
  const interruptedUtterance=utterances[utterances.length-1];
  const bargeResult=[{transcript:"Actually stop and answer this instead"}];bargeResult.isFinal=true;
  recognition.onresult({resultIndex:0,results:[bargeResult]});
  await Promise.resolve();await Promise.resolve();await Promise.resolve();
  const replacementTurn=Voice.browserTurn;
  const replacementUtterance=utterances[utterances.length-1];
  interruptedUtterance.onend();interruptedUtterance.onerror();
  const staleCallbackPreserved=Voice.state==="speaking"&&Voice.browserSubmitting&&
    Voice.utterance===replacementUtterance;
  replacementUtterance.onend();await replacementTurn;
  out.browserBargeIn={
    state:Voice.state,echoSuppressed,staleCallbackPreserved,
    turns:turns.slice(),spoken:spoken.slice(),
    recognitionStarts:recognition.started,recognitionStops:recognition.stopped,
    selectedVoice:spokenVoices[spokenVoices.length-1],status
  };
  window.speechSynthesis.autoEnd=false;
  const stoppedResult=[{transcript:"Stop during speech"}];stoppedResult.isFinal=true;
  recognition.onresult({resultIndex:0,results:[stoppedResult]});
  commitBrowserSpeech();
  await Promise.resolve();await Promise.resolve();await Promise.resolve();
  const stoppedTurn=Voice.browserTurn,cancelsBeforeSpeechStop=speechCancels.length;
  Voice.stop(false);await stoppedTurn;
  out.browserSpeechTeardown={state:Voice.state,aborted:recognition.aborted,
    speechCancels:speechCancels.length-cancelsBeforeSpeechStop};
  await Voice.requestStart();await Promise.resolve();
  const watchdogRecognition=recognitionInstances[recognitionInstances.length-1];
  const stalledResult=[{transcript:"Speech watchdog"}];stalledResult.isFinal=true;
  watchdogRecognition.onresult({resultIndex:0,results:[stalledResult]});
  commitBrowserSpeech();
  await Promise.resolve();await Promise.resolve();await Promise.resolve();
  const speechWatchdog=timers.slice().reverse().find(timer=>timer.ms>=30000&&timer.ms<=1800000);
  const stalledTurn=Voice.browserTurn;speechWatchdog.fn();await stalledTurn;
  out.browserSpeechWatchdog={state:Voice.state,turns:turns.slice(),spoken:spoken.slice(),status};
  const cancelsBeforeStop=speechCancels.length;Voice.stop(false);
  out.browserFallbackStop={aborted:watchdogRecognition.aborted,
    speechCancelsOnStop:speechCancels.length-cancelsBeforeStop,
    state:Voice.state};
  await Voice.requestStart();await Promise.resolve();
  const draftRecognition=recognitionInstances[recognitionInstances.length-1];
  const unfinishedResult=[{transcript:"This thought is unfinished"}];unfinishedResult.isFinal=true;
  draftRecognition.onresult({resultIndex:0,results:[unfinishedResult]});
  const staleDraftCommit=timers.slice().reverse().find(timer=>timer.ms===900);
  const turnsBeforeDraftStop=turns.length;
  Voice.stop(false);staleDraftCommit.fn();
  out.browserDraftTeardown={state:Voice.state,draft:Voice.browserDraftUtterance,
    commitTimer:Voice.browserCommitTimer,aborted:draftRecognition.aborted,
    staleCommitSuppressed:turns.length===turnsBeforeDraftStop};
  Voice.capability={available:true,state:"ready",mode:"browser"};
  conversationStatus="Your agent is thinking...";
  Voice.state="listening";Voice._render();
  out.statusIndependence={conversationStatus,voiceStatus:status,active:Voice.isActive()};
  els["btn-send"].disabled=true;turnStartedAt=123;
  Voice.stop(true);
  out.stopDuringPending={conversationStatus,voiceStatus:status,state:Voice.state,
    active:Voice.isActive()};
  els["btn-send"].disabled=false;turnStartedAt=0;conversationStatus="";
  Voice.conversationSettled(true);
  out.pendingSettled={conversationStatus,voiceStatus:status};
  // Seen live 2026-09-23 PDT: Voice stopped during the 17:39 turn, that turn's
  // transport went unconfirmed at 17:46 and the line said the reply did not
  // arrive and the message was available to retry. "Send it again" was NEVER
  // clicked - the founder typed two NEW questions at 17:47 and 17:54 (answered
  // 17:51 and 18:01) and the sentence was still sitting beside the answered
  // ones. A new turn of this same account
  // and home retires it; another account's or home's turn does not.
  const missing={message:"Retest my checklist",ts:4242,consumerRequest:null,
    owner:"p-1",scope:"u-1"};
  const goStale=()=>{els["btn-send"].disabled=true;turnStartedAt=123;Voice.stop(true);
    els["btn-send"].disabled=false;turnStartedAt=0;Voice.conversationSettled(false,missing);};
  goStale();
  const staleLine=status;
  Voice.turnStarted("p-2","u-1");
  const afterOtherOwnerTurn=status;
  Voice.turnStarted("p-1","u-9");
  const afterOtherHomeTurn=status;
  // The new turn is a DIFFERENT message: Voice is told only whose turn it is.
  Voice.turnStarted("p-1","u-1");
  out.newTurnRetiresStaleRetry={staleLine,afterOtherOwnerTurn,afterOtherHomeTurn,
    afterNewTurn:status,stateLabel:VOICE_LABELS[Voice.state],state:Voice.state,
    marker:Voice.pendingReplyMissing,
    secondNewTurnRewrites:(status="",Voice.turnStarted("p-1","u-1"),status!=="")};
  // Anything rendered over the sentence since owns the line: a real voice error
  // (which is what the founder would actually need to read) is never erased.
  goStale();
  Voice.fail(new Error("voice_permission_denied"));
  const realError=status;
  Voice.turnStarted("p-1","u-1");
  out.newTurnKeepsRealError={realError,after:status,state:Voice.state};
  // Belt and braces: marker still set, but the sentence on screen is not ours.
  goStale();
  Voice.pendingReplyMissing=Object.assign({},missing);
  setVoiceStatusLine("Voice is off. Something else entirely.");
  Voice.turnStarted("p-1","u-1");
  out.newTurnKeepsForeignLine=status;
  console.log(JSON.stringify(out));
})().catch(e=>{console.error(e&&e.stack||e);process.exit(1);});
"""
    script = tmp_path / "voice_adapter.js"
    script.write_text(
        "\n".join(
            (shim, labels.group(0), transitions.group(0), functions, voice.group(0), scenario)
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [node, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert proc.returncode == 0, f"voice adapter harness crashed:\n{proc.stderr}"
    return json.loads(proc.stdout)










def _turn(speaker, text, age_s):
    """A history turn as the peek sends it: epoch SECONDS, oldest first."""
    import time

    return {"speaker": speaker, "text": text, "ts": time.time() - age_s, "truncated": False}
























# --- a rail answer is the founder's line (2026-08-30) -----------------------------
#
# Live 2026-08-29: tiny raised an ask, the founder approved it in the rail, and
# tiny sat idle until the founder typed "approved - go ahead" - twice. The click
# is that line; the app now says it in the thread through the normal send path.

_TITLE = "Extend GitHub access so I can repair the README"
_REQ = {"request_id": "req_1", "kind": "API", "title": _TITLE, "fields": []}




































_NOW_MS = int(time.time() * 1000)




















































pytestmark = pytest.mark.verifies(
    'live-mcp-connector-surface:'
    'The Web App Is Served At The Apex `/app`',
)
