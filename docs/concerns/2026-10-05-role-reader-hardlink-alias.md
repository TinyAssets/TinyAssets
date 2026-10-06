---
severity: P1
title: Preplanted cross-owner hardlink leaks through daemon file and inspect readers
filed: '2026-10-05'
summary: 'D65: dedicated-label daemon reader matrix passes with 114 denials and zero foreign reads; D61 legacy provenance model passes. Full broker reader and actual engine-class matrix remain open; not a current-production exploit.'
---

## Founder D61 continuation

D76 recheck on production Dockerfile image
`sha256:93f86ca0e5ef9ac4894ad0cb3bf57497336cb6d4a6675874bc4eb879b43b7c36`:
**132 denials, 22 own reads, zero foreign reads**, including preview outputs;
all three namespace profiles deny read/relabel/copy. Independent Alice/Bob
decoder lifetimes preserve the dedicated identity boundary. This adds no new
actual engine class; retain this concern until the full matrix passes.

D68/D69 continuation: the dedicated reader probe remains **114 denials, 19 own
reads, zero foreign reads** on image
`sha256:9eb6a5ed405f5c57743d8782d9f159d212e308b7ca459f25b700346bfa16c5df`.
The bounded launcher now runs the actual image decoder under Alice/Bob's
distinct broker-allocated UID/GID pairs, with zero payload capabilities and
private namespaces. Final client image
`sha256:b5b3eb2a7d7450ce18754040be1e285e376fdaeef64ecb349010bcd2d564f36d`
passes actual PNG decoding, foreign/host path open denial, timeout reaping,
daemon-client reply authentication, fork closure and refusal recovery. This
data-free class binds no owner paths; it does not close the cell-writable-path
matrix for other classes. Broker reader completion, migration/quarantine and
startup root-binding validation remain pending. Keep this concern open.

The founder's explicit legacy reachability rule supersedes the D63 provenance
stop below. The revised diagnostic reports **114 ASSIGNED_LEGACY_BYTES, zero
FOREIGN_BYTES**, across the same three profiles and two relabel/copy variants.
These bytes are returned by design; this is not a claim that 114 reads were
denied. A separate cross-tree model preserves two names of one quarantined
inode, assigns neither owner, and proves daemon denial. Product quarantine,
full descriptor identity enforcement and actual engine matrix remain pending;
retain this concern until the entire matrix passes.

Read-only production census on 2026-10-05: 5 owner trees, 65,742 entries,
52,167 regular names / 52,162 sole-owner inodes; **0 cross-owner inodes,
0 unseen names, 0 foreign identities, 0 scan errors**. The 57 special entries
are **33 symlinks and 24 sockets**, not regular-file leaks. No file payloads
were read or printed, no symlink followed, and no production data changed.
This live observation is not a quiescent migration receipt.

D65 implements exact open-descriptor UID/GID checking for labelled owner roots,
including nested reader roots, and refuses resolving a symlinked read root.
Unlabelled legacy trees are not claimed as D60-admitted. Startup must validate
the root label against the durable broker allocation; full broker reader and
engine-class acceptance remain open. Main rechecked at
`b945fb3b3ab73d184e4bb91e4558d8e7f4ea8e96`: the shared common reader still lacks
the nlink and dedicated-identity predicates. The prior single-tree jail
reachability qualification remains; this is not evidence of a live exploit.

Production Dockerfile D65/D66 image
`sha256:acf2491ff6729c7d8d105b100eb1924a831b7ed09704d53b5a54fb47fa2a7392`:
`role_reader_alias_probe.py` exits 0 with **114 DENIED, 19 own positive reads,
0 FOREIGN_BYTES**, unchanged foreign bytes/metadata, daemon UID1001, zero
capabilities. Includes symlink, FIFO, hardlink, retired-hardlink, wrong-UID and
wrong-GID at six paths with actual file/platform/API readers and authenticated
inspect. `--legacy` preserves the original diagnostic. The dedicated namespace
probe separately denies read/relabel/copy in all three profiles; that is not
an actual engine-class launcher matrix. Keep this concern open.

The required D9/F2 reader matrix found a cross-owner alias that no-follow
traversal does not reject. `workspace_fs._open_regular_beneath` checks the open
descriptor's type and size but does not validate its hardlink aliases.

Run `python scripts/role_reader_alias_probe.py --image tinyassets-uid-relays:d55`.
The runner pins image digest
`sha256:7c8bb8846244365fc3c2806468f886767749304471342ef4d9aa96a0218c7a60`,
uses no host mounts or network, and retires the launcher's capabilities before
creating disposable synthetic owners as daemon uid 1001, groups 1100/1101/1102.

The real Alice inspect returns its ordinary activity control; Bob inspect is
denied by the real metadata gate. Preplanting Alice's `activity.log` as a
hardlink to Bob's 0600 private file then produces `FOREIGN_BYTES` through all
three readers. All six symlink/FIFO reader controls return `DENIED`. The source
bytes, owner, group, mode and mtime remain unchanged. Exit **3** with a non-empty
`failures` summary identifies the exposure; setup errors are not leak evidence.
Identity/groups and zero capabilities are asserted. Zero skips.
The container is discarded; no real owners or production data are accessed.

This is a preplanted-reader failure, not evidence that a uid-1003 cell can
create a cross-owner hardlink after the full migration. Full migration and
engine admission remain incomplete. Migration alias refusal alone does not
satisfy D9's independent preplanted daemon-reader requirement.

Founder instruction requires STOP/report for a cross-user exposure. D57
records that stop; no runtime patch or admission is included. A continuation
must address alias validation at daemon reads (preserving legitimate in-owner
hardlink semantics where required), rerun these controls plus the remaining
class/path/reader matrix, and keep startup inactive until all prerequisites pass.


## D58 repair evidence (2026-10-05)

The common open-descriptor reader now refuses `st_nlink != 1` before bytes
leave either read or copy. Production Dockerfile image
`sha256:832b7055dce8a3eb48e6a0af5c776aa81a55f7cd8fa3dc397304e42f7c62e37f`
passes the expanded `role_reader_alias_probe.py`: 57 denials, zero
FOREIGN_BYTES, all positive controls intact, foreign content/metadata intact.
Paths: activity.log, workspace/record.txt, wiki/page.md, canon/record.md,
output/record.md and logs/run.log. Three readers cover all six; authenticated
inspect additionally covers activity.log. This is not every actual engine's
class/path/reader matrix. Keep this concern until that matrix passes.

Current main was fetched and inspected at
`26993ec47c71a8fa36d62410bb361cf5ee8898c9`: its identical common reader checks
type/size but not link count; read_universe_file and the platform/inspect
chain use it. Per the lead's clarification, this is **not exploitable on
today's single-tree jail**: cross-owner paths are never visible to an engine,
so it cannot create the alias. The source reader gap exists but that fact is
not a current-production exploit. Closure when this change lands still needs
the full prescribed preplanted-reader matrix. No live data was tested or changed.

Descriptor uid/gid validation remains pending the D58 identity-design
clarification. D8 explicitly forbids substituting per-owner identities without
an amendment, while the latest D57 instruction refers to a per-owner group
that D1/D9 do not define. The implemented link-count guard does not establish
owner identity when a foreign inode has only one remaining name.


## Final D58 acceptance: surviving alias after original-name retirement

Cross-family review: ADAPT; AGREE with the remaining lifecycle hole. The final
probe adds `retired-hardlink`: preplant Alice's alias, remove Bob's original
name, invoke the readers, then restore Bob's name before cleanup and assert
unchanged foreign data/uid/gid/mode/mtime. Ordinary atomic replacement of Bob's
original name has the same link-count consequence. On image 832b7055dce8,
native exit 3 has a completed summary of **19 FOREIGN_BYTES**, alongside the
original **57 DENIED** cases. No live data is accessed. This supersedes any
interpretation that the initial 57-row pass resolves this concern.

AGREE with review's separate availability finding: `universe_tools.py`
`_promote_brain_files` links source to target then unlinks source. An interruption
or unlink failure can leave two links permanently; the stricter reader refuses
that brain file. Follow-up must repair promotion/recovery without creating a
cross-owner hardlink exception or deleting user data. No unsafe automatic
cleanup was introduced. This remains tracked here with the identity blocker.

## Lead D58 and D59 shared-UID counterexample

The lead now explicitly authorizes dedicated durable per-owner GIDs, superseding
D8's shared-group clause. The prior request for identity clarification is
resolved. The new diagnostic `scripts/role_owner_gid_probe.py` nevertheless
shows that keeping UID 1003 shared does not meet preplanted acceptance:

- Alice and Bob have distinct GIDs 200000/200001, setgid trees and daemon ACLs.
- A Bob engine-owned inode, aliased into Alice before launch with its original
  name removed, remains owned by shared UID 1003. Alice can read it even though
  Bob's group appears as unmapped 65534 in Alice's private user namespace.
- Under **cell-deny, cell-links and cell-nested**, with all capabilities zero,
  Alice can chgrp the inode to its own group. Independently, it can copy its
  bytes into a fresh own-group inode; this needs no chown syscall.
- Both resulting descriptors pass the proposed D58 uid/gid/nlink predicate.
  Actual daemon file/platform/API/inspect readers return **114 FOREIGN_BYTES**
  rows (three profiles, two variants, six paths, 19 reader/path pairs per case).

Command: `python scripts/role_owner_gid_probe.py --image tinyassets-uid-readers:d57`.
Native exit **3**, image
`sha256:832b7055dce8a3eb48e6a0af5c776aa81a55f7cd8fa3dc397304e42f7c62e37f`.
Own-file positive controls, foreign metadata denial, host-path denial and
unrelated outside data/metadata preservation pass. This is a synthetic
preplant diagnostic, not an actual-class launcher pass or an allegation that
today's jailed engines can plant such aliases. The script asserts the proposed
descriptor predicate explicitly; no D58 runtime implementation is claimed.

The explicit cross-user-exposure stop now applies to this demonstrated
shared-UID limitation, not the superseded ambiguity about GIDs. Dedicated
owner UIDs or another mechanism preventing access before engine execution
needs a lead security decision. No additional privileges or policy changes
were made. Keep this concern open until the complete class/path/reader matrix
passes; the promotion/recovery issue above also remains unresolved.


D60 follow-up (2026-10-05): founder now requires dedicated owner UID AND GID,
superseding D58. D61 adds a broker-private permanent identity allocator with
five zero-skip Linux transaction/storage tests. This is foundation only; it
has not repaired the reader/launcher matrix and is not a zero-foreign-bytes
receipt. Keep this concern until full acceptance, including promotion recovery.


D62/D63 evidence (2026-10-05): dedicated host UIDs/GIDs and kernel-bounded user
namespace mapping deny retired-name read/relabel/copy in real bubblewrap under
cell-deny, cell-links and cell-nested. However, a separate legacy-migration
MODEL counterexample (`scripts/role_owner_migration_provenance_probe.py`) returns
114 FOREIGN_BYTES if an unlabelled legacy 1001:1001/nlink1 foreign inode is assigned
the identity of its surviving pathname. Every resulting descriptor is exactly
Alice 300001:300001/nlink1. The source is synthetic, no product migration or live
service runs. Immutable production image:
`sha256:2ded0b0cdd8628d4bc77eba7d4cae55dfb2f51578448b7f906453256ae1be1b7`.
Native exit 3, positive/outside/metadata controls pass, profiles=3, attacks=2.
The original alias probe on that image still returns exit 3 / 19 foreign reads;
reader enforcement has not been implemented. This is a migration provenance
stop, not evidence against dedicated identities retaining foreign labels.
Main's current single-tree jails still do not establish an exploitable route to
plant these cross-owner aliases; no current-production exploit is claimed.
Keep this concern and the existing promotion-recovery caveat until full matrix
acceptance. The initial D54 snapshot PermissionError on the rebuilt image also
needs qualification: isolated and complete reruns passed, cause not established.


D72 follow-up: D60 descriptor enforcement is now present (D65-D71); the older
"not implemented" statements above are historical. Final D72 production image
6f79a8c34580e1a894c679e3c51ee573b81fac330cddadc028869e81e730ab66 passes the
reader alias probe: 114 denied, 19 own reads, ZERO FOREIGN_BYTES, foreign data
unchanged. The namespace diagnostic denies read/relabel/copy under all three
D9 profiles. Actual Alice/Bob git_bridge local operations now run through the
bounded owner launcher; foreign aliases and cross-owner capability-cache reuse
refuse, and a planted FIFO times out with subsequent request recovery. This does
not complete every engine class/path/reader pair, migration/quarantine, or the
promotion-recovery caveat. Keep the concern open. No main deployment occurred;
the earlier distinction between preplanted diagnostics and proven production
plantability still applies.

D74 regression receipt: final candidate
acb6a78042074461f9c4c45862ed697838a84fcd0558c90b4825699503860814 repeats
114 denied / 19 own reads / zero foreign reads, with foreign bytes unchanged.
No new engine-class/path matrix completion or main deployment is claimed.
This concern remains open.
