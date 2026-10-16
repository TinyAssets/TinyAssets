"""Authenticated founder home-universe resolution for conversation entry."""

from __future__ import annotations

import contextvars
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_HOME_MATERIALIZE_LOCK = threading.Lock()
_HOME_FAILURE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "founder_home_failure", default=""
)


def founder_home_failure() -> str:
    """Why the last ``ensure_founder_home`` on this context returned ``""``.

    ``scope`` means the caller lacks create capability. Every other token
    is a home that could not be created or loaded. Empty after a success.
    """
    return _HOME_FAILURE.get()


def _no_home(reason: str) -> str:
    _HOME_FAILURE.set(reason)
    return ""


def _home_dir(base: Path, universe_id: str) -> Path | None:
    """Resolve a home id inside ``base`` or fail closed on path traversal."""
    if not universe_id:
        return None
    root = base.resolve()
    candidate = (root / universe_id).resolve()
    return candidate if candidate.parent == root else None


def home_is_complete(base: Path, universe_id: str, *, owner: str) -> bool:
    """Return whether a bound home has the canonical completed-seed marker."""
    universe_dir = _home_dir(base, universe_id)
    from tinyassets.universe_soul import soul_present

    return universe_dir is not None and soul_present(universe_dir, owner=owner)


def principal_is_deleted(base: Path, founder: str) -> bool:
    """Whether this principal deleted their account (account-deletion tombstone).

    Read-only and fail-open on a missing table so a pre-migration database still
    serves. The tombstone is keyed by a one-way digest of the principal, never
    the principal itself, and is written before the deletion touches anything.
    """
    import sqlite3

    from tinyassets.account_deletion import principal_digest
    from tinyassets.daemon_server import _connect, initialize_author_server
    from tinyassets.principals import named_principal

    subject = named_principal(founder)
    if not subject:
        return False
    try:
        initialize_author_server(base)
        with _connect(base) as conn:
            row = conn.execute(
                "SELECT 1 FROM deleted_principals WHERE founder_sub = ?",
                (principal_digest(subject),),
            ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False


def ensure_founder_home(base: Path, founder: str) -> str:
    """Resolve or atomically create the authenticated founder's home universe.

    Returns ``""`` when the founder lacks create scope or materialization fails.
    The create scope is checked before reserving an id, and concurrent callers
    converge on one binding and one ledgered creation.
    """
    from tinyassets.daemon_server import (
        claim_founder_home,
        founder_home_is_platform_generated,
        get_founder_home,
    )

    _HOME_FAILURE.set("")
    home = get_founder_home(base, founder)
    if home_is_complete(base, home, owner=founder):
        return home

    # A deleted account does not come back on the next request. The principal's
    # already-issued token stays valid here until it expires and until the
    # identity provider removes the user, so a second device would otherwise be
    # handed a brand-new universe seconds after the person deleted their
    # account. Fail closed and loudly rather than silently re-founding them.
    if principal_is_deleted(base, founder):
        logger.warning(
            "first-contact refused: principal %s is tombstoned by account deletion",
            founder,
        )
        return _no_home("deleted")

    from tinyassets.auth.middleware import require_action_scope

    try:
        require_action_scope("universe", "create_universe")
    except PermissionError:
        return _no_home("scope")

    from tinyassets.ids import is_universe_serial, new_universe_id

    winner = claim_founder_home(base, founder, new_universe_id())
    if not winner:
        return _no_home("unbound")
    # Provenance gate (universe-creation 5.2): the internal-trust flag may only
    # materialize a value proven to be PLATFORM-GENERATED — not merely one that
    # matches the serial FORMAT. ``claim_founder_home`` runs INSERT ... ON
    # CONFLICT DO NOTHING, so ``winner`` may be a pre-existing binding returned
    # verbatim; and ``founder_home`` has two writers (``claim_founder_home`` and
    # the general ``set_founder_home``), so sole-writer provenance cannot be
    # assumed. A hostile or legacy caller could persist a value that satisfies
    # ``is_universe_serial`` (e.g. ``u-000...``) without the platform ever
    # generating it. We therefore require the row's structural provenance marker
    # (stamped only when the platform generated the id), with the format check
    # kept as defense-in-depth. Anything else fails closed and LOUDLY — never
    # rebind/migrate a stale binding to a serial here; backfilling legitimate
    # existing serial rows is host-run migration (universe-creation 5.4).
    proven = founder_home_is_platform_generated(
        base, founder_sub=founder, universe_id=winner
    )
    if not (proven and is_universe_serial(winner)):
        logger.warning(
            "first-contact refused to materialize founder %s home: bound "
            "universe_id %r is not a proven platform-generated serial "
            "(marker=%s, serial_shape=%s). Failing closed; a host-run serial "
            "migration must repair the binding before this founder births.",
            founder,
            winner,
            proven,
            is_universe_serial(winner),
        )
        return _no_home("provenance")
    universe_dir = _home_dir(base, winner)
    if universe_dir is None:
        return _no_home("path")
    if home_is_complete(base, winner, owner=founder):
        return winner

    from tinyassets.api.universe import _universe_impl

    with _HOME_MATERIALIZE_LOCK:
        if home_is_complete(base, winner, owner=founder):
            return winner
        # DA4: an incomplete home keeps its root and grant; _universe_impl
        # resumes admission and seeding in place.
        refused = False
        try:
            # ``winner`` passed the provenance gate above: its founder_home row
            # carries the platform-generated marker AND it is serial-shaped. Only
            # such a proven-generated serial may cross the trusted internal path
            # so the public-birth boundary accepts our own id.
            _universe_impl(
                action="create_universe",
                universe_id=winner,
                allow_named_universe_id=True,
            )
        except Exception as exc:  # noqa: BLE001 - failed birth degrades honestly
            logger.warning(
                "first-contact materialize failed for founder %s",
                founder,
                exc_info=True,
            )
            from tinyassets.boxes.provider import BoxAuthError

            refused = isinstance(exc, BoxAuthError)
        if not home_is_complete(base, winner, owner=founder):
            return _no_home("admission" if refused else "incomplete")
    return winner
