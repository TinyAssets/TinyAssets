"""Selected owner git refuses unsupported launches without a daemon fallback."""
import pytest

from tinyassets import role_decoder, role_git, workspace_git


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
