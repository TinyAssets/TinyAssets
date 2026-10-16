"""Real owner daemon used by the router transport acceptance suite (Linux)."""
import asyncio
import json
import sys
from pathlib import Path

import uvicorn

from tinyassets.owner_daemon import create_app


def main():
    root, relay, secret, socket = map(Path, sys.argv[1:])
    app = create_app(data=root, relay=relay, secret_file=secret)
    from tinyassets.daemon_server import grant_universe_ownership, set_founder_home
    from tinyassets.owner_runtime import current

    owner = current().principal
    center = "cc-" + owner
    folder = root / center
    folder.mkdir(exist_ok=True)
    (folder / "hello.txt").write_text(owner + " private data")
    grant_universe_ownership(root, universe_id=center, owner_id=owner)
    set_founder_home(root, founder_sub=owner, universe_id=center, platform_generated=True)
    from tinyassets.api_keys import ApiKeyStore
    from tinyassets.outside_authority import OutsideClientAuthority

    authority = OutsideClientAuthority(root)
    with authority.db() as db:
        db.execute("INSERT OR REPLACE INTO outside_switch VALUES (1, 1)")
    key = ApiKeyStore(root).mint(owner, name="router acceptance", authority=authority,
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

    # Transport probes supplement the production file/MCP/REST handlers above.
    app.routes.insert(0, Route("/app/api/test-stream", stream, methods=["GET"]))
    app.routes.insert(0, Route("/hooks/inbound/{token}", delivery, methods=["POST"]))
    # Use the real MCP transport lifespan; no model calls or host services needed.
    uvicorn.run(app, uds=str(socket), log_level="warning")


if __name__ == "__main__":
    main()
