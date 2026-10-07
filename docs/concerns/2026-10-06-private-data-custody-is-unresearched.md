---
severity: Watch
title: Private-data custody beyond the platform-held box is unresearched
filed: '2026-10-06'
summary: The founder kept custody open on purpose (host machine, private brain, vault, platform-held); only the platform-held sealed box is being built, and no lane has researched the other modes against real use cases
---

# Private-data custody beyond the platform-held box is unresearched

**Filed:** 2026-10-06, carried from `PLAN.md` § Open Tensions when PLAN.md was
retired (ADR-005).
**Severity:** Watch. Nothing is broken; the risk is a lane quietly settling the
question.

## Source (verbatim)

From `PLAN.md` § Open Tensions at `922e36d504`:

> **Private-data custody is an open research question, deliberately.** Custody
> is per-situation and user-chosen (host machine / private universe brain /
> vault / platform-held) and no lane may treat either the never-store or the
> platform-store position as settled. This tension stays open on purpose until
> the custody modes are researched against real use cases; see Scoping Rule 4.

## Where it stands

- The sealed-box target architecture (ADR-012) stores a command center's files
  on a platform-held box disk. That is one custody mode, built first.
- Export (ADR-018) keeps the user able to leave with everything, which is the
  no-lock-in half of the original concern.
- `docs/design-notes/2026-04-18-full-platform-architecture.md` §17 is research
  input for another mode, neither canonical nor retracted.

## What would resolve it

A recorded decision (an ADR) on which custody modes a user can pick, after
researching them against real use cases. Then delete this file.
