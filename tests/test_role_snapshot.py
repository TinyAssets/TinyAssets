"""Dedicated snapshot custody refuses before any credential material is read."""

import pytest

from tinyassets import credential_vault as vault
from tinyassets import role_decoder, role_snapshot
from tinyassets.broker.owner_identities import OwnerIdentity

# These drive the real bounded launcher and cells; no double is installed.
pytestmark = pytest.mark.role_split


def test_selected_snapshot_refuses_unmigrated_center(tmp_path, monkeypatch):
    monkeypatch.setattr(role_decoder, '_bounded_client', object())
    with pytest.raises((PermissionError, NotImplementedError)):
        role_snapshot.owner_uid(tmp_path)


def test_snapshot_foreign_custody_refuses_before_material_read(tmp_path, monkeypatch):
    from tinyassets.broker import owner_identities

    center = tmp_path / 'alice'
    center.mkdir()
    monkeypatch.setattr(role_snapshot, 'owner_uid', lambda path: 300001)
    monkeypatch.setattr(owner_identities, 'owner_identity',
                        lambda *args, **kwargs: OwnerIdentity(300002, 300002))
    def forbidden(*args, **kwargs):
        pytest.fail('foreign custody reached credential material')
    monkeypatch.setattr(vault, '_usable_subscription_record', forbidden)
    from types import SimpleNamespace

    custody = SimpleNamespace(universe_id='alice', service='codex', owner_user_id='bob')
    with pytest.raises(PermissionError, match='dedicated owner'):
        vault.snapshot_llm_subscription_credential(universe_dir=center, custody=custody)


def test_snapshot_broker_unavailable_never_uses_shared_group(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tinyassets.broker import owner_identities

    monkeypatch.setattr(role_snapshot, 'owner_uid', lambda path: 300001)
    def unavailable(*args, **kwargs):
        raise RuntimeError('broker unavailable')
    monkeypatch.setattr(owner_identities, 'owner_identity', unavailable)
    custody = SimpleNamespace(universe_id=tmp_path.name, service='codex', owner_user_id='alice')
    with pytest.raises(RuntimeError, match='broker unavailable'):
        vault.snapshot_llm_subscription_credential(universe_dir=tmp_path, custody=custody)
