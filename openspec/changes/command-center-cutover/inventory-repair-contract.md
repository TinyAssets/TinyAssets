# Inventory R1 repair: acquisition contract before the scanner

Status: **partly folded, 2026-10-02 (#4273).** The bounded repair landed -- the
scanner acquires its own private copy, opens no source store with a library,
reports `complete` / `coverage` / `exemptions` / `unscanned` / `deferred`
separately, and fails closed with no opt-in flag. What stays OPEN here is the
trusted-acquisition design below: an admission adapter over the existing
managed-writer fence, a producer-attested artifact, and decoders for checkpoint
serde and derived identities. Until those land the tool reports
`migration_ready: false` by construction, so it is still not a
migration-readiness oracle, and this file authorizes no production inventory.
Source: #4273 at `a2c28a3696f86936658e39c2be5d630734f33250`; original
[Codex R1 ADAPT](https://github.com/TinyAssets/TinyAssets/pull/4273#issuecomment-5947901737).
No source data, production inventory, credentials or host settings were accessed.

## Why the bounded patch cannot certify E1

E1 requires a zero-count migration oracle over operational fields decoded in each
encoding, excluding verbatim content, on a consistent production copy. E3.4 names
SQLite online backup. The existing script accepts any directory and has no
acquisition boundary. A directory called a copy, successful `integrity_check`, or
an `immutable=1` URI cannot establish how its database/WAL family or its JSON and
LanceDB state were captured. Per-database backup does not make independent stores
one cross-store snapshot. Existing raw byte scans also cannot identify hashed
dependencies or distinguish checkpoint operational identifiers from user text.

The existing layout fence is useful and must be reused: `storage_layout.py`
documents process-lifetime shared locks for managed writers, including host jobs
and children. However, `require_layout`, `check` and `_open_lock` can create/chmod
the lock or initialize the marker. None is a read-only inventory admission API.
An exclusive lock on an arbitrary already-copied directory also says nothing about
the copy's earlier acquisition. Do not introduce another fence or assume a
producer merely because a caller wrote a manifest saying `consistent: true`.

## Proposed acquisition and scanner boundary

One trusted acquisition invocation must capture and then scan its private artifact
without accepting a caller-supplied consistency assertion. It acts only on a
managed root for which all writer entry points honor the existing layout fence.
This requires an offline source window; the inventory must refuse a busy/live
root immediately instead of stopping services or waiting for them. Scheduling
that window or operating on production is separate host work, not this repair.

1. Add a narrow read-only admission adapter for the existing `.layout.lock` and
   `.layout.json`. Open the existing lock without create/chmod, with regular-file
   and no-link checks, and acquire exclusive/nonblocking. Refuse missing lock,
   missing/unknown/non-stable marker, unsupported lock/containment enforcement,
   submount/reparse traversal, or incomplete managed-writer coverage. Recheck the
   marker after locking. Never call the initializing public layout APIs.
2. Keep that fence for the whole acquisition. Managed daemon, workers, timer jobs
   and their children must be absent by the fence contract; prove this with the
   actual entry-point lock behavior on fixtures. A root under an uncontrolled
   independent writer cannot be declared consistent and is outside this mode.
3. Capture source bytes through rooted, no-follow descriptors into a fresh private
   scratch directory outside the source. Enumerate boundedly and reject links,
   special files and path replacement; never resolve a path outside the root.
   Copy a complete known database family under the same fence. Open SQLite only
   on that private family, then use its backup API to create the final standalone
   scan database. Recovery/sidecar creation is allowed only inside disposable
   scratch. Never open a source SQLite or LanceDB store with their library APIs.
4. Capture JSON, marker and LanceDB bytes during the same fence interval. Open
   LanceDB only on the completed private artifact. Release the source fence only
   after acquisition completes or is explicitly aborted. Record exporter version,
   captured path/type/byte hashes, layout, fence interval and completed categories.
   No raw contents or credential values enter the report.
5. The scanner receives only the artifact created by that invocation. A saved
   artifact may be reused only with independently trusted acquisition evidence
   and matching hashes. A plain user manifest/directory is unverified input and
   cannot produce `migration_ready: true`; do not build a new signing authority
   merely to accept it. Artifact-import trust is a separate follow-up unless
   existing trusted provenance can be demonstrated.

This is a proposed offline contract, not a claim that a read-only mount freezes
another writable mount, that SQLite immutability is established by an option, or
that a copied lock file authenticates the source. If the existing writer fence
cannot cover all acquisition writers, stop and return the specific uncovered
writer; do not silently fall back to live scanning.

## Scanner fold after the contract is accepted

- Classification is an exact creator-backed name registry. Add singular
  `workspace` from `fantasy_daemon/api.py`; remove generic Markdown, database and
  lock-extension rules. Sidecar/backup families derive only from registered DB
  basenames; a name like `agent-project.db.bak-x` stays unknown. Keep the explicit
  worker-supervisor family only with its documented creator. Cite each added
  name's creator; fixture expectations must stop endorsing arbitrary extensions.
- Separate operational schemas/fields and explicit verbatim exemptions through a
  creator/encoding coverage registry. Scan generated columns with `table_xinfo`,
  schema SQL including CHECK expressions and identifier literals, JSON keys and
  relevant values, and LanceDB identifier rows. Decode checkpoint serialization
  and account for derived-identity dependencies through their owning serializers.
  Unknown encoding/creator or missing decoder is incomplete, not zero. Tasks 3/4
  own migration of derived identities; that does not excuse E1 discovery coverage.
- Use bounded iterative traversal, pruning documented verbatim workspace/repos
  before descent and reporting each exemption. An unrecognized platform subtree
  cannot be silently pruned. Enforce entry, total-byte, file/value-byte, row and
  wall-time limits; cancellation returns an explicit incomplete report. SQLite
  gets a progress deadline; fetch only relevant affinity columns with bounded
  values/batches. Avoid sorting/materializing the full tree or loading whole
  oversized BLOBs before applying the limit.
- Report all unreadable, oversized, unsupported, unknown and skipped paths with
  category/reason. Only explicit verbatim exemptions count as covered without
  scanning content. Include known database backups. `--no-values` must omit JSON
  values as well as SQLite/LanceDB values and say the resulting report is partial.
  Distinguish row counts from matching cells; do not sum cell counts under a row
  label. Avoid exposing record values or secrets in examples/errors.

## Fail-closed limit and exits

The repaired scanner must report `complete`, `migration_ready`, `coverage`,
`exemptions`, `unscanned` and counts separately. A complete pre-migration report
may contain nonzero findings; only complete post-migration operational zero with
the required reader/digest proofs is migration-ready. A names-only report is
never migration-ready. Unknown entries, acquisition errors, unreadable stores,
unsupported encodings or exhausted limits produce `complete: false`,
`migration_ready: false` and nonzero exit by default. Removing `--strict` must
never turn incomplete into success. No incomplete category gets an apparent zero.

All of that paragraph is now enforced in code (#4273): the flags are reported
separately, `--strict` is deleted because unclassified entries fail by default,
`--no-values` cannot be complete, and `migration_ready` additionally requires an
empty `deferred`, which this round never is. There is no CLI success path for a
caller asserting that a root is a snapshot -- the tool makes its own copy, and the
only accepted input is a directory it copies itself. It still must not be used as
a migration-readiness oracle, for the reason it now states about itself: a
producer-attested acquisition and the derived-identity decoders are missing.

## Required bounded proofs and handoff

Use synthetic creator fixtures only. No real inventory, production probe, secret
read or deployment is needed to build the repair.

| Proof | Required result |
|---|---|
| Managed writer holds layout lock | Acquisition refuses promptly; no source file, marker or sidecar created/changed |
| Offline acquisition with WAL commits, JSON and LanceDB | One fenced artifact preserves committed state; all library writes stay in scratch |
| Missing marker/lock, malformed artifact or unverifiable writer | Explicit incomplete refusal; no initializing fallback |
| Symlink/reparse home/file/store, path replacement, FIFO/device/submount | No escape or blocking read; explicit refusal |
| Unknown extension names versus creator-known DB/sidecar/backup | Unknown remains unknown and exits nonzero |
| Oversized tree/BLOB/JSON, cancellation and SQLite work budget | Bounded consumption; listed incomplete category; nonzero exit |
| CHECK with nested expression, generated field, JSON actor, checkpoint and derived identity | Correct operational findings with verbatim bytes explicitly exempt |
| Same row matching several columns and names-only mode | Accurate row/cell labels; no value scan in names-only; no readiness claim |

Where that table stands after #4273's fold (`tests/test_command_center_inventory.py`):

- **Proven:** rows 5 (unknown stays unknown, nonzero exit), 6 (oversize, prune,
  deadline, entry and byte limits, SQLite progress cancellation), 8 (row versus
  cell counts, names-only), and row 2 minus the word *fenced* -- one disposable
  artifact preserves WAL-committed state and every library write lands in it.
- **Proven on POSIX only:** row 4. The symlink and FIFO cases `skip` on the
  Windows dev host (`os.symlink` raises WinError 1314, `os.mkfifo` is absent);
  descriptor replacement after `lstat` is covered on both. A Linux run is the
  oracle for the skipped two.
- **Partly proven:** row 7 -- nested `CHECK`, generated columns and JSON actor
  values are covered; checkpoint serde and derived identities are reported in
  `deferred` instead, and belong to tasks 3/4.
- **Open:** rows 1 and 3, which are the acquisition fence itself. The scanner
  does not refuse a root because a managed writer holds a lock; it detects that
  the source CHANGED during acquisition and fails on that. That is a weaker
  claim, honestly stated, not the fence.

Next owner on this file resolves the fence design; nothing in the folded scanner
depends on it, because it claims no more than it measures.
