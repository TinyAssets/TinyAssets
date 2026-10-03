"""Immutable, authority-free data for exactly one HTTP agent inference."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tinyassets.providers import agent_chat_codec as codec
from tinyassets.providers.discovery_execution import UsageShape
from tinyassets.providers.discovery_presets import compatibility_document

_LEGACY_USAGE = UsageShape.compile(compatibility_document()["usage"], legacy=True)

if TYPE_CHECKING:
    from tinyassets.providers.model_selection import SelectedModel


@dataclass(frozen=True, slots=True, init=False)
class AgentInferenceRequest:
    tools_json: str = field(repr=False)
    history: tuple[codec.CapturedToolRound, ...] = field(repr=False)
    tool_choice: str

    def __init__(self, *, tools, history=(), tool_choice="auto") -> None:
        if not isinstance(history, tuple) or any(
            not isinstance(item, codec.CapturedToolRound) for item in history
        ):
            raise ValueError("captured immutable agent history required")
        object.__setattr__(self, "tools_json", codec._dump({"tools": codec._definitions(tools)}))
        object.__setattr__(self, "history", history)
        if tool_choice not in {"auto", "none", "required"}:
            raise ValueError("invalid agent tool choice")
        object.__setattr__(self, "tool_choice", tool_choice)

    def tools(self) -> tuple[dict[str, Any], ...]:
        return codec._definitions(codec._object(self.tools_json)["tools"])

    def encode(
        self,
        *,
        prompt: str,
        system: str,
        selection: SelectedModel,
        temperature: float | None,
        max_tokens: int | None,
    ) -> tuple[str, dict[str, Any]]:
        from tinyassets.providers.protocol_encoders import agent_codec_for

        if selection is None or not selection.supports_tools:
            raise PermissionError("selected model lacks admitted agent tool support")
        contract = selection.contract()
        agent_codec = agent_codec_for(contract.inference_protocol)
        if agent_codec is None:
            raise PermissionError("agent inference protocol is unsupported")
        path, body = agent_codec.encode(
            prompt=prompt,
            system=system,
            source_ref=selection.provider,
            model=selection.model_id,
            tools=self.tools(),
            history=self.history,
            tool_choice=self.tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        # Streamed, so the broker can judge the reply by inactivity, never by
        # length. Added after the contract's own constraint (which knows only the
        # canonical fields) and before anything hashes or measures the body, so
        # the journal records the request exactly as sent.
        return path, {**contract.constrain_inference(body, selection.cost_caps), "stream": True}


def _encoded(prompt, system, config) -> bytes:
    """The request exactly as the broker encodes it, every wire field included."""
    request = config.agent_request
    if request is None:
        return (f"{system}\n\n{prompt}" if system else prompt).encode("utf-8")
    if type(request) is not AgentInferenceRequest:
        raise PermissionError("invalid internal agent inference request")
    _, body = request.encode(
        prompt=prompt,
        system=system,
        selection=config.selected_model,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )
    # Use the broker's ordinary JSON encoding, including every wire field.
    return json.dumps(body).encode("utf-8")


def input_size(prompt, system, config) -> int:
    """Conservative byte estimate shared by context checks and reservation."""
    return len(_encoded(prompt, system, config))


#: Encoded request bytes per token, for fitting a model's WINDOW only. Measured
#: 2026-10-02 on production first rounds (prompt, system and the served tool
#: schemas, as sent): 3.86-4.07 bytes per reported input token across nemotron,
#: qwen and ling. Three keeps a quarter of margin under the lowest. Comparing
#: raw bytes against the window made a 131k-token model "overflow" at ~37k real
#: tokens (turn 8dc8ada5). Reservation keeps the byte measure: that is money.
CONTEXT_BYTES_PER_TOKEN = 3
#: Three content classes tokenize far denser than prose, so each is counted on
#: its own (cl100k/o200k, measured 2026-10-02 on what the model sees):
#:
#: * base64/hex-like runs, wrapped or not: 1.39 characters per token for base64,
#:   1.76 for hex -> counted at 1.25 (Codex: a 40k base64 tool result, plain or
#:   wrapped at 60 columns, estimated at 13.6-14k tokens against 28.6-29.6k);
#: * JSON's ``\uXXXX`` escapes of non-ASCII text (emoji are two): 1.6 tokens each;
#: * punctuation-dense code (minified JS ran 1.95 bytes per token): never fewer
#:   than 1.1 tokens per punctuation character.
#:
#: Against prose, Python, HTML, minified JS, base64, hex, JSON schemas, CJK and
#: emoji this estimate is 1.04-1.72x the larger tokenizer's count.
DENSE_CHARS_PER_TOKEN = 1.25
ESCAPE_TOKENS = 1.6
PUNCT_TOKENS = 1.1
_DENSE_RUN = re.compile(rb"(?:[A-Za-z0-9+/=_-]{16,}(?:\\+[nr]|\s)?){4,}")
_ESCAPE = re.compile(rb"\\+u[0-9a-fA-F]{4}")
_PUNCT = re.compile(rb"[^A-Za-z0-9\s\\]")


def context_tokens(prompt, system, config) -> int:
    """Conservative token estimate of the encoded request, for the window fit."""
    return estimate_tokens(_encoded(prompt, system, config))


def estimate_tokens(data: bytes) -> int:
    """Tokens a request's encoded bytes may cost, erring high."""
    dense = sum(len(run) for run in _DENSE_RUN.findall(data))
    escapes = len(_ESCAPE.findall(data))
    rest = _DENSE_RUN.sub(b"", _ESCAPE.sub(b"", data))
    general = max(-(-len(rest) // CONTEXT_BYTES_PER_TOKEN),
                  math.ceil(len(_PUNCT.findall(rest)) * PUNCT_TOKENS))
    return (general + math.ceil(dense / DENSE_CHARS_PER_TOKEN)
            + math.ceil(escapes * ESCAPE_TOKENS))


def output_for_settlement(response) -> str:
    """Missing usage counts tool calls/reasoning too, not an empty text result."""
    if response.agent_reply is None:
        return response.text
    return json.dumps(
        {
            "message": codec._object(response.agent_reply.continuation_json),
            "refusal": response.agent_reply.refusal,
        }
    )


def openrouter_usage_cost(raw_json: str) -> int | None:
    """Compatibility name for the shared exact usage interpreter."""
    return _LEGACY_USAGE.decode(raw_json)
