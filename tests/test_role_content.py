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
