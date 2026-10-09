"""Selected owner nodes cannot fall back to daemon execution or foreign mounts."""
import os
from pathlib import Path

import pytest

from tinyassets import node_sandbox, role_node

# These drive the real bounded launcher and cells; no double is installed.
pytestmark = pytest.mark.role_split


def test_selected_node_requires_scope_before_any_local_spawn(monkeypatch):
    monkeypatch.setenv('TINYASSETS_CREDENTIAL_BROKER', 'process')
    def forbidden(*args, **kwargs):
        pytest.fail('selected owner node fell back to a daemon child')
    monkeypatch.setattr(node_sandbox.subprocess, 'Popen', forbidden)
    with pytest.raises(RuntimeError, match='bounded owner launcher'):
        node_sandbox.NodeSandbox().run_sync('node', 'def run(s): return s', {}, [], [])


def test_node_mount_refuses_unrelated_inherited_descriptors():
    mount = node_sandbox.WorkspaceMount('/proc/self/fd/4', pass_fds=(4, 5))
    with pytest.raises(ValueError, match='exactly'):
        role_node._workspace_fd(mount)


def test_owner_node_packet_rejects_profile_override_before_descriptor_use():
    import runpy

    module = runpy.run_path(str(Path(__file__).resolve().parents[1]
                               / 'deploy/role_owner_launcher.py'))
    launcher = object.__new__(module['OwnerLauncher'])
    with pytest.raises(ValueError, match='unsupported owner engine'):
        launcher._decoder(dict(op='START', kind='node-sandbox', principal='alice',
                               command_center='alice', workspace=False, profile='cell-deny'), [])


def test_workspace_fd_pins_original_directory_after_rename(tmp_path):
    directory = tmp_path / 'workspace'
    directory.mkdir()
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        mount = node_sandbox.WorkspaceMount(f'/proc/self/fd/{fd}', pass_fds=(fd,))
        pinned = role_node._workspace_fd(mount)
        try:
            directory.rename(tmp_path / 'moved')
            directory.mkdir()
            assert os.fstat(pinned).st_ino == os.fstat(fd).st_ino
            assert os.fstat(pinned).st_ino != directory.stat().st_ino
        finally:
            os.close(pinned)
    finally:
        os.close(fd)


def test_selected_compiler_workspace_never_probes_a_daemon_jail(tmp_path, monkeypatch):
    from tinyassets import graph_compiler
    from tinyassets.branches import NodeDefinition
    from tinyassets.effectors import EffectChain, WorkspaceMount

    # The daemon has no workspace launcher factory left to call: the nested
    # jail is resolved inside the cell, from the mount the cell opened.
    assert not hasattr(node_sandbox, 'WORKSPACE_LAUNCHER_FACTORY')
    seen = []
    def admitted(instance, **kwargs):
        seen.append((instance.universe_dir, instance.launcher, kwargs['workspace']))
        return node_sandbox.SandboxResult(node_id='build', success=True, output_state={'ok': True})
    monkeypatch.setattr(role_node, 'run', admitted)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    chain = EffectChain()
    chain.register_workspace('checkout', WorkspaceMount(
        'checkout', f'/proc/self/fd/{fd}', repo_fd=fd, lease_fd=os.dup(fd), pass_fds=(fd,)))
    try:
        node = NodeDefinition(node_id='build', display_name='Build', phase='draft',
                              source_code='def run(s): return {"ok":True}',
                              output_keys=['ok'], workspace='checkout')
        fn = graph_compiler._build_source_code_node(node, event_sink=None,
            base_path=tmp_path, effect_chain=chain, ancestors={'checkout'})
        assert fn({})['ok'] is True
        assert seen[0][0] == tmp_path and seen[0][1] is None
        assert seen[0][2].pass_fds
    finally:
        chain.close_workspaces()
