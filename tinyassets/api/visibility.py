"""Universe visibility model — the public-projection access surface.

Truth split (see ``openspec/changes/universe-visibility/design.md`` and the
delta spec ``openspec/specs/universe-visibility``):

  * ``tinyassets.api.permissions`` owns **ownership** (the ``universe_acl``
    grant set) and the legacy binary **``public_read``** bit.
  * This module owns the enriched **visibility level** that decomposes what an
    *unauthenticated / non-granted* reader may do into three separately-grantable
    capabilities — ``discover_existence`` / ``read_metadata`` / ``read_content``
    — composes a per-universe level with a per-page override, and **fails
    closed** on an undeclared, unrecognized, or unreadable level rather than
    defaulting to visible.

Two structural invariants (both demanded by the cross-family review that
rejected the first cut):

  1. **Tighten-only by construction.** The effective read decision is
     ``legacy_gate AND new_layer`` — ``visibility_permits`` returns ``False``
     whenever the legacy ``universe_access_allows`` read gate denies, so the new
     layer can never *grant* a read the legacy gate withholds (an inconsistent
     row with ``public_read=False`` plus a permissive explicit level can no
     longer open a read).
  2. **Fail closed by default.** ``universe_visibility`` returns the *declared*
     level or ``CLOSED``; it never derives an open default from ``public_read``.
     Undeclared, blank, unrecognized, wrong-type, corrupt, and non-dict states
     all resolve to ``private``. Existing universes are declared by
     ``backfill_universe_visibility`` (the migration path), not by a fail-open
     fallback or an env opt-in to strictness.

  3. **Private by default (founder, 2026-09-26).** "nodes in users universes
     should be private unless they make them other user accessible or visible or
     interactable in some way". The resolver above was already strict; the leak
     was the two places that *declare*. Neither of them may declare an open
     level on an owner's behalf any more: creation without an explicit level
     declares ``private``, and the backfill declares ``private`` rather than
     deriving from the legacy ``public_read`` bit (whose own default is ``True``,
     which is what produced the wrong answer). Exposure is a separate, explicit
     owner action — ``write_graph target=universe operation=set_visibility``.

A reader holding a read/write/admin grant on a universe is never limited by
public projection visibility.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from tinyassets.api.helpers import _base_path

logger = logging.getLogger("universe_server.visibility")

# The three separately-grantable capabilities, in the canonical order used by
# the delta spec (existence -> metadata -> content).
CAPABILITIES = ("discover_existence", "read_metadata", "read_content")


@dataclass(frozen=True)
class VisibilityLevel:
    """A named triple of public-projection capabilities."""

    name: str
    discover_existence: bool
    read_metadata: bool
    read_content: bool

    def permits(self, capability: str) -> bool:
        if capability not in CAPABILITIES:
            raise ValueError(f"unknown visibility capability: {capability!r}")
        return bool(getattr(self, capability))


# Canonical presets. ``private`` is the fail-closed level: every undeclared,
# unrecognized, or unreadable state resolves to it.
PUBLIC = VisibilityLevel("public", True, True, True)
METADATA_ONLY = VisibilityLevel("metadata_only", True, True, False)
UNLISTED = VisibilityLevel("unlisted", False, False, True)
PRIVATE = VisibilityLevel("private", False, False, False)

LEVELS: dict[str, VisibilityLevel] = {
    lvl.name: lvl for lvl in (PUBLIC, METADATA_ONLY, UNLISTED, PRIVATE)
}

#: The fail-closed level — used whenever a level cannot be trusted.
CLOSED = PRIVATE

#: Rules-metadata key that stores the explicit declared level name.
LEVEL_METADATA_KEY = "visibility_level"

#: Default level the creation path records when the creator does not choose one.
#: ``private`` per the founder, 2026-09-26: nothing in a user's universe is
#: visible, accessible or interactable to another user unless its owner exposed
#: it. This was ``public`` until then, and because the public
#: ``write_graph target=universe`` create never forwarded a visibility, that
#: default decided the level of every universe born through the connector
#: REPRODUCED as a P1 concern on 2026-08-06, re-verified live 2026-08-28 and
#: 2026-09-03, resolved here). A creator may still pass an explicit level to
#: override; see openspec/changes/archive/2026-09-30-private-by-default-universes/.
DEFAULT_CREATE_VISIBILITY = "private"

#: Rules-metadata key recording WHO decided the declared level.
LEVEL_SOURCE_METADATA_KEY = "visibility_level_source"

#: The recognized provenances for a declared level. ``owner`` is the only one
#: that means a person chose it; every other value, and a missing key (every row
#: written before 2026-09-26), means the platform supplied it.
LEVEL_SOURCE_OWNER = "owner"
LEVEL_SOURCES = frozenset({LEVEL_SOURCE_OWNER, "default", "migration", "backfill"})

#: Page frontmatter keys a page may use to narrow its own content visibility.
_PAGE_VISIBILITY_KEYS = ("visibility", "content_visibility")

#: Page-level string values that explicitly withhold content ("content: false").
_PAGE_FALSE_VALUES = frozenset({"false", "no", "off", "0", "none"})


def parse_level(name: Any) -> VisibilityLevel | None:
    """Return the named level, or ``None`` if the name is not recognized.

    An unrecognized level name is NOT an error the caller can ignore — callers
    that resolve visibility treat ``None`` as fail-closed.
    """
    key = str(name or "").strip()
    if not key:
        return None
    return LEVELS.get(key)


# Sentinels distinguishing "no rules row recorded" from "rules unreadable".
_MISSING = object()
_CORRUPT = object()


def _read_rules(universe_id: str) -> dict[str, Any] | object:
    """Return the universe's rules dict, or a sentinel.

    ``_MISSING`` when no rules row exists yet; ``_CORRUPT`` when the rules could
    not be read (DB/store error) — which must never fall open.
    """
    uid = (universe_id or "").strip()
    if not uid:
        return _MISSING
    try:
        from tinyassets.daemon_server import get_universe_rules

        return get_universe_rules(_base_path(), universe_id=uid)
    except KeyError:
        return _MISSING
    except Exception:
        logger.warning(
            "visibility: failing closed on rules-read error for command center %r",
            uid,
            exc_info=True,
        )
        return _CORRUPT


def universe_visibility(universe_id: str) -> VisibilityLevel:
    """Resolve the *declared* visibility level for a universe, or ``CLOSED``.

    This is a strict, fail-closed resolver. It NEVER derives an open default
    from ``public_read`` — that bit is the legacy gate's concern, composed
    separately and only as a *ceiling* in :func:`visibility_permits`. Every one
    of the following resolves to the fail-closed ``private`` level:

      * blank universe id;
      * rules unreadable (corrupt store) or no rules row at all (undeclared);
      * the whole rules value, or its ``metadata`` container, is not a dict;
      * no ``visibility_level`` key present (undeclared — the backfill declares);
      * the declared value is not a string, is blank/whitespace, or is an
        unrecognized level name.

    Only a rules row carrying an explicit, recognized ``visibility_level``
    resolves to that named level.
    """
    if not (universe_id or "").strip():
        return CLOSED

    rules = _read_rules(universe_id)
    if rules is _CORRUPT or rules is _MISSING:
        return CLOSED
    if not isinstance(rules, dict):
        return CLOSED  # never AssertionError on a hand-forged/non-dict row.

    metadata = rules.get("metadata")
    if not isinstance(metadata, dict) or LEVEL_METADATA_KEY not in metadata:
        return CLOSED  # undeclared -> fail closed (backfill declares).

    declared = metadata[LEVEL_METADATA_KEY]
    if not isinstance(declared, str):
        return CLOSED  # null / number / bool / list / object -> fail closed.
    level = parse_level(declared)  # blank / whitespace / unrecognized -> None.
    if level is None:
        logger.warning(
            "visibility: undeclared-or-unrecognized level %r for command center %r "
            "-> failing closed",
            declared,
            universe_id,
        )
        return CLOSED
    return level


def declared_level_name(universe_id: str) -> str:
    """The level name to report to a permitted reader (spec Req 4)."""
    return universe_visibility(universe_id).name


def _reader_has_grant(universe_id: str) -> bool:
    """True when the current caller holds an explicit ACL grant on a universe.

    A granted reader is never limited by public projection visibility. This checks a
    real ``universe_acl`` row (read/write/admin) for the authenticated actor —
    NOT the "public universes return read" convenience convention.
    """
    from tinyassets.api import permissions

    if not permissions.is_authenticated_request():
        return False
    actor = permissions.current_actor_id()
    from tinyassets.principals import has_named_principal

    if not has_named_principal(actor):
        return False
    try:
        from tinyassets.daemon_server import list_universe_acl

        rows = list_universe_acl(_base_path(), universe_id=universe_id)
    except Exception:
        logger.warning(
            "visibility: ACL read failed for command center %r -> no grant assumed",
            universe_id,
            exc_info=True,
        )
        return False
    return any(
        row.get("actor_id") == actor
        and str(row.get("permission") or "") in {"read", "write", "admin"}
        for row in rows
    )


def visibility_permits(universe_id: str, capability: str) -> bool:
    """Whether the current caller may exercise ``capability`` on a universe.

    Structurally tighten-only: the legacy read gate is the ceiling. If
    ``universe_access_allows`` denies the read, this returns ``False`` — the new
    layer can never grant what legacy denies (so an inconsistent row with
    ``public_read=False`` plus a permissive explicit level cannot open a read).
    Within what legacy allows, a granted reader gets full access and every other
    public/non-granted reader is bound by the declared level, which is
    ``CLOSED`` for any undeclared universe.
    """
    if capability not in CAPABILITIES:
        raise ValueError(f"unknown visibility capability: {capability!r}")

    from tinyassets.api import permissions

    # Ceiling: legacy read gate. New layer only narrows from here.
    #
    # A UNIVERSE NOBODY OWNS GRANTS NOTHING, and that check lives in the ceiling
    # (`permissions.universe_access_allows`), not here -- do not re-add it. The
    # ceiling is also what the wiki, runs and automations readers call directly,
    # so one definition there covers every by-id reader; a second copy here would
    # be the "two definitions of one fact" that has to drift eventually.
    if not permissions.universe_access_allows(universe_id, write=False):
        return False
    if _reader_has_grant(universe_id):
        return True
    return universe_visibility(universe_id).permits(capability)


def _page_declared_visibility(page_meta: dict[str, Any]) -> str:
    """The page's own declared visibility string, or '' if none."""
    if not isinstance(page_meta, dict):
        return ""
    for key in _PAGE_VISIBILITY_KEYS:
        raw = page_meta.get(key)
        if raw not in (None, ""):
            return str(raw).strip()
    return ""


def page_content_permitted(
    page_meta: dict[str, Any], universe_id: str = ""
) -> bool:
    """Whether a single wiki page's *content* may be served to this caller.

    A page narrows — never widens — its universe's content grant. A page that
    declares a restrictive visibility (``private``, ``metadata_only``, an
    unrecognized level, or an explicit ``content: false``) is withheld from any
    reader that is not a *granted* reader of the page's universe (spec Req 3).

    Authentication alone is NOT authority here: a valid user with ordinary wiki
    scope but no universe ACL grant is treated exactly like a public visitor,
    so page restrictions cannot be bypassed by merely logging in.
    """
    declared = _page_declared_visibility(page_meta)
    if not declared:
        return True  # no page-level restriction -> defer to the universe gate.

    # A granted reader of the universe is exempt from page-level restriction.
    if _reader_has_grant(universe_id):
        return True

    lowered = declared.lower()
    if lowered in _PAGE_FALSE_VALUES:
        return False  # explicit `content: false`
    level = parse_level(lowered)
    if level is None:
        logger.warning(
            "visibility: unrecognized page visibility %r -> withholding content",
            declared,
        )
        return False  # fail closed for a non-granted reader.
    return level.read_content


def page_visible_in_listing(
    page_meta: dict[str, Any], universe_id: str = ""
) -> bool:
    """Whether a page may appear in a sibling read (search / since / list).

    A restricted page's body, excerpt, title, and path are all disclosure; a
    page withheld from content is withheld from these enumerations too, unless
    the caller is a granted reader. Reuses the same rule as content serving.
    """
    return page_content_permitted(page_meta, universe_id)


def set_universe_visibility(
    universe_id: str, level: str, *, source: str
) -> VisibilityLevel:
    """Declare a universe's explicit visibility level and who decided it.

    Writes the level into the universe rules metadata (creating the rules row if
    needed) and keeps the legacy ``public_read`` bit consistent so older read
    paths that still consult it behave sensibly.

    ``source`` is a REQUIRED keyword, one of :data:`LEVEL_SOURCES`, recording
    whether an owner chose this level or the platform supplied it. It has no
    default on purpose: a defaulted provenance is the same shape of bug as the
    defaulted *level* this change exists to fix, and a silent
    ``source="owner"`` would leave the next migration unable to tell a decision
    from a fallback.
    """
    resolved = parse_level(level)
    if resolved is None:
        raise ValueError(
            f"unknown visibility level {level!r}; expected one of "
            f"{sorted(LEVELS)}"
        )
    if source not in LEVEL_SOURCES:
        raise ValueError(
            f"unknown visibility level source {source!r}; expected one of "
            f"{sorted(LEVEL_SOURCES)}"
        )
    from tinyassets.daemon_server import (
        ensure_universe_rules,
        update_universe_rules,
    )

    base = _base_path()
    ensure_universe_rules(base, universe_id=universe_id)
    # Keep the legacy public_read ceiling consistent: it is True iff the level
    # grants a public visitor ANY capability. This makes the legacy gate a
    # correct ceiling for `visibility_permits` (which ANDs with it).
    any_anon_capability = (
        resolved.discover_existence
        or resolved.read_metadata
        or resolved.read_content
    )
    update_universe_rules(
        base,
        universe_id=universe_id,
        updates={
            "public_read": any_anon_capability,
            "metadata": {
                LEVEL_METADATA_KEY: resolved.name,
                LEVEL_SOURCE_METADATA_KEY: source,
            },
        },
    )
    return resolved


def declared_level_source(universe_id: str) -> str:
    """The recorded provenance of a universe's declared level, or ``""``.

    ``""`` means no provenance was recorded — which is every row written before
    2026-09-26, and per :func:`level_was_chosen_by_owner` is read as "the
    platform supplied it", never as an owner's choice.
    """
    rules = _read_rules(universe_id)
    if not isinstance(rules, dict):
        return ""
    meta = rules.get("metadata")
    if not isinstance(meta, dict):
        return ""
    recorded = meta.get(LEVEL_SOURCE_METADATA_KEY)
    if not isinstance(recorded, str):
        return ""
    recorded = recorded.strip()
    return recorded if recorded in LEVEL_SOURCES else ""


def level_was_chosen_by_owner(universe_id: str) -> bool:
    """Whether this universe's declared level is an owner's decision.

    Fails toward "not chosen": an absent, unrecognized, or non-string
    provenance is a level the platform supplied. The private-by-default
    migration flips exactly the universes for which this is ``False``.
    """
    return declared_level_source(universe_id) == LEVEL_SOURCE_OWNER


def backfill_universe_visibility(
    universe_ids: list[str] | None = None,
) -> dict[str, str]:
    """Declare ``private`` for every universe lacking an explicit level.

    Idempotent. Each universe with no explicit ``visibility_level`` yet is
    declared ``private`` with ``source="backfill"``. This is the migration that
    keeps the strict fail-closed default safe: after it runs, an undeclared state
    means genuine corruption, and fails closed.
    :func:`run_visibility_startup_gate` runs this at boot and refuses readiness
    if any undeclared row survives.

    It used to derive the level from the current effective ``public_read`` bit
    (``True`` -> ``public``) on the reasoning that "no universe changes
    visibility — it only becomes declared". That reasoning was defensible and the
    result was wrong: ``public_read``'s own default is ``True``, so preserving
    current behaviour preserved a level nobody chose, and boot declared every
    legacy directory and every maintenance bucket ``public`` (observed live on
    2026-09-02: 12 of 12 universes public, 7 of them maintenance buckets and
    IdP-migration backups; see openspec/changes/archive/2026-09-30-private-by-default-universes/).
    Per the founder, 2026-09-26, the platform does not declare an open level on
    an owner's behalf; the owner exposes their universe explicitly. A universe
    whose ``public_read`` was already ``False`` was declared ``private`` before
    and still is, so nothing regresses — what changes is that boot no longer
    opens anything.

    Returns a map of universe_id -> declared level name for the ones written.
    """
    from tinyassets.daemon_server import (
        ensure_universe_rules,
        register_universe_if_absent,
    )

    base = _base_path()
    ids = universe_ids if universe_ids is not None else _discover_universe_ids()
    written: dict[str, str] = {}
    for uid in ids:
        uid = (uid or "").strip()
        if not uid:
            continue
        # A bare universe dir may have no `universes` row yet; register it first
        # so the rules-row FK is satisfied (universe_rules -> universes). ONLY if
        # absent -- `ensure_universe_registered` is an UPSERT that would otherwise
        # reset an owner's display name to the raw id and wipe registry metadata,
        # on every boot. See `daemon_server.register_universe_if_absent`, which
        # lives beside the UPSERT it guards because `daemon_server` imports
        # nothing from `tinyassets.api` and must not start.
        register_universe_if_absent(base, universe_id=uid)
        rules = ensure_universe_rules(base, universe_id=uid)
        metadata = rules.get("metadata") if isinstance(rules, dict) else None
        if isinstance(metadata, dict) and metadata.get(LEVEL_METADATA_KEY):
            continue  # already declared -> leave as-is.
        set_universe_visibility(uid, PRIVATE.name, source="backfill")
        written[uid] = PRIVATE.name
    return written


def _discover_universe_ids() -> list[str]:
    """Best-effort enumeration of on-disk universe ids for backfill.

    Owned only. This is the step that TURNED a stray directory into a public
    universe: :func:`backfill_universe_visibility` declares a level for every id
    returned here, so an archive the four-name denylist allowed got the
    ``public`` row that made it both listable and readable by id. It also gates
    readiness, so an unowned directory used to hold the startup gate.
    """
    base = _base_path()
    if not base.is_dir():
        return []
    try:
        from tinyassets.api.universe import _is_listable_universe_dir
        from tinyassets.daemon_server import owned_universe_ids

        owned = owned_universe_ids(base)
    except Exception:
        # Fail CLOSED: an unreadable ownership store means nothing is known to
        # be owned, so nothing is declared or gated on. The previous fallback
        # (predicate unavailable -> accept every non-dotted directory) would
        # re-open the leak exactly when the authority could not be consulted.
        logger.exception("ownership lookup failed while discovering command center ids")
        return []
    return [
        child.name
        for child in sorted(base.iterdir())
        if _is_listable_universe_dir(child, owned)
    ]


def is_declared(universe_id: str) -> bool:
    """Whether a universe carries an explicit, recognized ``visibility_level``.

    This is the *declaration* predicate — distinct from the *effective* level.
    An explicitly-``private`` universe is declared (its level resolves to
    ``CLOSED`` by intent); an undeclared universe is NOT declared (its level
    resolves to ``CLOSED`` by fail-closed default). The startup gate uses this
    to tell "correctly private" from "un-migrated undeclared".
    """
    rules = _read_rules(universe_id)
    if not isinstance(rules, dict):
        return False
    meta = rules.get("metadata")
    if not isinstance(meta, dict) or LEVEL_METADATA_KEY not in meta:
        return False
    declared = meta[LEVEL_METADATA_KEY]
    return isinstance(declared, str) and parse_level(declared) is not None


class VisibilityStartupGateError(RuntimeError):
    """Raised when undeclared universes survive the boot backfill."""


def run_visibility_startup_gate() -> dict[str, Any]:
    """Enforceable boot preflight: declare every universe, refuse readiness if any
    stays undeclared.

    The strict fail-closed resolver means an *un-migrated* deployment would serve
    every legacy undeclared universe as ``CLOSED`` — a silent availability
    regression. Prose "run the backfill" instructions are a config-text guard,
    not a runtime gate. This runs the idempotent, deterministic backfill (which
    declares ``private`` — the safe direction, and the only one the platform may
    choose for an owner) at boot, then verifies no undeclared universe remains.
    If one does
    (e.g. a corrupt/unreadable rules row the backfill could not declare), it
    raises loudly with the exact remediation rather than serving wrongly-closed
    universes.

    Returns a summary dict; raises :class:`VisibilityStartupGateError` on an
    undeclared remainder.
    """
    declared = backfill_universe_visibility()
    remaining = [uid for uid in _discover_universe_ids() if not is_declared(uid)]
    summary: dict[str, Any] = {
        "declared_now": declared,
        "declared_count": len(declared),
        "undeclared_remaining": remaining,
    }
    if remaining:
        raise VisibilityStartupGateError(
            "visibility startup gate: "
            f"{len(remaining)} universe(s) remain undeclared after the boot "
            f"backfill and would be served CLOSED: {remaining[:10]}"
            f"{'…' if len(remaining) > 10 else ''}. Inspect the universe_rules "
            "store for corruption, then re-run "
            "`python -c \"from tinyassets.api.visibility import "
            "backfill_universe_visibility as b; print(b())\"` against the data "
            "dir before serving."
        )
    logger.info(
        "visibility startup gate: %d universe(s) declared at boot; none undeclared.",
        len(declared),
    )
    return summary
