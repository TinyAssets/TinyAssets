"""Cell authority follows the broker's live binding, never actor prefixes alone."""
import os
import socket

import pytest

from tests.test_broker_server import broker  # noqa: F401
from tinyassets import role_scope, rpc_frames, storage
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.broker import owner_identities

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='real Unix broker')


@pytest.fixture
def scope(broker, tmp_path, monkeypatch):  # noqa: F811
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    identities = owner_identities.OwnerIdentities(private / 'owners.db', initialize=True)
    for owner, center in [('alice', 'a'), ('bob', 'b')]:
        identities.resolve(owner, allocate=True)
        identities.admission('admit', owner, center)
        (tmp_path / center).mkdir()
    broker.server._owner_identities = identities
    monkeypatch.setattr(storage, 'data_dir', lambda: tmp_path)

    def request(root, doc):
        doc = dict(generation=broker.state['generation'], token=broker.state['token']) | doc
        with socket.socket(socket.AF_UNIX) as channel:
            channel.settimeout(5)
            channel.connect(str(broker.path))
            channel.sendall(rpc_frames.control(rpc_frames.CONNECTION, doc))
            return rpc_frames.read_frame_blocking(channel).control()

    monkeypatch.setattr(owner_identities, '_owner_request', request)
    return tmp_path, identities, broker


@pytest.mark.parametrize('actor', ['alice', 'universe:a', 'command_center:a'])
def test_owner_background_and_automation_use_same_admission(scope, actor):
    root, _, _ = scope
    with identity_context(Identity(actor, actor)):
        assert role_scope.owner_principal(root / 'a') == 'alice'


@pytest.mark.parametrize('actor', ['bob', 'universe:b', 'command_center:b',
                                  'universe:a:suffix', 'command_center:', '', 'admin'])
def test_foreign_and_arbitrary_actors_refused(scope, actor):
    root, _, _ = scope
    with identity_context(Identity(actor, actor)), pytest.raises(PermissionError):
        role_scope.owner_principal(root / 'a')


def test_retired_unadmitted_and_unbound_fail_closed(scope):
    root, identities, server = scope
    with pytest.raises(PermissionError):
        role_scope.owner_principal(root / 'a')
    identities.admission('retire', 'alice', 'a')
    for center in ('a', 'unknown'):
        with pytest.raises(PermissionError):
            role_scope.owner_principal(root / center, actor='alice')
    server.server._owner_identities = None
    with pytest.raises(PermissionError):
        role_scope.owner_principal(root / 'b', actor='bob')


def test_alias_and_path_escape_refused(scope):
    root, _, _ = scope
    (root / 'alias').symlink_to(root / 'a', target_is_directory=True)
    for path in (root / 'alias', root / 'a' / '..' / 'a', root.parent / 'a'):
        with pytest.raises(PermissionError):
            role_scope.owner_principal(path, actor='alice')


@pytest.mark.parametrize('changes', [{'token': 'stale'}, {'generation': True},
    {'principal': 'alice'}, {'center': '../a'}, {'uid': 300001}])
def test_broker_lookup_rejects_stale_fence_and_authority_overrides(scope, changes):
    root, _, _ = scope
    answer = owner_identities._owner_request(
        root, {'op': 'ADMITTED_OWNER', 'center': 'a'} | changes)
    assert answer == {'op': 'ADMITTED_OWNER_REFUSED'}
