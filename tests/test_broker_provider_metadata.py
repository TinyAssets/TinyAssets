"""Broker metadata stays readable without listing or following owner content."""
import json
import os

import pytest

from tinyassets.broker.provider_metadata import read_definitions

pytestmark = pytest.mark.skipif(os.name != 'posix',
                               reason='runs-in=linux-oracle: POSIX descriptors')


def test_traversal_only_center(tmp_path):
    center = tmp_path / 'center'
    center.mkdir()
    rows = [{'id': 'metadata'}]
    (center / 'provider_definitions.json').write_text(json.dumps(rows))
    center.chmod(0o111)
    try:
        with pytest.raises(PermissionError):
            os.listdir(center)
        assert read_definitions(tmp_path, 'center') == rows
    finally:
        center.chmod(0o700)


@pytest.mark.parametrize('attack', ['center-link', 'leaf-link', 'hardlink', 'writable', 'fifo'])
def test_metadata_refuses_aliases_and_untrusted_inodes(tmp_path, attack):
    center = tmp_path / 'center'
    center.mkdir()
    leaf = center / 'provider_definitions.json'
    original = tmp_path / 'original'
    original.write_text('[]')
    if attack == 'center-link':
        (center / 'provider_definitions.json').write_text('[]')
        (tmp_path / 'alias').symlink_to(center)
    elif attack == 'leaf-link':
        leaf.symlink_to(original)
    elif attack == 'hardlink':
        os.link(original, leaf)
    elif attack == 'writable':
        leaf.write_text('[]')
        leaf.chmod(0o666)
    else:
        os.mkfifo(leaf)
    with pytest.raises(OSError):
        read_definitions(tmp_path, 'alias' if attack == 'center-link' else 'center')


@pytest.mark.parametrize('value', [{}, [None], ['not a definition']])
def test_malformed_definition_container_fails_closed(tmp_path, value):
    center = tmp_path / 'center'
    center.mkdir()
    (center / 'provider_definitions.json').write_text(json.dumps(value))
    with pytest.raises(ValueError, match='list of definitions'):
        read_definitions(tmp_path, 'center')
