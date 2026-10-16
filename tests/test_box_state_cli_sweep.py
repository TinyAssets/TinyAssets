"""Unretried private CLI results cannot pin a credential lease forever."""

import pytest

from tinyassets.boxes.provider import BoxError
from tinyassets.boxes.state import BoxHostState


def test_unacknowledged_cli_result_expires_without_a_same_key_retry(tmp_path):
    state = BoxHostState(tmp_path / "boxhost.db")
    try:
        state.begin("owner", "cli-old", "exec", "digest-old", cli_lease="pin")
        state.register_exec("owner", "cli-old", "exec-old")
        state.finish("owner", "cli-old", {
            "exec_id": "exec-old", "cli_lease": "pin", "cli_ack": False,
            "cli_deadline": 200,
        })
        with pytest.raises(BoxError, match="unsettled"):
            state.begin("owner", "cli-new", "exec", "digest-new", cli_lease="pin")
        assert state.sweep_cli(now=199) == ()
        assert state.sweep_cli(now=200) == ("exec-old",)
        assert state.lookup("owner", "cli-old")["outcome"]["cli_lost"] is True
        assert state.begin("owner", "cli-new", "exec", "digest-new",
                           cli_lease="pin") is None
    finally:
        state.close()


def test_departed_host_releases_result_it_can_no_longer_deliver(tmp_path):
    path = tmp_path / "boxhost.db"
    state = BoxHostState(path)
    state.begin("owner", "cli-old", "exec", "digest-old", cli_lease="pin")
    state.finish("owner", "cli-old", {"cli_lease": "pin", "cli_ack": False})
    state.close()
    restarted = BoxHostState(path)
    try:
        assert restarted.lookup("owner", "cli-old")["outcome"]["cli_lost"] is True
        assert restarted.begin("owner", "cli-new", "exec", "digest-new",
                               cli_lease="pin") is None
    finally:
        restarted.close()
