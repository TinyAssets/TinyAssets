# U2 migration lane

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
