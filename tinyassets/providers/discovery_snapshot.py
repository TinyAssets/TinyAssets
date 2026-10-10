"""Fresh, advisory discovery through owned profiles; never inference authority."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tinyassets.api.helpers import _base_path
from tinyassets.providers.definition import ProviderDefinition, get_definition
from tinyassets.providers.discovery_contract import SourceContract
from tinyassets.providers.discovery_http import (
    ModelDiscoveryUnavailable,
    read_http_discovery_document,
)
from tinyassets.providers.discovery_protocols import DiscoveryProtocol
from tinyassets.providers.model_policy import ConnectionModels
from tinyassets.providers.wire_dialects import same_dialect
from tinyassets.storage.outbound_connections import (
    GrantResolutionError,
    ModelDiscoveryCapability,
    ModelUseCapability,
    ProxyRequestError,
    _resource_from_row,
    _validate_connection_capability,
    _verb_within_scopes,
)


@dataclass(frozen=True, slots=True)
class DiscoverySnapshot:
    owner_id: str
    universe_id: str
    provider: str
    connection_id: str
    grant_id: str
    source_digest: str
    catalogue_url: str
    benchmark_url: str
    observed_at: datetime
    completed_at: datetime
    models: ConnectionModels
    warnings: tuple[str, ...]
    # SourceContract, DiscoveryProtocol, or a DeclaredModelContract.
    execution_contract: SourceContract | DiscoveryProtocol | None = field(default=None, repr=False)

    def contract(self) -> SourceContract | DiscoveryProtocol:
        if self.execution_contract is not None:
            return self.execution_contract
        # Compatibility for legacy snapshots only. A connection-scoped custom
        # source is never registered as a provider alias or resolved by its name.
        from tinyassets.providers.discovery_protocols import discovery_protocol

        return discovery_protocol(self.models.provider_scope)


@dataclass(frozen=True, slots=True)
class _Context:
    definition: ProviderDefinition
    # A fetched catalogue (model_discovery) or the owner's declared list (model_use).
    profile: ModelDiscoveryCapability | ModelUseCapability
    digest: str
    auth_scheme: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _context(base: Path, owner: str, uid: str, definition_id: str) -> _Context:
    from tinyassets.credential_vault import _connection_grant_record_digest

    try:
        if not owner or not uid or not definition_id:
            raise ValueError("missing discovery context")
        definition = get_definition(uid, definition_id)
        if (
            not owner
            or not uid
            or definition is None
            or definition.owner_user_id != owner
            or definition.access_method != "api_key_http"
        ):
            raise ModelDiscoveryUnavailable("source_revoked")
        from tinyassets.broker.ledger_queries import DISCOVERY_FACTS, query_ledger

        facts = query_ledger(base, query=DISCOVERY_FACTS, principal=owner,
                             command_center=uid, grant_id=definition.ref)
        resource = _resource_from_row(facts["resource"])
        if (resource.revoked_at is not None or resource.owner_user_id != owner
                or resource.connection_type != "http"):
            raise ModelDiscoveryUnavailable("source_revoked")
        # The broker chooses the priced catalogue over a declared list within
        # the same transaction that validates the live grant and connection.
        use_row = facts["profile"] if facts["profile_kind"] == "model_use" else None
        if use_row is not None:
            profile = _validate_connection_capability(
                resource.connection_id, "model_use", use_row
            )
            if not isinstance(profile, ModelUseCapability):
                raise ValueError("wrong profile kind")
            if not _verb_within_scopes("POST", resource.scopes, resource.access_mode):
                raise ModelDiscoveryUnavailable("missing_discovery_scope")
            if not same_dialect(definition.protocol, profile.wire):
                raise ModelDiscoveryUnavailable("protocol_mismatch")
        else:
            if not _verb_within_scopes("GET", resource.scopes, resource.access_mode):
                raise ModelDiscoveryUnavailable("missing_discovery_scope")
            profile_row = facts["profile"]
            if profile_row is None:
                raise ModelDiscoveryUnavailable("missing_discovery_scope")
            profile = _validate_connection_capability(
                resource.connection_id, "model_discovery", profile_row
            )
            if not isinstance(profile, ModelDiscoveryCapability):
                raise ValueError("wrong profile kind")
            if resource.auth_scheme != profile.execution_contract().auth_scheme:
                raise ModelDiscoveryUnavailable("protocol_mismatch")
        # Existing custody identity, not a new secret hash or permission.
        identity = _connection_grant_record_digest(
            grant_id=definition.ref,
            connection_id=resource.connection_id,
            credential_ref=resource.credential_ref,
            owner_user_id=owner,
            universe_id=uid,
        )
        material = {
            "definition_id": definition.id,
            "grant_identity": identity,
            "granted_at": facts["granted_at"],
            "view": resource.to_view().as_dict(),
            "profile": profile.descriptor(),
        }
        digest = hashlib.sha256(
            json.dumps(
                material, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
        return _Context(definition, profile, digest, resource.auth_scheme)
    except GrantResolutionError:
        raise ModelDiscoveryUnavailable("source_revoked") from None
    except (LookupError, OSError, TypeError, ValueError, ProxyRequestError):
        raise ModelDiscoveryUnavailable("discovery_unavailable") from None


def refresh_model_discovery(
    *, owner_user_id: str, universe_id: str, definition_id: str
) -> DiscoverySnapshot:
    """Fetch current data; any later invocation still needs fresh authorization.

    Current explicit preferences, inference bindings and private workflows are
    untouched. Discovery grants no execution capability. Missing benchmark evidence
    leaves models unranked rather than making the catalogue disappear.
    """
    base = _base_path()
    before = _context(base, owner_user_id, universe_id, definition_id)
    profile = before.profile
    if isinstance(profile, ModelUseCapability):
        return _declared_snapshot(base, before, owner_user_id, universe_id, definition_id)
    contract = profile.execution_contract()
    custom = isinstance(contract, SourceContract)
    from tinyassets.providers.protocol_encoders import agent_codec_for

    def read(url: str):
        mode = {"json_mode": "exact"} if custom else {}
        return read_http_discovery_document(
            db_path=base / "outbound.db",
            definition=before.definition,
            owner_user_id=owner_user_id,
            universe_id=universe_id,
            url=url,
            **mode,
        )

    # Include the catalogue request itself in the freshness window.
    observed_at = _now()
    payload = read(profile.catalogue_url)
    benchmarks = None
    warnings = ()
    if profile.benchmark_url:
        try:
            decode_benchmark = contract.decode_benchmarks if custom else contract.benchmark_decoder
            benchmarks = decode_benchmark(
                read(profile.benchmark_url), now=_now(), max_age=timedelta(days=1)
            )
        except Exception:
            warnings = ("benchmark_unavailable",)
    provider = f"api_key_http:{before.definition.id}"
    decode_models = contract.decode_models if custom else contract.model_decoder
    models = decode_models(
        payload,
        connection=ConnectionModels(
            connection_id=provider,
            provider_scope="custom-http" if custom else profile.protocol,
            source_kind="http",
            freshness="fresh",
            owner_filtered=False if custom else contract.account_filtered,
            # Local executor capability, never the remote catalogue's claim.
            executor_tools=agent_codec_for(contract.inference_protocol) is not None,
            models=(),
            authenticated_account_id=None,
            availability_basis="owner_configured_contract" if custom else None,
        ),
        benchmarks=benchmarks,
    )
    after = _context(base, owner_user_id, universe_id, definition_id)
    completed_at = _now()
    if after.digest != before.digest or after.definition != before.definition:
        raise ModelDiscoveryUnavailable("source_revoked")
    if completed_at < observed_at or completed_at - observed_at > timedelta(minutes=5):
        raise ModelDiscoveryUnavailable("discovery_expired")
    return DiscoverySnapshot(
        owner_user_id,
        universe_id,
        provider,
        profile.connection_id,
        before.definition.ref,
        before.digest,
        profile.catalogue_url,
        profile.benchmark_url,
        observed_at,
        completed_at,
        models,
        warnings,
        contract,
    )


def _declared_snapshot(
    base: Path, before: _Context, owner_user_id: str, universe_id: str, definition_id: str,
) -> DiscoverySnapshot:
    """The owner's declared models as a fresh snapshot; nothing is fetched.

    The same authority re-read as a catalogue refresh brackets it, so a grant
    revoked or a declaration edited in between still fails closed.
    """
    from tinyassets.providers.declared_models import (
        declared_connection_models,
        declared_model_contract,
    )

    observed_at = _now()
    contract = declared_model_contract(before.profile.descriptor(), auth_scheme=before.auth_scheme)
    provider = f"api_key_http:{before.definition.id}"
    models = declared_connection_models(contract, provider=provider)
    after = _context(base, owner_user_id, universe_id, definition_id)
    if after.digest != before.digest or after.definition != before.definition:
        raise ModelDiscoveryUnavailable("source_revoked")
    return DiscoverySnapshot(
        owner_user_id, universe_id, provider, before.profile.connection_id,
        before.definition.ref, before.digest, "", "", observed_at, _now(), models, (),
        contract,
    )


_INFLIGHT: dict[tuple[asyncio.AbstractEventLoop, str, str, str, str], asyncio.Task] = {}


def assert_discovery_snapshot_current(snapshot: DiscoverySnapshot) -> None:
    """Recheck trusted refresh output at dispatch; this does not issue authority."""
    now = _now()
    if (
        now < snapshot.completed_at or now - snapshot.observed_at > timedelta(minutes=5)
        or snapshot.models.freshness != "fresh"
    ):
        raise ModelDiscoveryUnavailable("discovery_expired")
    current = _context(
        _base_path(), snapshot.owner_id, snapshot.universe_id,
        snapshot.provider.removeprefix("api_key_http:"),
    )
    if current.digest != snapshot.source_digest:
        raise ModelDiscoveryUnavailable("source_revoked")


async def refresh_model_discovery_async(
    *, owner_user_id: str, universe_id: str, definition_id: str
) -> DiscoverySnapshot:
    """Per-event-loop single-flight, no completed-result cache or blocking ingress.

    Cancelling a waiting caller does not cancel the broker worker or permit a
    replacement request before it settles. Separate server processes retain their
    own flights; neither this map nor a snapshot is an authority store.
    """
    loop = asyncio.get_running_loop()
    key = (loop, str(_base_path().resolve()), owner_user_id, universe_id, definition_id)
    task = _INFLIGHT.get(key)
    if task is None or task.done():
        task = asyncio.create_task(
            asyncio.to_thread(
                refresh_model_discovery,
                owner_user_id=owner_user_id,
                universe_id=universe_id,
                definition_id=definition_id,
            )
        )
        _INFLIGHT[key] = task

        def finished(done):
            if _INFLIGHT.get(key) is done:
                del _INFLIGHT[key]
            if not done.cancelled():
                done.exception()  # Retrieve failures even if every waiter cancelled.

        task.add_done_callback(finished)
    return await asyncio.shield(task)
