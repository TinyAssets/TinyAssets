"""Only shipped, digest-pinned package revisions are executable before the box."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from tinyassets.extension_manifest import parse_manifest

_ROOT = Path(__file__).with_name("packages")


@dataclass(frozen=True, slots=True)
class Package:
    row: dict
    digest: str

    @property
    def name(self) -> str:
        return self.row["name"]

    @property
    def install_root(self) -> Path:
        return Path("/opt") / f"{self.name}-install"

    @property
    def executable(self) -> Path:
        install = self.row["install"]
        return self.install_root / "node_modules" / ".bin" / install["bin"]


def _load() -> dict[str, Package]:
    result = {}
    for directory in sorted(_ROOT.iterdir()):
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError("invalid shipped external-agent directory")
        raw = (directory / "extension.json").read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        pin = (directory / "sha256").read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[a-f0-9]{64}", pin) or digest != pin:
            raise ValueError("external-agent package digest differs from shipped pin")
        manifest = parse_manifest(raw)
        rows = manifest.get("external_agents", [])
        if (len(rows) != 1 or manifest["name"] != directory.name
                or rows[0]["name"] != directory.name):
            raise ValueError("external-agent package name differs from its directory")
        result[directory.name] = Package(rows[0], digest)
    return result


PACKAGES = _load()


def resolve(name: str) -> Package:
    try:
        return PACKAGES[name]
    except KeyError:
        raise LookupError("external agent is not shipped") from None
