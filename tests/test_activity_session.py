"""Branchless activity admission retains durable identity and cancellation fences."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tinyassets import agent_activities as activities
from tinyassets import runs
from tinyassets.activity_runner import start
from tinyassets.execution_subject import ExecutionSubject, ExecutionSubjectKind
from tinyassets.foreground_run_provider import _ForegroundRunProviderSession
from tinyassets.provider_work_authority import _validate_work_lineage


@pytest.fixture
def claimed(tmp_path, monkeypatch):
    from tinyassets.api import permissions
    from tinyassets.daemon_server import initialize_author_server

    initialize_author_server(tmp_path)
    root = tmp_path / "home"
    root.mkdir()
    record = activities.create(
        root, owner_principal="owner", title="T", brief="B", origin_kind="ask")
    generation = activities.claim(root, record["activity_id"], replaceable=lambda _: False)

    @contextmanager
    def identity(*args):
        yield True

    monkeypatch.setattr(permissions, "owner_run_identity", identity)
    return root, record, generation


def test_binding_commits_before_submit_and_never_creates_branch(claimed, monkeypatch):
    root, record, generation = claimed
    submitted = []

    def submit(*args):
        bound = activities.get(root, record["activity_id"])
        row = runs.get_run(root.parent, bound["runner_token"])
        assert row["status"] == "queued" and row["branch_def_id"] == ""
        assert row["owner_user_id"] == "owner" and row["queue_universe_id"] == "home"
        submitted.append(args)
        return object()

    monkeypatch.setattr(runs, "_get_executor", lambda **kwargs: SimpleNamespace(submit=submit))
    monkeypatch.setattr(runs, "_track_future", lambda *args: None)
    assert start(root.parent, root.name, record, generation)
    assert len(submitted) == 1
    with pytest.raises(activities.ActivityRefused, match="claim changed"):
        start(root.parent, root.name, record, generation)
    assert len(submitted) == 1


def test_stale_claim_is_refused_before_run_creation(claimed, monkeypatch):
    root, record, generation = claimed
    monkeypatch.setattr(runs, "create_run", lambda *a, **kw: pytest.fail("stale claim created run"))
    with pytest.raises(activities.ActivityRefused, match="claim changed"):
        start(root.parent, root.name, record, generation + 1)


def test_lost_binding_is_cancelled_without_submission(claimed, monkeypatch):
    root, record, generation = claimed
    monkeypatch.setattr(activities, "bind_run", lambda *args: False)
    monkeypatch.setattr(runs, "_get_executor", lambda **kw: pytest.fail("lost claim dispatched"))
    run_id = start(root.parent, root.name, record, generation)
    assert runs.get_run(root.parent, run_id)["status"] == "cancelled"


def test_submission_failure_settles_bound_run(claimed, monkeypatch):
    root, record, generation = claimed

    def unavailable(**kwargs):
        raise RuntimeError("executor unavailable")

    monkeypatch.setattr(runs, "_get_executor", unavailable)
    with pytest.raises(RuntimeError, match="executor unavailable"):
        start(root.parent, root.name, record, generation)
    bound = activities.get(root, record["activity_id"])
    assert runs.get_run(root.parent, bound["runner_token"])["status"] == "failed"


def test_provider_subject_is_bound_to_current_activity_run(claimed, monkeypatch):
    root, record, generation = claimed
    run_id = runs.create_run(root.parent, branch_def_id="", thread_id="activity", inputs={},
                             actor="universe:home", owner_user_id="owner", queue_universe_id="home")
    activities.bind_run(root, record["activity_id"], generation, run_id)
    runs.update_run_status(root.parent, run_id, status="running")
    provider = object.__new__(_ForegroundRunProviderSession)
    provider._base_path, provider._universe_dir = root.parent, root
    provider._universe_id, provider._principal_id = "home", "owner"
    provider._run_id = provider._branch_def_id = provider._branch_version_id = ""
    provider._activity_binding = provider._activity_subject = None
    provider._request_budget = SimpleNamespace(link=lambda *args: None)
    monkeypatch.setattr(provider, "_validate_founder_home", lambda: None)
    frozen = provider.prepare_activity(run_id=run_id, activity_id=record["activity_id"],
                                       generation=generation)
    assert frozen["runner_token"] == run_id
    assert provider._activity_subject.kind is ExecutionSubjectKind.AGENT_ACTIVITY
    assert provider._activity_subject.ref == f"{record['activity_id']}:{generation}:{run_id}"
    assert provider._branch_def_id == provider._branch_version_id == ""
    with pytest.raises(PermissionError, match="already bound"):
        provider.prepare_activity(run_id=run_id, activity_id=record["activity_id"],
                                  generation=generation)
    runs.request_cancel(root.parent, run_id)
    with pytest.raises(PermissionError, match="state or immutable subject"):
        provider._validate_run(allowed_statuses={"running"})


@pytest.mark.parametrize("field,value", [
    ("branch_def_id", "branch"), ("branch_version_id", "version"),
    ("work_item_kind", "background_attempt"), ("agent_invocation_generation", 1),
])
def test_activity_subject_cannot_inherit_other_execution_authority(field, value):
    fields = dict(work_item_kind="run", branch_def_id=None, branch_version_id=None,
                  agent_invocation_command_id=None, agent_invocation_command_digest=None,
                  agent_invocation_generation=None,
                  execution_subject=ExecutionSubject(ExecutionSubjectKind.AGENT_ACTIVITY,
                      "act_" + "a" * 16 + ":1:" + "b" * 16, "sha256:" + "c" * 64))
    _validate_work_lineage(**fields)
    fields[field] = value
    with pytest.raises(ValueError):
        _validate_work_lineage(**fields)


def test_owner_loss_refuses_before_creating_a_run(claimed, monkeypatch):
    from tinyassets.api import permissions

    root, record, generation = claimed

    @contextmanager
    def lost_owner(*args):
        yield False

    monkeypatch.setattr(permissions, "owner_run_identity", lost_owner)
    monkeypatch.setattr(runs, "create_run", lambda *a, **kw: pytest.fail("owner lost"))
    with pytest.raises(activities.ActivityRefused, match="owner lost"):
        start(root.parent, root.name, record, generation)


def test_explicit_run_cancel_fences_activity_without_replacing_generation(claimed):
    from tinyassets.activity_runner import ActivityRunBinding

    root, record, generation = claimed
    run_id = runs.create_run(root.parent, branch_def_id="", thread_id="activity", inputs={},
                             actor="universe:home", owner_user_id="owner", queue_universe_id="home")
    activities.bind_run(root, record["activity_id"], generation, run_id)
    binding = ActivityRunBinding(root, record["activity_id"], generation, run_id)
    binding.check()
    runs.request_cancel(root.parent, run_id)
    with pytest.raises(PermissionError, match="activity run cancelled"):
        binding.check()
    assert activities.holds(root, record["activity_id"], generation, run_id=run_id)
