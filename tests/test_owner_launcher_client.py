"""Real Unix credentials reject daemon peers impersonating the bounded launcher."""
import array
import os
import socket
from pathlib import Path

import pytest

from tinyassets.owner_launcher_client import OwnerLauncherClient

pytestmark = pytest.mark.skipif(
    os.name != 'posix' or not hasattr(os, 'pidfd_open'), reason='Linux pidfd and Unix credentials')


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
