"""Shared immutable subject identity for canonical execution owners."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ExecutionSubjectKind(str, Enum):
    BRANCH_VERSION = "branch_version"


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _digest(value: object, name: str) -> str:
    value = _required(value, name)
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a canonical sha256 digest")
    return value


@dataclass(frozen=True, slots=True)
class ExecutionSubject:
    kind: ExecutionSubjectKind
    ref: str
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ExecutionSubjectKind):
            raise ValueError("kind must be typed")
        _required(self.ref, "ref")
        _digest(self.digest, "digest")

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ExecutionSubject:
        if not isinstance(value, Mapping) or set(value) != {"kind", "ref", "digest"}:
            raise ValueError("execution subject must have exact kind/ref/digest fields")
        try:
            kind = ExecutionSubjectKind(value["kind"])
        except (TypeError, ValueError) as exc:
            raise ValueError("kind must be a supported execution subject kind") from exc
        return cls(
            kind=kind,
            ref=_required(value["ref"], "ref"),
            digest=_digest(value["digest"], "digest"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "ref": self.ref,
            "digest": self.digest,
        }


__all__ = [
    "ExecutionSubject",
    "ExecutionSubjectKind",
]
