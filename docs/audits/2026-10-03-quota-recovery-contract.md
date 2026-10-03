# Quota recovery contract prerequisite

## Status and scope

This tests/docs-only prerequisite preserves the production behavior at base
`8a8ec27532902cd4b61b9886ef194919dcf93e2e`. It does not remove, rename, loosen, skip,
or replace either original grace-budget test. All original test definitions,
fixtures and protected assertions remain unchanged. Two added tests make the
session-startup-before-cleanup ordering explicit.

The original product PR #4403 remains frozen at
`13c52042de24ff94f3f6e0a88cf4090885b3b3b6`. Its test-hygiene block is valid: it
removed two intentional recovery tests while changing product code. This
prerequisite does not clear that block or authorize its zero-growth policy.
No Test-Removal declaration, gate change, label change, receipt fabrication,
queue mutation, production access or privileged local workaround is used.

## Old versus proposed behavior

| Situation | Existing contract | #4403 proposed contract |
| --- | --- | --- |
| Account at quota | Launch can grow by bounded GRACE_BYTES (16 MiB); no reservation | Zero additional growth |
| Positive remaining quota below grace | Launch gets grace-sized growth, even if its reservation is smaller | Zero growth once below the 16 MiB write-headroom carve-out |
| Session/runtime write before cleanup | Small growth may survive the supervisor poll | Any positive measured growth can stop the provider family |
| Direct shell deletes before any growth | Can free space | Can free space |

The old allowance is explicitly intentional in `jail_disk.py`: provider CLI
session files and agent notes let the owner converse and free space. The
provider jail binds runtime credential/session directories read-write, and
`DiskBudget.growth()` includes `.runtime` even though account billing excludes
it. Therefore, proving `tools.bash(..., "rm ...")` alone does not establish that
a provider can start, converse, then invoke the cleanup tool.

The new tests use only temporary files and real budget/accounting functions.
They write a 1 KiB synthetic runtime/session file, force a budget poll before
deleting any retained file, verify that retained bytes are still readable,
then delete the fixture file. Runtime is excluded from billed bytes but remains
in the jail's physical-growth walk. A volume-floor breach must still stop the
full-account case; runtime growth beyond the bound must stop the near-full case.
These are budget-level ordering proofs, not real-provider or complete jailed
startup proofs. Hosted jail evidence remains owned by the integration lane.

The original full-account assertions (exact grace, no reservation, collaborator
number redaction) and near-full exact-grace assertion remain verbatim in their
original tests. The new tests also retain them; nothing is weakened to make the
product patch pass. Existing floor/inode, same-owner reservation, different-user
write isolation, walk-trigger, runtime-limit, renewal and settlement tests are
untouched.

## Policy/design decision required before replacing the contract

Independent contract review returned DISAGREE_EVIDENCE on approving zero growth
as a replacement for bounded provider recovery. That corrects the earlier narrow
reservation/security approval: no new quota expansion did not establish unchanged
agent usability. The code path presents a concrete regression risk; it does not
prove every real provider writes before cleanup or attribute the live incident.

Either:

1. Preserve provider-assisted recovery using separately enforced finite limits
   for billable durable growth and transient runtime, with explicit per-owner
   concurrent allocation and lease/settlement behavior; or
2. Explicitly approve losing provider-assisted recovery when launch headroom is
   exhausted, identify the usable recovery surface, and review the new contract.

Do not simply exclude `.runtime` from the growth walk (unbounded runtime), exempt
parent reservations (double spending), or retain unreserved durable grace beside
the carve-out (spending the same account capacity twice). Keeping grace only at
actually-full usage still leaves near-full and reservation-exhausted providers
unable to perform session setup.

Once that decision and design are accepted, a separate test-contract replacement
can state exactly which legacy allowance is changing. The repository requires
the other model family's review for such removals. The available review here is
independent same-family; the Claude CLI is unavailable. No cross-family review
or removal approval is claimed. Process call-site changes must be coordinated
with integration owner `01a10149-49ab-7403-8295-a337a0e7102c` before implementation.

## Evidence

- Existing/base source plus the additive contract suite: 20 passed.
- Candidate #4403 source plus the two added ordering tests: both fail at the
  pre-cleanup session poll (`storage_limit`, expected no breach).
- All original test ASTs and fixtures equal the base definitions.
- Ruff and diff whitespace checks pass. The original product head is unchanged.
