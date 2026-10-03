"""Auth providers — pluggable authentication backends.

Two concrete providers:
  - DevAuthProvider: every bearer resolves to one named local operator
  - OAuthProvider: OAuth 2.1 + PKCE with Dynamic Client Registration

The provider is selected at TinyAssets Server startup based on UNIVERSE_SERVER_AUTH.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tinyassets.resolution.contracts import ResolverDecision

logger = logging.getLogger("universe_server.auth")


# ═══════════════════════════════════════════════════════════════════════════
# GitHub destination-scoped secret vending
# ═══════════════════════════════════════════════════════════════════════════
#
# Process-env GitHub capability tokens are centralized here so every
# daemon-side consumer (the PR/push effector today; read/search later)
# resolves a destination-scoped token through one helper instead of each
# effector hand-parsing its own JSON-map env var. This is the operator
# (process-env) capability tier; the per-universe credential vault
# (``tinyassets.credential_vault``) remains the higher-priority source —
# effectors check the vault first and fall through to this vended token.
#
# ``read`` reads ``TINYASSETS_GITHUB_READ_CAPABILITIES``. There is no
# ``push`` capability: the platform holds no GitHub push credential. The
# former ``TINYASSETS_GITHUB_PUSH_CAPABILITIES`` / legacy
# ``TINYASSETS_GITHUB_PR_CAPABILITIES`` maps, and the GitHub App token
# refresher that filled them, were retired 2026-09-24. GitHub is a connection
# a universe's owner may or may not have made.

_GITHUB_SECRET_CAPABILITY_ENVS: dict[str, tuple[str, ...]] = {
    "read": ("TINYASSETS_GITHUB_READ_CAPABILITIES",),
}


def _load_destination_secret_map(env_var: str) -> dict[str, str]:
    """Load a destination-keyed secret map from one JSON env var.

    Returns an empty dict when the env is unset, empty, or malformed.
    Malformed JSON is logged loudly (hard rule #8) but never raises — a
    parse failure means "no capability configured" for that env, which
    collapses the caller to its dry-run / unauthenticated path. Keys and
    values are whitespace-stripped; entries with an empty key or token
    are dropped so a placeholder line cannot accidentally grant a
    capability.
    """
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "%s is set but not valid JSON: %s. Destination-scoped GitHub "
            "secrets from this env are unavailable.",
            env_var,
            exc,
        )
        return {}
    if not isinstance(parsed, dict):
        logger.warning(
            "%s must decode to a JSON object (mapping destination -> "
            "token); got %s.",
            env_var,
            type(parsed).__name__,
        )
        return {}
    cleaned: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        destination = key.strip()
        token = value.strip()
        if not destination or not token:
            continue
        cleaned[destination] = token
    return cleaned


def vend_github_destination_secret(
    *, destination: str, capability: str = "read",
) -> dict[str, Any]:
    """Return a destination-scoped GitHub credential payload.

    Looks up ``destination`` (exact, whitespace-stripped match) in the
    capability map for ``capability`` (only "read" exists). The returned
    dict carries routing metadata plus the resolved token (empty string
    when none is configured). It is safe to pass within the daemon, but
    callers MUST NOT echo the ``token`` field into run state or external
    evidence (hard rule: capability tokens stay daemon-side).
    """
    destination_key = destination.strip()
    capability_key = capability.strip().lower() or "read"
    env_vars = _GITHUB_SECRET_CAPABILITY_ENVS.get(capability_key, ())
    vendored: dict[str, Any] = {
        "destination": destination_key,
        "capability": capability_key,
        "token": "",
        "source_env_var": env_vars[0] if env_vars else "",
    }
    if not destination_key or not env_vars:
        return vendored
    for env_var in env_vars:
        token = _load_destination_secret_map(env_var).get(destination_key, "")
        if token:
            vendored["token"] = token
            vendored["source_env_var"] = env_var
            break
    return vendored


# ═══════════════════════════════════════════════════════════════════════════
# Identity
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class PermissionAction:
    """Action being authorized by the replayable permission check."""

    name: str
    cost_tier: str = "standard"
    required_scope: str = ""

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        self.cost_tier = self.cost_tier.strip() or "standard"
        self.required_scope = self.required_scope.strip()
        if not self.name:
            raise ValueError("PermissionAction.name is required")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ActionScopeMetadata:
    """Named OAuth-scope metadata derived from an internal action registry."""

    tool: str
    action: str
    oauth_scope: str
    cost_tier: str
    effect: str
    source: str
    checkpoint: str = "dispatch"
    caveats: tuple[str, ...] = ()

    @property
    def action_name(self) -> str:
        return f"{self.tool}.{self.action}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "action": self.action,
            "action_name": self.action_name,
            "oauth_scope": self.oauth_scope,
            "cost_tier": self.cost_tier,
            "effect": self.effect,
            "source": self.source,
            "checkpoint": self.checkpoint,
            "caveats": list(self.caveats),
        }


@dataclass
class PermissionScope:
    """Bounded resource scope for a permission decision."""

    universe_id: str = ""
    goal_id: str | None = None
    branch_id: str | None = None
    node_id: str | None = None
    tier: str = "universe"
    resource_type: str = ""
    resource_id: str = ""
    actor_scope: str = "user"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PermissionContext:
    """Replay inputs that are not identity, action, or resource scope."""

    actor_id: str = ""
    presented_grants: tuple[str, ...] = ()
    resource_policy_version: str = "permission-policy-v1"
    resolver_decision: ResolverDecision | None = None
    external_evidence_handles: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "presented_grants": list(self.presented_grants),
            "resource_policy_version": self.resource_policy_version,
            "resolver_decision": (
                self.resolver_decision.to_dict()
                if self.resolver_decision is not None else None
            ),
            "external_evidence_handles": list(self.external_evidence_handles),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class PermissionVerdict:
    """Auditable authorization result returned by ``.can(...)``."""

    allowed: bool
    action: str
    required_scope: str
    scope: dict[str, Any]
    reason: str
    presented_grants: tuple[str, ...]
    resource_policy_version: str
    resolver_decision_status: str = ""
    evidence_handles: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.allowed

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "action": self.action,
            "required_scope": self.required_scope,
            "scope": self.scope,
            "reason": self.reason,
            "presented_grants": list(self.presented_grants),
            "resource_policy_version": self.resource_policy_version,
            "resolver_decision_status": self.resolver_decision_status,
            "evidence_handles": list(self.evidence_handles),
        }


def _coerce_action(action: str | PermissionAction) -> PermissionAction:
    if isinstance(action, PermissionAction):
        return action
    return PermissionAction(name=str(action))


def _coerce_scope(scope: PermissionScope | None) -> PermissionScope:
    return scope if scope is not None else PermissionScope()


def _coerce_context(
    actor_id: str,
    grants: list[str] | tuple[str, ...],
    context: PermissionContext | None,
) -> PermissionContext:
    if context is not None:
        return context
    return PermissionContext(actor_id=actor_id, presented_grants=tuple(grants))


def resolve_permission(
    *,
    actor_id: str,
    action: str | PermissionAction,
    grants: list[str] | tuple[str, ...],
    scope: PermissionScope | None = None,
    context: PermissionContext | None = None,
) -> PermissionVerdict:
    """Return a deterministic permission verdict from replayable inputs."""

    permission_action = _coerce_action(action)
    permission_scope = _coerce_scope(scope)
    permission_context = _coerce_context(actor_id, grants, context)
    presented_grants = tuple(
        sorted({grant.strip() for grant in (
            permission_context.presented_grants or tuple(grants)
        ) if grant.strip()})
    )
    required_scope = permission_action.required_scope or permission_action.name
    allowed = required_scope in presented_grants
    resolver_decision = permission_context.resolver_decision
    evidence_handles = tuple(permission_context.external_evidence_handles)
    resolver_status = ""
    if resolver_decision is not None:
        resolver_status = resolver_decision.status
        evidence_handles = tuple(resolver_decision.evidence_handles)
    reason = (
        "action grant present"
        if allowed else
        "action grant missing"
    )
    return PermissionVerdict(
        allowed=allowed,
        action=permission_action.name,
        required_scope=required_scope,
        scope=permission_scope.to_dict(),
        reason=reason,
        presented_grants=presented_grants,
        resource_policy_version=permission_context.resource_policy_version,
        resolver_decision_status=resolver_status,
        evidence_handles=evidence_handles,
    )


@dataclass
class Identity:
    """Resolved user identity from an authenticated request."""

    user_id: str
    username: str
    display_name: str = ""
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def can(
        self,
        action: str | PermissionAction,
        scope: PermissionScope | None = None,
        context: PermissionContext | None = None,
    ) -> PermissionVerdict:
        """Check whether this identity may perform an action."""

        return resolve_permission(
            actor_id=self.user_id,
            action=action,
            grants=self.capabilities,
            scope=scope,
            context=context,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_EFFECT_TO_SCOPE_SUFFIX: dict[str, str] = {
    "read": "read",
    "write": "write",
    "costly": "costly",
    "admin": "admin",
}

_EFFECT_TO_COST_TIER: dict[str, str] = {
    "read": "free",
    "write": "standard",
    "costly": "costly",
    "admin": "admin",
}

_EXTENSION_STANDALONE_ACTIONS = frozenset({
    "register", "list", "inspect", "approve", "disable", "enable", "remove",
    "get_action_scope_status",
})
_EXTENSION_STANDALONE_WRITE_ACTIONS = frozenset({
    "register", "approve", "disable", "enable", "remove",
})
_UNIVERSE_COSTLY_ACTIONS = frozenset({
    "create_universe",
    "post_to_goal_pool",
    "submit_node_bid",
    "daemon_create",
    "daemon_summon",
    "daemon_memory_promote",
})
_UNIVERSE_ADMIN_ACTIONS = frozenset({
    "control_daemon",
    "set_tier_config",
    "set_engine",
    "offer_engine",
    "daemon_banish",
    "daemon_pause",
    "daemon_resume",
    "daemon_restart",
    "daemon_update_behavior",
})
_EXTENSIONS_COSTLY_ACTIONS = frozenset({
    "deliver_output",
    "run_branch",
    "run_branch_version",
    "resume_run",
    "rollback_merge",
    "rollback_node",
    "subscribe_branch",
    "escrow_lock",
    "escrow_release",
    "escrow_refund",
    "record_outcome",
    "record_remix",
})
# cancel_run intentionally has no costly/admin override: _RUN_WRITE_ACTIONS
# classifies it as an owner write. api.runs checks its universe ACL or legacy owner.
_EXTENSIONS_ADMIN_ACTIONS = frozenset({
    "approve",
    "delete_branch",
    "approve_source_code",
    "grant_effector_consent",
    "revoke_effector_consent",
})
_GATES_COSTLY_ACTIONS = frozenset({
    "claim",
    "claim_from_branch_run",
    "stake_bonus",
    "release_bonus",
})
_GATES_ADMIN_ACTIONS = frozenset({
    "define_ladder",
    "retract",
    "unstake_bonus",
})


def _metadata_for_action(
    *,
    tool: str,
    action: str,
    source: str,
    write_actions: set[str] | frozenset[str],
    costly_actions: set[str] | frozenset[str] = frozenset(),
    admin_actions: set[str] | frozenset[str] = frozenset(),
) -> ActionScopeMetadata:
    if action in admin_actions:
        effect = "admin"
    elif action in costly_actions:
        effect = "costly"
    elif action in write_actions:
        effect = "write"
    else:
        effect = "read"
    return ActionScopeMetadata(
        tool=tool,
        action=action,
        oauth_scope=f"tinyassets.{tool}.{_EFFECT_TO_SCOPE_SUFFIX[effect]}",
        cost_tier=_EFFECT_TO_COST_TIER[effect],
        effect=effect,
        source=source,
    )


def _extend_scope_rows(
    rows: list[ActionScopeMetadata],
    *,
    tool: str,
    actions: set[str] | frozenset[str],
    source: str,
    write_actions: set[str] | frozenset[str],
    costly_actions: set[str] | frozenset[str] = frozenset(),
    admin_actions: set[str] | frozenset[str] = frozenset(),
) -> None:
    for action in sorted(actions):
        rows.append(_metadata_for_action(
            tool=tool,
            action=action,
            source=source,
            write_actions=write_actions,
            costly_actions=costly_actions,
            admin_actions=admin_actions,
        ))


def build_action_scope_registry() -> dict[str, ActionScopeMetadata]:
    """Derive action-scope metadata from internal MCP dispatch registries."""

    rows: list[ActionScopeMetadata] = []

    from tinyassets.api.auto_ship_actions import _AUTO_SHIP_ACTIONS
    from tinyassets.api.branches import _BRANCH_ACTIONS, _BRANCH_WRITE_ACTIONS
    from tinyassets.api.evaluation import (
        _BRANCH_VERSION_ACTIONS,
        _JUDGMENT_ACTIONS,
        _JUDGMENT_WRITE_ACTIONS,
    )
    from tinyassets.api.extensions_consent_actions import _EFFECTOR_CONSENT_ACTIONS
    from tinyassets.api.extensions_leaderboard_actions import _LEADERBOARD_ACTIONS
    from tinyassets.api.market import (
        _ATTRIBUTION_ACTIONS,
        _ESCROW_ACTIONS,
        _GATE_EVENT_ACTIONS,
        _GATES_ACTIONS,
        _GOAL_ACTIONS,
        _GOAL_WRITE_ACTIONS,
        _OUTCOME_ACTIONS,
    )
    from tinyassets.api.runs import _RUN_ACTIONS, _RUN_WRITE_ACTIONS
    from tinyassets.api.runtime_ops import (
        _INSPECT_DRY_ACTIONS,
        _MESSAGING_ACTIONS,
        _PROJECT_MEMORY_ACTIONS,
        _PROJECT_MEMORY_WRITE_ACTIONS,
        _SCHEDULER_ACTIONS,
    )
    from tinyassets.api.universe import UNIVERSE_ACTIONS, WRITE_ACTIONS
    from tinyassets.api.wiki import WIKI_ACTIONS, WIKI_WRITE_ACTIONS
    from tinyassets.authoring.service import (
        _AUTHORING_ACTIONS,
        _AUTHORING_COSTLY_ACTIONS,
        _AUTHORING_WRITE_ACTIONS,
    )
    from tinyassets.handoffs.service import (
        _HANDOFF_ACTIONS,
        _HANDOFF_COSTLY_ACTIONS,
        _HANDOFF_WRITE_ACTIONS,
    )

    _extend_scope_rows(
        rows,
        tool="universe",
        actions=set(UNIVERSE_ACTIONS),
        source="tinyassets.api.universe.UNIVERSE_ACTIONS",
        write_actions=set(WRITE_ACTIONS),
        costly_actions=_UNIVERSE_COSTLY_ACTIONS,
        admin_actions=_UNIVERSE_ADMIN_ACTIONS,
    )
    _extend_scope_rows(
        rows,
        tool="wiki",
        actions=set(WIKI_ACTIONS),
        source="tinyassets.api.wiki.WIKI_ACTIONS",
        write_actions=set(WIKI_WRITE_ACTIONS),
    )

    extension_actions: set[str] = set(_EXTENSION_STANDALONE_ACTIONS)
    extension_writes: set[str] = set(_EXTENSION_STANDALONE_WRITE_ACTIONS)
    for action_map in (
        _BRANCH_ACTIONS,
        _RUN_ACTIONS,
        _JUDGMENT_ACTIONS,
        _BRANCH_VERSION_ACTIONS,
        _PROJECT_MEMORY_ACTIONS,
        _MESSAGING_ACTIONS,
        _ESCROW_ACTIONS,
        _GATE_EVENT_ACTIONS,
        _INSPECT_DRY_ACTIONS,
        _AUTO_SHIP_ACTIONS,
        _SCHEDULER_ACTIONS,
        _OUTCOME_ACTIONS,
        _EFFECTOR_CONSENT_ACTIONS,
        _ATTRIBUTION_ACTIONS,
        _LEADERBOARD_ACTIONS,
        _AUTHORING_ACTIONS,
        _HANDOFF_ACTIONS,
    ):
        extension_actions.update(action_map)
    extension_writes.update(_BRANCH_WRITE_ACTIONS)
    extension_writes.update(_RUN_WRITE_ACTIONS)
    extension_writes.update(_JUDGMENT_WRITE_ACTIONS)
    extension_writes.update({"publish_version"})
    extension_writes.update(_PROJECT_MEMORY_WRITE_ACTIONS)
    extension_writes.update({"messaging_send", "messaging_ack"})
    extension_writes.update({
        # Money WRITE actions — these mutate balances / move funds out and MUST
        # require write authorization, never derive as read-scope. escrow_fund,
        # escrow_set_wallet, and escrow_withdraw were silently read-classified
        # before (slice1a review CRITICAL 2).
        "escrow_lock", "escrow_release", "escrow_refund",
        "escrow_fund", "escrow_set_wallet", "escrow_withdraw",
    })
    extension_writes.update({
        "attest_gate_event", "verify_gate_event", "dispute_gate_event",
        "retract_gate_event",
    })
    extension_writes.update({"subscribe_branch", "unsubscribe_branch"})
    extension_writes.update({"record_outcome", "record_remix"})
    extension_writes.update({
        "grant_effector_consent", "revoke_effector_consent",
    })
    # Authoring drafts: start/edit/publish mutate owner-scoped state, and a test
    # run spends execution/model budget, so it derives costly like run_branch.
    extension_writes.update(_AUTHORING_WRITE_ACTIONS)
    # Handoffs: prepare mints a single-use confirmation, execute performs a real
    # external effect, and record_evidence/attest_outcome mutate the durable
    # lifecycle and outcome registry. Omitting these would derive read scope for
    # a mutating action — the fail-OPEN direction.
    extension_writes.update(_HANDOFF_WRITE_ACTIONS)
    _extend_scope_rows(
        rows,
        tool="extensions",
        actions=extension_actions,
        source="tinyassets.api.extensions internal dispatch tables",
        write_actions=extension_writes,
        costly_actions=(
            _EXTENSIONS_COSTLY_ACTIONS
            | _AUTHORING_COSTLY_ACTIONS
            | _HANDOFF_COSTLY_ACTIONS
        ),
        admin_actions=_EXTENSIONS_ADMIN_ACTIONS,
    )

    gates_writes = {
        "define_ladder",
        "claim",
        "claim_from_branch_run",
        "record_conformance_pack",
        "retract",
        "stake_bonus",
        "unstake_bonus",
        "release_bonus",
    }
    _extend_scope_rows(
        rows,
        tool="gates",
        actions=set(_GATES_ACTIONS),
        source="tinyassets.api.market._GATES_ACTIONS",
        write_actions=gates_writes,
        costly_actions=_GATES_COSTLY_ACTIONS,
        admin_actions=_GATES_ADMIN_ACTIONS,
    )

    _extend_scope_rows(
        rows,
        tool="goals",
        actions=set(_GOAL_ACTIONS),
        source="tinyassets.api.market._GOAL_ACTIONS",
        write_actions=set(_GOAL_WRITE_ACTIONS),
        # run_canonical triggers a branch run; scope it as costly like run_branch.
        costly_actions=frozenset({"run_canonical"}),
    )

    return {row.action_name: row for row in rows}


def action_scope_for(tool: str, action: str) -> ActionScopeMetadata | None:
    """Return the derived action-scope row for one MCP tool action."""

    return build_action_scope_registry().get(f"{tool}.{action}")


def supported_oauth_scopes() -> list[str]:
    """Advertise named action scopes plus legacy coarse scopes."""

    scopes = {row.oauth_scope for row in build_action_scope_registry().values()}
    scopes.update({"read", "write", "admin"})
    return sorted(scopes)


def action_scope_audit() -> dict[str, Any]:
    """Self-auditing read payload for CHILD-3 gradient scopes."""

    registry = build_action_scope_registry()
    rows = [row.to_dict() for row in registry.values()]
    rows.sort(key=lambda item: (item["tool"], item["action"]))
    return {
        "schema_version": 1,
        "source": "internal_dispatch_action_registries",
        "scope_derivation": (
            "Scopes are derived from server-side action dispatch tables and "
            "write-action sets, not raw MCP tool schemas."
        ),
        "checkpoint_mode": (
            "Dispatch checkpoints fail closed for missing named scopes when "
            "auth is enabled; dev/no-auth mode bypasses grant enforcement."
        ),
        "oauth_scopes": supported_oauth_scopes(),
        "actions": rows,
        "counts": {
            "actions": len(rows),
            "oauth_scopes": len({row["oauth_scope"] for row in rows}),
        },
        "caveats": [
            "Read/write/admin legacy OAuth scopes are still advertised for "
            "older clients but are not the source of the derived table.",
            "Argument-sensitive actions such as command center control_daemon are "
            "classified at the action level; finer sub-action scoping can be "
            "added by attaching metadata to that internal sub-dispatch.",
        ],
    }


#: The operational probes' service principal (canary scripts, the deploy gate).
#: It holds NO capabilities: the auth middleware admits it only for the exact
#: JSON-RPC shapes in ``tinyassets.auth.wiki_canary.canary_request_allowed``
#: and refuses everything else before dispatch. There is no fallback identity
#: any more (founder, 2026-08-22 and 2026-09-02): a request either resolves to
#: a named principal or is refused.
CANARY = Identity(
    user_id="canary",
    username="canary",
    display_name="Canary",
    capabilities=[],
)

HOST = Identity(
    user_id="host",
    username="host",
    display_name="Host",
    capabilities=["read", "write", "submit_request", "list"],
)


# ═══════════════════════════════════════════════════════════════════════════
# Abstract Provider
# ═══════════════════════════════════════════════════════════════════════════


class AuthProvider(ABC):
    """Abstract authentication provider."""

    @abstractmethod
    def resolve_token(self, token: str) -> Identity | None:
        """Resolve a Bearer token to an Identity, or None if invalid."""
        ...

    @abstractmethod
    def is_auth_required(self) -> bool:
        """Whether this provider requires authentication."""
        ...

    def resolve_always_writes(self) -> bool:
        """Resolve-always mode (default off).

        When True, read-effect actions may use a separately established public
        projection, but every write/costly/admin action requires an
        authenticated principal holding the action's grant. This is the WorkOS
        production model (D0b): the per-universe ACL layer then confines an
        authenticated founder to their own universe. Providers that leave this
        False keep their existing gating (dev = fully open; OAuth = all-auth).
        """
        return False

    def writes_require_identity(self) -> bool:
        """Whether mutating MCP handles require a resolved identity.

        OAuth-backed modes reject writes without a principal. Dev mode keeps
        writes open for local and test flows. Merge note 2026-07-15:
        resolve-always mode (WorkOS/optional, D0b) is by definition an
        OAuth-backed write-gating mode, so it is folded in here — one
        semantic switch for the #1441 tool-layer gate and the pre-dispatch
        401 write challenge.
        """
        return self.is_auth_required() or self.resolve_always_writes()

    @abstractmethod
    def register_client(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Dynamic Client Registration (RFC 7591).

        Accepts client metadata, returns client credentials including
        client_id. MCP clients call this to self-register.
        """
        ...

    @abstractmethod
    def create_authorization(
        self,
        client_id: str,
        redirect_uri: str,
        scope: str,
        state: str,
        code_challenge: str,
        code_challenge_method: str,
    ) -> str:
        """Create an authorization code for the OAuth flow.

        Returns the authorization code. In a full implementation this
        would redirect to a consent page; for now it auto-approves.
        """
        ...

    @abstractmethod
    def exchange_code(
        self,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> dict[str, Any] | None:
        """Exchange an authorization code for tokens.

        Returns token response dict or None if invalid.
        """
        ...


# ═══════════════════════════════════════════════════════════════════════════
# Dev Provider — no auth
# ═══════════════════════════════════════════════════════════════════════════


DEV_USER_ENV = "UNIVERSE_SERVER_DEV_USER"


class DevAuthProvider(AuthProvider):
    """Development provider: every bearer resolves to ONE named local operator.

    There is no fallback identity (founder, 2026-09-02). The operator names
    themselves through ``UNIVERSE_SERVER_DEV_USER``; ``create_provider``
    refuses to start dev mode without it. A missing bearer is still refused
    by the transport, so local clients send any non-empty bearer.
    """

    def __init__(self, user_id: str | None = None) -> None:
        raw = (user_id or os.environ.get(DEV_USER_ENV, "")).strip()
        if not raw:
            raise RuntimeError(
                f"dev auth mode needs {DEV_USER_ENV}: name the local operator "
                "(there is no fallback principal)"
            )
        self._identity = Identity(
            user_id=raw,
            username=raw,
            display_name=raw,
            capabilities=["read", "write", "submit_request", "list", "costly", "admin"],
        )

    def resolve_token(self, token: str) -> Identity | None:
        if not token:
            return None
        return self._identity

    def is_auth_required(self) -> bool:
        return False

    def register_client(self, metadata: dict[str, Any]) -> dict[str, Any]:
        # Auto-register with a dummy client_id
        return {
            "client_id": f"dev_{secrets.token_hex(8)}",
            "client_name": metadata.get("client_name", "dev-client"),
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }

    def create_authorization(
        self, client_id: str, redirect_uri: str, scope: str,
        state: str, code_challenge: str, code_challenge_method: str,
    ) -> str:
        return f"dev_code_{secrets.token_hex(16)}"

    def exchange_code(
        self, code: str, client_id: str, redirect_uri: str,
        code_verifier: str,
    ) -> dict[str, Any] | None:
        return {
            "access_token": f"dev_token_{secrets.token_hex(16)}",
            "token_type": "Bearer",
            "expires_in": 86400,
            "scope": "read write",
        }


# ═══════════════════════════════════════════════════════════════════════════
# OAuth Provider — production auth
# ═══════════════════════════════════════════════════════════════════════════


class OAuthProvider(AuthProvider):
    """OAuth 2.1 + PKCE provider with SQLite-backed state.

    Implements:
      - Dynamic Client Registration (RFC 7591)
      - Authorization Code + PKCE (RFC 7636)
      - Token issuance and validation
      - Refresh tokens

    Storage is SQLite for V1 (matches the author_server pattern).
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            # Route through the canonical resolver so container deploys
            # get $TINYASSETS_DATA_DIR/.auth.db instead of the CWD-relative
            # ``output/.auth.db`` (which lands in /app/output inside a
            # container — ephemeral, loses auth sessions on every
            # restart). See tinyassets.storage.data_dir for precedence
            # semantics.
            from tinyassets.storage import data_dir
            db_path = data_dir() / ".auth.db"
        self._db_path = Path(db_path)
        self._initialize_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id       TEXT PRIMARY KEY,
                client_name     TEXT NOT NULL DEFAULT '',
                redirect_uris   TEXT NOT NULL DEFAULT '[]',
                grant_types     TEXT NOT NULL DEFAULT '["authorization_code"]',
                response_types  TEXT NOT NULL DEFAULT '["code"]',
                token_endpoint_auth_method TEXT NOT NULL DEFAULT 'none',
                created_at      TEXT NOT NULL,
                metadata_json   TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS authorization_codes (
                code            TEXT PRIMARY KEY,
                client_id       TEXT NOT NULL,
                redirect_uri    TEXT NOT NULL,
                scope           TEXT NOT NULL DEFAULT '',
                code_challenge  TEXT NOT NULL,
                code_challenge_method TEXT NOT NULL DEFAULT 'S256',
                user_id         TEXT NOT NULL,
                created_at      REAL NOT NULL,
                used            INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (client_id) REFERENCES oauth_clients(client_id)
            );

            CREATE TABLE IF NOT EXISTS access_tokens (
                token_hash      TEXT PRIMARY KEY,
                client_id       TEXT NOT NULL,
                user_id         TEXT NOT NULL,
                scope           TEXT NOT NULL DEFAULT '',
                expires_at      REAL NOT NULL,
                created_at      REAL NOT NULL,
                revoked         INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (client_id) REFERENCES oauth_clients(client_id)
            );

            CREATE TABLE IF NOT EXISTS refresh_tokens (
                token_hash      TEXT PRIMARY KEY,
                client_id       TEXT NOT NULL,
                user_id         TEXT NOT NULL,
                scope           TEXT NOT NULL DEFAULT '',
                expires_at      REAL NOT NULL,
                created_at      REAL NOT NULL,
                revoked         INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (client_id) REFERENCES oauth_clients(client_id)
            );
        """)
        conn.commit()
        conn.close()
        logger.info("OAuth database initialized at %s", self._db_path)

    # --- Token resolution ---

    def resolve_token(self, token: str) -> Identity | None:
        if not token:
            return None

        token_hash = hashlib.sha256(token.encode()).hexdigest()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT user_id, scope, expires_at, revoked "
                "FROM access_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()

            if not row:
                return None
            if row["revoked"]:
                return None
            if row["expires_at"] < time.time():
                return None

            # Resolve user identity
            user_id = row["user_id"]
            scope = row["scope"]
            capabilities = scope.split() if scope else ["read"]

            return Identity(
                user_id=user_id,
                username=user_id,
                display_name=user_id,
                capabilities=capabilities,
            )
        finally:
            conn.close()

    def is_auth_required(self) -> bool:
        return True

    # --- Dynamic Client Registration (RFC 7591) ---

    def register_client(self, metadata: dict[str, Any]) -> dict[str, Any]:
        client_id = f"mcp_{secrets.token_hex(16)}"
        now = datetime.now(timezone.utc).isoformat()

        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO oauth_clients "
                "(client_id, client_name, redirect_uris, grant_types, "
                " response_types, token_endpoint_auth_method, created_at, "
                " metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    client_id,
                    metadata.get("client_name", ""),
                    json.dumps(metadata.get("redirect_uris", [])),
                    json.dumps(metadata.get("grant_types", ["authorization_code"])),
                    json.dumps(metadata.get("response_types", ["code"])),
                    metadata.get("token_endpoint_auth_method", "none"),
                    now,
                    json.dumps(metadata),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Registered OAuth client: %s (%s)", client_id, metadata.get("client_name", ""))

        return {
            "client_id": client_id,
            "client_name": metadata.get("client_name", ""),
            "grant_types": metadata.get("grant_types", ["authorization_code"]),
            "response_types": metadata.get("response_types", ["code"]),
            "token_endpoint_auth_method": "none",
        }

    # --- Authorization ---

    def create_authorization(
        self, client_id: str, redirect_uri: str, scope: str,
        state: str, code_challenge: str, code_challenge_method: str,
    ) -> str:
        # Verify client exists
        conn = self._connect()
        try:
            client = conn.execute(
                "SELECT client_id FROM oauth_clients WHERE client_id = ?",
                (client_id,),
            ).fetchone()
            if not client:
                raise ValueError(f"Unknown client: {client_id}")

            code = secrets.token_urlsafe(48)
            conn.execute(
                "INSERT INTO authorization_codes "
                "(code, client_id, redirect_uri, scope, code_challenge, "
                " code_challenge_method, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    code, client_id, redirect_uri, scope or "read write",
                    code_challenge, code_challenge_method or "S256",
                    time.time(),
                ),
            )
            conn.commit()
            return code
        finally:
            conn.close()

    # --- Token exchange ---

    def exchange_code(
        self, code: str, client_id: str, redirect_uri: str,
        code_verifier: str,
    ) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM authorization_codes "
                "WHERE code = ? AND client_id = ? AND used = 0",
                (code, client_id),
            ).fetchone()

            if not row:
                return None

            # Check expiry (codes valid for 10 minutes)
            if time.time() - row["created_at"] > 600:
                return None

            # Verify PKCE
            if not self._verify_pkce(
                code_verifier, row["code_challenge"], row["code_challenge_method"],
            ):
                logger.warning("PKCE verification failed for client %s", client_id)
                return None

            # Mark code as used
            conn.execute(
                "UPDATE authorization_codes SET used = 1 WHERE code = ?",
                (code,),
            )

            # Issue tokens
            access_token = secrets.token_urlsafe(48)
            refresh_token = secrets.token_urlsafe(48)
            now = time.time()
            scope = row["scope"]

            access_hash = hashlib.sha256(access_token.encode()).hexdigest()
            refresh_hash = hashlib.sha256(refresh_token.encode()).hexdigest()

            conn.execute(
                "INSERT INTO access_tokens "
                "(token_hash, client_id, user_id, scope, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (access_hash, client_id, row["user_id"], scope, now + 3600, now),
            )
            conn.execute(
                "INSERT INTO refresh_tokens "
                "(token_hash, client_id, user_id, scope, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (refresh_hash, client_id, row["user_id"], scope, now + 86400 * 30, now),
            )
            conn.commit()

            return {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": refresh_token,
                "scope": scope,
            }
        finally:
            conn.close()

    @staticmethod
    def _verify_pkce(
        verifier: str, challenge: str, method: str,
    ) -> bool:
        """Verify PKCE code_verifier against stored challenge."""
        if method == "S256":
            computed = (
                hashlib.sha256(verifier.encode("ascii"))
                .digest()
            )
            import base64
            expected = base64.urlsafe_b64encode(computed).rstrip(b"=").decode("ascii")
            return expected == challenge
        elif method == "plain":
            return verifier == challenge
        return False


class OptionalOAuthProvider(OAuthProvider):
    """Compatibility OAuth provider with the same fail-closed boundary."""

    def is_auth_required(self) -> bool:
        # The ASGI transport owns the universal bearer challenge. Keep this
        # compatibility flag false so resolve-always action grants retain their
        # coarse WorkOS capability semantics after identity resolution.
        return False

    def resolve_always_writes(self) -> bool:
        # Compatibility mode still uses the resolve-always write model: public
        # projections are separate, and every write/costly/admin action requires an
        # authenticated principal holding the grant. Without this override both
        # gate flags are False, `require_action_scope` short-circuits, and an
        # unbound `write_graph target=universe` (create) slips through — the
        # pre-deploy security gap (STATUS 2026-07-02). Fail closed for writes.
        # The base class derives writes_require_identity() from this flag, so
        # the #1441 tool-layer write gate engages in this mode too.
        return True


# ═══════════════════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════════════════


def create_provider() -> AuthProvider:
    """Create the appropriate auth provider based on configuration."""
    auth_mode = os.environ.get("UNIVERSE_SERVER_AUTH", "false").strip().lower()
    if auth_mode == "workos":
        # WorkOS AuthKit is the Authorization Server; we are a Resource Server
        # validating its bearer JWTs. Lazy import so non-WorkOS deployments
        # don't load PyJWT. See docs/reference/workos-authkit-integration.md.
        from tinyassets.auth.workos_provider import WorkOSAuthProvider

        logger.info("WorkOS AuthKit auth provider enabled (Resource Server)")
        return WorkOSAuthProvider.from_env()
    if auth_mode in ("true", "1", "yes", "oauth"):
        logger.info("OAuth auth provider enabled")
        return OAuthProvider()
    if auth_mode in ("optional", "resolve"):
        logger.info("Optional OAuth auth provider enabled")
        return OptionalOAuthProvider()
    logger.info("Dev auth provider (named local operator)")
    return DevAuthProvider()
