"""The shared door: authenticated identity -> broker admission -> Unix HTTP stream.

This module never imports an owner store. Boxhostd
owns the wake step; the router never manages a process.
"""
from __future__ import annotations

import base64
import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

import anyio
import httpx
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse

_HOP = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
        b"te", b"trailer", b"transfer-encoding", b"upgrade"}
_TRUSTED = {b"x-owner", b"x-box-secret", b"x-owner-identity"}
_DELETIONS = threading.Lock()
_SHARED_APP = {
    "/app/account/delete",
    "/app", "/app/client-error", "/app/token", "/app/native-sign-in",
    "/app/owner-sign-in", "/app/owner-sign-in/complete", "/app/canary-sign-in",
    "/app/oauth/client-metadata.json", "/app/ui-frame", "/app/sw.js",
    # Sign-in navigation: its rows live in the router's owner-sessions store.
    "/app/approval-handoff", "/app/connections",
}
_SHARED_APP_PREFIXES = ("/app/billing/", "/app/run/", "/app/m/", "/app/model-callback/",
                        "/app/approval-handoff/", "/app/model-connect/")


def hook_token(path: str) -> str | None:
    prefix = "/hooks/inbound/"
    if path.startswith(prefix):
        token = path[len(prefix):]
        if token and "/" not in token:
            return token
    return None


def forwarded_path(path: str) -> bool:
    """Owner doors are removed from the shared route table, not shadowed."""
    if path.startswith("/app/"):
        return path not in _SHARED_APP and not path.startswith(_SHARED_APP_PREFIXES)
    return (path in {"/mcp", "/mcp/", "/mcp/events", "/api"}
            or path.startswith(("/api/", "/hooks/inbound/")))


@dataclass(frozen=True)
class BoxRoute:
    socket: Path
    secret: str
    owner: str = ""
    lease: str = ""


def resolve_box(owner: str, *, cleanup=False) -> BoxRoute:
    from tinyassets.boxes.daemon_control import request
    from tinyassets.broker.owner_identities import owner_box_route
    from tinyassets.erasure_ledger import ErasureLedger
    from tinyassets.storage import data_dir

    if not cleanup and ErasureLedger(data_dir()).contains(owner):
        raise LookupError("owner account is deleted")
    result = request("resolve", owner, cleanup=cleanup)
    if (set(result) != {"uid", "lease"} or type(result["uid"]) is not int
            or not isinstance(result["lease"], str) or len(result["lease"]) != 43):
        raise RuntimeError("invalid box lease response")
    try:
        uid, secret = owner_box_route(data_dir(), principal=owner)
        if uid != result["uid"]:
            raise RuntimeError("owner box identity changed during wake")
    except BaseException:
        request("release", owner, lease=result["lease"])
        raise
    root = Path(os.environ.get("TINYASSETS_BOXES_RUN", "/run/tinyassets-boxhostd"))
    return BoxRoute(root / str(uid) / "app.sock", secret, owner, result["lease"])


def release_route(route):
    if route.lease:
        from tinyassets.boxes.daemon_control import request

        request("release", route.owner, lease=route.lease)


def _headers(headers, *, private=False):
    blocked = set(_HOP)
    for name, value in headers:
        if name.lower() == b"connection":
            blocked.update(piece.strip().lower() for piece in value.split(b","))
    if private:
        blocked.update(_TRUSTED | {b"authorization", b"cookie"})
    return [(name, value) for name, value in headers if name.lower() not in blocked]


class OwnerRouter:
    def __init__(self, app, *, resolver=resolve_box):
        self.app, self.resolver = app, resolver

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from tinyassets.auth.middleware import current_identity_or_none
        from tinyassets.router_traffic import admits
        from tinyassets.storage import data_dir

        identity = current_identity_or_none()
        if not forwarded_path(scope["path"]):
            # Only immutable public app assets and the independently keyed
            # canary sign-in may bypass closed admission. Anonymous token,
            # OAuth and billing callbacks can write shared authority too.
            path = scope["path"]
            probe = (scope["method"] in {"GET", "HEAD"} and (
                path in {"/app", "/app/sw.js", "/app/ui-frame",
                         "/app/oauth/client-metadata.json"}
                or path.startswith("/app/m/")))
            canary_login = path == "/app/canary-sign-in"
            if not probe and not canary_login and not await run_in_threadpool(
                    admits, data_dir(), identity.user_id if identity else None):
                return await JSONResponse({"error": "maintenance"}, status_code=503)(
                    scope, receive, send)
            return await self.app(scope, receive, send)
        token = hook_token(scope["path"])
        if token is not None:
            from tinyassets.router_credentials import hook_identity
            from tinyassets.storage import data_dir

            identity = await run_in_threadpool(hook_identity, data_dir(), token)
        if identity is None:
            return await JSONResponse({"error": "not_found"}, status_code=404)(
                scope, receive, send)
        if not await run_in_threadpool(admits, data_dir(), identity.user_id):
            return await JSONResponse({"error": "maintenance"}, status_code=503)(
                scope, receive, send)
        if scope["path"].startswith("/app/"):
            from tinyassets.onboarding.owner_sessions import COOKIE, lookup

            session = await run_in_threadpool(lookup, Request(scope).cookies.get(COOKIE, ""))
            if session is not None:
                if json.loads(session["identity_json"])["user_id"] != identity.user_id:
                    return await JSONResponse({"error": "identity_conflict"}, status_code=401)(
                        scope, receive, send)
                # Protected-view proof is shared session state; its consumer in
                # the box retains the existing exact-origin/expiry checks.
                from dataclasses import replace

                identity = replace(identity, metadata={**identity.metadata,
                                                       "router_owner_session": session})
        try:
            route = await run_in_threadpool(self.resolver, identity.user_id)
        except LookupError:
            return await JSONResponse({"error": "not_found"}, status_code=404)(
                scope, receive, send)
        except (OSError, RuntimeError):
            return await JSONResponse({"error": "box_unavailable"}, status_code=503)(
                scope, receive, send)
        await forward(scope, receive, send, identity, route)


async def forward(scope, receive, send, identity, route: BoxRoute):
    """One reverse proxy for every owner door, with bounded streaming and cancellation."""
    headers = _headers(scope["headers"], private=True)
    headers.extend([
        (b"x-owner", identity.user_id.encode("utf-8")),
        (b"x-box-secret", route.secret.encode("ascii")),
        (b"x-owner-identity", base64.urlsafe_b64encode(
            json.dumps(asdict(identity), separators=(",", ":")).encode("utf-8"))),
    ])
    target = scope.get("raw_path", scope["path"].encode("utf-8"))
    if scope.get("query_string"):
        target += b"?" + scope["query_string"]
    url = httpx.URL("http://owner").copy_with(raw_path=target)
    started = False
    released = False
    try:
        transport = httpx.AsyncHTTPTransport(uds=str(route.socket))
        timeout = httpx.Timeout(connect=5, read=None, write=30, pool=5)
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            async with client.stream(scope["method"], url, headers=headers,
                                     content=Request(scope, receive).stream()) as response:
                await run_in_threadpool(release_route, route)
                released = True
                await send({"type": "http.response.start", "status": response.status_code,
                            "headers": _headers(response.headers.raw)})
                started = True
                async with anyio.create_task_group() as tasks:
                    async def disconnect():
                        while (await receive())["type"] != "http.disconnect":
                            pass
                        tasks.cancel_scope.cancel()

                    tasks.start_soon(disconnect)
                    async for chunk in response.aiter_raw():
                        await send({"type": "http.response.body", "body": chunk,
                                    "more_body": True})
                    await send({"type": "http.response.body", "body": b""})
                    tasks.cancel_scope.cancel()
    except ClientDisconnect:
        return
    except httpx.HTTPError:
        if started:
            raise  # A truncated stream must never look like a successful response.
        await JSONResponse({"error": "box_unavailable"}, status_code=503)(scope, receive, send)
    finally:
        if not released:
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(release_route, route)


async def resume_deleted_accounts(root):
    """Retry durable deletions without requiring the deleted owner to sign in."""
    from tinyassets.auth.provider import Identity
    from tinyassets.erasure_ledger import ErasureLedger

    for entry in await run_in_threadpool(ErasureLedger(root).pending):
        owner = entry["principal"]
        if owner is None:
            import logging

            logging.getLogger(__name__).error(
                "erasure phases await principal recovery for digest %s", entry["digest"])
            continue
        await delete_owner_account(Identity(user_id=owner, username=owner))


async def delete_owner_account(identity):
    # One router writer serializes receipt updates and concurrent deletion
    # retries. Never hold the credential dispatch lock across network calls.
    with anyio.CancelScope(shield=True):
        await run_in_threadpool(_DELETIONS.acquire)
        try:
            return await _delete_owner_account(identity)
        finally:
            _DELETIONS.release()


async def _delete_owner_account(identity):
    """The door records each shared phase; a tombstone never blocks cleanup."""
    from tinyassets.billing.stripe_adapter import cancel_account_billing
    from tinyassets.broker.account_erasure import erase_account
    from tinyassets.broker.owner_identities import retire_owner
    from tinyassets.erasure_ledger import ErasureLedger
    from tinyassets.router_account_deletion import delete_workos_user
    from tinyassets.router_credentials import revoke_account, tombstone
    from tinyassets.storage import data_dir

    root, owner = data_dir(), identity.user_id
    deleted = ErasureLedger(root)
    receipt = None
    try:
        if await run_in_threadpool(deleted.contains, owner):
            receipt = await run_in_threadpool(deleted.receipt, owner)
        else:
            await run_in_threadpool(tombstone, root, owner, {"local_cleanup": "pending"})
            receipt = await run_in_threadpool(deleted.receipt, owner)

        # The ledger fences sign-in before draining already-admitted work.
        # A blocked drain stays retryable without requiring a new login.
        if (receipt.get("local_cleanup") == "pending"
                and "owner_tree" not in receipt.get("completed_phases", [])):
            route = await run_in_threadpool(resolve_box, owner, cleanup=True)
            response = await delete_box_data(identity, route)
            if response.status_code != 200:
                return response
            local = json.loads(response.body)
            receipt.update(local_cleanup="done",
                           local_unfinished=local.get("unfinished_phases", []))
            await run_in_threadpool(deleted.save_receipt, owner, receipt)

        receipt.setdefault("billing", "pending")
        receipt.setdefault("identity", "pending")
        done = receipt.setdefault("completed_phases", [])
        failures = []

        async def phase(name, operation):
            if name in done:
                return
            try:
                result = await run_in_threadpool(operation)
                if name in {"billing", "identity"}:
                    receipt[name] = result
                    accepted = {"cancelled", "none", "attempt_expired"} if name == "billing" else {
                        "deleted", "not_applicable"}
                    if result not in accepted:
                        raise RuntimeError("external deletion remains unfinished")
                done.append(name)
            except Exception:
                failures.append(name)
                if name in {"billing", "identity"} and receipt[name] == "pending":
                    receipt[name] = "error"
            receipt["unfinished_phases"] = sorted(set(failures + [
                step for step in ("owner_tree", "broker_egress", "billing",
                                  "credentials", "identity")
                if step not in done]))
            await run_in_threadpool(deleted.save_receipt, owner, receipt)

        # The sweep is best-effort; destruction after a proven stop removes
        # every remaining local byte, including failed sweep phases and execs.
        if not receipt.get("quarantines"):
            await phase("owner_tree", lambda: retire_owner(root, principal=owner))
        if "owner_tree" in done:
            receipt["home_removed"] = True
            receipt["local_unfinished"] = []
            receipt["box_destroy"] = {"destroyed": True}
        await phase("broker_egress", lambda: erase_account(root, principal=owner))
        # Money cleanup must run even when stopping or erasing the box failed.
        await phase("billing", lambda: cancel_account_billing(root, owner))
        if "owner_tree" in done:
            await phase("credentials", lambda: revoke_account(root, owner))
        # Durable router retries finish cleanup even though sign-in is fenced.
        if all(step in done for step in ("owner_tree", "broker_egress", "billing", "credentials")):
            await phase("identity", lambda: delete_workos_user(owner))
        receipt["unfinished_phases"] = [step for step in (
            "owner_tree", "broker_egress", "billing", "credentials", "identity")
            if step not in done]
        await run_in_threadpool(deleted.save_receipt, owner, receipt)
        if receipt["unfinished_phases"]:
            return JSONResponse({"error": "deletion_incomplete", "receipt": receipt},
                                status_code=503, headers={"cache-control": "no-store"})
        return JSONResponse(receipt, headers={"cache-control": "no-store"})
    except Exception:
        import logging

        logging.getLogger(__name__).exception("account deletion failed")
        return JSONResponse({"error": "account_deletion_failed", "receipt": receipt},
                            status_code=503,
                            headers={"cache-control": "no-store"})


async def delete_box_data(identity, route):
    """Use the normal secret-authenticated forwarder, retaining the box outcome."""
    import asyncio

    path = "/_box/account/delete"
    scope = {"type": "http", "method": "POST", "path": path, "raw_path": path.encode(),
             "query_string": b"", "headers": [], "http_version": "1.1"}
    delivered = False
    messages = []

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Future()

    async def send(message):
        messages.append(message)

    await forward(scope, receive, send, identity, route)
    from starlette.responses import Response

    return Response(b"".join(m.get("body", b"") for m in messages),
                    status_code=messages[0]["status"], media_type="application/json",
                    headers={"cache-control": "no-store"})
