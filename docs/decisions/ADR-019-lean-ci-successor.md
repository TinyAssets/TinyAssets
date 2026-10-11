# ADR-019: Lean CI on the affected path

## Status

Accepted. Supersedes the pipeline design in ADR-003; ADR-003 remains the history of the first required test gate.

## Date

2026-10-11

## Decision

The PR and merge queue run affected tests, structural guards and scoped proofs. The full required suite runs after each merge to main, and a red main is surfaced automatically. Heavy tests run on their scheduled path. The gate never silently skips the tests it selected. Platform release builds do not consume routine PR runners. This keeps the merge path short without treating an unrelated pre-existing failure as a regression.

The current executable contract is `.github/workflows/tests.yml`, `scripts/affected_tests.py`, `scripts/ci_required_tests.py` and `scripts/main_red.py`; the as-built specification lives in `openspec/specs/development-coordination-runtime/`.
