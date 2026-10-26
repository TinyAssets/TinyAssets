"""Realtime turns are streaming model calls. The retired voice loop is gone."""

import asyncio
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets.broker.duplex import DuplexRefused, DuplexSession, accept_duplex
from tinyassets.providers import model_call
from tinyassets.providers.base import ProviderResponse, UniverseContext
from tinyassets.providers.media_budget import MediaCap
from tinyassets.providers.model_policy import ModelRef
from tinyassets.providers.realtime_session import (
    CapabilityGap,
    RealtimeSession,
    RealtimeSource,
    resolve_realtime,
)
from tinyassets.storage.owner_media import OwnerMediaStore

pytestmark = pytest.mark.verifies(
    "n3-realtime-model-sessions:Realtime exchanges use the one owner session and model call",
    "n3-realtime-model-sessions:Realtime source authority and transport stay at the broker",
    "n3-realtime-model-sessions:Realtime capture and history are owner scoped",
    "n3-realtime-model-sessions:Realtime spending is bounded throughout a session",
)

AUDIO = {"realtime": {"audio": {"in": ["audio/pcm"]}, "text": {"in": ["text/plain"]}}}
VIDEO = {"realtime": {"video": {"in": ["video/mp4"]}, "audio": {"in": ["audio/pcm"]}}}


class _Fence:
    def __init__(self, ok=True):
        self.ok = ok

    def admits(self, generation, token):
        return self.ok and generation == 3 and token == "fence-token"


def _stream():
    return SimpleNamespace(generation=3, token="fence-token", duplex=None)


def test_duplex_cap_cancel_fence_and_no_credential_or_direct_egress(monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError("direct egress")

    monkeypatch.setattr(socket, "create_connection", refused)
    cap = MediaCap(remaining=50, slice_bytes=40)
    stream = _stream()
    receipt = accept_duplex(stream, {
        "upgrade": "duplex",
        "script": [
            {"op": "frame", "direction": "up", "payload": b"a" * 10},
            {"op": "credit", "direction": "down", "n": 1},
            {"op": "frame", "direction": "down", "payload": b"b" * 50},
        ],
    }, fence=_Fence(), cap=cap, credential="broker-secret")
    assert receipt["receipts"] == 1
    assert receipt["outcome"] == "budget_exhausted"
    assert receipt["direct_egress"] is False
    assert receipt["side_effect_state"] == "unknown"
    assert "broker-secret" not in repr(receipt)
    assert "broker-secret" not in repr(stream.duplex.box_view())
    assert stream.duplex.direct_egress is False
    with pytest.raises(DuplexRefused) as leaked:
        accept_duplex(_stream(), {"credential": "broker-secret"}, fence=_Fence())
    assert leaked.value.code == "credential"
    with pytest.raises(DuplexRefused) as fenced:
        accept_duplex(_stream(), {"upgrade": "duplex"}, fence=_Fence(ok=False))
    assert fenced.value.code == "fenced"
    session = DuplexSession(
        generation=3, token="fence-token", fence=_Fence(), cap=MediaCap(remaining=None),
        credential="broker-secret",
    )
    session.open()
    session.revoke()
    assert session.upstream is False
    assert "broker-secret" not in repr(session.box_view())
    session.cancel()
    assert session.settle()["receipts"] == 1
    assert session.settle()["outcome"] == "cancelled"


def test_unsupported_media_is_rejected_before_a_weaker_source():
    sources = (
        RealtimeSource("strong", 10, AUDIO),
        RealtimeSource("weak", 1, VIDEO),
    )
    with pytest.raises(CapabilityGap) as gap:
        resolve_realtime(sources, "video")
    assert gap.value.source_id == "strong"
    assert gap.value.reason == "unsupported"
    assert resolve_realtime(sources, "audio").id == "strong"


def test_realtime_uses_s3_model_call(tmp_path, monkeypatch):
    center = tmp_path / "center"
    center.mkdir()
    budget = model_call.open_allocation("owner", center)
    session = RealtimeSession(
        owner="owner", command_center="center", parent_usage_id=budget.usage_id,
        root=tmp_path,
    )

    class Bridge:
        def __init__(self):
            self.cancelled = []

        def cancel_execution(self, turn):
            self.cancelled.append(turn)

    bridge = Bridge()
    session.bind_s3(bridge)
    calls = []
    png = b"\x89PNG-out"

    def complete(role, prompt, system, config, *, operation, universe_context, **kwargs):
        del role, system, operation, kwargs
        calls.append((prompt, config.request_budget.usage_id, universe_context))
        if len(calls) == 1:
            session.interrupt()
            return ProviderResponse(
                text="partial", provider="source-a", model="m", reported_model="m",
                family="test", latency_ms=0,
            )
        return ProviderResponse(
            text="spoken", provider="source-a", model="m", reported_model="m",
            family="test", latency_ms=0,
            source_parts={"parts": [
                {"kind": "text", "text": "spoken"},
                {"kind": "audio", "mime": "audio/wav", "data": png, "complete": True},
            ], "outcome": "succeeded"},
            media_contract={"media": {"text": {}, "audio": {"mime": ["audio/wav"]}}},
        )

    async def call(*args, **kwargs):
        return complete(*args, **kwargs)

    router = SimpleNamespace(call=call, call_sync=complete)
    from tinyassets.providers import call as bridge_mod

    monkeypatch.setattr(bridge_mod, "get_provider_router", lambda: router)
    monkeypatch.setattr(bridge_mod, "register_open_providers_for", lambda context: None)
    monkeypatch.setattr("tinyassets.owner_runtime.broker_egress", lambda: None)
    context = UniverseContext(
        universe_dir=center,
        served_provider=SimpleNamespace(owner_user_id="owner"),
        model_selection=ModelRef("source-a", "first"),
    )
    first = asyncio.run(session.commit_turn("hello", context=model_call.ModelContext(
        prompt="", universe_context=context, operation="converse",
    ), audio=b"microphone-secret"))
    second = asyncio.run(session.commit_turn("again", context=model_call.ModelContext(
        prompt="", universe_context=context, operation="converse",
    )))
    assert [item[0] for item in calls] == ["hello", "again"]
    assert {item[1] for item in calls} == {budget.usage_id}
    assert first.receipt.outcome == "cancelled"
    assert second.receipt.outcome == "succeeded"
    assert second.receipt.parent_usage_id == budget.usage_id
    assert len(session.receipts) == 2
    assert session.receipts[0] is not session.receipts[1]
    assert bridge.cancelled and bridge.cancelled[0] != session.current
    assert len(bridge.cancelled) == 1
    assert session.history == [{"text": "hello"}, {"text": "again"}]
    blob = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    assert b"microphone-secret" not in blob
    stored = OwnerMediaStore(center).read("owner", second.parts[1].reference)
    assert stored == png
    gone = session.note_disconnect(request_left_broker=True)
    assert gone["resend"] is False and gone["side_effect_state"] == "unknown"
    assert gone["history"] == session.history
    quiet = session.note_disconnect(request_left_broker=False)
    assert quiet["side_effect_state"] == "none" and quiet["resend"] is False


def test_no_realtime_loop_beside_s3():
    forbidden = ("sendVoiceTurn", "realtime_voice", "/voice/session", "offer_sdp")
    root = Path("tinyassets")
    offenders = []
    for path in list(root.rglob("*.py")) + list(root.rglob("*.js")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path}:{token}")
    assert offenders == []
    source = Path("tinyassets/providers/realtime_session.py").read_text(encoding="utf-8")
    assert "model_call" in source and "cancel_execution" in source


def test_capture_teardown_transcript_and_barge_in():
    script = r"""
const fs = require("fs");
const vm = require("vm");
const capture = fs.readFileSync("tinyassets/onboarding/app/realtime_session.js", "utf8");
const sandbox = {};
vm.runInNewContext(capture + "\nthis.api = RealtimeCapture;", sandbox);
const api = sandbox.api;
const gap = api.admitBeforeCapture({admitted:false, gap:"unsupported"}, "video");
if (gap.ok) throw new Error("capture started");
let stopped = 0;
const n = api.stopTracks([{stop(){ stopped += 1; }}, {stop(){ stopped += 1; }}]);
if (n !== 2 || stopped !== 2) throw new Error("tracks live");
const history = api.inputHistory([], "hello");
if (history.length !== 1 || history[0].text !== "hello" || "audio" in history[0])
  throw new Error("input audio kept");
const lost = api.disconnect(true);
if (lost.resend !== false || lost.side_effect_state !== "unknown") throw new Error("resend");
const quiet = api.disconnect(false);
if (quiet.side_effect_state !== "none") throw new Error("uncertain");
if (api.bargeIn("turn-1").cancel !== "turn-1") throw new Error("barge");
const document = {
  currentScript: {nonce: "n"},
  createElement(name){ return el(name); },
  createElementNS(){ return el("svg"); },
  createTextNode(value){ return {node:"text", text:String(value)}; },
};
function el(name){
  return {name, children:[], dataset:{}, textContent:"", alt:"", controls:false,
    appendChild(child){ this.children.push(child); return child; }, setAttribute(){}};
}
const renderSandbox = {document, console};
const renderer = fs.readFileSync("tinyassets/onboarding/chat_render.js","utf8");
vm.runInNewContext(renderer + "\nthis.ChatRender = ChatRender;", renderSandbox);
const parent = el("span");
renderSandbox.ChatRender.renderParts(parent, [
  {kind:"text", text:"see"},
  {kind:"image", mime:"image/png", reference:"media:owner:center:aa", byte_length:2},
]);
if (parent.children[0].text !== "see") throw new Error("text");
if (parent.children[1].dataset.mediaRef !== "media:owner:center:aa") throw new Error("ref");
if (String(parent.children[1].src || "").includes("/")) throw new Error("host path");
"""
    proc = subprocess.run(["node", "-e", script], check=False, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
