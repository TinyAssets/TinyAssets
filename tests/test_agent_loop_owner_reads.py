"""Owner reads served by the loop: the turn's own owner and command center only."""

from __future__ import annotations

import json
import sqlite3

import pytest

from tinyassets.agent_loop.owner_reads import OwnerReads


@pytest.fixture
def home(tmp_path):
    from tests.engine_authority_helpers import seed_engine_authority
    from tinyassets.daemon_server import set_founder_home

    universe = tmp_path / "u-home"
    universe.mkdir()
    set_founder_home(tmp_path, founder_sub="owner", universe_id="u-home",
                     platform_generated=True)
    seed_engine_authority(tmp_path, actor="owner", graph="u-home")
    with sqlite3.connect(universe / ".conversation_memory.db") as conn:
        conn.execute("CREATE TABLE conversation_turns (id INTEGER PRIMARY KEY, "
                     "session_id TEXT, speaker TEXT, ts REAL, content TEXT)")
        conn.execute("INSERT INTO conversation_turns (session_id, speaker, ts, content) "
                     "VALUES ('principal:owner', 'founder', 1.0, 'plant the tomatoes')")
        conn.execute("INSERT INTO conversation_turns (session_id, speaker, ts, content) "
                     "VALUES ('principal:intruder', 'founder', 2.0, 'not yours')")
    return universe


def test_history_pages_the_founders_own_conversation(home):
    reads = OwnerReads(owner="owner", universe_dir=home)
    page = json.loads(reads.history())["history"]
    assert page["available"] is True
    message_id = str(page["messages"][0]["id"])
    chunk = json.loads(reads.history(message_id=message_id))["history"]["chunk"]
    assert chunk == "plant the tomatoes"


def test_history_never_shows_another_sessions_message(home):
    reads = OwnerReads(owner="owner", universe_dir=home)
    assert "error" in json.loads(reads.history(message_id="2"))["history"]


def test_history_refuses_an_owner_who_does_not_hold_the_command_center(home):
    reads = OwnerReads(owner="intruder", universe_dir=home)
    assert json.loads(reads.history()) == {"error": "shared_self_requires_current_founder"}


def test_an_unknown_argument_is_refused(home):
    reads = OwnerReads(owner="owner", universe_dir=home)
    assert "does not take" in json.loads(reads.call("activity", {"universe_id": "x"}))["error"]
