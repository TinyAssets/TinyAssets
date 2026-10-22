"""Editable subscription source data; no vendor logic or credentials."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
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
    if install["manager"] not in {"npm", "pip"} or not _VERSION.fullmatch(install["version"]):
        raise ValueError("source requires a pinned package version")
    if not re.fullmatch(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+", install["package"]):
        raise ValueError("invalid npm package")
    if install["manager"] == "npm" and not re.fullmatch(
        r"sha512-[A-Za-z0-9+/]+={0,2}", install["integrity"]
    ):
        raise ValueError("source package integrity required")
    if install["manager"] == "pip":
        requirements = install["requirements"]
        if not isinstance(requirements, list) or not requirements:
            raise ValueError("pip requires the complete hash-pinned dependency closure")
        for item in requirements:
            if (not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", item["package"])
                    or not _VERSION.fullmatch(item["version"])
                    or not item["hashes"]
                    or any(not re.fullmatch(r"sha256:[a-f0-9]{64}", h) for h in item["hashes"])):
                raise ValueError("pip requirement must have an exact version and SHA256")
        if not any(item["package"] == install["package"]
                   and item["version"] == install["version"] for item in requirements):
            raise ValueError("pip requirements must include the pinned source package")
    relative(install["bin"])
    if "archive" in install:
        relative(install["archive"])
    if "wheelhouse" in install:
        relative(install["wheelhouse"])
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
    if "models" in doc:
        if not isinstance(doc["models"], list) or not doc["models"]:
            raise ValueError("source models must be a nonempty declaration")
        for model in doc["models"]:
            if (not isinstance(model.get("id"), str) or not model["id"]
                    or type(model.get("context")) is not int or model["context"] <= 0):
                raise ValueError("model requires an id and positive context capacity")
    if "ranking" in doc:
        ranking = doc["ranking"]
        if (not isinstance(ranking["source"], str) or not ranking["source"]
                or datetime.fromisoformat(ranking["expires_at"]).tzinfo is None):
            raise ValueError("ranking requires an evidence source and expiry with timezone")
        for model, scores in ranking["scores"].items():
            if (model not in {m["id"] for m in doc["models"]}
                    or not set(scores) <= {"agentic", "general"}
                    or any(type(score) is not int for score in scores.values())):
                raise ValueError("ranking scores must name declared models and exact integers")
    for check in doc["login"].get("state_checks", []):
        relative(check["path"])
    if not doc["login"].get("status_argv") and not doc["login"].get("state_checks"):
        raise ValueError("source needs a native login success probe")
    for name, content in doc.get("files", {}).items():
        relative(name)
        if not isinstance(content, str):
            raise ValueError("source config files must contain text")
    for argv in (doc["login"]["argv"], doc["login"].get("status_argv"), doc["invoke"]["argv"]):
        if argv is None:
            continue
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
