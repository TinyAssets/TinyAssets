# Jail reservation starvation: bounded repair

## Evidence and attribution limits

Base: `8a8ec275`. Temp-only real-function reproduction with a 2 GiB free quota:

| Retained logical bytes | Overlapping launches | Reservations | Ledger used | 49,101-byte UI admission |
| --- | --- | --- | --- | --- |
| 0 | 2 | 1 GiB + 1 GiB | 2 GiB | refused |
| 100 MiB | 2 | 1 GiB + 924 MiB | 2 GiB | refused |
| 1,100 MiB | 1 | 948 MiB | 2 GiB | refused |

Remeasurement does not release running reservations. Settling the last launch
admits the same write. This proves a mechanism, not incident attribution. The
reported production refusals at 17:11/17:12 had used=quota=2,147,483,648 and
requested=49,101, free tier; the live per-owner measurements/pending breakdown
was unavailable. The reported host was 47.92% used with 27.4 GB free.

The supplied partial inventory (178 directories, 1,729 files, 107,720,537 bytes)
is not a full account total: listing truncation and missing shared-store and
pending data prevent that conclusion. Render/image dependencies were documented
as needed but rebuildable; reference images and data-URL copies warrant owner
review, not deletion. No production inventory or cleanup is performed here.

`NodeSandbox` uses `subprocess.Popen`; it does not open this budget. Supported
Bash opens its own tool budget and settles in `finally`. A completed sequential
Bash cannot supply a second outstanding reservation at a later UI save. The
mechanism requires overlapping launches, or enough retained accounting for one
launch to consume the remaining pool. Direct children share their parent's
supervised universe walk; independently launched tools/providers reserve again.
There is no trusted hierarchical transfer token at these call sites.

## Design before implementation

Owner scope: `tinyassets/storage_accounting.py`, `tinyassets/jail_disk.py`, their
tests and this audit. No process/jail call-site edits, queue mutations, quota
settings, credentials, production, provider calls, or user-file deletions.

1. `reserve_fitted` computes and inserts under one `BEGIN IMMEDIATE`, after
   refreshing stale measurements outside the transaction. Add optional nonnegative
   `headroom=0`. Bound = min(cap, max(0, quota-used-headroom)+credit). Insert the
   incremental reservation only when used+increment <= quota and bound >= minimum.
   Existing workspace callers retain headroom=0 and replacement-credit semantics.
2. `open_budget` requests `headroom=16 MiB, minimum=0`. Its successful accounted
   bound is exactly the reserved increment; never enlarge it to a grace minimum.
   At/over quota it admits zero growth with a truthful notice. Reads/deletes can
   run; a CLI that needs new files may be stopped. This intentionally tightens
   owned-account recovery behavior and avoids spending the protected allowance
   twice. Unknown accounting retains the existing bounded emergency fallback;
   unattributed universes retain their existing capped policy.
3. Account measured stores plus all reservations still count. Do not exempt
   parent reservations, transfer them across stores, infer nested identity, or
   subtract raw measured growth. Permanent workspaces are billed separately;
   runtime, staging, and shared scratch are excluded from durable billing.
   Jail growth includes runtime for physical-disk protection, so it remains
   deliberately more conservative than billed bytes.
4. No promise that 16 MiB is always writable: ordinary writes or newly measured
   data can consume it. The promise is that jail speculation alone stops short
   of consuming it. Shared disk/inode floors, per-launch cap and polling stay.

Independent pre-implementation design review: `/root/quota_design_review`, AGREE
on atomic fitting, zero-growth exhaustion, no shared/nested reservation bypass.
The cross-family CLI specified by repo guidance is unavailable (no Claude CLI);
the available independent review uses the session model and is not cross-family.

## Existing lifecycle limits (not repaired in this narrow slice)

- `DiskBudget.settle` releases before marking measurements dirty. A competing
  admission can use stale measurements; even dirty rows are refreshed only on
  refusal. A repair needs proof of measurement coverage before releasing capacity.
- Reserved rows expire after 600 seconds; renewal swallows errors/missing rows.
  A paused or partitioned supervisor can lose its lease while writers remain.
- Provider watcher cancellation settles without first proving the process family
  stopped. Integration owner must address this with process-family ownership;
  this lane does not modify `owned_process` or `provider_jail`.
- Polling permits write overshoot; simultaneous same-root walks cannot attribute
  individual writers and may stop conservatively. Measurements plus pending can
  overlap conservatively; this is not proof of duplicate-file billing.

Consequently, tests may prove serialized *admitted reservation* bounds, not hard
physical no-oversubscription. Crash/TTL/idempotent settlement tests characterize
existing recovery only; they do not prove safety for a still-running orphan.
Kernel quotas or a fenced reservation/process lifecycle remain necessary for
that stronger claim. No deploy or real-user pass is claimed by this code-only lane.

## Owner diagnostic extension (reviewed before implementation)

The parent reports a read-only agent receipt for the same UI workflow at
17:12:46, 36 seconds after the second refusal: replace_ui saved revision 56,
renderable=true. This is source-agent-reported, not independently root-read full
output, and strengthens a transient explanation without proving ledger attribution.
A surfaced 4 GiB workspace allocation belongs to a separate ledger and is not
proof of 4 GiB billable retained storage.

Expose additive `measured_bytes`, `reserved_bytes`, and `committed_bytes` in
`Usage` and the owner's existing refusal record. Query pending by the same
account and preserve used = measured + reserved + committed without clamping.
These are accounting components, not disjoint physical bytes; measured and
pending can conservatively overlap. The refusal explains reservations and
suggests retrying after active calls finish before deleting files. No new
endpoint, reservation identifiers, raw paths, or credentials. Existing
`visible_record` keeps its fixed generic response for every nonowner; test
explicit owner, other owner, unauthenticated, and inferred authenticated viewers.
The independent exact-head reviewer agreed with these constraints before code.
