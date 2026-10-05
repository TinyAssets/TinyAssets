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
