---
severity: P2
title: IdP subject ids are persisted in 24 columns across 5 databases
filed: '2026-08-29'
summary: a tenant identity change is a hand migration today, and there will be another
---

# P2 - IdP subject ids are persisted in 24 columns across 5 databases

**Filed:** 2026-08-29
**Severity:** P2 -- a tenant identity change is a hand migration today; there will be another

## What happened

The WorkOS staging -> production move (2026-08-29) gave every user a new `user_...`
subject. A sweep of every sqlite db under `/data` found the OLD subject in **1,561 rows across
~45 columns in 5 databases** (`.tinyassets.db`, `outbound.db`, `.runs.db`, two
`.effector_consents.db`). Restoring one founder took: `founder_home` + `universe_acl`
re-point, 402 `conversation_turns` re-keys, a 104-row ownership migration
(`branch_definitions.author`, `agent_bindings.created_by`, `llm_credential_deposit_owners`,
`goals.author`, non-Pipes `outbound_connections` + grants, `branch_schedules.owner_actor`), and a rebuild of both
serving assignments through `bind_serving_provider` because custody / assignment digests
hash the owner id. Provenance columns (`contribution_events.actor_id`,
`branch_versions.publisher`, `action_approvals.decided_by`, `effector_consents.granted_by`,
...) were left as history, so attribution is now split across two ids for one person, and the
16 background-automation bindings still authorize the old principal until re-issued.

Verdict and boundary: `2026-08-29-codex-subject-migration-boundary.md (archived in git)`.

## Why it matters

Every store that persists the raw IdP subject is a store that breaks on an issuer
change, a subject rotation, or account linking (`simkalholdingsllc` and the founder are
one person with two subjects today). The digests are correct to refuse a rewrite -- the
problem is that ownership has no single home to re-point.

## Shape that fixes it (Codex, agreed)

Resolve `(issuer, subject)` **once at authentication ingress** to a stable internal
`principal_id`, backed by an `external_identities` table (one principal, N subjects).
Operational stores persist the `principal_id`; historical rows keep the subject they
were written with, and aliases serve attribution/display. Not "every resolver consults
a `subject_aliases` table" -- that re-scatters enforcement. Retire the staging issuer
explicitly (done by construction: the daemon only trusts the production JWKS).

Authority/storage-shape change -> needs an OpenSpec change before code.
