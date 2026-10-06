## Context

PLAN was fully read for the parent correction and its cross-cutting principles
rechecked for this slice. Reuse existing state; observations are not authorities.
The current scope/evidence lives in the September 8 workspace-hourly-cap concern.
Commander authorized this bounded implementation while forbidding app prompts,
production workloads, new ledgers/migrations, pricing and threshold changes.

## Goals / Non-Goals

Goals: useful owner-only retained-file measurements with explicit limitations,
bounded resource use, no file contents read and no writes to user state.
Non-goals: complete account billing/attribution, aggregate disk enforcement,
quota changes, shared-host concurrency repair, workflow editing or live testing.

## Decisions

1. Preserve `retained_storage.availability=unavailable` and its total-storage
   reason; add a `footprint` observation containing its own capture time,
   availability, logical regular-file bytes, category counts and coverage reasons.
   Root shared databases, unattributed staging/scratch and platform-wide overhead
   remain exclusions. No filenames, paths, lease IDs, content or owner IDs leave
   the helper. Measurements are not billable bytes or allocated physical blocks.
2. Scan canonical universe roots in fixed category order: `workspaces`, `.runtime`,
   then other entries excluding those roots. Separately derive scratch and
   quarantine names from existing authorized scratch lease IDs/generations;
   never follow lease-row `path` strings. Query only this universe with a bounded
   row count; missing/legacy/unreadable records mean incomplete scratch coverage.
   Filter cleanup-confirmed AVAILABLE leases out. ENOENT at a derived leaf is
   observed absence, not a failed walk; inaccessible or changing entries remain
   partial. Validate IDs with the existing path-component regex and generations
   as non-negative integers before deriving names; transferred `measured_bytes`
   is never a size substitute. Workspaces include their own quarantine subtree.
3. Reuse `workspace_fs.open_dir_nofollow` / `open_subdir_nofollow` for descriptor
   traversal on POSIX. Use lazy `os.scandir(fd)` plus no-follow metadata stat;
   compare a child directory's opened identity to its observed identity. Never
   open regular-file contents. Symlinks, special files and inaccessible/changing
   entries are skipped with partial coverage. On hosts without safe descriptor
   traversal report unavailable, not a racy Windows fallback. No new dependency.
4. Limit total entries, lease rows, depth and elapsed scan work cooperatively;
   native filesystem syscalls cannot be forcibly interrupted, so no hard latency
   guarantee. Deduplicate regular-file `(device,inode)` identities within the
   snapshot, assigning cross-category hard links to the first fixed-order bucket.
   This is observational allocation, not ownership/billing policy. Count directory
   identities too to avoid repeated descent. Close every descriptor/iterator.
5. Reuse `TTLMemo` (bounded LRU and single-flight) keyed by canonical root, universe
   and universe directory identity; recheck existing admin ACL before every cache
   access. Retain the capture timestamp on cache hits and expose maximum cache
   age. Bound simultaneous distinct scans with a small nonblocking process-local
   gate; contention returns explicit unavailable evidence rather than queueing
   another walk. A TTLMemo waiter may return None after its existing 30-second
   wait; normalize that to unavailable/contention. These are measurement work
   bounds, not user activity quotas. Initial bounds: 10,000 visited entries,
   256 lease rows, depth 64, 0.2 seconds cooperative scan time, two process-local
   scans, 128 cached scopes, maximum 60-second TTL using the existing storage TTL
   setting (clamped to this ceiling). Coverage reasons are fixed enums, never
   exception strings. Include scope and not_atomic_snapshot in the response.
6. Leave existing storage walkers and all admission/cleanup machinery unchanged.
   They suppress errors or expose host scope and are not reusable as exact private
   measurements. The safe descriptor helpers and memo are the reusable parts.

## Risks / Trade-offs

- File changes during a walk: partial evidence when detected; every result states
  it is not an atomic filesystem snapshot, even if traversal completed.
- Scratch ownership/history can be incomplete: explicit exclusions, never scan
  foreign leases or all scratch to infer ownership.
- Cache may be stale: stamped bounded reuse; authority is never cached.
- Unsupported Windows descriptor traversal: honest unavailable status; Linux
  verification is required before landing, not skipped-test optimism.
- Metadata work can still touch sensitive sizes: exact current admin access only;
  no cross-universe paths, inode identities or per-file metadata in output.
- Forged authoritative lease rows could misattribute a shared scratch size. This
  slice trusts the existing ledger's ownership, never caller-selected row paths;
  it does not add a new ownership protocol or repair corrupted authority records.

## Migration Plan

No storage migration or data rewrite. Additive response only. Independent shape
review before implementation; focused tests, Linux proof, exact-head approval,
required CI and authenticated deploy gates before readiness. Owner owns rendered
acceptance; no app prompt. Rollback reverts the additive code without changing data.

## Open Questions

## September 18 legacy observation freshness follow-up

The existing `storage_utilization` response gains additive fields without a
schema-version bump: `observed_at` (UTC ISO-8601 scan-start time),
`observation_age_seconds` (nonnegative elapsed monotonic seconds since scan
start, recalculated per read), `cache_ttl_seconds` (configured nonnegative memo
reuse duration after scan completion, not a maximum total observation age),
`volume_scope=filesystem_containing_data_root`,
`volume_percent_formula=1 - volume_bytes_free / volume_bytes_total`,
`subsystem_scope=partial_enumerated_daemon_paths`, and fixed explanatory
`accounting_caveats`. A private monotonic capture marker stays inside the memo
and is removed before returning. Cache hits preserve original capture time.
Status overlays a few universe-specific measurements after the memo; caveats
explicitly mark this non-atomic mixed-scope observation. Largest listed subsystem
is not largest filesystem consumer, attributable owner usage or billable storage;
Docker images and unlisted paths are not inventoried. No extra traversal,
content access, identities, paths, quotas, cleanup or persisted records are added.
Existing zeros on failed disk probes are retained for compatibility but labeled
with additive `volume_availability=unavailable`; successful probes report
`available`. This prevents claiming healthy evidence from unavailable numbers.
The broader attribution/policy work and owner acceptance remain open.

No founder policy choice is required. Full attribution, retained-disk enforcement
and simpler admission policy remain in the existing concern rather than this slice.
