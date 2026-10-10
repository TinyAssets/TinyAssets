"""Publication safety guards; the image oracle proves the owner UID handoff."""
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from tinyassets import role_content

pytestmark = pytest.mark.role_split


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo'])
def test_append_refuses_aliases_and_special_files_without_changing_bytes(tmp_path, kind):
    original = tmp_path / 'original'
    original.write_bytes(b'keep')
    target = tmp_path / 'target'
    if kind == 'symlink':
        target.symlink_to(original)
    elif kind == 'hardlink':
        os.link(original, target)
    else:
        os.mkfifo(target)
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError):
            role_content._append(parent, 'target', b'lost', os.getuid())
    finally:
        os.close(parent)
    assert original.read_bytes() == b'keep'


def test_concurrent_appends_preserve_every_record(tmp_path):
    (tmp_path / 'target').write_bytes(b'initial\n')
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        def append(index):
            role_content._append(parent, 'target', f'{index}\n'.encode(), os.getuid())
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(append, range(32)))
    finally:
        os.close(parent)
    lines = (tmp_path / 'target').read_bytes().splitlines()
    assert lines[0] == b'initial'
    assert sorted(int(line) for line in lines[1:]) == list(range(32))


@pytest.mark.parametrize('parts', [['.credentials', 'vault'], ['owner.json'],
                                  ['provider_definitions.json'],
                                  ['notes', '..', 'file'], ['notes/file'], ['']])
def test_publication_never_accepts_platform_or_escaping_paths(tmp_path, parts):
    with pytest.raises(ValueError, match='invalid owner content'):
        role_content.write(tmp_path, parts, b'data')
    assert list(tmp_path.iterdir()) == []


def test_directory_only_cell_outputs_have_no_dummy_file(tmp_path):
    import io

    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        role_content._create_outputs(root, io.BytesIO(b''), size=0, directories=2, file=False)
    finally:
        os.close(root)
    assert sorted(path.name for path in tmp_path.iterdir()) == ['dir-0', 'dir-1']
    assert all(path.is_dir() and list(path.iterdir()) == [] for path in tmp_path.iterdir())


def test_explicit_empty_file_is_distinct_from_directory_only_output(tmp_path):
    import io

    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        role_content._create_outputs(root, io.BytesIO(b''), size=0, directories=1, file=True)
    finally:
        os.close(root)
    assert (tmp_path / 'file').read_bytes() == b''
    assert (tmp_path / 'dir-0').is_dir()


# Mutation table: recursive removal loses nonempty/racing bytes; following links
# touches an unrelated tree; replacing an existing destination loses owner data.
@pytest.mark.parametrize('state', ['empty', 'nonempty', 'filled-during-cell', 'symlink',
                                  'owner-wins'])
def test_wiki_repair_only_removes_an_atomically_empty_directory(tmp_path, monkeypatch, state):
    from types import SimpleNamespace

    assert os.getuid() == 1001  # linux_oracle's real daemon identity
    center = tmp_path / 'center'
    parent = center / 'wiki' / 'drafts'
    parent.mkdir(parents=True)
    candidate = parent / 'category'
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'private').write_bytes(b'keep')
    if state == 'symlink':
        candidate.symlink_to(outside, target_is_directory=True)
    else:
        candidate.mkdir()
    if state == 'nonempty':
        (candidate / 'bytes').write_bytes(b'keep')
    before = candidate.lstat().st_ino
    def materialize(client, staging, **kwargs):
        os.mkdir('dir-0', 0o770, dir_fd=staging)
        if state == 'filled-during-cell':
            (candidate / 'bytes').write_bytes(b'keep')
    monkeypatch.setattr(role_content, '_materialize', materialize)
    if state == 'owner-wins':
        def publish(*args):
            candidate.mkdir()
            (candidate / 'bytes').write_bytes(b'owner replacement')
            raise FileExistsError
        monkeypatch.setattr(role_content.admission, '_renameat2', publish)
    descriptor = os.open(center, os.O_RDONLY | os.O_DIRECTORY)
    try:
        def repair():
            role_content.repair_empty_wiki_categories(None, center, descriptor,
                principal='owner', identity=SimpleNamespace(uid=1001, gid=1001))
        if state in ('nonempty', 'filled-during-cell'):
            with pytest.raises(OSError):
                repair()
            assert (candidate / 'bytes').read_bytes() == b'keep'
            assert candidate.stat().st_ino == before
        else:
            repair()
            if state == 'empty':
                assert candidate.stat().st_ino != before
                assert not list(candidate.iterdir())
            elif state == 'owner-wins':
                assert (candidate / 'bytes').read_bytes() == b'owner replacement'
            else:
                assert candidate.is_symlink() and candidate.lstat().st_ino == before
        assert (outside / 'private').read_bytes() == b'keep'
    finally:
        os.close(descriptor)
