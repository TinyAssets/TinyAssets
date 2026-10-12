"""Soul-scoped effect-authority resolution for external-write effectors.

Gap 1 of the souled-universe self-maintenance model (design note
``openspec/specs/universe-lifecycle-and-soul/spec.md``). The
running universe's soul is the SOURCE of effect-authority: it declares which
real-world hands (``<sink>:<destination>`` grants) the universe's founder
authorizes. The secret/token comes from the owner's own connection (the
platform holds no push credential; the env capability map was retired
2026-09-24); the soul carries the *authority decision*.

Transitional contract (this scaffold):

- declared + match    -> ``AUTHORIZED``  (proceed to capability/consent gates)
- declared + no match -> ``DENIED``      (fail closed; effector returns dry-run)
- nothing declared    -> ``UNDECLARED``  (legacy fall-through to env + consent)

The cutover (post-review, after Tiny's soul declares its grants) removes the
``UNDECLARED`` fall-through so a soul grant becomes strictly required. Until
then, ``UNDECLARED`` preserves the pre-existing env-capability + consent
behavior so no live universe breaks. This module is effect-sink-agnostic;
any effector resolves authority the same way.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

AUTHORIZED = "authorized"
DENIED = "denied"
UNDECLARED = "undeclared"


def effect_authority_key(sink: str, destination: str) -> str:
    """Canonical ``<sink>:<destination>`` grant key, both sides stripped."""
    return f"{(sink or '').strip()}:{(destination or '').strip()}"


def resolve_soul_effect_authority(
    universe_dir: Path | None,
    sink: str,
    destination: str,
) -> str:
    """Resolve a (sink, destination) against the running universe's soul.

    Returns one of ``AUTHORIZED`` / ``DENIED`` / ``UNDECLARED``. Never raises —
    authority resolution must not break the effector path. Unexpected failures
    fail closed (``DENIED``) rather than silently permitting a real write.
    """
    if universe_dir is None:
        # No universe context (e.g. Phase-1 backward-compat invocations).
        # Nothing to resolve against; defer to the legacy gates.
        return UNDECLARED
    try:
        from tinyassets.universe_soul import effect_authority_from_soul

        grants = effect_authority_from_soul(universe_dir)
    except Exception:  # pragma: no cover - defensive; soul read is OSError-safe
        logger.exception(
            "soul effect-authority lookup crashed for %s; failing closed",
            destination,
        )
        return DENIED
    if not grants:
        return UNDECLARED
    return AUTHORIZED if effect_authority_key(sink, destination) in set(grants) else DENIED


__all__ = [
    "AUTHORIZED",
    "DENIED",
    "UNDECLARED",
    "effect_authority_key",
    "resolve_soul_effect_authority",
]
