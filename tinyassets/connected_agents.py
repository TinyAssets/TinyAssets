"""Owner-visible authentication receipts, never agent authority or liveness."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time

logger = logging.getLogger(__name__)


def connection_ref(issuer: str, subject: str) -> str:
    """Opaque correlation only. Neither input is retained or used as authority."""
    return hashlib.sha256((issuer + "\0" + subject).encode()).hexdigest()


def display_name(value) -> str:
    if not isinstance(value, str) or len(value) > 80:
        return ""
    # Names are untrusted metadata, not an avenue to retain credentials/URLs.
    if re.search(r"(?i)(ta_key_|bearer|eyJ|sk-|sk_|https?[:/]|token[=:])", value):
        return ""
    return re.sub(r"[^\w .-]", "", value).strip()[:80]


def workos_connection(claims: dict, token: str, outside: dict | None) -> dict:
    """Called after JWT verification. Claim interpretation grants no authority."""
    if outside is not None:
        subject = outside["client"] + ":" + outside["family"]
        kind = "verified_family"
    elif isinstance(claims.get("sid"), str) and claims["sid"]:
        subject, kind = claims["sid"], "verified_session"
    else:
        # A provider need not expose session/client identity. Keep separate
        # unknown credential connections rather than invent a vendor or merge
        # different agents. Rotation may produce another unknown connection.
        subject, kind = token, "unknown_client"
    return {"id": connection_ref(str(claims.get("iss", "")), subject),
            "identity_source": kind}


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS authenticated_connections (
        owner TEXT NOT NULL, connection_id TEXT NOT NULL, name TEXT NOT NULL,
        name_source TEXT NOT NULL, identity_source TEXT NOT NULL,
        outside_origin TEXT, first_authenticated_at REAL NOT NULL,
        last_authenticated_at REAL NOT NULL,
        PRIMARY KEY(owner, connection_id))""")


def observe_mcp(identity, reported_name="") -> None:
    """Observe only an authenticated external MCP door; no HTTP body is kept."""
    from tinyassets.daemon_server import _connect, initialize_author_server
    from tinyassets.outside_authority import current_store
    from tinyassets.storage.current_home import CurrentHomeChanged, check_principal_not_deleted

    metadata = identity.metadata
    connection = metadata.get("oauth_connection")
    if not connection or metadata.get("first_party_client"):
        return
    name = display_name(connection.get("name")) or display_name(reported_name)
    source = "registered_client" if display_name(connection.get("name")) else "client_reported"
    if not name:
        name, source = "Unknown MCP client", "unknown"
    now = time.time()
    try:
        store = current_store()
        initialize_author_server(store.path.parent)
        with store.db() as conn:
            _schema(conn)
            # Serialize with this store's erasure before checking the permanent
            # tombstone. A deletion starting after this check must wait for our
            # receipt commit and then erase it; a completed deletion forbids it.
            conn.execute("BEGIN IMMEDIATE")
            with _connect(store.path.parent) as root:
                root.execute("BEGIN")
                check_principal_not_deleted(root, identity.user_id)
            conn.execute("""INSERT INTO authenticated_connections VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(owner,connection_id) DO UPDATE SET
                name=CASE WHEN excluded.name_source='unknown' THEN name ELSE excluded.name END,
                name_source=CASE WHEN excluded.name_source='unknown' THEN name_source
                           ELSE excluded.name_source END,
                outside_origin=excluded.outside_origin,
                last_authenticated_at=excluded.last_authenticated_at""",
                         (identity.user_id, connection["id"], name, source,
                          connection["identity_source"],
                          json.dumps(metadata["outside_origin"])
                          if metadata.get("outside_origin") else None, now, now))
    except CurrentHomeChanged:
        return
    except (sqlite3.Error, OSError):
        # Receipt failure must never change authentication or leak metadata.
        logger.warning("authenticated connection receipt unavailable")


def _entry(ident, name, protocol, timestamp, source, identity_source):
    return {"connection_id": ident, "name": name, "protocol": protocol,
            "name_source": source, "identity_source": identity_source,
            "last_authenticated_at": timestamp, "state": "previously_authenticated",
            "messaging_ready": False}


def roster(base, *, universe_id: str, owner: str) -> list[dict]:
    """A receipt projection for an already-authorized owner/center read.

    Existing local revocation and exact scopes govern inclusion. An unclassified
    OAuth receipt states only past authentication, never current remote access.
    """
    from tinyassets.api_keys import ApiKeyStore, _client, _family
    from tinyassets.daemon_server import get_founder_home
    from tinyassets.outside_authority import OutsideClientAuthority, OutsideRefused

    authority = OutsideClientAuthority(base)
    result = []
    for key in ApiKeyStore(base).list(owner, authority=authority)["keys"]:
        if key["last_used_at"] is None or key["revoked_at"] is not None:
            continue
        bound = {"client": _client(key["key_id"]), "family": _family(key["key_id"])}
        if not _has_scope(authority, owner, bound, key["scopes"], universe_id):
            continue
        result.append(_entry("api:" + key["key_id"], display_name(key["name"]) or
                             "Unnamed API client", "api", key["last_used_at"],
                             "owner_supplied", "api_key"))
    with authority.db() as conn:
        # A read does not manufacture a connection or need to migrate the table.
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                            "name='authenticated_connections'").fetchone():
            return result
        rows = conn.execute("SELECT * FROM authenticated_connections WHERE owner=? "
                            "ORDER BY first_authenticated_at,connection_id", (owner,)).fetchall()
        grants = {g["client"]: g for g in authority.inspect(owner)["clients"]}
    for row in rows:
        if row["outside_origin"]:
            bound = json.loads(row["outside_origin"])
            grant = grants.get(bound["client"])
            if not grant:
                continue
            try:
                authority.admit(owner, bound)
            except OutsideRefused:
                continue
            if not _has_scope(authority, owner, bound, json.loads(grant["scopes"]), universe_id):
                continue
        elif get_founder_home(base, owner) != universe_id:
            continue
        result.append(_entry("mcp:" + row["connection_id"], row["name"], "mcp",
                             row["last_authenticated_at"], row["name_source"],
                             row["identity_source"]))
    return result


def _has_scope(authority, owner, bound, scopes, universe):
    from tinyassets.outside_authority import OutsideRefused

    for scope in scopes:
        if scope["universe"] != universe:
            continue
        try:
            authority.admit(owner, bound, universe=universe, agent=scope["agent"],
                            capability=scope["capability"])
            return True
        except OutsideRefused:
            continue
    return False


async def serve_mcp(app, scope, receive, send, identity):
    """Passive bounded initialize-name capture; preserve streaming/body behavior."""
    from starlette.concurrency import run_in_threadpool
    from starlette.requests import Request

    from tinyassets.auth.wellknown import _server_url
    from tinyassets.onboarding import _same_origin_json

    # The first-party app also speaks MCP for actions. Its same-origin browser
    # traffic is not another external agent. This affects display only; it
    # neither authenticates the browser nor changes any grant decision.
    if (not identity.metadata.get("outside_origin")
            and _same_origin_json(Request(scope), _server_url())):
        await app(scope, receive, send)
        return

    body = bytearray()
    complete = False
    oversized = False

    async def receiving():
        nonlocal complete, oversized
        message = await receive()
        if message["type"] == "http.request" and not oversized:
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > 16384:
                oversized = True
                body.clear()
            else:
                body.extend(chunk)
                complete = not message.get("more_body", False)
        return message

    async def sending(message):
        if message["type"] == "http.response.start" and 200 <= message["status"] < 300:
            name = ""
            if complete and not oversized:
                try:
                    data = json.loads(body)
                    if isinstance(data, dict) and data.get("method") == "initialize":
                        params = data.get("params")
                        info = params.get("clientInfo") if isinstance(params, dict) else None
                        name = info.get("name", "") if isinstance(info, dict) else ""
                except (ValueError, UnicodeError):
                    pass
            await run_in_threadpool(observe_mcp, identity, name)
        await send(message)

    await app(scope, receiving, sending)
