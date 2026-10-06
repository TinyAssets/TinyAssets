"""D88: the shipped adapters reach provider-exec through one provider-neutral cell view."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets import (
    role_decoder,
    role_provider_cell,
    role_provider_discovery,
    role_provider_execution,
)
from tinyassets.broker import supervisor
from tinyassets.providers import provider_jail
from tinyassets.role_provider_execution import CellView


def _scope(tmp_path):
    center = tmp_path / 'alice'
    return center, SimpleNamespace(
        universe_dir=center, engine_route=None,
        credential_dir=center / '.runtime/provider-launch-credentials/one')


def _pipes(**extra):
    return dict(stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, **extra)


def _capture(monkeypatch):
    seen = []
    monkeypatch.setattr(supervisor, '_protect_daemon', lambda: None)

    async def launch(argv, **kwargs):
        seen.append((argv, kwargs))
        return 'owner-process'
    monkeypatch.setattr(role_provider_discovery, 'aspawn_cell', launch)
    return seen


def test_cell_view_replaces_a_caller_view_and_presents_the_center_as_workspace(
        tmp_path, monkeypatch):
    seen = _capture(monkeypatch)
    center, scope = _scope(tmp_path)
    view = CellView(persistent=True, home='CODEX_HOME')
    result = asyncio.run(role_provider_execution.spawn(
        ['/installed/cli', '-C', str(center), 'exec'], scope=scope, shell=False,
        view=object(), nested_sandbox=False, cell_view=view,
        options=_pipes(cwd=center, env={'A': 'b'})))
    assert result == 'owner-process'
    argv, kwargs = seen[0]
    assert argv == ['/installed/cli', '-C', '/workspace', 'exec']
    assert kwargs['execution'] and kwargs['cell_view'] == {
        'persistent': True, 'home': 'CODEX_HOME', 'secret_fds': []}


@pytest.mark.parametrize('change', ['scratch-cwd', 'foreign-cwd', 'sub-cwd', 'nested',
                                    'scratch-center-argv', 'other-center-argv', 'not-a-view'])
def test_cell_view_admits_nothing_beyond_the_owner_workspace(tmp_path, monkeypatch, change):
    seen = _capture(monkeypatch)
    center, scope = _scope(tmp_path)
    view = CellView(persistent=not change.startswith('scratch'))
    options = _pipes()
    argv = ['/installed/cli']
    if change.endswith('cwd'):
        options['cwd'] = {'scratch-cwd': center, 'foreign-cwd': tmp_path / 'bob',
                          'sub-cwd': center / 'sub'}[change]
    if change == 'scratch-center-argv':
        argv.append(str(center))
    if change == 'other-center-argv':
        argv.append(str(tmp_path / 'bob'))
    with pytest.raises(provider_jail.ProviderConfinementError):
        asyncio.run(role_provider_execution.spawn(
            argv, scope=scope, shell=False, view=None, nested_sandbox=change == 'nested',
            options=options,
            cell_view=SimpleNamespace(persistent=True) if change == 'not-a-view' else view))
    assert seen == []


@pytest.mark.parametrize('fields', [
    dict(home='lower'), dict(home='A=B'), dict(home=''),
    dict(secret_fds=(('FD', '../auth.json'),)), dict(secret_fds=(('FD', '/auth.json'),)),
    dict(secret_fds=(('FD', 'a'), ('FD', 'b'))), dict(home='FD', secret_fds=(('FD', 'a'),)),
    dict(secret_fds=tuple((f'F{index}', 'a') for index in range(5))),
    dict(persistent=1),
])
def test_cell_view_document_refuses_every_unbounded_shape(fields):
    with pytest.raises(provider_jail.ProviderConfinementError):
        CellView(**fields).document()


def test_cell_config_carries_the_view_and_never_an_ambient_token(tmp_path):
    center = tmp_path / 'alice'
    snapshot = center / '.runtime/provider-launch-credentials/one'
    view = CellView(
        secret_fds=(('CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR', 'auth.json'),)).document()
    empty = str()
    result = json.loads(role_provider_discovery.cell_config(
        ['/installed/cli', '--tools', empty, '--setting-sources', empty],
        {'CLAUDE_CODE_OAUTH_TOKEN': 'sk-ant-oat01-owner',
         'CLAUDE_CONFIG_DIR': str(center / 'x')},
        (), snapshot, tmp_path, view))
    assert result['argv'] == ['/installed/cli', '--tools', empty, '--setting-sources', empty]
    assert result['env'] == {} and result['view'] == view
    assert b'sk-ant' not in json.dumps(result).encode()


def _cell_document(argv, view=None):
    config = {'argv': argv, 'env': {}}
    if view is not None:
        config['view'] = view
    return json.dumps(config)


def test_in_cell_validation_rechecks_the_view_and_admits_empty_arguments(monkeypatch):
    monkeypatch.setattr(role_provider_cell, 'shipped_executable', lambda path: True)
    view = CellView(persistent=True, home='CODEX_HOME').document()
    empty = str()
    argv, env, checked = role_provider_cell.validate(
        _cell_document(['/opt/a-install/cli', '--tools', empty], view), '/data', execution=True)
    assert argv[-1] == empty and checked == view and env['HOME'] == '/tmp'
    argv, env, checked = role_provider_cell.validate(
        _cell_document(['/opt/a-install/cli']), '/data', execution=True)
    assert checked == {'persistent': False, 'home': None, 'secret_fds': []}
    for raw, execution in ((_cell_document([empty], view), True),
                           (_cell_document(['/opt/a-install/cli'], view), False),
                           (_cell_document(['/opt/a-install/cli'], {**view, 'extra': 1}), True),
                           (_cell_document(['/opt/a-install/cli'],
                                           {**view, 'session': ['sessions', 'codex']}), True),
                           (_cell_document(['/opt/a-install/cli'],
                                           {**view, 'home': 'lower'}), True),
                           (_cell_document(['/opt/a-install/cli'],
                                           {**view, 'persistent': 1}), True)):
        with pytest.raises(ValueError):
            role_provider_cell.validate(raw, '/data', execution=execution)


@pytest.mark.skipif(os.name != 'posix', reason='in-cell pipes are POSIX; '
                    'runs-in=the Linux oracle')
def test_home_copy_keeps_the_sessions_mount_and_pipes_secrets_without_copies(tmp_path):
    snapshot, private = tmp_path / 'snapshot', tmp_path / 'private'
    snapshot.mkdir()
    (private / 'sessions').mkdir(parents=True)  # the entry's bind mount point
    (snapshot / 'auth.json').write_text('sk-ant-oat01-owner\n')
    (snapshot / 'config.toml').write_text('x=1')
    role_provider_cell.copy_snapshot(str(snapshot), str(private), exclude={'auth.json'})
    assert sorted(path.name for path in private.iterdir()) == ['config.toml', 'sessions']
    env = {}
    role_provider_cell.prepare_view(
        {'persistent': True, 'home': 'CODEX_HOME', 'secret_fds': [['TOKEN_FD', 'auth.json']]},
        str(private), env, snapshot=str(snapshot))
    assert env['CODEX_HOME'] == str(private)
    descriptor = int(env['TOKEN_FD'])
    try:
        assert os.read(descriptor, 4096) == b'sk-ant-oat01-owner\n'
        assert os.get_inheritable(descriptor)
    finally:
        os.close(descriptor)
    assert all(b'sk-ant' not in path.read_bytes() for path in private.rglob('*')
               if path.is_file())
    # The sealed snapshot never decides what lands in the session store, and
    # only the home itself may pre-exist.
    (snapshot / 'sessions').mkdir()
    mounted = tmp_path / 'mounted'
    (mounted / 'sessions').mkdir(parents=True)
    with pytest.raises(FileExistsError):
        role_provider_cell.copy_snapshot(str(snapshot), str(mounted))
    (tmp_path / 'not-a-home').write_text('x')
    with pytest.raises(FileExistsError):
        role_provider_cell.copy_snapshot(str(snapshot), str(tmp_path / 'not-a-home'))


def test_claude_states_its_cell_view_and_moves_a_raw_token_to_a_pipe(monkeypatch, tmp_path):
    from tinyassets.providers import claude_provider
    from tinyassets.providers.base import ModelConfig

    universe = tmp_path / 'alice'
    snapshot = universe / '.runtime/provider-launch-credentials/one'
    snapshot.mkdir(parents=True)
    (snapshot / 'auth.json').write_text('sk-ant-oat01-owner')
    captured = {}

    async def fake_spawn(cmd, **kwargs):
        captured.update(kwargs)
        raise RuntimeError('stop before inference')
    monkeypatch.setattr(claude_provider, 'aspawn_owned', fake_spawn)
    monkeypatch.setattr(claude_provider, '_resolve_claude_cmd', lambda: (['claude'], False))
    config = ModelConfig(credential_snapshot_dir=snapshot)
    with pytest.raises(RuntimeError, match='stop before inference'):
        asyncio.run(claude_provider.ClaudeProvider().complete(
            'hi', '', config, universe_dir=universe))
    assert captured['cell_view'] == CellView(
        persistent=False,
        secret_fds=(('CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR', 'auth.json'),))
    assert captured['cwd'] is None
    assert claude_provider._cell_view({}, str(universe)) == CellView(persistent=True)


def test_codex_states_a_persistent_cell_view_for_its_universe(monkeypatch, tmp_path):
    from tinyassets.providers import codex_provider
    from tinyassets.providers.base import ModelConfig

    universe = tmp_path / 'alice'
    snapshot = universe / '.runtime/provider-launch-credentials/one'
    snapshot.mkdir(parents=True)
    (snapshot / 'auth.json').write_text('{}')
    captured = {}

    async def fake_spawn(cmd, **kwargs):
        captured.update(kwargs, cmd=list(cmd))
        raise RuntimeError('stop before inference')
    monkeypatch.setattr(codex_provider, 'aspawn_owned', fake_spawn)
    monkeypatch.setattr(codex_provider.CodexProvider, 'native_command_resolver',
                        lambda self: (['codex'], False))
    with provider_jail.provider_launch_scope(universe, credential_dir=snapshot):
        with pytest.raises(RuntimeError, match='stop before inference'):
            asyncio.run(codex_provider.CodexProvider().complete(
                'hi', '', ModelConfig(credential_snapshot_dir=snapshot), universe_dir=universe))
    assert captured['cell_view'] == CellView(persistent=True)
    assert captured['cmd'][captured['cmd'].index('-C') + 1] == str(universe)
    assert Path(captured['env']['CODEX_HOME']) == snapshot.resolve()


def test_selected_session_store_is_the_owner_workspace_and_is_never_daemon_made(
        monkeypatch, tmp_path):
    from tinyassets import agent_sessions

    monkeypatch.setattr(role_decoder, '_bounded_client', object())
    store = agent_sessions.native_store(tmp_path / 'alice', 'codex')
    assert store == tmp_path / 'alice/.provider-workspace/sessions'
    assert not (tmp_path / 'alice').exists()
    assert not agent_sessions.native_file_exists(store, 'thread.jsonl')


def test_client_sends_a_workspace_descriptor_only_with_its_flag():
    from tinyassets.broker.owner_identities import OwnerIdentity
    from tinyassets.owner_launcher_client import OwnerLauncherClient

    client = object.__new__(OwnerLauncherClient)
    identity = OwnerIdentity(300001, 300001)
    for extra, workspace_fd in (({'egress': True, 'workspace': True}, None),
                                ({'egress': True, 'workspace': False}, 7),
                                ({'egress': True}, 7)):
        with pytest.raises(ValueError, match='workspace'):
            client.start_cell(kind='provider-exec', principal='alice', command_center='alice',
                              identity=identity, extra=extra, directory_fd=5,
                              workspace_fd=workspace_fd)


def test_teardown_after_a_reaped_cell_is_a_no_op():
    from tinyassets.providers.owned_process import OwnerCellProcess, kill_owned_tree

    class Cell:
        revoked = 0

        def revoke(self):
            self.revoked += 1

    class Writer:
        transport = None

    cell = Cell()
    process = OwnerCellProcess(cell, None, Writer())
    kill_owned_tree(process)
    assert cell.revoked == 1
    process.returncode = 0
    kill_owned_tree(process)
    assert cell.revoked == 1


def test_closing_execution_stdin_half_closes_and_keeps_stdout():
    class Writer:
        transport = None
        closed = eof = False

        def is_closing(self):
            return self.closed

        def write_eof(self):
            self.eof = True

        def close(self):
            self.closed = True

    writer = Writer()
    process = role_provider_execution.ExecutionProcess(
        SimpleNamespace(), SimpleNamespace(), writer, SimpleNamespace(), Writer())
    process.stdin.close()  # what both adapters do after writing the prompt
    assert writer.eof and not writer.closed
    process.stdin.close()
    assert not writer.closed
