"""Context-budget invariant: the rulebook only shrinks.

Wraps `scripts/check_context_budget.py` under the Invariant contract. Every
rulebook file is pinned at its post-cut byte size (`AGENTS.md`, `CLAUDE.md`, and
the three `docs/reference/` procedures); lowering a pin is allowed, raising one
is the edit this invariant exists to make visible.

**This one blocks.** It was propose-only until 2026-08-25 on the reasoning that
the always-loaded set is host-managed, so a bust should surface drift for a
human to curate rather than stop a commit. The result: the invariant sat
registered and VIOLATED while the set grew from ~17.6 KB (2026-04-28) to
62,082 B, and nothing noticed, because nothing ran it and nothing failed when
it did. Measurement without a pawl is not a ratchet.

Ceilings are HARD and set just above the achieved values (see
`scripts/check_context_budget.py`). No auto-heal: deciding WHICH content moves
to `docs/reference/` is editorial, so a human does it. Basis:
`docs/architecture.md` and
`docs/architecture.md`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from . import CheckResult, Invariant, Status

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BUDGET_SCRIPT = REPO_ROOT / "scripts" / "check_context_budget.py"


def _load_budget_module():
    spec = importlib.util.spec_from_file_location(
        "check_context_budget_for_invariant", BUDGET_SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class ContextBudgetInvariant(Invariant):
    name = "context-budget"
    description = "Rulebook files stay at or under their pinned byte size."
    pre_commit_scope = True  # blocks: a budget that only warns is what let 17.6 KB become 62 KB
    poll_interval_s = None  # on-demand
    auto_heal = False  # no auto-heal: which content to move is editorial, so a human decides

    def _check(self) -> CheckResult:
        if not BUDGET_SCRIPT.exists():
            return CheckResult(
                status=Status.SKIPPED,
                message=f"check_context_budget.py not found at {BUDGET_SCRIPT}",
            )
        mod = _load_budget_module()
        results, combined, hard_busted, imported, missing = mod.run(REPO_ROOT)
        hard_over = [r.path for r in results if r.kind == "hard" and r.over]
        soft_over = [r.path for r in results if r.kind == "soft" and r.over]
        evidence = {
            "combined_bytes": combined,
            "combined_hard_bytes": mod.COMBINED_HARD_BYTES,
            "hard_over": hard_over,
            "soft_over": soft_over,
            "imported": imported,   # @-imports counted toward COMBINED
            "missing": missing,     # configured always-loaded files that vanished
        }
        if hard_busted:
            reason = ", ".join(hard_over) or (
                f"missing: {', '.join(missing)}" if missing else "combined over ceiling"
            )
            return CheckResult(
                status=Status.VIOLATED,
                message=(
                    f"{reason} grew past its pin -- the rulebook only shrinks, so "
                    f"displace an old rule instead of adding one; always-loaded "
                    f"total {combined} bytes (ceiling {mod.COMBINED_HARD_BYTES}). "
                    f"Run: python scripts/check_context_budget.py"
                ),
                evidence=evidence,
            )
        msg = f"always-loaded {combined} bytes; no HARD budget exceeded"
        if soft_over:
            msg += f" (soft target over: {', '.join(soft_over)})"
        return CheckResult(status=Status.OK, message=msg, evidence=evidence)
