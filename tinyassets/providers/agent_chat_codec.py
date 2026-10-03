"""Pure, private text/tool translation; not a registered provider or authority.

Each immutable round can be persisted by a future authoritative journal. This
module performs no inference, tool dispatch, retry, storage or model selection.
Legacy text codecs and full-agent eligibility deliberately remain unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from mcp.types import CallToolResult, Tool

from tinyassets.providers.protocol_encoders import (
    ProtocolDecodeError,
    model_receipt,
)

StopReason = Literal[
    "completed", "tool_requests", "truncated", "content_filter", "refusal", "unknown",
]
_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_CONTINUATION = frozenset({"role", "content", "tool_calls", "reasoning", "reasoning_details"})
#: Finish reasons under which a present tool batch is a tool request. ``""`` is
#: an absent or null ``finish_reason``: OpenAI-compatible servers (and routers
#: normalizing many upstreams) send null beside a complete batch, and
#: ``function_call`` is the legacy spelling of ``tool_calls``.
_TOOL_FINISHES = frozenset({"stop", "tool_calls", "function_call", ""})
_KEY = re.compile(r"[A-Za-z0-9_.-]{1,40}\Z")
_STRUCTURE_LIMIT = 140


def _bad(detail: str) -> ProtocolDecodeError:
    return ProtocolDecodeError("agent chat " + detail)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise _bad("JSON contains duplicate keys")
        result[key] = value
    return result


def _nonfinite(_: str) -> None:
    raise _bad("JSON contains nonfinite numbers")


def _dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        raise _bad("value is not JSON data") from None


def _object(raw: str) -> dict[str, Any]:
    if not isinstance(raw, str):
        raise _bad("JSON object string required")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        # JSON exponent overflow also becomes infinity without parse_constant.
        _dump(value)
    except (ValueError, TypeError, RecursionError):
        raise _bad("invalid JSON object") from None
    if not isinstance(value, dict):
        raise _bad("JSON object required")
    return value


def _identifier(value: Any, maximum: int = 256) -> bool:
    return (
        isinstance(value, str) and 0 < len(value) <= maximum
        and bool(value.strip()) and value.isprintable()
    )


def _sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


@dataclass(frozen=True, slots=True)
class ToolRequest:
    call_id: str
    name: str
    arguments_json: str = field(repr=False)

    def arguments(self) -> dict[str, Any]:
        """Detached parsed arguments; never normalize the stored wire string."""
        return _object(self.arguments_json)


@dataclass(frozen=True, slots=True)
class AgentReply:
    stop: StopReason
    text: str | None = field(repr=False)
    refusal: str | None = field(repr=False)
    tool_requests: tuple[ToolRequest, ...]
    continuation_json: str = field(repr=False)
    dropped_fields: tuple[str, ...] = field(repr=False)
    source_ref: str
    requested_model: str
    reported_model: str
    raw_finish_reason: str = field(repr=False)
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    call_id: str
    result_json: str = field(repr=False)
    is_error: bool


@dataclass(frozen=True, slots=True)
class ToolRound:
    reply: AgentReply
    outcomes: tuple[ToolOutcome, ...]


@dataclass(frozen=True, slots=True, init=False)
class CapturedToolRound:
    """Completed round with a detached, immutable historical tool inventory."""

    round: ToolRound
    tools_json: str = field(repr=False)

    def __init__(self, *, round: ToolRound, tools: Sequence[dict[str, Any]]) -> None:
        if not isinstance(round, ToolRound):
            raise _bad("invalid captured round")
        object.__setattr__(self, "round", round)
        object.__setattr__(self, "tools_json", _dump({"tools": _definitions(tools)}))


def _structure(value: Any, depth: int = 0) -> str:
    """Key names and JSON types only -- never a value, argument or content.

    This is what an unsupported shape reports, so the next failure is
    diagnosable from the served-turn log without copying model output into it.
    """
    if isinstance(value, dict):
        if depth >= 2:
            return "{...}"
        labels = [
            (key if isinstance(key, str) and _KEY.fullmatch(key) else "?")
            + ":" + _structure(item, depth + 1)
            for key, item in list(value.items())[:6]
        ]
        return "{" + ",".join(labels + (["..."] if len(value) > 6 else [])) + "}"
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if value is None:
        return "null"
    if isinstance(value, str):
        return "str" if value else "empty_str"
    return {bool: "bool", int: "int", float: "float"}.get(type(value), "other")


def _unsupported(position: int, item: Any) -> ProtocolDecodeError:
    shape = _structure(item)
    if len(shape) > _STRUCTURE_LIMIT:
        shape = shape[: _STRUCTURE_LIMIT - 3] + "..."
    return _bad(f"unsupported tool call shape: tool_calls[{position}]={shape}")


def _arguments(value: Any, position: int, item: Any) -> str:
    """The standard argument spellings, canonicalized to one JSON object string.

    A string is kept byte-exact once it parses as an object; an object (sent by
    some OpenAI-compatible servers) is serialized; absent, null or blank means
    a call with no arguments. Anything else is not a tool call we can run.
    """
    if value is None:
        return "{}"
    if isinstance(value, dict):
        return _dump(_object(_dump(value)))
    if not isinstance(value, str):
        raise _unsupported(position, item)
    if not value.strip() or value.strip() == "null":
        return "{}"
    _object(value)
    return value


def _synthetic_id(position: int, name: str, arguments: str) -> str:
    """Stable for one reply: the same batch always yields the same identity."""
    material = "\x00".join((str(position), name, arguments))
    return "call_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _normalize_calls(raw: Any) -> list[dict[str, Any]]:
    """Canonical ``{id, type, function: {name, arguments}}`` tool calls.

    One vendor-neutral tolerance of the OpenAI chat-completions wire, not a
    per-model branch: ``index`` and other extra keys are dropped, a missing
    ``type`` is ``function``, a missing or blank ``id`` is synthesized, and
    ``arguments`` may be a string, an object, or absent. Idempotent, so a
    stored continuation re-validates to itself.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _bad("tool calls must be a list")
    result = []
    for position, item in enumerate(raw):
        if not isinstance(item, dict):
            raise _unsupported(position, item)
        fn = item.get("function")
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
            raise _unsupported(position, item)
        if item.get("type") not in (None, "function"):
            raise _unsupported(position, item)
        name = fn["name"]
        arguments = _arguments(fn.get("arguments"), position, item)
        call_id = item.get("id")
        if call_id is None or (isinstance(call_id, str) and not call_id.strip()):
            call_id = _synthetic_id(position, name, arguments)
        result.append({"id": call_id, "type": "function", "function": {
            "name": name, "arguments": arguments,
        }})
    return result


def _calls(raw: Any, names: frozenset[str]) -> tuple[ToolRequest, ...]:
    calls: list[ToolRequest] = []
    seen: set[str] = set()
    for item in _normalize_calls(raw):
        call_id = item["id"]
        if not _identifier(call_id) or call_id in seen:
            raise _bad("invalid or duplicate tool call identity")
        name, arguments = item["function"]["name"], item["function"]["arguments"]
        if not _NAME.fullmatch(name) or name not in names:
            raise _bad("tool name is not enabled")
        _object(arguments)
        calls.append(ToolRequest(call_id, name, arguments))
        seen.add(call_id)
    return tuple(calls)


def _assistant(message: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...], bool]:
    if message.get("role", "assistant") != "assistant":
        raise _bad("response message is not assistant")
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise _bad("non-text assistant content is unsupported")
    result = {"role": "assistant", "content": content}
    raw_calls = message.get("tool_calls")
    legacy = message.get("function_call")
    # The legacy single ``function_call`` is the same request in its older
    # spelling; it becomes one standard tool call and leaves the dropped set.
    converted = not raw_calls and isinstance(legacy, dict) and bool(legacy)
    if converted:
        raw_calls = [{"type": "function", "function": legacy}]
    if raw_calls:
        result["tool_calls"] = _normalize_calls(raw_calls)
    for name in ("reasoning", "reasoning_details"):
        value = message.get(name)
        if value is None:
            continue
        if name == "reasoning" and not isinstance(value, str):
            raise _bad("invalid reasoning continuation")
        if name == "reasoning_details" and (
            not isinstance(value, list) or any(not isinstance(item, dict) for item in value)
        ):
            raise _bad("invalid reasoning continuation")
        result[name] = value
    dropped = tuple(
        key for key in message
        if key not in _CONTINUATION and not (converted and key == "function_call")
    )
    incompatible = any(message[key] not in (None, "", [], {}) for key in dropped)
    _dump(result)
    return result, dropped, incompatible


def validate_reply_context(
    source_ref: str, requested_model: str, tool_names: frozenset[str],
) -> None:
    """Validate the source and captured tool inventory without a wire response."""
    if not _identifier(source_ref, 4096) or not _identifier(requested_model, 4096):
        raise _bad("source and requested model are required")
    if not isinstance(tool_names, frozenset) or any(
        not isinstance(name, str) or not _NAME.fullmatch(name) for name in tool_names
    ):
        raise _bad("invalid enabled tool names")


def reply_state(
    message: dict[str, Any], *, finish: str, calls: tuple[ToolRequest, ...],
    incompatible: bool,
) -> tuple[StopReason, str | None, str | None]:
    """State semantics shared by wire decoding and the version-one journal.

    Callers validate the message projection and complete call batch first. The
    journal keeps its historical finish labels; no HTTP envelope is required to
    validate those records and no held state gains execution authority here.
    """
    if finish == "error":
        raise _bad("choice unavailable")
    refusal = message.get("refusal")
    if refusal is not None and not isinstance(refusal, str):
        raise _bad("invalid refusal field")
    refusal = refusal if refusal and refusal.strip() else None
    content = message.get("content")
    text = content if isinstance(content, str) and content.strip() else None
    stop: StopReason = "unknown"
    if calls and finish == "length":
        raise _bad("tool batch is incomplete")
    if refusal is not None:
        stop, text = "refusal", None
    elif finish == "content_filter":
        stop = "content_filter"
    elif finish == "length":
        stop = "truncated"
    elif calls and finish in _TOOL_FINISHES and not incompatible:
        stop = "tool_requests"
    elif not calls and finish == "stop" and text is not None and not any(
        message.get(key) not in (None, "", [], {}) for key in ("function_call", "audio")
    ):
        stop = "completed"
    elif not calls and finish == "tool_calls":
        raise _bad("tool finish without tool requests")
    return stop, text, refusal


def decode_agent_message(
    message: Any, *, finish: Any, receipt: Any, input_tokens: Any, output_tokens: Any,
    source_ref: str, requested_model: str, tool_names: frozenset[str],
) -> AgentReply:
    """Validate the canonical assistant projection without an HTTP envelope."""
    validate_reply_context(source_ref, requested_model, tool_names)
    if finish == "error":
        raise _bad("choice unavailable")
    if not isinstance(message, dict):
        raise _bad("assistant message required")
    projection, dropped, incompatible = _assistant(message)
    calls = _calls(projection.get("tool_calls"), tool_names)
    finish = finish if isinstance(finish, str) else ""
    stop, text, refusal = reply_state(
        message, finish=finish, calls=calls, incompatible=incompatible,
    )
    def tokens(value: Any) -> int | None:
        return value if type(value) is int and value >= 0 else None

    return AgentReply(
        stop, text, refusal, calls if stop == "tool_requests" else (),
        _dump(projection), dropped, source_ref, requested_model, model_receipt(receipt),
        finish, tokens(input_tokens), tokens(output_tokens),
    )


def is_event_stream(body: Any) -> bool:
    """Whether a response body is server-sent events rather than one JSON object."""
    if not isinstance(body, str):
        return False
    head = body.lstrip()[:512]
    return head.startswith(("data:", ":", "event:")) and "data:" in body


def fold_chat_stream(body: str) -> dict[str, Any]:
    """Fold a streamed chat completion into the one-object response it describes.

    Standard ``chat.completion.chunk`` semantics: text deltas concatenate, and
    tool-call deltas accumulate by their ``index`` (a delta without one extends
    the call in progress unless it names a new ``id``). The last non-null
    ``finish_reason`` wins. The result is then decoded like any other reply, so
    folding grants nothing a non-streamed response would not.
    """
    if not is_event_stream(body):
        raise _bad("response is not an event stream")
    message: dict[str, Any] = {"role": "assistant"}
    text: dict[str, list[str]] = {}
    calls: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    legacy: dict[str, str] = {}
    folded: dict[str, Any] = {}
    finish = None
    done = False
    chunks = 0
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue  # comments, event names, ids and retry hints carry no content
        data = line[5:].strip()
        if data == "[DONE]":
            done = True
            continue
        if not data:
            continue
        chunk = _object(data)
        chunks += 1
        if chunk.get("error") is not None:
            return {"error": chunk["error"]}
        for key in ("model", "usage"):
            if chunk.get(key) is not None:
                folded[key] = chunk[key]
        choices = chunk.get("choices") or []
        if not isinstance(choices, list) or len(choices) > 1:
            raise _bad("exactly one streamed choice required")
        for choice in choices:
            if not isinstance(choice, dict):
                raise _bad("exactly one streamed choice required")
            if choice.get("finish_reason") is not None:
                finish = choice["finish_reason"]
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                raise _bad("streamed delta must be an object")
            for key in ("content", "reasoning", "refusal"):
                piece = delta.get(key)
                if piece is None:
                    continue
                if not isinstance(piece, str):
                    raise _bad("streamed text delta must be text")
                text.setdefault(key, []).append(piece)
            for key in ("name", "arguments"):
                piece = (delta.get("function_call") or {}).get(key)
                if isinstance(piece, str):
                    legacy[key] = legacy.get(key, "") + piece
            _fold_tool_deltas(delta.get("tool_calls"), calls, order)
    if not chunks:
        raise _bad("event stream carried no chunks")
    # A stream cut off at EOF (no finish reason, no [DONE]) is not a reply: a
    # partially received tool call must never be completed by defaulting.
    if finish is None and not done:
        raise _bad("event stream incomplete: no finish reason or [DONE]")
    # Absent arguments default to {} only in the documented non-streamed
    # spellings; a fold that received no argument text never received them.
    folded_arguments = [calls[index]["function"]["arguments"] for index in order]
    if legacy:
        folded_arguments.append(legacy.get("arguments", ""))
    if any(not arguments.strip() for arguments in folded_arguments):
        raise _bad("streamed tool call incomplete: no arguments received")
    for key, pieces in text.items():
        message[key] = "".join(pieces)
    message.setdefault("content", None)
    if order:
        message["tool_calls"] = [calls[index] for index in order]
    elif legacy:
        message["function_call"] = legacy
    folded["choices"] = [{"message": message, "finish_reason": finish}]
    return folded


def partial_stream_text(body: str) -> str:
    """The assistant text a CUT event stream had delivered, best effort.

    For the owner's notice when a stream stopped arriving: only ``content``
    deltas, in order, skipping any line that does not parse. Never a tool call
    -- a half-received call is not an action and is not offered as one.
    """
    pieces: list[str] = []
    for line in body.splitlines() if isinstance(body, str) else ():
        if not line.startswith("data:"):
            continue
        try:
            chunk = json.loads(line[5:].strip())
        except ValueError:
            continue
        for choice in (chunk.get("choices") or []) if isinstance(chunk, dict) else ():
            delta = choice.get("delta") if isinstance(choice, dict) else None
            piece = delta.get("content") if isinstance(delta, dict) else None
            if isinstance(piece, str):
                pieces.append(piece)
    return "".join(pieces)


def _fold_tool_deltas(
    deltas: Any, calls: dict[int, dict[str, Any]], order: list[int],
) -> None:
    if deltas is None:
        return
    if not isinstance(deltas, list):
        raise _bad("streamed tool calls must be a list")
    for delta in deltas:
        if not isinstance(delta, dict):
            raise _unsupported(len(order), delta)
        index = delta.get("index")
        if type(index) is not int or index < 0:
            new_id = delta.get("id")
            current = calls[order[-1]] if order else None
            starts_new = current is None or (
                isinstance(new_id, str) and new_id and new_id != current.get("id")
            )
            index = (max(order) + 1 if order else 0) if starts_new else order[-1]
        if index not in calls:
            calls[index] = {"type": "function", "function": {"name": "", "arguments": ""}}
            order.append(index)
        call = calls[index]
        if isinstance(delta.get("id"), str) and delta["id"]:
            call["id"] = delta["id"]
        if isinstance(delta.get("type"), str) and delta["type"]:
            call["type"] = delta["type"]
        fn = delta.get("function") or {}
        if not isinstance(fn, dict):
            raise _unsupported(len(order) - 1, delta)
        name = fn.get("name")
        if isinstance(name, str) and name and name != call["function"]["name"]:
            call["function"]["name"] += name
        arguments = fn.get("arguments")
        if isinstance(arguments, str):
            call["function"]["arguments"] += arguments
        elif isinstance(arguments, dict):
            call["function"]["arguments"] = _dump(arguments)


def _definitions(definitions: Any) -> tuple[dict[str, Any], ...]:
    if not _sequence(definitions) or not definitions:
        raise _bad("tool definitions required")
    seen: set[str] = set()
    result = []
    for definition in definitions:
        if not isinstance(definition, dict) or set(definition) != {"type", "function"}:
            raise _bad("unsupported tool definition")
        fn = definition["function"]
        if definition["type"] != "function" or not isinstance(fn, dict) or set(fn) != {
            "name", "description", "parameters",
        }:
            raise _bad("unsupported function definition")
        name, description, parameters = fn["name"], fn["description"], fn["parameters"]
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in seen:
            raise _bad("invalid or duplicate tool definition name")
        if not isinstance(description, str) or len(description) > 65536:
            raise _bad("tool description is unsupported")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise _bad("object tool schema required")
        result.append(_object(_dump(definition)))
        seen.add(name)
    return tuple(result)


def tool_definitions(tools: Sequence[Tool]) -> tuple[dict[str, Any], ...]:
    """Project schemas from the validated engine-client inventory, no strict flag."""
    if not _sequence(tools) or any(not isinstance(tool, Tool) for tool in tools):
        raise _bad("MCP tool inventory required")
    return _definitions(tuple({"type": "function", "function": {
        "name": tool.name,
        "description": tool.description if tool.description is not None else "",
        "parameters": tool.inputSchema,
    }} for tool in tools))


def _result_projection(raw: dict[str, Any]) -> dict[str, Any]:
    if set(raw) != {"content", "structuredContent", "isError"}:
        raise _bad("unsupported tool result envelope")
    if type(raw["isError"]) is not bool or (
        raw["structuredContent"] is not None and not isinstance(raw["structuredContent"], dict)
    ):
        raise _bad("invalid tool result envelope")
    if not isinstance(raw["content"], list) or any(
        not isinstance(block, dict) or block.get("type") != "text"
        or not isinstance(block.get("text"), str)
        or set(block) - {"type", "text", "annotations"}
        for block in raw["content"]
    ):
        raise _bad("non-text tool content is unsupported")
    return _object(_dump(raw))


#: What a text-only connection is told in place of an image block. The exact
#: result (image included) stays in the turn journal; only the model's view of
#: it is this line, so the agent knows it could not see it. Mapped ONCE, in
#: ``tool_outcome``; every later boundary (the body builder, history validation)
#: still accepts text only, so unprojected image bytes cannot reach a model.
IMAGE_NOT_SHOWN = "[image not shown: this model connection carries text only]"


def _presented(block: Any) -> Any:
    if isinstance(block, dict) and block.get("type") == "image":
        return {"type": "text", "text": IMAGE_NOT_SHOWN}
    return block


def tool_outcome(request: ToolRequest, result: CallToolResult) -> ToolOutcome:
    if not isinstance(request, ToolRequest) or not _identifier(request.call_id):
        raise _bad("tool request identity required")
    if not isinstance(result, CallToolResult):
        raise _bad("MCP tool result required")
    projected = {
        "content": [_presented(block.model_dump(
            mode="json", by_alias=True, exclude_none=True, include={"type", "text", "annotations"},
        )) for block in result.content],
        "structuredContent": result.structuredContent,
        "isError": result.isError,
    }
    return ToolOutcome(request.call_id, _dump(_result_projection(projected)), result.isError)


def build_agent_body(
    *, prompt: str, system: str, source_ref: str, model: str,
    tools: Sequence[dict[str, Any]], rounds: Sequence[ToolRound] = (),
    temperature: float | None = None, max_tokens: int | None = None,
    tool_choice: Literal["auto", "none", "required"] = "auto",
) -> dict[str, Any]:
    """Build a fresh same-source request from fully completed immutable rounds."""
    if not isinstance(prompt, str) or not isinstance(system, str):
        raise _bad("prompt and system must be text")
    if not _identifier(source_ref, 4096) or not _identifier(model, 4096):
        raise _bad("source and model are required")
    if tool_choice not in ("auto", "none", "required"):
        raise _bad("unsupported tool choice")
    definitions = _definitions(tools)
    names = frozenset(item["function"]["name"] for item in definitions)
    if not _sequence(rounds):
        raise _bad("completed rounds required")
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    seen: set[str] = set()
    for completed in rounds:
        if not isinstance(completed, ToolRound) or not isinstance(completed.reply, AgentReply):
            raise _bad("invalid completed round")
        reply = completed.reply
        if reply.stop != "tool_requests" or (reply.source_ref, reply.requested_model) != (
            source_ref, model,
        ):
            raise _bad("continuation source or state mismatch")
        assistant = _object(reply.continuation_json)
        projection, dropped, _ = _assistant(assistant)
        requests = _calls(assistant.get("tool_calls"), names)
        if dropped or not requests or requests != reply.tool_requests:
            raise _bad("continuation does not match tool requests")
        if not isinstance(completed.outcomes, tuple) or len(requests) != len(completed.outcomes):
            raise _bad("tool result count mismatch")
        messages.append(projection)
        for request, outcome in zip(requests, completed.outcomes):
            if request.call_id in seen or not isinstance(outcome, ToolOutcome) or (
                outcome.call_id != request.call_id or type(outcome.is_error) is not bool
            ):
                raise _bad("tool result correlation mismatch")
            result = _result_projection(_object(outcome.result_json))
            if result["isError"] != outcome.is_error:
                raise _bad("tool result error flag mismatch")
            messages.append({"role": "tool", "tool_call_id": outcome.call_id,
                             "content": outcome.result_json})
            seen.add(request.call_id)
    body: dict[str, Any] = {
        "model": model, "messages": messages, "tools": list(definitions),
        "tool_choice": tool_choice,
    }
    if temperature is not None:
        try:
            valid_temperature = type(temperature) in (float, int) and math.isfinite(temperature)
        except OverflowError:
            valid_temperature = False
        if not valid_temperature:
            raise _bad("invalid temperature")
        body["temperature"] = temperature
    if max_tokens is not None:
        if type(max_tokens) is not int or max_tokens < 1:
            raise _bad("invalid output token cap")
        body["max_tokens"] = max_tokens
    return body


def build_portable_agent_body(
    *, prompt: str, system: str, source_ref: str, model: str,
    tools: Sequence[dict[str, Any]], history: Sequence[CapturedToolRound] = (),
    temperature: float | None = None, max_tokens: int | None = None,
    tool_choice: Literal["auto", "none", "required"] = "auto",
) -> dict[str, Any]:
    """Render known results across selections without carrying foreign reasoning.

    Historical inventories validate historical calls, never today's permission
    to execute them. Wire identities are local to each completed tool batch.
    The caller must still admit every new inference and tool dispatch.
    """
    body = build_agent_body(
        prompt=prompt, system=system, source_ref=source_ref, model=model,
        tools=tools, temperature=temperature, max_tokens=max_tokens,
        tool_choice=tool_choice,
    )
    body["messages"].extend(project_completed_history(history, source_ref=source_ref, model=model))
    return body


def project_completed_history(
    history: Sequence[CapturedToolRound], *, source_ref: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Validate exact completed batches without requiring a new model or wire.

    An absent destination strips all private reasoning, including for native
    agents. Each old batch is still checked against its own captured inventory.
    No historical tool becomes permission to execute it again.
    """
    if (source_ref is None) != (model is None):
        raise _bad("incomplete history destination")
    result = []
    if not _sequence(history):
        raise _bad("captured completed history required")
    for captured in history:
        if not isinstance(captured, CapturedToolRound) or not isinstance(
            captured.round.reply, AgentReply,
        ):
            raise _bad("invalid captured history")
        reply = captured.round.reply
        # Reuse the strict single-source validator for ONE historical batch.
        # It checks completeness, exact arguments/results and wire correlation;
        # its per-request ID set must not span independent historical rounds.
        historical = build_agent_body(
            prompt="", system="", source_ref=reply.source_ref,
            model=reply.requested_model,
            tools=_object(captured.tools_json).get("tools"),
            rounds=(captured.round,),
        )
        messages = historical["messages"][1:]
        if (reply.source_ref, reply.requested_model) != (source_ref, model):
            messages[0] = {
                key: value for key, value in messages[0].items()
                if key in {"role", "content", "tool_calls"}
            }
        result.extend(messages)
    return result


#: ``(rounds kept whole at the end, result characters, argument-string
#: characters)`` per compaction level; index 0 is level 1. Older rounds only,
#: until level 3 trims the last round too.
COMPACTION_LEVELS = ((2, 4000, 2000), (1, 1000, 300), (0, 400, 200))


def _clip(text: str, limit: int, what: str) -> str:
    if len(text) <= limit:
        return text
    head, tail = text[: limit * 2 // 3], text[len(text) - limit // 3:]
    return (f"{head}\n[... {len(text) - len(head) - len(tail)} characters of {what} "
            f"omitted here to fit the model's context window ...]\n{tail}")


def _clip_strings(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return _clip(value, limit, "this earlier call's argument (it ran with them in full)")
    if isinstance(value, dict):
        return {key: _clip_strings(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_clip_strings(item, limit) for item in value]
    return value


def _compact_round(captured: CapturedToolRound, results: int, arguments: int):
    reply, outcomes = captured.round.reply, captured.round.outcomes
    clipped = {
        request.call_id: _dump(_clip_strings(request.arguments(), arguments))
        for request in reply.tool_requests
    }
    assistant = _object(reply.continuation_json)
    # Older reasoning is the model's scratch work for a step already taken.
    for name in ("reasoning", "reasoning_details"):
        assistant.pop(name, None)
    for call in assistant.get("tool_calls") or ():
        call["function"]["arguments"] = clipped[call["id"]]
    compact_reply = replace(
        reply, continuation_json=_dump(assistant),
        tool_requests=tuple(
            ToolRequest(item.call_id, item.name, clipped[item.call_id])
            for item in reply.tool_requests
        ),
    )
    compact_outcomes = []
    for outcome in outcomes:
        result = _object(outcome.result_json)
        text = "\n".join(block["text"] for block in result["content"])
        if result["structuredContent"] is not None:
            text = (text + "\n" if text else "") + _dump(result["structuredContent"])
        if len(outcome.result_json) > results:
            outcome = ToolOutcome(outcome.call_id, _dump({
                "content": [{"type": "text", "text": _clip(
                    text, results,
                    "this earlier tool result (unchanged on record; call the tool again "
                    "to read it whole)",
                )}],
                "structuredContent": None, "isError": outcome.is_error,
            }), outcome.is_error)
        compact_outcomes.append(outcome)
    return CapturedToolRound(
        round=ToolRound(compact_reply, tuple(compact_outcomes)),
        tools=_object(captured.tools_json)["tools"],
    )


def compact_history(
    history: Sequence[CapturedToolRound], level: int,
) -> tuple[CapturedToolRound, ...]:
    """The same completed rounds, rendered shorter for a small context window.

    Level 0 is the history unchanged. Each higher level clips older tool
    results and older call arguments harder, every clip saying what it left
    out. Only what the model is SHOWN changes: the journal keeps every round
    whole, and a clipped round still validates as a complete, correlated batch.
    """
    history = tuple(history)
    if level <= 0 or not history:
        return history
    keep, results, arguments = COMPACTION_LEVELS[min(level, len(COMPACTION_LEVELS)) - 1]
    cut = len(history) - keep
    return tuple(
        _compact_round(captured, results, arguments) if position < cut else captured
        for position, captured in enumerate(history)
    )


def history_size(history: Sequence[CapturedToolRound]) -> int:
    """Characters a history renders as: replies plus results."""
    return sum(
        len(captured.round.reply.continuation_json)
        + sum(len(outcome.result_json) for outcome in captured.round.outcomes)
        for captured in history
    )


def encode_openai_chat_agent(**kwargs) -> tuple[str, dict[str, Any]]:
    """Historical import compatibility; the engine uses the installed adapter."""
    from tinyassets.providers.agent_wire_codec import installed_agent_wire

    return installed_agent_wire().wrap_body(build_agent_body(**kwargs))


def encode_openai_chat_agent_portable(**kwargs) -> tuple[str, dict[str, Any]]:
    """Historical import compatibility for callers with captured history."""
    from tinyassets.providers.agent_wire_codec import installed_agent_wire

    return installed_agent_wire().encode(**kwargs)


def decode_openai_chat_agent(response_body: Any, **kwargs) -> AgentReply:
    """Historical import compatibility for the installed message envelope."""
    from tinyassets.providers.agent_wire_codec import installed_agent_wire

    return installed_agent_wire().decode(response_body, **kwargs)


def validate_agent_body(body: dict[str, Any]) -> None:
    """Validate only the portable encoder's wire shape, without granting tools.

    Historical tool names need not be in today's inventory. Every historical
    batch must still be complete and correlated, with exact known result data.
    """
    if not isinstance(body, dict) or set(body) - {
        "model", "messages", "tools", "tool_choice", "temperature", "max_tokens",
    }:
        raise _bad("unsupported request fields")
    if body.get("tool_choice") not in ("auto", "none", "required"):
        raise _bad("unsupported tool choice")
    build_agent_body(
        prompt="", system="", source_ref="validation", model=body.get("model"),
        tools=body.get("tools"), temperature=body.get("temperature"),
        max_tokens=body.get("max_tokens"), tool_choice=body["tool_choice"],
    )
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages or not isinstance(messages[0], dict):
        raise _bad("request messages required")
    offset = 0
    for role in ("system", "user"):
        if role == "system" and messages[0].get("role") != "system":
            continue
        if offset >= len(messages):
            raise _bad("user message required")
        message = messages[offset]
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] != role or not isinstance(message["content"], str)):
            raise _bad("invalid initial message")
        offset += 1
    while offset < len(messages):
        message = messages[offset]
        if not isinstance(message, dict) or not {"role", "content"} <= message.keys():
            raise _bad("invalid historical assistant")
        _, dropped, _ = _assistant(message)
        if dropped:
            raise _bad("unsupported historical assistant fields")
        raw_calls = message.get("tool_calls")
        if not isinstance(raw_calls, list) or not raw_calls:
            raise _bad("historical tool batch required")
        names = frozenset(
            item["function"]["name"] for item in raw_calls
            if isinstance(item, dict) and isinstance(item.get("function"), dict)
            and isinstance(item["function"].get("name"), str)
        )
        requests = _calls(raw_calls, names)
        offset += 1
        for request in requests:
            if offset >= len(messages):
                raise _bad("incomplete historical tool results")
            result = messages[offset]
            if (not isinstance(result, dict)
                    or set(result) != {"role", "tool_call_id", "content"}
                    or result["role"] != "tool" or result["tool_call_id"] != request.call_id):
                raise _bad("historical tool result correlation mismatch")
            _result_projection(_object(result["content"]))
            offset += 1
