# Independent shape review and disposition

2026-09-08, local Windows worktree, Claude subscription peer via
`python scripts/peer_agent.py claude --out output/platform-resource-shape-review-claude.md --prompt-file output/platform-resource-shape-review-brief.md --timeout 600`.
The peer completed successfully. Its substantive response was recovered from
the same invocation's session transcript when its stop-hook replaced the wrapper's
final text. Verdict: **ADAPT**. This is shape review, not approval of subsequent code.

## Adopted

- The proposed new accounting ledger, policy epochs and historical import were
  overbuilt for the observed duplicate starts gate. Reuse existing stores.
- Remove the independent workspace jobs/hour refusal at both admission sites,
  rather than choosing another unexplained larger number. Preserve observations.
- Preserve rolling transfer bytes, lease/pool/storage guards, ownership, locks
  and outbox cleanup. No migration or quota reset.
- Use the existing authorized status seam for observations. Unknown is not zero;
  status must not initialize or migrate the stores it reads.
- Keep broader storage attribution and host-global capacity as explicit open
  work, not implied completion of the owner's simplification request.

## Qualifications / structured disagreement

- **DISAGREE_EVIDENCE:** a quarantined tree's retained size does not establish
  failed network transfer bytes. `workspace_pool` reserves transport maximums;
  interrupted checkout can have transferred compressed or discarded data. Do
  not reconcile an unknown transfer to a small retained tree.
- **DISAGREE_EVIDENCE:** workspace and terminal run records are not necessarily
  one atomic database. The production incident read found universe-local leases
  and root-level terminal run rows. This slice preserves that shape and makes
  no stronger atomicity claim.
- **DISAGREE_EVIDENCE:** existing activity is not just one admitted run: the
  admissions ledger also counts engine mutations. Status must name its actual
  units and scope, not silently change policy or imply all entry-point coverage.
- **DISAGREE_CONCERN:** host-global semaphores and total-storage enforcement need
  separate shape/authority review; neither is implemented by a usage projection.

## Gate history

The shape review required focused tests, Linux evidence, independent exact-head
code review, required CI, deployment receipt and coordinated rendered app-owned
continuation. Engineering gates have since passed (below); Patches and owner
acceptance remain open.

## Code review round 1 — ADAPT

2026-09-08, Claude independent review of committed head
`7da49d00ce57d289bc291437b7afe42e0adf482c` against `f1ea4750`:
`python scripts/peer_agent.py claude --out output/resource-policy-code-review-claude.md --prompt-file output/resource-policy-code-review-brief.md --timeout 600`.
Completed in 598 seconds. Substantive response recovered from the invocation's
session transcript; the wrapper retained its closing ADAPT verdict.

**Confirmed blocker:** the new reader refused quiescent WAL-mode stores without
sidecars, suppressing valid admin usage. Production connection factories close
their connections; the earlier integration fixture masked that normal state.
The workspace jobs-gate removal, retained safeguards, privacy boundary, units,
plugin parity and bookkeeping correction otherwise received AGREE.

**Correction:** use SQLite's ordinary read-only, query-only connection for both
quiescent and live WAL databases. It may create normal coordination sidecars,
but cannot create a missing database, change schema or mutate records. Added
production-factory quiescent tests, prohibited SQL-write tests and a concurrent
ACL-revocation test. Unreadable ACL checks now emit a sanitized warning.

**DISAGREE_EVIDENCE with the suggested immutable workaround:** a missing WAL
before connection does not establish immutability for the duration of a read;
a writer can start immediately after the check. SQLite's
[URI documentation](https://www.sqlite.org/uri.html) warns that immutable reads
disable locking/change detection and can return incorrect results if the file
changes. Its [WAL documentation](https://www.sqlite.org/wal.html) permits normal
read-only WAL access with coordination sidecars. Do not risk stale ACL reads to
avoid those internal lock files. This is an explicit clarification of the
read-only contract, not permission to write user data or bootstrap schemas.

The nonblocking duplicate engine-subcap formula is now shared by enforcement,
status and refusal wording. Five unknown 4 GiB failed checkouts can still consume
the existing 20 GiB transfer window; that pre-existing conservative accounting
remains open, not silently reconciled from retained-tree size. Remaining stale
as-built quota text will be synced with deployment, not prematurely represented
as shipped.

## Code review round 2 — APPROVE

Independent Claude review of exact head
`79a4f7650f1c3307af76ecb522819b0c3212a9b7` completed in 344 seconds:
`python scripts/peer_agent.py claude --out output/resource-policy-code-review-round2-claude.md --prompt-file output/resource-policy-code-review-round2-brief.md --timeout 480`.
The peer found no introduced blocking issue and independently checked the
quiescent/live WAL behavior, read-only SQL restrictions and ACL-race regression.
[Full review and disposition](https://github.com/Jonnyton/TinyAssets/pull/3560#issuecomment-5594942162).

Required Linux CI tested an identical full tree; deployment, public canary and
protected revision containment passed for merge `0eb1388f` on September 9 UTC.
Exact evidence (archived in git).
Rendered acceptance remains owner-held, not inferred from these gates.
