"""Package proofs; materialization is not the still-pending D10 provisioning API."""
from __future__ import annotations

import json
import runpy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets.starter_skills import MUSE_SKILL_NAMES, starter_agent_files
from tinyassets.universe_tools import skill_index

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)


@pytest.fixture
def package(tmp_path):
    for name, body in starter_agent_files().items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


@pytest.fixture
def runner(package):
    return runpy.run_path(str(package / "starter/checkins.py"))["check"]


def write(root, name, value):
    (root / "starter" / name).write_text(json.dumps(value), encoding="utf-8")


def goal(status="active", **extra):
    return {"id": "ship", "title": "Ship a song", "status": status,
            "next_step": "Record vocals", **extra}


def test_bundle_index_assets_and_no_resident_prompt_growth(package):
    indexed = dict(skill_index(package))
    for name in MUSE_SKILL_NAMES:
        assert indexed[f"starter-{name}"]
    files = starter_agent_files()
    source = Path(__file__).resolve().parents[1] / "tinyassets/starter"
    for rel in ("AGENTS.md", "hooks.md"):
        key = rel if rel == "AGENTS.md" else f"starter/{rel}"
        assert files[key] == (source / rel).read_text(encoding="utf-8")
    for rel in ("settings.json", "goals.json", "monitors.json", "ideas.json", "workflows.json"):
        json.loads(files[f"starter/{rel}"])
    from tinyassets.custom_agents import app_ui_renderability

    assert app_ui_renderability(json.loads(files["starter/command-center.json"])) == {}


@pytest.mark.parametrize("dial,status,step,expected", [
    ("off", "done", "Record vocals", False),
    ("low", "done", "Record vocals", True),
    ("low", "blocked", "Record vocals", True),
    ("low", "active", "Mix vocals", False),
    ("high", "active", "Mix vocals", True),
    ("high", "active", "Record vocals", False),
])
def test_goals_baseline_changes_and_dial(package, runner, dial, status, step, expected):
    sent = []

    def send(title, body):
        sent.append((title, body))
        return {"request_id": "note", "delivery": {"sent": 0}}

    write(package, "settings.json", {"proactivity": dial})
    write(package, "goals.json", {"goals": [goal()]})
    assert not runner(package, "goals", send=send)["notified"]
    write(package, "goals.json", {"goals": [goal(status, next_step=step, updated_at="new")]})
    result = runner(package, "goals", send=send)
    assert result["notified"] is expected
    assert len(sent) == int(expected)
    assert not runner(package, "goals", send=send)["notified"]


def test_discussed_goals_are_acknowledged_and_failed_notify_is_retryable(package, runner):
    write(package, "goals.json", {"goals": [goal()]})
    runner(package, "goals")
    write(package, "goals.json", {"goals": [goal("blocked")]})
    runner(package, "goals", acknowledge=True)
    assert not runner(package, "goals")["notified"]
    write(package, "goals.json", {"goals": [goal("done")]})
    with pytest.raises(RuntimeError, match="not acknowledged"):
        runner(package, "goals", send=lambda *_: {"error": "unavailable"})
    assert runner(package, "goals", send=lambda *_: {"request_id": "retry"})["notified"]


@pytest.mark.parametrize("settings", [{}, {"proactivity": "loud"}])
def test_invalid_dial_fails_without_notifying(package, runner, settings):
    write(package, "settings.json", settings)
    sent = []
    with pytest.raises((KeyError, ValueError)):
        runner(package, "goals", send=lambda *args: sent.append(args))
    assert not sent
    assert not (package / "starter/goals-state.json").exists()


def test_requested_monitors_and_reminders_off_dedupe_and_new_event(package, runner):
    write(package, "settings.json", {"proactivity": "off"})
    watch = {"id": "release", "active": True, "requested": True, "matched": True,
             "change_id": "v1", "summary": "Version 1 is out", "observed_at": NOW.isoformat()}
    reminder = {"id": "call", "active": True, "requested": True,
                "due_at": (NOW + timedelta(minutes=1)).isoformat(), "summary": "Call Sam"}
    data = {"monitors": [watch], "reminders": [reminder]}
    write(package, "monitors.json", data)
    sent = []

    def send(title, body):
        sent.append((title, body))
        return {"request_id": str(len(sent))}

    assert runner(package, "monitors", now=NOW, send=send)["notified"]
    assert not runner(package, "monitors", now=NOW, send=send)["notified"]
    assert not runner(package, "reminders", now=NOW, send=send)["notified"]
    assert runner(package, "reminders", now=NOW + timedelta(minutes=1), send=send)["notified"]
    assert not runner(package, "reminders", now=NOW + timedelta(minutes=2), send=send)["notified"]
    watch.update(change_id="v2", summary="Version 2 is out")
    write(package, "monitors.json", data)
    assert runner(package, "monitors", now=NOW, send=send)["notified"]
    assert [body for _, body in sent] == ["Version 1 is out", "Call Sam", "Version 2 is out"]
    watch.update(change_id="v3", active=False)
    write(package, "monitors.json", data)
    assert not runner(package, "monitors", now=NOW, send=send)["notified"]


def test_stale_match_and_naive_reminder_are_errors(package, runner):
    write(package, "monitors.json", {"monitors": [{"id": "m", "active": True,
          "requested": True, "matched": True, "change_id": "v1", "summary": "Release",
          "observed_at": (NOW - timedelta(hours=2)).isoformat()}], "reminders": [
          {"id": "r", "active": True, "requested": True, "due_at": "2026-10-06T10:00:00"}]})
    with pytest.raises(ValueError, match="fresh observation"):
        runner(package, "monitors", now=NOW)
    with pytest.raises(ValueError, match="UTC offset"):
        runner(package, "reminders", now=NOW)


@pytest.mark.parametrize("mode", ["goals", "monitors", "reminders"])
def test_ta_notification_uses_real_owner_notify(package, monkeypatch, mode):
    """Run the script's ta invocation through the CLI to the real notify primitive."""
    from tests.test_owner_notifications import _home
    from tinyassets import ta_cli
    from tinyassets.api.agent_notifications import notify
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.storage.pending_requests import list_pending

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(package))
    home = _home(package, "u-a", "actor-a")

    def remote(message):
        if message["op"] == "catalog":
            return {"extension_roots": {}, "capabilities": [
                {"name": "write_graph", "description": "Notify", "arguments": {}}]}
        args = message["arguments"]
        assert args["target"] == "pending_request" and args["operation"] == "notify"
        return {"result": notify(universe_id="u-a", payload=json.loads(args["payload_json"]))}

    monkeypatch.setattr(ta_cli, "remote", remote)

    def run(argv, **kwargs):
        assert argv[0] == "ta" and kwargs["check"]
        return SimpleNamespace(stdout=json.dumps(ta_cli.main(argv[1:])))

    monkeypatch.setattr("subprocess.run", run)
    script = runpy.run_path(str(package / "starter/checkins.py"))
    with identity_context(Identity(user_id="actor-a", username="a", capabilities=[])):
        if mode == "goals":
            write(package, "goals.json", {"goals": [goal()]})
            script["check"](package, "goals")
            write(package, "goals.json", {"goals": [goal("done")]})
        else:
            write(package, "monitors.json", {mode: [{
                "id": "test", "active": True, "requested": True,
                "due_at": NOW.isoformat(), "matched": True, "change_id": "v1",
                "observed_at": NOW.isoformat(), "summary": "Verified change",
            }]})
        result = script["check"](package, mode, now=NOW)
    assert result["notified"]
    row, = list_pending(home)
    assert row["informational"]
    assert row["title"] == f"Starter {mode} update"
    assert row["body"].startswith("Ship a song: done" if mode == "goals" else "Verified change")


def test_publisher_does_not_touch_owner_edits_or_deleted_files(package, monkeypatch):
    """Only the read-only publisher contract, NOT D10's account preservation policy."""
    edited = package / "skills/starter-goals/SKILL.md"
    edited.write_text("My goals policy", encoding="utf-8")
    removed = package / "starter/settings.json"
    removed.unlink()

    def forbid_write(*_args, **_kwargs):
        raise AssertionError("Publishing package content must not mutate the filesystem")

    for method in ("write_text", "write_bytes", "unlink"):
        monkeypatch.setattr(Path, method, forbid_write)
    starter_agent_files()
    assert edited.read_text(encoding="utf-8") == "My goals policy"
    assert not removed.exists()


@pytest.mark.parametrize("mode", ["goals", "monitors", "reminders"])
def test_muted_notification_is_acknowledged_without_retry(package, runner, mode):
    if mode == "goals":
        write(package, "goals.json", {"goals": [goal()]})
        runner(package, mode)
        write(package, "goals.json", {"goals": [goal("done")]})
    else:
        write(package, "monitors.json", {mode: [{
            "id": "muted", "active": True, "requested": True, "summary": "Update",
            "matched": True, "change_id": "v1", "observed_at": NOW.isoformat(),
            "due_at": NOW.isoformat(),
        }]})
    sent = []

    def send(*args):
        sent.append(args)
        return {"settled": True, "decision": "declined"}

    result = runner(package, mode, now=NOW, send=send)
    assert result["suppressed"] == "declined"
    assert not result["notified"]
    assert not runner(package, mode, now=NOW, send=send)["notified"]
    assert len(sent) == 1


def test_long_digest_fits_notify_without_truncating_owner_file(package, runner):
    write(package, "goals.json", {"goals": [goal()]})
    runner(package, "goals")
    write(package, "goals.json", {"goals": [goal("done", next_step="a" * 9000)]})
    original = (package / "starter/goals.json").read_bytes()
    sent = []

    def send(title, body):
        sent.append(body)
        return {"request_id": "bounded"}

    assert runner(package, "goals", send=send)["notified"]
    assert len(sent[0]) <= 8000
    assert sent[0].endswith("full details in starter/goals.json.]")
    assert (package / "starter/goals.json").read_bytes() == original
