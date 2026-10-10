"""Executable pre-refactor authorization baseline at b3bb8956.

Kept for differential tests; not a production authority implementation.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from tinyassets.provider_assignment import (
    _SERVED_REQUEST_MAX_INVOCATIONS,
    ServedProviderAuthority,
    _is_open_provider,
    load_provider_assignment_in_transaction,
    provider_assignment_admission,
)


@contextmanager
def legacy_authorize_served_provider_call(
    base_path: str | Path,
    *,
    universe_dir: str | Path,
    request_carrier: object,
    role: str,
    operation: str,
) -> Iterator[ServedProviderAuthority]:
    """Fence selection + request + binding + custody immediately before launch."""

    from tinyassets.auth.middleware import validate_provider_request_carrier
    from tinyassets.credential_vault import (
        cleanup_llm_credential_snapshot,
        current_connection_grant_custody,
        current_llm_subscription_custody,
        snapshot_llm_subscription_credential,
    )
    from tinyassets.custom_agents import get_binding
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.storage.provider_work_authority import (
        SQLiteProviderWorkAuthorityStore,
    )

    held = (
        "Connect your provider before running this universe. TinyAssets will not "
        "borrow platform credentials or start a metered trial."
    )
    universe = Path(universe_dir)
    uid = universe.name
    carrier_uid = str(getattr(request_carrier, "universe_id", ""))
    carrier_binding_id = str(getattr(request_carrier, "agent_binding_id", ""))
    carrier_revision = getattr(request_carrier, "binding_revision", 0)
    if carrier_uid != uid or not carrier_binding_id:
        raise ProviderAuthorityHeldError(held)

    with provider_assignment_admission().shared(universe):
        authority: ServedProviderAuthority | None = None
        credential_snapshot = None
        try:
            capability = validate_provider_request_carrier(
                request_carrier,
                universe_id=uid,
                agent_binding_id=carrier_binding_id,
                binding_revision=carrier_revision,
                operation=operation,
            )
            accepted_request_sources = {
                (
                    "tinyassets.authenticated-request.v1",
                    "tinyassets.auth.middleware",
                    "converse",
                ),
                (
                    "tinyassets.authenticated-app-event.v1",
                    "tinyassets.app_ingress_http",
                    "slack_event",
                ),
            }
            if (
                capability.mechanism,
                capability.issuer,
                capability.tool_name,
            ) not in accepted_request_sources:
                raise PermissionError("provider request source is not trusted")
            if role != "writer" or operation != "converse":
                raise PermissionError("served authority is converse/writer only")
            agent = get_binding(
                base_path,
                universe_id=uid,
                binding_id=carrier_binding_id,
            )
            if agent is None:
                raise PermissionError("agent binding is missing")
            exact_agent = (
                agent["status"] == "serving",
                agent["created_by"] == capability.principal_id,
                int(agent["revision"]) == carrier_revision,
            )
            if not all(exact_agent):
                raise PermissionError("agent binding is not current serving authority")

            store = SQLiteProviderWorkAuthorityStore(base_path)
            with store.connection() as conn:
                conn.execute("BEGIN")
                assignment = load_provider_assignment_in_transaction(
                    conn,
                    universe_id=uid,
                )
                provider_ref = agent["configuration"].get("provider_ref")
                if (
                    assignment is None
                    or assignment.state != "ready"
                    or assignment.owner_user_id != capability.principal_id
                    or provider_ref != assignment.binding_id
                ):
                    raise PermissionError("provider assignment is not current")
                provider_binding = store.get_binding_in_transaction(
                    conn,
                    binding_id=assignment.binding_id,
                )
                if provider_binding is None or not store.validate_in_transaction(
                    conn,
                    binding_id=assignment.binding_id,
                    binding_generation=assignment.binding_generation,
                    binding_digest=assignment.binding_digest,
                    owner_user_id=capability.principal_id,
                    universe_id=uid,
                    provider=assignment.provider,
                    operation=operation,
                    role=role,
                ):
                    raise PermissionError("provider binding is not current")
                # Shared custody-identity check for BOTH variants (Codex: exact tuple).
                def _exact_custody(cust: object) -> bool:
                    return all((
                        cust is not None,
                        cust is not None
                        and cust.reference_id == assignment.credential_reference_id,
                        cust is not None
                        and cust.generation == assignment.credential_reference_generation,
                        cust is not None
                        and cust.reference_digest == assignment.credential_reference_digest,
                        provider_binding.assignment_generation == assignment.generation,
                        provider_binding.assignment_digest == assignment.assignment_digest,
                        provider_binding.credential_reference_digest
                        == assignment.credential_reference_digest,
                    ))

                if _is_open_provider(assignment.provider):
                    # connection_grant variant: no subscription snapshot/custody. Two
                    # independent gates (Codex reject #1): (a) the custody row must match
                    # the assignment's exact digests (_exact_custody), AND (b) the LIVE
                    # grant — resolved fresh under the AUTHENTICATED CALLER as owner —
                    # must still be owned + bound + not-revoked + not-rotated
                    # (verify_open_grant_custody recomputes the live grant-identity digest
                    # and compares it to the stored custody, so a rotated grant / changed
                    # credential_ref that kept the connection_id is rejected). This is the
                    # independent caller-ownership check, not a read of the grant's own owner.
                    from tinyassets.provider_serving_binding import (
                        _open_connection_id,
                        verify_open_grant_custody,
                    )

                    connection_id = _open_connection_id(
                        Path(base_path), uid, assignment.provider,
                        owner_user_id=capability.principal_id,
                    )
                    custody = current_connection_grant_custody(
                        conn,
                        owner_user_id=capability.principal_id,
                        universe_id=uid,
                        connection_id=connection_id,
                    )
                    if not _exact_custody(custody):
                        raise PermissionError("credential custody is not current")
                    verify_open_grant_custody(
                        Path(base_path), uid, capability.principal_id,
                        assignment.provider, custody,
                    )
                    authority = ServedProviderAuthority(
                        authority_kind="connection_grant",
                        provider=assignment.provider,
                        max_invocations=provider_binding.max_invocations,
                        request_max_invocations=_SERVED_REQUEST_MAX_INVOCATIONS,
                        max_tokens=provider_binding.max_tokens,
                        max_cost_microunits=provider_binding.max_cost_microunits,
                        owner_user_id=capability.principal_id,
                        universe_id=uid,
                        agent_binding_id=carrier_binding_id,
                        binding_revision=carrier_revision,
                        binding_id=provider_binding.binding_id,
                        binding_generation=provider_binding.generation,
                        binding_digest=provider_binding.binding_digest,
                        credential_reference_id=custody.reference_id,
                        credential_reference_generation=custody.generation,
                        credential_reference_digest=custody.reference_digest,
                        credential_service="http",
                        credential_snapshot_dir=None,
                        request_capability=capability,
                    )
                else:
                    service = {"codex": "codex", "claude-code": "claude"}.get(
                        assignment.provider
                    )
                    if service is None:
                        raise PermissionError("provider is not supported for serving")
                    custody = current_llm_subscription_custody(
                        conn,
                        universe_dir=universe,
                        owner_user_id=capability.principal_id,
                        universe_id=uid,
                        service=service,
                    )
                    if not _exact_custody(custody):
                        raise PermissionError("credential custody is not current")
                    credential_snapshot = snapshot_llm_subscription_credential(
                        universe_dir=universe,
                        custody=custody,
                    )
                    authority = ServedProviderAuthority(
                        authority_kind="subscription_snapshot",
                        provider=assignment.provider,
                        max_invocations=provider_binding.max_invocations,
                        request_max_invocations=_SERVED_REQUEST_MAX_INVOCATIONS,
                        max_tokens=provider_binding.max_tokens,
                        max_cost_microunits=provider_binding.max_cost_microunits,
                        owner_user_id=capability.principal_id,
                        universe_id=uid,
                        agent_binding_id=carrier_binding_id,
                        binding_revision=carrier_revision,
                        binding_id=provider_binding.binding_id,
                        binding_generation=provider_binding.generation,
                        binding_digest=provider_binding.binding_digest,
                        credential_reference_id=custody.reference_id,
                        credential_reference_generation=credential_snapshot.generation,
                        credential_reference_digest=credential_snapshot.reference_digest,
                        credential_service=service,
                        credential_snapshot_dir=credential_snapshot.directory,
                        request_capability=capability,
                    )
                conn.rollback()
            yield authority
        except ProviderAuthorityHeldError:
            raise
        except Exception as exc:
            # Exceptions from the provider body are raised back through the
            # context-manager yield. They are not authority failures and must
            # retain their original type/diagnostics.
            if authority is not None:
                raise
            raise ProviderAuthorityHeldError(held) from exc
        finally:
            cleanup_llm_credential_snapshot(credential_snapshot)
