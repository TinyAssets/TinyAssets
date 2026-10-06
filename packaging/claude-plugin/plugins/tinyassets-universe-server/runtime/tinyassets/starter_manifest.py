"""Immutable seed manifests. Content is data, never an authority grant."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def relative_path(value: str) -> str:
    """Accept only canonical portable paths, including agent-directory prefixes."""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("seed paths must be canonical relative paths")
    if any(
        part in ("", ".", "..") or part.endswith((".", " ")) for part in value.split("/")
    ) or any(ord(c) < 32 for c in value):
        raise ValueError("unsafe seed path")
    if any(part.startswith(".") for part in value.split("/")):
        raise ValueError("seeds cannot own hidden platform state")
    return value


@dataclass(frozen=True)
class SeedFile:
    path: str
    content: bytes
    predecessors: tuple[str, ...] = ()
    historically_seeded: bool = False

    def __post_init__(self):
        relative_path(self.path)
        if not isinstance(self.content, bytes) or len(self.content) > 1024 * 1024:
            raise ValueError("seed content must be bytes within the file-reader bound")
        if not isinstance(self.predecessors, tuple) or any(
            not re.fullmatch(r"[0-9a-f]{64}", h) for h in self.predecessors
        ):
            raise ValueError("predecessors must be immutable exact SHA-256 hashes")

    @property
    def sha256(self) -> str:
        return digest(self.content)


@dataclass(frozen=True)
class SeedManifest:
    bundle_id: str
    version: str
    files: tuple[SeedFile, ...]

    def __post_init__(self):
        for identifier in (self.bundle_id, self.version):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", identifier):
                raise ValueError("invalid seed bundle/version")
        if not isinstance(self.files, tuple) or not self.files:
            raise ValueError("manifest files must be a nonempty immutable tuple")
        paths = [f.path.casefold() for f in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("duplicate seed path")
        if any(a.startswith(b + "/") for a in paths for b in paths if a != b):
            raise ValueError("seed file is another seed's parent")

    @property
    def sha256(self) -> str:
        document = [
            self.bundle_id,
            self.version,
            [
                [f.path, f.sha256, f.predecessors, f.historically_seeded]
                for f in sorted(self.files, key=lambda f: f.path)
            ],
        ]
        return digest(json.dumps(document, separators=(",", ":")).encode())
