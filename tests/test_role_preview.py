"""Selected previews cannot fall back to an unrestricted daemon subprocess."""
import contextlib

import pytest

from tinyassets import role_decoder, role_preview, ui_preview

# These drive the real bounded launcher and cells; no double is installed.
pytestmark = pytest.mark.role_split


def test_selected_preview_refusal_never_runs_in_daemon(monkeypatch):
    monkeypatch.setattr(role_decoder, '_bounded_client', object())
    monkeypatch.setattr(ui_preview, '_host_slot', contextlib.nullcontext)

    def refuse(*args):
        raise PermissionError('scope refused')

    def forbidden(*args):
        pytest.fail('preview escaped the selected owner launcher')

    monkeypatch.setattr(role_preview, 'render', refuse)
    monkeypatch.setattr('subprocess.Popen', forbidden)
    with pytest.raises(PermissionError, match='scope refused'):
        ui_preview._run_child({}, 60)


def test_preview_without_a_bounded_launcher_refuses_rather_than_rendering(monkeypatch):
    """No launcher is not "render here": the cell is the only renderer."""
    monkeypatch.setattr(role_decoder, '_bounded_client', None)
    monkeypatch.setattr(ui_preview, '_host_slot', contextlib.nullcontext)

    def forbidden(*args):
        pytest.fail('preview escaped without a bounded launcher')

    monkeypatch.setattr('subprocess.Popen', forbidden)
    spec = {'base_path': '/absent', 'owner_user_id': 'alice',
            'universe_id': 'alice', 'hashes': {}}
    with pytest.raises(Exception, match='bounded preview launcher|not admitted'):
        role_preview.render(spec, 60)


@pytest.mark.parametrize('wall', [0, -1, 61, float('nan'), float('inf'), True])
def test_preview_deadline_refuses_before_launch(wall):
    from tinyassets.owner_launcher_client import OwnerLauncherClient

    client = object.__new__(OwnerLauncherClient)
    with pytest.raises(ValueError, match='deadline'):
        client.preview({}, wall, principal='alice', command_center='alice', identity=None)


def test_selected_preview_write_refusal_never_uses_daemon_writer(monkeypatch, tmp_path):
    from tinyassets import universe_files

    monkeypatch.setattr(role_decoder, '_bounded_client', object())

    def refuse(*args):
        raise PermissionError('scope refused')

    def forbidden(*args, **kwargs):
        pytest.fail('preview output escaped the owner identity')

    monkeypatch.setattr(role_preview, 'write', refuse)
    monkeypatch.setattr(universe_files, 'write_universe_file', forbidden)
    with pytest.raises(ui_preview.PreviewUnavailable, match='scope refused'):
        ui_preview.write_preview(tmp_path, 'preview', b'png')
