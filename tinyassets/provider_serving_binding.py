"""Server-owned serving bindings for authenticated universe turns."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tinyassets.config import write_provider_assignment_projection
from tinyassets.credential_vault import (
    LLMCredentialCustodyReference,
    adopt_connection_grant_custody,
    adopt_llm_subscription_custody,
    current_connection_grant_custody,
    current_llm_subscription_custody,
)
from tinyassets.custom_agents import (
    get_binding,
    serving_binding_candidates,
    set_binding_provider_ref_in_transaction,
    set_binding_serving_in_transaction,
)
from tinyassets.provider_assignment import (
    ProviderAssignment,
    load_provider_assignment,
    load_provider_assignment_in_transaction,
    provider_assignment_admission,
    provider_assignment_digest,
    store_provider_assignment_in_transaction,
)
from tinyassets.provider_assignment_manifest import (
    AssignmentCandidate,
    ModelAccess,
    manifest_digest,
)
from tinyassets.provider_work_authority import (
    ProviderWorkAuthorityWriteOutcome,
    ProviderWorkBindingFence,
    ProviderWorkBindingRoot,
    ProviderWorkBindingSeed,
    ProviderWorkBindingService,
    provider_work_binding_id,
)
from tinyassets.storage.provider_work_authority import (
    SQLiteProviderWorkAuthorityStore,
)

_PROVIDER_SERVICE = {
    "claude-code": "claude",
    "codex": "codex",
}
_SERVING_OPERATIONS = ("converse",)
_SERVING_ROLES = ("writer",)
_MAX_BINDING_INVOCATIONS = 10_000
# In-flight token / cost ceilings for a serving binding. These bound only
# UNSETTLED (concurrent) reserved spend — a settled turn RELEASES (see
# provider_assignment.reserve_served_provider_budget), so this is a CONCURRENCY
# runaway guard, NOT a cumulative spend limit (the user's own deposited
# subscription meters real spend upstream). The prior 32_768 was sized like a
# spend cap and bricked at ~2 concurrent codex turns: a served turn reserves
# `len(system+prompt bytes)` (a rebuilt persona/brain system prompt is ~15-30 KB)
# plus its output, so the SECOND simultaneous turn across ANY surface got
# `output_tokens < 1` -> "budget exhausted". That violated the core requirement
# that one user drive their universe from many surfaces at once alongside
# concurrent LangGraph automations (and many users doing the same, each on their
# OWN per-binding ceiling). Sized now for realistic single-user concurrency
# (~40-50 simultaneous ~80-95 KB reservations); the true runaway backstops remain
# the rolling per-hour invocation cap (_MAX_BINDING_INVOCATIONS) + the engine-run
# rate limit (20/hr) + the user's metered subscription.
_MAX_TOKENS = 4_000_000
_MAX_COST_MICROUNITS = 400_000_000  # affordable = _MAX_COST/100 tokens, kept >= _MAX_TOKENS
_BINDING_TTL = timedelta(days=30)
_PLACEHOLDER_DIGEST = f"sha256:{'0' * 64}"


@dataclass(frozen=True, slots=True)
class _ServingResolver:
    seed: ProviderWorkBindingSeed

    def resolve(self, root: ProviderWorkBindingRoot) -> ProviderWorkBindingSeed | None:
        return self.seed if self._matches(root) else None

    def resolve_current_in_transaction(
        self,
        _connection: object,
        root: ProviderWorkBindingRoot,
    ) -> ProviderWorkBindingSeed | None:
        return self.resolve(root)

    def _matches(self, root: ProviderWorkBindingRoot) -> bool:
        return (
            root.owner_user_id == self.seed.owner_user_id
            and root.universe_id == self.seed.universe_id
            and root.provider == self.seed.provider
        )


def _expiry(now: datetime) -> str:
    return (now + _BINDING_TTL).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_universe(base: Path, universe_dir: str | Path, universe_id: str) -> Path:
    canonical_base = base.resolve(strict=False)
    expected = (canonical_base / universe_id).resolve(strict=False)
    supplied = Path(universe_dir).resolve(strict=False)
    if supplied != expected or expected.parent != canonical_base:
        raise ValueError("command center directory does not match the canonical command center id")
    return supplied


def _projection(binding) -> dict[str, object]:
    return {
        "binding_id": binding.binding_id,
        "generation": binding.generation,
        "binding_digest": binding.binding_digest,
        "state": binding.state.value,
        "owner_user_id": binding.owner_user_id,
        "universe_id": binding.universe_id,
        "provider": binding.provider,
        "allowed_operations": list(binding.allowed_operations),
        "allowed_roles": list(binding.allowed_roles),
        "assignment_generation": binding.assignment_generation,
        "assignment_digest": binding.assignment_digest,
        "max_invocations": binding.max_invocations,
        "max_tokens": binding.max_tokens,
        "max_cost_microunits": binding.max_cost_microunits,
        "expires_at": binding.expires_at,
    }


def _assignment(
    *,
    owner_user_id: str,
    universe_id: str,
    state: str,
    generation: int,
    provider: str,
    binding_id: str,
    binding_generation: int,
    binding_digest: str,
    custody: LLMCredentialCustodyReference,
    updated_at: str,
    candidates: tuple[AssignmentCandidate, ...] = (),
) -> ProviderAssignment:
    manifest = manifest_digest(provider, candidates) if candidates else ""
    digest = provider_assignment_digest(
        owner_user_id=owner_user_id,
        universe_id=universe_id,
        provider=provider,
        generation=generation,
        binding_id=binding_id,
        credential_reference_id=custody.reference_id,
        credential_reference_generation=custody.generation,
        credential_reference_digest=custody.reference_digest,
        manifest_digest=manifest,
    )
    return ProviderAssignment(
        universe_id=universe_id,
        owner_user_id=owner_user_id,
        state=state,
        generation=generation,
        provider=provider,
        binding_id=binding_id,
        binding_generation=binding_generation,
        binding_digest=binding_digest,
        credential_reference_id=custody.reference_id,
        credential_reference_generation=custody.generation,
        credential_reference_digest=custody.reference_digest,
        assignment_digest=digest,
        updated_at=updated_at,
        manifest_digest=manifest,
        candidates=candidates,
    )


def _write_failed_assignment(
    store: SQLiteProviderWorkAuthorityStore,
    pending: ProviderAssignment,
    universe_dir: Path,
) -> None:
    try:
        with store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if (
                load_provider_assignment_in_transaction(
                    conn,
                    universe_id=pending.universe_id,
                )
                != pending
            ):
                # Another authoritative transition must not be overwritten by
                # recovery for this older publication attempt.
                return
            failed = replace(
                pending,
                state="failed",
                updated_at=store.timestamp(),
            )
            store_provider_assignment_in_transaction(conn, failed)
            conn.commit()
        write_provider_assignment_projection(
            universe_dir,
            state="failed",
            generation=pending.generation,
        )
    except Exception:
        # The already-published pending assignment is deny-all. Preserve it
        # rather than masking the original exception with recovery diagnostics.
        pass


def _is_open_provider(name: str) -> bool:
    return name.startswith("api_key_http:")


class UnknownServingProvider(ValueError):
    """The name is neither an alias nor a definition registered in this universe.

    Subclasses ``ValueError`` so every existing ``except ValueError`` handler keeps
    catching it; callers that want to REPORT the distinction catch this first.
    ``get_definition`` is universe-scoped, so another universe's definition_id
    lands here too — missing and foreign stay indistinguishable, which is what
    keeps this from being an existence oracle.
    """


class ServingProviderNotOwned(PermissionError):
    """The definition exists in this universe but its grant is absent, revoked, or
    not the caller's. Only reachable for a definition the caller can already list,
    so naming it discloses nothing they could not see."""


class NoServingProvider(PermissionError):
    """The founder has no current serving binding."""


class ServingProviderHeld(PermissionError):
    """Readiness hold with a fixed display code; existing refusal semantics stay."""

    def __init__(self, message: str, *, reason: str):
        if reason not in {"host_serving_hold", "role_not_supported"}:
            raise ValueError("invalid serving hold reason")
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class CurrentServingProviderAuthority:
    """Secret-free result of the canonical serving-authority revalidation."""

    provider: str
    access_method: str
    connection_id: str = ""
    grant_id: str = ""


def _open_serving_context(
    base: Path, universe_id: str, owner_user_id: str, definition_id: str
) -> tuple[str, str, str, str]:
    """Resolve + validate an open api_key_http provider for serving.

    Returns ``(provider_name, grant_id, connection_id, credential_ref)`` or raises.
    The connection grant MUST be owned by the caller + bound to this universe + not
    revoked — the bind-time isolation gate (authorize re-checks it at call time)."""
    from tinyassets.providers.definition import get_definition
    from tinyassets.providers.provider_resolver import provider_for_definition
    from tinyassets.storage.outbound_connections import ConnectionLedger

    definition = get_definition(universe_id, definition_id)
    if definition is None or definition.access_method != "api_key_http":
        raise UnknownServingProvider(
            "provider must be claude-code, codex, or a registered api_key_http definition_id"
        )
    provider_name = provider_for_definition(definition).name
    from tinyassets.broker.supervisor import broker_selected

    if broker_selected():
        from tinyassets.broker.ledger_queries import granted_resource_row
        from tinyassets.storage.outbound_connections import GrantResolutionError

        if not owner_user_id or definition.owner_user_id != owner_user_id:
            raise ServingProviderNotOwned("open provider definition is not owned by the caller")
        try:
            resource = granted_resource_row(
                base, principal=owner_user_id, command_center=universe_id,
                grant_id=definition.ref)
        except GrantResolutionError:
            raise ServingProviderNotOwned(
                "open provider connection authority unavailable") from None
        return (provider_name, definition.ref, resource["connection_id"],
                resource["credential_ref"])
    ledger = ConnectionLedger(base / "outbound.db")
    grant = ledger.get_grant(definition.ref)
    if grant is None or getattr(grant, "revoked_at", None) is not None:
        raise ServingProviderNotOwned("open provider connection grant is absent or revoked")
    if grant.owner_user_id != owner_user_id or grant.universe_id != universe_id:
        raise ServingProviderNotOwned(
            "open provider grant is not owned by the caller / bound to this command center"
        )
    resource = ledger._get_connection_resource(grant.connection_id)
    if resource is None:
        raise ServingProviderNotOwned("open provider connection resource is absent")
    return provider_name, grant.grant_id, grant.connection_id, resource.credential_ref


def verify_open_grant_custody(
    base: Path, universe_id: str, owner_user_id: str, provider_name: str, custody: object
) -> str:
    """Fail closed unless the LIVE connection grant behind ``provider_name`` still
    matches ``custody``. Returns the connection_id on success.

    This is the exact-identity revalidation the authority + budget + bind-ready paths
    MUST use (Codex serve-open-compute reject #1/#2/#3): it (a) re-resolves the grant
    with the FULL owner + universe + not-revoked gate via ``_open_serving_context`` —
    a foreign/rotated/revoked grant raises — and (b) recomputes the grant-identity
    record digest from the LIVE grant and requires it to equal the stored custody's
    ``_record_digest``, so a grant/credential_ref rotation that keeps the same
    connection_id is rejected (a stale-digest comparison alone would let it pass)."""
    from tinyassets.credential_vault import _connection_grant_record_digest

    def_id = provider_name.split("api_key_http:", 1)[-1]
    _pname, grant_id, connection_id, credential_ref = _open_serving_context(
        base, universe_id, owner_user_id, def_id
    )
    live_digest = _connection_grant_record_digest(
        grant_id=grant_id,
        connection_id=connection_id,
        credential_ref=credential_ref,
        owner_user_id=owner_user_id,
        universe_id=universe_id,
    )
    if custody is None or getattr(custody, "_record_digest", None) != live_digest:
        raise PermissionError(
            "open provider grant/credential changed since binding — re-bind required"
        )
    return connection_id


def _open_connection_id(base: Path, universe_id: str, provider_name: str, *,
                        owner_user_id: str = "") -> str:
    """The connection_id backing an open serving provider name, for the FIRST custody
    read before the full identity revalidation (verify_open_grant_custody). Existence +
    revocation in legacy mode; broker mode also requires the admitted owner.
    Callers MUST follow with verify_open_grant_custody for the rotation gate."""
    from tinyassets.broker.supervisor import broker_selected
    from tinyassets.providers.definition import get_definition
    from tinyassets.storage.outbound_connections import ConnectionLedger

    def_id = provider_name.split("api_key_http:", 1)[-1]
    if broker_selected():
        # The caller supplies its independently admitted owner. A definition or
        # grant's own owner must never stand in for that authority.
        return _open_serving_context(base, universe_id, owner_user_id, def_id)[2]
    definition = get_definition(universe_id, def_id)
    if definition is None or definition.access_method != "api_key_http":
        raise PermissionError("open provider definition is absent")
    grant = ConnectionLedger(base / "outbound.db").get_grant(definition.ref)
    if grant is None or getattr(grant, "revoked_at", None) is not None:
        raise PermissionError("open provider connection grant is absent or revoked")
    return grant.connection_id


@dataclass(frozen=True, slots=True)
class _ServingSource:
    provider: str
    open_grant: tuple[str, str, str] | None
    access: ModelAccess


def _resolve_serving_source(
    base: Path,
    uid: str,
    owner: str,
    provider: str,
    access: ModelAccess,
) -> _ServingSource:
    selected = provider.strip()
    # open_grant carries (grant_id, connection_id, credential_ref) for an open
    # api_key_http provider; None for a subscription-CLI provider. When set, the
    # custody source below switches to the secret-free connection-grant custody and
    # `selected` becomes the resolved executor name (api_key_http:<def-id>).
    open_grant: tuple[str, str, str] | None = None
    if selected not in _PROVIDER_SERVICE:
        provider_name, grant_id, connection_id, credential_ref = _open_serving_context(
            base, uid, owner, selected
        )
        selected = provider_name
        open_grant = (grant_id, connection_id, credential_ref)
    if selected == "claude-code":
        # claude-code serving is HELD BY DEFAULT. The OpenSpec design
        # (byo-llm-connect-flow/design.md §"Claude requester-local readiness")
        # requires that Slice 1 "must not silently bypass" the role-completeness
        # invariant "merely because `converse` currently asks only for `writer`".
        # So enabling it requires BOTH, and neither alone is enough:
        #
        #   (a) an EXPLICIT operator opt-in (`TINYASSETS_ALLOW_CLAUDE_SERVING`).
        #       Off by default, so no deployment silently gains claude serving;
        #       the vetted single-founder host sets it deliberately (host
        #       directive: the founder's universe serves on their own deposited
        #       Claude subscription). This is the documented ratification of the
        #       decision the founder drove — it is NOT computed away silently.
        #   (b) a COMPUTED proof that claude-code covers every role THIS binding
        #       actually grants (`_SERVING_ROLES`). Today that is `("writer",)`
        #       and claude-code heads the writer chain, so converse serving is
        #       covered. If the serving scope ever widens to a role claude-code
        #       cannot cover (judge/extract), (b) re-blocks automatically — the
        #       invariant is enforced, not bypassed.
        #
        # Cross-family review 2026-08-19 (Codex reject #2) flagged the earlier
        # (b)-only form as a silent bypass. Gate (a) restores the spec DEFAULT
        # (claude-code serving held) and makes any relaxation an explicit host
        # deployment decision, not a computed no-op. Formal OpenSpec sync of this
        # host exception (byo-llm-connect-flow/design.md §"Claude requester-local
        # readiness") is owed and pending founder ratification — tracked in
        # STATUS.md; the code fails safe (held) until the host opts in.
        from tinyassets.providers.router import FALLBACK_CHAINS

        opt_in = os.environ.get("TINYASSETS_ALLOW_CLAUDE_SERVING", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        uncovered = [
            role for role in _SERVING_ROLES if selected not in FALLBACK_CHAINS.get(role, ())
        ]
        if not opt_in:
            raise ServingProviderHeld(
                "claude-code serving is held by default; set "
                "TINYASSETS_ALLOW_CLAUDE_SERVING for the vetted host to enable it",
                reason="host_serving_hold",
            )
        if uncovered:
            raise ServingProviderHeld(
                "claude-code serving is held until every live role is covered; "
                f"uncovered role(s): {', '.join(uncovered)}",
                reason="role_not_supported",
            )
    return _ServingSource(selected, open_grant, access)


def _source_custody(
    conn,
    source: _ServingSource,
    *,
    base: Path,
    universe: Path,
    owner: str,
    uid: str,
    adopt: bool,
) -> LLMCredentialCustodyReference:
    if source.open_grant is not None:
        grant_id, connection_id, credential_ref = source.open_grant
        if adopt:
            custody = adopt_connection_grant_custody(
                conn,
                owner_user_id=owner,
                universe_id=uid,
                grant_id=grant_id,
                connection_id=connection_id,
                credential_ref=credential_ref,
            )
        else:
            custody = current_connection_grant_custody(
                conn,
                owner_user_id=owner,
                universe_id=uid,
                connection_id=connection_id,
            )
            verify_open_grant_custody(base, uid, owner, source.provider, custody)
    else:
        resolver = adopt_llm_subscription_custody if adopt else current_llm_subscription_custody
        custody = resolver(
            conn,
            universe_dir=universe,
            owner_user_id=owner,
            universe_id=uid,
            service=_PROVIDER_SERVICE[source.provider],
        )
    if custody is None:
        raise PermissionError("credential custody changed during assignment")
    return custody


def _member(
    source: _ServingSource,
    binding_id: str,
    generation: int,
    digest: str,
    custody: LLMCredentialCustodyReference,
) -> AssignmentCandidate:
    return AssignmentCandidate(
        source.provider,
        binding_id,
        generation,
        digest,
        custody.reference_id,
        custody.generation,
        custody.reference_digest,
        source.access,
    )


def _same_custody(member: AssignmentCandidate, custody: LLMCredentialCustodyReference) -> bool:
    return (
        member.credential_reference_id == custody.reference_id
        and member.credential_reference_generation == custody.generation
        and member.credential_reference_digest == custody.reference_digest
    )


def bind_serving_provider(
    *,
    base_path: str | Path,
    universe_dir: str | Path,
    owner_user_id: str,
    universe_id: str,
    agent_binding_id: str,
    expected_revision: int,
    provider: str,
    model_access: dict[str, ModelAccess] | None = None,
    expected_assignment_digest: str | None = None,
    expected_binding_updated_at: float | None = None,
    require_current_home: bool = False,
) -> dict[str, object]:
    """Publish one exact agent's accepted connections in the existing two phases.

    None preserves the legacy single-provider contract. A supplied map is the
    complete accepted membership, NOT fallback order or a model preference.
    App/MCP callers supply it only through explicit model-access confirmation;
    preferences and current model choices never imply this authority change.
    """
    base = Path(base_path)
    owner, uid = owner_user_id.strip(), universe_id.strip()
    binding_id = agent_binding_id.strip()
    anchor = _resolve_serving_source(base, uid, owner, provider, ModelAccess())
    selected = anchor.provider
    if model_access is None:
        sources = (anchor,)
    else:
        if type(model_access) is not dict or not model_access:
            raise ValueError("model access requires nonempty accepted membership")
        supplied = tuple(model_access.items())
        if any(
            not isinstance(name, str) or not name.strip() or not isinstance(access, ModelAccess)
            for name, access in supplied
        ):
            raise ValueError("invalid accepted connection model access")
        sources = tuple(
            _resolve_serving_source(base, uid, owner, name, access) for name, access in supplied
        )
        names = [source.provider for source in sources]
        if len(names) != len(set(names)) or selected not in names:
            raise ValueError("accepted membership must be unique and include the root provider")
        # Sorting is for canonical publication, never the user's fallback order.
        sources = tuple(sorted(sources, key=lambda source: source.provider))
    if not owner or not uid or not binding_id:
        raise ValueError("owner, command center, and agent binding are required")
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        raise ValueError("expected_revision must be a positive integer")
    universe = _canonical_universe(base, universe_dir, uid)
    agent = get_binding(base, universe_id=uid, binding_id=binding_id)
    if agent is None:
        raise LookupError("agent binding was not found")
    if agent["created_by"] != owner:
        raise PermissionError("only the binding creator may assign its provider")
    if int(agent["revision"]) != expected_revision:
        raise ValueError("agent binding revision is stale")
    if (expected_binding_updated_at is not None
            and agent["updated_at"] != expected_binding_updated_at):
        raise PermissionError("agent serving intent changed")

    admission = provider_assignment_admission()
    store = SQLiteProviderWorkAuthorityStore(base)
    binding_ids = {
        source.provider: provider_work_binding_id(
            owner_user_id=owner,
            universe_id=uid,
            provider=source.provider,
            binding_class="serving",
        )
        for source in sources
    }
    with admission.exclusive(universe):
        agent = get_binding(base, universe_id=uid, binding_id=binding_id)
        if agent is None:
            raise LookupError("agent binding was not found")
        if (expected_binding_updated_at is not None
                and agent["updated_at"] != expected_binding_updated_at):
            raise PermissionError("agent serving intent changed")
        if agent["created_by"] != owner:
            raise PermissionError("only the binding creator may assign its provider")
        if int(agent["revision"]) != expected_revision:
            raise ValueError("agent binding revision is stale")
        if agent.get("retired"):
            raise PermissionError("agent retired; restore it before assigning a provider")
        current_assignment = load_provider_assignment(base, universe_id=uid)
        if expected_assignment_digest is not None and expected_assignment_digest != (
            current_assignment.assignment_digest if current_assignment is not None else ""
        ):
            raise PermissionError("provider assignment changed since approval")
        if require_current_home:
            from tinyassets.shared_self import require_founder_home

            require_founder_home(base, uid, owner)
        current_bindings = {name: store.get(identifier) for name, identifier in binding_ids.items()}
        if (
            current_assignment is not None
            and current_assignment.state == "ready"
            and current_assignment.owner_user_id == owner
            and current_assignment.provider == selected
            and agent["configuration"].get("provider_ref") == binding_ids[selected]
            and bool(current_assignment.manifest_digest) == (model_access is not None)
            and (
                model_access is None
                or {
                    member.provider: member.access.document()
                    for member in current_assignment.candidates
                }
                == {source.provider: source.access.document() for source in sources}
            )
        ):
            try:
                with store.connection() as replay_conn:
                    replay_conn.execute("BEGIN")
                    # Re-read root and members inside the same SQLite snapshot.
                    replay_root = load_provider_assignment_in_transaction(
                        replay_conn, universe_id=uid
                    )
                    if replay_root != current_assignment:
                        raise PermissionError("assignment changed during replay")
                    members = replay_root.candidates or (None,)
                    for member in members:
                        _root, current, _custody = _current_bound_member_authority(
                            replay_conn,
                            store=store,
                            universe_dir=universe,
                            base_path=base,
                            owner_user_id=owner,
                            universe_id=uid,
                            assignment=replay_root,
                            member=member,
                        )
                        if (
                            current.max_invocations != _MAX_BINDING_INVOCATIONS
                            or current.max_tokens != _MAX_TOKENS
                            or current.max_cost_microunits != _MAX_COST_MICROUNITS
                        ):
                            raise PermissionError("serving ceilings changed")
                    replay_conn.rollback()
            except PermissionError:
                pass
            else:
                response = {
                    "status": "ready",
                    "replayed": True,
                    "provider_binding": _projection(current_bindings[selected]),
                    "agent_binding": agent,
                    "assignment_generation": current_assignment.generation,
                }
                if model_access is not None:
                    response["provider_candidates"] = [
                        _projection(current_bindings[source.provider]) for source in sources
                    ]
                return response

        generation = (current_assignment.generation + 1) if current_assignment else 1
        pending: ProviderAssignment | None = None
        try:
            # Publish a durable deny-all root and complete membership first.
            with store.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if require_current_home:
                    from tinyassets.storage.current_home import check_current_home

                    check_current_home(conn, owner, uid)
                if expected_assignment_digest is not None:
                    observed = load_provider_assignment_in_transaction(conn, universe_id=uid)
                    if expected_assignment_digest != (
                        observed.assignment_digest if observed is not None else ""
                    ):
                        raise PermissionError("provider assignment changed since approval")
                pending_members = []
                custodies = {}
                for source in sources:
                    custody = _source_custody(
                        conn,
                        source,
                        base=base,
                        universe=universe,
                        owner=owner,
                        uid=uid,
                        adopt=True,
                    )
                    custodies[source.provider] = custody
                    prior = current_bindings[source.provider]
                    pending_members.append(
                        _member(
                            source,
                            binding_ids[source.provider],
                            prior.generation + 1 if prior is not None else 1,
                            _PLACEHOLDER_DIGEST,
                            custody,
                        )
                    )
                anchor_member = next(
                    member for member in pending_members if member.provider == selected
                )
                pending = _assignment(
                    owner_user_id=owner,
                    universe_id=uid,
                    state="pending",
                    generation=generation,
                    provider=selected,
                    binding_id=anchor_member.binding_id,
                    binding_generation=anchor_member.binding_generation,
                    binding_digest=anchor_member.binding_digest,
                    custody=custodies[selected],
                    updated_at=store.timestamp(),
                    candidates=tuple(pending_members) if model_access is not None else (),
                )
                store_provider_assignment_in_transaction(conn, pending)
                conn.commit()
            write_provider_assignment_projection(universe, state="pending", generation=generation)

            with store.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if load_provider_assignment_in_transaction(conn, universe_id=uid) != pending:
                    raise PermissionError("assignment changed before publication")
                if require_current_home:
                    from tinyassets.storage.current_home import check_current_home

                    check_current_home(conn, owner, uid)
                records = {}
                ready_members = []
                for source, pending_member in zip(sources, pending_members, strict=True):
                    custody = _source_custody(
                        conn,
                        source,
                        base=base,
                        universe=universe,
                        owner=owner,
                        uid=uid,
                        adopt=False,
                    )
                    if not _same_custody(pending_member, custody):
                        raise PermissionError("credential custody changed during assignment")
                    custodies[source.provider] = custody
                    seed = ProviderWorkBindingSeed(
                        owner_user_id=owner,
                        universe_id=uid,
                        provider=source.provider,
                        credential_reference_digest=custody.reference_digest,
                        allowed_operations=_SERVING_OPERATIONS,
                        allowed_roles=_SERVING_ROLES,
                        assignment_generation=generation,
                        assignment_digest=pending.assignment_digest,
                        max_invocations=_MAX_BINDING_INVOCATIONS,
                        max_tokens=_MAX_TOKENS,
                        max_cost_microunits=_MAX_COST_MICROUNITS,
                        expires_at=_expiry(datetime.now(timezone.utc)),
                    )
                    service = ProviderWorkBindingService(store, _ServingResolver(seed))
                    current_binding = current_bindings[source.provider]
                    root = ProviderWorkBindingRoot(owner, uid, source.provider)
                    if current_binding is None:
                        issued = service.issue_in_transaction(conn, root)
                    else:
                        issued = service.rebind_in_transaction(
                            conn, ProviderWorkBindingFence(current_binding), root
                        )
                    if (
                        issued.outcome
                        not in {
                            ProviderWorkAuthorityWriteOutcome.APPLIED,
                            ProviderWorkAuthorityWriteOutcome.REPLAYED,
                        }
                        or issued.record is None
                    ):
                        raise PermissionError("serving provider binding could not be issued")
                    record = issued.record
                    if record.assignment_digest != pending.assignment_digest:
                        raise RuntimeError("binding and assignment digests disagree")
                    records[source.provider] = record
                    ready_members.append(
                        _member(
                            source,
                            record.binding_id,
                            record.generation,
                            record.binding_digest,
                            custody,
                        )
                    )
                provider_binding = records[selected]
                updated_agent = set_binding_provider_ref_in_transaction(
                    conn,
                    universe_id=uid,
                    binding_id=binding_id,
                    expected_revision=expected_revision,
                    owner_user_id=owner,
                    provider_ref=provider_binding.binding_id,
                )
                ready = _assignment(
                    owner_user_id=owner,
                    universe_id=uid,
                    state="ready",
                    generation=generation,
                    provider=selected,
                    binding_id=provider_binding.binding_id,
                    binding_generation=provider_binding.generation,
                    binding_digest=provider_binding.binding_digest,
                    custody=custodies[selected],
                    updated_at=store.timestamp(),
                    candidates=tuple(ready_members) if model_access is not None else (),
                )
                if ready.assignment_digest != pending.assignment_digest:
                    raise RuntimeError("ready and pending assignment digests disagree")
                store_provider_assignment_in_transaction(conn, ready)
                binding_projection = {
                    "binding_id": provider_binding.binding_id,
                    "generation": provider_binding.generation,
                    "binding_digest": provider_binding.binding_digest,
                    "assignment_digest": provider_binding.assignment_digest,
                }
                write_provider_assignment_projection(
                    universe,
                    state="ready",
                    generation=generation,
                    provider=selected,
                    binding=binding_projection,
                    **({} if model_access is None else {
                        "assignment_candidates": ready.candidates,
                    }),
                )
                conn.commit()
        except Exception:
            if pending is not None:
                _write_failed_assignment(store, pending, universe)
            raise

    response = {
        "status": "ready",
        "replayed": False,
        "provider_binding": _projection(provider_binding),
        "agent_binding": updated_agent,
        "assignment_generation": ready.generation,
        "next_action": "set_serving",
    }
    if model_access is not None:
        response["provider_candidates"] = [
            _projection(records[source.provider]) for source in sources
        ]
    return response


def _current_serving_authority(
    conn,
    *,
    store: SQLiteProviderWorkAuthorityStore,
    universe_dir: Path,
    base_path: Path | None = None,
    owner_user_id: str,
    universe_id: str,
    agent: dict[str, object],
) -> tuple[ProviderAssignment, object, LLMCredentialCustodyReference]:
    """Re-read the complete server-owned serving chain in one SQLite fence."""

    # Dispatch supplies its explicit storage root; readiness callers already
    # validate the canonical universe path. Do not change either lookup root.
    authority_root = Path(base_path) if base_path is not None else Path(universe_dir).parent

    assignment = load_provider_assignment_in_transaction(
        conn,
        universe_id=universe_id,
    )
    if assignment is not None and assignment.manifest_digest:
        # The storage seam may be built before per-attempt model/cost validation.
        # Never reinterpret a new manifest as legacy single-provider authority.
        raise PermissionError("model selection authority is not active")
    provider_ref = agent["configuration"].get("provider_ref")
    if (
        assignment is None
        or assignment.state != "ready"
        or assignment.owner_user_id != owner_user_id
        or not isinstance(provider_ref, str)
        or provider_ref != assignment.binding_id
    ):
        raise PermissionError("connect your provider before enabling serving")
    return _current_bound_member_authority(
        conn,
        store=store,
        universe_dir=universe_dir,
        base_path=authority_root,
        owner_user_id=owner_user_id,
        universe_id=universe_id,
        assignment=assignment,
    )


def _current_selected_member_authority(
    conn, *, store: SQLiteProviderWorkAuthorityStore, universe_dir: Path,
    base_path: Path, owner_user_id: str, universe_id: str,
    agent: dict[str, object], provider: str,
) -> tuple[ProviderAssignment, object, LLMCredentialCustodyReference]:
    """Current accepted member, still requiring selected-model validation."""
    assignment = load_provider_assignment_in_transaction(conn, universe_id=universe_id)
    if (
        assignment is None or not assignment.manifest_digest
        or agent["configuration"].get("provider_ref") != assignment.binding_id
    ):
        raise PermissionError("model selection requires the current accepted assignment")
    member = next((m for m in assignment.candidates if m.provider == provider), None)
    if member is None:
        raise PermissionError("provider is not in the current assignment")
    return _current_bound_member_authority(
        conn, store=store, universe_dir=universe_dir, base_path=base_path,
        owner_user_id=owner_user_id, universe_id=universe_id,
        assignment=assignment, member=member,
    )


def _current_bound_member_authority(
    conn,
    *,
    store: SQLiteProviderWorkAuthorityStore,
    universe_dir: Path,
    base_path: Path,
    owner_user_id: str,
    universe_id: str,
    assignment: ProviderAssignment,
    member: AssignmentCandidate | None = None,
) -> tuple[ProviderAssignment, object, LLMCredentialCustodyReference]:
    """Validate one published connection, not its model choice or launch permission.

    This is also the publication replay check. A ready independent member does
    not depend on the anchor's live credential. Public execution still passes
    through _current_serving_authority and its model-selection integration gate.
    """
    if (
        assignment.state != "ready"
        or assignment.owner_user_id != owner_user_id
        or assignment.universe_id != universe_id
    ):
        raise PermissionError("connect your provider before enabling serving")
    if assignment.manifest_digest:
        if member is None or member not in assignment.candidates:
            raise PermissionError("provider is not in the current assignment")
    elif member is not None:
        raise PermissionError("legacy assignment has no candidate authority")
    else:
        member = assignment
    authority_root = Path(base_path)
    provider_binding = store.get_binding_in_transaction(
        conn,
        binding_id=member.binding_id,
    )
    if provider_binding is None or not store.validate_in_transaction(
        conn,
        binding_id=member.binding_id,
        binding_generation=member.binding_generation,
        binding_digest=member.binding_digest,
        owner_user_id=owner_user_id,
        universe_id=universe_id,
        provider=member.provider,
        operation="converse",
        role="writer",
    ):
        raise PermissionError("connect your provider before enabling serving")
    if _is_open_provider(member.provider):
        custody = current_connection_grant_custody(
            conn,
            owner_user_id=owner_user_id,
            universe_id=universe_id,
            connection_id=_open_connection_id(
                authority_root, universe_id, member.provider, owner_user_id=owner_user_id),
        )
        # Exact live-grant revalidation (owner + bound + not-revoked + not-rotated),
        # not just a stored-digest compare (Codex reject #1/#2).
        verify_open_grant_custody(
            authority_root,
            universe_id,
            owner_user_id,
            member.provider,
            custody,
        )
    else:
        custody = current_llm_subscription_custody(
            conn,
            universe_dir=universe_dir,
            owner_user_id=owner_user_id,
            universe_id=universe_id,
            service=_PROVIDER_SERVICE[member.provider],
        )
    if custody is None or (
        custody.reference_id != member.credential_reference_id
        or custody.generation != member.credential_reference_generation
        or custody.reference_digest != member.credential_reference_digest
        or provider_binding.credential_reference_digest != custody.reference_digest
        or provider_binding.assignment_generation != assignment.generation
        or provider_binding.assignment_digest != assignment.assignment_digest
    ):
        raise PermissionError("connect your provider before enabling serving")
    return assignment, provider_binding, custody


def set_serving(
    *,
    base_path: str | Path,
    universe_dir: str | Path,
    owner_user_id: str,
    universe_id: str,
    agent_binding_id: str,
    expected_revision: int,
    enabled: bool,
    expected_assignment_digest: str | None = None,
    expected_binding_updated_at: float | None = None,
    require_current_home: bool = False,
) -> dict[str, object]:
    """Enable/disable an exact founder-owned binding for served turns.

    A composed owner-consent operation can additionally pin the assignment it
    approved. Binding revision alone does not fence another binding's changes
    to this universe's assignment. None preserves existing standalone callers.
    """

    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    if expected_assignment_digest is not None and not isinstance(expected_assignment_digest, str):
        raise ValueError("expected_assignment_digest must be a string")
    owner = owner_user_id.strip()
    uid = universe_id.strip()
    binding_id = agent_binding_id.strip()
    if not owner or not uid or not binding_id:
        raise ValueError("owner, command center, and agent binding are required")
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        raise ValueError("expected_revision must be a positive integer")

    universe = _canonical_universe(Path(base_path), universe_dir, uid)
    # Initialize/migrate the custom-agent schema before opening the composed
    # authority transaction below.
    existing = get_binding(base_path, universe_id=uid, binding_id=binding_id)
    if existing is None:
        raise LookupError("agent binding was not found")
    if (expected_binding_updated_at is not None
            and existing["updated_at"] != expected_binding_updated_at):
        raise PermissionError("agent serving intent changed")
    store = SQLiteProviderWorkAuthorityStore(base_path)
    prepared = None
    if enabled:
        with store.connection() as conn:
            assignment = load_provider_assignment_in_transaction(conn, universe_id=uid)
        if expected_assignment_digest is not None and expected_assignment_digest != (
            assignment.assignment_digest if assignment is not None else ""
        ):
            raise PermissionError("provider assignment changed since approval")
        if assignment is not None and assignment.manifest_digest:
            if existing["created_by"] != owner or int(existing["revision"]) != expected_revision:
                raise PermissionError("agent binding is not current owner authority")
            from tinyassets.config import load_universe_config
            from tinyassets.providers.served_model_plan import prepare_owned_model_plan

            # Remote discovery must finish before exclusive admission/SQL mutation.
            prepared = prepare_owned_model_plan(
                base=Path(base_path), universe=universe, owner=owner,
                agent=existing, config=load_universe_config(universe),
            )
    with provider_assignment_admission().exclusive(universe):
        current = get_binding(base_path, universe_id=uid, binding_id=binding_id)
        if current is None:
            raise LookupError("agent binding was not found")
        if current.get("retired"):
            raise PermissionError("agent retired; restore it before changing serving state")
        if (expected_binding_updated_at is not None
                and current["updated_at"] != expected_binding_updated_at):
            raise PermissionError("agent serving intent changed")
        if current["created_by"] != owner:
            raise PermissionError("only the binding creator may change serving state")
        if int(current["revision"]) != expected_revision:
            raise ValueError("agent binding revision is stale")
        with store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if require_current_home:
                from tinyassets.storage.current_home import check_current_home

                check_current_home(conn, owner, uid)
            if expected_assignment_digest is not None:
                approved = load_provider_assignment_in_transaction(conn, universe_id=uid)
                if expected_assignment_digest != (
                    approved.assignment_digest if approved is not None else ""
                ):
                    raise PermissionError("provider assignment changed since approval")
            if enabled:
                if prepared is not None:
                    prepared.recheck(
                        conn, store=store, base=Path(base_path), universe=universe,
                        owner=owner, agent=current, check_preferences=True,
                    )
                    assignment = prepared.assignment
                else:
                    from tinyassets.storage.current_home import check_current_home
                    from tinyassets.storage.model_preferences import _read

                    # Home-only preferences must agree with legacy readiness.
                    # Read under this same write transaction, so a concurrent
                    # save cannot slip between validation and enabling serving.
                    # Non-home bindings retain their existing independent path.
                    # Standalone legacy installations may not have initialized
                    # onboarding/home storage at all. Do not bootstrap it here.
                    has_home_storage = conn.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'founder_home'",
                    ).fetchone() is not None
                    home = None if not has_home_storage else conn.execute(
                        "SELECT universe_id FROM founder_home WHERE founder_sub = ?", (owner,),
                    ).fetchone()
                    if home is not None and home[0] == uid:
                        check_current_home(conn, owner, uid)
                        preferences = _read(conn, owner, uid)
                        if preferences.policy is not None and preferences.policy.mode == "explicit":
                            raise PermissionError(
                                "model choice requires an accepted model assignment"
                            )
                    assignment, _provider_binding, _custody = _current_serving_authority(
                        conn,
                        store=store,
                        universe_dir=universe,
                        owner_user_id=owner,
                        universe_id=uid,
                        agent=current,
                    )
            updated = set_binding_serving_in_transaction(
                conn,
                universe_id=uid,
                binding_id=binding_id,
                expected_revision=expected_revision,
                owner_user_id=owner,
                enabled=enabled,
            )
            conn.commit()
    response: dict[str, object] = {
        "status": "serving" if enabled else "configured",
        "agent_binding": updated,
    }
    if enabled:
        from tinyassets.engine_mcp_http import notify_engine_serving_changed

        notify_engine_serving_changed(actor_id=owner, graph_id=uid, root=Path(base_path))
        response["provider"] = (
            assignment.provider if prepared is None
            else prepared.plan.next_candidate(owner, uid).connection_id
        )
        response["assignment_generation"] = assignment.generation
    return response


def resolve_serving_agent_binding(
    base_path: str | Path,
    *,
    universe_id: str,
    owner_user_id: str,
) -> dict[str, object]:
    """Select exactly one current serving binding for a founder turn."""

    matches = serving_binding_candidates(
        base_path, universe_id=universe_id, owner_user_id=owner_user_id,
    )
    if not matches:
        # Nothing serves this universe, so nothing ran: a setup state, not a
        # failure. Typed so the turn can say so instead of "actions may already
        # have occurred" (live 2026-09-24, an unpowered free-only account).
        raise NoServingProvider(
            "connect your provider: no founder serving binding exists yet"
        )
    if len(matches) != 1:
        raise PermissionError(
            "connect your provider: exactly one founder serving binding is required"
        )
    return matches[0]


def serving_connection_is_current(
    base_path: str | Path,
    *,
    universe_dir: str | Path,
    universe_id: str,
    owner_user_id: str,
) -> bool:
    """Local connection readiness only, never model or execution authority.

    A manifest needs at least one current accepted member, not a healthy anchor.
    Polling must not discover models, check remote quota, or enable an executor.
    Actual work still selects and authorizes its model at launch.
    """
    base = Path(base_path)
    uid, owner = universe_id.strip(), owner_user_id.strip()
    if not uid or not owner:
        return False
    universe = _canonical_universe(base, universe_dir, uid)
    store = SQLiteProviderWorkAuthorityStore(base)
    with provider_assignment_admission().shared(universe):
        matches = serving_binding_candidates(base, universe_id=uid, owner_user_id=owner)
        if len(matches) != 1:
            return False
        agent = get_binding(base, universe_id=uid,
                            binding_id=str(matches[0]["agent_binding_id"]))
        if agent is None or agent["status"] != "serving":
            return False
        with store.connection() as conn:
            conn.execute("BEGIN")
            try:
                assignment = load_provider_assignment_in_transaction(conn, universe_id=uid)
                if assignment is not None and assignment.manifest_digest:
                    for member in assignment.candidates:
                        try:
                            _current_selected_member_authority(
                                conn, store=store, universe_dir=universe, base_path=base,
                                owner_user_id=owner, universe_id=uid, agent=agent,
                                provider=member.provider,
                            )
                        except (PermissionError, UnknownServingProvider):
                            continue
                        return True
                    return False
                _current_serving_authority(
                    conn, store=store, universe_dir=universe, base_path=base,
                    owner_user_id=owner, universe_id=uid, agent=agent,
                )
                return True
            finally:
                conn.rollback()


def resolve_current_serving_provider_authority(
    base_path: str | Path,
    *,
    universe_dir: str | Path,
    universe_id: str,
    owner_user_id: str,
) -> CurrentServingProviderAuthority:
    """Resolve exactly the provider serving one founder, with live custody checks.

    The returned projection is deliberately credential-free. Open HTTP providers
    include only the exact connection/grant ids needed by server-internal
    capability code; subscription providers carry no auxiliary connection.
    """

    base = Path(base_path)
    uid = universe_id.strip()
    owner = owner_user_id.strip()
    if not uid or not owner:
        raise ValueError("owner and command center are required")
    universe = _canonical_universe(base, universe_dir, uid)
    store = SQLiteProviderWorkAuthorityStore(base)
    with provider_assignment_admission().shared(universe):
        matches = serving_binding_candidates(base, universe_id=uid, owner_user_id=owner)
        if not matches:
            raise NoServingProvider("connect your provider before enabling serving")
        if len(matches) != 1:
            raise PermissionError(
                "connect your provider: exactly one founder serving binding is required"
            )
        binding_id = str(matches[0]["agent_binding_id"])
        agent = get_binding(base, universe_id=uid, binding_id=binding_id)
        if agent is None or agent["status"] != "serving":
            raise PermissionError("connect your provider before enabling serving")
        with store.connection() as conn:
            conn.execute("BEGIN")
            assignment, _provider_binding, _custody = _current_serving_authority(
                conn,
                store=store,
                universe_dir=universe,
                owner_user_id=owner,
                universe_id=uid,
                agent=agent,
            )
            conn.rollback()

    if not _is_open_provider(assignment.provider):
        return CurrentServingProviderAuthority(
            provider=assignment.provider,
            access_method="subscription_cli",
        )
    definition_id = assignment.provider.split("api_key_http:", 1)[-1]
    _provider_name, grant_id, connection_id, _credential_ref = _open_serving_context(
        base, uid, owner, definition_id
    )
    return CurrentServingProviderAuthority(
        provider=assignment.provider,
        access_method="api_key_http",
        connection_id=connection_id,
        grant_id=grant_id,
    )


def list_serving_universes(base_path: str | Path) -> list[str]:
    """Return enrolled universes with current connection authority.

    This is scheduler inventory, not model selection or launch authorization.
    Manifest enrollments need a current accepted member; actual work must still
    select and authorize its model at launch. Never discover remote models while
    holding the inventory's database snapshot.
    """

    from collections import defaultdict

    from tinyassets.storage import db_path

    base = Path(base_path)
    conn = sqlite3.connect(db_path(base))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT universe_id, agent_binding_id, created_by
              FROM agent_bindings
             WHERE status = 'serving'
             ORDER BY universe_id, agent_binding_id
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[str(row["universe_id"])].append(row)

    valid: list[str] = []
    store = SQLiteProviderWorkAuthorityStore(base)
    for uid, candidates in grouped.items():
        universe = base / uid
        for row in candidates:
            agent = get_binding(
                base,
                universe_id=uid,
                binding_id=str(row["agent_binding_id"]),
            )
            if agent is None or agent["status"] != "serving":
                continue
            try:
                with provider_assignment_admission().shared(universe):
                    with store.connection() as authority_conn:
                        authority_conn.execute("BEGIN")
                        # Re-read after taking admission: a pause/rebind must not
                        # be evaluated using the pre-lock enrollment projection.
                        current_agent = get_binding(
                            base, universe_id=uid,
                            binding_id=str(row["agent_binding_id"]),
                        )
                        if current_agent is None or current_agent["status"] != "serving":
                            continue
                        assignment = load_provider_assignment_in_transaction(
                            authority_conn, universe_id=uid,
                        )
                        if assignment is not None and assignment.manifest_digest:
                            for member in assignment.candidates:
                                try:
                                    _current_selected_member_authority(
                                        authority_conn, store=store, universe_dir=universe,
                                        base_path=base, owner_user_id=str(row["created_by"]),
                                        universe_id=uid, agent=current_agent,
                                        provider=member.provider,
                                    )
                                except (PermissionError, UnknownServingProvider):
                                    continue
                                break
                            else:
                                continue
                        else:
                            _current_serving_authority(
                                authority_conn,
                                store=store,
                                universe_dir=universe,
                                owner_user_id=str(row["created_by"]),
                                universe_id=uid,
                                agent=current_agent,
                            )
                        authority_conn.rollback()
            except (PermissionError, RuntimeError, ValueError, sqlite3.Error):
                continue
            valid.append(uid)
            break
    return sorted(valid)


__all__ = [
    "CurrentServingProviderAuthority",
    "NoServingProvider",
    "bind_serving_provider",
    "list_serving_universes",
    "resolve_current_serving_provider_authority",
    "resolve_serving_agent_binding",
    "serving_connection_is_current",
    "set_serving",
]
