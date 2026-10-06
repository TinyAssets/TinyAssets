"""One inert extension revision, encoded by the command-center package machinery."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from tinyassets import command_center_packages as packages

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
KINDS = ("tools", "hooks", "commands", "cards", "connections", "mcp_servers")
EVENTS = {"input", "turn_start", "context", "before_tool", "after_tool", "turn_end"}


class ExtensionError(ValueError):
    """Visible invalid package or unavailable lifecycle operation."""


def _object(value, fields, required=()):
    if not isinstance(value, dict) or set(value) - set(fields) or set(required) - set(value):
        raise ExtensionError("invalid extension fields")
    return value


def _name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ExtensionError("invalid extension name")
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip() or not value.isprintable():
        raise ExtensionError("expected nonempty printable text")
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ExtensionError("duplicate extension JSON key")
        result[key] = value
    return result


def _constant(_):
    raise ExtensionError("non-finite extension JSON value")


def parse_manifest(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or len(raw) > 256 * 1024:
        raise ExtensionError("manifest must be at most 256 KiB")
    try:
        doc = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
        _object(doc, {"schema_version", "name", "description", "executable", *KINDS},
                {"schema_version", "name"})
        if type(doc["schema_version"]) is not int or doc["schema_version"] != 2:
            raise ExtensionError("expected extension schema_version 2")
        _name(doc["name"])
        if "description" in doc:
            _text(doc["description"])
        if "executable" in doc:
            packages.check_path(doc["executable"])
        for kind in KINDS:
            rows = doc.get(kind, [])
            if not isinstance(rows, list):
                raise ExtensionError(f"{kind} must be a list")
            names = set()
            for row in rows:
                if kind in {"tools", "commands", "hooks"}:
                    fields = {"name", "description", "arguments"}
                    if kind == "hooks":
                        fields.add("event")
                    _object(row, fields, fields)
                    if "executable" not in doc:
                        raise ExtensionError("executable contributions require executable")
                    if not isinstance(row["arguments"], dict):
                        raise ExtensionError("arguments must be a JSON schema object")
                    # No network schema resolution; remote references are not executable input.
                    if _has_ref(row["arguments"]):
                        raise ExtensionError("argument schema references are not supported")
                    Draft202012Validator.check_schema(row["arguments"])
                    if kind == "hooks" and row["event"] not in EVENTS:
                        raise ExtensionError("unsupported lifecycle event")
                elif kind == "cards":
                    _object(row, {"name", "description", "asset"},
                            {"name", "description", "asset"})
                    packages.check_path(row["asset"])
                elif kind == "connections":
                    _object(row, {"name", "description", "verbs"},
                            {"name", "description", "verbs"})
                    verbs = row["verbs"]
                    if (not isinstance(verbs, list) or not verbs
                            or any(not isinstance(v, str) or not re.fullmatch(r"[A-Z]+", v)
                                   for v in verbs) or len(set(verbs)) != len(verbs)):
                        raise ExtensionError("connection verbs must be unique uppercase names")
                else:
                    _object(row, {"name", "description", "transport", "url", "slot",
                                  "executable", "args"}, {"name", "description", "transport"})
                    if row["transport"] == "remote":
                        if set(row) - {"name", "description", "transport", "url", "slot"}:
                            raise ExtensionError("remote MCP fields")
                        url = urlsplit(_text(row.get("url")))
                        if (url.scheme != "https" or not url.hostname or url.username
                                or url.password or url.fragment or url.query):
                            raise ExtensionError("remote MCP needs a credential-free HTTPS URL")
                        _ = url.port
                    elif row["transport"] == "stdio":
                        if set(row) - {"name", "description", "transport", "executable", "args"}:
                            raise ExtensionError("stdio MCP fields")
                        packages.check_path(row.get("executable"))
                        if not isinstance(row.get("args", []), list):
                            raise ExtensionError("stdio args must be a list")
                        for arg in row.get("args", []):
                            _text(arg)
                    else:
                        raise ExtensionError("unsupported MCP transport")
                name = _name(row["name"])
                _text(row["description"])
                if name in names:
                    raise ExtensionError(f"duplicate {kind} name")
                names.add(name)
        slots = {row["name"] for row in doc.get("connections", [])}
        for row in doc.get("mcp_servers", []):
            if "slot" in row and row["slot"] not in slots:
                raise ExtensionError("MCP references undeclared connection slot")
        return doc
    except (ValueError, TypeError, KeyError, RecursionError, SchemaError) as exc:
        raise ExtensionError(f"invalid extension manifest: {str(exc)[:200]}") from None


def _has_ref(value):
    if isinstance(value, dict):
        return any(k in {"$ref", "$dynamicRef"} or _has_ref(v) for k, v in value.items())
    return isinstance(value, list) and any(_has_ref(v) for v in value)


@dataclass(frozen=True)
class Revision:
    name: str
    digest: str
    blob: bytes

    def content(self):
        _, files = packages.check_blob(self.blob)
        prefix = f"extensions/{self.name}/"
        local = {path.removeprefix(prefix): data for path, data in files.items()}
        return parse_manifest(local["extension.json"]), local


def build_revision(files: dict[str, bytes]) -> Revision:
    """Input is bytes, never host paths or someone else's blob identifier."""
    try:
        if not isinstance(files, dict) or not files or len(files) > packages.MAX_FILES:
            raise ExtensionError("invalid extension file map")
        for path, data in files.items():
            packages.check_path(path)
            if not isinstance(data, bytes) or len(data) > packages.MAX_FILE_BYTES:
                raise ExtensionError("invalid extension file bytes")
        packages.check_tree(list(files))
        doc = parse_manifest(files["extension.json"])
        references = [doc["executable"]] if "executable" in doc else []
        references += [row["asset"] for row in doc.get("cards", [])]
        references += [row["executable"] for row in doc.get("mcp_servers", [])
                       if row["transport"] == "stdio"]
        if any(path not in files for path in references):
            raise ExtensionError("extension references a missing file")
        rooted = {f"extensions/{doc['name']}/{path}": data for path, data in files.items()}
        manifest = packages.build_manifest(
            profile=packages.PROFILE_PUBLISH, name=doc["name"],
            description=doc.get("description", "Extension"), files=rooted,
            workflows=[], ui="", automations=[],
            connections=[row["name"] for row in doc.get("connections", [])],
        )
        blob = packages.build_blob(manifest, rooted)
        packages.check_blob(blob)
        return Revision(doc["name"], hashlib.sha256(blob).hexdigest(), blob)
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        raise ExtensionError(str(exc)) from None
