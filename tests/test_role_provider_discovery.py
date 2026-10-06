"""D82 provider-discovery cell: config bounds, proof, refusal and transport fixture.

The fixture responder below is supplementary transport proof only; it is not
evidence that an installed CLI exposes its metadata API.
"""
import asyncio
import json
import os
import runpy
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets import role_decoder, role_provider_cell, role_provider_discovery
from tinyassets.exceptions import ProviderError
from tinyassets.providers import native_jsonrpc_discovery as discovery
from tinyassets.providers import owned_process
from tinyassets.providers.native_jsonrpc_discovery import NativeJsonRpcProtocol

PROTOCOL = NativeJsonRpcProtocol(
    list_method='model/list', items_key='data', model_key='id', default_key='isDefault',
    modalities_key='inputModalities', hidden_key='hidden', cursor_key='nextCursor',
    cursor_param='cursor', initialize_method='initialize',
    initialized_notification='initialized')


def _snapshot(tmp_path, center='alice', name='codex-abc'):
    root = tmp_path / 'data'
    snapshot = root / center / '.runtime' / 'provider-launch-credentials' / name
    snapshot.mkdir(parents=True)
    return root, snapshot


def test_config_rewrites_snapshot_and_drops_host_data_paths(tmp_path):
    root, snapshot = _snapshot(tmp_path)
    raw = role_provider_discovery.cell_config(
        ['/opt/codex-install/node_modules/.bin/codex', 'app-server'],
        {'CODEX_HOME': str(snapshot), 'TINYASSETS_DATA_DIR': str(root), 'TERM': 'dumb'},
        (('HOME', '/tmp'), ('AUTH', str(snapshot / 'auth.json'))), snapshot, root)
    assert raw.endswith(b'\n') and raw.count(b'\n') == 1
    config = json.loads(raw)
    assert config['env'] == {'CODEX_HOME': '/snapshot', 'TERM': 'dumb', 'HOME': '/tmp',
                             'AUTH': '/snapshot/auth.json'}
    assert str(root) not in raw.decode()
    guarded = json.loads(role_provider_discovery.cell_config(
        ['immutable-cli'], {'DAEMON_TOKEN': 'foreign-token', 'LD_PRELOAD': '/snapshot/evil',
                           'CODEX_HOME': str(snapshot)}, (), snapshot, root))
    assert guarded['env'] == {'CODEX_HOME': '/snapshot'}
    with pytest.raises(ValueError, match='bound'):
        role_provider_discovery.cell_config(['x'] * 30000, {}, (), snapshot, root)


def test_cell_reads_exactly_one_config_line_and_leaves_cli_bytes(monkeypatch):
    read, write = os.pipe()
    try:
        os.write(write, b'{"argv":["a"],"env":{}}\n{"method":"initialize"}\n')
        assert role_provider_cell.read_config(read) == b'{"argv":["a"],"env":{}}'
        assert os.read(read, 100) == b'{"method":"initialize"}\n'
        # A pipe buffer cannot hold the real 64 KiB bound without a writer thread.
        monkeypatch.setattr(role_provider_cell, 'MAX_CONFIG_BYTES', 16)
        os.write(write, b'x' * 18)
        with pytest.raises(ValueError, match='bound'):
            role_provider_cell.read_config(read)
    finally:
        os.close(read)
        os.close(write)


@pytest.mark.parametrize('config', [
    {'argv': ['/usr/bin/sh', '-c', 'id'], 'env': {}},
    {'argv': ['/opt/codex-install/bin/codex', '/data/bob'], 'env': {}},
    {'argv': ['/opt/codex-install/bin/codex'], 'env': {'X': '/data/bob/.credential-vault.json'}},
    {'argv': ['/opt/codex-install/bin/codex'], 'env': {'lower': 'x'}},
    {'argv': ['/opt/codex-install/bin/codex'], 'env': {}, 'cwd': '/'},
    {'argv': [], 'env': {}},
    {'argv': ['/opt/codex-install/bin/codex\0'], 'env': {}},
])
def test_cell_refuses_foreign_executable_paths_and_shape(monkeypatch, config):
    monkeypatch.setattr(role_provider_cell.os.path, 'realpath', lambda value: value)
    monkeypatch.setattr(role_provider_cell.os.path, 'isfile', lambda value: True)
    with pytest.raises(ValueError):
        role_provider_cell.validate(json.dumps(config), '/data')


def test_cell_fixes_disposable_environment(monkeypatch):
    monkeypatch.setattr(role_provider_cell.os.path, 'realpath', lambda value: value)
    monkeypatch.setattr(role_provider_cell.os.path, 'isfile', lambda value: True)
    argv, env = role_provider_cell.validate(json.dumps(
        {'argv': ['/opt/claude-code-install/node_modules/.bin/claude'],
         'env': {'HOME': '/elsewhere', 'PATH': '/evil', 'CODEX_HOME': '/snapshot'}}), '/data')
    assert env['HOME'] == '/tmp' and env['PATH'] == '/usr/bin:/bin'
    assert env['CODEX_HOME'] == '/snapshot'


def test_private_snapshot_copy_preserves_sealed_source(tmp_path):
    source, private = tmp_path / 'sealed', tmp_path / 'private'
    source.mkdir()
    (source / 'auth.json').write_bytes(b'owner-auth-verbatim')
    (source / 'auth.json').chmod(0o400)
    role_provider_cell.copy_snapshot(str(source), str(private))
    assert (private / 'auth.json').read_bytes() == b'owner-auth-verbatim'
    (private / 'auth.json').write_bytes(b'local-state')
    assert (source / 'auth.json').read_bytes() == b'owner-auth-verbatim'


@pytest.mark.parametrize('alias', ['symlink', 'hardlink', 'fifo'])
def test_snapshot_copy_refuses_aliases_and_special_files(tmp_path, alias):
    source = tmp_path / 'sealed'
    source.mkdir()
    target = tmp_path / 'foreign'
    target.write_bytes(b'foreign-sentinel')
    if alias == 'symlink':
        os.symlink(target, source / 'auth')
    elif alias == 'hardlink':
        os.link(target, source / 'auth')
    else:
        os.mkfifo(source / 'auth')
    with pytest.raises(ValueError):
        role_provider_cell.copy_snapshot(str(source), str(tmp_path / 'private'))
    assert target.read_bytes() == b'foreign-sentinel'


def _proof(**override):
    proof = dict(uid=1, gid=1, fds=[0, 1, 2], groups=[], caps='zero', nnp=1,
                 profile='cell-deny', nested_userns=False, source=[7, 9], sockets={})
    proof.update(override)
    return proof


@pytest.mark.parametrize('override', [
    {'fds': [0, 1, 2, 3]}, {'nested_userns': True}, {'profile': 'cell-nested'},
    {'source': [7, 10]}, {'sockets': {'e': [1, 2]}}, {'uid': 2}, {'groups': [1001]},
])
def test_proof_refuses_inherited_fd_nested_userns_foreign_snapshot_and_socket(override):
    identity = SimpleNamespace(uid=300001)
    role_provider_discovery.check_proof(_proof(), identity, [7, 9])
    with pytest.raises(RuntimeError, match='proof'):
        role_provider_discovery.check_proof(_proof(**override), identity, [7, 9])


def test_selected_metadata_without_client_refuses_without_daemon_fallback(monkeypatch, tmp_path):
    root, snapshot = _snapshot(tmp_path)
    monkeypatch.setenv('TINYASSETS_CREDENTIAL_BROKER', 'process')
    monkeypatch.setattr(role_decoder, '_bounded_client', None)

    async def forbidden(*args, **kwargs):
        pytest.fail('selected metadata spawned a daemon subprocess')
    monkeypatch.setattr(discovery, 'aspawn_owned', forbidden)
    with pytest.raises(ProviderError, match='unavailable'):
        asyncio.run(discovery.read_native_catalogue(
            ['/opt/codex-install/node_modules/.bin/codex', 'app-server'], protocol=PROTOCOL,
            env={}, cwd=str(snapshot), universe_dir=root / 'alice'))


class _FakeCell:
    def __init__(self, stream):
        self.stream, self.revoked, self.closed = stream, 0, False

    def revoke(self):
        self.revoked += 1

    def wait(self, timeout=5):
        return 0

    def close(self):
        self.closed = True


def _responder(peer, pages):
    lines = peer.makefile('rb')
    for raw in lines:
        message = json.loads(raw)
        if message['method'] == 'initialize':
            result = {}
        elif message['method'] == 'model/list':
            result = pages.pop(0)
        else:
            continue
        peer.sendall(json.dumps({'id': message['id'], 'result': result}).encode() + b'\n')
    peer.close()


def test_cell_stream_shim_preserves_metadata_protocol_and_revokes(monkeypatch, tmp_path):
    import threading

    root, snapshot = _snapshot(tmp_path)
    monkeypatch.setenv('TINYASSETS_CREDENTIAL_BROKER', 'process')
    ours, peer = socket.socketpair()
    cell = _FakeCell(ours)
    pages = [{'data': [{'id': 'm1', 'isDefault': True, 'inputModalities': ['text']}],
              'nextCursor': 'c1'},
             {'data': [{'id': 'm2', 'inputModalities': ['text', 'image']}]}]
    thread = threading.Thread(target=_responder, args=(peer, pages), daemon=True)
    thread.start()

    async def aspawn_cell(argv, *, env, view, universe_dir, snapshot_dir, limit):
        assert Path(snapshot_dir) == snapshot and limit == discovery._MAX_BYTES
        ours.setblocking(False)
        reader, writer = await asyncio.open_connection(sock=ours, limit=limit)
        return owned_process.OwnerCellProcess(cell, reader, writer)

    monkeypatch.setattr(role_provider_discovery, 'aspawn_cell', aspawn_cell)

    async def forbidden(*args, **kwargs):
        pytest.fail('selected metadata spawned a daemon subprocess')
    monkeypatch.setattr(discovery, 'aspawn_owned', forbidden)
    catalogue = asyncio.run(discovery.read_native_catalogue(
        ['/opt/codex-install/node_modules/.bin/codex', 'app-server'], protocol=PROTOCOL,
        env={}, cwd=str(snapshot), universe_dir=root / 'alice'))
    assert [model.model_id for model in catalogue.models] == ['m1', 'm2']
    assert catalogue.default_model_id == 'm1'
    assert cell.revoked == 1 and cell.closed
    thread.join(5)


def test_kill_owned_tree_revokes_shim_and_never_signals(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('shim teardown attempted a PID signal')
    monkeypatch.setattr(owned_process, '_kill_direct', forbidden)
    monkeypatch.setattr(owned_process, '_take_family', forbidden)
    writer = SimpleNamespace(transport=None)
    cell = _FakeCell(None)
    proc = owned_process.OwnerCellProcess(cell, None, writer)
    owned_process.kill_owned_tree(proc)
    asyncio.run(owned_process.akill_owned_tree(proc))
    assert cell.revoked == 2 and proc.pid is None


def test_missing_receipt_fails_the_catalogue(monkeypatch):
    class NoReceipt(_FakeCell):
        def wait(self, timeout=5):
            raise RuntimeError('invalid owner cell completion')

    async def scenario():
        ours, peer = socket.socketpair()
        ours.setblocking(False)
        reader, writer = await asyncio.open_connection(sock=ours)
        cell = NoReceipt(ours)
        with pytest.raises(ProviderError, match='receipt'):
            await discovery._close_metadata_process(
                owned_process.OwnerCellProcess(cell, reader, writer))
        peer.close()
        return cell
    cell = asyncio.run(scenario())
    assert cell.revoked == 1 and cell.closed


def test_cancelled_wait_and_concurrent_wait_share_one_receipt():
    import threading

    entered, release = threading.Event(), threading.Event()
    class HeldCell(_FakeCell):
        reads = 0
        def wait(self, timeout=5):
            self.reads += 1
            entered.set()
            assert release.wait(3)
            return 0

    async def scenario():
        cell = HeldCell(None)
        proc = owned_process.OwnerCellProcess(cell, None, SimpleNamespace(transport=None))
        first = asyncio.create_task(proc.wait())
        assert await asyncio.to_thread(entered.wait, 3)
        second = asyncio.create_task(proc.wait())
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert cell.reads == 1 and not cell.closed
        release.set()
        assert await second == 0
        assert cell.reads == 1 and cell.closed
    asyncio.run(scenario())


def _launcher(tmp_path, overflow=None):
    module = runpy.run_path(str(Path(__file__).resolve().parents[1]
                               / 'deploy/role_owner_launcher.py'))
    launcher = object.__new__(module['OwnerLauncher'])
    launcher.data_root = str(tmp_path / 'data')
    launcher.overflow_uid = os.getuid() if overflow is None else overflow
    launcher.daemon_pid = os.getpid()
    launcher.bindings = {('alice', 'alice'): module['FIRST'] + 1}
    launcher.jobs = {}
    launcher.delete_fences = {}
    launcher._daemon_endpoint = lambda fd, kind: None
    return launcher


def _request(**override):
    return {**dict(op='START', kind='provider-discovery', principal='alice',
                   command_center='alice', egress=False), **override}


def test_mapper_refuses_schema_identity_and_descriptor_count(tmp_path):
    launcher = _launcher(tmp_path)
    for request in (_request(op='SPAWN'), _request(uid=300002), _request(executable='/bin/sh'),
                    _request(profile='cell-nested'), _request(egress=1),
                    _request(snapshot='/data/alice')):
        with pytest.raises(ValueError, match='unsupported owner engine'):
            launcher._decoder(request, [0, 1, 2])
    with pytest.raises(ValueError, match='unsupported owner engine'):
        launcher._decoder(_request(), [0, 1])  # missing snapshot descriptor
    with pytest.raises(ValueError, match='unsupported owner engine'):
        launcher._decoder(_request(egress=True), [0, 1, 2])  # missing egress socket


def _open(path):
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


@pytest.mark.parametrize('case', ['foreign', 'center-root', 'nested', 'not-daemon'])
def test_mapper_refuses_foreign_or_unsealed_snapshot(tmp_path, case):
    root, own = _snapshot(tmp_path)
    _, foreign = _snapshot(tmp_path, center='bob')
    target = {'foreign': foreign, 'center-root': root / 'alice',
              'nested': own / 'sub', 'not-daemon': own}[case]
    target.mkdir(exist_ok=True)
    launcher = _launcher(tmp_path, overflow=os.getuid() + 1 if case == 'not-daemon' else None)
    stream, status = socket.socketpair()
    fd = _open(target)
    try:
        with pytest.raises(ValueError, match='provider snapshot'):
            launcher._decoder(_request(), [stream.fileno(), fd, status.fileno()])
    finally:
        os.close(fd)
        stream.close()
        status.close()


@pytest.mark.parametrize('case', ['foreign-center', 'other-daemon', 'regular-file'])
def test_mapper_refuses_foreign_or_revoked_egress(tmp_path, case):
    root, own = _snapshot(tmp_path)
    center = {'foreign-center': 'bob'}.get(case, 'alice')
    sidecar = root / '.universe-sidecars' / center
    sidecar.mkdir(parents=True)
    pid = os.getpid() + 1 if case == 'other-daemon' else os.getpid()
    path = sidecar / f'egress-{pid}.sock'
    server = socket.socket(socket.AF_UNIX)
    if case == 'regular-file':
        path.write_bytes(b'')
    else:
        server.bind(str(path))
    egress = os.open(path, os.O_PATH | os.O_NOFOLLOW)
    launcher = _launcher(tmp_path)
    stream, status = socket.socketpair()
    fd = _open(own)
    try:
        with pytest.raises(ValueError, match='provider egress'):
            launcher._decoder(_request(egress=True),
                              [stream.fileno(), fd, egress, status.fileno()])
    finally:
        for value in (fd, egress):
            os.close(value)
        for value in (stream, status, server):
            value.close()
