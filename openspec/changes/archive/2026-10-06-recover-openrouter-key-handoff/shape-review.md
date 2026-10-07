# Independent shape verdict — Claude Fable, September 18

Session `0e56edbd-fb91-4385-93e0-7a82d6a3590d`, terminal exit 0 after397s.
Author of proposal: Codex; reviewer: Claude Fable. Lead dispatched the read-only
review. This record is the implementation-author's faithful verdict summary,
not a newly asserted exact-head release approval. The full substantive assistant
verdict was read from the reviewer transcript at
`C:/Users/Jonathan/.claude/projects/C--Users-Jonathan--codex-worktrees-openrouter-handoff-clarity-TinyAssets/0e56edbd-fb91-4385-93e0-7a82d6a3590d.jsonl`.

**ADAPT:** shape correct; one basic safety blocker is inherited deletion/vault
race, requiring the guard and regressions before enabling `deposit_key`.

## Agreed boundary and required adaptations

- AGREE exact `{preset_id,key}` on existing same-origin model-connect ingress,
  reuse `scope` and `complete_bootstrap`, fixed exact endpoint policy, free-model
  candidate and ordinary `ModelAccess("discovered")` approval. No caller owner,
  URL, model or grant selectors. Reviewer did not reverify serve-time selector
  filtering in this pass.
- DISAGREE_EVIDENCE: design4096 exceeds route2048 bound. Use2048 printable ASCII
  and literal `openrouter_user_models_v1` before preset loading.
- AGREE deletion race: writer owner-row commit precedes vault persist, which
  creates the directory; deletion had no shared admission. Admission itself can
  create a directory for later lock takers.
- DISAGREE_CONCERN: do not hold admission across home rename. On Windows the
  open lock handle inside the home prevents rename. Only tombstone write holds
  exclusive admission; release before staging, row sweeps, external deletion.
- Vault writer checks `deleted_principals` for the owner's digest after
  `BEGIN IMMEDIATE` and before DML, while retaining its existing exclusive lock
  through commit and persist. Raise `CurrentHomeChanged`; do not enforce
  founder-home in this shared writer because legitimate non-home admins use it.
- Lock order is admission then SQLite on both sides; deletion never takes the
  gesture lock. No nested provider lock around bootstrap.

## Required proofs

- T1: park writer inside vault persistence; deletion must block until released;
  afterward no secret vault or owner rows remain and tombstone is present.
- T2: after deletion, owned vault write raises without secret file or owner rows.
- T3: witness exclusive lock at tombstone write and no writer at home staging.
  T1–T3 must be red on baseline and green with guard on Windows and Linux.
- T4: concurrent bootstrap calls with different keys have one provisioning
  winner; the vault retains only that winning key.

## Non-blocking observations

Process-local gesture serialization is sufficient only for today's single
daemon process; note the assumption. Lock-created empty ghost homes, inert
post-discovery orphan records, redundant vault mkdir, inherited first-contact
TOCTOU and missing busy timeout are separate hardening. UI design approved;
actual DOM implementation still needed independent exact-head release review.
