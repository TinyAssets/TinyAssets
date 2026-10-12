"""Strict data contract for a shipped native-agent contribution."""
from __future__ import annotations

import re

from tinyassets.extension_manifest import ExtensionError, _name, _object, _text

PLACEHOLDERS = frozenset({"model", "effort", "workdir", "session", "input",
                          "system", "mcp_config"})
_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_ENV = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){2}(?:-[A-Za-z0-9.-]+)?\Z")


def _fields(value, allowed, required=()):
    return _object(value, allowed, required)


def _template(value):
    if isinstance(value, str):
        if "{" in value or "}" in value:
            if not (value.startswith("{") and value.endswith("}")
                    and value[1:-1] in PLACEHOLDERS):
                raise ExtensionError("invalid external-agent placeholder")
        else:
            _text(value)
    elif isinstance(value, list):
        for item in value:
            _template(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            _text(key)
            _template(item)
    elif value is not None and type(value) not in (bool, int, float):
        raise ExtensionError("invalid external-agent template value")


def _argv(value):
    if not isinstance(value, list) or len(value) > 32:
        raise ExtensionError("invalid external-agent argv")
    for item in value:
        if not isinstance(item, str):
            raise ExtensionError("invalid external-agent argv")
        _template(item)


def _pointer(value):
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 200:
        raise ExtensionError("invalid external-agent pointer")
    if any(part in ("", ".", "..") for part in value[1:].split("/")):
        raise ExtensionError("invalid external-agent pointer")


def _method(value, extra=()):
    _fields(value, {"method", "params", *extra}, {"method", "params"})
    _text(value["method"])
    if not isinstance(value["params"], dict):
        raise ExtensionError("invalid external-agent method params")
    _template(value["params"])


def validate_agent(row):
    """Reject executable tricks and undeclared files before a revision is accepted."""
    _fields(row, {"name", "description", "family", "install", "auth", "catalogue", "job"},
            {"name", "description", "family", "install", "auth", "catalogue", "job"})
    _name(row["name"])
    _text(row["description"])
    _name(row["family"])
    install = _fields(row["install"], {"npm", "version", "bin"},
                      {"npm", "version", "bin"})
    if not all(isinstance(install[key], str) for key in install):
        raise ExtensionError("invalid external-agent install")
    if not re.fullmatch(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+", install["npm"]):
        raise ExtensionError("invalid external-agent npm package")
    if not _VERSION.fullmatch(install["version"]):
        raise ExtensionError("external-agent version must be exact")
    _name(install["bin"])
    auth = _fields(row["auth"], {"service", "home_env", "home_dir", "files",
                                 "writeback", "env_from_file"},
                   {"service", "home_env", "home_dir", "files", "writeback",
                    "env_from_file"})
    _name(auth["service"])
    _name(auth["home_dir"])
    if not _ENV.fullmatch(auth["home_env"]):
        raise ExtensionError("invalid external-agent home variable")
    for field in ("files", "writeback"):
        value = auth[field]
        if not isinstance(value, list) or len(value) > 16 or len(set(value)) != len(value):
            raise ExtensionError("invalid external-agent auth files")
        if any(not isinstance(item, str) or not _FILE.fullmatch(item) for item in value):
            raise ExtensionError("invalid external-agent auth file name")
    if not set(auth["writeback"]) <= set(auth["files"]):
        raise ExtensionError("external-agent writeback must name an auth file")
    if not isinstance(auth["env_from_file"], dict):
        raise ExtensionError("invalid external-agent auth environment")
    for key, file in auth["env_from_file"].items():
        if not _ENV.fullmatch(key) or file not in auth["files"]:
            raise ExtensionError("invalid external-agent auth environment")
    catalogue = _fields(row["catalogue"], {"transport", "argv", "initialize", "list", "row"},
                        {"transport", "argv", "list", "row"})
    job = _fields(row["job"], {"transport", "argv", "start", "resume", "input",
                                 "session_handle", "tools", "events", "cancel"},
                  {"transport", "argv", "start", "resume", "input",
                   "session_handle", "tools", "events", "cancel"})
    for operation in (catalogue, job):
        if operation["transport"] not in ("jsonrpc", "control_stream"):
            raise ExtensionError("unsupported external-agent transport")
        _argv(operation["argv"])
    if "initialize" in catalogue:
        init = _fields(catalogue["initialize"], {"method", "params", "notification"},
                       {"method", "params", "notification"})
        _text(init["notification"])
        _method(init, {"notification"})
    listed = _fields(catalogue["list"], {"method", "params", "items", "cursor",
                                            "cursor_param"}, {"method", "params", "items"})
    _method(listed, {"items", "cursor", "cursor_param"})
    _pointer(listed["items"])
    if ("cursor" in listed) != ("cursor_param" in listed):
        raise ExtensionError("external-agent cursor fields differ")
    if "cursor" in listed:
        _pointer(listed["cursor"])
        _text(listed["cursor_param"])
    fields = _fields(catalogue["row"], {"model", "default", "hidden", "modalities",
                                                "effort_levels", "effort_level"},
                     {"model", "default", "hidden", "modalities"})
    for value in fields.values():
        _pointer(value)
    for field in ("start", "resume", "input"):
        _method(job[field])
    _pointer(job["session_handle"])
    tools = _fields(job["tools"], {"mode", "define_at", "call_method"}, {"mode"})
    if tools["mode"] not in ("dynamic_rpc", "mcp_config"):
        raise ExtensionError("unsupported external-agent tool mode")
    if tools["mode"] == "dynamic_rpc":
        _pointer(tools["define_at"])
        _text(tools["call_method"])
    events = _fields(job["events"], {"text_delta", "item_done", "usage", "model", "done"},
                     {"text_delta", "usage", "model", "done"})
    for event in events.values():
        if not isinstance(event, dict) or not isinstance(event.get("method"), str):
            raise ExtensionError("invalid external-agent event")
        for key, value in event.items():
            if key == "method":
                _text(value)
            else:
                _pointer(value)
    cancel = _fields(job["cancel"], {"method", "params", "grace_s"},
                     {"method", "params", "grace_s"})
    _method(cancel, {"grace_s"})
    if type(cancel["grace_s"]) is not int or not 1 <= cancel["grace_s"] <= 30:
        raise ExtensionError("invalid external-agent cancel grace")
