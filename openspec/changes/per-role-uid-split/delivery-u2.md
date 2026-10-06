# U2 D214: replacement-inode provenance fixed (Claude takeover of D213)

### D214. Migration provenance is bound to one inode generation

Design: `design.md` D214. Root cause: owner and metadata journals keyed rows by
`(dev, ino, type)`, and any unrecorded inode recorded its live ids as legacy
in every state, so replace -> forward restart -> reverse (and replace ->
reverse -> forward -> reverse) restored the migrated GID 1102. The owner phase
had the same class: a daemon file born `1001:<machine>` in a setgid work dir
reversed to the machine GID even without a restart. Fix: one `_provenance`
rule for uid/gid/mode; rows carry the statx birth time; a record moves only to
the same inode generation; unrecorded inodes on a migrated volume reverse to
legacy 1001:1001 with their live mode.

Linux root oracle, owner/metadata/inventory/volume selection: **148 passed,
zero skips** (140 prior + 8 new D214 cases: vault and liveness under forward
restart, reverse/forward cycle, recycled-inode stale record; owner born-after-
forward with and without restart). All 8 failed before the fix. Ruff and mirror
parity pass. This closes only the D213 provenance finding; stable-generation
configuration growth, two-pass deletion, old CMD boot and startup wiring remain
open and activation stays OFF. Concern `2026-10-06-u2-stale-mode-replay.md`
resolved and deleted.


### D214 round 2 and D215 (Codex BLOCK on ab3553a08d)

Codex reproduced a stale replay on an interrupted reverse: an incomplete
same-direction owner journal skips inventory and checked key, nlink and ids
but not generation, so a replaced `1001:1100` file with a reused key was
restored to `1001:1100`. Resume validation and the pre-chown descriptor check
now compare the live statx generation (fd form uses `AT_EMPTY_PATH`), and a row
without one refuses. `_generation` refuses overlayfs (D215: copy-up re-births
the inode under the same number, measured). The production `/data` is an ext4
named volume with stable birth time.

Linux root oracle, owner/metadata/inventory/volume: **151 passed, zero skips**.
The three new cases are replaced-generation resume, generation-less resume and
overlayfs refusal. All three failed on the parent implementation. The root
fixture moved to `/dev/shm`, and the production-image probe seeds there too.

---

## Post-review final verification

Merged origin/main `22bc0f728e989813630d54c8378921370fafe7f2` at
`0604291e28`, then pushed the source checkpoint. Post-merge Linux oracle ran
the four migration files plus `tests/test_starter_instructions.py` and
`tests/test_converse_turn_cost.py`: **160 passed, zero skips**. The installed
image receipt below predates this unrelated starter-guidance merge. Plugin
mirror regeneration/import probe passed and produced no diff. Targeted Ruff
and diff checks pass. The pre-main-merge hygiene result was 344 tests added,
0 removed, 0 tampering; final merged-stack hygiene is recorded in the PR body.
No further implementation occurred after D213. Final local edits are the
already-prepared installed-image probe and this handoff/concern record.

# U2 handoff: D213 repeated rollback-provenance finding

### D213. Stop under AGENTS loop rule 7; do not patch again

D211's permission cap is implemented and committed at `5ea4a3e572`.
Claude cross-family review via peer-agents completed, exit 0, **ADAPT**; full
output read at `C:/Users/Jonathan/AppData/Local/Temp/u2-d211-review-result.md`.
No floor defect was found in owner/metadata/egress mode-cap logic.

**AGREE** with finding 1, a repeat of D210 finding 2: forward migration,
atomic vault/liveness replacement, forward restart, then reverse records the
replacement's broker GID1102 as historical ownership. Reverse restores that
GID instead of legacy 1001. Direct replacement-to-reverse is covered and passes,
but an intervening forward restart is not. A second forward/reverse cycle after
the direct fallback can likewise revive the wrongly recorded IDs. The live mode
remains capped: this is ownership-provenance correctness, not stale chmod replay.
Do not activate or treat the migration as rollback-ready. Per AGENTS rule 7,
"the same finding twice: hand off, do not patch." No implementation changes were
made after the review arrived. The next implementation owner must address the
whole replacement/restart/direction-cycle contract rather than patch one branch.

**AGREE** with the coordinator restart limitation: new visible immediate entries
change `work`, and the existing journal's exact configuration guard refuses.
Stable-generation reconciliation is still unimplemented, not grounds to delete
the journal or weaken binding checks. **DISAGREE_EVIDENCE** with the claimed U1
preview ownership collision: precreation starts at 1001 only inside the stopped
window, then the owner phase assigns the dedicated UID/GID. The full-volume test
asserts final `previews.st_uid == machine` for both owners, satisfying U1's stated
handoff. No U1 launcher/class/decoder/ui-preview file was edited.

Verified receipts (synthetic data only):
- Linux root oracle selection below: **140 passed, zero skips**, after D212.
- Targeted Ruff and diff checks pass; static prompt guidance is unchanged.
- Production Dockerfile build exits 0, privileged chain PASS. Image
  `sha256:a3203def8dc464a7c395ecd679ff20f4bc47e8b78991f048eb3f5e4f0d8fc7db`.
- `python scripts/role_owner_migration_probe.py --image tinyassets-uid-u2:d211`
  exits 0 under exactly CHOWN/DAC_OVERRIDE/FOWNER/SETUID/SETGID/SETPCAP/KILL:
  dry/apply/repeat/reverse, nine crash boundaries, quarantine names/inode/bytes,
  continuous lock, real foreign-owner denials and current chmod retention in
  both directions. All child capability sets zero, groups empty, NNP=1.
  This installed-image proof covers the owner substep, not the full coordinator.

Per requested item:
1. Offline owner/metadata/egress mode caps and coordinator tests are committed;
   full migration remains incomplete due to the repeated ownership finding,
   stable-generation handling, first-allocation and installed full-volume proof.
2. Two-pass deletion is not implemented; U1 owner-delete class admission remains
   an integration dependency. No substitute daemon privilege was introduced.
3. Actual previous-production CMD boot is not implemented/proven. The historical
   D67 egress subprocess proof does not satisfy this item.
4. Startup/healthcheck wiring remains unimplemented. Activation remains OFF.

Both requested drafts (#4509 into U1, #4510 into main) remain NOT merge-ready.
U1 remote merge was already current. Direct coordination failed before sending:
official Codex proxy connection refused (10061), vendor catalog named-pipe ENOENT.
No settings were changed and no delivery is claimed. No production volume was
mounted or changed; no deployment, spec completion, deployed-SHA assertion or
real-user app pass is claimed. This handoff supersedes the in-progress notes below.

---

## Current verification

Linux command (root is required to exercise the pre-drop migration identity):
`MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py --as-root -- -q tests/test_role_owner_migration.py tests/test_role_metadata_migration.py tests/test_role_volume_inventory.py tests/test_role_volume_migration.py --basetemp /tmp/b`

**140 passed, zero skips**, after D212. Targeted Ruff and diff checks pass.
This includes full dry-run/apply/repeat/reverse and six coordinator crash
boundaries in each direction, owner quarantine and mode preservation. It does
not yet prove first-time broker allocation, dynamic inventory generations,
production-image full migration or old CMD boot. Claude review is running.
The current production Dockerfile image is building for the installed owner
substep. No production execution or activation is authorized or performed.

### D212. Egress relocation exclusively owns its inode provenance

The first full coordinator oracle found that the metadata phase recorded
post-forward UID1002 for the relocated ledger, then reapplied it after reverse
relocation restored UID1001. Exclude the exact D12 ledger/sidecars/proxy sets in
both physical locations from metadata traversal; the egress substep validates
and owns them. No competing metadata journal may relabel these inodes.
The initial full selection had 133 passes and seven reverse-coordinator failures;
this is an implementation correctness failure, not production acceptance.

# U2 D211 resume (verification in progress)

### D211. Lead decision: current permissions outrank migration history

The lead resolves D210: migration never widens a current permission. Ownership
provenance remains durable, but a recorded mode cannot undo a later chmod.
Forward owner-work migration preserves the descriptor's current mode; reverse
restores original UID/GID while retaining the current pre-reverse mode. New
engine entries reverse to daemon 1001. Canonical root and metadata policies may
narrow current modes, never add bits. This supersedes D209's directory-setgid
addition and old-mode replay. No regular file gains read/write/execute/setid.

The same cap applies in metadata and egress setters, including resumed rows and
WAL/SHM. Missing original metadata after atomic replacement reverses to legacy
1001:1001 while retaining current mode. Consequently an existing 0600 vault
stays 0600: this migration does not silently grant its broker read permission.
Full activation must account for this constraint; activation remains OFF.

First Linux root oracle: 113 passed, zero skips (owner, metadata, inventory),
including 36 chmod-after-record/restart cases. Broader run including egress
mode tests, replacement-inode regression and coordinator is in progress.
No current full-volume, deletion, old CMD boot or startup acceptance claimed.
U1 remote merge reports already current. Official Codex session proxy remains
unavailable (10061); no coordination send claimed. U1-owned files untouched.

---

# U2 STOP: stale permission replay after owner chmod (D210)

### D210. Founder new-privilege stop on restart permission widening

The requested D209 implementation fixes directory bits on regular files and
journals exact original permissions. Its bounded Linux selection passes **77
tests, zero skips** (owner migration, metadata migration, volume inventory),
and targeted Ruff passes. Claude's required cross-family review completed with
exit 0, **ADAPT**. Full result read:
`C:/Users/Jonathan/AppData/Local/Temp/u2-d209-review-result.md`.

**AGREE** with finding 1: the completed owner-journal signature compares only
executable/non-executable status. After a legitimate engine chmod from 0644 to
0600, restart reuses the saved 0644 mode and `_permissions` reapplies it. The
same defect can restore directory group/other access after a restrictive chmod.
Although Claude classified this as correctness rather than floor, granting
permissions that the owner removed is the founder's explicit **new-privilege**
stop condition. Stop before patching or activating. This is source-review
evidence, not a reproduced cross-user read or production incident. New concern:
`docs/concerns/2026-10-06-u2-stale-mode-replay.md`.

**AGREE** with finding 2: atomic replacement of a vault/liveness inode loses
its journaled original; reverse then captures the forward broker-readable
ownership/mode as original and keeps it. Reverse needs the specified legacy
fallback for new protected entries. No fix was attempted after this stop.
Claude confirmed regular owner/sidecar files gain no execute/setgid/setuid,
originals survive known-inode direction changes, and U1 files are untouched.
D209 preserves pre-existing special bits on regular files as required by the
lead's exact-mode decision; it never introduces an absent special bit.

Current source changes remain **uncommitted and uninstalled** in wf-uid2 for
handoff, including the reviewed mode fix, inherited D202/D206 changes, classifier,
and unfinished coordinator. No code is newly certified for merge. A new draft
`tests/test_role_volume_migration.py` and coordinator accounting preflight/preview
precreation edits were prepared while review ran; they were **not executed or
reviewed**. Do not treat them as a full-volume receipt. No owner-delete admission,
old production CMD boot, or startup/healthcheck wiring was implemented. Activation
remains OFF; no production data was mounted, changed or deployed.

Per item: (1) D209 bounded mode tests pass but restart correctness now stopped;
full migration still incomplete. (2) Two-pass deletion remains unimplemented.
(3) Actual previous-production CMD boot remains unimplemented. (4) Startup and
healthcheck integration remains unimplemented, switch OFF. No task checkbox,
spec acceptance, deployed-SHA assertion or real-user app pass is claimed.

U1 remote merge was already up to date. Session coordination was unavailable:
official Codex proxy refused connection (10061), and vendor catalog returned
named-pipe ENOENT before dispatch. No message delivered or settings changed.
Both existing drafts (#4509 into U1, #4510 into main) must remain not merge-ready.
Only this stop/handoff documentation is committed; all source work is preserved.

Merged origin/main at adf29db4e790825eca212709c98f19d6b4125d4e without
conflicts, preserving all unfinished files. Plugin mirror rebuilt after that
merge; import probe passes and it creates no additional diff. The 77-pass
receipt predates this main merge and excludes the unexecuted coordinator draft.
Final committed-stack hygiene against origin/feat/per-role-uid-split passes:
325 added, 0 removed, 0 tampering (includes inherited main changes; excludes
the uncommitted source). Both base branches are ancestors of this checkpoint.

# U2 resume: lead mode decision implemented, verification in progress

### D209. Exact legacy owner-work and sidecar modes across rollback

The lead's 2026-10-06 decision supersedes D201's permission-widening rollback
semantics and releases D208's implementation stop. Regular owner-work files
(including SQLite -wal/-shm) retain their exact permission bits forward; only
UID/GID changes. Work directories retain their mode plus directory-only setgid;
the daemon access ACL is constrained by that original group mask. Restrictive
modes are handled by D10 deletion/reverse, not by adding execute or write bits.
Canonical daemon-owned roots retain D65's protected sibling traversal policy.
Protected vault/snapshot metadata retains its separately specified D4 policy;
no regular metadata file may acquire setuid/setgid/execute bits forward.
Sidecar regular files preserve their exact mode instead of receiving the
sidecar directory mode.

Journal original UID/GID/mode before mutation and carry it by inode across
restart generations and direction changes. Reverse restores those exact values,
removing forward-added directory setgid. New engine-created entries have no
legacy owner; reverse assigns them to daemon 1001 while retaining their mode.
Broker-private permanent identity reservations remain private in both directions.
An old metadata journal without original permissions refuses recovery rather
than inventing an original mode. The uninstalled D206 draft never served data.

Linux owner/metadata selection: 62 passed, zero skips, including exact file,
directory and WAL/SHM modes and original ownership in both directions, existing
crash boundaries, quarantine and real owner/foreign-owner denials. A subsequent
ACL helper cleanup and review remain to be verified. Activation stays OFF.
U1 merge reports already up to date. Both coordination routes remain unavailable
(proxy connection refused; vendor catalog named pipe missing); no send claimed.

# U2 migration lane

## Resume inspection: existing founder stop remains in effect

Resumed at `827d84c8797def5a6962124c4cc0732e4ac62e99`. Fetched origin;
`git merge origin/feat/per-role-uid-split` reports `Already up to date`.
The resume prompt describes a Docker interruption, but the latest committed
handoff is D208's new-privilege stop. Inspection confirms that `target()` still
assigns `SIDECAR_DIRECTORY_MODE` to regular sidecar files. The repeated founder
stop rule therefore stops this run before implementation or execution of that
migration. Existing uncommitted implementation and tests are preserved.

Draft PRs #4509 (U1 base) and #4510 (main) remain open. No new verified slice,
commit, push, deployment, or acceptance test is claimed by this inspection.
`git diff --check` passes. Prior test receipts below remain historical and do
not certify the unfinished worktree. Items 1-4 retain D208's incomplete status;
activation remains OFF. This receipt is a local handoff update only.

## STOP receipt: founder new-privilege rule (2026-10-06)

### D208. Stop on unintended sidecar permission widening

Claude's D206 review completed (exit 0), verdict **ADAPT**. **AGREE**: the new,
uncommitted metadata phase assigns directory mode 2710 to regular files under
`.universe-sidecars`, introducing setgid/execute bits; reverse then retains an
execute bit. Treat this as the founder's new-privilege stop condition, although
the library is uninstalled, startup remains OFF, and no production data was
touched. Do not patch or activate while stopped. Concern:
`docs/concerns/2026-10-06-u2-metadata-sidecar-mode.md`.

**AGREE** with the second correctness finding: changed stable configurations
(new centers or immediate work entries) currently refuse forever in both owner
and metadata phases. Restart generation support is incomplete. No journal
deletion or fail-open workaround is authorized by this handoff.

Verified and pushed slice: `36af744ac0` (inventory, broker main reconciliation,
protected-consent oracle). Then merged U1 D76/D77 at `af2bb19713` and latest
origin/main at `6b7a1d8a35`. D202 continuous-lock changes, D206 metadata/owner
changes and their tests remain uncommitted in this worktree. The full coordinator
`deploy/role_volume_migration.py` and later classification additions are unfinished
and untested: no full-migration receipt. Preserve them for inspection, not deploy.

### D207. Classification draft (unfinished)

The draft classifier uses the existing provider hidden-root mask boundary:
visible immediate work names, except broker `provider_definitions.json`, are
owner work; hidden canonical-root platform state remains protected. Hidden
entries beneath a work tree stay work. This draft has not completed validation.
The coordinator still lacks preview precreation, complete preflight accounting,
stable-generation handling and installed-image/crash proof. Its dry-run is not
yet a complete volume acceptance check. Broker allocation code is untested.

Per requested item: (1) verified owner/quarantine and inventory substeps only;
full migration incomplete. (2) two-pass deletion unimplemented; U1 owner-delete
class admission also pending. (3) actual old production CMD boot unimplemented.
(4) startup/healthcheck integration unimplemented; activation stays OFF.

Receipts from this run: Linux 91-pass inherited selection, 85-pass broker HTTP
selection, 14-pass inventory selection, 54-pass owner/metadata/accounting selection,
all zero skips; targeted Ruff passes on the tested slice. Plugin mirror rebuilt.
Production-image oracle passed at the D205 image documented below, before the
latest base merges and uncommitted D206/coordinator changes. These are bounded
receipts, not acceptance of the current full worktree. D203-D205 Claude APPROVE;
D202 Claude APPROVE; D206 Claude ADAPT with the stop above. Drafts #4509 (U1 base)
and #4510 (main) remain not merge-ready. No deployed-SHA assertion or live app
pass is claimed.

## Resume receipt (2026-10-06)

Clean worktree resumed at 33ab2b4696; merged the latest U1 base at
3577dc43c9. The local Docker Linux API is available again. The previously cached
`tinyassets-uid-u2:migration` image predates the committed ACL/setgid fix and
fails the repeat/no-op assertion; it is not accepted evidence.

Fresh production Dockerfile build `tinyassets-uid-u2:resume` passed (exit 0),
including the unchanged privileged-chain gate. Image:
`sha256:8287c58f42a9199847caa32460581cfc976c1730d67c825bf239c9bf751570c5`.
`python scripts/role_owner_migration_probe.py --image tinyassets-uid-u2:resume`
passed with the exact seven startup capabilities, no network or host volume:
dry-run, apply, repeat, reverse, nine journal crash boundaries, both quarantine
names/inode/bytes preserved, foreign and quarantine access denied, restrictive
engine-created files restored. Real owner/daemon children report no groups,
all five capability sets zero and NNP=1. `old_cmd_boot=false`, startup OFF.

The six-file Linux selection from the initial receipt was rerun with the exact
same command: **91 passed, zero skips** after merging U1. Targeted Ruff and diff
checks pass. Claude cross-family review returned **ADAPT**, with no floor
finding. **AGREE** with the quarantine-repeat unit coverage gap: added a full
metadata-stability assertion and separated probe assertions with a precise
snapshot diff. The strengthened Linux file passes **25 tests, zero skips**;
the production-image probe rerun passes. **DISAGREE_EVIDENCE** with the stale
red-receipt premise: inspecting the old image proves it lacks the committed
egid-around-access-ACL fix and special-mode readback. The fresh installed image
passes under the exact seven-capability set, without CAP_FSETID. No product
guard changed to obtain the passing result. The full reviewer output was read;
no second review was dispatched.

Coordination recovery remains unavailable: official Codex proxy returns 10061;
vendor app discovery returns named-pipe ENOENT before dispatch. No message was
delivered and no session owner/settings were changed. U1's delivery handoff is
read: full migration must classify/precreate `previews` with dedicated ownership
while preserving daemon-owned canonical roots. No U1-owned file was edited.

Full orchestration/protected metadata, two-pass deletion, actual old CMD boot,
and startup/healthcheck integration remain unimplemented. This receipt closes
only the installed-image proof missing from the initial substep.

### D201. Repeat evidence and migration mode semantics

No-op evidence includes the durable journal and directory metadata, not only
the count of ownership/name changes. The production probe reports both the
change count and exact differing snapshot entries on failure. Full metadata
stability is also asserted in the cross-tree quarantine unit fixture.
Forward work permissions intentionally provide owner read/write and retained
execute, and reverse provides daemon read/write/traverse: rollback restores the
old runtime's usable ownership, not the original read-only bits. This implements
D4/D10 and never changes bytes. Full protected-metadata migration remains separate.

### D202. One lock across all startup migration substeps

The full caller must retain one open `.layout.lock` descriptor across egress,
accounting, liveness and owner phases. Each substep can duplicate this description,
checks its inode against that volume's single-link regular lock, and takes the
same exclusive nonblocking flock. Closing a substep's duplicate cannot release
the caller's lock. Standalone substep callers retain their existing behavior.
The caller must still stop all writers; a flock is not a process quiescence proof.
Directory and layout-marker reads in egress planning use O_NOATIME so dry-run
does not change access times. No capability or runtime admission changes.

The combined Linux test executes all four substeps forward/reverse under one
lock and forks competing lock attempts between phases, preserving ledger bytes.
Wrong-volume descriptors refuse before mutation; forced-old-atime fixtures stay
unchanged during dry-run. Targeted Linux selection: **35 passed, zero skips**.
Production image `sha256:5c6d18db40ed085a667d77c082c28e181f65cb25c5537a1365ee0ea61622a042`
passed the expanded installed owner-migration probe, including competing lock
attempts throughout both directions. Claude cross-family review: **APPROVE**,
no floor/correctness finding; targeted Ruff passes. The broader existing
production-image oracle reached the HTTP-deposit consumer and failed because
the main merge removed `_HTTP_ACTION_CAP` but the stacked broker still imports
it. That run is not a full oracle pass. D203 records the merge reconciliation.

### D203. Reconcile the broker deposit with main's uncapped HTTP grants

Main commit 5e4090e05a (#4476) removes `_HTTP_ACTION_CAP` and stops attaching the
unused per-connection HTTP request cap. The stacked broker deposit retained the
deleted import, making every prepare fail closed. Remove that import and the
obsolete grant argument in the broker path as well; do not restore the retired
cap or alter the authentication, prepare/commit or conflict checks. Assert the
actual broker-created grant is uncapped and regenerate the plugin mirror.
This is a required merge reconciliation, not a new authority or activation.
Affected Linux tests: **85 passed, zero skips**. Targeted Ruff and regenerated
plugin parity pass. Image `sha256:bb95bc79da08457a44b82b3ad29f8eebd1cf9340cbdfe35e5c100f984f1e8746`
passes the original egress/accounting/liveness migration receipts and gets past
the deposit, then correctly refuses the legacy probe's bearer consent answer.
That broader run is still not a pass; D205 updates its transport.

### D205. Preserve interactive consent in the inherited launcher fixture

Main now requires a protected owner session for workspace consent. Keep the
original grant/result assertions, add assertions that the bearer answer refuses
and leaves the request pending, then use the real protected HTTP handler with
a disposable seeded owner-session cookie and its exact configured origin.
No consent guard is mocked or bypassed and no runtime authorization changes.
This is the same synthetic signed-in-owner premise as the existing application
tests, not proof of an IdP login or a live-user app pass. Revoke the fixture
cookie afterward; no production data or sessions participate.

Production image `sha256:f43b2fca35306478456fb7c683358936047cddc7b933bd1cc9740bf6635eed0e`
passes `python scripts/linux_oracle.py --production-image tinyassets-uid-u2:consent`
(exit 0), including the existing foundation, broker-consumer, restart, migration
and old-UID-write probes. This does not claim an old production CMD boot.

### D204. Complete stopped-volume authority discovery

Discover every `u-*` name (including incomplete trees) and legacy nonhidden
directories with `universe.json`, using pinned no-follow/no-atime descriptors.
Resolve their principal from the daemon's stored `founder_home` binding. A
tree without a home binding must have exactly one stored admin; missing
or competing records refuse instead of guessing from names, display text or
host_path. SQL views cannot substitute for authority tables. Read SQLite/WAL
through the existing private-copy snapshot helper so source metadata is unchanged.

Read the broker-private append-only reservation map without allocating. Report
unallocated principals separately; startup must allocate using the retired
broker identity before any owner chown. Invalid IDs, duplicates, or a missing
map after owner migration refuse. This inventory library is under construction,
not installed or wired to startup; no full migration completion is claimed.

Linux inventory tests: **14 passed, zero skips**, including missing and reassigned
permanent reservations after forward/reverse owner migration. Claude's D203-D205
cross-family review: **APPROVE**, no floor/correctness finding. AGREE with its
wording observation: the single-admin fallback applies to every non-home tree.
Multiple-admin trees refuse; switching authority models is outside this slice.

### D206. Protected metadata journal and restart generations

Use a separate root-private metadata journal under the same continuously held
layout lock. Validate all protected/shared entries before mutation, remove only
the obsolete broker owner-file name and recognized stale relay sockets without
following them, retain broker-private reservations in both directions, and
apply D4/D73 vault, liveness, relay and dedicated snapshot permissions. Work
subtrees remain the owner/quarantine phase's responsibility. Reverse restores
daemon-only metadata access; sealed snapshots never become engine-writable.
An interrupted direction must finish before reversal. Dry-run preserves metadata;
completed repeat avoids chmod, xattr and journal writes. This new library is not
installed or activated yet; full classification/orchestration remains pending.

Owner restart inventory also notices execute-bit changes on existing files and
marks a new journal generation migrating even when the prior phase was stable.
Linux owner/metadata/accounting selection: **54 passed, zero skips**. It covers
all six metadata crash boundaries in both directions, cleanup sentinels,
protected-link refusal and real owner/foreign-owner snapshot access. Ruff passes.
Latest U1 cell lifetime/application adoption and origin/main were merged without
conflicts after the D203-D205 push. Activation remains OFF.

Branch: `feat/per-role-uid-split-migration`, stacked on
`feat/per-role-uid-split`; initial parent `b8f9c258bd9a8ae20a0b57614b39c9b9af57e760`.
Activation remains OFF. U1 owns all engine-class launch admission, decoder and
ui-preview code. This lane does not edit those surfaces.

## D200. U2 mechanical migration journal and quarantine recovery

Reserve D200 onward for U2 decisions to avoid racing U1's appended design
numbers. The existing design D60, founder D61, D10 and D65 govern this slice.
The pre-drop owner migration accepts trusted broker-resolved center bindings
and classified work entries. It scans every entry including protected trees,
checks complete hardlink reachability, and writes a root-private inode/name
journal before any ownership or name change. Incomplete names, foreign labels,
protected aliases, special entries and changed descriptors refuse.

Cross-tree work aliases move by no-replace rename, preserving their original
relative names and shared inode beneath root-only quarantine. Each rename fsyncs
both parents. Resume matches the recorded inode at source or destination;
it never reclassifies a partly moved alias as sole-owner work. Reports alarm the
quarantined names without reading contents. Reverse retains this quarantine;
automatic restoration would put the same ambiguous names back into owner views.

Canonical roots remain daemon-owned with the reserved owner group. Work content
receives dedicated UID/GID, daemon access/default ACLs, no other access and
preserved executable capability. Root ACLs allow owner read/traverse and broker
traverse without engine modification of protected sibling names. Reverse scans
again to include restrictive engine-created content, restores daemon ownership
and removes extended/default ACLs. No migration authority survives service.

This is a substep, not full startup: complete center discovery and broker binding
resolution, protected metadata modes, shared-store inventory, orchestration with
egress/accounting/liveness, two-pass deletion and actual old-image CMD remain.
The top-level layout remains migrating until a full orchestrator proves all steps.

Coordination: session-control `codex_sessions.py loaded` failed with local proxy
connection refused (10061). No U1 message delivery claimed. This file records the
interface; regular origin/feat/per-role-uid-split merges remain the coordination
backstop. No U1-owned file edited.

## Initial slice verification

- Linux oracle: **90 passed, zero skips** on Python 3.11.17, git 2.47.3,
  bubblewrap 0.12.0. Command: `MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py
  --as-root -- -q tests/test_role_owner_migration.py tests/test_role_legacy_alias_scan.py
  tests/test_ta_op_modes.py tests/test_role_launcher.py tests/test_dockerfile_shape.py
  tests/test_privileged_chain.py --basetemp /tmp/b`. Root is required to exercise
  the real pre-drop chown/ACL window; access tests then retire to real owner IDs.
- Targeted Ruff and strict OpenSpec validation pass. No tinyassets source changed,
  so plugin mirror regeneration is not applicable. Always-sent prompts unchanged.
- First production Dockerfile build passed its privileged-chain check; installed
  migration probe and rebuilt-image execution remain pending at this checkpoint.
- Initial test snapshot helper changed directory atimes itself; corrected it to
  use no-atime descriptors. No assertion, test or guard was removed or weakened.
- Exact U2 release-critical paths: **2**, `Dockerfile` and
  `deploy/role_owner_migration.py`. Inherited U1 infrastructure remains outside
  this slice's review scope; a main-target draft contains that inherited stack.

The test matrix covers unresolved/foreign/protected aliases, FIFO and symlinked
roots, same-tree hardlinks, no-follow venv links, all nine forward/reverse journal
boundaries, faults inside chown/access/default ACL changes, namespace/identity
substitution on resume, restricted engine creations, ordinary restart writes,
quarantine pathname reuse, and real owner/daemon access denials. These receipts
prove the bounded migration substep, **not** full migration, deletion, old-image
CMD boot or startup. No full task checkbox is newly complete.

Cross-family review and production-image receipt are pending. Both session-control
routes were unavailable: local Codex proxy connection refused, app-tools discovery
pipe absent. No U1 message delivery claimed; no session takeover attempted.

# U2 continuation D216: verified work-name reconciliation; baseline handoff

D214/D215 remain unchanged and their prior approval is not reopened. D216 adds
coordinator-only reconciliation of completed work-name configurations with fixed
principal/numeric owner bindings. Interrupted phase configurations stay exact;
standalone phase callers stay strict. New tests cover forward restart, reverse,
dry-run immutability, repeat no-op, restrictive new content and interrupted
configuration refusal. This closes the visible-work growth prerequisite only.

Verification so far:
- Baseline root oracle: 150 passed, 1 failed, zero skips.
- Root oracle with D216: 153 passed, the same 1 failed, zero skips.
- Focused coordinator/inventory root oracle: 31 passed, zero skips.
- Ruff on all four changed Python files and diff checks pass.
- Production Dockerfile build and privileged-chain check pass. Image
  `sha256:4595e301b91c16cc6f6fdfc05c6529b3e739610c0b69db249879189ff910e123`.
- Installed owner migration probe passes, including nine crash boundaries,
  zero-capability/empty-group children, no foreign access and restrictive-file
  rollback. It explicitly reports `old_cmd_boot=false`, `startup_active=false`.

The repeated baseline failure is recorded in
`docs/concerns/2026-10-05-u2-baseline-oracle-failure.md`; no speculative fix or test
weakening is made. Under the founder's no-reopen instruction and AGENTS rule 7,
this evidence needs another implementation owner. Deletion must wait for fully
verified migration. Full previous-production CMD boot and startup/healthcheck
integration remain undone. The existing draft main PR is #4510; its inherited
U1 stack means it is not yet a U2-only diff (258 files before this continuation).
No history rewrite or U1 file edit is used to hide that dependency. Cross-family
review and final main merge receipts follow below. No deployment or app pass.

## D216 cross-family review and main integration

Claude via `peer-agents`, exit 0, **VERDICT: APPROVE**. Full review read at
`C:/Users/Jonathan/AppData/Local/Temp/u2-d216-review-result.md`. AGREE with strict
defaults, unchanged principal/numeric bindings, interrupted-journal refusal and
unchanged D214/D215 provenance. AGREE with the non-blocking metadata-marker
observation: include configuration changes in the migrating-layout condition.
Applied that correction and added a metadata crash/refusal/resume regression;
coordinator/inventory root oracle now passes **32 tests, zero skips**. No second
review round or provenance patch. The repeated baseline failure is a handoff,
not an approval of full migration or startup acceptance.

Merged origin/main `a97c17c26e` at `bd2d42ef46`. Plugin mirror regenerated after
the merge, import probe passes, and there is no generated diff. Ruff passes on
changed Python files and main-merge Python files. Merged-stack hygiene before
this final regression: 281 added, **0 removed / 0 tampering**. Static prompt
budgets are unchanged. Final post-merge test and image receipts follow below.

## D216 final verification receipt

Post-main-merge root oracle selection:
`tests/test_role_volume_migration.py tests/test_role_volume_inventory.py tests/test_command_center_agent_templates.py tests/test_command_center_system_browser.py tests/test_delivery_account_deletion.py tests/test_converse_turn_cost.py`
returned **118 passed, zero skips**. The subsequent metadata-marker regression
is included in the final coordinator/inventory run: **32 passed, zero skips**.
Both use `MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py --as-root -- -q ... --basetemp /tmp/b`.
This does not erase the separately reported 153-pass/1-fail baseline migration
receipt. No third retry of that repeated failure was used to obtain green.

Final merged-stack hygiene at `3fb4b00ab2`: **282 added, 0 removed, 0 tampering**.
Ruff, plugin mirror/import probe and diff checks pass. The post-main-merge
production Dockerfile build passes its privileged-chain check. Image
`sha256:154e60c7d51d25e11621fc2a30386024967a1f6b7c70faf29e0ae4fd5682b4c0`
passes `python scripts/role_owner_migration_probe.py --image tinyassets-uid-u2:d216-final`.
The receipt covers only the installed owner substep, explicitly retaining
`old_cmd_boot=false` and `startup_active=false`. It is not the required full
coordinator/old-CMD acceptance. Draft #4510 is updated with all limitations,
Claude APPROVE for D216, and the inherited-U1 isolation gap. Code and main merge
are pushed; this final commit only records receipts. Working tree will be clean.
