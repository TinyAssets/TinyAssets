---
severity: P1
title: Preplanted cross-owner hardlink leaks through daemon file and inspect readers
filed: '2026-10-05'
summary: 'D9 preplanted alias acceptance fails on production image 7c8bb8846244: an Alice activity.log hardlink to a synthetic Bob file returns Bob bytes through read_universe_file, _read_platform_text and authenticated Alice inspect. Symlink and FIFO controls refuse. Engine planting after migration is not established.'
---

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
chain use it. The preplanted cross-owner alias therefore matters for the
single-UID design too: both owners' files are readable by uid1001. This is
source evidence of the reader gap, not proof that a production engine can
plant the alias, and no live production data was tested or changed.

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
