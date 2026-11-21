---
severity: P1
title: Workspace cap correction awaits owner acceptance
filed: '2026-09-08'
summary: duplicate ten-start gate removed and scoped usage shipped with authenticated deploy proof; owner-held rendered acceptance and broader consumption-policy simplification remain open
---

# Workspace hourly-cap correction awaits owner acceptance

**Current state (2026-09-09 02:55 UTC):** the duplicate ten-start refusal is
removed in deployed merge `0eb1388f`; authenticated public canary and protected
revision containment passed in deploy 34305023434. The owner-facing usage
projection is also shipped. [Release evidence](../reviews/2026-09-08-workspace-resource-policy-proof.md).
This concern remains open for owner-held rendered acceptance and the broader
consumption-policy gaps below. Historical values and diagnosis below describe
the pre-fix deploy, not current configuration. Do not send the app another test
message or run workflows without a fresh explicit user request.

**Storage follow-through (03:42 UTC):** scoped retained-file observations shipped
as `cb74e216`, with authenticated deploy proof. [Evidence](../reviews/2026-09-08-attributable-storage-proof.md).
The original measurement recommendation below is implemented, not fresh queued
work. It adds measured local/lease-attributed footprint, not full storage
attribution, new quota policy or rendered acceptance. Those remaining gaps stay open.

**Filed:** 2026-09-08 (America/Los_Angeles).
**Verified:** 2026-09-09 UTC, production receipt
`49dc655fdb11e58d7e6682f7b41e111d042cf246`, deployed 2026-09-08T22:31:43Z;
rendered owner conversation through September 8, 18:07 PDT, then read-only
production SQLite and deployed-source inspection.
**Severity:** P1 — normal diagnostic work is refused despite free workspace slots.

## Source and user direction

The owner asked why light use still hits limits after the previous increases.
The app's 18:07 PDT response said:

> I haven’t verified a fix end to end or changed production. My checks hit tooling limitations and then the hourly workspace limit. I removed my temporary diagnostic workflows.

It corrected the older single-use-carrier failure to September 1 and said it had
no evidence that old error was still happening. This is not a new reproduction
of that older provider failure.

The earlier directive is preserved verbatim in commit `d23ca9e5` (PR #2770):

> in all my usage of the platform I have never so far ever used it a lot, so
> use that as reference for all limits on users and raise them accordingly.

New September 8 direction, relayed by the owner's Commander task: simplify
limits around actual consumption of platform dependencies, with understandable
activity and total-storage limits instead of many independently chosen levers.
Concurrent actions and actions per hour were examples, **not approved numerical
thresholds**. Concurrency and hourly throughput must remain distinct. Consolidate
redundant controls while retaining internal protections for other users and the
host. This records direction, not an approved replacement design or a PLAN edit.

Workflow authorship stays with the app's agent. The owner clarified:

> but you dont do it with your own hands, you do it as a user talking to your
> agent like a user would

This investigation sent no app messages, changed no private workflows, and
modified no production state.

The owner subsequently clarified that **Patches remains open**. The app's
rendered checklist, repository-write and heartbeat-cleanup successes are scoped
evidence, not owner acceptance of the whole lane. This incident and the
simplification work must be resolved before claiming completion.

## Exact failure and accounting

Run `ad0fe689da1b46ec` started at **2026-09-09T01:06:17.061Z** and failed at
01:06:17.436Z with `workspace_quota_exceeded`. Its error, with the universe
identifier replaced by an alias, was:

```text
workspace jobs per hour (10) exhausted for <target-universe>: 10 charged, clears_at=1788918551.6590228
```

This is a **rolling-hour throughput refusal**, not `workspace_busy` and not an
upstream LLM quota. Four admitted scratch-workspace checklist jobs at about
00:49Z and six admitted diagnostic attempts at 01:02–01:05Z account for all ten
job charges. The first charge ages out at **01:49:11.659Z** (18:49:11.659 PDT).
That is the first possible release, not a promise of unlimited capacity then.

| Group | Admitted run IDs | Hourly job charge |
|---|---|---|
| Checklist | `afb4f2f0c94d4ee8`, `fa061f73d0ef49f9`, `7f580f270df4453e`, `eb35cb08d7f04427` | 4 |
| Diagnostics | `dffaab53e47c4a50`, `4d1df98bfbf34bfe`, `62889e9dad70487a`, `4cef6f74efd24a1c`, `13285407bdb7474a`, `3620a566a2e248bd` | 6 |

All ten leases were `AVAILABLE`; the target universe had no current lock rows.
Their recorded release times precede the quota refusal. The checklist's busy
refusal, `200df7f1a9d648cb`, has no workspace ledger charge. These records show
real admitted attempts, **not leaked concurrency slots or repeated busy retries
being billed as fresh jobs**.

One failed checkout (`13285407bdb7474a`) retained a conservative **4 GiB** hourly
byte reservation (`reserved=1`, no measured byte value), despite its lease being
released. The other diagnostic charges were roughly 45.8 MB each; the four
scratch jobs had zero measured bytes. Keeping an unknown transfer's maximum is
the existing spec, not evidence that 4 GiB was actually transferred or retained.
It did **not** cause this jobs-per-hour refusal, but must be considered when
separating actual consumption, reservations, throughput, and retained storage.

## Historical diagnosis values versus prior increases

| Control | Confirmed deployed value | Meaning |
|---|---|---|
| Workspace jobs | 10 / universe / rolling 3,600 seconds | Independent starts/operations gate; source calls do not override it |
| Workspace hourly bytes | 20 GiB / universe / rolling hour | Reservations settled to measurement when known; not total retained storage |
| Default lease reservation / pool cap constants | 4 GiB / 20 GiB | Source defaults, not proof of a host-global disk bound |
| Permanent workspace storage | 16 GiB per universe | `_universe_quota_kwargs` supplies a hard-coded filesystem-measured quota, not a wired commercial tier |
| General write-run / total admission limits | 300 / 900 per universe per rolling hour | At diagnosis, the internal engine-write share derived as 600; #3577 removes that separate share while retaining 300/900 |
| Effect-node dispatches / delivered-result bytes | 5,000 / 2 GiB per universe per rolling hour | A third activity-budget family in the engine admissions DB; workspace nodes consume its dispatch counter too, while workspace transfer bytes remain separate |
| Provider binding token / cost ceilings | 4,000,000 / 400,000,000 microunits | Separate provider authority capacity, not workspace starts or upstream entitlement |

PR #2770 raised the general limits to 300/900 and several other per-user caps,
but did not edit `tinyassets/workspace_pool.py`. The separate
`raise-served-concurrency-budget` change raised provider ceilings. Both increases
are present in deployed source; neither lifts the ten-workspace-jobs gate.

The generic effect budget implementation already landed in PR #2731 (`98b48964`);
At diagnosis, `run-usage-budgets`' unchecked task file was stale completion
bookkeeping, not an unbuilt implementation; that bookkeeping is now archived.
The hourly dispatch/byte values were independently
confirmed in deployed source too. The separate `usage_policy` / `usage_ledger`
tier meter is not the live refusal source: its enforcement was confirmed **off**
in production. Do not enable it as a shortcut to consolidation; its own module
documents unresolved crash/settlement accounting.

Push and discard each also reserve one workspace job (`_reserve_operation` in
`effectors/workspace.py`, `reserve_operation` in `workspace_pool.py`). Thus a
checkout/push/discard sequence spends three jobs; even zero-byte discard can hit
the starts cap. `ledger_usage` currently has no production caller, so the agent
learns about the bound from a refusal rather than an accessible usage snapshot.

Further source verification corrects the common description of the third
budget as HTTP-only: `dispatch_node_effects` consults and increments its counter
once per node with effects, before iterating sinks. It does not exempt workspace
nodes. `_bytes_moved` sums only results marked `delivered=True`; workspace results
do not report that field, so their transfer bytes are not charged there. A node
with several sinks is one generic dispatch, not one charge per network request.
Synthetic, non-production proof on Windows: `python -m pytest -q
output/test_workspace_dispatch_accounting_probe.py` => 1 passed (September 9
UTC); a fake workspace adapter left generic window usage `(1, 0)`. No private
branch, workflow or live workspace was created. The interpretation is important
for consolidation: one physical operation can already cross two count gates.

`tinyassets/engine_admissions.py`'s introductory 20/60/40 numbers and portions of
`openspec/specs/engine-run-admissions/spec.md` still describe older values. Do not
use them as current configuration evidence. The current callers are
`tinyassets/engine_mcp_server.py`'s `_RUN_GRAPH_RATE_MAX`,
`_RUN_GRAPH_TOTAL_MAX`, and `_engine_run_admit`.

## Re-verifiable mechanism and evidence collection

- Historical deployed `tinyassets/workspace_pool.py` at the receipt above:
  `DEFAULT_JOBS_PER_HOUR`, `WINDOW_S`, `admit`, `_ledger_sum`,
  `_window_clears_at`, and `reserve_operation_bytes`. Admission checks
  the per-universe rolling sum before transport, within its immediate transaction.
- `tinyassets/effectors/workspace.py`: `_pool_db`, checkout/create call sites of
  `workspace_pool.admit`. These use the universe's `.runs.db` and omit hourly
  budget overrides. The separate `outbound.db` is credential authority, **not**
  this workspace ledger. No matching workspace-hourly env overrides were found;
  the actual call path uses the constants regardless.
- `openspec/specs/engine-run-admissions/spec.md`: workspace job/byte budget and
  unknown-transfer reservation requirements.
- Do not infer a functioning host-global semaphore from the `SCOPE_HOST` name:
  adapters use universe-local databases. See the qualified findings in
  [workspace admission claims](2026-08-31-workspace-admission-claims-are-narrower-than-stated.md).

Production check commands (2026-09-09 UTC): `python scripts/droplet.py status`,
then `Get-Content -Raw output/workspace_hourly_readonly.py | python
scripts/droplet.py ssh -- 'docker exec -i tinyassets-daemon python -'`.
The session-local ignored diagnostic script opened SQLite with `mode=ro` and
`PRAGMA query_only=ON`, emitted only whitelisted numeric fields, run IDs, failure
classes, and a redacted quota message; it did not initialize or reconcile stores.
For reproduction without that local script, query `workspace_ledger` by
`universe_id` and the 3,600 seconds preceding the failed run in the universe's
`.runs.db`; join `workspace_leases` by lease ID, inspect `workspace_locks`, and
look up the failed run in the root `.runs.db` (`queue_universe_id` scoped).
Read `release-state.json` through the configured release-state path and inspect
only the named numeric constants from deployed source. Do not dump payloads,
credentials, or other universes' records.

The release SHA was also checked with `python check_primitive_exists.py
sha 49dc655fdb11e58d7e6682f7b41e111d042cf246`. No new deploy is claimed here.

## Correction scope and acceptance

The defect is fragmented platform policy, not a malformed user workflow. A
correction must account for actual shared dependency use, not silently reset this
universe's ledger or give it a private exception. Raising ten to another isolated
literal would leave the same fragmentation and is not the requested end state.

The bounded correction is tracked in `consolidate-platform-resource-policy`,
merged PR #3560; it removes both starts-count refusals while preserving resource
guards and adding scoped observations. It is deployed. The owner's 21:39 PDT
app report confirms 12 workspace starts and contention/recovery success, but
also reports explicit discard failure; complete cleanup acceptance remains open.
Shipped specifications are being synchronized with #3577's engine-share removal:
300 write admissions / 900 total, no separate 600 engine ceiling. The table above
is historical diagnosis evidence, not a claim that retired gates remain current.
The already-landed `run-usage-budgets` records were verified, synced and archived
under [the `run-usage-budgets` change](https://github.com/TinyAssets/TinyAssets/tree/88e74e1cd32b1ad19bb8af9bf88ab3bd68e4e979/openspec/changes/archive/2026-09-08-run-usage-budgets). A new
accounting scope, authority surface, or storage migration needs proposal/design
and independent cross-family review. No exact thresholds, cross-universe owner
aggregation, commercial tier changes, or extra provider spending are authorized
by the examples alone. Keep host memory, actual disk containment, credential
isolation, and overload protection intact; hourly transfers must not be called
retained storage and free concurrency must not imply free throughput.

Acceptance: ordinary app-owned creation, editing, running, and cleanup can
continue without this hidden ten-start cliff; simultaneous work remains bounded;
retries and failures are accounted for once at the right stage; the owner and
agent can understand usage, the exhausted resource, and available recovery.
Prove platform changes with focused tests plus the Linux oracle where required,
deployed-SHA/canary evidence, and an ordinary rendered app conversation. Do not
substitute locally repaired workflows or duplicate checklist prompts for proof.

Independent diagnosis review: [Claude review and disposition](../reviews/2026-09-08-workspace-usage-diagnosis-claude.md).

## Post-3560 gap assessment and next slice

**2026-09-08 PDT / September 9 UTC, source assessment at `ad198c37`.** Runtime
source is the deployed `0eb1388f` tree. Read-only repository inspection only;
no new live workload, provider call, user-workflow edit or deployment. Commands:
`python docview.py lines <source> --start <line> --end <line>` and scoped
`rg -n` searches over the source symbols below. This is not a new load test.
The existing independent shape/code reviews remain the starting point, not a
reason to repeat review of the already shipped code.

### What prevents the simple system

| User-facing dimension | Existing truth to reuse | Remaining gap / safe disposition |
|---|---|---|
| Work happening now | Workspace leases/locks; provider admission condition/counter | Workspace `SCOPE_HOST` is in each universe's `.runs.db`, while scratch directories share a parent. Same-run locks are reentrant. Provider slots are process-local and reserve child-call headroom. Neither is a proven single host-wide, per-user concurrency allowance. Keep these safeguards; do not sum leases and provider processes into an invented concurrency number. |
| Activity over time | Root `admissions` and `dispatch_budget`; universe `workspace_ledger` | Admissions count runs and engine mutations; generic effects count nodes, not sinks or gestures. Workspace jobs are now observations only. The status projection does not yet show the generic effect window. Keep distinct units visible before selecting one activity policy; a combined number would double-count some work and miss other work. |
| Stored data | Permanent workspace tree; universe files; scratch lease ownership; existing host storage report | No complete, attributable retained-storage observation. Workspace `measured_bytes` is transport evidence and can be stale after code writes. A directory total alone omits owned scratch and shared-root records while mixing provider runtime/cache with user data. |
| Internal dependency safety | Transport reservations, process/memory bounds, pool/storage checks, provider-work authority | These protect different resources. They may appear under the three understandable headings, but cannot be removed merely to leave three constants. Unknown transferred bytes remain reserved; upstream entitlement is not a platform allowance. |

Fresh source anchors (symbol names are the durable locator):

- `effectors/workspace.py::_pool_db` (758) delegates to `runs_db_path(base_path)`;
  `scratch_pool_root` (216) uses the shared parent. `workspace_pool::_acquire_lock`
  (489) is run-reentrant; `admit` acquires both scopes in the passed database.
  A real multi-process/within-run capacity proof remains missing, not assumed.
- `provider_admission::_effective_limit` (142) and `_try_acquire` (154) use
  in-memory state and nested headroom. Its introduction's old 2 GB/no-cgroup
  measurements are historical, not current sizing evidence; the prior live
  diagnosis found an 8 GB host and a 4 GiB daemon cgroup.
- `effectors/__init__.py::_budget_refusal` (332) checks per-run and hourly
  budgets; `dispatch_node_effects` charges once after a node's effects (793).
  `engine_admissions::dispatch_window_usage` (436) fails open on unreadable
  accounting. It must not become an admission authority by relabeling it.
- `effectors/workspace.py::_universe_used_bytes` (995) measures only permanent
  workspaces, ignores individual filesystem errors and is unbounded; it is not
  an honest complete-status reader. `workspace_pool::reconcile_bytes` (763)
  records transport measurement, not current retained size.
- `storage::inspect_storage_utilization` (650) already has TTL/single-flight
  reuse, but returns host-wide subsystem paths and totals. `path_size_bytes`
  (595) suppresses failures and follows file targets. Do not expose this report
  to an owner or reuse its result as their exact storage allowance.
- `providers/base.py::_provider_child_runtime_env` (461) creates a universe-local
  `.runtime/provider-child/<provider>` tree. Count it separately, not as evidence
  the user authored that data. `usage_policy.py` still cites a deleted August 28
  duplication concern; the historical 515 MB / 99% figures are not reverified
  here and must not be used to choose present quotas.
- `usage_policy::enforcement_enabled` (194) remains default-off in source and
  documents non-atomic settlement and an unmetered sink. Its tier storage numbers
  are configuration, not evidence of live total-storage enforcement. Do not
  switch it on as a consolidation shortcut.

### Which controls can be consolidated safely

The duplicate workspace jobs refusal was the proven removable gate and is gone.
Current admission constants/formula already have one source after PR #3560.
Next, the *explanation* of activity can reuse `admissions` plus `dispatch_budget`
without a second meter; preserve their separate names, windows and units.
Do not yet delete write/engine subcaps (fairness/abuse policy), per-run versus
hourly effect guards (different horizons), or transport versus retained-storage
checks (different resources). No further refusal is proven redundant by this
assessment. Removing one requires an executable same-scope counterexample and
proof that the surviving authority covers every reachable path.

### Smallest next implementable slice: attributable storage observations

Build one bounded, read-only filesystem-metadata sampler beneath the existing
authorized resource-status seam. Reuse canonical paths, the current admin gate,
existing workspace lease ownership and the existing TTL/single-flight pattern.
No new ledger, stored counter, schema migration, quota reset or PLAN change.

1. Report a complete-or-partial **universe-local logical file footprint** with
   separate permanent-workspace, provider-runtime and other-universe-file buckets.
   Include SQLite sidecars. Label bytes as logical file size, not allocated disk,
   billable user content or complete platform-attributed storage.
2. Attribute shared scratch/quarantine only through that universe's existing
   lease rows and validated canonical paths. Never infer ownership from an
   arbitrary database path, follow symlinks/reparse points, or reveal paths/IDs.
   Missing, changing, unreadable or time/entry-bounded walks return explicit
   partial/unavailable coverage, not zero. Deduplicate hard links within the
   measured scope; do not promise an atomic snapshot of a live filesystem.
3. Keep shared-root database overhead, unattributed scratch/staging and unknown
   runtime attribution explicit exclusions. Root `.runs.db` and `.tinyassets.db`
   contain cross-universe state: file sizes cannot be assigned per owner from row
   counts. These gaps keep **total attributed storage unavailable**, even when
   the measured local footprint is useful. Never scan other owners to fill it.
4. Synthetic tests first: own versus foreign roots/leases, live WAL, hard links,
   symlink/junction escape and replacement, permission errors, disappearing files,
   traversal bounds, stale cache and concurrent callers. Assert no content reads,
   no state mutations and no authorization leakage. Linux proof and independent
   shape review gate public wiring; owner-held rendered testing gates acceptance.

This produces the missing trustworthy measurement foundation, not a renamed
transfer counter or another new refusal. Aggregate write-time disk containment
and genuine shared-host concurrency remain separate prerequisites before relaxing
their existing protections. The existing workspace-admission concern owns those
findings; no new hardening workstream is opened here.

**Decision boundary:** no founder answer is needed to build/verify this bounded
observation design. Commercial thresholds, charging platform runtime to owners,
cross-universe account pooling and a new capacity entitlement would require an
explicit policy decision; none is needed or implicitly authorized now. Do not
ask the founder to choose numbers before measuring their actual scope.

**Current action:** the source/coverage assessment's bounded measurement slice
shipped in PR #3568 as cb74e216, with Linux and independent approval plus
authenticated deploy proof. `observe-attributable-storage` retains owner-held
acceptance only. PR #3565's original checks passed and it merged without
modification. No new app prompt is authorized. Shared-root attribution,
write-time disk containment, true shared-host concurrency and consolidation of
the differing activity units remain unresolved; do not replay this implemented
sampler recommendation as new work. Wider simplification and owner acceptance
remain OPEN.

## Decision-ready consolidation follow-through

The September 9 UTC design-only pass is recorded in
[`consolidation-decision.md`](../../openspec/changes/consolidate-platform-resource-policy/consolidation-decision.md).
It recommends an actual first retirement (the engine-mutation share inside the
unchanged total allowance), then boundary-owned concurrency/activity/storage
enforcement—not another telemetry feature. It separates technical safeguards from
product choices with fresh source evidence and records one later founder decision
about responsibility for mandatory platform overhead. No runtime, policy, PLAN,
ledger, test dispatch or app action was changed by that pass. Implementation is
not authorized by the design-only follow-up, and broader acceptance remains open.

## September 9 UTC implementation resumed

The owner's active goal now explicitly authorizes finishing and deploying the
limits simplification. `retire-engine-mutation-subcap` implements the first
predicate retirement from the recommendation. The earlier design-only restriction
is historical, not the current authorization. Broader concurrency/activity/storage
consolidation remains open; this slice must not be reported as its completion.
