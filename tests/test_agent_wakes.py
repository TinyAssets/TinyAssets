"""Durable follow-ups through real owner stores and the shared continuation worker."""
import asyncio
import importlib
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.test_owner_notifications import _home
from tinyassets import agent_wakes as wakes
from tinyassets import request_continuations, runs
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.served_tools import BACKEND_ENGINE_CAPABILITIES
from tinyassets.storage import pending_requests
from tinyassets.ta_capabilities import Capabilities, ExecutionContext

OWNER = "wake-owner"
OTHER = "other-owner"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    home = _home(tmp_path, "wake-home", OWNER)
    _home(tmp_path, "other-home", OTHER)
    return home


def register(home, **kwargs):
    return wakes.register(home, OWNER, "main", note="Resume my authorized task",
                          now=1000, **kwargs)["wake_id"]


def status(home, key):
    return next(row for row in wakes.listing(home, OWNER) if row["wake_id"] == key)


def test_time_repeats_cancel_and_restart(home):
    calls = []
    key = register(home, after_seconds=30, interval_seconds=3600, max_fires=2)
    def run(_home, row):
        calls.append(row["wake_id"])
        return {"status": "completed"}
    assert wakes.recover(home, run=run, now=1029) == 0
    importlib.reload(wakes)
    assert wakes.recover(home, run=run, now=1030) == 1
    assert wakes.recover(home, run=run, now=4629) == 0
    assert wakes.recover(home, run=run, now=4630) == 1
    assert wakes.recover(home, run=run, now=9000) == 0
    assert calls == [key, key] and status(home, key)["status"] == "done"
    cancelled = register(home, after_seconds=30)
    wakes.cancel(home, OWNER, cancelled)
    assert wakes.recover(home, run=run, now=1040) == 0
    assert status(home, cancelled)["status"] == "cancelled"


def test_request_answer_and_run_events(home):
    with identity_context(Identity(user_id=OWNER, username=OWNER, capabilities=[])):
        from tinyassets import turn_interrupt
        with turn_interrupt.interactive_turn(OWNER, home.name, agent_id="main"):
            request = pending_requests.create_request(
                home, kind="Review", title="Proceed?", body="", fields=[],
                action={"type": "answer"}, dedupe_key="wake-request", agent="main")
    request_id = request["request_id"]
    key = register(home, condition={"kind": "request_answered", "request_id": request_id})
    run_id = runs.create_run(home.parent, branch_def_id="example", thread_id="test",
                            inputs={}, actor=OWNER, owner_user_id=OWNER,
                            queue_universe_id=home.name)
    finished = register(home, condition={"kind": "run_finished", "run_id": run_id})
    failed = register(home, condition={"kind": "run_failed", "run_id": run_id})
    calls = []
    def run(_home, row):
        calls.append(row["wake_id"])
        return {"status": "completed"}
    assert wakes.recover(home, run=run, now=1000) == 0
    pending_requests.resolve_request(home, request_id, status="answered", decision="allowed")
    with runs._connect(home.parent) as conn:
        conn.execute("UPDATE runs SET status='failed' WHERE run_id=?", (run_id,))
    assert wakes.recover(home, run=run, now=1030) == 3
    assert set(calls) == {key, finished, failed}


def test_release_contains_commit_or_pr(home, tmp_path, monkeypatch):
    from tinyassets.api import status as api_status

    receipt = tmp_path / "release.json"
    monkeypatch.setattr(api_status, "_release_state_path", lambda: receipt)
    sha, ancestor = "a" * 40, "b" * 40
    keys = {register(home, condition={"kind": "release", "commit": ancestor}),
            register(home, condition={"kind": "release", "pr": 4584})}
    receipt.write_text(json.dumps({"git_sha": sha, "containment": {
        "git_sha": "c" * 40, "commits": [ancestor], "prs": [4584]}}))
    calls = []
    def run(_home, row):
        calls.append(row["wake_id"])
        return {"status": "completed"}
    assert wakes.recover(home, run=run, now=1000) == 0
    receipt.write_text(json.dumps({"git_sha": sha, "containment": {
        "git_sha": sha, "commits": [sha, ancestor], "prs": [4584]}}))
    assert wakes.recover(home, run=run, now=1030) == 2
    assert set(calls) == keys
    assert "containment" not in api_status._load_release_state().get("extra", {})


def test_release_manifest_excludes_unmerged_history(tmp_path):
    from scripts.release_containment import containment

    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args],
                              capture_output=True, text=True, check=True).stdout.strip()
    git("init")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    git("commit", "--allow-empty", "-m", "Ship fix (#4584)")
    deployed = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-m", "Unshipped fix (#9999)")
    future = git("rev-parse", "HEAD")
    manifest = containment(deployed, cwd=tmp_path)
    assert manifest == {"git_sha": deployed, "commits": [deployed], "prs": [4584]}
    assert future not in manifest["commits"]


def test_probe_bound_unknown_retry_expiry_and_cancel_race(home):
    attempts = []
    def probe(_home, row, _condition):
        attempts.append(row["checks"])
        if len(attempts) == 1:
            raise OSError("lost reply")
        return False
    key = register(home, condition={"kind": "probe", "command": "test -f ready"},
                   max_checks=2, backoff_seconds=10)
    for instant in (1000, 1010, 1030, 1070):
        assert wakes.recover(home, probe=probe, now=instant) == 0
    assert attempts == [1, 2]
    assert status(home, key)["status"] == "exhausted"
    cancelled = register(home, condition={"kind": "probe", "command": "true"})
    def cancel_during_probe(*_):
        wakes.cancel(home, OWNER, cancelled)
        return True
    assert wakes.recover(home, probe=cancel_during_probe, now=1000) == 0
    expired = register(home, after_seconds=1, expires_in_seconds=2)
    assert wakes.recover(home, now=1002) == 0
    assert status(home, expired)["status"] == "expired"


@pytest.mark.parametrize("operation", ["register", "list", "cancel", "agent", "run", "request"])
def test_foreign_authority_refused(home, operation):
    key = register(home, after_seconds=1)
    foreign = home.parent / "other-home"
    with pytest.raises(PermissionError):
        if operation == "register":
            wakes.register(home, OTHER, "main", note="steal", after_seconds=1)
        elif operation == "list":
            wakes.listing(home, OTHER)
        elif operation == "cancel":
            wakes.cancel(foreign, OTHER, key)
        elif operation == "agent":
            wakes.register(home, OWNER, "foreign-agent", note="steal", after_seconds=1)
        elif operation == "run":
            run_id = runs.create_run(home.parent, branch_def_id="example", thread_id="other",
                                    inputs={}, actor=OTHER, queue_universe_id=foreign.name)
            register(home, condition={"kind": "run_finished", "run_id": run_id})
        else:
            row = pending_requests.create_request(
                foreign, kind="Review", title="Private", body="", fields=[],
                action={"type": "answer"}, dedupe_key="private", agent="main")
            register(home, condition={"kind": "request_answered", "request_id": row["request_id"]})
    assert status(home, key)["status"] == "pending"


def test_blocked_turn_registers_through_ta_and_worker_resumes_after_restart(home, monkeypatch):
    backend = Capabilities(home, ExecutionContext(home.name, OWNER, "main"), [],
                           None, lambda: None, capability_grant=BACKEND_ENGINE_CAPABILITIES)
    ready = False
    turns = []
    def converse(**kwargs):
        turns.append(kwargs)
        if not ready:
            result = asyncio.run(backend.dispatch({"op": "call", "name": "wake:register",
                "arguments": {"note": "Publish the authorized API after the blocker clears",
                              "condition": {"kind": "probe", "command": "test -f ready"}}}))
            assert result["result"]["status"] == "pending"
        else:
            assert "Publish the authorized API" in kwargs["message"]
        return json.dumps({"status": "completed"})
    monkeypatch.setattr("tinyassets.universe_server.converse", converse)
    monkeypatch.setattr("tinyassets.wake_conditions.run_probe", lambda *_: ready)
    monkeypatch.setattr(wakes.time, "time", lambda: 1000)
    converse(message="Build the API", graph_id=home.name, agent_id="main")
    request_continuations.tick(home.parent)
    assert len(turns) == 1
    importlib.reload(request_continuations)
    ready = True
    monkeypatch.setattr(wakes.time, "time", lambda: 1030)
    request_continuations.tick(home.parent)
    assert len(turns) == 2 and turns[-1]["agent_id"] == "main"
    assert turns[-1]["input_method"] == "unknown"
    assert wakes.listing(home, OWNER)[0]["status"] == "done"
    request_continuations.tick(home.parent)
    assert len(turns) == 2


def test_owed_turn_survives_uncertain_delivery(home):
    key = register(home, after_seconds=0)
    assert wakes.recover(home, now=1000, run=lambda *_: {"error": "offline"}) == 0
    assert status(home, key)["status"] == "firing"
    importlib.reload(wakes)
    assert wakes.recover(home, now=1030, run=lambda *_: {"status": "completed"}) == 1
    assert status(home, key)["fires"] == 1


def test_narrowed_launch_cannot_schedule_wider_chat_authority(home):
    backend = Capabilities(home, ExecutionContext(home.name, OWNER, "main"), [],
                           None, lambda: None, capability_grant=["read", "bash"])
    result = asyncio.run(backend.dispatch({"op": "call", "name": "wake:register",
        "arguments": {"note": "Run with more authority later", "after_seconds": 0}}))
    assert "full serving-owner launch" in result["error"]
    assert wakes.listing(home, OWNER) == []


def test_resumed_turn_uses_real_owner_provider_admission(home, monkeypatch):
    from tests.owner_answer import connect_owner_provider
    from tinyassets.provider_assignment import _served_request_agent
    from tinyassets.providers.owner_binding import require_owner_bound_context

    connect_owner_provider(home, OWNER)
    key = register(home, after_seconds=0)
    turns = []
    def writer(turn_input, *, universe_context, **_):
        require_owner_bound_context(universe_context, operation="converse")
        capability, _agent = _served_request_agent(
            home.parent, home, universe_context.provider_request, "writer", "converse")
        turns.append((capability.principal_id, universe_context.agent_id, turn_input))
        return "Resumed the authorized task."
    monkeypatch.setattr("tinyassets.universe_intelligence._call_writer", writer)
    assert wakes.recover(home, now=1000) == 1
    assert turns[0][:2] == (OWNER, "main")
    assert "Resume my authorized task" in turns[0][2]
    assert status(home, key)["status"] == "done"


def test_uncertain_probe_consumes_bound_across_restart(home):
    attempts = []
    def unavailable(*_):
        attempts.append(1)
        raise OSError("lost execution result")
    key = register(home, condition={"kind": "probe", "command": "test -f ready"}, max_checks=1)
    assert wakes.recover(home, now=1000, probe=unavailable) == 0
    importlib.reload(wakes)
    assert wakes.recover(home, now=1060, probe=unavailable) == 0
    assert attempts == [1]
    assert status(home, key)["status"] == "exhausted"


def test_fresh_process_recovers_persisted_follow_up(home):
    key = register(home, after_seconds=10)
    script = """
import sys
from pathlib import Path
from tinyassets import agent_wakes
home = Path(sys.argv[1])
def resumed(home, row):
    print(row['wake_id'])
    return {'status': 'completed'}
assert agent_wakes.recover(home, now=1010, run=resumed) == 1
"""
    child = subprocess.run([sys.executable, "-c", script, str(home)],
                           text=True, capture_output=True, timeout=60)
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == key
    assert status(home, key)["status"] == "done"


def test_connection_checks_only_owner_catalogue(home, monkeypatch):
    seen = []
    views = []
    def catalogue(_base, **scope):
        seen.append(scope)
        return [(None, view, None) for view in views]
    monkeypatch.setattr("tinyassets.broker.catalog.connections", catalogue)
    key = register(home, condition={"kind": "connection", "destination": "api.example.com"})
    views.append(SimpleNamespace(owner_user_id=OTHER, revoked_at=None,
                                 destination="api.example.com"))
    assert wakes.recover(home, now=1000) == 0
    views.append(SimpleNamespace(owner_user_id=OWNER, revoked_at=None,
                                 destination="api.example.com"))
    assert wakes.recover(home, now=1030, run=lambda *_: {"status": "completed"}) == 1
    assert status(home, key)["status"] == "done"
    assert seen == [{"principal": OWNER, "command_center": home.name}] * 2


def test_app_lists_and_cancels_only_signed_in_owners_wakes(home, monkeypatch):
    from tests.test_inline_owner_sessions import request
    from tinyassets import onboarding
    from tinyassets.onboarding import owner_sessions
    from tinyassets.onboarding.wakes import handle_wakes

    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_read_home",
                        lambda identity: home.name if identity.user_id == OWNER else "other-home")
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://tinyassets.io"})
    monkeypatch.setattr(owner_sessions, "lookup", lambda cookie: {
        "identity_json": json.dumps({"user_id": OWNER})} if cookie == "protected" else None)
    key = register(home, after_seconds=1)
    req = request()
    req.method = "GET"
    async def body():
        return {"wake_id": key}
    req.json = body
    with identity_context(Identity(user_id=OWNER, username=OWNER, capabilities=[])):
        response = asyncio.run(handle_wakes(req))
        assert response.status_code == 200
        assert json.loads(response.body)["wakes"][0]["wake_id"] == key
        req.method = "POST"
        req.cookies = {}
        assert asyncio.run(handle_wakes(req)).status_code == 403
        assert status(home, key)["status"] == "pending"
        req.cookies = {owner_sessions.COOKIE: "protected"}
        assert asyncio.run(handle_wakes(req)).status_code == 200
    with identity_context(Identity(user_id=OTHER, username=OTHER, capabilities=[])):
        req.method = "GET"
        response = asyncio.run(handle_wakes(req))
        assert json.loads(response.body)["wakes"] == []
    assert status(home, key)["status"] == "cancelled"
