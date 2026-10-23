"""Native credential documents in the existing owner credential namespace."""
from __future__ import annotations

import base64
import json
import re
import secrets
from pathlib import PurePosixPath

FILENAME = ".subscription-state.json"
MAX_STATE = 2 * 1024 * 1024
MAX_OWNER_ENTRIES = 16
MAX_OWNER_BYTES = 8 * 1024 * 1024


def validate_state(state):
    if not isinstance(state, dict) or len(state) > 128:
        raise ValueError("invalid native credential state")
    total = 0
    for name, value in state.items():
        path = PurePosixPath(name)
        if (not path.parts or path.is_absolute() or ".." in path.parts or "\\" in name
                or "\0" in name or not isinstance(value, str)):
            raise ValueError("invalid native credential file")
        total += len(base64.b64decode(value, validate=True))
        if total > MAX_STATE:
            raise ValueError("native credential state exceeds bound")


def operation(data_root, owner, document):
    from tinyassets.broker.key_service import open_document, seal_document
    from tinyassets.broker.vault import (
        _credential_root,
        _fsync_directory,
        _persist_role_vault,
        check_not_deleted,
        vault_lock,
    )
    from tinyassets.credential_vault import custody_namespace

    action, source = document.get("action"), document.get("source")
    if (action not in {"read", "begin", "save", "delete"}
            or not isinstance(source, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", source)
            or set(document) - {"action", "source", "revision", "state"}):
        raise ValueError("invalid native custody operation")
    if action == "save":
        validate_state(document["state"])
    namespace = custody_namespace(data_root, owner)
    with vault_lock(namespace):
        check_not_deleted(data_root, owner)
        path = _credential_root(namespace) / FILENAME
        records = (json.loads(open_document(namespace, FILENAME, path.read_bytes()))
                   if path.exists() else {})
        entry = records.get(source)
        if action == "read":
            return entry or {"revision": None, "state": {}}
        if action == "begin" and entry is not None:
            return entry
        if action == "save" or action == "delete" and "revision" in document:
            if entry is None or document.get("revision") != entry["revision"]:
                raise PermissionError("native credential generation changed")
        if action == "delete":
            records.pop(source, None)
            result = {"revision": None, "state": {}}
        else:
            if entry is None and len(records) >= MAX_OWNER_ENTRIES:
                raise ValueError("native custody source limit exceeded")
            result = {"revision": secrets.token_hex(32),
                      "state": document["state"] if action == "save" else {}}
            records[source] = result
            if len(json.dumps(records).encode("utf-8")) > MAX_OWNER_BYTES:
                raise ValueError("native custody owner byte limit exceeded")
        if records:
            sealed = seal_document(namespace, FILENAME, json.dumps(records).encode())
            _persist_role_vault(path, sealed.decode())
        else:
            path.unlink(missing_ok=True)
        _fsync_directory(path.parent)
        return result
