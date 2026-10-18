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
    owner_runtime.RESTORE_EPOCH = Path("/dev/null")
    owner_runtime._is_owner_mount = lambda path: path == root
    # Like the router process rig, these apps share the oracle's network
    # namespace. Native egress has a separate null-netns, fixed-port proof.
    from tinyassets import owner_public_egress

    owner_public_egress.LISTEN_ADDRESS = ("127.0.0.1", 0)
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

    async def write(request):
        from tinyassets.conversation_store import record_turn
        from tinyassets.storage.pending_requests import create_request

        document = await request.json()
        folder = root / document['center']
        owner = request.headers['x-owner']
        record_turn(folder, f'principal:{owner}', 'founder', 'post-cutover chat')
        pending = create_request(folder, kind='Question', title='Post-cutover request',
                                 body='Synthetic served write', fields=[],
                                 action={'type': 'answer'}, dedupe_key='post-cutover')
        return JSONResponse({'request_id': pending['request_id']})

    app.routes.insert(0, Route('/app/test-cutover-write', write, methods=['POST']))
    serve(app, socket)


if __name__ == "__main__":
    main()
