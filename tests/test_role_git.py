"""Selected owner git refuses unsupported launches without a daemon fallback."""
import pytest

from tinyassets import role_decoder, role_git, workspace_git

# These drive the real bounded launcher and cells; no double is installed.
pytestmark = pytest.mark.role_split


def test_selected_git_refusal_never_runs_in_daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(role_decoder, '_bounded_client', object())

    def refuse(*args, **kwargs):
        raise PermissionError('scope refused')

    def forbidden(*args, **kwargs):
        pytest.fail('git escaped the selected owner launcher')

    monkeypatch.setattr(role_git, 'run', refuse)
    monkeypatch.setattr(workspace_git, '_default_launcher', forbidden)
    with pytest.raises(PermissionError, match='scope refused'):
        workspace_git.run_git(['status'], cwd=tmp_path, home_dir=tmp_path,
                              path='/usr/bin:/bin', timeout_s=5)


def test_selected_git_rejects_injected_host_launcher(tmp_path, monkeypatch):
    monkeypatch.setattr(role_decoder, '_bounded_client', object())

    def forbidden(*args, **kwargs):
        pytest.fail('selected git accepted a host launcher')

    with pytest.raises(workspace_git.WorkspaceGitError, match='not admitted'):
        workspace_git.run_git(['status'], cwd=tmp_path, home_dir=tmp_path,
                              path='/usr/bin:/bin', timeout_s=5, launcher=forbidden)


@pytest.mark.parametrize('operation', ['git', 'gh'])
def test_bridge_selected_never_falls_back_to_host(tmp_path, monkeypatch, operation):
    from tinyassets import git_bridge

    monkeypatch.setattr(role_decoder, '_bounded_client', object())
    def refuse(*args, **kwargs):
        raise RuntimeError('scope refused')
    def forbidden(*args, **kwargs):
        pytest.fail('bridge escaped owner launcher')
    monkeypatch.setattr(role_git, 'run', refuse)
    monkeypatch.setattr(git_bridge.subprocess, 'run', forbidden)
    result = git_bridge._run([operation, 'status'], capture_output=True, text=True,
                             check=False, timeout=5, cwd=str(tmp_path))
    assert result.returncode == 126
    assert result.stderr == 'owner git bridge refused'


def test_bridge_cache_cannot_skip_owner_admission(tmp_path, monkeypatch):
    from tinyassets import git_bridge

    monkeypatch.setattr(role_decoder, '_bounded_client', object())
    monkeypatch.setattr(git_bridge, '_ENABLED_CACHE', True)
    monkeypatch.setattr(git_bridge.shutil, 'which', lambda name: '/usr/bin/git')
    def refuse(*args, **kwargs):
        raise PermissionError('foreign owner')
    monkeypatch.setattr(role_git, 'run', refuse)
    assert not git_bridge.is_enabled(tmp_path)


def test_bridge_outside_data_probe_is_disabled(tmp_path, monkeypatch):
    from tinyassets import git_bridge

    monkeypatch.setattr(role_decoder, '_bounded_client', object())
    monkeypatch.setenv('TINYASSETS_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(git_bridge.shutil, 'which', lambda name: '/usr/bin/git')
    assert not git_bridge.is_enabled(tmp_path)
