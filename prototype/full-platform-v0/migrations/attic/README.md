# Attic — retired migrations

- `002_rls.sql` — DEAD: references `concept_visibility`, which `001_core_tables.sql`
  never created (deferred to Track L). It cannot apply to this schema; `003_rls.sql`
  is the superseding rewrite. It remains outside `migrations/` because the
  replay-safe runner rejects non-gap-free active history and must never execute
  a retired file.
  Validated on vanilla Postgres 16, chain 001 → 002_flags → 003_rls → 004 → 005 →
  006 → 007 → 008 → 009 (see `WORK-2026-07-11-DRIVING-SESSION.md (archived in git)` §3).
