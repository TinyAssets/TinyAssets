"""Bounded JSON messages for the owner file service; no Python deserialization."""

from __future__ import annotations

import base64
import dataclasses
import enum
import json

from tinyassets.boxes.provider import BoxError

MAX_DATA = 4 * 1024 * 1024
MAX_MESSAGE = 8 * 1024 * 1024


def encode(value: object) -> bytes:
    def default(item):
        if isinstance(item, bytes):
            return {"$bytes": base64.b64encode(item).decode("ascii")}
        if dataclasses.is_dataclass(item):
            return dataclasses.asdict(item)
        if isinstance(item, enum.Enum):
            return item.value
        raise TypeError("unsupported box message value")

    body = json.dumps(value, default=default, allow_nan=False).encode("utf-8")
    if len(body) > MAX_MESSAGE:
        raise BoxError("box message exceeds the transport bound")
    return body


def decode(body: bytes) -> dict:
    if len(body) > MAX_MESSAGE:
        raise BoxError("box message exceeds the transport bound")

    def object_hook(item):
        if "$bytes" in item:
            if set(item) != {"$bytes"} or not isinstance(item["$bytes"], str):
                raise ValueError("invalid byte envelope")
            return base64.b64decode(item["$bytes"], validate=True)
        return item

    try:
        result = json.loads(body, object_hook=object_hook,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, RecursionError) as error:
        raise BoxError("invalid box response") from error
    if not isinstance(result, dict):
        raise BoxError("box message must be an object")
    return result
