"""Exact package revision, custody, admission and narrowed connection slots."""
import hashlib
import json
import os
import runpy
from pathlib import Path

import pytest

from tinyassets import role_packages, workspace_fs
from tinyassets.role_package_manifest import parse

# These drive the real bounded launcher and cells; no double is installed.
pytestmark = pytest.mark.role_split


def manifest(**changes):
    doc = dict(runtime='python', entry='main.py', args=[], slots=[],
               files={'main.py': hashlib.sha256(b'print(1)\n').hexdigest()})
    raw = json.dumps(doc | changes).encode()
    return raw, hashlib.sha256(raw).hexdigest()


def test_manifest_exact_revision_and_entry():
    raw, revision = manifest()
    assert parse(raw, revision)['entry'] == 'main.py'
    with pytest.raises(ValueError, match='revision'):
        parse(raw + b' ', revision)


@pytest.mark.parametrize('change', [
    {'entry': '../main.py'}, {'runtime': 'vendor-name'}, {'args': ['bad\0arg']},
    {'slots': ['x', 'x']}, {'files': {'../escape': '0' * 64}},
    {'files': {'manifest.json': '0' * 64}}, {'files': {'main.py': 'not-a-digest'}},
    {'egress': 'yes'}, {'egress': 1},
])
def test_manifest_rejects_unpinned_or_unsafe_configuration(change):
    with pytest.raises(ValueError):
        parse(*manifest(**change))


def test_manifest_egress_is_an_optional_exact_revision_opt_in():
    assert parse(*manifest())['egress'] is False
    assert parse(*manifest(egress=True))['egress'] is True
    # The opt-in is part of the hashed bytes: a different flag is a different revision.
    assert manifest(egress=True)[1] != manifest(egress=False)[1]


def test_sealed_tree_rejects_changed_unlisted_linked_or_foreign_content(tmp_path):
    raw, revision = manifest()
    (tmp_path / 'manifest.json').write_bytes(raw)
    content = tmp_path / 'main.py'
    content.write_bytes(b'print(1)\n')
    tmp_path.chmod(0o750)
    content.chmod(0o640)
    descriptor = workspace_fs.open_dir_nofollow(tmp_path)
    try:
        doc = parse(workspace_fs.read_package_manifest(
            descriptor, owner_uid=os.getuid(), max_bytes=1024), revision)

        def verify():
            workspace_fs.verify_package_tree(descriptor, doc['files'],
                                             owner_uid=os.getuid(), max_bytes=100)

        verify()
        with pytest.raises(workspace_fs.UnsafePoolPath, match='custody'):
            workspace_fs.verify_package_tree(descriptor, doc['files'],
                                             owner_uid=os.getuid() + 1, max_bytes=100)
        content.write_bytes(b'changed!\n')
        with pytest.raises(workspace_fs.UnsafePoolPath, match='revision'):
            verify()
        content.write_bytes(b'print(1)\n')
        extra = tmp_path / 'unlisted.py'
        extra.write_bytes(b'print(2)')
        with pytest.raises(workspace_fs.UnsafePoolPath, match='unmanifested'):
            verify()
        extra.unlink()
        os.link(content, extra)
        with pytest.raises(workspace_fs.UnsafePoolPath, match='aliased'):
            verify()
        extra.unlink()
        content.chmod(0o666)
        with pytest.raises(workspace_fs.UnsafePoolPath, match='custody'):
            verify()
    finally:
        os.close(descriptor)


def test_slot_dispatch_cannot_select_or_expand_authority():
    class Capabilities:
        called = []
        current = {'connection:one:GET': ('grant', 'view', 'GET')}

        def connections(self):
            return self.current

        async def dispatch(self, message):
            self.called.append(message)
            return {'result': 'broker response'}

    caps = Capabilities()
    dispatch = role_packages._slot_dispatch(caps, {'read': 'connection:one:GET'})
    assert dispatch({'slot': 'read', 'request': {}}) == {'result': 'broker response'}
    for bad in ({'slot': 'foreign', 'request': {}},
                {'slot': 'read', 'request': {}, 'principal': 'bob'}):
        assert 'error' in dispatch(bad)
    caps.current = {'connection:one:GET': ('replacement-grant', 'view', 'GET')}
    assert 'error' in dispatch({'slot': 'read', 'request': {}})
    assert len(caps.called) == 1


def test_package_mapper_rejects_caller_policy_or_bad_revision():
    scope = runpy.run_path(str(Path(__file__).resolve().parents[1]
                              / 'deploy/role_owner_launcher.py'))
    mapper = object.__new__(scope['OwnerLauncher'])
    request = dict(op='START', kind='package', principal='alice', command_center='alice',
                   revision='0' * 64, ta=False, egress=False)
    for changes in ({'uid': 300002}, {'profile': 'cell-nested'}, {'revision': '../other'},
                    {'egress': 'yes'}, {'egress': True}, {'op': 'SPAWN'},
                    {'argv': ['/bin/sh']}):
        with pytest.raises(ValueError, match='unsupported'):
            mapper._decoder(request | changes, [])
    with pytest.raises(ValueError, match='unsupported'):
        mapper._decoder({key: value for key, value in request.items() if key != 'egress'}, [])


def test_selected_package_refuses_without_launcher(monkeypatch, tmp_path):
    from tinyassets import role_decoder

    monkeypatch.setattr(role_decoder, '_bounded_client', None)
    with pytest.raises(PermissionError, match='bounded owner'):
        with role_packages.start(tmp_path, '0' * 64):
            pytest.fail('unconfined package started')


def test_mapper_counts_detached_descendants_outside_the_cell(tmp_path):
    scope = runpy.run_path(str(Path(__file__).resolve().parents[1]
                              / 'deploy/role_owner_launcher.py'))
    for pid, parent, pages in ((10, 1, 5), (11, 10, 7), (12, 11, 13), (99, 1, 1000000)):
        folder = tmp_path / str(pid)
        folder.mkdir()
        # stat state/ppid ... rss; session IDs do not affect ancestry.
        fields = ['S', str(parent), *(['0'] * 19), str(pages)]
        (folder / 'stat').write_text(str(pid) + ' (package child) ' + ' '.join(fields))
    assert scope['package_usage'](10, str(tmp_path)) == (3, 25 * os.sysconf('SC_PAGE_SIZE'))
    with pytest.raises(RuntimeError, match='unmeasurable'):
        scope['package_usage'](123, str(tmp_path))
