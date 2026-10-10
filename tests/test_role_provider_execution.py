"""Shared execution routing, three real stdio pipes and no daemon fallback."""
import asyncio
import json
import os
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets import role_decoder, role_provider_discovery, role_provider_execution
from tinyassets.broker import supervisor
from tinyassets.providers import owned_process, provider_jail


def test_shared_spawn_refuses_an_unbound_launch_before_any_process(monkeypatch):
    monkeypatch.setattr(role_decoder, '_bounded_client', object())
    def forbidden(*args, **kwargs):
        pytest.fail('a provider reached a daemon subprocess')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', forbidden)
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
        def close(self):
            self.closed = True
    async def scenario():
        stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
        stdout.feed_data(b'protocol output')
        stdout.feed_eof()
        stderr.feed_data(b'auth error')
        stderr.feed_eof()
        cell, writer = Cell(), Writer()
        process = role_provider_execution.ExecutionProcess(cell, stdout, writer, stderr)
        assert process.stdin is writer  # a real pipe writer, no half-close shim
        assert await process.communicate(b'prompt') == (b'protocol output', b'auth error')
        assert writer.value == b'prompt' and process.returncode == 17
        assert cell.closed and writer.closed and not cell.revoked
    asyncio.run(scenario())


def _mapper():
    scope = runpy.run_path(str(Path(__file__).resolve().parents[1]
                              / 'deploy/role_owner_launcher.py'))
    mapper = object.__new__(scope['OwnerLauncher'])
    mapper.overflow_uid = os.getuid() if hasattr(os, 'getuid') else 0
    return mapper


def test_exec_mapper_requires_stdin_stdout_and_stderr_pipes():
    mapper = _mapper()
    request = dict(op='START', kind='provider-exec', principal='alice',
                   command_center='alice', egress=True, engine=False)
    # data, snapshot, egress, status: the old duplex layout, and one short.
    for received in ([0, 1, 2, 3], [0, 1, 2, 3, 4]):
        with pytest.raises(ValueError, match='unsupported'):
            mapper._decoder(request, received)


@pytest.mark.skipif(sys.platform == 'win32', reason='asyncio pipe transports and /proc are POSIX')
@pytest.mark.parametrize('case', ['socket', 'named-fifo', 'wrong-direction', 'file'])
def test_exec_mapper_admits_only_daemon_made_anonymous_pipes(tmp_path, case):
    import socket

    mapper = _mapper()
    read_fd, write_fd = os.pipe()
    opened = [read_fd, write_fd]
    try:
        mapper._daemon_pipe(read_fd, os.O_RDONLY)
        mapper._daemon_pipe(write_fd, os.O_WRONLY)
        if case == 'socket':
            sock, peer = socket.socketpair()
            opened += [sock.fileno(), peer.fileno()]
            bad, direction = sock.fileno(), os.O_RDONLY
        elif case == 'named-fifo':
            os.mkfifo(tmp_path / 'fifo')
            bad = os.open(tmp_path / 'fifo', os.O_RDONLY | os.O_NONBLOCK)
            opened.append(bad)
            direction = os.O_RDONLY
        elif case == 'wrong-direction':
            bad, direction = write_fd, os.O_RDONLY
        else:
            bad = os.open(tmp_path / 'plain', os.O_RDWR | os.O_CREAT)
            opened.append(bad)
            direction = os.O_WRONLY
        with pytest.raises(ValueError, match='daemon pipes'):
            mapper._daemon_pipe(bad, direction)
    finally:
        for fd in opened:
            os.close(fd)


@pytest.mark.skipif(sys.platform == 'win32', reason='asyncio pipe transports and /proc are POSIX')
def test_cli_exiting_with_unread_stdin_leaves_stdout_clean_and_stderr_whole():
    """The production failure, on real pipes: no ECONNRESET, the reason survives."""
    async def run():
        stdin_read, stdin_write = os.pipe()
        stdout_read, stdout_write = os.pipe()
        stderr_read, stderr_write = os.pipe()
        cell = SimpleNamespace(
            stream=os.fdopen(stdout_read, 'rb', buffering=0),
            stdin=os.fdopen(stdin_write, 'wb', buffering=0),
            stderr=os.fdopen(stderr_read, 'rb', buffering=0),
            wait=lambda timeout=None: 1, close=lambda: None, revoke=lambda: None)
        reader, writer, error_reader = await role_provider_execution.pipe_streams(cell, 65536)
        proc = role_provider_execution.ExecutionProcess(cell, reader, writer, error_reader)
        # The "CLI": emits one line, a stderr verdict, and exits WITHOUT reading
        # stdin, which still holds the whole prompt.
        os.write(stdout_write, b'{"type":"system","subtype":"init"}\n')
        os.write(stderr_write, b'error: unknown option\n')
        for fd in (stdin_read, stdout_write, stderr_write):
            os.close(fd)
        proc.stdin.write(b'prompt the CLI never read' * 100)
        with pytest.raises((BrokenPipeError, ConnectionResetError)):
            await proc.stdin.drain()
        proc.stdin.close()
        assert await proc.stdout.readline() == b'{"type":"system","subtype":"init"}\n'
        assert await proc.stdout.readline() == b''  # clean EOF, never a reset
        assert await proc.stderr.read() == b'error: unknown option\n'
        assert await proc.wait() == 1
        for endpoint in (cell.stream, cell.stderr):
            endpoint.close()
    asyncio.run(run())


@pytest.mark.skipif(sys.platform == 'win32', reason='asyncio pipe transports and /proc are POSIX')
def test_stdin_close_is_only_the_cli_input_side():
    """Closing stdin reaches the CLI as EOF while its output keeps flowing."""
    async def run():
        stdin_read, stdin_write = os.pipe()
        stdout_read, stdout_write = os.pipe()
        stderr_read, stderr_write = os.pipe()
        cell = SimpleNamespace(
            stream=os.fdopen(stdout_read, 'rb', buffering=0),
            stdin=os.fdopen(stdin_write, 'wb', buffering=0),
            stderr=os.fdopen(stderr_read, 'rb', buffering=0),
            wait=lambda timeout=None: 0, close=lambda: None, revoke=lambda: None)
        reader, writer, error_reader = await role_provider_execution.pipe_streams(cell, 65536)
        proc = role_provider_execution.ExecutionProcess(cell, reader, writer, error_reader)
        proc.stdin.write(b'prompt')
        await proc.stdin.drain()
        proc.stdin.close()
        await proc.stdin.wait_closed()
        # The writer held the daemon's ONLY write end: its close is the CLI's EOF.
        assert cell.stdin is None
        assert os.read(stdin_read, 100) == b'prompt' and os.read(stdin_read, 100) == b''
        # The CLI answers only AFTER it has seen stdin EOF.
        os.write(stdout_write, b'delayed streamed answer\n')
        for fd in (stdin_read, stdout_write, stderr_write):
            os.close(fd)
        assert await proc.stdout.read() == b'delayed streamed answer\n'
        assert await proc.wait() == 0
        for endpoint in (cell.stream, cell.stderr):
            endpoint.close()
    asyncio.run(run())


def test_stderr_excerpt_is_the_bounded_verdict_tail():
    long = 'started\n' + 'x' * 1000 + '\nerror: unknown option --bogus'
    excerpt = owned_process.stderr_excerpt(long)
    assert excerpt.startswith('...') and excerpt.endswith('error: unknown option --bogus')
    assert len(excerpt) <= 403
    assert owned_process.stderr_excerpt('  short verdict\n') == 'short verdict'


def test_cell_refusal_reaches_the_reader_as_the_decoder_marker():
    """On pipes a refused launch is a clean EOF; the mapper's stop reason names it."""
    from tinyassets.exceptions import ProviderError

    class Cell:
        stop_reason = 'decoder:role_provider_cell.py:153:ValueError:errno=None'
        revoke_caller = None
        def wait(self, timeout):
            return 1
        def close(self):
            pass
    async def scenario():
        stdout = asyncio.StreamReader()
        stdout.feed_eof()
        process = role_provider_execution.ExecutionProcess(
            Cell(), stdout, SimpleNamespace(transport=None, close=lambda: None),
            asyncio.StreamReader())
        with pytest.raises(ProviderError, match='provider cell ended') as failure:
            await process.stdout.readline()
        assert Cell.stop_reason in str(failure.value)
    asyncio.run(scenario())


@pytest.mark.parametrize('reply', [True, False, RuntimeError('invalid reply')])
def test_cell_heartbeat_uses_only_the_owned_authenticated_channel(reply):
    calls = []
    def heartbeat():
        calls.append('pulse')
        if isinstance(reply, Exception):
            raise reply
        return reply
    proc = owned_process.OwnerCellProcess(SimpleNamespace(heartbeat=heartbeat), None,
                                         SimpleNamespace(transport=None))
    assert asyncio.run(owned_process.cell_heartbeat(proc)) is (reply is True)
    assert calls == ['pulse']
    assert asyncio.run(owned_process.cell_heartbeat(SimpleNamespace(returncode=None))) is False
