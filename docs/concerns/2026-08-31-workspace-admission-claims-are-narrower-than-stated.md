---
severity: P2
title: Workspace admission is narrower than the claims made for it
filed: '2026-08-31'
summary: 'four findings from a Codex REJECT on #2742, all pre-existing in the workspace primitive but falsifying the claims that justified the widening'
---

# The workspace admission model is narrower than the claims made for it

**Filed** 2026-08-31 from a Codex refute review of PR #2742, which returned
`REJECT` with five findings. One (self-grantable consent) was fixed in that PR.
These are the rest: each is **pre-existing in the workspace primitive** (#2732),
not introduced by the allowlist widening — but the widening was justified by
claims these falsify, so they are written down rather than dropped.

Severity **P2**: all of it is reachable only from a vetted-founder universe
today (`run_graph_allowlist` gates `source_channel`, and the served surface
gates the rest), and none of it reaches credentials or another universe's data.
The gate is meant to widen, which is what makes these worth fixing first.

## 1. "One job at a time per universe" is true ACROSS runs, not WITHIN one

`_acquire_lock` (`tinyassets/workspace_pool.py:465-495`) returns silently when
the existing row's `run_id` matches, and both `SCOPE_UNIVERSE` and `SCOPE_HOST`
key on `run_id` (`:639-650`).

This is **deliberate and load-bearing**: a run must check out and then push, and
a non-reentrant lock would make the second operation impossible. The docstring
says so.

What it does not do is bound concurrency *inside* one run. Two workspace effect
nodes scheduled in the same superstep both pass admission, so one run can hold
several simultaneous clones. There are no structural caps on graph size (founder
2026-08-30, deliberately), so the number is not bounded by the shape either.

**Verified:** the reentrancy and the shared key, by reading the code.
**Not verified:** that the compiled graph actually schedules two workspace nodes
concurrently. Codex cited `graph_compiler.py:3064-3067`. Confirm with a real
two-checkout graph before designing a fix — the fix differs depending on whether
the right bound is per-run, per-lease, or a semaphore under the reentrant lock.

## 2. The byte ledger is accounting, not enforcement

`DEFAULT_LEASE_BYTES_CAP` is 4 GiB and the operation's maximum is reserved
before the wire, then `reconcile_bytes` (`workspace_pool.py:737-760`) measures
and **clamps** rather than refuses. Admission for the next operation reads a
filesystem measurement of prior usage.

Nothing measures the tree while the node is writing to it. Inside the jail the
only disk bound is per-file `RLIMIT_FSIZE`, 512 MiB
(`tinyassets/node_sandbox.py:209`). A single `ws.run` writing many distinct
files stays under every per-file limit and past the lease cap; the host notices
at the *next* admission, by which time the disk is already consumed. On a
1 vCPU / 2 GiB droplet that is an availability risk, not a rounding error.

**Verified:** the cap is a reservation, the reconcile clamps, `RLIMIT_FSIZE` is
512 MiB, and no code in `workspace_pool.py`, `effectors/workspace.py` or
`workspace_git.py` measures the tree mid-run.

## 3. A failed push can leak staging and keep the full charge

Codex: for a push packet naming a missing `.tiny-export/<sha>.bundle`,
`_staging_root` creates disk state before the reservation
(`effectors/workspace.py:1219-1233`), and the copy failure exits without
reconciling or cleaning up (`:1237-1252`); the unreconciled maximum stays
charged for the hour by design (`workspace_pool.py:873-880`). Repeated failures
would consume the universe's hourly quota and leave staging directories behind.

**Not verified.** Reproduce before acting: drive a push whose bundle is absent
and check both the ledger row and the staging directory.

## 4. Effects may be re-dispatched after a crash

Codex: dispatch completes before the durable event and the node checkpoint
(`graph_compiler.py:3192-3203`), so a kill between the two leaves the prior
checkpoint to re-execute the node and dispatch a second workspace operation
under the same run.

This matters more than the others because it interacts with #1: a re-dispatch
reuses the same `run_id` and therefore passes the reentrant lock. It also
interacts with a known live behaviour — every merge auto-deploys and recreates
the container, killing in-flight turns
([[deploy-kills-in-flight-turns]] / the resolved 2026-08-29 deploy-kills-turns concern),
so the crash window is not hypothetical here.

**Not verified.** Needs a kill-at-the-right-moment test, which the Linux oracle
can host.

## How to work this

### 2026-09-08 review note: holder identity in busy details

Independent Claude admission-evidence shape review reported: "The existing busy
refusal detail already discloses the holding run's id, and for the host slot that
can be another universe's run." `_acquire_lock` still includes `held by run` in
its detail; the adapter passes it through. The string is verified, but the
cross-universe claim is **conditional/unverified**: production adapters use each
universe's runs database. Establish shared-database reachability before calling
this a cross-user disclosure or changing the contract. No new identity fields
are added by `workspace-admission-evidence`.

### 2026-09-08 receipt follow-up

The additive observation slice is deployed as `0e485ba0add1` (PR #3442).
Rendered app retest at 08:48 UTC independently marked contention recovery PASS
from two conflicts followed by successful retry. Retry exhaustion remains OPEN;
no post-fix organic receipt use is visible in that conversation. This is not
proof of fairness or closure of the older admission findings above. Independent
exact-head review also suggested a direct effector test with a real reconciliation
sweep clearing a stale holder (2 attempts, 1 conflict, no sleep); the shipped
coordinated lock-release tests stub that sweep. This is nonblocking test debt,
not a demonstrated runtime failure. Review:
`docs/reviews/2026-09-08-workspace-admission-exact-head-claude.md`.


Do **not** open another review round on the same code — that cap exists for a
reason and this is round one of three. Take these in order 2, 4, 1, 3: the disk
bound is the one that can take the host down, and the re-dispatch one is the one
whose trigger already happens weekly. Each wants a failing test first, on the
Linux oracle where the jail is real.

Correct the claims in `_sanitize_served_branch_spec`'s comment when the fixes
land — it currently states the per-universe concurrency bound without the
within-run qualification.
