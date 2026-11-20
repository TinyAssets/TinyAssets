# Sandbox sync file identity, November 19

- [x] Rechecked the runner's file offer, change detection for kept but ignored files, engine session staging, settled-time optimization, sync-in permissions, and the Owner's versioned write. The Owner commits the offered executable bit even when the bytes match. The runner's durable mirror records a base version, byte length, modification time and digest. It forgets the executable bit. It also treats old modification time plus length as sufficient evidence that the bytes still match.
- [x] On main source `f934871ad`, two ordinary Node 22 runner regressions failed (23 tests outside the filter). A local file changed from plain to executable with the same bytes. Another five-byte file received different five-byte contents and had its original timestamp restored, as when copying with preserved times. In each case the first sync committed the file and the next sent zero operations. The tests require exactly one update and a quiet third sync.
- [ ] Unfinished. Preserve the mirror's recorded base version, including disks written by an older runner. Check the format's expand/contract requirements, rollback, metadata-only updates and the bounded cost for untouched files. Compare put, fetch, held-back reporting and session-cache consumers before choosing a representation. Verify a separate owner commit and a fresh sandbox readback. This branch changes tests only.

- [x] Rollback prerequisite. The current runner prepares a five-value positional insert when opening the durable mirror. On a copy of the real format-one disk, adding one nullable column caused SQLite to reject the runner before it could read the cursor or a base. Name the five fields this writer owns. The real mirror then preserved cursor 7 and base 3, wrote base 4, and cleared the newer row metadata when replacing it. All 14 ordinary Node 22 disk-format and protocol tests passed. This change still writes format one. A new format needs the two ordinary deployment phases and tests in both directions.
- [x] Owner isolation. On unchanged main Owner source, one ordinary Workerd case used a real lease to commit the same bytes as plain, executable, then plain. Each public read returned the expected mode and original bytes with a new version and etag. The two omitted runner updates remain open on this intermediate commit.

- [x] Historical mode discovery. Two ordinary Workerd cases passed on the unchanged Owner with the compatibility writer staged in `dcfbd94f`. Two independent owners had identical file bytes but different original executable bits. A one-row change-feed read at the saved base returned the correct mode in each case. Once both advanced to the same plain file and each owner purged its history, the same read returned the newer version. The public entry, bytes, etag, base number and remaining feed observations matched across histories. A current bit cannot establish a lost old base. Preserve the old base and local file during reconciliation.
- [x] Separate session-cache consumer. On Node 22 and 24 the real session staging function with synthetic directories staged the five bytes `alpha`, omitted their five-byte replacement `bravo` at a restored timestamp, and staged `bravo` once the timestamp changed. That cache is disposable across format changes. The durable mirror keeps base versions.

- [x] Session staging now keys its disposable digest on the file's device, inode, size and nanosecond status-change and modification times. A sample within the existing coarse-tick window is hashed again. Rows from the prior cache lack a matching stamp and are rehashed. A real filesystem and the ordinary staging entry point in a focused Node 22/24 runner test both delivered the same-length replacement at its preserved timestamp; a third settled scan performed zero reads of the already-held file. The original staging function missed the second write. The separate durable workspace mirror still has the two failing regressions above.

- [x] Session cache upgrade and rollback. Eight calls per Node 22 and 24 used the original and corrected bundled staging functions against the same synthetic store and cache. Old to new, back to old and forward to new delivered every rewritten five-byte session. Both settled repeat reads were quiet, and two recent rewrites were delivered. The sandbox Worker and guard suite passed 140 cases on the corrected runtime. A test-only mock initially caused two TypeScript errors from production path literals; the corrected fixture passed all 15 session and format cases on each Node version and the full workspace typecheck. The new runner and probe bundles built successfully.

- [x] Combined behavior. Reconciled `aa341901` with the staged runner digest `493276e2`, ignore matcher `534e6bcb` and the renewal, runner-write and import/restore changes. The three runner test files had 36 passes and four size cases excluded on Node 22. Both known workspace identity regressions still failed. Both native Owner mode/history cases passed on that combination, and the full workspace typecheck and runner bundle build passed. An independent refusal/retry check exposed loss before change detection even starts. [Refused workspace retry, base tests and next dependencies](https://github.com/TinyAssets/TinyAssets/commit/5a694c62f72536a197ba566bf8ace79964c11daf). Preserve held local edits across incoming updates before adopting a new disk format.

## Two conversations using the same engine store

- [x] On 44e44370 two Codex run homes copied the same two synthetic session
  files. Each run edited a different file. Each call that returned a home
  to the store held the normal engine lock. The run finishing last restored
  the other file to its original bytes. Reversing the finish order reversed
  which edit was lost. Both outcomes occurred on Node 22 and 24 using the
  actual makeHome and keepHome functions with temporary state directories.
  Conversation serialization does not serialize distinct conversations.
- [x] Added an ordinary runner regression with two different conversations
  and a held CLI so the homes overlap. It requires both session edits in the
  final store. Privileged execution remains outstanding. This is a staged
  regression and design finding.
- [ ] Compare what each home inherited before writing a session back. Include
  reboot recovery, sign-in, probes and a file genuinely edited in both
  homes. Format one records the copied credential digests but no session
  baselines. Reconcile historical homes and the expand/contract rule before
  choosing a durable representation. Preserve both copies when a genuine
  divergence still needs a decision.
- [x] A separate 8,388,639-byte synthetic session went through sync-in,
  import into the engine store, home creation, writeback, staging and
  sync-out on Node 22 and 24. The resulting bytes and mirror digest matched.
  Allocation observations showed one whole-file buffer at store import and
  home creation each, two at writeback and two at initial staging, plus a
  whole Latin-1 string at staging even with no scan terms. A settled repeat
  staged nothing. The upload read in 4,194,304-byte pieces. These are
  observations of individual allocations, not measurements of process RSS.
- [ ] Use the existing chunk reader and atomic writer to bound session
  copying across those consumers. A different hash function alone cannot
  remove the whole-file reads. Preserve per-store locks, no-follow reads,
  refusal and retry semantics, and correct cache identity. The number of
  paths in the cache and the mirror lookup needs separate review.

### Delivery order between conversations

- [x] On 44e44370 two independent run directories staged older and newer
  bytes for one path. Both delivery orders ran on Node 24 with the real
  staging and upload code and one persistent mirror. In a separate Node 22
  Owner Worker test the newer content created version 34. The delayed older
  offer used version 34 as its base and the Owner accepted the replacement
  without a conflict. A public read returned the older bytes. Another sync
  can recover the newer content if the warm store still retains it.
- [x] Independently varied the number of already-held two-byte sessions.
  For 1, 64 and 256 files the single mirror lookup contained 1, 64 and 256
  paths (60, 3,831 and 15,507 JSON bytes). The local cache contained 151,
  9,655 and 38,803 bytes. All three stages offered zero changed files.
  This is path-count evidence, separate from the byte length of a transcript.
- [x] The ordinary scheduling controls allow overlap between distinct
  conversations and serialize one conversation. Platform types passed for
  the new two-conversation regression at 2937dcf5. The privileged runner
  stopped at its root preflight, exit 2. Joined native controls on Node 22
  and 24 still reproduced loss in both finish orders after changing inbound
  copies to bounded reads.
- [ ] Resolve the provenance of bytes at both writeback and upload. A new
  mirror version describes current remote state and cannot establish the
  base of an older staged offer. Include both homes changing one path,
  interrupted runs and warm-store loss. Preserve the last recoverable copy
  through cleanup. The baseline and ordering decisions are still open.

### Existing homes after an interruption

- [x] On 2208c72f, five synthetic histories went through the actual home creator and keeper on Node 22 and 24, once for ordinary writeback and once for completed sign-in (ten cases per runtime). In three histories, the current home bytes, current store bytes and complete serialized snapshot were identical across histories. Only the home had changed in the first, only the store in the second, and both in the third. All three wrote the home bytes to the store. This preserved the local edit in the first case, reversed the newer store edit in the second and lost one edit in the third.
- [x] A newly created home transcript and an untouched inherited transcript removed from the store also gave identical home bytes, absent store entry and serialized snapshot. Both ended with the transcript in the store. These comparisons establish what the format-one snapshot and current byte values can tell recovery. Filesystem timestamps were not used as evidence of origin.
- [ ] Resolve historical homes separately from new snapshots. Retain both candidates and the fact of a missing store entry when origin is unknown. Define where interrupted work survives cleanup and how it re-enters reconciliation. For new homes, verify a baseline for each inherited session and one shared rule for ordinary finish, sign-in and reboot. The delayed upload has its own base to preserve. The earlier two-conversation root regression and coordinator integration remain open.
