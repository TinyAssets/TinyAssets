"""Daemon browser coordinator. Agent calls never reach capture or vault payloads."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from tinyassets import browser_egress
from tinyassets.broker.browser_vault import operation
from tinyassets.owner_control import control

_captures = {}
_guard = threading.RLock()
BOUND = 8 * 1024 * 1024


class Cell:
    def __init__(self, root, owner, home):
        from tinyassets import role_decoder
        from tinyassets.broker.owner_identities import owner_identity

        client = role_decoder._bounded_client
        if client is None:
            raise RuntimeError('owner browser launcher unavailable')
        identity = owner_identity(root, principal=owner)
        self.job = client.start_cell(kind='browser', principal=owner,
                                    command_center=home, identity=identity)
        self.job.stream.settimeout(45)
        self.stream = self.job.stream.makefile('rwb', buffering=0)
        self.proof = self.read().get('cell', {})
        if (self.proof.get('uid') != identity.uid - 300000
                or self.proof.get('gid') != identity.gid - 300000
                or self.proof.get('fds') != [0, 1, 2]
                or self.proof.get('caps') != 'zero' or self.proof.get('nnp') != 1
                or self.proof.get('profile') != 'cell-nested'):
            self.close()
            raise RuntimeError('owner browser cell proof absent')

    def read(self):
        raw = self.stream.readline(BOUND + 1)
        if len(raw) > BOUND or not raw.endswith(b'\n'):
            raise RuntimeError('browser outcome unknown')
        return json.loads(raw)

    def send(self, document):
        raw = json.dumps(document).encode() + b'\n'
        if len(raw) > BOUND:
            raise ValueError('browser packet exceeds bounds')
        self.stream.write(raw)

    def call(self, document, checkpoint=lambda: None):
        checkpoint()
        self.send(document)
        while True:
            value = self.read()
            if 'network' in value:
                try:
                    checkpoint()
                    reply = {'response': browser_egress.fetch(value['request'])}
                except Exception:  # noqa: BLE001 - never forward network exception details
                    reply = {'error': 'network refused'}
                self.send({'network_reply': value['network'], **reply})
            else:
                return value['result']

    def close(self):
        self.stream.close()
        self.job.close()


def public(row):
    return {key: row[key] for key in ('id', 'url', 'revision', 'status', 'account')}


def _vault(root, owner, home, action, **values):
    return operation(root, owner, home, dict(action=action, **values))


def verify_state(root, owner, home, row, state, checkpoint):
    cell = Cell(root, owner, home)
    try:
        opened = cell.call(dict(action='open', capture=False, url=row['verify']['url'],
                                state=state, verify=row['verify']), checkpoint)
        if opened.get('error') or opened.get('blocked'):
            return False
        return 'state' in cell.call({'action': 'verify'}, checkpoint)
    finally:
        cell.close()


def agent(root, context, arguments, checkpoint=lambda: None):
    owner, home = context.owner, context.universe
    action = arguments.get('action', 'list')

    def request_login(row):
        from tinyassets.api.pending_requests import request_from_user

        request = request_from_user(universe_id=home, payload={
            'kind': 'Browser', 'title': 'Sign in · ' + browser_egress.origin(row['url']),
            'body': 'Sign in on the website. Your agent can then use this site for you.',
            'action': {'type': 'connect_browser', 'connection_id': row['id']}})
        return {**public(row), 'needs_login': True, 'request': request}

    with control(Path(root) / home):
        if action == 'list':
            return _vault(root, owner, home, 'list')
        if action == 'connect':
            created = _vault(root, owner, home, 'create', url=arguments['url'],
                             account=arguments.get('account', ''), verify=arguments.get('verify'))
            if created.get('needs_account'):
                return created
            ident = created['id']
        else:
            ident = arguments.get('id', '')
        row = _vault(root, owner, home, 'read', id=ident)
        if action == 'reconnect':
            return request_login(row)
        if action not in {'steps', 'connect'}:
            raise ValueError('unsupported browser capability')
        if row['status'] != 'connected':
            with _guard:
                capturing = (str(root), owner, home, ident) in _captures
            if capturing or not row['state']:
                return request_login(row)
        from tinyassets.effectors.authenticated_external_call import _rule_refusal

        refusal = _rule_refusal(Path(root) / home, ident, 'BROWSER',
                                evidence=json.dumps(arguments.get('steps', []))[:2000],
                                agent=context.initiating_agent)
        if refusal:
            return refusal
        checkpoint()
        cell = Cell(root, owner, home)
        try:
            opened = cell.call(dict(action='open', capture=False, url=row['verify']['url'],
                                    verify=row['verify'], state=row['state']), checkpoint)
            if opened.get('needs_login'):
                return request_login(row)
            if opened.get('error') or opened.get('blocked'):
                return opened
            verified = cell.call({'action': 'verify'}, checkpoint)
            if verified.get('needs_login'):
                return request_login(row)
            if 'state' not in verified:
                return {k: v for k, v in verified.items() if k != 'state'}
            if action == 'connect':
                if row['status'] != 'connected':
                    row = _vault(root, owner, home, 'save', id=ident,
                                 revision=row['revision'], state=verified['state'])
                return {**public(row), 'remembered': True}
            result = cell.call(dict(action='steps', steps=arguments.get('steps', [])), checkpoint)
            state = result.pop('state', None)
            if result.get('needs_login'):
                return {**request_login(row), 'completed_steps': result.get('completed_steps', 0),
                        'replay': 'Do not replay prior steps; reconcile their outcomes.'}
            if state is not None:
                checkpoint()
                _vault(root, owner, home, 'save', id=ident, revision=row['revision'], state=state)
            return result
        finally:
            cell.close()


def _drop(key):
    with _guard:
        capture = _captures.pop(key, None)
    if capture:
        capture['timer'].cancel()
        capture['cell'].close()


def owner_action(root, owner, home, session, data):
    ident, action = data.get('id', ''), data.get('action')
    key = (str(root), owner, home, ident)
    with control(Path(root) / home):
        def checkpoint():
            from tinyassets.onboarding.owner_sessions import store

            with store() as conn:
                live = conn.execute('SELECT 1 FROM owner_sessions '
                    'WHERE session_hash=? AND expires_at>?',
                    (session['session_hash'], time.time())).fetchone()
            if not live:
                _drop(key)
                raise PermissionError('owner session ended')

        checkpoint()
        if action == 'list':
            return _vault(root, owner, home, 'list')
        if action == 'revoke':
            result = _vault(root, owner, home, 'revoke', id=ident)
            _drop(key)
            return result
        row = _vault(root, owner, home, 'read', id=ident)
        if action == 'frame' and row['status'] == 'connected' and key not in _captures:
            return public(row)  # Recover a lost response/continuation commit, never re-login.
        if action == 'begin':
            _drop(key)
            if verify_state(root, owner, home, row, None, checkpoint):
                return {'error': 'verification page is public; choose an authenticated check'}
            prior = row['state']
            verify = row['verify']
            row = _vault(root, owner, home, 'capture', id=ident, revision=row['revision'])
            cell = Cell(root, owner, home)
            timer = threading.Timer(600, _drop, (key,))
            timer.daemon = True
            with _guard:
                _captures[key] = dict(cell=cell, session=session['session_hash'], timer=timer,
                                      revision=row['revision'], expires=time.time() + 600)
            timer.start()
            result = cell.call(dict(action='open', capture=True, url=row['url'], state=prior,
                                   verify=verify), checkpoint)
            if result.get('error') or result.get('blocked'):
                _drop(key)
            return {**result, 'id': ident}
        with _guard:
            capture = _captures.get(key)
        if (capture is None or capture['session'] != session['session_hash']
                or capture['expires'] <= time.time() or capture['revision'] != row['revision']):
            raise PermissionError('browser capture unavailable; reconnect')
        if action == 'cancel':
            _drop(key)
            return _vault(root, owner, home, 'cancel', id=ident, revision=row['revision'])
        if action not in {'frame', 'input', 'fill_private'}:
            raise ValueError('unsupported browser owner operation')
        command = {'action': action}
        if action == 'input':
            command['event'] = data['event']
        elif action == 'fill_private':
            command.update({key: data[key] for key in ('token', 'origin', 'value')})
        result = capture['cell'].call(command, checkpoint)
        if result.get('blocked'):
            _drop(key)
        if 'state' in result:
            if not verify_state(root, owner, home, row, result['state'], checkpoint):
                return {'needs_login': True}
            checkpoint()
            if capture['expires'] <= time.time() or _captures.get(key) is not capture:
                raise PermissionError('browser capture expired')
            saved = _vault(root, owner, home, 'save', id=ident, revision=row['revision'],
                           state=result['state'])
            _drop(key)
            return saved
        return {**result, 'account': row['account']}
