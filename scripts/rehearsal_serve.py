"""Caller-side HTTP assertions against the compose-started production daemon."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path


def router_snapshot_call(owner, action):
    """Run a synthetic router action only after real broker snapshot delivery."""
    from tinyassets.router_credentials import serving
    from tinyassets.routing_snapshot import snapshot

    async def run():
        root = Path('/data')
        async with serving(root):
            deadline = time.monotonic() + 30
            while True:
                try:
                    snapshot(root).record(owner)
                    break
                except RuntimeError:
                    if time.monotonic() >= deadline:
                        raise
                    await asyncio.sleep(.01)
            return await asyncio.to_thread(action)

    return asyncio.run(run())



def broker(directory):
    """Offline C14 recovery fixture; never used for host_startup or serving."""
    os.environ.setdefault("BACKUP_DEST", ":local:/snapshots/owner-snapshots")
    from tinyassets.boxes import daemon_control
    from tinyassets.broker.fence import Fence
    from tinyassets.broker.ops import OpStore
    from tinyassets.broker.owner_identities import OwnerIdentities
    from tinyassets.broker.owner_reads import Plans
    from tinyassets.broker.process import _Dispatchers
    from tinyassets.broker.server import BOX_HOST, BrokerServer

    host_request = daemon_control.request

    def traced_host_request(operation, *args, **kwargs):
        # Synthetic rehearsal diagnostics: preserve call boundaries without
        # printing launch records or credentials, or changing timeout behavior.
        owner = kwargs.get("uid", args[0] if args else "platform")
        print(f"host begin op={operation} owner={owner}", file=sys.stderr, flush=True)
        try:
            return host_request(operation, *args, **kwargs)
        finally:
            print(f"host end op={operation} owner={owner}", file=sys.stderr, flush=True)

    daemon_control.request = traced_host_request

    run = Path(directory)
    os.setgroups([1102])
    os.setgid(1002)
    os.setuid(1002)
    dispatch = _Dispatchers(Path("/data"), allow_test_fixtures=False)
    identities = OwnerIdentities(
        Path("/data/.broker/state/owner-identities.db"), plans=Plans("/data")
    )
    server = BrokerServer(
        ledger_for=dispatch.ledger_for,
        dispatch_for=dispatch.dispatch_for,
        ops=OpStore(Path("/data/.broker/state/ops.db")),
        fence=Fence(
            run / "fence.json",
            verify_lease_proof=lambda _, proof: proof == "synthetic-rehearsal-proof" * 2,
            lease_sha256=hashlib.sha256(("synthetic-rehearsal-proof" * 2).encode()).hexdigest(),
        ),
        roles={0: BOX_HOST, 1001: "owner"},
        owner_identities=identities,
        data_root=Path("/data"),
    )

    async def serving():
        listener = await server.serve(run / "broker.sock")
        (run / "broker.sock").chmod(0o666)
        async with listener:
            stop = asyncio.Event()
            from tinyassets.broker.waker import Waker

            publication = asyncio.create_task(Waker(
                server.next_wakes, identities.plans, wake=server.routing.wake,
                lifecycle_tick=server.routing.tick, poll_seconds=1).run(stop))
            try:
                await listener.serve_forever()
            finally:
                stop.set()
                await publication

    asyncio.run(serving())



def seed(document):
    """Synthetic caller credentials, through the same schema as app sign-in."""
    from tinyassets.auth.provider import Identity, OAuthProvider, supported_oauth_scopes
    from tinyassets.onboarding import owner_sessions

    plan = json.loads(document)
    provider = OAuthProvider('/data/.auth.db')
    client = provider.register_client({'client_name': 'Synthetic rehearsal',
                                       'redirect_uris': ['https://rehearsal.invalid/callback']})
    credentials = {}
    for row in plan:
        owner = row['principal']
        token = 'synthetic-rehearsal-' + owner
        scopes = supported_oauth_scopes()
        with provider._connect() as db:
            db.execute('INSERT OR REPLACE INTO access_tokens VALUES (?,?,?,?,?,?,0)',
                       (hashlib.sha256(token.encode()).hexdigest(), client['client_id'],
                        owner, ' '.join(scopes), time.time() + 3600, time.time()))
        with owner_sessions.store() as db:
            db.execute('INSERT OR REPLACE INTO owner_sessions VALUES (?,?,?)',
                       (owner_sessions.hashed(token), json.dumps(Identity(
                           user_id=owner, username=owner, capabilities=scopes).to_dict()),
                        time.time() + 3600))
        credentials[owner] = token
    print(json.dumps({'plan': plan, 'credentials': credentials}))



def request_from_user(client, center, payload):
    """Exercise the public MCP request rail, then the ordinary app deposit door."""
    headers = {'Accept': 'application/json, text/event-stream'}

    def rpc(method, params, number=None):
        document = {'jsonrpc': '2.0', 'method': method, 'params': params}
        if number is not None:
            document['id'] = number
        request = client.build_request('POST', '/mcp', headers=headers, json=document)
        # An external MCP client uses its bearer credential, without browser state.
        del request.headers['origin']
        del request.headers['cookie']
        response = client.send(request)
        assert response.status_code in {200, 202}, response.text
        if session := response.headers.get('mcp-session-id'):
            headers['mcp-session-id'] = session
        if number is None:
            return None
        if response.headers.get('content-type', '').startswith('text/event-stream'):
            replies = [json.loads(line[6:]) for line in response.text.splitlines()
                       if line.startswith('data: ')]
            reply = next((item for item in replies if item.get('id') == number), None)
            assert reply is not None, response.text
        else:
            reply = response.json()
        assert 'error' not in reply, reply
        return reply['result']

    rpc('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {},
                       'clientInfo': {'name': 'synthetic-rehearsal', 'version': '1'}}, 1)
    rpc('notifications/initialized', {})
    result = rpc('tools/call', {'name': 'write_graph', 'arguments': {
        'target': 'connection', 'operation': 'request_from_user', 'graph_id': center,
        'payload_json': json.dumps(payload)}}, 2)
    assert not result.get('isError'), result
    request = json.loads(next(item['text'] for item in result['content'] if item['type'] == 'text'))
    assert request.get('status') == 'pending', request
    return request


def ask_connection(client, center):
    payload = {'kind': 'API', 'title': 'Reconnect synthetic source',
               'body': 'Synthetic credential, never sent externally',
               'fields': [{'name': 'secret', 'type': 'secret', 'label': 'Key'}],
               'action': {'type': 'connect', 'destination': 'rehearsal.invalid',
                          'auth_scheme': 'bearer', 'host': 'rehearsal.invalid',
                          'path_template': '/v1/data', 'methods': ['GET']}}
    return request_from_user(client, center, payload)['request_id']

def smoke(document):
    """Only public HTTP requests; no replacement router, broker or owner server."""
    import httpx

    inputs = json.loads(document)
    counts = dict.fromkeys(('owners', 'centers', 'history', 'history_turns', 'pending',
                           'pending_saved', 'controls', 'plans', 'writes', 'reconnect',
                           'production_launch'), 0)
    for row in inputs['plan']:
        token = inputs['credentials'][row['principal']]
        with httpx.Client(base_url='http://127.0.0.1:8001', timeout=120,
                          headers={'Authorization': 'Bearer ' + token,
                                   'Cookie': '__Host-ta-owner=' + token,
                                   'Origin': 'https://rehearsal.invalid',
                                   'Host': 'rehearsal.invalid'}) as client:
            def call(path, data=None):
                response = (client.get(path) if data is None else client.post(path, json=data))
                assert response.status_code == 200, (path, response.status_code, response.text)
                result = response.json()
                assert 'error' not in result, (path, result)
                return result

            for center in row['centers']:
                result = call('/app/api/status', {'universe_id': center,
                                                  'include_conversation': True})
                history = result['recent_conversation']
                assert 'error' not in history
                counts['history'] += 1
                counts['history_turns'] += history['turn_count']
                pending = call('/app/api/read', {'target': 'pending_requests', 'graph_id': center})
                counts['pending'] += 1
                counts['pending_saved'] += sum(not r['request_id'].startswith('sys_')
                                                for r in pending['pending'])
                # The real app's stop and pending controls, not private handler calls.
                assert 'interrupted' in call('/app/turn/interrupt', {'universe_id': center})
                assert 'pending' in call('/app/turn/pending', {'universe_id': center})
                counts['controls'] += 2
                plan = call('/app/billing/status')
                assert plan['tier'] in {'paid', 'free'}
                counts['plans'] += 1
                written = request_from_user(client, center, {
                    'kind': 'Question', 'title': 'Synthetic serving write',
                    'body': 'rehearsal write',
                    'fields': [{'name': 'answer', 'type': 'text', 'label': 'Answer'}],
                    'action': {'type': 'answer'}})
                readback = call('/app/api/read', {'target': 'pending_requests', 'graph_id': center})
                assert any(r['request_id'] == written['request_id']
                           and r['body'] == 'rehearsal write' for r in readback['pending'])
                counts['writes'] += 1
                counts['centers'] += 1
            # Deposit synthetic custody at the real shared door. No external call.
            request_id = ask_connection(client, row['centers'][0])
            connected = call('/app/model-connect/deposit', {
                'request_ref': request_id,
                'secret': 'synthetic-rehearsal-key-never-sent',
                'definition': {'destination': 'rehearsal.invalid', 'auth_scheme': 'bearer',
                               'endpoints': [{'host': 'rehearsal.invalid',
                                              'path_template': '/v1/data', 'methods': ['GET']}]}})
            assert connected['status'] == 'provisioned'
            reconciled = call('/app/api/read', {'target': 'pending_requests',
                                                'graph_id': row['centers'][0]})
            assert any(r['request_id'] == request_id and r['status'] == 'answered'
                       for r in reconciled['recently_answered']), reconciled
            counts['reconnect'] += 1
            counts['owners'] += 1
    counts['production_launch'] = 1
    print(json.dumps(counts))



async def owner_operations(action, center, operations, principal):
    """Caller probe executed inside an already-running production owner box."""
    import subprocess
    import uuid
    from pathlib import Path

    from tinyassets.boxes.boxd import OwnerBoxd
    from tinyassets.boxes.provider import BoxOperationRefused

    env = dict(part.split(b'=', 1) for part in Path('/proc/1/environ').read_bytes().split(b'\0')
               if b'=' in part)
    root = Path('/home/owner')
    # A caller-owned operation journal inside the real sandbox. Never acquire
    # the serving daemon's journal or launch another owner runtime.
    core = OwnerBoxd(owner_id=principal, owner_root=root,
                     state_dir=root / '.rehearsal-operations',
                     volume_generation=int(env[b'TINYASSETS_VOLUME_GENERATION']))
    handle = core.bind(principal)
    logical = '/cc/' + center + '/.restore-proof'

    def bash(operation, command):
        core.consume_operation(handle, operation, 'bash', {'command': command})
        subprocess.run(['bash', '-c', command], cwd=root / center, check=True)
    try:
        if action == 'prepare':
            core.write(handle, core.new_operation(uuid.uuid4().hex), logical,
                       b'snapshot', max_bytes=100)
            return {key: core.new_operation(uuid.uuid4().hex)
                    for key in ('write', 'bash', 'unseen')}
        if action == 'mutate':
            core.write(handle, operations['write'], logical, b'after', max_bytes=100)
            bash(operations['bash'], 'printf once > .restore-bash')
            return {'mutated': True}
        assert (root / center / '.restore-proof').read_bytes() == b'snapshot'
        refused = 0
        for operation in (operations['write'], operations['unseen']):
            try:
                core.write(handle, operation, logical, b'replay', max_bytes=100)
            except BoxOperationRefused:
                refused += 1
            else:
                raise AssertionError('old-generation write replayed')
        try:
            bash(operations['bash'], 'printf replay > .restore-bash')
        except BoxOperationRefused:
            refused += 1
        else:
            raise AssertionError('old-generation bash replayed')
        assert not (root / center / '.restore-bash').exists()
        core.write(handle, core.new_operation(uuid.uuid4().hex), logical,
                   b'new work', max_bytes=100)
        bash(core.new_operation(uuid.uuid4().hex), 'printf fresh > .restore-bash')
        assert (root / center / '.restore-bash').read_text() == 'fresh'
        return {'replay_refusals': refused, 'new_operations': 2}
    finally:
        core.close()


def key_call(token, center, operation, key_id=""):
    """Caller-side shared credential fixture for the owner-snapshot restore proof."""
    from tinyassets.api_keys import ApiKeyStore

    assert token.startswith('synthetic-rehearsal-')
    owner = token.removeprefix('synthetic-rehearsal-')
    keys = ApiKeyStore('/data')
    if operation == 'create':
        result = keys.mint(owner, name='synthetic restore proof', scopes=[], authority=None)
    elif operation == 'revoke':
        result = keys.revoke(owner, key_id, authority=None)
    else:
        assert operation == 'list'
        result = keys.list(owner, authority=None)
    print(json.dumps(result))


if __name__ == '__main__':
    {'seed': seed, 'smoke': smoke, 'broker': broker, 'key': key_call}[sys.argv[1]](*sys.argv[2:])
