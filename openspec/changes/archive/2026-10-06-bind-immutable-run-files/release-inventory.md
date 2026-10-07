# First usable file slice: isolated release inventory

Date: 2026-09-20. Builder worktree `run-input-file-custody/TinyAssets` contains
preserved experimental cloud ancestry. **Do not merge/push that branch wholesale.**
Root owns integration and release. Comparison target is reviewed shared foundation
`f65ceeb9`; retain its activation/recovery correction and any successor fixes.

## Current isolated assembly

The release worktree `run-file-custody-release/TinyAssets` on
`codex/file-custody-release` starts at consumer `f9b91ed7`, not the experimental
file ancestry. Owned paths were imported from file candidate `4dd99ba4` using
explicit patches. Foundation runs/containment/provision files, consumer runtime,
the consumer's lazy-unavailable origin registry and common schema/runtime are
byte-unchanged. Only the file compiler context, file physical-deletion additions,
five reset table classifications and independent file maintenance cursor were
merged into shared paths. Conversation expiry/model bridge/receipt observers
remain intact. Existing read_graph request_key positional ordering is preserved.

Consumer's review-disposition/specification/test-only successor is imported as
`456d9368`, `82145d50` and `7e17f751` (originals `47025163`, `50774c81`,
`d3fa5f91`). The maintenance AST fixture binds both cursors and tests independent
file/admission/delivery failure. Lower-level file fixtures explicitly acquire the
real admitted-worker guard instead of relying on superseded implicit family
enrollment, with no-guard refusal retained. No production runtime changed for
either fixture correction.

## Not file dependencies

Do not import this branch's `tinyassets/runs.py`: its only difference from the
foundation is the older unconditional root-family enrollment, which would undo
the activation correction. Likewise do not import this branch's
`node_sandbox.py`, `workspace_cgroup_join.py`, `workspace_cgroup_kernel.py`,
`workspace_family.py` or `workspace_provision_process.py`. Their differences are
experimental containment wiring, not used by owned authoring capture or reads.
No cgroup bootstrap fixtures, host configuration or cloud launch documents are
needed. Keep the foundation's worker guard/use, family provenance and prepared
invocation interfaces, which file reads actually require.

## Necessary custody additions

Copy the file modules and their tests, preserving independent ownership and
lifecycle: `tinyassets/run_file_{binding,capture,cleanup,contract,erasure,node,
reader,release,retention,sources}.py`; `tinyassets/storage/{run_files,
run_file_lock}.py`; `tinyassets/execution_authority/{blob_stream,blob_proof}.py`
file streaming changes. These use existing storage, authority, authoring session,
reset barrier and workspace byte accounting APIs; they create no new executor.

`tinyassets/workspace_pool.py` difference from the foundation is the 54-line
byte-only `reserve_transfer_bytes` extraction. Preserve that hunk, including
scope-bound replay and no fabricated workspace-job observation. Canonical
compiler difference is the 58-line alias/read-context/explicit placement work
from `722f7156`, separate from consumer model/receipt edits in the same file.

Executable `io_manifest` preservation and strict runtime declarations require
changes in `authoring/io.py`, `authoring/models.py`, `authoring/store.py`,
`branches.py`, `branch_versions.py` and `daemon_server.py`. Do not omit these as
presentation-only work: without them capture references can be lost before the
immutable version contract or cannot resolve their real owned authoring source.

## Shared admission dependencies and lifecycle

Generic envelope/common worker provenance predates this builder resume; preserve
the foundation guard hooks and `76804605` common carrier/on-settled semantics.
`5292b378` adds origin fields/immutable replay checks, pure origin validator,
single static registry, safe prior-marker exception handling and the independent
bounded boot/maintenance admission scan. `d2695029` adds reserve-only direct
intake. `59ca33f6` adds the pure read-only uncertainty classifier.

Canonical consumer import does NOT need custody modules: its registry adapter
imports are lazy. Keep consumer's current static exports and v1 execution options,
and its same initial/recovery registry call. Never infer origin from file presence.

Retain exact generic `run_input_admissions` reset/account lifecycle from
`89b5ea23` (consumer release already imports its isolated equivalent), and file
custody reset/account cleanup from `e4e93d97`, retention `6c518ed5`, bound-read /
selective settlement `a2003223`. Current file tables and physical deletion debt
must all remain classified. Preserve tombstones and cleanup ordering; no
origin/object table may be omitted merely because an idle reset happens to pass.

## Public route candidate

`tinyassets/api/run_files.py`, `api/runs.py`, `universe_server.py` and
`engine_mcp_server.py` expose same-owner capture/read/release/limits plus direct
and versioned file admission. Public run status uses the shared pure classifier
after owner validation. The new version selector is appended to signatures to
preserve positional callers and has existing source-read checks on both handles.
Merge only these file-specific route hunks where consumer edits overlap.

Rebuild all canonical mirrors after assembly. Evidence and remaining release
gates are in `verification.md`; test counts from this mixed-ancestry tree do not
substitute for rerunning the assembled candidate on Windows/Linux and CI.

## Global rollout configuration proposal (not applied)

No prior approved numeric capacity was selected: the reviewed contract requires
an explicit global ceiling, and unset capacity refuses public capture. Root's
read-only production snapshot on 2026-09-20 about 06:37 UTC, using
`df -B1 --output=size,used,avail,pcent,target /var/lib/docker`, reported
52,626,063,360 total bytes, 11,066,957,824 available bytes and 79% used. That is
point-in-time rollout evidence, not an assurance about the custody filesystem.

Propose normal daemon deployment environment configuration:
`TINYASSETS_RUN_FILE_CUSTODY_MAX_BYTES=1073741824` and
`TINYASSETS_RUN_FILE_HEADROOM_BYTES=1073741824` (1 GiB each). The subsystem
ceiling is under 10% of that observed available space, independent of any user's
tier or pricing. Headroom is a separate free-space admission floor, not a second
retained allocation or new resource policy. Existing admission also considers
pending byte debt. The release lead must freshly verify actual custody mount
identity/free space and approved values before deployment, configure globally
through the existing deployment environment, then prove capture availability in
the ordinary app. Never require a per-user patch or silently ship unset.

Rollback stops new intake (unset the ceiling) while preserving all custody,
bindings, manifests and physical cleanup debt. Compatible code must remain for
exact owned read/export/release and collection; do not deploy an old binary that
silently discards declarations or delete retained files. Reducing the configured
ceiling below retained allocations refuses additional intake rather than evicts
files. Unsetting stops NEW BYTE CAPTURE ONLY: existing references remain bindable,
executable and exportable without the ceiling, and bound files do not expire.
Stopping existing-file binding/execution requires a targeted hotfix redeploy,
never an old binary. Root owns actual environment changes, exact deployed proof and live
acceptance; this builder has made no production configuration changes.

Release/hold checks follow the shipping-and-launch checklist: required hosted
CI and exact-head independent review remain mandatory, then deployed-SHA proof,
the canonical authenticated MCP handle canary and a rendered app conversation.
The first ordinary owner must be able to create/edit/publish the workflow,
capture files, run the old immutable version after an edit, and export exact
hash-matching bytes without operator workflow changes. Confirm expected refusal
for foreign/unbound inputs and unknown capacity with disposable fixtures.

For the first hour, the release lead checks daemon health, new capture/read/run
error types, disk headroom, retained/pending allocations and cleanup debt against
the predeploy snapshot. Hold rollout for unexplained failures or growing debt;
stop new byte intake immediately for integrity or authorization failure, but do
not mistake that for disabling access to existing files. If existing binding,
execution or reads are affected, deploy the targeted safety hotfix as well;
there is no existing-execution kill switch in the custody capacity setting.
Preserve accepted runs/files for inspection and compatible export/cleanup. Report actual
monitoring observations and rollback timing, not assumed successful recovery.
