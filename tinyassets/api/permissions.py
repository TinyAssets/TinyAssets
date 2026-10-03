"""Shared MCP permission checks for universe-scoped writes.

Single source of truth for the ownership/visibility model ratified in
``docs/design-notes/2026-06-26-founder-and-universe-identity.md``:

  * **Visibility** is the ``public_read`` rule on a universe. A universe with
    no recorded rule is publicly readable by default; ``public_read=False``
    makes it private (unlisted, unreadable without a grant).
  * **Ownership** is the ``universe_acl`` grant set. Owning/admin/writing a
    universe is orthogonal to whether it is publicly visible — an admin grant
    does NOT make a universe private (that conflation is the bug this module
    replaces).

Writes always require an explicit grant (``write`` or ``admin``); reads are
allowed on public universes and otherwise require a grant.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from tinyassets.api.helpers import _base_path

logger = logging.getLogger("universe_server.permissions")

_READ_PERMISSIONS = frozenset({"read", "write", "admin"})
_WRITE_PERMISSIONS = frozenset({"write", "admin"})
_OPERATOR_PRIORITY_POLICY_VERSION = "operator-priority-v1"


@dataclass(frozen=True)
class OperatorRequestAdmissionVerdict:
    """One request-local composition of ordinary and priority authority."""

    allowed: bool
    error_code: str
    actor_id: str
    tenant_id: str
    universe_id: str
    trigger_source: str
    accepted_priority_weight: float
    grant_generation: int | None
    priority_policy_version: str = _OPERATOR_PRIORITY_POLICY_VERSION


def _now() -> float:
    return time.time()


def operator_request_admission_verdict(
    universe_id: str,
    *,
    requested_priority_weight: float,
    directed: bool = False,
) -> OperatorRequestAdmissionVerdict:
    """Compose request identity, ordinary scope, ACL, and priority grant.

    The caller supplies only the requested weight and target. Actor, tenant,
    ordinary action authority, exact-universe ACL, grant generation, trigger
    source, and evaluation time are server-derived. Environment identity and
    wildcard grants are never consulted.
    """

    from tinyassets.auth.middleware import current_identity
    from tinyassets.auth.provider import action_scope_for
    from tinyassets.daemon_server import universe_access_permission
    from tinyassets.storage.accounts import get_active_priority_grant

    uid = (universe_id or "").strip()
    identity = current_identity()
    actor_id = (identity.user_id or "").strip()
    authenticated = bool(actor_id)
    identity_metadata = identity.metadata or {}
    tenant_id = str(
        identity_metadata.get("org_id")
        or identity_metadata.get("tenant_id")
        or actor_id
    ).strip()
    trigger_source = "owner_queued" if directed else "user_request"

    def verdict(
        *,
        allowed: bool,
        error_code: str = "",
        accepted_priority_weight: float = 0.0,
        grant_generation: int | None = None,
        priority_trigger: bool = False,
    ) -> OperatorRequestAdmissionVerdict:
        return OperatorRequestAdmissionVerdict(
            allowed=allowed,
            error_code=error_code,
            actor_id=actor_id,
            tenant_id=tenant_id or actor_id,
            universe_id=uid,
            trigger_source=(
                "owner_queued"
                if directed
                else "operator_request" if priority_trigger
                else trigger_source
            ),
            accepted_priority_weight=accepted_priority_weight,
            grant_generation=grant_generation,
        )

    if not authenticated or not uid:
        return verdict(allowed=False, error_code="universe_access_denied")

    action_scope = action_scope_for("universe", "submit_request")
    grants = {
        str(capability).strip()
        for capability in identity.capabilities
        if str(capability).strip()
    }
    ordinary_authorized = bool(
        action_scope
        and (
            action_scope.oauth_scope in grants
            or action_scope.effect in grants
        )
    )
    if not ordinary_authorized:
        return verdict(allowed=False, error_code="universe_access_denied")

    try:
        acl_permission = universe_access_permission(
            _base_path(),
            universe_id=uid,
            actor_id=actor_id,
        )
    except Exception:
        logger.warning(
            "operator request ACL evaluation failed closed for %r",
            uid,
            exc_info=True,
        )
        return verdict(allowed=False, error_code="universe_access_denied")
    if acl_permission not in _WRITE_PERMISSIONS:
        return verdict(allowed=False, error_code="universe_access_denied")

    if requested_priority_weight == 0:
        return verdict(allowed=True)

    evaluated_at = _now()
    try:
        grant = get_active_priority_grant(
            _base_path(),
            subject_id=actor_id,
            universe_id=uid,
            evaluated_at=evaluated_at,
        )
    except Exception:
        logger.warning(
            "operator request priority evaluation failed closed for %r",
            uid,
            exc_info=True,
        )
        grant = None
    if grant is None:
        return verdict(
            allowed=False,
            error_code="priority_authorization_required",
        )

    return verdict(
        allowed=True,
        accepted_priority_weight=requested_priority_weight,
        grant_generation=int(grant["generation"]),
        priority_trigger=True,
    )


def operator_request_replay_verdict(
    universe_id: str,
) -> OperatorRequestAdmissionVerdict:
    """Reauthorize replay visibility without reauthorizing past priority.

    A replay can reveal committed history only while the current request
    identity still has ordinary submit scope and write/admin ACL on the exact
    universe. Passing zero deliberately skips the prospective priority-grant
    leg: revocation or expiry blocks new priority work, not historical replay.
    """

    return operator_request_admission_verdict(
        universe_id,
        requested_priority_weight=0.0,
    )


def operator_request_transaction_checks(
    verdict: OperatorRequestAdmissionVerdict,
) -> tuple[Callable[[Any], None], Callable[[Any], None]]:
    """Build exact ACL-before-lookup and priority-before-write checks."""

    from tinyassets.storage.accounts import (
        CapabilityGrantAuthorizationError,
        active_priority_grant_from_connection,
    )

    def access_check(conn: Any) -> None:
        row = conn.execute(
            """
            SELECT permission
            FROM universe_acl
            WHERE universe_id = ? AND actor_id = ?
            """,
            (verdict.universe_id, verdict.actor_id),
        ).fetchone()
        if row is None or str(row["permission"]) not in _WRITE_PERMISSIONS:
            raise PermissionError("universe_access_denied")

    def priority_check(conn: Any) -> None:
        if verdict.accepted_priority_weight == 0:
            return
        grant = active_priority_grant_from_connection(
            conn,
            subject_id=verdict.actor_id,
            universe_id=verdict.universe_id,
            evaluated_at=_now(),
        )
        if (
            grant is None
            or int(grant["generation"]) != verdict.grant_generation
        ):
            raise CapabilityGrantAuthorizationError(
                "priority_authorization_required"
            )

    return access_check, priority_check


def current_request_actor_id() -> str:
    """The authenticated request subject, or "" when nothing is bound.

    Never a stand-in: there is no synthetic principal (founder, 2026-09-02).
    Callers that need an actor treat "" as a refusal.
    """
    from tinyassets.auth.middleware import current_identity_or_none

    identity = current_identity_or_none()
    if identity is None:
        return ""
    return (getattr(identity, "user_id", "") or "").strip()


def current_actor_id() -> str:
    """Return the actor used for permission checks and error payloads.

    No environment fallback: the actor is exactly the authenticated request
    subject ("" when nothing is bound). A universe-server env var must never
    confer write authority over a universe.
    """
    return current_request_actor_id()


def is_authenticated_request() -> bool:
    return bool(current_request_actor_id())


def is_local_single_tenant() -> bool:
    """Whether this process is the local single-tenant daemon (stdio/sse tray),
    as opposed to a served multi-tenant request.

    Use this, never ``not is_authenticated_request()``, to decide whether a
    host-global side effect is safe: with a fail-closed boundary every caller
    is authenticated, so that test is always False and the side effect never
    happens.
    """
    from tinyassets.auth.middleware import is_local_operator_process

    return is_local_operator_process()


@contextmanager
def owner_run_identity(base: Any, universe_id: str, principal_id: str) -> Iterator[bool]:
    """Read as the universe's OWNER for a run the owner's own universe set up.

    A universe's own agents, automations and background wakes are the owner
    acting (founder, 2026-09-27: "pretty much the same as itself"), but they run
    on threads no request bound, and every by-id reader resolves the REQUEST
    actor. While universes defaulted public that was invisible; private-by-default
    (2026-09-26) turned it into a refusal of the owner's own content.

    Binds ``principal_id`` only when it is a named principal holding the
    ``admin`` grant on ``universe_id`` -- the same ownership signal the
    interlocutor tier uses. Anyone else leaves the context exactly as it was, so
    this can never widen what another user or a visitor reads. When the owner is
    ALREADY the bound actor (their live request), that identity is kept rather
    than narrowed. Yields whether the owner is the actor inside.

    This is the owner, not a read-only view of them: ACL gates check the stored
    grant, not ``capabilities``, so the run may write its own universe too. That
    is intended (founder 2026-09-27, "same as itself"); the capabilities bound
    only scoped dispatch (``require_action_scope``) to read/list.
    """
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.daemon_server import universe_access_permission
    from tinyassets.principals import has_named_principal

    uid = (universe_id or "").strip()
    pid = (principal_id or "").strip()
    try:
        owner = bool(uid and has_named_principal(pid)) and universe_access_permission(
            base, universe_id=uid, actor_id=pid,
        ) == "admin"
    except Exception:  # noqa: BLE001 -- an unreadable ACL confers nothing
        logger.warning("owner_run_identity: ACL read failed for %r", uid, exc_info=True)
        owner = False
    if not owner:
        yield False
        return
    if current_request_actor_id() == pid:
        yield True
        return
    with identity_context(Identity(user_id=pid, username=pid, capabilities=["read", "list"])):
        yield True


def universe_public_read_allowed(universe_id: str) -> bool:
    """Return the explicit public-read rule for a universe.

    A *missing* rules row means no private/public decision has been recorded
    yet, so the universe remains publicly readable by default. Ownership/admin
    ACL rows are separate from this visibility bit.

    Fail-closed on real errors: a missing row (``KeyError``) is by-design
    public, but any *other* failure reading the rules (DB error, corrupt
    store) must NOT expose a possibly-private universe — it returns False.
    """
    uid = (universe_id or "").strip()
    if not uid:
        return True

    try:
        from tinyassets.daemon_server import get_universe_rules

        rules = get_universe_rules(_base_path(), universe_id=uid)
    except KeyError:
        # No rules row recorded → public by design.
        return True
    except Exception:
        # A real error reading the visibility rule — never fall open.
        logger.warning(
            "universe_public_read_allowed: failing closed on rules-read error "
            "for command center %r",
            uid,
            exc_info=True,
        )
        return False
    return bool(rules.get("public_read", True))


def _universe_is_owned(base: Any, universe_id: str) -> bool:
    """Whether an ownership row names this universe. Fails CLOSED.

    An unreadable ownership store means nothing is known to be owned, and the
    question this answers is "may the caller reach it" -- so the safe answer is
    no. The by-id readers in `tinyassets.api.universe` raise instead, so they can
    tell a caller the store is down rather than that their universe does not
    exist; a boolean gate has no room for that distinction and must deny.
    """
    from tinyassets.daemon_server import owned_universe_id

    try:
        return bool(owned_universe_id(base, universe_id))
    except Exception:  # noqa: BLE001 - fail closed
        logger.warning(
            "command center ownership lookup failed closed for %r", universe_id, exc_info=True,
        )
        return False


def universe_access_allows(universe_id: str, *, write: bool = False) -> bool:
    """Return whether the current actor may read/write a universe.

    Any authenticated caller may read a public universe. Universe-brain
    writes require a ``write`` or ``admin`` grant on it. Nothing unauthenticated
    reaches this check (the transport refused it).
    """
    uid = (universe_id or "").strip()
    if not uid:
        return not write

    from tinyassets.daemon_server import universe_access_permission

    base = _base_path()

    # A UNIVERSE NOBODY OWNS GRANTS NOTHING (2026-09-02; enforced here after the
    # Codex review of 2026-09-26). This is THE shared gate -- `visibility_permits`
    # composes it as its ceiling, and the wiki, runs, automations, status and
    # auto-ship readers call it directly -- so the ownership requirement belongs
    # here rather than at sixteen call sites.
    #
    # Reads were the hole. A write already needed a write/admin grant on the exact
    # id, which an unowned directory can never have. But a READ short-circuits on
    # `universe_public_read_allowed`, and that is True both for a `public` row and
    # for NO row at all ("missing means public by design") -- so an archive the old
    # boot backfill declared public, and a bare directory with no row whatsoever,
    # were both readable by explicit id. Filtering discovery did not retract
    # either, which is how `read_page` and explicit-id `get_status` kept answering
    # for the graveyard after it stopped being listed.
    if not _universe_is_owned(base, uid):
        return False

    if not write and universe_public_read_allowed(uid):
        return True

    if not is_authenticated_request():
        return False

    actor_id = current_actor_id()
    permission = universe_access_permission(
        base,
        universe_id=uid,
        actor_id=actor_id,
    )
    if not write and permission == "read":
        from tinyassets.daemon_server import list_universe_acl

        if not any(
            row.get("actor_id") == actor_id
            for row in list_universe_acl(base, universe_id=uid)
        ):
            return False
    allowed = _WRITE_PERMISSIONS if write else _READ_PERMISSIONS
    return permission in allowed


def universe_access_error(
    *,
    universe_id: str,
    write: bool = False,
    action: str = "",
    surface: str = "universe",
) -> dict[str, Any]:
    return {
        "error": "universe_access_denied",
        "surface": surface,
        "action": action,
        "universe_id": (universe_id or "").strip(),
        "actor_id": current_actor_id(),
        "required_permission": "write" if write else "read",
    }


def branch_run_actor(universe_id: str) -> str:
    uid = (universe_id or "").strip()
    if uid:
        return f"universe:{uid}"
    return current_actor_id()
