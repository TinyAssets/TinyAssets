"""Real Unix credentials reject daemon peers impersonating the bounded launcher."""
import array
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets.owner_launcher_client import OwnerLauncherClient

# This drives the real bounded launcher channel; no double is installed.
pytestmark = [
    pytest.mark.role_split,
    pytest.mark.skipif(os.name != 'posix' or not hasattr(os, 'pidfd_open'),
                       reason='Linux pidfd and Unix credentials'),
]


def test_cell_diagnostics_never_echo_exception_material():
    from tinyassets.cell_diagnostics import PATTERN, failure_reason

    secret = 'owner-private-key-and-path'
    try:
        raise PermissionError(13, secret, '/' + secret)
    except PermissionError as exc:
        reason = failure_reason(exc, 'decoder')
    assert secret not in reason
    assert 'PermissionError:errno=13' in reason
    assert PATTERN.fullmatch(('TA_CELL_FAILURE ' + reason + '\n').encode())
    assert not PATTERN.search(b'TA_CELL_FAILURE owner-private-key-and-path\n')
    assert not PATTERN.search(b'TA_CELL_FAILURE decoder:secret.py:1:ValueError:errno=None\n')


def test_daemon_uid_cannot_impersonate_owner_launcher():
    assert os.getuid() == 1001  # linux_oracle.py's unprivileged venue
    daemon, impostor = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    with daemon, impostor:
        client = OwnerLauncherClient(daemon, os.getpid())
        try:
            impostor.sendall(b'{"op":"STOPPED"}')
            with pytest.raises(RuntimeError, match='unauthenticated'):
                client._reply()
        finally:
            client._close()


def test_rejected_reply_closes_all_received_descriptors(tmp_path):
    assert os.getuid() == 1001
    daemon, impostor = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    with daemon, impostor, (tmp_path / 'private').open('wb') as private:
        client = OwnerLauncherClient(daemon, os.getpid())
        try:
            before = len(list(Path('/proc/self/fd').iterdir()))
            impostor.sendmsg([b'{"op":"STOPPED"}'], [(
                socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [private.fileno()]))])
            with pytest.raises(RuntimeError, match='unauthenticated'):
                client._reply()
            assert len(list(Path('/proc/self/fd').iterdir())) == before
        finally:
            client._close()


def test_independent_cell_receipt_rejects_daemon_impersonation():
    from tinyassets.broker.owner_identities import OwnerIdentity
    from tinyassets.owner_launcher_client import OwnerCell

    daemon, mapper = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    status, impostor = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    stream, peer = socket.socketpair()
    with daemon, mapper, status, impostor, stream, peer:
        client = OwnerLauncherClient(daemon, os.getpid())
        status.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        cell = OwnerCell(client, stream, status, OwnerIdentity(300001, 300001))
        try:
            impostor.sendall(
                b'{"op":"SPAWN_DONE","returncode":0,"uid":300001,"gid":300001}')
            with pytest.raises(RuntimeError, match='unauthenticated'):
                cell.wait()
        finally:
            cell._after_fork()
            client._close()


@pytest.mark.parametrize('machines', [[300001] * 4, list(range(300002, 300034))])
def test_cell_concurrency_refuses_before_fork_or_descriptor_use(machines):
    import runpy

    module = runpy.run_path(str(Path(__file__).resolve().parents[1]
                               / 'deploy' / 'role_owner_launcher.py'))
    launcher = object.__new__(module['OwnerLauncher'])
    launcher.delete_fences = {}
    launcher.bindings = {('alice', 'alice'): 300001}
    launcher.jobs = {i: (machine - 300000, machine, 0, None)
                     for i, machine in enumerate(machines)}
    with pytest.raises(ValueError, match='concurrency is exhausted'):
        launcher._decoder(dict(op='START', kind='image-decoder', principal='alice',
                               command_center='alice', mime='image/png'), [-1, -1])


def test_completed_cell_revoke_is_idempotent_but_fork_copy_still_refuses(monkeypatch):
    from tinyassets.broker.owner_identities import OwnerIdentity
    from tinyassets.owner_launcher_client import OwnerCell
    from tinyassets.providers.owned_process import OwnerCellProcess, disk_stop_note

    reply = dict(op='SPAWN_DONE', returncode=-9, uid=300001, gid=300001,
                 stop_reason='rss_limit: bytes=536875008 limit=536870912')
    client = SimpleNamespace(_reply=lambda **kwargs: reply)
    stream, peer = socket.socketpair()
    status, mapper = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    with peer, mapper:
        cell = OwnerCell(client, stream, status, OwnerIdentity(300001, 300001))
        process = OwnerCellProcess(cell, None, SimpleNamespace(transport=None))
        assert cell.wait() == -9
        cell.close()
        cell.revoke()
        cell.revoke()
        assert 'rss_limit' in disk_stop_note(process)
        monkeypatch.setattr(cell, '_pid', -1)
        with pytest.raises(RuntimeError, match='unavailable'):
            cell.revoke()


@pytest.mark.parametrize('usage,reason', [
    ((69, 0), 'process_limit'), ((1, 536870913), 'rss_limit'),
    (PermissionError(13, 'private path must not be returned'), 'usage_unavailable'),
])
def test_mapper_resource_kills_have_authenticated_completion_reason(monkeypatch, usage, reason):
    import runpy

    from tinyassets.broker.owner_identities import OwnerIdentity
    from tinyassets.owner_launcher_client import OwnerCell

    scope = runpy.run_path(str(Path(__file__).resolve().parents[1]
                              / 'deploy/role_owner_launcher.py'))
    launcher = object.__new__(scope['OwnerLauncher'])
    status, mapper = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    stream, peer = socket.socketpair()
    reader, writer = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(writer)
        os.read(reader, 1)  # Block until the real mapper's SIGKILL.
        os._exit(0)
    os.close(reader)
    def measure(pid):
        assert pid == child
        if isinstance(usage, Exception):
            raise usage
        return usage
    monkeypatch.setitem(launcher._service_jobs.__globals__, 'package_usage', measure)
    monkeypatch.setitem(launcher._service_jobs.__globals__, 'assert_mapper', lambda launch: None)
    launcher.jobs = {child: (1, 300001, float('inf'), mapper)}
    error_read, error_write = os.pipe()
    os.close(error_write)
    os.set_blocking(error_read, False)
    launcher.diagnostics = {child: [error_read, None, b'', None]}
    launcher.package_jobs = {child}
    launcher.launch = None
    try:
        launcher._service_jobs()
        import json
        reply = json.loads(status.recv(4096))
        assert reply['returncode'] == -9 and reply['stop_reason'].startswith(reason)
        assert 'private path' not in reply['stop_reason']
        cell = OwnerCell(SimpleNamespace(_reply=lambda **kwargs: reply), stream, status,
                         OwnerIdentity(300001, 300001))
        assert cell.wait() == -9
        assert cell.stop_reason == reply['stop_reason']
        cell.close()
        cell.revoke()
        assert not launcher.jobs and not launcher.package_jobs
    finally:
        os.close(writer)
        stream.close()
        peer.close()
        status.close()
        mapper.close()
        try:
            os.waitpid(child, 0)
        except ChildProcessError:
            pass


def test_revoke_reports_first_callsite_without_replacing_mapper_reason():
    from tinyassets.broker.owner_identities import OwnerIdentity
    from tinyassets.owner_launcher_client import OwnerCell
    from tinyassets.providers.owned_process import OwnerCellProcess, disk_stop_note

    status, mapper = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    stream, peer = socket.socketpair()
    with status, mapper, stream, peer:
        cell = OwnerCell(None, stream, status, OwnerIdentity(300001, 300001))
        process = OwnerCellProcess(cell, None, SimpleNamespace(transport=None))
        process.revoke()
        caller = cell.revoke_caller
        process.kill()
        assert cell.revoke_caller == caller
        assert 'test_revoke_reports_first_callsite' in caller
        assert mapper.recv(1) == b''
        cell.stop_reason = 'revoked'
        assert f'revoked by {caller}' in disk_stop_note(process)
        cell.stop_reason = 'rss_limit: bytes=600000000 limit=536870912'
        assert 'rss_limit' in disk_stop_note(process)
        assert caller not in disk_stop_note(process)
        cell._after_fork()
