"""Versioned encrypted browser custody, reachable only on the fenced owner channel."""
from __future__ import annotations

import json
import os
import re
import secrets
import socket

from tinyassets import rpc_frames as rf
from tinyassets.broker.admission import digest

MAX_STATE = 2 * 1024 * 1024


def origin(url):
    """The public HTTPS origin of a browser connection URL."""
    from urllib.parse import urlsplit

    if not isinstance(url, str) or len(url) > 8192:
        raise ValueError('invalid browser URL')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.port not in (None, 443)
            or any(ord(c) < 33 for c in url) or '\\' in url):
        raise ValueError('browser requires public HTTPS')
    return 'https://' + parsed.hostname.lower()


def local_operation(ledger, *, principal, command_center, document):
    from tinyassets.broker.key_service import service_for

    action = document.get('action')
    ident = document.get('id', '')
    if (action not in {'create', 'list', 'read', 'save', 'revoke', 'capture', 'cancel'}
            or set(document) - {'action', 'id', 'url', 'revision', 'state', 'account', 'verify'}
            or (action not in {'create', 'list'} and not re.fullmatch('[a-f0-9]{32}', ident))):
        raise ValueError('invalid browser custody operation')
    with ledger._connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS browser_vault ('
                     'id TEXT PRIMARY KEY, owner TEXT NOT NULL, '
                     'url TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL, '
                     'sealed BLOB NOT NULL, account TEXT NOT NULL, verify TEXT NOT NULL)')
        conn.execute('BEGIN IMMEDIATE')
        if action == 'create':
            site = origin(document['url'])
            account = document.get('account', '')
            verify = document.get('verify', {})
            if not isinstance(account, str) or len(account) > 200:
                raise ValueError('invalid account label')
            existing = conn.execute(
                "SELECT id,url,revision,status,account FROM browser_vault "
                "WHERE owner=? AND status<>'revoked'",
                (principal,)).fetchall()
            matches = [dict(item) for item in existing if origin(item['url']) == site
                       and (not account or item['account'] == account)]
            if len(matches) > 1:
                return {'accounts': matches, 'needs_account': True}
            if matches:
                return matches[0]
            if (not isinstance(verify, dict) or set(verify) != {'url', 'selector'}
                    or origin(verify['url']) != site
                    or not isinstance(verify['selector'], str)
                    or not 1 <= len(verify['selector']) <= 500):
                raise ValueError('an authenticated-page verification URL and selector are required')
            ident = secrets.token_hex(16)
            conn.execute('INSERT INTO browser_vault VALUES (?,?,?,?,?,?,?,?)',
                         (ident, principal, document['url'], 1, 'needs_login',
                          b'', account, json.dumps(verify)))
        if action == 'list':
            return {'items': [dict(row) for row in conn.execute(
                'SELECT id,url,revision,status,account FROM browser_vault '
                "WHERE owner=? AND status<>'revoked' ORDER BY id",
                (principal,))]}
        row = conn.execute('SELECT * FROM browser_vault WHERE id=? AND owner=?',
                           (ident, principal)).fetchone()
        if row is None:
            raise PermissionError('browser connection unavailable')
        if row['status'] == 'revoked':
            if action == 'revoke':
                return {key: row[key] for key in ('id', 'url', 'revision', 'status', 'account')}
            raise PermissionError('browser connection unavailable')
        revision = row['revision']
        if action in {'save', 'revoke', 'capture', 'cancel'}:
            if ((action != 'revoke' or 'revision' in document)
                    and document.get('revision') != revision):
                raise PermissionError('browser connection changed')
            revision += 1
        keys = service_for(ledger._data_root)
        owner = digest(principal)
        slot = f"browser/{ident}/{revision}"
        status = row['status']
        if action in {'save', 'capture', 'cancel'}:
            old_slot = f"browser/{ident}/{row['revision']}"
            sealed = row['sealed']
            state = (json.loads(keys.open(owner, old_slot, sealed))
                     if sealed else {})
            if action == 'save':
                state = document['state']
            raw = json.dumps(state).encode()
            if len(raw) > MAX_STATE or not isinstance(state, dict):
                raise ValueError('browser state exceeds bounds')
            status = {'capture': 'login', 'cancel': 'needs_login', 'save': 'connected'}[action]
            conn.execute('UPDATE browser_vault SET revision=?,status=?,sealed=? WHERE id=?',
                         (revision, status, keys.seal(owner, slot, raw), ident))
        elif action == 'revoke':
            conn.execute("UPDATE browser_vault SET revision=?,status='revoked',sealed=? WHERE id=?",
                         (revision, b'', ident))
        result = dict(id=ident, url=row['url'], revision=revision,
                      status='revoked' if action == 'revoke' else status, account=row['account'])
        if action == 'read':
            sealed = row['sealed']
            result['state'] = (json.loads(keys.open(owner, slot, sealed))
                               if sealed else {})
            result['verify'] = json.loads(row['verify'])
        return result


def operation(root, owner, home, document):
    from tinyassets.broker.supervisor import get_supervisor

    supervisor = get_supervisor(root)
    if supervisor is None:
        raise RuntimeError('browser credential broker unavailable')
    generation, token = supervisor.fence()
    wire = dict(op='BROWSER_VAULT', principal=owner,
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
