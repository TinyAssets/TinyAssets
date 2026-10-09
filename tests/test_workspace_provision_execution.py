"""Provisioning is retired: an admitted request refuses, spawns nothing, charges nothing."""
from tinyassets import workspace_provision_execution as execution


def test_admitted_provision_refuses_without_spawning(monkeypatch):
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("retired provisioning started a process")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = execution.execute_provision(
        object(), lease_fd=3, repo_fd=4, max_transfer_bytes=1, storage_bound=1,
        timeout_s=1, cancelled=lambda: False,
    )
    assert result == execution.ProvisionResult(execution.RETIRED, 0)
