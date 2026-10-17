"""Router-held hook token -> owner map. No branch, payload or owner-store reads.

The cutover seeds digests from existing hooks. Box token publication uses this
same map through the shared-state broker seam; delivery never opens hook rows.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import socket
import sqlite3
from contextlib import asynccontextmanager, closing
from pathlib import Path

from tinyassets import rpc_frames as rf
from tinyassets.boxes.layout import BROKER_READ_GID, BROKER_UID, DAEMON_UID

#: Router rows one owner may hold, so no box can grow the shared map unbounded.
MAX_HOOKS_PER_OWNER = 200


class HookOwners:
    def __init__(self, root: Path):
        self.path = root / ".hook-owners.sqlite3"

    def publish(self, token_hash: str, owner: str):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS hook_owners ("
                       "token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL)")
            # One statement: the count and the insert cannot interleave.
            added = db.execute(
                "INSERT INTO hook_owners SELECT ?, ? WHERE "
                "(SELECT COUNT(*) FROM hook_owners WHERE owner=?) < ?",
                (token_hash, owner, owner, MAX_HOOKS_PER_OWNER)).rowcount
        if added != 1:
            raise ValueError("too many inbound hooks; revoke one first")

    def revoke(self, token_hash: str, owner: str):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM hook_owners WHERE token_hash=? AND owner=?",
                       (token_hash, owner))

    def revoke_owner(self, owner: str):
        if not self.path.exists():
            return
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM hook_owners WHERE owner=?", (owner,))

    def resolve(self, token: str) -> str | None:
        if not self.path.exists():
            return None
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT owner FROM hook_owners WHERE token_hash=?",
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return row[0] if row else None


def hook_identity(root: Path, token: str):
    from tinyassets.auth.provider import Identity
    from tinyassets.webhook_inbound import inbound_enabled

    if not inbound_enabled():
        return None
    owner = HookOwners(root).resolve(token)
    return Identity(user_id=owner, username=owner) if owner else None


def credential_socket(root: Path) -> Path:
    return root / ".runtime" / "router-credentials.sock"


def broker_request(root: Path, owner: str, request: dict) -> dict:
    """Broker forwarding only: the router remains the sole credential writer."""
    from tinyassets.broker.server import peer_uid

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(30)
        channel.connect(str(credential_socket(root)))
        if peer_uid(channel) != DAEMON_UID:
            raise PermissionError("credential peer is not the router")
        channel.sendall(rf.control(rf.CONNECTION, {
            "op": "ROUTER_CREDENTIAL", "owner": owner, "request": request,
        }))
        answer = rf.read_frame_blocking(channel)
    if answer is None or answer.kind != rf.CONTROL or answer.stream != rf.CONNECTION:
        raise ValueError("invalid router credential reply")
    return answer.control()


def dispatch(root: Path, owner: str, request: dict) -> dict:
    """Only router state; the owner argument comes from the authenticated broker."""
    from tinyassets.api_keys import ApiKeyStore
    from tinyassets.broker.owner_identities import validate_principal

    validate_principal(owner)
    if not isinstance(request, dict):
        raise ValueError("invalid credential operation")
    operation = request.get("operation")
    keys = ApiKeyStore(root)
    if operation == "account_revoke" and set(request) == {"operation"}:
        from tinyassets.onboarding import approval_handoff, owner_sessions

        # This process's data_dir is the router root, never the requesting box.
        owner_sessions.revoke_owner(owner)
        with approval_handoff.flows() as db:
            db.execute("DELETE FROM approval_handoffs WHERE owner=?", (owner,))
        for key in keys.list(owner, authority=None)["keys"]:
            if key["revoked_at"] is None:
                keys.revoke(owner, key["key_id"], authority=None)
        HookOwners(root).revoke_owner(owner)
        return {"revoked": True}
    if operation == "key_mint" and set(request) == {"operation", "name", "request_ref"}:
        if not isinstance(request["request_ref"], str) or len(request["request_ref"]) > 4096:
            raise ValueError("invalid key request reference")
        return keys.mint(owner, name=request["name"], request_ref=request["request_ref"],
                         scopes=[], authority=None)
    if operation == "key_list" and set(request) == {"operation"}:
        return keys.list(owner, authority=None)
    if operation == "key_revoke" and set(request) == {"operation", "key_id"}:
        if not isinstance(request["key_id"], str):
            raise ValueError("invalid key id")
        return keys.revoke(owner, request["key_id"], authority=None)
    if operation == "hook_mint" and set(request) == {"operation"}:
        token = secrets.token_urlsafe(32)
        HookOwners(root).publish(hashlib.sha256(token.encode()).hexdigest(), owner)
        return {"token": token}
    if operation == "hook_revoke" and set(request) == {"operation", "token_hash"}:
        digest = request["token_hash"]
        if not isinstance(digest, str) or re.fullmatch("[0-9a-f]{64}", digest) is None:
            raise ValueError("invalid hook digest")
        HookOwners(root).revoke(digest, owner)
        return {"revoked": True}
    raise ValueError("invalid credential operation")


@asynccontextmanager
async def serving(root: Path):
    """Private router RPC: only the broker's kernel identity may supply an owner."""
    from tinyassets.broker.server import peer_uid

    async def handle(reader, writer):
        answer = None
        try:
            if peer_uid(writer.get_extra_info("socket")) != BROKER_UID:
                return
            frame = await asyncio.wait_for(rf.read_frame(reader), 30)
            if frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION:
                return
            doc = frame.control()
            if (set(doc) != {"op", "owner", "request"}
                    or doc["op"] != "ROUTER_CREDENTIAL"):
                raise ValueError("invalid router request")
            result = await asyncio.to_thread(dispatch, root, doc["owner"], doc["request"])
            answer = {"op": "ROUTER_CREDENTIAL_IS", "result": result}
        except (ValueError, PermissionError, sqlite3.Error, OSError, TimeoutError):
            answer = {"op": "ROUTER_CREDENTIAL_REFUSED"}
        finally:
            try:
                if answer is not None:
                    writer.write(rf.control(rf.CONNECTION, answer))
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

    path = credential_socket(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Existing router runtime group: the broker may traverse it and connect,
    # but cannot write either the directory or the router's credential stores.
    if BROKER_READ_GID in os.getgroups():
        os.chown(path.parent, -1, BROKER_READ_GID)
        os.chmod(path.parent, 0o710)
    path.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle, path=str(path))
    try:
        if BROKER_READ_GID in os.getgroups():
            os.chown(path, -1, BROKER_READ_GID)
        os.chmod(path, 0o660)
        async with server:
            yield
    finally:
        server.close()
        await server.wait_closed()
        path.unlink(missing_ok=True)
