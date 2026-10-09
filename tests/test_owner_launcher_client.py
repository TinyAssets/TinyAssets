"""Real Unix credentials reject daemon peers impersonating the bounded launcher."""
import array
import os
import socket
from pathlib import Path

import pytest

from tinyassets.owner_launcher_client import OwnerLauncherClient

# This drives the real bounded launcher channel; no double is installed.
pytestmark = [
    pytest.mark.role_split,
    pytest.mark.skipif(os.name != 'posix' or not hasattr(os, 'pidfd_open'),
                       reason='Linux pidfd and Unix credentials'),
]


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
