# Distributed execution platform

> Current-main change surface for the complete distributed-execution program.
> The detailed anti-loss plan remains
> `2026-07-18-distributed-execution-platform.md (archived in git)`.
> This change preserves the complete V1-V8 destination, S0-S16 stage map, and
> B01-B44 backlog while making the next apply slice deliberately dark and
> test-only.

## Why

`origin/main@405a7b7e` contains the backend-neutral `runner/v1` seam and detached
sandbox diagnostic from merged PR #1485. It does not contain the signed-record
authority foundation, B2 job/lease/result protocol, real sandbox backend,
production trust root, authenticated execution route, or live owner-daemon
path described by the older distributed-execution branches.

The prior ledger confused work on open stacked PRs with work landed on main.
Those PRs are now stale relative to current main and overlap hundreds of
unrelated files. Merging or rebasing them wholesale would overwrite newer
operator-request, runtime, and security work. In particular, merged PR #1697
now publishes trusted epoch-2 worker descriptors that must be preserved.

The recurring authority defect remains valid: mutable rows, projections,
events, receipts, and queue claims can be forged or restored and therefore
cannot create positive execution authority. Positive authority must be
re-derived at the decision point from one of three honest mechanisms:

- M1: canonical, domain-separated platform signatures.
- M2: fresh content-address or exact-object re-derivation.
- M3: fresh verification by the external authority.

These mechanisms share a sealed `Verified[T]` evidence shape but never share an
authority constructor or promote evidence between mechanisms.

## What Changes

### Immediate first apply slice: dark signed-authority spine

The first apply slice builds the smallest final-shaped authority spine behind a
test-only fake composition root:

1. immutable domain contracts and canonical signed capsule, grant, candidate,
   and terminal-record carriers;
2. a sealed `Verified[T]` mint seam with mechanism-specific M1/M2/M3 minters;
3. generation floors, fences, idempotency, and verify-first replay semantics;
4. decision-point blob proof plus one physical-root-then-SQLite lock order; and
5. a fake signer/verifier/composition root usable only by focused tests.

The fake root must fail hard in production configuration. This slice adds no
real provider, money, credential, `run_graph` bridge, queue integration,
production route/backend, deployment, GitHub effect, live enrollment, or
market behavior. Passing it proves only the dark contract spine; it does not
satisfy V1 or any later vertical slice.

### Complete destination remains in this change

The delivery order remains:

- V1: authenticated signed-completion spine over a real persisted job.
- V2: confined owner-daemon execution and single B2 cutover.
- V3: exactly-once reviewable GitHub PR effect, never approval or merge.
- V4: first live B2 user path, authority gates, and load proof.
- V5: source-exec breadth and private delivery.
- V6: public-source market execution over unchanged B2.
- V7: live B3 path and private-market policy.
- V8: protected GitHub merge and adjacent authority closure.

Every S0-S16 obligation and every B01-B44 backlog item remains represented in
`tasks.md`. "Not in the first slice" never means "not in the program."

### Authority boundaries preserved

Admission receipts, epoch-2 queue rows, internal scheduling leases,
provider-attempt receipts, and B2 signed grants/leases are distinct artifacts.
They may narrow or reject one another where an explicit contract says so, but
none may mint, widen, substitute for, or promote another's authority. The
server-derived #1697 worker descriptor remains an epoch-2 eligibility and
scheduling fact, not an owner, provider, credential, payment, execution, or
result grant.

### Current-main extraction instead of stale-branch integration

Useful contracts and mutation tests are extracted onto current main in explicit
dependency order from #1472, #1477, #1479, #1481, #1487, #1491, and #1478.
Each extraction begins with a current-main failing test and takes only the
smallest reviewed behavior. No stale PR is merged, rebased, or cherry-picked
wholesale. PR #1572 is excluded: it implements a different, design-gated M2
branch-version change and deliberately breaks legacy identifiers; it is not a
source for this change.

## Capabilities

### Modified capabilities

- `distributed-execution`: extend the canonical as-built runner/diagnostic
  capability with staged future requirements for the dark authority spine and
  complete V1-V8 program. This is not a new capability and does not rewrite or
  claim already-unbuilt behavior as current truth.

### New capabilities

- None.

## Impact

The first apply slice is limited to new or newly extracted authority-domain
modules and focused tests selected by a future implementation claim. It must
not wire production callers.

Later slices affect execution runtime/storage, daemon authentication and
enrollment, B2 transport, graph checkpoint integration, blob storage,
sandbox backends, owner-daemon coordination, GitHub effects, market settlement,
CI authority gates, deployment configuration, and rendered live acceptance.
Those surfaces remain separately claimed and review-gated before implementation.

No live route, deployment, production key, credential path, provider path,
queue claim path, GitHub mutation, or money path changes merely because this
proposal is approved.
