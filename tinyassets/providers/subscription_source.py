"""Editable subscription source data; no vendor logic or credentials."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

SOURCES = Path(__file__).with_name("subscription_sources")
_ID = re.compile(r"[a-z0-9][a-z0-9-]{1,63}\Z")
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\Z")
_VERSION = re.compile(r"\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?\Z")


def relative(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("source path must be HOME-relative")
    return value


def validate(doc):
    if not isinstance(doc, dict) or not _ID.fullmatch(doc.get("id", "")):
        raise ValueError("invalid subscription source id")
    if not isinstance(doc.get("display_name"), str) or not doc["display_name"].strip():
        raise ValueError("source display name required")
    install = doc["install"]
    if install["manager"] != "npm" or not _VERSION.fullmatch(install["version"]):
        raise ValueError("source requires a pinned npm version")
    if not re.fullmatch(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+", install["package"]):
        raise ValueError("invalid npm package")
    if not re.fullmatch(r"sha512-[A-Za-z0-9+/]+={0,2}", install["integrity"]):
        raise ValueError("source package integrity required")
    relative(install["bin"])
    if "archive" in install:
        relative(install["archive"])
    for phase in ("registry", "provider", "login"):
        hosts = doc["egress"][phase]
        if not isinstance(hosts, list) or not hosts:
            raise ValueError("source egress hosts required")
        if any(not isinstance(h, str) or not _HOST.fullmatch(h) or ".." in h for h in hosts):
            raise ValueError("source egress requires exact host names")
    for path in doc["state_paths"]:
        relative(path)
    if not doc["state_paths"]:
        raise ValueError("native login state paths required")
    for argv in (doc["login"]["argv"], doc["login"]["status_argv"], doc["invoke"]["argv"]):
        if (
            not isinstance(argv, list)
            or not argv
            or argv[0] != "{bin}"
            or any(not isinstance(a, str) or "\0" in a for a in argv)
        ):
            raise ValueError("source argv must invoke its installed binary")
    for key in ("url_pattern", "code_pattern"):
        if doc["login"].get(key):
            re.compile(doc["login"][key])
    if doc["invoke"]["format"] not in {"json", "jsonl", "text"}:
        raise ValueError("unknown CLI output format")
    for key, value in doc.get("env", {}).items():
        if (
            not re.fullmatch(r"[A-Z][A-Z0-9_]+", key)
            or not isinstance(value, str)
            or key in {"HOME", "PATH", "NODE_OPTIONS", "PYTHONPATH", "LD_PRELOAD"}
            or "PROXY" in key
            or key.startswith("NPM_")
        ):
            raise ValueError("source environment cannot override execution transport")
    return doc


def sources(home: Path | None = None):
    result = {}
    directories = [SOURCES]
    if home is not None:
        directories.append(home / ".ta-cli" / "sources")
    for directory in directories:
        for path in sorted(directory.glob("*.json")):
            doc = validate(json.loads(path.read_text("utf-8")))
            if path.stem != doc["id"]:
                raise ValueError("source filename must match its id")
            result[doc["id"]] = doc
    return result


def digest(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()


def select(value, path):
    for part in path.split(".") if path else ():
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value
