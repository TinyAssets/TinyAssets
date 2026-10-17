"""Broker validation reads published descriptors, independent of owner inodes."""
import json
import os

import pytest

from tests.support.broker_ipc import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tests.test_broker_usage_source import source  # noqa: F401
from tinyassets.broker.owner_sources import publish, read
from tinyassets.storage.agent_request_usage import UsageStore

pytestmark = pytest.mark.skipif(os.name != 'posix',
                               reason='runs-in=linux-oracle: POSIX descriptors')


def test_published_source_needs_no_center_listing(source):  # noqa: F811
    center = source.root / 'cc-alice'
    center.mkdir()
    rows = read(source.ledger, 'cc-alice')
    (center / 'provider_definitions.json').write_text(json.dumps(rows))
    center.chmod(0o111)
    try:
        with pytest.raises(PermissionError):
            os.listdir(center)
        assert read(source.ledger, 'cc-alice') == rows
        UsageStore(source.root, broker_ledger=source.ledger)._validate_source(**source.arguments)
    finally:
        center.chmod(0o700)


@pytest.mark.parametrize('attack', ['center-link', 'leaf-link', 'hardlink', 'writable', 'fifo'])
def test_owner_inode_cannot_replace_published_source(source, attack):  # noqa: F811
    rows = read(source.ledger, 'cc-alice')
    center = source.root / 'cc-alice'
    outside = source.root / 'outside'
    outside.mkdir()
    original = outside / 'original'
    original.write_text('private owner content')
    if attack == 'center-link':
        center.symlink_to(outside, target_is_directory=True)
    else:
        center.mkdir()
    leaf = center / 'provider_definitions.json'
    if attack == 'leaf-link':
        leaf.symlink_to(original)
    elif attack == 'hardlink':
        os.link(original, leaf)
    elif attack == 'writable':
        leaf.write_text('invalid private definition')
        leaf.chmod(0o666)
    elif attack == 'fifo':
        os.mkfifo(leaf)
    else:
        leaf.write_text('invalid private definition')
    before = original.read_bytes(), original.stat()
    UsageStore(source.root, broker_ledger=source.ledger)._validate_source(**source.arguments)
    assert read(source.ledger, 'cc-alice') == rows
    assert original.read_bytes() == before[0]
    assert os.path.samestat(original.stat(), before[1])
    assert original.stat().st_mode == before[1].st_mode


@pytest.mark.parametrize('value', [{}, [None], ['not a definition']])
def test_malformed_publication_preserves_admitted_descriptor(source, value):  # noqa: F811
    rows = read(source.ledger, 'cc-alice')
    with pytest.raises((ValueError, TypeError)):
        publish(source.ledger, 'alice', 'cc-alice', value)
    assert read(source.ledger, 'cc-alice') == rows
    assert not (source.root / 'cc-alice').exists()
