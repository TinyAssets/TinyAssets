"""A pre-forked broker is pinned and never acquired through the legacy launcher."""
import os
import socket

import pytest

from tinyassets.broker import supervisor as module


@pytest.mark.skipif(not hasattr(os, 'pidfd_open'), reason='Linux process handles')
def test_bootstrapped_acquisition_never_opens_legacy_launcher(tmp_path, monkeypatch):
    monkeypatch.setattr(module, '_protect_daemon', lambda: None)
    instance = module.BrokerSupervisor.from_bootstrap(
        tmp_path, broker_pid=os.getpid(), socket_path=tmp_path / 'broker.sock', proof='x' * 40)
    try:
        def forbidden(*args, **kwargs):
            pytest.fail('bootstrap tried the legacy privileged launcher')
        monkeypatch.setattr(socket, 'socket', forbidden)
        instance._acquire()
    finally:
        os.close(instance._bootstrap_pidfd)


@pytest.mark.skipif(not hasattr(os, 'pidfd_open'), reason='Linux process handles')
def test_bootstrap_rejects_same_uid_different_broker_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(module, '_protect_daemon', lambda: None)
    instance = module.BrokerSupervisor.from_bootstrap(
        tmp_path, broker_pid=os.getpid(), socket_path=tmp_path / 'broker.sock', proof='x' * 40)
    try:
        monkeypatch.setattr(module, '_peer', lambda _: (os.getpid() + 1, 1002, 1002))
        with pytest.raises(module.BrokerUidSplitRequired, match='broker identity'):
            instance.verify_broker(None)
    finally:
        os.close(instance._bootstrap_pidfd)


@pytest.mark.skipif(not hasattr(os, 'pidfd_open'), reason='Linux process handles')
def test_dead_bootstrapped_broker_cannot_be_reacquired(tmp_path, monkeypatch):
    import subprocess
    import sys

    monkeypatch.setattr(module, '_protect_daemon', lambda: None)
    child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'],
                             stdin=subprocess.PIPE)
    instance = module.BrokerSupervisor.from_bootstrap(
        tmp_path, broker_pid=child.pid, socket_path=tmp_path / 'broker.sock', proof='x' * 40)
    try:
        child.communicate(timeout=5)
        with pytest.raises(module.BrokerUidSplitRequired, match='container restart required'):
            instance._acquire()
        with pytest.raises(module.BrokerUidSplitRequired, match='container restart required'):
            instance.fence()
    finally:
        child.kill() if child.poll() is None else None
        child.wait()
        os.close(instance._bootstrap_pidfd)
