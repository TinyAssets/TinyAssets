"""Interactive owner-only browser view transport. No bearer-only capture access."""
import json
import time
from contextlib import closing

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from tinyassets.onboarding.owner_sessions import HEADERS, require


def perform(root, owner, home, session, data):
    from tinyassets import bound_requests
    from tinyassets.browser_sessions import owner_action
    from tinyassets.owner_control import control
    from tinyassets.storage.pending_requests import get_request, resolve_request

    with control(root / home):
        action = data.get('action')
        row = None
        if action not in {'list', 'revoke'}:
            row = get_request(root / home, data.get('request_id', ''))
            if (not row or row['action'] != {'type': 'connect_browser',
                                             'connection_id': data.get('id')}
                    or row['status'] not in {'pending', 'answered'}):
                raise PermissionError('browser request ended')
            context = row.get('asking_context', {})
            if context.get('kind') == 'connection':
                with closing(bound_requests.connect(root / home)) as conn:
                    task = conn.execute('SELECT * FROM activities WHERE activity_id=?',
                                        (context['task_id'],)).fetchone()
                if (context['owner'] != owner or context['home'] != home or not task
                        or task['task_generation'] != context['task_generation']
                        or task['status'] in {'paused', 'completed', 'failed'}
                        or task['task_expires_at'] <= time.time()):
                    raise PermissionError('browser task ended')
            if row['status'] == 'answered' and action != 'frame':
                raise PermissionError('browser request already completed')
        result = owner_action(root, owner, home, session, data)
        if action == 'frame' and result.get('status') == 'connected' and row:
            if row['status'] == 'pending' and not resolve_request(
                    root / home, row['request_id'], status='answered', decision='allowed',
                    answer={'connection_id': data['id'], 'status': 'connected'}):
                raise RuntimeError('completion pending')
        return result


async def handle(request):
    from tinyassets.api.helpers import _base_path
    from tinyassets.auth.middleware import current_identity
    from tinyassets.daemon_server import get_founder_home
    from tinyassets.onboarding import _read_small_json, onboarding_enabled

    if not onboarding_enabled():
        return JSONResponse({'error': 'not_found'}, status_code=404)
    try:
        session = require(request, owner=current_identity().user_id)
    except PermissionError:
        return JSONResponse({'error': 'interactive_approval_required'},
                            status_code=403, headers=HEADERS)
    owner = json.loads(session['identity_json'])['user_id']
    data = await _read_small_json(request, limit=16000)
    root = _base_path()
    home = await run_in_threadpool(get_founder_home, root, owner)
    if not data or not home or data.get('universe_id') != home:
        return JSONResponse({'error': 'browser_scope_refused'}, status_code=403, headers=HEADERS)
    try:
        result = await run_in_threadpool(perform, root, owner, home, session, data)
        return JSONResponse(result, status_code=409 if result.get('error') else 200,
                            headers=HEADERS)
    except Exception:  # noqa: BLE001 - login data and browser exception traces stay private
        return JSONResponse({'error': 'browser_operation_unavailable'},
                            status_code=409, headers=HEADERS)
