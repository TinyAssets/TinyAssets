"""Shared execution routing, distinct stdio and no daemon fallback in selected mode."""
import asyncio
import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets import role_decoder, role_provider_discovery, role_provider_execution
from tinyassets.broker import supervisor
from tinyassets.providers import owned_process, provider_jail


def test_shared_spawn_refuses_unbound_selected_execution_before_legacy_jail(monkeypatch):
    monkeypatch.setattr(role_decoder, '_bounded_client', object())
    def forbidden(*args, **kwargs):
        pytest.fail('selected provider reached daemon subprocess path')
    monkeypatch.setattr(provider_jail, 'confine_launch', forbidden)
    with pytest.raises(provider_jail.ProviderConfinementError, match='view'):
        asyncio.run(owned_process.aspawn_owned(['/bin/sh']))


def test_all_adapters_share_the_same_selected_dispatch(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(role_decoder, '_bounded_client', object())
    monkeypatch.setattr(supervisor, '_protect_daemon', lambda: None)
    async def launch(argv, **kwargs):
        seen.append((argv, kwargs))
        return 'owner-process'
    monkeypatch.setattr(role_provider_discovery, 'aspawn_cell', launch)
    snapshot = tmp_path / '.runtime/provider-launch-credentials/one'
    with provider_jail.provider_launch_scope(tmp_path, credential_dir=snapshot):
        for binary in ('/opt/first/cli', '/opt/second/cli'):
            assert asyncio.run(owned_process.aspawn_owned([binary, '--version'],
                env={}, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)) == 'owner-process'
    assert len(seen) == 2
    assert all(item[1]['execution'] and item[1]['snapshot_dir'] == snapshot for item in seen)


@pytest.mark.parametrize('change', ['view', 'shell', 'nested', 'cwd',
                                    'center-cwd', 'snapshot-cwd', 'host-argv', 'extra'])
def test_unsupported_execution_view_never_reaches_cell(tmp_path, monkeypatch, change):
    monkeypatch.setattr(role_decoder, '_bounded_client', object())
    scope = SimpleNamespace(universe_dir=tmp_path, credential_dir=tmp_path / 'snapshot',
                            engine_route=None)
    kwargs = dict(scope=scope, shell=change == 'shell', view=object() if change == 'view' else None,
                  nested_sandbox=change == 'nested', options=dict(
                      stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                      stderr=asyncio.subprocess.PIPE))
    if change == 'cwd':
        kwargs['options']['cwd'] = '/foreign'
    if change == 'center-cwd':
        kwargs['options']['cwd'] = tmp_path
    if change == 'snapshot-cwd':
        kwargs['options']['cwd'] = tmp_path / 'snapshot'
    if change == 'extra':
        kwargs['options']['pass_fds'] = (3,)
    with pytest.raises(provider_jail.ProviderConfinementError):
        argv = ['/installed/cli', str(tmp_path)] if change == 'host-argv' else ['/installed/cli']
        asyncio.run(role_provider_execution.spawn(argv, **kwargs))


def test_served_turn_carries_its_engine_route_into_the_cell(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(supervisor, '_protect_daemon', lambda: None)

    async def launch(argv, **kwargs):
        seen.append(kwargs)
        return 'owner-process'
    monkeypatch.setattr(role_provider_discovery, 'aspawn_cell', launch)
    scope = SimpleNamespace(universe_dir=tmp_path, credential_dir=tmp_path / 'snapshot',
                            engine_route=('alice', tmp_path.name))
    assert asyncio.run(role_provider_execution.spawn(
        ['/installed/cli'], scope=scope, shell=False, view=None, nested_sandbox=False,
        options=dict(env={}, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                     stderr=asyncio.subprocess.PIPE))) == 'owner-process'
    assert seen[0]['engine_route'] == ('alice', tmp_path.name) and seen[0]['execution']


def test_execution_only_relocates_the_snapshot_not_a_persistent_workspace(tmp_path):
    center = tmp_path / 'alice'
    snapshot = center / '.runtime/provider-launch-credentials/one'
    snapshot.mkdir(parents=True)
    (snapshot / 'auth.json').write_text('owner-token' + chr(10))
    result = json.loads(role_provider_discovery.cell_config(
        ['/installed/cli', '-C', '/tmp/workspace', str(snapshot / 'config')],
        {'AUTH_HOME': str(snapshot), 'HOST_SECRET': 'never', 'LD_PRELOAD': '/tmp/evil',
         'OWNER_TOKEN': 'owner-token', 'FEATURE_OFF': 'false', 'NAME': 'free text'},
        (), snapshot, tmp_path, engine_port=8790))
    assert result['argv'] == ['/installed/cli', '-C', '/tmp/workspace', '/snapshot/config']
    # The owner's own sealed value and a switch cross; ambient values never do.
    assert result['env'] == {'AUTH_HOME': '/snapshot', 'OWNER_TOKEN': 'owner-token',
                             'FEATURE_OFF': 'false'}
    assert result['engine_port'] == 8790


def test_execution_communicate_preserves_stdout_stderr_and_authenticated_reap():
    class Cell:
        closed = False
        revoked = False
        def wait(self, timeout):
            assert timeout is None
            return 17
        def close(self):
            self.closed = True
        def revoke(self):
            self.revoked = True
    class Writer:
        transport = None
        closed = False
        value = b''
        def write(self, data):
            self.value += data
        async def drain(self):
            pass
        def write_eof(self):
            pass
        def close(self):
            self.closed = True
    async def scenario():
        stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
        stdout.feed_data(b'protocol output')
        stdout.feed_eof()
        stderr.feed_data(b'auth error')
        stderr.feed_eof()
        cell, writer, error_writer = Cell(), Writer(), Writer()
        process = role_provider_execution.ExecutionProcess(
            cell, stdout, writer, stderr, error_writer)
        assert await process.communicate(b'prompt') == (b'protocol output', b'auth error')
        assert writer.value == b'prompt' and process.returncode == 17
        assert cell.closed and writer.closed and error_writer.closed and not cell.revoked
    asyncio.run(scenario())


def test_exec_mapper_requires_the_independent_stderr_descriptor():
    scope = runpy.run_path(str(Path(__file__).resolve().parents[1]
                              / 'deploy/role_owner_launcher.py'))
    mapper = object.__new__(scope['OwnerLauncher'])
    request = dict(op='START', kind='provider-exec', principal='alice',
                   command_center='alice', egress=True, engine=False)
    with pytest.raises(ValueError, match='unsupported'):
        mapper._decoder(request, [0, 1, 2, 3])
