"""Exact relay admission does not export a sidecar or a foreign socket."""
import os
import struct

import pytest

from tinyassets import role_modes, role_relays
from tinyassets.broker.owner_identities import OwnerIdentity
from tinyassets.owner_launcher_client import OwnerLauncherClient
from tinyassets.ta_capabilities import JailBridge


def test_other_cell_classes_cannot_receive_tool_socket_descriptors():
    client = object.__new__(OwnerLauncherClient)
    with pytest.raises(ValueError, match='unsupported cell sockets'):
        client.start_cell(kind='image-decoder', principal='alice', command_center='alice',
                          identity=OwnerIdentity(300001, 300001), socket_fds=(9,))


@pytest.mark.skipif(os.name != 'posix', reason='Linux socket ACL descriptors')
def test_pinned_socket_acl_names_only_the_owner_and_preserves_inode(tmp_path, monkeypatch):
    monkeypatch.setenv('TINYASSETS_CREDENTIAL_BROKER', 'process')
    monkeypatch.setattr(role_modes, 'WORK_GID', os.getgid())
    center = tmp_path / 'alice'
    center.mkdir()
    with JailBridge(lambda value: value, universe_dir=center) as bridge:
        before = bridge.path.stat()
        fd = role_relays.pin_for_owner(bridge.path, center, 300001, kind='ta')
        try:
            assert (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == (before.st_dev, before.st_ino)
            acl = os.getxattr(f'/proc/self/fd/{fd}', 'system.posix_acl_access')
            entries = list(struct.iter_unpack('<HHI', acl[4:]))
            assert (2, 6, 300001) in entries
            assert [(tag, perm) for tag, perm, _ in entries if tag in (4, 32)] == [(4, 0), (32, 0)]
            with pytest.raises(PermissionError, match='owner scope'):
                role_relays.pin_for_owner(bridge.path, tmp_path / 'bob', 300002, kind='ta')
            assert os.getxattr(f'/proc/self/fd/{fd}', 'system.posix_acl_access') == acl
            parent_acl = os.getxattr(bridge.path.parent, 'system.posix_acl_access')
            parent_entries = list(struct.iter_unpack('<HHI', parent_acl[4:]))
            assert (2, 1, 300001) in parent_entries
            assert [(tag, perm) for tag, perm, _ in parent_entries
                    if tag in (4, 32)] == [(4, 0), (32, 0)]
        finally:
            os.close(fd)
        path = bridge.path
    assert not path.exists()


@pytest.mark.skipif(os.name != 'posix', reason='Linux relay socket descriptors')
def test_replaced_capability_socket_is_not_unlinked_on_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv('TINYASSETS_CREDENTIAL_BROKER', 'process')
    monkeypatch.setattr(role_modes, 'WORK_GID', os.getgid())
    center = tmp_path / 'alice'
    center.mkdir()
    bridge = JailBridge(lambda value: value, universe_dir=center).__enter__()
    original = bridge.path
    original.unlink()
    original.write_bytes(b'preserve-replacement')
    with pytest.raises(PermissionError, match='changed before removal'):
        bridge.__exit__()
    assert original.read_bytes() == b'preserve-replacement'


def test_socket_paths_cannot_be_supplied_to_unscoped_capability_bridge(monkeypatch):
    monkeypatch.setenv('TINYASSETS_CREDENTIAL_BROKER', 'process')
    with pytest.raises(ValueError, match='command center'):
        with JailBridge(lambda value: value):
            pytest.fail('unscoped bridge started')
