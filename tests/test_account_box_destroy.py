"""Account deletion consumes C6's opaque destruction-only handle and durable receipt."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from tests.test_account_deletion import HOME_A, HOME_B, A, B, _rows, two_users  # noqa: F401
from tinyassets import account_deletion as deletion
from tinyassets.agent_loop import served_chat
from tinyassets.api.first_contact import principal_is_deleted
from tinyassets.boxes.provider import DestroyReceipt
from tinyassets.daemon_server import get_founder_home

REAL_DESTROY = deletion._destroy_owner_box


class AuthenticatedDestroy:
    """Protocol double: opaque handles, tombstone admission and durable idempotence."""
    def __init__(self, root):
        self.root = root
        self.handles = {}
        self.receipts = {}
        self.operations = []
        self.lock = threading.Lock()
        self.lose_response = False
        self.removals = 0

    def bind(self, *args, **kwargs):
        pytest.fail('ordinary bind must never be used for deletion')

    def bind_for_destroy(self, center, *, account_id):
        assert principal_is_deleted(self.root, account_id)
        assert get_founder_home(self.root, account_id) == center
        handle = object()
        self.handles[handle] = (center, account_id)
        return handle

    def destroy(self, handle, op_id):
        from tests.support.admission_double import _remove

        with self.lock:
            center, owner = self.handles[handle]
            self.operations.append(op_id)
            if op_id not in self.receipts:
                self.removals += _remove(self.root / center)
                self.receipts[op_id] = DestroyReceipt(center, op_id, self.removals, 2)
                if self.lose_response:
                    self.lose_response = False
                    raise OSError('response lost after durable destruction')
            return self.receipts[op_id]


@pytest.fixture
def bound(two_users, monkeypatch):  # noqa: F811
    provider = AuthenticatedDestroy(two_users)
    monkeypatch.setattr(served_chat, 'configured_box_provider', lambda: (provider, None))
    monkeypatch.setattr(deletion, '_destroy_owner_box', REAL_DESTROY)
    return two_users, provider


def test_unknown_destroy_outcome_preserves_binding_but_erases_broker_and_retries(bound):
    root, provider = bound
    provider.lose_response = True
    identities = []
    def erase():
        return deletion.delete_account(root, founder_sub=A, cancel_billing=lambda _: 'none',
                                       delete_identity=lambda owner:
                                       identities.append(owner) or 'deleted')
    first = erase()
    assert {'owner_tree', 'root_rows', 'identity'} <= set(first['unfinished_phases'])
    assert first['home_deletion_pending'] and not first['home_removed']
    assert get_founder_home(root, A) == HOME_A and identities == []
    assert not (root / HOME_A).exists()  # outcome was uncertain, not "no effect"
    assert _rows(root / 'outbound.db', 'SELECT owner_user_id FROM outbound_connections') == [(B,)]
    second = erase()
    assert second['unfinished_phases'] == [] and second['home_removed']
    assert second['box_destroy']['op_id'] == provider.operations[0]
    assert len(provider.operations) == 2 and len(set(provider.operations)) == 1
    assert provider.removals == 1 and identities == [A]
    assert not get_founder_home(root, A)
    assert get_founder_home(root, B) == HOME_B and (root / HOME_B / 'soul.md').exists()


def test_concurrent_destroy_requests_share_stable_id_and_durable_outcome(bound):
    root, provider = bound
    deletion.write_tombstone(root, A)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: REAL_DESTROY(root, HOME_A, principal=A), range(8)))
    assert len(set(provider.operations)) == 1 and provider.removals == 1
    assert all(result == results[0] for result in results)
    assert get_founder_home(root, A) == HOME_A  # only delete_account retires the binding
    assert (root / HOME_B / 'soul.md').exists()


@pytest.mark.parametrize('change', [
    {'command_center_id': HOME_B}, {'op_id': 'unrelated'}, {'files_removed': -1},
    {'files_removed': True}, {'new_epoch': 0}, {'new_epoch': True},
])
def test_invalid_durable_receipt_retains_binding_and_identity(bound, monkeypatch, change):
    root, provider = bound
    def invalid(handle, op_id):
        return replace(DestroyReceipt(HOME_A, op_id, 0, 2), **change)
    monkeypatch.setattr(provider, 'destroy', invalid)
    result = deletion.delete_account(root, founder_sub=A, cancel_billing=lambda _: 'none',
                                      delete_identity=lambda _: pytest.fail('identity retired'))
    assert 'owner_tree' in result['unfinished_phases'] and not result['home_removed']
    assert get_founder_home(root, A) == HOME_A
    assert (root / HOME_A / 'soul.md').exists() and (root / HOME_B / 'soul.md').exists()


@pytest.mark.parametrize('provider', [None, object()])
def test_missing_authenticated_destroy_driver_has_no_legacy_fallback(
    two_users, monkeypatch, provider,  # noqa: F811
):
    monkeypatch.setattr(served_chat, 'configured_box_provider', lambda: (provider, None))
    deletion.write_tombstone(two_users, A)
    with pytest.raises(deletion.AccountDeletionError, match='unavailable'):
        REAL_DESTROY(two_users, HOME_A, principal=A)
    assert (two_users / HOME_A / 'soul.md').exists()
    assert (two_users / HOME_B / 'soul.md').exists()
