"""Actual grant application joins cloud admission and the service reset barrier."""
import pytest

from tests.test_command_center_release_policy import enable
from tests.test_command_center_update_executor import published as published
from tests.test_command_center_update_executor import release, ui
from tinyassets import command_center_update_maintenance as maintenance


def test_unadmitted_tick_cannot_reach_a_writer_or_create_storage(tmp_path, monkeypatch):
    from tinyassets import platform_runtime_provenance, scoped_reset

    def denied(**kwargs):
        raise PermissionError("synthetic admission refusal")

    def forbidden(*args, **kwargs):
        pytest.fail("unadmitted maintenance reached a writer")

    monkeypatch.setattr(platform_runtime_provenance, "require_process_cloud_admission", denied)
    monkeypatch.setattr(scoped_reset, "prepare_service_writer_barrier", forbidden)
    base = tmp_path / "not-created"
    result = maintenance.tick(base)
    assert result["state"] == "blocked" and result["reason"] == "PermissionError"
    assert not base.exists()


def test_actual_reset_barrier_blocks_then_permits_opted_in_data_update(published, monkeypatch):
    from tests.test_command_center_packages import _as
    from tinyassets import command_center_update_executor as executor
    from tinyassets.api import command_center_updates, permissions
    from tinyassets.providers.router import ProviderRouter
    from tinyassets.scoped_reset import acquire_maintenance_barrier

    enable(published)
    target = release(published, style="main { color: teal; }")
    before = ui(published)

    def forbidden(*args, **kwargs):
        pytest.fail("maintenance impersonated an owner, accepted consent or invoked a provider")

    monkeypatch.setattr(permissions, "owner_run_identity", forbidden)
    monkeypatch.setattr(command_center_updates, "commit_update", forbidden)
    monkeypatch.setattr(ProviderRouter, "call_sync", forbidden)
    barrier = acquire_maintenance_barrier(published["base"], exclusive=True)
    try:
        assert maintenance.tick(published["base"])["state"] == "blocked"
        assert ui(published) == before
    finally:
        barrier.release()
    assert maintenance.tick(published["base"])["state"] == "ready"
    after = ui(published)
    assert after["revision"] == before["revision"] + 1
    with _as(published["owner"]):
        result = executor.inspect_status(universe_id=published["uid"],
                                         adoption_id=published["adoption"]["adoption_id"])
    assert result["applied"] and result["to_release_id"] == target["release_id"]
    assert maintenance.tick(published["base"])["state"] == "ready"
    assert ui(published) == after
    exclusive = acquire_maintenance_barrier(published["base"], exclusive=True)
    exclusive.release()  # the successful tick released its shared lease too


def test_failed_sweep_releases_barrier_and_reports_sanitized_unavailability(published, monkeypatch):
    from tinyassets import command_center_update_executor as executor
    from tinyassets.scoped_reset import acquire_maintenance_barrier

    def broken(*args, **kwargs):
        raise OSError("private account path must not reach the owner UI")

    monkeypatch.setattr(executor, "settle_pending", broken)
    result = maintenance.tick(published["base"])
    assert result["state"] == "blocked" and result["reason"] == "OSError"
    assert "private" not in str(result)
    exclusive = acquire_maintenance_barrier(published["base"], exclusive=True)
    exclusive.release()


def test_worker_availability_does_not_claim_an_unstarted_loop(tmp_path):
    assert maintenance.status(tmp_path)["state"] == "waiting_for_service"
    maintenance.scheduled(tmp_path)
    assert maintenance.status(tmp_path) == {"state": "scheduled", "last_checked_at": None}
    maintenance.unavailable(tmp_path)
    assert maintenance.status(tmp_path)["state"] == "unavailable"
