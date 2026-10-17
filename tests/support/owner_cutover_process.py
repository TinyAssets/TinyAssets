"""Serve an already migrated tree; this launcher never seeds owner data."""
import json
import os
import sys
from pathlib import Path


def main():
    from tinyassets.platform_secrets import CHILD_FORBIDDEN_ENV, ENGINE_REQUIRED_SECRET_ENV

    for name in CHILD_FORBIDDEN_ENV | ENGINE_REQUIRED_SECRET_ENV.keys():
        os.environ.pop(name, None)
    root, relay, secret, socket = map(Path, sys.argv[1:])
    from tinyassets import owner_runtime
    from tinyassets.owner_daemon import create_app, serve

    # The process rig substitutes only the fixed mount supplied by boxhostd.
    owner_runtime.OWNER_MOUNT = root
    owner_runtime._is_owner_mount = lambda path: path == root
    app = create_app(relay=relay, secret_file=secret)
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def control(request):
        from tinyassets.api.universe import _action_control_daemon
        from tinyassets.broker.owner_reads import remote

        document = await request.json()
        result = json.loads(_action_control_daemon(
            universe_id=document['center'], text=document['action']))
        return JSONResponse({'control': result, 'plan': remote('plan')})

    # Like the existing router process rig, expose a test probe for a real
    # domain operation. History and request reads use production routes.
    app.routes.insert(0, Route('/app/test-cutover-control', control, methods=['POST']))
    serve(app, socket)


if __name__ == "__main__":
    main()
