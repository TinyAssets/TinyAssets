"""Real owner daemon used by the router transport acceptance suite (Linux)."""
import asyncio
import json
import os
import sys
from pathlib import Path

from tinyassets.owner_daemon import create_app


def main():
    from tinyassets.platform_secrets import CHILD_FORBIDDEN_ENV, ENGINE_REQUIRED_SECRET_ENV

    for name in CHILD_FORBIDDEN_ENV | ENGINE_REQUIRED_SECRET_ENV.keys():
        os.environ.pop(name, None)
    root, relay, secret, socket = map(Path, sys.argv[1:])
    import tinyassets.owner_runtime as owner_runtime

    # A test process has no boxhostd mount: the fixed mount is this temp root.
    owner_runtime.OWNER_MOUNT = root
    owner_runtime._is_owner_mount = lambda path: path == root
    app = create_app(relay=relay, secret_file=secret)
    from tinyassets.daemon_server import grant_universe_ownership, set_founder_home
    from tinyassets.owner_runtime import current

    owner = current().principal
    center = "cc-" + owner
    folder = root / center
    folder.mkdir(exist_ok=True)
    (folder / "hello.txt").write_text(owner + " private data")
    grant_universe_ownership(root, universe_id=center, owner_id=owner)
    set_founder_home(root, founder_sub=owner, universe_id=center, platform_generated=True)
    from tinyassets.api_keys import current_store
    from tinyassets.outside_authority import OutsideClientAuthority

    authority = OutsideClientAuthority(root)
    with authority.db() as db:
        db.execute("INSERT OR REPLACE INTO outside_switch VALUES (1, 1)")
    key = current_store().mint(owner, name="router acceptance", authority=authority,
                                scopes=[{"universe": center, "agent": "main",
                                         "capability": "read_graph"}])
    (root / "key.json").write_text(json.dumps(key))
    from starlette.responses import JSONResponse, StreamingResponse
    from starlette.routing import Route

    async def stream(request):
        async def chunks():
            try:
                yield b"data: first\n\n"
                await asyncio.sleep(60)
                yield b"data: last\n\n"
            finally:
                (root / "stream-closed").write_text("closed")
        return StreamingResponse(chunks(), media_type="text/event-stream",
                                 headers={"mcp-session-id": "box-stream"})

    async def delivery(request):
        from tinyassets.auth.middleware import current_identity

        return JSONResponse({"owner": current_identity().user_id,
                             "payload": (await request.body()).decode()}, status_code=202)

    async def protected_session(request):
        from tinyassets.auth.middleware import current_identity
        from tinyassets.onboarding.owner_sessions import require

        proof = require(request, owner=current_identity().user_id)
        return JSONResponse({"owner": json.loads(proof["identity_json"])["user_id"]})

    async def credentials(request):
        from tinyassets.storage import webhook_hooks

        data = await request.json()
        operation = data["operation"]
        if operation == "account_revoke":
            result = owner_runtime.router_credential(operation)
        elif operation == "key_list":
            result = current_store().list(owner, authority=authority)
        elif operation == "key_revoke":
            result = current_store().revoke(owner, data["key_id"], authority=authority)
        elif operation == "hook_mint":
            token = webhook_hooks.mint(root, universe_id=center, branch_def_id="main",
                                       owner_principal_id="request-field-is-ignored",
                                       source_id=data.get("source_id"))
            result = {"token": token}
        elif operation == "hook_revoke":
            result = {"revoked": webhook_hooks.revoke(root, token=data["token"])}
        elif operation == "hook_revoke_prefix":
            result = {"revoked": webhook_hooks.revoke_by_prefix(
                root, universe_id=center, token_prefix=data["prefix"])}
        elif operation == "hook_revoke_source":
            result = {"revoked": webhook_hooks.revoke_source(
                root, universe_id=center, source_id=data["source_id"])}
        else:
            raise ValueError(operation)
        return JSONResponse(result)

    app.routes.insert(0, Route("/app/test-credentials", credentials, methods=["POST"]))
    # Transport probes supplement the production file/MCP/REST handlers above.
    app.routes.insert(0, Route("/app/api/test-stream", stream, methods=["GET"]))
    app.routes.insert(0, Route("/hooks/inbound/{token}", delivery, methods=["POST"]))
    app.routes.insert(0, Route("/app/test-session", protected_session, methods=["POST"]))
    # Use the real MCP transport lifespan; no model calls or host services needed.
    from tinyassets.owner_daemon import serve

    serve(app, socket)


if __name__ == "__main__":
    main()
