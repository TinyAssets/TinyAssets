"""Verified extension bytes staged for one owner command."""
import base64
import os
import re

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def stage(root, extensions):
    """Host-verified revision bytes, read-only for this one launch; no host paths."""
    for item in extensions:
        if not _NAME.fullmatch(item["name"]) or not _DIGEST.fullmatch(item["revision"]):
            raise ValueError("invalid extension delivery")
        package = root / item["name"] / item["revision"]
        for path, data in item["files"].items():
            parts = path.split("/")
            if path.startswith("/") or any(part in ("", ".", "..") for part in parts):
                raise ValueError("invalid extension path")
            target = package.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(data, validate=True))
            target.chmod(0o555)
    for directory, _, _ in sorted(os.walk(root), reverse=True):
        os.chmod(directory, 0o555)


def unstage(root):
    for directory, _, _ in os.walk(root):
        os.chmod(directory, 0o700)


