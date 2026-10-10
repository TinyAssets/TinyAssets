"""Provisioning must refuse before any cell or registry relay starts."""
import pytest

from tinyassets import workspace_provision_execution as execution
from tinyassets.workspace_provision import admit_node
from tinyassets.workspace_resolver import ProvisionManifests


def plans():
    return ProvisionManifests(None, admit_node(
        '{"name":"empty","version":"1.0.0"}',
        '{"name":"empty","version":"1.0.0","lockfileVersion":3,"packages":{}}'))


def test_unadmitted_manifests_never_start_a_process(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail('unadmitted provisioning started a process')
    monkeypatch.setattr('subprocess.Popen', forbidden)
    with pytest.raises(ValueError, match='admitted manifests'):
        execution.execute_provision(
            object(), lease_fd=3, repo_fd=4, max_transfer_bytes=1, storage_bound=1,
            timeout_s=1, cancelled=lambda: False, universe_dir=tmp_path, principal='alice')


@pytest.mark.parametrize('timeout', [0, -1, True, float('nan'), float('inf'), 1801])
def test_invalid_deadline_refuses_before_admission(monkeypatch, tmp_path, timeout):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid deadline reached owner admission')
    monkeypatch.setattr('tinyassets.role_remote_git._scope', forbidden)
    with pytest.raises(ValueError, match='deadline'):
        execution.execute_provision(
            plans(), lease_fd=3, repo_fd=4, max_transfer_bytes=1, storage_bound=1,
            timeout_s=timeout, cancelled=lambda: False, universe_dir=tmp_path, principal='alice')
