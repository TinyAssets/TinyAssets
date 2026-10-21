from __future__ import annotations

import importlib
from datetime import datetime, timezone
from pathlib import Path

from tinyassets.provider_work_authority import (
    ProviderWorkBindingSeed,
)
from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger
from tinyassets.storage.provider_work_authority import (
    SQLiteProviderWorkAuthorityStore,
)


def _authority(ledger):
    from tinyassets.broker.connection_authority import BrokerConnectionAuthority

    return BrokerConnectionAuthority(ledger._data_root, 'universe_alice',
                                     ledger.require_authenticated_principal_id)


def _automation():
    return importlib.import_module("tinyassets.user_owned_cloud_automation")


_DEFAULT_ACTION_CAP = object()


def _definition_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "principal_id": "acct_alice",
        "universe_id": "universe_alice",
        "repository": "example/project",
        "accepted_spec_ref": "openspec/specs/example/spec.md",
        "accepted_spec_digest": f"sha256:{'a' * 64}",
        "branch_def_id": "branch_repo_spec_loop",
        "branch_version_id": "branch_repo_spec_loop@abc12345",
        "branch_content_digest": f"sha256:{'b' * 64}",
        "acceptance_scenario_id": "scenario:repo-spec-baseline-v1",
        "acceptance_scenario_digest": f"sha256:{'c' * 64}",
        "input_artifact_digests": [
            f"sha256:{'a' * 64}",
            f"sha256:{'b' * 64}",
        ],
        "provider_binding_id": "provider_binding_alice",
        "destination_grant_id": "destination_grant_project",
        "destination_purpose": "pull_request",
        "max_attempts": 2,
        "max_provider_invocations": 4,
        "max_wall_time_seconds": 3600,
        "max_tokens": 100_000,
        "max_cost_microunits": 5_000_000,
    }




def _cloud_authority_fixture(
    tmp_path: Path,
    *,
    provider_overrides: dict[str, object] | None = None,
    connection_scopes: tuple[str, ...] = (
        "pull_requests:write",
        "pull_requests:read_for_commit",
    ),
    action_cap: ActionCap | None | object = _DEFAULT_ACTION_CAP,
) -> tuple[
    object,
    SQLiteProviderWorkAuthorityStore,
    ConnectionLedger,
]:
    def clock() -> datetime:
        return datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)

    provider_store = SQLiteProviderWorkAuthorityStore(
        tmp_path,
        clock=clock,
        allow_test_fixtures=True,
    )
    seed_values: dict[str, object] = {
        "owner_user_id": "acct_alice",
        "universe_id": "universe_alice",
        "provider": "codex",
        "credential_reference_digest": f"sha256:{'9' * 64}",
        "allowed_operations": ("repository_spec_delivery",),
        "allowed_roles": ("writer",),
        "assignment_generation": 2,
        "assignment_digest": f"sha256:{'8' * 64}",
        "max_invocations": 4,
        "max_tokens": 100_000,
        "max_cost_microunits": 5_000_000,
        "expires_at": "2026-08-02T00:00:00Z",
    }
    seed_values.update(provider_overrides or {})
    seed = ProviderWorkBindingSeed(**seed_values)  # type: ignore[arg-type]

    binding = provider_store.install_test_binding(seed).record
    assert binding is not None
    payload = _definition_payload()
    payload["provider_binding_id"] = binding.binding_id
    definition = _automation().RepositorySpecWorkDefinition.from_dict(payload)
    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: "acct_alice",
    )
    ledger.create_connection(
        connection_id="conn_tinyassets",
        owner_user_id="acct_alice",
        connection_class="pull-request-writer",
        scopes=connection_scopes,
        provider="github",
        destination="github.com/example/project",
        credential_ref="vault://github/example-project",
    )
    ledger.grant_connection(
        grant_id="destination_grant_project",
        connection_id="conn_tinyassets",
        owner_user_id="acct_alice",

        granted_at=1.0,
        unprompted_action_cap=(
            action_cap
            if action_cap is not _DEFAULT_ACTION_CAP
            else ActionCap("one_pull_request", 1, "pull_requests")
        ),  # type: ignore[arg-type]
    )
    return definition, provider_store, ledger






































