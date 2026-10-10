"""Versioned encrypted browser custody, reachable only on the fenced owner channel."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import socket

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tinyassets import rpc_frames as rf

MAX_STATE = 2 * 1024 * 1024


def key_path(ledger, principal):
    directory = ledger._db_path.parent / 'browser-vault'
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory / (hashlib.sha256(principal.encode()).hexdigest() + '.key')


def local_operation(ledger, *, principal, command_center, document):
    from tinyassets.browser_egress import origin

    action = document.get('action')
    ident = document.get('id', '')
    if (action not in {'create', 'list', 'read', 'save', 'revoke', 'capture', 'cancel'}
            or set(document) - {'action', 'id', 'url', 'revision', 'state', 'account', 'verify'}
            or (action not in {'create', 'list'} and not re.fullmatch('[a-f0-9]{32}', ident))):
        raise ValueError('invalid browser custody operation')
    with ledger._connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS browser_vault ('
                     'id TEXT PRIMARY KEY, owner TEXT NOT NULL, home TEXT NOT NULL, '
                     'url TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL, '
                     'sealed BLOB NOT NULL, account TEXT NOT NULL, verify TEXT NOT NULL)')
        conn.execute('BEGIN IMMEDIATE')
        if action == 'create':
            site = origin(document['url'])
            account = document.get('account', '')
            verify = document.get('verify', {})
            if (not isinstance(account, str) or len(account) > 200
                    or not isinstance(verify, dict) or set(verify) != {'url', 'selector'}
                    or origin(verify['url']) != site
                    or not isinstance(verify['selector'], str)
                    or not 1 <= len(verify['selector']) <= 500):
                raise ValueError('an authenticated-page verification URL and selector are required')
            existing = conn.execute(
                "SELECT id,url,revision,status,account FROM browser_vault "
                "WHERE owner=? AND home=? AND status<>'revoked'",
                (principal, command_center)).fetchall()
            matches = [dict(item) for item in existing if origin(item['url']) == site
                       and (not account or item['account'] == account)]
            if len(matches) > 1:
                return {'accounts': matches, 'needs_account': True}
            if matches:
                return matches[0]
            ident = secrets.token_hex(16)
            conn.execute('INSERT INTO browser_vault VALUES (?,?,?,?,?,?,?,?,?)',
                         (ident, principal, command_center, document['url'], 1, 'needs_login',
                          b'', account, json.dumps(verify)))
        if action == 'list':
            return {'items': [dict(row) for row in conn.execute(
                'SELECT id,url,revision,status,account FROM browser_vault '
                "WHERE owner=? AND home=? AND status<>'revoked' ORDER BY id",
                (principal, command_center))]}
        row = conn.execute('SELECT * FROM browser_vault WHERE id=? AND owner=? AND home=?',
                           (ident, principal, command_center)).fetchone()
        if row is None or row['status'] == 'revoked':
            raise PermissionError('browser connection unavailable')
        revision = row['revision']
        if action in {'save', 'revoke', 'capture', 'cancel'}:
            if document.get('revision') != revision:
                raise PermissionError('browser connection changed')
            revision += 1
        path = key_path(ledger, principal)
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(AESGCM.generate_key(bit_length=256))
                stream.flush()
                os.fsync(stream.fileno())
        cipher = AESGCM(path.read_bytes())
        aad = json.dumps([1, principal, command_center, ident, revision]).encode()
        status = row['status']
        if action in {'save', 'capture', 'cancel'}:
            old_aad = json.dumps([1, principal, command_center, ident, row['revision']]).encode()
            sealed = row['sealed']
            state = (json.loads(cipher.decrypt(sealed[:12], sealed[12:], old_aad))
                     if sealed else {})
            if action == 'save':
                state = document['state']
            raw = json.dumps(state).encode()
            if len(raw) > MAX_STATE or not isinstance(state, dict):
                raise ValueError('browser state exceeds bounds')
            nonce = secrets.token_bytes(12)
            status = {'capture': 'login', 'cancel': 'needs_login', 'save': 'connected'}[action]
            conn.execute('UPDATE browser_vault SET revision=?,status=?,sealed=? WHERE id=?',
                         (revision, status, nonce + cipher.encrypt(nonce, raw, aad), ident))
        elif action == 'revoke':
            conn.execute("UPDATE browser_vault SET revision=?,status='revoked',sealed=? WHERE id=?",
                         (revision, b'', ident))
        result = dict(id=ident, url=row['url'], revision=revision,
                      status='revoked' if action == 'revoke' else status, account=row['account'])
        if action == 'read':
            sealed = row['sealed']
            result['state'] = (json.loads(cipher.decrypt(sealed[:12], sealed[12:], aad))
                               if sealed else {})
            result['verify'] = json.loads(row['verify'])
        return result


def operation(root, owner, home, document):
    from tinyassets.broker.supervisor import get_supervisor

    supervisor = get_supervisor(root)
    if supervisor is None:
        raise RuntimeError('browser credential broker unavailable')
    generation, token = supervisor.fence()
    wire = dict(op='BROWSER_VAULT', principal=owner, command_center=home,
                generation=generation, token=token, document=document)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(30)
        channel.connect(os.fspath(supervisor.socket_path))
        supervisor.verify_broker(channel)
        channel.sendall(rf.control(rf.CONNECTION, wire))
        frame = rf.read_frame_blocking(channel)
        if frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION:
            raise RuntimeError('browser custody outcome unknown')
        answer = frame.control()
    if answer.get('op') != 'BROWSER_RESULT':
        raise PermissionError('browser custody refused')
    return answer['result']
