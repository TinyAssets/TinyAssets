"""Local-only schedule consent, durable claims, time zones and copied CLI."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_export_local_runner import RUNNER, SOURCE

NOW = 1791972000.0


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "agents/main").mkdir(parents=True)
    (tmp_path / "agents/main/AGENTS.md").write_text("Help with the project")
    return tmp_path


def configure(folder, *, trigger=None, rows=None):
    row = {"id": "job", "agent": "main", "message": "Summarize the project", "enabled": True,
           "trigger": trigger or {"kind": "interval", "seconds": 300,
                                  "start_at": dt.datetime.fromtimestamp(NOW, dt.UTC).isoformat()}}
    (folder / "automations.json").write_text(json.dumps({
        "schema_version": 1, "automations": rows or [row],
    }))
    return row


def test_enable_is_required_before_any_schedule_state_or_turn(folder):
    configure(folder)
    with pytest.raises(ValueError, match="--enable"):
        RUNNER["schedule"](folder, once=True, turn=lambda _: pytest.fail("turn without opt-in"))
    assert not (folder / ".run-state").exists()


def test_claim_is_durable_before_turn_and_slot_is_not_replayed(folder):
    configure(folder)
    turns = []
    def turn(row):
        ledger = json.loads((folder / ".run-state/schedules.json").read_text())
        assert ledger["attempts"][row["id"]]["status"] == "claimed"
        turns.append(row["id"])
        return "done"
    for now in (NOW, NOW + 1, NOW + 299, NOW + 300, NOW - 300):
        RUNNER["schedule"](folder, enabled=True, once=True, turn=turn, clock=lambda: now)
    assert turns == ["job", "job"]
    assert json.loads((folder / ".run-state/schedules.json").read_text())["attempts"]["job"] == {
        "due": NOW + 300, "local": "", "status": "completed",
    }


def test_failure_and_process_death_do_not_replay_uncertain_effects(folder):
    configure(folder)
    code = '''import os, runpy, sys
r=runpy.run_path(sys.argv[1])
def turn(row):
    open(sys.argv[2]+"/effect", "w").write("happened")
    os._exit(71)
r["schedule"](sys.argv[2], enabled=True, once=True, turn=turn, clock=lambda: float(sys.argv[3]))
'''
    result = subprocess.run([sys.executable, "-c", code, str(SOURCE), str(folder), str(NOW)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 71, result.stderr
    assert (folder / "effect").read_text() == "happened"
    with pytest.raises(RuntimeError, match="Uncertain"):
        RUNNER["schedule"](folder, enabled=True, once=True, turn=lambda _: pytest.fail("replayed"),
                           clock=lambda: NOW + 300)


def test_python_failure_keeps_claim_instead_of_inventing_no_effect(folder):
    configure(folder)
    def failed(row):
        raise OSError("response lost after effect")
    with pytest.raises(OSError):
        RUNNER["schedule"](folder, enabled=True, once=True, turn=failed, clock=lambda: NOW)
    ledger = json.loads((folder / ".run-state/schedules.json").read_text())
    assert ledger["attempts"]["job"]["status"] == "claimed"


def test_fsync_failure_prevents_turn(folder, monkeypatch):
    configure(folder)
    state = RUNNER["ScheduleState"](folder)
    state.close()  # establish the directory before injecting the claim failure
    monkeypatch.setattr(RUNNER["os"], "fsync", lambda _: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        RUNNER["schedule"](folder, enabled=True, once=True, clock=lambda: NOW,
                           turn=lambda _: pytest.fail("effect before durable claim"))


def test_concurrent_runner_and_symlinked_state_are_refused(folder):
    configure(folder)
    state = RUNNER["ScheduleState"](folder)
    try:
        with pytest.raises(BlockingIOError):
            RUNNER["schedule"](folder, enabled=True, once=True, clock=lambda: NOW,
                               turn=lambda _: pytest.fail("second runner"))
    finally:
        state.close()
    target = folder / "unchanged"
    target.write_text("owner data")
    (folder / ".run-state/schedules.json").symlink_to(target)
    with pytest.raises(OSError):
        RUNNER["schedule"](folder, enabled=True, once=True, clock=lambda: NOW,
                           turn=lambda _: pytest.fail("linked ledger"))
    assert target.read_text() == "owner data"


def test_configuration_change_stops_before_second_turn(folder):
    row = configure(folder)
    configure(folder, rows=[row, dict(row, id="second")])
    calls = []
    def turn(row):
        calls.append(row["id"])
        configure(folder, rows=[dict(row, message="changed")])
        return "done"
    with pytest.raises(ValueError, match="changed"):
        RUNNER["schedule"](folder, enabled=True, once=True, clock=lambda: NOW, turn=turn)
    assert calls == ["job"]
    with pytest.raises(ValueError, match="changed"):
        RUNNER["schedule"](folder, enabled=True, once=True, clock=lambda: NOW, turn=turn)


def test_cron_dst_fold_is_one_wall_clock_slot_and_gap_does_not_run(folder):
    row = configure(folder, trigger={"kind": "cron", "expression": "30 1 * * *",
                                    "timezone": "America/New_York"})
    first = dt.datetime(2026, 11, 1, 5, 30, tzinfo=dt.UTC).timestamp()
    repeated = dt.datetime(2026, 11, 1, 6, 30, tzinfo=dt.UTC).timestamp()
    slot = RUNNER["due_slot"](row, first, None)
    assert slot["local"] == "2026-11-01T01:30"
    assert RUNNER["due_slot"](row, repeated, slot) is None
    row["trigger"]["expression"] = "30 2 * * *"
    gap = dt.datetime(2026, 3, 8, 7, 30, tzinfo=dt.UTC).timestamp()
    assert RUNNER["due_slot"](row, gap, None) is None


def test_offline_backlog_disabled_and_future_once_do_not_run(folder):
    row = configure(folder, trigger={"kind": "once",
                                    "at": dt.datetime.fromtimestamp(NOW, dt.UTC).isoformat()})
    assert RUNNER["due_slot"](row, NOW - 1, None) is None
    assert RUNNER["due_slot"](row, NOW + 60, None) is None
    assert RUNNER["due_slot"](row, NOW + 1, None) is not None
    row["enabled"] = False
    assert RUNNER["due_slot"](row, NOW, None) is None


@pytest.mark.parametrize("trigger", [
    {"kind": "interval", "seconds": True, "start_at": "2026-01-01T00:00:00Z"},
    {"kind": "interval", "seconds": 60, "start_at": "2026-01-01T00:00:00"},
    {"kind": "cron", "expression": "*/0 * * * *", "timezone": "UTC"},
    {"kind": "cron", "expression": "99 * * * *", "timezone": "UTC"},
    {"kind": "cron", "expression": "* * * * *", "timezone": "../private"},
    {"kind": "event", "topic": "hosted"},
])
def test_unsupported_schedule_data_refuses_without_state(folder, trigger):
    configure(folder, trigger=trigger)
    with pytest.raises(ValueError):
        RUNNER["schedule"](folder, enabled=True, once=True)
    assert not (folder / ".run-state").exists()


def test_duplicate_data_and_corrupt_ledger_do_not_default(folder):
    configure(folder)
    original = (folder / "automations.json").read_text()
    duplicate = original.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
    (folder / "automations.json").write_text(duplicate)
    with pytest.raises(ValueError, match="Duplicate"):
        RUNNER["schedules"](folder)
    (folder / "automations.json").write_text(original)
    state = RUNNER["ScheduleState"](folder)
    state.save({"configuration": RUNNER["schedules"](folder)[1], "attempts": {
        "job": {"due": "not-a-time", "local": "", "status": "completed"},
    }})
    state.close()
    with pytest.raises(ValueError, match="attempt"):
        RUNNER["schedule"](folder, enabled=True, once=True)


def test_copied_schedule_cli_uses_owned_model_and_does_not_start_implicitly(folder):
    from tinyassets.okf.export_assets import generated_files

    requests = []
    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            payload = json.dumps({"choices": [{"message": {
                "role": "assistant", "content": "scheduled reply",
            }}]}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        configure(folder, trigger={"kind": "interval", "seconds": 3600,
                  "start_at": (dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)).isoformat()})
        (folder / "run/agent.py").write_bytes(SOURCE.read_bytes())
        (folder / "run/schedule").write_bytes(generated_files()["run/schedule"])
        env = {key: value for key, value in os.environ.items() if not RUNNER["_model_key"](key)}
        env.update(MODEL_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1", MODEL_NAME="local")
        command = ["sh", str(folder / "run/schedule"), "--once"]
        refused = subprocess.run(command, capture_output=True, text=True, env=env, timeout=20)
        assert refused.returncode == 2 and not requests and not (folder / ".run-state").exists()
        for _ in range(2):
            result = subprocess.run(command + ["--enable"], capture_output=True, text=True,
                                    env=env, timeout=20)
            assert result.returncode == 0, result.stderr
        assert len(requests) == 1
        assert requests[0]["messages"][-1]["content"] == "Summarize the project"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
