# Independent shape review

2026-09-08, Claude subscription peer, 202 seconds, exit 0:
`python scripts/peer_agent.py claude --out output/storage-observation-shape-claude.md --prompt-file output/storage-observation-shape-brief.md --timeout 480`.

Verdict ADAPT; shape AGREE. Four design-level adaptations adopted before build:
exclude cleanup-confirmed AVAILABLE scratch rows and treat derived-leaf ENOENT
as absence; enumerate reasons without exception text; validate lease IDs and
non-negative generations; normalize TTLMemo's timed-out None to contention.
The review explicitly permits building after these adaptations, with no further
shape round. Concrete bounds, scope/non-atomic caveats and workspace quarantine
classification were also added. Forged-authoritative-row size attribution is a
recorded residual, not a new authority mechanism in this slice.

## Exact-head implementation review — APPROVE

Claude reviewed `bf39c4478bb368fd5724270e8a151e3295957cbf` against
`20ae3883e614e9b4ad7911375146fdfd0066aa4c`, 431 seconds, exit 0:
`python scripts/peer_agent.py claude --out output/storage-observation-code-claude.md --prompt-file output/storage-observation-code-brief.md --timeout 480`.
No introduced blockers; approval is contingent on all 21 new POSIX-specific tests
passing Linux. Independent Windows run: six passed, 21 skipped, not traversal
proof. [Durable exact-head receipt](https://github.com/Jonnyton/TinyAssets/pull/3568#issuecomment-5595344630).

AGREE on path/ownership scope, no content reads or private identifiers, work/cache
bounds, partial/unknown semantics, unchanged quota and authority, descriptor
cleanup, directory replacement/symlinks, inode deduplication, scan gate and current
admin gate before cached observations. Plugin mirror exact at the reviewed head.

Nonblocking follow-ups: unavailable observations can remain cached for the TTL;
an early invalid-root response omits cache age; symlinked data-root ancestors are
unsupported; dir-to-symlink changes use the general unreadable reason. Narrowing
the lease-ID format would reduce the existing forged-authority-row residual but
must not silently contradict canonical IDs. No follow-up is an introduced blocker
or a reason for another review of unchanged code.

Linux condition satisfied: run 34306784859 passed all 27 new tests, including all
21 POSIX cases, plus the new admin-cache regression. Actual checkout 2c321ed5 has
the identical full tree to approved bf39c447. The affected suite has 181 passes
and two unchanged Windows-only skips, no regression or missing case against its
baseline. [Reconciliation](https://github.com/Jonnyton/TinyAssets/pull/3568#issuecomment-5595397012).
PR #3568 merged as cb74e216. Deploy 34308081219 passed authenticated public
canary and revision containment at 2026-09-09 03:42 UTC. No broader policy or owner
acceptance is inferred. Approval was published without changing the reviewed head;
this record is same-lane documentation follow-through. [Release proof](../../../docs/reviews/2026-09-08-attributable-storage-proof.md).
