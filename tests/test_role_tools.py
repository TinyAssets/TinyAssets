"""Staged owner tools fail closed before any daemon-side subprocess."""
import runpy
from dataclasses import asdict
from pathlib import Path

import pytest

from tinyassets import role_decoder, role_tools, universe_tools

# These drive the real bounded launcher and cells; no double is installed.
pytestmark = pytest.mark.role_split


def test_selected_tool_requires_bounded_launcher(monkeypatch, tmp_path):
    monkeypatch.setattr(role_decoder, '_bounded_client', None)
    def forbidden(*args, **kwargs):
        pytest.fail('selected tool attempted a daemon subprocess')
    monkeypatch.setattr(universe_tools.subprocess, 'Popen', forbidden)
    with pytest.raises(universe_tools.UniverseToolError, match='bounded owner launcher'):
        universe_tools.run_jailed(tmp_path, ['/bin/true'], agent_id='main')


def test_selected_tool_refuses_unadmitted_sockets_before_owner_resolution(monkeypatch, tmp_path):
    monkeypatch.setattr(role_decoder, '_bounded_client', object())
    for socket_kind in ('ta_socket', 'egress_socket'):
        with pytest.raises(universe_tools.UniverseToolError, match='socket forwarding'):
            universe_tools.run_jailed(tmp_path, ['/bin/true'], agent_id='main',
                                     **{socket_kind: tmp_path / 'socket'})


def test_tool_limits_do_not_lower_disk_floors_or_increase_resource_ceilings():
    request = dict(inner=['/bin/true'], agent_id='main', stdin=None,
                   limits=asdict(universe_tools.DEFAULT_LIMITS), wall=600, cap=65536)
    role_tools._validate(request)
    for key, value in [('min_free_disk_bytes', 1), ('memory_bytes', 2**40)]:
        invalid = dict(request, limits={**request['limits'], key: value})
        with pytest.raises(ValueError):
            role_tools._validate(invalid)
    for wall in (float('nan'), 601, -1, True):
        with pytest.raises(ValueError):
            role_tools._validate(dict(request, wall=wall))


def test_tool_mapper_refuses_profile_and_numeric_identity_before_descriptor_use():
    module = runpy.run_path(str(Path(__file__).resolve().parents[1]
                               / 'deploy/role_owner_launcher.py'))
    launcher = object.__new__(module['OwnerLauncher'])
    for override in ({'profile': 'cell-nested'}, {'uid': 300002}, {'executable': '/bin/sh'}):
        with pytest.raises(ValueError, match='unsupported owner engine'):
            launcher._decoder(dict(op='START', kind='tool-jail', principal='alice',
                                   command_center='alice', **override), [])


def test_image_read_preserves_its_existing_source_allowance(monkeypatch, tmp_path):
    seen = []
    def runner(root, inner, *, agent_id, limits):
        request = dict(inner=inner, agent_id=agent_id, stdin=None,
                       limits=asdict(limits), wall=limits.wall_seconds, cap=limits.output_bytes)
        role_tools._validate(request)
        seen.append(limits.output_bytes)
        return universe_tools.ToolRun(exit_code=1, output=b'missing fixture',
                                      killed=None, elapsed=0)
    monkeypatch.setattr(universe_tools, 'RUNNER', runner)
    assert 'missing fixture' in universe_tools.read_file(tmp_path, 'image.png', agent_id='main')
    assert seen == [universe_tools.MAX_IMAGE_SOURCE_BYTES]


def test_prepared_cell_view_never_requests_daemon_preparation(monkeypatch, tmp_path):
    workspace = tmp_path / universe_tools.WORKSPACE_DIR
    workspace.mkdir()
    (workspace / 'identity.md').write_text('new brain', encoding='utf-8')

    def forbidden(*args, **kwargs):
        pytest.fail('prepared owner cell requested daemon workspace authority')

    monkeypatch.setattr(role_tools, 'prepare', forbidden)
    view = universe_tools._universe_view(tmp_path, agent_id='main',
        workspace_prepared=True, promote_brain_files=False)
    assert view.mounts[0].source == workspace
    assert not (tmp_path / 'identity.md').exists()
    assert not (tmp_path / 'skills').exists()


@pytest.mark.parametrize('kind', ['missing', 'file'])
def test_prepared_cell_view_requires_existing_directory(tmp_path, kind):
    if kind == 'file':
        (tmp_path / universe_tools.WORKSPACE_DIR).write_text('invalid', encoding='utf-8')
    with pytest.raises(universe_tools.UniverseToolError, match='prepared agent workspace'):
        universe_tools._universe_view(tmp_path, agent_id='main',
            workspace_prepared=True, promote_brain_files=False)
