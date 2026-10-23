"""Ordered model output parts, normalized from a source media contract.

The contract is data the connected source declared or that discovery recorded.
This module does not recognize a vendor wire. Malformed or unsupported parts
fail with a typed outcome; they are not dropped and not shown as text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tinyassets.storage.owner_media import OwnerMediaStore

_KINDS = frozenset({"text", "tool_call", "image", "audio", "video"})
_MEDIA = frozenset({"image", "audio", "video"})
_MIME = re.compile(r"^(?:text|image|audio|video)/[a-z0-9.+-]{1,64}$")
_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,80}")


class ModelMediaError(Exception):
    """A part the source contract does not allow, or a part that is not well formed."""

    def __init__(self, outcome: str, committed: tuple = (), *, detail: str = ""):
        if outcome not in {"malformed", "unsupported"}:
            raise ValueError("unknown media failure")
        self.outcome = outcome
        self.committed = tuple(committed)
        self.detail = detail
        self.result = None
        super().__init__(outcome if not detail else f"{outcome}: {detail}")


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str
    kind: str = "text"


@dataclass(frozen=True, slots=True)
class ToolCallPart:
    name: str
    arguments: dict
    kind: str = "tool_call"


@dataclass(frozen=True, slots=True)
class MediaPart:
    kind: str
    mime: str
    byte_length: int
    digest: str
    reference: str
    timing: tuple[int, int] | None = None


def contract_kinds(contract: object) -> dict:
    """The source's media map. ``{"media": {kind: {"mime": [...]}}}`` or the map itself."""
    if not isinstance(contract, dict):
        raise ModelMediaError("malformed", detail="media contract is not an object")
    media = contract.get("media", contract)
    if not isinstance(media, dict) or not media:
        raise ModelMediaError("malformed", detail="media contract is empty")
    if any(kind not in _KINDS or not isinstance(spec, dict) for kind, spec in media.items()):
        raise ModelMediaError("unsupported", detail="media contract names an unknown part")
    return media


def _mime_allowed(spec: dict, mime: object) -> bool:
    allowed = spec.get("mime")
    if not isinstance(allowed, list) or not allowed:
        return False
    return isinstance(mime, str) and mime in allowed and _MIME.fullmatch(mime) is not None


def _timing(item: dict) -> tuple[int, int] | None:
    if "timing" not in item:
        return None
    timing = item["timing"]
    if (not isinstance(timing, (list, tuple)) or len(timing) != 2
            or any(type(value) is not int or value < 0 for value in timing)
            or timing[1] < timing[0]):
        raise ModelMediaError("malformed", detail="video timing is invalid")
    return int(timing[0]), int(timing[1])


def normalize_parts(contract: object, payload: object, *, store: OwnerMediaStore,
                    owner: str, command_center: str) -> tuple[tuple, str]:
    """Commit complete media, then return ``(parts, outcome)``.

    Partial frames (``complete`` false) stay ephemeral and get no reference.
    ``payload`` is a list of parts or ``{"parts": [...], "outcome": ...}``.
    """
    kinds = contract_kinds(contract)
    if isinstance(payload, dict):
        items = payload.get("parts")
        stream = payload.get("outcome", "succeeded")
    else:
        items = payload
        stream = "succeeded"
    if stream not in {"succeeded", "failed", "incomplete", "cancelled"}:
        raise ModelMediaError("malformed", detail="stream outcome is invalid")
    if not isinstance(items, list):
        raise ModelMediaError("malformed", detail="parts are not a list")
    committed: list = []
    try:
        for item in items:
            if not isinstance(item, dict) or item.get("kind") not in _KINDS:
                raise ModelMediaError("malformed", detail="part kind is missing")
            kind = item["kind"]
            if kind not in kinds:
                raise ModelMediaError("unsupported", detail=kind)
            if kind == "text":
                text = item.get("text")
                if not isinstance(text, str):
                    raise ModelMediaError("malformed", detail="text part has no text")
                committed.append(TextPart(text))
                continue
            if kind == "tool_call":
                name = item.get("name")
                arguments = item.get("arguments")
                if not isinstance(name, str) or _NAME.fullmatch(name) is None:
                    raise ModelMediaError("malformed", detail="tool name is invalid")
                if not isinstance(arguments, dict):
                    raise ModelMediaError("malformed", detail="tool arguments are invalid")
                committed.append(ToolCallPart(name, dict(arguments)))
                continue
            complete = item.get("complete", True)
            if complete is False:
                continue
            if complete is not True:
                raise ModelMediaError("malformed", detail="complete flag is invalid")
            mime = item.get("mime")
            data = item.get("data")
            if not _mime_allowed(kinds[kind], mime) or not isinstance(data, (bytes, bytearray)):
                raise ModelMediaError("malformed", detail="media bytes or mime are invalid")
            if not data:
                raise ModelMediaError("malformed", detail="media part is empty")
            timing = _timing(item) if kind == "video" else None
            if kind != "video" and "timing" in item:
                raise ModelMediaError("malformed", detail="only video carries timing")
            reference = store.commit(owner, command_center, bytes(data))
            digest = reference.rsplit(":", 1)[-1]
            committed.append(MediaPart(
                kind, str(mime), len(data), digest, reference, timing,
            ))
    except ModelMediaError as exc:
        if exc.committed:
            raise
        raise ModelMediaError(exc.outcome, tuple(committed), detail=exc.detail) from None
    outcome = {"failed": "incomplete", "incomplete": "incomplete",
               "cancelled": "cancelled"}.get(stream, "succeeded")
    return tuple(committed), outcome


def part_reference(index: int, part: object) -> str:
    reference = getattr(part, "reference", "")
    if reference:
        return reference
    return f"part:{index}:{getattr(part, 'kind', 'text')}"


def media_link(parts: tuple, outcome: str, meter: dict | None = None, *,
               unknown: bool = False) -> dict:
    meter = meter or {}
    link: dict = {
        "part_refs": [part_reference(index, part) for index, part in enumerate(parts)],
        "outcome": outcome,
        "usage": "unknown" if unknown else "measured",
    }
    if unknown:
        return link
    output = meter.get("output_bytes")
    if type(output) is not int:
        output = sum(getattr(part, "byte_length", 0) for part in parts)
    incoming = meter.get("input_bytes", 0)
    if type(incoming) is not int or incoming < 0 or output < 0:
        raise ModelMediaError("malformed", parts, detail="media usage is invalid")
    link["input_bytes"] = incoming
    link["output_bytes"] = output
    return link
