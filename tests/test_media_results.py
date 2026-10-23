"""Typed model parts stay in the owner box and settle on one receipt per call."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.providers import model_call
from tinyassets.providers.base import ProviderResponse, UniverseContext
from tinyassets.providers.execution_receipt import normalize_execution_receipt
from tinyassets.providers.media_budget import BudgetExhausted, MediaCap
from tinyassets.providers.media_parts import ModelMediaError, normalize_parts
from tinyassets.providers.model_policy import ModelRef
from tinyassets.storage.owner_media import OwnerMediaStore

pytestmark = pytest.mark.verifies(
    "n9-model-media-results:Model calls return ordered typed parts",
    "n9-model-media-results:Every admitted model call has one media-linked receipt",
    "n9-model-media-results:Media bytes remain in the owner's box",
)

CONTRACT = {
    "media": {
        "text": {},
        "tool_call": {},
        "image": {"mime": ["image/png"]},
        "audio": {"mime": ["audio/wav"]},
        "video": {"mime": ["video/mp4"]},
    }
}
PNG = b"\x89PNG-one"
PNG_B = b"\x89PNG-two"


def _response(parts, *, outcome="succeeded", meter=None):
    return ProviderResponse(
        text="ignored image bytes", provider="source-a", model="m",
        reported_model="m", family="test", latency_ms=0,
        source_parts={"parts": parts, "outcome": outcome},
        media_contract=CONTRACT, media_meter=meter,
    )


def _install_router(monkeypatch, responses):
    calls = []

    def complete(role, prompt, system, config, *, operation, universe_context, **kwargs):
        del role, system, operation, kwargs
        calls.append((prompt, config.request_budget.usage_id, universe_context))
        return responses.pop(0)

    async def call(*args, **kwargs):
        return complete(*args, **kwargs)

    router = SimpleNamespace(call=call, call_sync=complete)
    from tinyassets.providers import call as bridge

    monkeypatch.setattr(bridge, "get_provider_router", lambda: router)
    monkeypatch.setattr(bridge, "register_open_providers_for", lambda context: None)
    monkeypatch.setattr("tinyassets.owner_runtime.broker_egress", lambda: None)
    return calls


def _context(tmp_path):
    center = tmp_path / "center"
    center.mkdir()
    return UniverseContext(
        universe_dir=center,
        served_provider=SimpleNamespace(owner_user_id="owner"),
        model_selection=ModelRef("source-a", "first"),
    )


def _call(context, response_holder, monkeypatch, *, cap=None):
    calls = _install_router(monkeypatch, response_holder)
    request = model_call.ModelCall(context.model_selection, (), model_call.ModelContext(
        prompt="reply", universe_context=context, operation="converse",
        media_cap=cap, streaming=cap is not None,
    ))
    result = asyncio.run(model_call.call(request, model_call.Parent("owner", "center")))
    return result, calls


def test_mixed_text_and_image_keep_order_and_text_projection(tmp_path):
    store = OwnerMediaStore(tmp_path)
    parts, outcome = normalize_parts(CONTRACT, [
        {"kind": "text", "text": "before"},
        {"kind": "image", "mime": "image/png", "data": PNG, "complete": True},
        {"kind": "text", "text": " after"},
        {"kind": "tool_call", "name": "lookup", "arguments": {"q": "x"}},
        {"kind": "video", "mime": "video/mp4", "data": b"mp4", "complete": True,
         "timing": [0, 40]},
        {"kind": "image", "mime": "image/png", "data": b"partial", "complete": False},
    ], store=store, owner="owner", command_center="center")
    assert outcome == "succeeded"
    assert [part.kind for part in parts] == ["text", "image", "text", "tool_call", "video"]
    assert "".join(part.text for part in parts if part.kind == "text") == "before after"
    image = parts[1]
    assert image.mime == "image/png" and image.byte_length == len(PNG)
    assert store.read("owner", image.reference) == PNG
    assert parts[4].timing == (0, 40)
    assert not any(path.name == "partial" for path in tmp_path.rglob("*") if path.is_file())


def test_malformed_and_unsupported_parts_fail_without_becoming_text(tmp_path):
    store = OwnerMediaStore(tmp_path)
    with pytest.raises(ModelMediaError) as malformed:
        normalize_parts(CONTRACT, [
            {"kind": "text", "text": "kept"},
            {"kind": "image", "mime": "image/gif", "data": b"GIF89a", "complete": True},
        ], store=store, owner="owner", command_center="center")
    assert malformed.value.outcome == "malformed"
    assert [part.kind for part in malformed.value.committed] == ["text"]
    assert malformed.value.committed[0].text == "kept"
    with pytest.raises(ModelMediaError) as unsupported:
        normalize_parts({"media": {"text": {}}}, [
            {"kind": "audio", "mime": "audio/wav", "data": b"wav", "complete": True},
        ], store=store, owner="owner", command_center="center")
    assert unsupported.value.outcome == "unsupported"
    assert unsupported.value.committed == ()


def test_another_owner_cannot_read_media_and_shared_services_hold_no_path(tmp_path):
    store = OwnerMediaStore(tmp_path)
    reference = store.commit("alice", "center", b"secret-bytes")
    with pytest.raises(PermissionError) as refused:
        store.read("bob", reference)
    assert b"secret" not in str(refused.value).encode()
    assert store.read("alice", reference) == b"secret-bytes"
    root = Path("tinyassets")
    for path in [root / "owner_router.py", * (root / "broker").rglob("*.py")]:
        text = path.read_text(encoding="utf-8")
        assert "owner_media" not in text
        assert "OwnerMediaStore" not in text


def test_media_receipt_per_call(tmp_path, monkeypatch):
    context = _context(tmp_path)
    calls = _install_router(monkeypatch, [
        _response([
            {"kind": "text", "text": "one"},
            {"kind": "image", "mime": "image/png", "data": PNG, "complete": True},
        ]),
        _response([
            {"kind": "image", "mime": "image/png", "data": PNG_B, "complete": True},
            {"kind": "image", "mime": "image/png", "data": b"cut", "complete": False},
        ], outcome="failed"),
    ])
    results = []
    for prompt in ("first", "second"):
        request = model_call.ModelCall(context.model_selection, (), model_call.ModelContext(
            prompt=prompt, universe_context=context, operation="converse", streaming=True,
        ))
        results.append(asyncio.run(model_call.call(
            request, model_call.Parent("owner", context.universe_dir.name))))
    assert calls[0][1] != calls[1][1]
    assert results[0].receipt.usage_id != results[1].receipt.usage_id
    assert results[0].receipt.parent_usage_id == results[0].receipt.usage_id
    assert results[1].receipt.parent_usage_id == results[1].receipt.usage_id
    assert results[0].text == "one"
    assert results[0].receipt.media["outcome"] == "succeeded"
    assert results[1].receipt.media["outcome"] == "incomplete"
    assert results[0].receipt.media["part_refs"] != results[1].receipt.media["part_refs"]
    assert all(len({item.receipt.usage_id}) == 1 for item in results)
    assert normalize_execution_receipt(results[0].receipt)["media"]["part_refs"]
    stored = OwnerMediaStore(context.universe_dir)
    assert stored.read("owner", results[1].parts[0].reference) == PNG_B


def test_cap_exhaustion_settles_one_receipt_and_pre_admission_is_not_sent(
        tmp_path, monkeypatch):
    context = _context(tmp_path)
    calls = _install_router(monkeypatch, [
        _response([
            {"kind": "audio", "mime": "audio/wav", "data": b"wav", "complete": True},
        ], meter={"renew": True, "renew_bytes": 32, "input_bytes": 4}),
    ])
    cap = MediaCap(remaining=40, slice_bytes=32)
    request = model_call.ModelCall(context.model_selection, (), model_call.ModelContext(
        prompt="stream", universe_context=context, operation="converse",
        media_cap=cap, streaming=True,
    ))
    result = asyncio.run(model_call.call(request, model_call.Parent("owner", "center")))
    assert len(calls) == 1
    assert result.receipt.media["outcome"] == "budget_exhausted"
    assert result.receipt.outcome == "budget_exhausted"
    assert result.parts[0].kind == "audio"
    free = MediaCap()
    free.reserve()
    with pytest.raises(BudgetExhausted):
        free.renew()
    assert free.paid is False
    dry = MediaCap(remaining=0, slice_bytes=8)
    calls2 = _install_router(monkeypatch, [])
    blocked = model_call.ModelCall(context.model_selection, (), model_call.ModelContext(
        prompt="nope", universe_context=context, operation="converse", media_cap=dry,
    ))
    stopped = asyncio.run(model_call.call(blocked, model_call.Parent("owner", "center")))
    assert calls2 == []
    assert stopped.text == ""
    assert stopped.response.failure_class == "budget_exhausted"
    assert stopped.receipt.media["part_refs"] == []
    refused = model_call.ModelCall(None, (), model_call.ModelContext(prompt="x"))
    with pytest.raises(ProviderAuthorityHeldError) as not_sent:
        asyncio.run(model_call.call(refused, model_call.Parent("owner", "center")))
    assert not_sent.value.outcome == "not_sent"


def test_ta_result_exposes_typed_parts():
    from tinyassets.providers.media_parts import MediaPart, TextPart
    from tinyassets.ta_capabilities import public_model_part

    viewed = [
        public_model_part(TextPart("hello")),
        public_model_part(MediaPart(
            "image", "image/png", 4, "ab" * 32, "media:owner:center:" + "ab" * 32,
        )),
    ]
    assert [part["kind"] for part in viewed] == ["text", "image"]
    assert viewed[0]["text"] == "hello"
    assert viewed[1]["reference"].startswith("media:owner:center:")
    assert b"PNG" not in str(viewed).encode()
    source = Path("tinyassets/ta_capabilities.py").read_text(encoding="utf-8")
    assert "public_model_part" in source and "def model_result" in source
