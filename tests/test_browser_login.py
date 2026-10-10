"""Browser custody mutation/authority tests; production-image proof is a separate oracle."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from tinyassets.broker.browser_vault import local_operation
from tinyassets.browser_egress import origin
from tinyassets.storage.outbound_connections import ConnectionLedger


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr('tinyassets.broker.browser_vault.SECRET_ROOT',
                        tmp_path / 'secrets', raising=False)
    ledger = ConnectionLedger(tmp_path / 'data' / '.broker' / 'outbound.db',
                              data_root=tmp_path / 'data')

    def call(action, owner='alice', home='home-a', **kwargs):
        if action == 'create':
            kwargs.setdefault('verify', {'url': kwargs['url'], 'selector': '#account'})
        return local_operation(ledger, principal=owner, command_center=home,
                               document=dict(action=action, **kwargs))
    return ledger, call


def test_short_storage_values_preserve_untrusted_page_text():
    from unittest.mock import AsyncMock

    from tinyassets.browser_cell import Browser

    state = {'cookies': [{'value': '1'}, {'value': 'en'},
                         {'value': 'session-6a7f925bc013'}],
             'origins': [{'localStorage': [{'value': 'false'}],
                          'indexedDB': [{'value': 'on'}, {'value': 'undefined'}]}]}
    browser = Browser()
    browser.capture = False
    browser.page = SimpleNamespace(is_closed=lambda: False,
        locator=lambda _: SimpleNamespace(inner_text=AsyncMock(
            return_value='$1,234 English undefined session-6a7f925bc013')))
    browser.context = SimpleNamespace(storage_state=AsyncMock(return_value=state))
    browser.challenged = AsyncMock(return_value=False)
    result = asyncio.run(browser.command({'action': 'steps', 'steps': []}, None))
    assert result['untrusted'] is True
    assert result['text'] == '$1,234 English undefined [private]'


def test_vault_key_is_outside_data_and_legacy_key_migrates(vault):
    from tinyassets.broker.browser_vault import key_path

    ledger, call = vault
    row = call('create', url='https://example.com/')
    state = {'cookies': [{'value': 'secret-123456789'}]}
    call('save', id=row['id'], revision=1, state=state)
    path = key_path(ledger, 'alice')
    assert not path.is_relative_to(ledger._db_path.parent.parent)
    legacy = ledger._db_path.parent / 'browser-vault' / path.name
    legacy.parent.mkdir(exist_ok=True)
    original = path.read_bytes()
    path.replace(legacy)
    assert call('read', id=row['id'])['state'] == state
    assert path.read_bytes() == original
    assert not legacy.exists()
    assert call('read', id=row['id'])['state'] == state


def test_vault_refuses_secret_root_inside_data(vault, monkeypatch):
    ledger, call = vault
    monkeypatch.setattr('tinyassets.broker.browser_vault.SECRET_ROOT',
                        ledger._db_path.parent / 'keys', raising=False)
    with pytest.raises(ValueError, match='outside'):
        call('create', url='https://example.com/')


@pytest.mark.parametrize('conflict', [False, True])
def test_interrupted_legacy_migration_preserves_keys(vault, conflict):
    from tinyassets.broker.browser_vault import key_path

    ledger, call = vault
    row = call('create', url='https://example.com/')
    path = key_path(ledger, 'alice')
    legacy = ledger._db_path.parent / 'browser-vault' / path.name
    legacy.parent.mkdir()
    legacy.write_bytes(b'x' * 32 if conflict else path.read_bytes())
    if conflict:
        with pytest.raises(ValueError, match='conflicting'):
            call('read', id=row['id'])
        assert legacy.read_bytes() == b'x' * 32
        assert path.read_bytes() != legacy.read_bytes()
    else:
        assert call('read', id=row['id'])['state'] == {}
        assert not legacy.exists()


def test_migration_syncs_new_directory_before_unlink(vault, monkeypatch):
    from tinyassets.broker import browser_vault

    ledger, call = vault
    row = call('create', url='https://example.com/')
    path = browser_vault.key_path(ledger, 'alice')
    legacy = ledger._db_path.parent / 'browser-vault' / path.name
    legacy.parent.mkdir()
    path.replace(legacy)
    synced = []

    def sync(directory):
        if directory == path.parent.parent:
            assert legacy.exists()
        synced.append(directory)

    monkeypatch.setattr(browser_vault, '_sync_directory', sync)
    call('read', id=row['id'])
    assert synced == [path.parent, path.parent.parent, legacy.parent]
    assert not legacy.exists()


def test_concurrent_steps_reuse_rotated_cookie(vault, tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from tinyassets import browser_sessions, singleton_lock

    _, call = vault
    row = call('create', url='https://example.com/')
    call('save', id=row['id'], revision=1, state={'cookie': 0})
    opened, release, contended = threading.Event(), threading.Event(), threading.Event()
    seen = []
    real_lock = singleton_lock._lock_fd

    def observe(fd):
        acquired = real_lock(fd)
        if not acquired:
            contended.set()
        return acquired

    class Cell:
        def __init__(self, *args):
            pass

        def call(self, command, checkpoint):
            if command['action'] == 'open':
                self.cookie = command['state']['cookie']
                seen.append(self.cookie)
                opened.set()
                assert release.wait(10)
                return {'status': 'ready'}
            return {'untrusted': True, 'state': {'cookie': self.cookie + 1}}

        def close(self):
            pass

    monkeypatch.setattr(singleton_lock, '_lock_fd', observe)
    monkeypatch.setattr(browser_sessions, 'Cell', Cell)
    monkeypatch.setattr(browser_sessions, '_vault',
        lambda root, owner, home, action, **values: call(action, **values))
    monkeypatch.setattr('tinyassets.effectors.authenticated_external_call._rule_refusal',
                        lambda *args, **kwargs: None)
    monkeypatch.setenv('TINYASSETS_OWNER_CONTROL_WAIT_S', '30')
    context = SimpleNamespace(owner='alice', universe='home-a', initiating_agent='main')
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(browser_sessions.agent, tmp_path, context,
                            {'action': 'steps', 'id': row['id']})
        try:
            assert opened.wait(10)
            second = pool.submit(browser_sessions.agent, tmp_path, context,
                                 {'action': 'steps', 'id': row['id']})
            assert contended.wait(10)
        finally:
            release.set()
        assert first.result()['untrusted'] and second.result()['untrusted']
    assert seen == [0, 1]
    assert call('read', id=row['id'])['state'] == {'cookie': 2}


def test_encrypted_roundtrip_and_revocation_fence(vault):
    ledger, call = vault
    row = call('create', url='https://example.com/')
    state = {'cookies': [{'name': 'session', 'value': 'SECRET-session'}], 'origins': []}
    row = call('save', id=row['id'], revision=row['revision'], state=state)
    assert call('read', id=row['id'])['state'] == state
    with ledger._connect() as conn:
        sealed = conn.execute('SELECT sealed FROM browser_vault').fetchone()[0]
    assert b'SECRET-session' not in sealed
    call('revoke', id=row['id'], revision=row['revision'])
    with pytest.raises(PermissionError):
        call('save', id=row['id'], revision=row['revision'], state=state)
    with pytest.raises(PermissionError):
        call('read', id=row['id'])
    with ledger._connect() as conn:
        assert conn.execute('SELECT sealed FROM browser_vault').fetchone()[0] == b''


@pytest.mark.parametrize('action', ['read', 'save', 'capture', 'cancel', 'revoke'])
@pytest.mark.parametrize('scope', [{'owner': 'bob'}, {'home': 'home-b'}])
def test_foreign_scope_cannot_read_or_mutate(vault, action, scope):
    _, call = vault
    row = call('create', url='https://unknown.example/')
    with pytest.raises(PermissionError):
        call(action, id=row['id'], revision=1, state={}, **scope)
    assert call('read', id=row['id'])['revision'] == 1


def test_capture_fences_old_save_without_losing_state(vault):
    _, call = vault
    row = call('create', url='https://example.com/')
    row = call('save', id=row['id'], revision=1, state={'cookies': [], 'origins': []})
    capture = call('capture', id=row['id'], revision=row['revision'])
    assert capture['status'] == 'login'
    assert call('read', id=row['id'])['state'] == {'cookies': [], 'origins': []}
    with pytest.raises(PermissionError):
        call('save', id=row['id'], revision=row['revision'], state={})
    call('cancel', id=row['id'], revision=capture['revision'])
    with pytest.raises(PermissionError):
        call('save', id=row['id'], revision=capture['revision'], state={})


def test_ciphertext_cannot_be_transplanted(vault):
    from cryptography.exceptions import InvalidTag

    ledger, call = vault
    a = call('create', url='https://example.com/')
    b = call('create', url='https://example.com/', account='Second')
    call('save', id=a['id'], revision=1, state={'cookies': ['secret']})
    with ledger._connect() as conn:
        conn.execute('UPDATE browser_vault SET sealed=(SELECT sealed FROM browser_vault '
                     'WHERE id=?),revision=2 WHERE id=?', (a['id'], b['id']))
    with pytest.raises(InvalidTag):
        call('read', id=b['id'])


@pytest.mark.parametrize('url', ['http://example.com', 'file:///etc/passwd',
                                'https://user:pass@example.com/', 'https://example.com:8000/',
                                'https://example.com\\@localhost/', 'https://example.com/\n'])
def test_invalid_browser_urls(url):
    with pytest.raises(ValueError):
        origin(url)


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1'])
def test_private_dns_is_refused_before_connect(monkeypatch, address):
    from tinyassets import browser_egress
    from tinyassets.storage import outbound_connections as outbound

    monkeypatch.setattr(outbound, '_make_default_resolver', lambda _: lambda *args: [address])
    monkeypatch.setattr(outbound, '_default_open_socket',
                        lambda *args: pytest.fail('dialed private IP'))
    with pytest.raises(outbound.SsrfValidationError):
        browser_egress.fetch(dict(url='https://site.example/', method='GET', headers={}))


def test_owner_transport_refuses_bearer_only(monkeypatch):
    from starlette.requests import Request

    from tinyassets.onboarding import browser_login, owner_sessions

    monkeypatch.setattr('tinyassets.onboarding.onboarding_enabled', lambda: True)
    monkeypatch.setattr('tinyassets.auth.middleware.current_identity',
                        lambda: SimpleNamespace(user_id='alice'))
    monkeypatch.setattr(owner_sessions, 'lookup', lambda _: None)
    monkeypatch.setattr('tinyassets.onboarding._same_origin_json', lambda *args: True)
    monkeypatch.setattr('tinyassets.onboarding.app_config',
                        lambda: {'resource': 'https://tinyassets.io/mcp'})
    request = Request({'type': 'http', 'method': 'POST', 'path': '/app/browser-login',
                       'headers': [(b'origin', b'https://tinyassets.io'),
                                   (b'authorization', b'Bearer agent-token')]})
    result = asyncio.run(browser_login.handle(request))
    assert result.status_code == 403
    assert json.loads(result.body)['error'] == 'interactive_approval_required'


def test_agent_cannot_request_capture_or_export(tmp_path, monkeypatch):
    from contextlib import nullcontext

    from tinyassets import browser_sessions

    monkeypatch.setattr(browser_sessions, 'control', lambda _: nullcontext())
    monkeypatch.setattr(browser_sessions, '_vault', lambda *args, **kwargs: {'status': 'connected'})
    monkeypatch.setattr(browser_sessions, 'Cell', lambda *args: pytest.fail('cell created'))
    context = SimpleNamespace(owner='alice', universe='home-a')
    for action in ('frame', 'input', 'evaluate', 'cookies', 'storage', 'export'):
        with pytest.raises(ValueError):
            browser_sessions.agent(tmp_path, context, {'action': action, 'id': 'x'})


def test_account_erasure_removes_only_owners_ciphertext_and_key(vault):
    from tinyassets.broker.account_erasure import ACCOUNT_SCOPE, local_erase
    from tinyassets.broker.browser_vault import key_path

    ledger, call = vault
    own = call('create', url='https://example.com/')
    foreign = call('create', owner='bob', home='home-b', url='https://example.com/')
    own_key = key_path(ledger, 'alice')
    assert own_key.exists()
    erased = local_erase(ledger, principal='alice', command_center=ACCOUNT_SCOPE)
    assert erased['browser_vault'] == 1
    assert not own_key.exists()
    with pytest.raises(PermissionError):
        call('read', id=own['id'])
    assert call('read', owner='bob', home='home-b', id=foreign['id'])['id'] == foreign['id']


def test_capture_state_values_include_previous_cookie_but_not_metadata():
    from tinyassets.browser_cell import Browser

    browser = Browser()
    browser.remember({'cookies': [{'name': 'session', 'value': 'old-private', 'path': '/'}]})
    browser.remember({'cookies': [{'name': 'session', 'value': 'new-private', 'path': '/'}]})
    assert browser.held == {'old-private', 'new-private'}


def test_site_account_reuse_and_ambiguity(vault):
    _, call = vault
    first = call('create', url='https://example.com/login', account='Personal')
    again = call('create', url='https://example.com/', account='Personal')
    assert again['id'] == first['id']
    second = call('create', url='https://example.com/', account='Work')
    assert second['id'] != first['id']
    ambiguous = call('create', url='https://example.com/')
    assert ambiguous['needs_account']
    assert {item['id'] for item in ambiguous['accounts']} == {first['id'], second['id']}
    call('revoke', id=first['id'], revision=first['revision'])
    assert call('create', url='https://example.com/')['id'] == second['id']


@pytest.mark.parametrize('verify', [None, {}, {'url': 'https://foreign.example/',
                                             'selector': '#account'}])
def test_verification_is_required_and_same_origin(vault, verify):
    _, call = vault
    with pytest.raises(ValueError):
        call('create', url='https://example.com/', verify=verify)


def test_abandoned_capture_revalidates_retained_session(vault, tmp_path, monkeypatch):
    from contextlib import nullcontext

    from tinyassets import browser_sessions

    _, call = vault
    state = {'cookies': [{'name': 'session', 'value': 'private'}], 'origins': []}
    row = call('create', url='https://example.com/')
    row = call('save', id=row['id'], revision=1, state=state)
    row = call('capture', id=row['id'], revision=row['revision'])
    row = call('cancel', id=row['id'], revision=row['revision'])
    checked = []

    class Cell:
        def __init__(self, *args):
            pass

        def call(self, command, checkpoint):
            checked.append(command['action'])
            return {'state': state} if command['action'] == 'verify' else {'status': 'ready'}

        def close(self):
            pass

    monkeypatch.setattr(browser_sessions, 'Cell', Cell)
    monkeypatch.setattr(browser_sessions, 'control', lambda _: nullcontext())
    monkeypatch.setattr(browser_sessions, '_vault',
        lambda root, owner, home, action, **values: call(action, owner=owner, home=home, **values))
    monkeypatch.setattr('tinyassets.effectors.authenticated_external_call._rule_refusal',
                        lambda *args, **kwargs: None)
    result = browser_sessions.agent(tmp_path, SimpleNamespace(owner='alice', universe='home-a',
        initiating_agent='main'), {'action': 'connect', 'url': 'https://example.com/'})
    assert result['remembered'] and result['status'] == 'connected'
    assert checked == ['open', 'verify']
    assert call('read', id=row['id'])['state'] == state


def test_interrupted_key_write_never_publishes_partial_key(vault, monkeypatch):
    import os

    from tinyassets.broker.browser_vault import key_path

    ledger, call = vault
    real_fsync = os.fsync

    def crash(_):
        raise OSError('interrupted write')

    monkeypatch.setattr(os, 'fsync', crash)
    with pytest.raises(OSError):
        call('create', url='https://example.com/')
    assert not key_path(ledger, 'alice').exists()
    monkeypatch.setattr(os, 'fsync', real_fsync)
    row = call('create', url='https://example.com/')
    assert len(key_path(ledger, 'alice').read_bytes()) == 32
    assert call('read', id=row['id'])['status'] == 'needs_login'
