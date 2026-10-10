"""Probe commands execute through the shipping owner tool path and real jail."""
import shutil
import sys

import pytest

from tests.test_agent_wakes import home as home
from tests.test_agent_wakes import register, status
from tinyassets import agent_wakes

pytestmark = [pytest.mark.real_jail, pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("bwrap"),
    reason="requires Linux + bubblewrap; owner=Jonnyton; runs-in=linux-jail-proof")]


def test_probe_clears_blocker_inside_owner_jail_and_resumes(home):
    (home / "notes").mkdir()
    key = register(home, condition={"kind": "probe", "command":
        "test -f /u/notes/ready && test ! -e /u/../other-home/private"}, max_checks=2)
    turns = []
    def run(_home, row):
        turns.append(row["note"])
        return {"status": "completed"}
    assert agent_wakes.recover(home, now=1000, run=run) == 0
    assert status(home, key)["checks"] == 1
    (home / "notes" / "ready").write_text("blocker cleared")
    assert agent_wakes.recover(home, now=1030, run=run) == 1
    assert turns == ["Resume my authorized task"]
    assert status(home, key)["status"] == "done"
