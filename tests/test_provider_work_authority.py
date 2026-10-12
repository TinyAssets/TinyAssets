from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone

import pytest

from tinyassets.provider_work_authority import (
    ProviderWorkAuthorityWriteOutcome,
    ProviderWorkBindingFence,
    ProviderWorkBindingRoot,
    ProviderWorkBindingSeed,
    ProviderWorkBindingService,
    ProviderWorkBindingState,
    provider_work_binding_class,
)
from tinyassets.storage import db_path
from tinyassets.storage.provider_work_authority import (
    SQLiteProviderWorkAuthorityStore,
)

NOW = datetime(2026, 8, 1, 6, 0, tzinfo=timezone.utc)


def test_background_branch_run_has_distinct_binding_identity_class() -> None:
    assert (
        provider_work_binding_class(
            allowed_operations=("background_branch_run",),
            allowed_roles=("writer", "judge"),
        )
        == "background_branch_run"
    )
    assert (
        provider_work_binding_class(
            allowed_operations=("converse",),
            allowed_roles=("writer",),
        )
        == "serving"
    )


def _seed(**overrides: object) -> ProviderWorkBindingSeed:
    values: dict[str, object] = {
        "owner_user_id": "acct_alice",
        "universe_id": "universe_alice",
        "provider": "codex",
        "credential_reference_digest": f"sha256:{'a' * 64}",
        "allowed_operations": ("repository_spec_delivery",),
        "allowed_roles": ("writer", "reviewer"),
        "assignment_generation": 3,
        "assignment_digest": f"sha256:{'b' * 64}",
        "max_invocations": 4,
        "max_tokens": 100_000,
        "max_cost_microunits": 5_000_000,
        "expires_at": "2026-08-02T00:00:00Z",
    }
    values.update(overrides)
    return ProviderWorkBindingSeed(**values)  # type: ignore[arg-type]


def _install(
    tmp_path,
    seed: ProviderWorkBindingSeed | None = None,
) -> tuple[SQLiteProviderWorkAuthorityStore, object]:
    store = SQLiteProviderWorkAuthorityStore(
        tmp_path,
        clock=lambda: NOW,
        allow_test_fixtures=True,
    )
    result = store.install_test_binding(_seed() if seed is None else seed)
    return store, result


def test_binding_is_secret_free_immutable_and_content_addressed(tmp_path) -> None:
    _store, created = _install(tmp_path)

    assert created.outcome is ProviderWorkAuthorityWriteOutcome.APPLIED
    binding = created.record
    assert binding is not None
    assert binding.binding_id.startswith("pwb_")
    assert binding.binding_digest.startswith("sha256:")
    assert binding.state is ProviderWorkBindingState.ACTIVE
    assert "credential" not in binding.to_dict()
    assert binding.to_dict()["credential_reference_digest"] == f"sha256:{'a' * 64}"
    with pytest.raises(FrozenInstanceError):
        binding.provider = "claude"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("credential_reference_digest", "secret-token"),
        ("assignment_digest", "not-a-digest"),
        ("allowed_operations", ()),
        ("allowed_roles", ()),
        ("assignment_generation", 0),
        ("max_invocations", 0),
        ("max_tokens", -1),
        ("max_cost_microunits", -1),
    ],
)
def test_binding_seed_rejects_unbounded_or_noncanonical_authority(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError):
        _seed(**{field: value})


def test_same_fixture_binding_replays_across_restart(tmp_path) -> None:
    first = _install(tmp_path)[1]
    second = _install(tmp_path)[1]

    assert first.record is not None
    assert second.outcome is ProviderWorkAuthorityWriteOutcome.REPLAYED
    assert second.record == first.record


def test_changed_assignment_conflicts_instead_of_silently_rebinding(tmp_path) -> None:
    original = _install(tmp_path)[1]

    changed = _install(
        tmp_path,
        _seed(
            assignment_generation=4,
            assignment_digest=f"sha256:{'c' * 64}",
        ),
    )[1]

    assert original.record is not None
    assert changed.outcome is ProviderWorkAuthorityWriteOutcome.CONFLICT
    assert changed.record == original.record


def test_concurrent_create_has_one_record_and_only_replays(tmp_path) -> None:
    def create_once(_index: int):
        return _install(tmp_path)[1]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create_once, range(16)))

    assert (
        sum(result.outcome is ProviderWorkAuthorityWriteOutcome.APPLIED for result in results) == 1
    )
    assert all(
        result.outcome
        in {
            ProviderWorkAuthorityWriteOutcome.APPLIED,
            ProviderWorkAuthorityWriteOutcome.REPLAYED,
        }
        for result in results
    )
    assert len({result.record.binding_digest for result in results if result.record}) == 1


def test_exact_current_binding_validation_fails_closed_after_revoke(tmp_path) -> None:
    store, created = _install(tmp_path)
    service = ProviderWorkBindingService(store)
    binding = created.record
    assert binding is not None

    with store.connection() as conn:
        assert store.validate_in_transaction(
            conn,
            binding_id=binding.binding_id,
            binding_generation=binding.generation,
            binding_digest=binding.binding_digest,
            owner_user_id="acct_alice",
            universe_id="universe_alice",
            provider="codex",
            operation="repository_spec_delivery",
            role="writer",
        )
        assert not store.validate_in_transaction(
            conn,
            binding_id=binding.binding_id,
            binding_generation=binding.generation,
            binding_digest=binding.binding_digest,
            owner_user_id="acct_other",
            universe_id="universe_alice",
            provider="codex",
            operation="repository_spec_delivery",
            role="writer",
        )

    revoked = service.revoke(ProviderWorkBindingFence(binding))
    assert revoked.outcome is ProviderWorkAuthorityWriteOutcome.APPLIED
    assert revoked.record is not None
    assert revoked.record.state is ProviderWorkBindingState.REVOKED
    assert revoked.record.generation == binding.generation + 1
    assert revoked.record.revocation_generation == binding.revocation_generation + 1

    with store.connection() as conn:
        assert not store.validate_in_transaction(
            conn,
            binding_id=binding.binding_id,
            binding_generation=binding.generation,
            binding_digest=binding.binding_digest,
            owner_user_id="acct_alice",
            universe_id="universe_alice",
            provider="codex",
            operation="repository_spec_delivery",
            role="writer",
        )


def test_revoke_cas_has_one_winner(tmp_path) -> None:
    store, created = _install(tmp_path)
    binding = created.record
    assert binding is not None

    def revoke_once(_index: int):
        return ProviderWorkBindingService(
            SQLiteProviderWorkAuthorityStore(tmp_path),
        ).revoke(ProviderWorkBindingFence(binding))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(revoke_once, range(8)))

    assert (
        sum(result.outcome is ProviderWorkAuthorityWriteOutcome.APPLIED for result in results) == 1
    )
    assert all(result.record is not None for result in results)
    assert all(
        result.record.state is ProviderWorkBindingState.REVOKED
        for result in results
        if result.record
    )


def test_tampered_persisted_record_is_rejected_on_read(tmp_path) -> None:
    store, created = _install(tmp_path)
    binding = created.record
    assert binding is not None

    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE provider_work_bindings SET record_json = ? WHERE binding_id = ?",
            ("{}", binding.binding_id),
        )

    with pytest.raises(ValueError, match="persisted provider binding"):
        store.get(binding.binding_id)


def test_binding_state_cannot_be_forged_by_replace(tmp_path) -> None:
    _store, created = _install(tmp_path)
    binding = created.record
    assert binding is not None

    with pytest.raises(ValueError, match="revoked binding"):
        replace(binding, state=ProviderWorkBindingState.REVOKED)


def test_production_store_has_no_binding_installation_path(tmp_path) -> None:
    store = SQLiteProviderWorkAuthorityStore(tmp_path)

    with pytest.raises(PermissionError, match="test fixtures are disabled"):
        store.install_test_binding(_seed())
    assert not hasattr(ProviderWorkBindingService(store), "create")
    assert not hasattr(store, "issue_universe_receipt")
    with store.transaction() as transaction:
        assert not hasattr(transaction, "issue_universe_receipt")
        assert not hasattr(transaction, "claim_receipt")
        assert not hasattr(transaction, "reserve_invocation")


class _BindingResolver:
    def __init__(self, seed: ProviderWorkBindingSeed | None) -> None:
        self.seed = seed
        self.roots: list[ProviderWorkBindingRoot] = []

    def resolve(
        self,
        root: ProviderWorkBindingRoot,
    ) -> ProviderWorkBindingSeed | None:
        self.roots.append(root)
        return self.seed


def test_production_binding_issuance_uses_only_trusted_resolved_assignment(
    tmp_path,
) -> None:
    root = ProviderWorkBindingRoot(
        owner_user_id="acct_alice",
        universe_id="universe_alice",
        provider="codex",
    )
    resolver = _BindingResolver(_seed())
    store = SQLiteProviderWorkAuthorityStore(tmp_path, clock=lambda: NOW)

    created = ProviderWorkBindingService(store, resolver).issue(root)
    replayed = ProviderWorkBindingService(
        SQLiteProviderWorkAuthorityStore(tmp_path, clock=lambda: NOW),
        resolver,
    ).issue(root)

    assert created.outcome is ProviderWorkAuthorityWriteOutcome.APPLIED
    assert replayed.outcome is ProviderWorkAuthorityWriteOutcome.REPLAYED
    assert created.record == replayed.record
    assert resolver.roots == [root, root]
    assert store.get(created.record.binding_id) == created.record
    assert not hasattr(store, "issue_binding")


def test_rebind_recovers_revoked_binding_and_applies_new_budget(tmp_path) -> None:
    root = ProviderWorkBindingRoot(
        owner_user_id="acct_alice",
        universe_id="universe_alice",
        provider="codex",
    )
    original_seed = _seed()
    store = SQLiteProviderWorkAuthorityStore(tmp_path, clock=lambda: NOW)
    service = ProviderWorkBindingService(store, _BindingResolver(original_seed))
    created = service.issue(root)
    assert created.record is not None
    revoked = service.revoke(ProviderWorkBindingFence(created.record))
    assert revoked.record is not None

    updated_seed = replace(
        original_seed,
        assignment_generation=2,
        assignment_digest=f"sha256:{'b' * 64}",
        max_cost_microunits=64,
    )
    rebound = ProviderWorkBindingService(
        store,
        _BindingResolver(updated_seed),
    ).rebind(ProviderWorkBindingFence(revoked.record), root)

    assert rebound.outcome is ProviderWorkAuthorityWriteOutcome.APPLIED
    assert rebound.record is not None
    assert rebound.record.binding_id == created.record.binding_id
    assert rebound.record.generation == revoked.record.generation + 1
    assert rebound.record.state is ProviderWorkBindingState.ACTIVE
    assert rebound.record.max_cost_microunits == 64


@pytest.mark.parametrize(
    "seed",
    [
        None,
        _seed(owner_user_id="acct_mallory"),
        _seed(universe_id="universe_other"),
        _seed(provider="claude-code"),
    ],
)
def test_production_binding_issuance_rejects_missing_or_cross_root_resolution(
    tmp_path,
    seed: ProviderWorkBindingSeed | None,
) -> None:
    root = ProviderWorkBindingRoot(
        owner_user_id="acct_alice",
        universe_id="universe_alice",
        provider="codex",
    )

    with pytest.raises(PermissionError, match="provider assignment"):
        ProviderWorkBindingService(
            SQLiteProviderWorkAuthorityStore(tmp_path, clock=lambda: NOW),
            _BindingResolver(seed),
        ).issue(root)


def test_production_binding_issuance_requires_a_resolver(tmp_path) -> None:
    root = ProviderWorkBindingRoot(
        owner_user_id="acct_alice",
        universe_id="universe_alice",
        provider="codex",
    )

    with pytest.raises(PermissionError, match="provider assignment"):
        ProviderWorkBindingService(
            SQLiteProviderWorkAuthorityStore(tmp_path, clock=lambda: NOW),
        ).issue(root)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner_user_id", "acct_mallory"),
        ("allowed_roles", ("writer", "admin")),
    ],
)
def test_store_rejects_each_coherent_identity_or_scope_transfer(
    tmp_path,
    field: str,
    value: object,
) -> None:
    store, created = _install(tmp_path)
    binding = created.record
    assert binding is not None
    forged = replace(
        binding,
        generation=binding.generation + 1,
        binding_digest=f"sha256:{'0' * 64}",
        state=ProviderWorkBindingState.REVOKED,
        revocation_generation=binding.revocation_generation + 1,
        updated_at="2026-08-01T00:00:00Z",
        **{field: value},
    )
    forged = replace(forged, binding_digest=forged.expected_digest())

    with pytest.raises(ValueError, match="immutable|transition"):
        with store.transaction() as transaction:
            transaction.compare_and_swap(
                ProviderWorkBindingFence(binding),
                forged,
            )


