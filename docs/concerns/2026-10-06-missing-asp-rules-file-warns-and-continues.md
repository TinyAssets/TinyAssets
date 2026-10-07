---
severity: P3
title: A missing data/world_rules.lp warns and continues instead of failing loudly
filed: '2026-10-06'
summary: ASPEngine logs a warning and runs with empty base rules when its rule file is absent, so the constraint check becomes a silent no-op; PLAN.md required a fail-loud startup probe that was never built
---

# A missing `data/world_rules.lp` warns and continues instead of failing loudly

**Filed:** 2026-10-06, carried from `PLAN.md` (§ Module: Providers and
§ Module: Constraints) when PLAN.md was retired (ADR-005).
**Verified:** 2026-10-06 on `922e36d504`.
**Severity:** P3. The ASP engine serves the fantasy domain only.

## Source (verbatim)

From `PLAN.md` § Module: Constraints at `922e36d504`:

> *Required-files probe applies.* `data/world_rules.lp` (or equivalent) must be
> probed at startup — silent absence reducing the engine to a no-op violates
> Hard Rule #8.

## Evidence

- `tinyassets/constraints/asp_engine.py`, `ASPEngine.__init__`: when the path
  does not exist it calls `logger.warning("Base rules file not found: ...")`
  and keeps `self._base_rules = ""`.
- `openspec/specs/constraint-evaluation/spec.md` records that as-built
  behaviour: "It MUST warn and continue with empty base-rule text when the
  configured file is absent."

So the spec is accurate and the old design was never built. It conflicts with
`AGENTS.md` fact 8 (fail loudly).

## What would resolve it

Make an absent default rule file an error (and update the spec), or delete the
ASP engine if no live path needs it. Then delete this file.
