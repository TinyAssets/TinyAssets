"""Staged owner tools fail closed before any daemon-side subprocess."""
import io
import runpy
import socket
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets import role_decoder, role_tools, universe_tools

# These drive the real bounded launcher and cells; no double is installed.
pytestmark = pytest.mark.role_split


@pytest.mark.parametrize('payload', [b'', b'{"secret": "owner-data"}', b'x' * 65])
@pytest.mark.parametrize('reason', ['decoder:role_decoder.py:35:ValueError:errno=None',
                                  'signal=31', 'bwrap:bootstrap-refused:errno=13'])
def test_tool_framing_failure_keeps_completion_without_payload(payload, reason):
    from tinyassets.broker.owner_identities import OwnerIdentity
    from tinyassets.owner_launcher_client import OwnerCell

    stream, peer = socket.socketpair()
    status, mapper = socket.socketpair()
    reply = dict(op='SPAWN_DONE', returncode=1, uid=300001, gid=300001, stop_reason=reason)
    cell = OwnerCell(SimpleNamespace(_reply=lambda **kwargs: reply), stream, status,
                     OwnerIdentity(300001, 300001))
    with peer, mapper, pytest.raises(universe_tools.UniverseToolError) as caught:
        with role_tools._tool_cell(SimpleNamespace(start_cell=lambda **kwargs: cell)):
            role_tools._read(io.BytesIO(payload), 64)
    assert reason in str(caught.value)
    assert 'owner-data' not in str(caught.value)
    assert cell._closed and cell.stop_reason == reason


def test_tool_launcher_refusal_is_a_tool_error():
    from tinyassets.owner_launcher_client import OwnerLaunchRefused

    def refused(**kwargs):
        raise OwnerLaunchRefused('owner launcher: mapper:library:1:ValueError:errno=None')

    with pytest.raises(universe_tools.UniverseToolError, match='owner launcher: mapper:'):
        with role_tools._tool_cell(SimpleNamespace(start_cell=refused)):
            pytest.fail('refused cell ran')


def test_outer_tool_cell_preserves_nested_maintenance_refusal():
    closed = []
    def waiting(*args):
        pytest.fail('outer jail is awaiting a budget verdict, not exiting')

    cell = SimpleNamespace(wait=waiting, close=lambda: closed.append(True))
    refusal = universe_tools.UniverseToolError(
        'tool cell failed (owner cell: decoder:role_tool_files.py:35:PermissionError:errno=13)')
    with pytest.raises(universe_tools.UniverseToolError) as caught:
        with role_tools._tool_cell(SimpleNamespace(start_cell=lambda **kwargs: cell)):
            raise refusal
    assert caught.value is refusal and closed == [True]


def test_deployed_diagnostic_filename_is_canonical_and_secret_free():
    from tinyassets.cell_diagnostics import PATTERN, failure_reason

    try:
        exec(compile("raise ValueError('private-data')", '/usr/local/libexec/ta-decoder.py',
                     'exec'))
    except ValueError as exc:
        reason = failure_reason(exc, 'decoder')
    assert reason == 'decoder:role_decoder.py:1:ValueError:errno=None'
    assert PATTERN.fullmatch(('TA_CELL_FAILURE ' + reason + '\n').encode())


@pytest.mark.parametrize('failure', [BrokenPipeError('private-data'),
                                   ConnectionResetError('private-data'),
                                   TimeoutError('private-data')])
def test_tool_transport_failure_and_failed_cleanup_preserve_safe_error(failure):
    def unavailable(*args):
        raise TimeoutError('private-completion-data')

    cell = SimpleNamespace(wait=unavailable, close=unavailable)
    with pytest.raises(universe_tools.UniverseToolError) as caught:
        with role_tools._tool_cell(SimpleNamespace(start_cell=lambda **kwargs: cell)):
            raise failure
    assert type(failure).__name__ in str(caught.value)
    assert 'completion-unavailable' in str(caught.value)
    assert 'private' not in str(caught.value) + str(caught.value.__notes__)
    assert 'cleanup failed' in caught.value.__notes__[0]


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
    image_cap = universe_tools.MAX_IMAGE_SOURCE_BYTES
    role_tools._validate(dict(request, cap=image_cap,
                             limits={**request['limits'], 'output_bytes': image_cap}))
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
