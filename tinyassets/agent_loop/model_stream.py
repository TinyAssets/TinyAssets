"""Scanned broker bytes to bounded chat deltas and the existing terminal codec."""
from __future__ import annotations

import asyncio
import codecs
import inspect
import json
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar

from tinyassets.providers.agent_chat_codec import _object

_OBSERVER = ContextVar("model_stream_observer", default=None)
# Same aggregate response bound as the broker. The incremental line buffer is
# part of this total, not a second unbounded copy of the stream.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


@contextmanager
def observe_model_stream(callback):
    token = _OBSERVER.set(callback)
    try:
        yield
    finally:
        _OBSERVER.reset(token)


async def emit(event):
    callback = _OBSERVER.get()
    if callback is not None:
        result = callback(event)
        if inspect.isawaitable(result):
            await result


class TextDeltas:
    """Parse only complete data lines; never expose reasoning or tool arguments."""

    def __init__(self):
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.pending = ""

    def feed(self, chunk):
        self.pending += self.decoder.decode(chunk)
        lines = self.pending.split("\n")
        self.pending = lines.pop()
        for line in lines:
            if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                continue
            try:
                value = _object(line[5:].strip())
            except (ValueError, json.JSONDecodeError):
                continue  # terminal codec refuses malformed wire; no preview
            choices = value.get("choices")
            if not isinstance(choices, list) or len(choices) != 1:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            text = delta.get("content") if isinstance(delta, dict) else None
            if isinstance(text, str) and text:
                yield text


async def collect_stream(proxy, request, *, usage=None):
    from tinyassets.broker.ops import new_op_id
    from tinyassets.exceptions import ProviderProtocolError

    operation_id = usage.operation_id if usage is not None else new_op_id()
    data = bytearray()
    deltas = TextDeltas()
    status = "interrupted"
    await emit({"type": "model_start", "operation_id": operation_id})
    try:
        async with proxy.stream(
            "POST", request, inference_usage=usage, operation_id=operation_id,
        ) as stream:
            head = await stream.head()
            async for chunk in stream.body():
                if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise ProviderProtocolError("model response exceeds the byte limit")
                data.extend(chunk)
                if type(head.get("status")) is int and 200 <= head["status"] < 300:
                    for text in deltas.feed(chunk):
                        await emit({"type": "model_text_delta", "operation_id": operation_id,
                                    "text": text})
        status = "completed"
        return {**head, "body": data.decode("utf-8", errors="replace")}
    finally:
        await emit({"type": "model_end", "operation_id": operation_id, "status": status})


# Active clients only: overlapping streams share one connection; the last user
# closes it, so short-lived loops and completed owners leave no background task.
_CLIENTS = {}


@asynccontextmanager
async def shared_broker_client(path, *, principal, command_center, fence, verify_peer):
    from tinyassets.broker.aclient import AsyncBrokerClient

    loop = asyncio.get_running_loop()
    key = (loop, str(path), principal, command_center, fence)
    entry = _CLIENTS.get(key)
    if entry is None:
        entry = [AsyncBrokerClient(
            path, principal=principal, command_center=command_center, fence=fence,
            verify_peer=verify_peer,
        ), 0]
        _CLIENTS[key] = entry
    entry[1] += 1
    try:
        yield entry[0]
    finally:
        entry[1] -= 1
        if not entry[1]:
            del _CLIENTS[key]
            await entry[0].close()
