"""Typed architecture signer. Key, founder identity and issued proofs stay in broker custody."""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tinyassets.architecture_approval import PURPOSE, TTL, canonical, files_digest, validate_action


class ArchitectureSigner:
    def __init__(self, state: Path):
        self.state = state

    def execute(self, document: dict) -> dict:
        # No environment override, generated fallback, or caller-selected key/path.
        config = json.loads((self.state / "architecture-signing.json").read_text("utf-8"))
        if (set(config) != {"repo", "owner_id", "private_key"}
                or not isinstance(config["owner_id"], str) or not config["owner_id"]
                or not isinstance(config["repo"], str) or not config["repo"]):
            raise ValueError("invalid architecture signing configuration")
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(
            config["private_key"], validate=True))
        db = self.state / "architecture-attestations.db"
        with closing(sqlite3.connect(db, timeout=10)) as conn:
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("CREATE TABLE IF NOT EXISTS architecture_attestations "
                         "(request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, pr INTEGER NOT NULL, "
                         "approved_at INTEGER NOT NULL, envelope TEXT NOT NULL)")
            if document.get("operation") == "read":
                if set(document) != {"operation", "pr"} or type(document["pr"]) is not int:
                    raise ValueError("invalid attestation read")
                row = conn.execute("SELECT envelope FROM architecture_attestations WHERE pr=? "
                                   "ORDER BY approved_at DESC, rowid DESC LIMIT 1",
                                   (document["pr"],)).fetchone()
                return json.loads(row[0]) if row else {}
            if (set(document) != {"operation", "owner", "request_id", "action"}
                    or document["operation"] != "issue" or document["owner"] != config["owner_id"]
                    or not isinstance(document["request_id"], str)
                    or not 1 <= len(document["request_id"]) <= 100):
                raise PermissionError("founder architecture approval required")
            action = validate_action(document["action"])
            if action["repo"] != config["repo"].lower():
                raise PermissionError("repository not configured for architecture approval")
            digest = hashlib.sha256(canonical(document)).hexdigest()
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT digest,envelope FROM architecture_attestations "
                               "WHERE request_id=?", (document["request_id"],)).fetchone()
            if row:
                if row[0] != digest:
                    raise PermissionError("approval request identity changed")
                return json.loads(row[1])
            now = int(time.time())
            payload = {"purpose": PURPOSE, "repo": action["repo"], "pr": action["pr"],
                       "head_sha": action["head_sha"], "diff_key": action["diff_key"],
                       "release_critical_count": action["release_critical_count"],
                       "files_digest": files_digest(action["release_critical_files"]),
                       "approver_owner_id": config["owner_id"], "approved_at": now,
                       "expiry": now + TTL}
            envelope = {"payload": payload,
                        "signature": base64.b64encode(key.sign(canonical(payload))).decode("ascii")}
            conn.execute("INSERT INTO architecture_attestations VALUES (?,?,?,?,?)",
                         (document["request_id"], digest, action["pr"], now,
                          canonical(envelope).decode("ascii")))
            conn.commit()
            return envelope
