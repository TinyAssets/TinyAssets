"""Router-held hook token -> owner map. No branch, payload or owner-store reads.

The cutover seeds digests from existing hooks. Box token publication uses this
same map through the fixed owner relay; delivery never opens hook rows.
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
RELAY_UID = 0

def active_owner(owner: str, root: Path | None = None) -> bool:
    """One identity fence shared by every router authentication entry point."""
    from tinyassets.routing_snapshot import snapshot
    from tinyassets.storage import data_dir

    try:
        snapshot(root if root is not None else data_dir()).require_current()
    except Exception:
        return False
    return True


class HookOwners:
    def __init__(self, root: Path):
        from tinyassets.owner_runtime import refuse_router_store

        refuse_router_store("hook_owners")
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
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='hook_owners'").fetchone():
                return None
            row = db.execute("SELECT owner FROM hook_owners WHERE token_hash=?",
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return row[0] if row and active_owner(row[0], self.path.parent) else None

    def shared_endpoint(self, binding: str) -> bool:
        if not self.path.exists():
            return False
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS inbound_endpoints (binding TEXT PRIMARY KEY)")
            return db.execute("SELECT 1 FROM inbound_endpoints WHERE binding=?",
                              (binding,)).fetchone() is not None



def credential_socket(root: Path) -> Path:
    return root / ".runtime" / "router-credentials.sock"


def revoke_request(root: Path, owner: str) -> dict:
    """The broker can revoke an account; owner mints enter through the relay."""
    return _request(root, {"op": "ROUTER_CREDENTIAL", "owner": owner,
                           "request": {"operation": "revoke_account"}})


def relay_request(root: Path, owner: str, request: dict) -> dict:
    """Root relay supplies only its fixed binding; boxes cannot supply an owner."""
    return _request(root, {"op": "RELAY_CREDENTIAL", "owner": owner, "request": request})


def routing_request(root: Path, request: dict) -> dict:
    """C5 broker publication is separate from owner-bound credential operations."""
    return _request(root, {"op": "ROUTING_SNAPSHOT", "request": request})


def _request(root, document):
    from tinyassets.broker.server import peer_uid

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(30)
        channel.connect(str(credential_socket(root)))
        if peer_uid(channel) != DAEMON_UID:
            raise PermissionError("credential peer is not the router")
        channel.sendall(rf.control(rf.CONNECTION, document))
        answer = rf.read_frame_blocking(channel)
    if answer is None or answer.kind != rf.CONTROL or answer.stream != rf.CONNECTION:
        raise ValueError("invalid router credential reply")
    return answer.control()


def dispatch(root: Path, owner: str, request: dict) -> dict:
    """Only router state; owner comes from the authenticated service binding."""
    from tinyassets.broker.owner_identities import validate_principal

    validate_principal(owner)
    if not isinstance(request, dict):
        raise ValueError("invalid credential operation")
    if request == {"operation": "revoke_account"}:
        revoke_account(root, owner)
        return {"revoked": True}
    from tinyassets.routing_snapshot import snapshot

    snapshot(root).route(owner)
    return _dispatch(root, owner, request)


def revoke_account(root: Path, owner: str):
    """Identity cleanup for the broker's fixed account-revocation operation."""
    from tinyassets.api_keys import ApiKeyStore

    _revoke_account(root, owner, ApiKeyStore(root))


def _revoke_account(root: Path, owner: str, keys):
    from tinyassets.onboarding import approval_handoff, owner_sessions, session_store
    from tinyassets.router_account_deletion import revoke_local_tokens

    revoke_local_tokens(root, owner, deleted=True)
    # This process's data_dir is the router root, never the requesting box.
    owner_sessions.revoke_owner(owner)
    session_store.revoke_owner(owner)
    with approval_handoff.flows() as db:
        db.execute("DELETE FROM approval_handoffs WHERE owner=?", (owner,))
    for key in keys.list(owner, authority=None)["keys"]:
        if key["revoked_at"] is None:
            keys.revoke(owner, key["key_id"], authority=None)
    HookOwners(root).revoke_owner(owner)
    # C15: credentials are the authentication fence after routes omit erasures.
    # Retire local OAuth codes and both bearer types in their one identity store.
    oauth = root / ".auth.db"
    if oauth.exists():
        with closing(sqlite3.connect(oauth.as_uri() + "?mode=rw", uri=True)) as db, db:
            for table in ("authorization_codes", "access_tokens", "refresh_tokens"):
                db.execute(f"DELETE FROM {table} WHERE user_id=?", (owner,))


def _dispatch(root: Path, owner: str, request: dict) -> dict:
    from tinyassets.api_keys import ApiKeyStore

    operation = request.get("operation")
    keys = ApiKeyStore(root)
    if operation == "key_mint" and set(request) == {"operation", "name", "request_ref"}:
        if not isinstance(request["request_ref"], str) or len(request["request_ref"]) > 4096:
            raise ValueError("invalid key request reference")
        result = keys.mint(owner, name=request["name"], request_ref=request["request_ref"],
                           scopes=[], authority=None)
        from tinyassets.routing_snapshot import snapshot

        try:
            snapshot(root).route(owner)
        except Exception:
            if not result.get("already_created"):
                with keys.db() as db:
                    db.execute("DELETE FROM api_keys WHERE key_id=? AND owner=?",
                               (result["key_id"], owner))
            raise
        return result
    if operation == "key_list" and set(request) == {"operation"}:
        return keys.list(owner, authority=None)
    if operation == "key_revoke" and set(request) == {"operation", "key_id"}:
        if not isinstance(request["key_id"], str):
            raise ValueError("invalid key id")
        return keys.revoke(owner, request["key_id"], authority=None)
    if operation == "hook_mint" and set(request) == {"operation"}:
        token = secrets.token_urlsafe(32)
        hooks = HookOwners(root)
        digest = hashlib.sha256(token.encode()).hexdigest()
        hooks.publish(digest, owner)
        from tinyassets.routing_snapshot import snapshot

        try:
            snapshot(root).route(owner)
        except Exception:
            hooks.revoke(digest, owner)
            raise
        return {"token": token}
    if operation == "hook_revoke" and set(request) == {"operation", "token_hash"}:
        digest = request["token_hash"]
        if not isinstance(digest, str) or re.fullmatch("[0-9a-f]{64}", digest) is None:
            raise ValueError("invalid hook digest")
        HookOwners(root).revoke(digest, owner)
        return {"revoked": True}
    raise ValueError("invalid credential operation")


def routing_dispatch(root, request):
    from tinyassets.routing_snapshot import snapshot

    if (request.get("operation") == "inbound_state"
            and set(request) == {"operation", "owner"}):
        from tinyassets.inbound_routing import DeliveryBreakers

        snapshot(root).route(request["owner"])
        return {"breakers": DeliveryBreakers(root).for_owner(request["owner"])}

    if (request.get("operation") == "inbound_ready"
            and set(request) == {"operation", "owner", "binding", "revision"}
            and isinstance(request.get("owner"), str) and request["owner"]
            and isinstance(request.get("binding"), str) and request["binding"]
            and type(request.get("revision")) is int and request["revision"] >= 1):
        from tinyassets.inbound_routing import DeliveryBreakers

        snapshot(root).route(request["owner"])
        DeliveryBreakers(root).ready(request["owner"], request["binding"], request["revision"])
        return {"ready": True}

    if (request.get("operation") == "inbound_endpoint"
            and set(request) == {"operation", "binding"}
            and isinstance(request["binding"], str) and request["binding"]):
        with closing(sqlite3.connect(HookOwners(root).path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS inbound_endpoints (binding TEXT PRIMARY KEY)")
            db.execute("INSERT OR IGNORE INTO inbound_endpoints VALUES (?)", (request["binding"],))
        return {"published": True}

    if request == {"operation": "routing_instance"}:
        return snapshot(root).installed()
    if (request.get("operation") == "routing_install"
            and set(request) == {"operation", "instance", "revision", "records"}):
        revision = snapshot(root).install(request["instance"], request["revision"],
                                          request["records"])
        return {"revision": revision}
    raise ValueError("invalid routing publication")


@asynccontextmanager
async def serving(root: Path):
    """Broker publication/revocation and root relay's fixed owner binding."""
    from tinyassets.broker.server import peer_uid

    async def handle(reader, writer):
        answer = None
        try:
            caller = peer_uid(writer.get_extra_info("socket"))
            if caller not in {RELAY_UID, BROKER_UID}:
                return
            frame = await asyncio.wait_for(rf.read_frame(reader), 30)
            if frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION:
                return
            doc = frame.control()
            if (caller == BROKER_UID and set(doc) == {"op", "request"}
                    and doc["op"] == "ROUTING_SNAPSHOT"):
                result = routing_dispatch(root, doc["request"])
                answer = {"op": "ROUTING_SNAPSHOT_IS", "result": result}
            elif (set(doc) == {"op", "owner", "request"} and
                  ((caller == BROKER_UID and doc["op"] == "ROUTER_CREDENTIAL"
                    and doc["request"] == {"operation": "revoke_account"})
                   or (caller == RELAY_UID and doc["op"] == "RELAY_CREDENTIAL"))):
                if doc["op"] == "RELAY_CREDENTIAL" and doc["request"].get("operation") not in {
                        "key_mint", "key_list", "key_revoke", "hook_mint", "hook_revoke"}:
                    raise PermissionError("relay credential operation refused")
                result = await asyncio.to_thread(dispatch, root, doc["owner"], doc["request"])
                answer = {"op": "ROUTER_CREDENTIAL_IS", "result": result}
            else:
                raise ValueError("invalid router request")
        except (ValueError, LookupError, RuntimeError, PermissionError, sqlite3.Error,
                OSError, TimeoutError):
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
