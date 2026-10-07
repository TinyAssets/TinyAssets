# ADR-017: Knowledge Is Files; the OKF Bundle Is the Default Form

## Status

Accepted. The bundle write path is not built.

## Date

2026-07-25

## Context

"Postgres-canonical or file-canonical?" was asked as one global question. It
resolves by scoping each domain to the store that fits it.

## Decision

- For the commons, and as the default organization of a command center's
  brain, the canonical knowledge form is an OKF bundle: markdown files with
  YAML front matter, one file per entry, cross-links as the graph, and
  `okf_version` at the bundle root. SQLite, full-text and vector stores over it
  are derived, rebuildable indexes; the bundle wins when they disagree.
- OKF is the default, not a mandate: a user may design their own brain
  organization, and it keeps the same source-versus-index split.
- Postgres is canonical only for the transactional domains (ADR-012). GitHub is
  an export sink for those, not their store.

## Consequences

- New command centers are seeded as an OKF soul bundle
  (`tinyassets/universe_bundle.py`).
- The only export is unwired: `docs/concerns/2026-10-01-okf-export-is-unwired.md`.
- Decision record: `openspec/changes/archive/2026-07-25-brain-okf-canonical-store/`.
