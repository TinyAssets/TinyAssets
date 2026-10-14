"""Platform health follows the process it started, not old heartbeat files."""

import json
import threading
import time

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets.control_plane import tick as runtime

pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture
def coordinators(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    made = []

    def start(root=tmp_path):
        entered = threading.Event()
        release = threading.Event()
        consumer = runtime.AssignedQueueConsumer(root, poll_seconds=0.01)
        monkeypatch.setattr(consumer, "_scavenge_orphaned_credentials", lambda: None)

        def poll():
            entered.set()
            release.wait(10)
            return 0

        monkeypatch.setattr(consumer, "poll_once", poll)
        made.append((consumer, release))
        consumer.start()
        assert entered.wait(2)
        return consumer, release

    yield start
    for consumer, release in made:
        release.set()
        consumer.stop()


def test_missing_enabled_executor_is_unhealthy_even_with_recent_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    (tmp_path / ".worker_supervisor.json").write_text(json.dumps({"alive": True}))
    assert runtime.current_consumer_liveness(tmp_path) == {
        "present": True, "alive": False, "beat_age_s": None,
        "phase": "not_started", "consec_crashes": 0,
    }


def test_consumer_off_still_starts_the_owner_tick(tmp_path, monkeypatch):
    monkeypatch.delenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", raising=False)
    consumer = runtime.AssignedQueueConsumer(tmp_path, poll_seconds=0.01)
    monkeypatch.setattr(consumer, "poll_once", lambda: 0)
    consumer.start()
    try:
        assert consumer._thread is not None and consumer._thread.is_alive()
        snapshot = runtime.current_consumer_liveness(tmp_path)
        assert snapshot["present"] is True
        assert snapshot["alive"] is True
        assert snapshot["phase"] == "starting"
    finally:
        consumer.stop()


def test_disabled_without_a_tick_stays_explicit(tmp_path, monkeypatch):
    monkeypatch.delenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", raising=False)
    assert runtime.current_consumer_liveness(tmp_path) == {
        "present": False, "phase": "disabled"}


def test_engine_child_reports_observation_boundary_not_missing_executor(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    monkeypatch.setenv("TINYASSETS_ENGINE_GRAPH_ID", "private-universe")
    snapshot = runtime.current_consumer_liveness(tmp_path)
    assert snapshot["alive"] is None
    assert snapshot["phase"] == "external_coordinator"
    assert "private-universe" not in json.dumps(snapshot)


def test_current_coordinator_ignores_stale_legacy_and_old_named_beats(
    tmp_path, coordinators,
):
    for filename in (".worker_supervisor.json", ".worker_supervisor.old.json"):
        (tmp_path / filename).write_text('{"ts":"2000-01-01T00:00:00Z"}')
    coordinators()
    snapshot = runtime.current_consumer_liveness(tmp_path)
    assert snapshot["alive"] is True
    assert snapshot["phase"] == "starting"
    assert snapshot.keys() == {
        "present", "alive", "beat_age_s", "phase", "consec_crashes"}


def test_stalled_current_poll_cannot_be_hidden_by_a_fresh_peer(tmp_path, coordinators):
    stale, _ = coordinators()
    stale._started_monotonic = time.monotonic() - 901
    coordinators()
    snapshot = runtime.current_consumer_liveness(tmp_path)
    assert snapshot["alive"] is False
    assert snapshot["phase"] == "stalled"
    assert snapshot["beat_age_s"] >= 900


def test_stopped_instance_is_retired_without_hiding_replacement(tmp_path, coordinators):
    previous, release = coordinators()
    release.set()
    previous.stop()
    assert runtime.current_consumer_liveness(tmp_path)["phase"] == "not_started"
    coordinators()
    assert runtime.current_consumer_liveness(tmp_path)["alive"] is True
    assert previous._thread is not None and not previous._thread.is_alive()


def test_stop_timeout_keeps_unexited_coordinator_visible(tmp_path, coordinators):
    consumer, _ = coordinators()
    consumer.stop(timeout=0)
    snapshot = runtime.current_consumer_liveness(tmp_path)
    assert snapshot["alive"] is False
    assert snapshot["phase"] == "stopping"


def test_other_data_root_does_not_supply_this_daemons_liveness(tmp_path, coordinators):
    coordinators(tmp_path / "other-root")
    assert runtime.current_consumer_liveness(tmp_path)["phase"] == "not_started"


def test_successful_poll_stamps_progress_and_errors_do_not(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    consumer = runtime.AssignedQueueConsumer(tmp_path, poll_seconds=0.001)
    outcomes = iter([None, RuntimeError("probe failure")])

    def poll():
        result = next(outcomes)
        if result:
            consumer._stop.set()
            raise result

    monkeypatch.setattr(consumer, "poll_once", poll)
    try:
        consumer._run()
        assert consumer._last_poll_completed is not None
        assert consumer._last_poll_failed is True
    finally:
        consumer.stop()


def test_unexpected_coordinator_exit_is_not_hidden(tmp_path, coordinators):
    consumer, release = coordinators()
    release.set()
    consumer._stop.set()
    consumer._thread.join(timeout=2)
    # Unlike stop(), an unexpected coordinator exit has not retired ownership.
    consumer._stop.clear()
    snapshot = runtime.current_consumer_liveness(tmp_path)
    assert snapshot["alive"] is False
    assert snapshot["phase"] == "stopped"


def test_platform_projection_allowlists_and_keeps_unavailable_visible(monkeypatch):
    from tinyassets.api import status

    monkeypatch.setattr(runtime, "current_consumer_liveness", lambda root: {
        "present": True, "alive": True, "phase": "polling", "beat_age_s": 1,
        "consec_crashes": 0, "universe_id": "private", "worker_id": "private",
    })
    assert set(status._platform_worker_liveness()) == {
        "present", "alive", "beat_age_s", "phase", "consec_crashes"}

    def failed(root):
        raise OSError("observation unavailable")

    monkeypatch.setattr(runtime, "current_consumer_liveness", failed)
    result = status._platform_worker_liveness()
    assert result["present"] is True and result["alive"] is None
    assert result["phase"] == "unavailable"
