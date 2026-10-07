"""PID1 orphan reaper: adopted zombies only, never held or owned children."""

import ctypes
import os
import runpy
import subprocess
import sys
import time
import traceback
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux'),
                                reason='kernel reparenting is Linux-only')


def _zombie(pid):
    raw = Path('/proc', str(pid), 'stat').read_text()
    return raw[raw.rfind(')') + 2:].split()[0] == 'Z'


def _until(predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.02)


def _scenario(reap_orphans):
    # A subreaper adopts orphans exactly as container PID1 does.
    assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
    held = os.fork()
    if held == 0:
        os._exit(0)
    owned = subprocess.Popen(['/bin/sh', '-c', 'exit 3'])
    script = 'echo $(/bin/sleep 0.1 >/dev/null & echo $!)'
    orphan = int(subprocess.run(['/bin/sh', '-c', script], check=True,
                                capture_output=True, text=True).stdout)
    _until(lambda: all(_zombie(pid) for pid in (held, owned.pid, orphan)))
    clock = [0.0]
    seen = {}
    assert reap_orphans({held}, seen, now=lambda: clock[0]) == []
    assert {pid for pid, _ in seen} == {owned.pid, orphan}
    assert owned.wait() == 3  # the owner's real status, not a stolen 0
    clock[0] = 59.0
    assert reap_orphans({held}, seen, now=lambda: clock[0]) == []
    young = int(subprocess.run(['/bin/sh', '-c', script], check=True,
                               capture_output=True, text=True).stdout)
    _until(lambda: _zombie(young))
    clock[0] = 60.0
    assert reap_orphans({held}, seen, now=lambda: clock[0]) == [orphan]
    assert not Path('/proc', str(orphan)).exists()
    assert _zombie(held) and _zombie(young)  # held identity kept; young not yet
    clock[0] = 121.0
    assert reap_orphans({held}, seen, now=lambda: clock[0]) == [young]
    assert _zombie(held) and os.waitpid(held, 0)[0] == held


def test_reaps_only_aged_adopted_orphans():
    module = runpy.run_path(str(Path(__file__).resolve().parents[1]
                                / 'deploy' / 'role_owner_launcher.py'))
    child = os.fork()
    if child == 0:
        try:
            _scenario(module['reap_orphans'])
        except BaseException:
            traceback.print_exc()
            os._exit(1)
        os._exit(0)
    assert os.waitstatus_to_exitcode(os.waitpid(child, 0)[1]) == 0
