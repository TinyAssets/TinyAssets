## Context

This is the sole implementation contract for parent `universe-agent-harness` §4.14/D10 seed provisioning and upgrades. `starter-agent-out-of-plumbing` supplies the bundle content and solely owns consumer/renderer cutover wiring and the all-center/dormant-center proof (its task 2.3). This change supplies the prerequisite transaction API and its storage/recovery proof; D7 owns extraction retirement. Current `read_operating_instructions` recreates missing `AGENTS.md` and substitutes defaults for empty/unreadable files. The starter slice removes that per-turn behavior.

## Goals / Non-Goals

The additive `clean-agent-start` change supplies the L9 three-personal-file
profile, conservative legacy classification and sparse-reader acceptance. It
uses this same manifest/receipt API; no second seed bundle may claim the same
paths. Its delta syncs after this capability and leaves these transaction
requirements intact. Clean-profile personal files without receipts are preserved
even when they match predecessor templates; legacy companion files are never
deleted by that migration. AGENTS/hooks cutover remains the starter consumer's.
The clean profile publishes no predecessor hashes for its three personal paths,
so only installed receipts select their automatic updates under this mechanism.

Automatically deliver current seeds without changing owner customizations or making an absent owner a deployment dependency. Use the same receipt for every center. Do not build a second package system, alter D9 quarantine, migrate lessons, or preserve legacy runtime modes.

## Decisions

### One manifest and one receipt

Publish immutable `starter-agent-v1` with version, normalized relative paths, SHA-256 hashes and exact known predecessor seed hashes (including historical stock `AGENTS.md`). Only platform-published templates qualify; never infer stock content by fuzzy matching or use the founder's private files. Reject escaping paths and symlink/reparse traversal.

Store one owner/center-scoped receipt outside the agent-writable tree: bundle version, transaction ID, per-path prior state/hash, installed hash (or explicit never-installed state), outcome, and references to prior bytes needed for Undo. New-center provisioning writes this receipt in the same transaction as files. It is bookkeeping, never injected policy or an authority grant. Deletion tombstones persist across later versions and Undo.

### Receipt storage contract (schema version 1)

Use one SQLite database at `<data>/.universe-sidecars/<canonical-center-key>/starter-seeds.sqlite3`, beside existing daemon-owned center state, never inside the command-center tree. WAL/SHM and the per-center seed lock stay in that same sidecar. Resolve the canonical center key and immutable owner/center IDs from the authenticated platform binding, never a caller-provided path, display name or agent instruction. The existing sidecar resolver must refuse links/reparse traversal; no jail mounts this storage. Backups and center deletion follow the existing sidecar lifecycle. Moving this store into owner-writable content is not an implementation option.

All tables below carry non-null `owner_id` and `center_id`. Every primary/foreign key starts with that pair, every query/update is scoped by both, and opening the database verifies its singleton binding row matches the authenticated pair. A mismatched binding fails closed before reads or writes. UUIDs and paths alone never authorize lookup. Schema version is recorded in `seed_binding(owner_id, center_id, schema_version)` with exactly one binding per database; unknown/newer versions fail visibly without resetting the database.

| Table / key after `(owner_id, center_id)` | Persisted fields and role |
|---|---|
| `seed_receipts`: `(bundle_id)` | Current committed bundle version and manifest SHA-256, last committed transaction UUID; one current receipt per bundle, with historical receipts retained in transactions. |
| `seed_paths`: `(bundle_id, relative_path)` | Normalized center-relative path (including an agent directory prefix where applicable), `ever_installed`, last installed version/hash, latest outcome, owner-choice enum (`automatic`, `preserved`, `undo`, `deleted`), explicit deletion tombstone flag and source transaction UUID. A never-installed path has `ever_installed=false` and null installed hash; deletion retains installation history. Tombstones/owner choices survive new versions and Undo until explicit hash/absence-bound adoption. A path cannot be owned by two bundles. |
| `seed_transactions`: `(transaction_id)` | Bundle ID/version, manifest hash, operation (`provision`, `upgrade`, `adopt`, `undo`), prior transaction for Undo, phase (`prepared`, `applying`, `committed`), created/committed UTC times. Automatic provision/upgrade retries reuse a unique `(owner_id, center_id, bundle_id, version, operation)` transaction; explicit adopt/Undo uses a persisted request idempotency key. No runtime-selection flag. |
| `seed_transaction_paths`: `(transaction_id, relative_path)` | Durable journal: expected prior kind/hash, prior-byte blob reference if readable, intended action/hash and target-byte reference, per-path phase/outcome and resulting installed hash/owner choice/tombstone. `transaction_id` references the same owner/center transaction. Unreadable/linked paths are journaled as preserved without reading targets or inventing prior bytes. |
| `seed_blobs`: `(blob_id)` | UUID-keyed exact byte BLOB, SHA-256 and length for prior/target/candidate content. Journal blob foreign keys include the owner/center pair. No global content-addressed store or cross-owner deduplication; even identical hashes cannot resolve another center's bytes. Retain every blob referenced by history or an available Undo/candidate. |
| `seed_notices`: `(bundle_id, version)` | Durable notice payload containing per-path outcomes, former-defaults diagnostics and same-scope candidate/transaction references, plus delivery status and stable notification idempotency key. Persist with receipt commit; replay delivery by this key. Undo/adoption updates the same version notice. |

The journal, receipt, tombstones, notice outbox and Undo bytes live in this database, not free-floating JSON files or indexed skills. SQLite commits their state atomically, but cannot atomically commit workspace files. Therefore "same transaction as files" means one recoverable filesystem transaction: durably stage intent and exact prior/target bytes before writes, conditionally apply files, then atomically commit receipt/path state and notice. Hold the scoped lock and do not expose a new index or serve a turn between those phases. Recovery reconciles each prepared path with actual path identity/hash before continuing; divergent bytes are preserved and reported, never overwritten from the journal. A storage failure prevents that turn with a visible technical error. A completed receipt is never published before its file outcomes are reconciled. No unjournaled provisioning path is allowed.

Authenticated owner/center checks also apply to notices, candidate reads, recovery, adoption and Undo, not just installation. Add isolation tests using two owners and two centers of the same owner, with identical bundle/path/hash values and forged transaction/blob IDs; neither private bytes nor tombstones, history, notices or writes may cross the bound center. File mutations retain the existing governed write boundary and no-link checks.

### Autonomous per-path default

| Observed path | Automatic action |
|---|---|
| Absent, proven never installed | Exclusive create with the new bundle bytes. |
| Regular file exactly matches its last installed hash or a published predecessor seed hash | Atomically replace with the new version, conditional on the observed hash still matching. This includes stock `AGENTS.md` and stock skills. |
| Modified or unknown existing bytes, including empty files | Preserve byte-for-byte; one visible version-keyed note offers a candidate/diff. Unknown files are conservatively treated as customized. |
| Linked, unreadable or otherwise unverifiable path | Preserve without traversal; report the issue and offer the candidate. Never treat it as missing. |
| Absent after recorded installation, or a known historical seeded path absent before receipts existed | Preserve the deletion and record a tombstone; offer an explicit reinstall. Legacy missing `AGENTS.md` is conservatively an owner deletion. |

Fresh provisioning proves never-installed status. A legacy manifest records which paths previous provisioning could seed: newly introduced `starter/hooks.md` and five starter skills may be exclusively created alongside untouched custom/empty/linked/unreadable/deleted `AGENTS.md`, while absence of previously seeded instructions is not guessed away. Hook loading and content are specified by the starter consumer; AGENTS.md customization alone must not suppress delivery of these new paths. Owner edits, emptying, removal, and explicit file Undo are customization choices. Future versions never reverse those choices automatically.

Receipt-recorded owner choices and deletion tombstones take precedence over stock-hash matching. In particular, Undo can restore bytes that happen to match a published predecessor; those bytes remain an explicit owner choice, not permission for the next upgrade to reapply what was undone. An owner can explicitly adopt a later offered version to resume automatic stock upgrades for that path.

Only applying a candidate over customized content or reinstalling an owner-deleted file needs the owner's choice, bound to the current hash/absence. That optional choice never gates runtime activation. Existing customized skills win by normal index precedence; the new content remains available as an inert candidate in the scoped receipt store, outside the indexed tree. A collision or owner deletion of hooks/skills can weaken that route and must be named with the unavailable guidance in the note; preserving custom AGENTS.md alone does not remove resident hooks. No case keeps a center on the old renderer or silently substitutes stock instructions.

### One cutover, file-only Undo

There is no prepared/active/declined owner-acceptance state machine and no per-center legacy-renderer flag. At the coordinated release boundary, every center uses the new renderer and generic skill index, including dormant centers and those with collisions or no response. Before its first new-renderer turn, run or resume its automatic file transaction. A dormant center may defer file IO until wake; it never serves another old-renderer turn. The old renderer is removed in that release, not after a count of owner responses falls to zero.

This release boundary is the hard cutoff; a separate grace-period date would retain compatibility unnecessarily. Deployment records its actual UTC cutoff time and SHA. D6 reachability and starter proof are technical preconditions; waiting for human review is not one. No default grants authority or alters protected rules.

Lock by authenticated owner/center pair in its sidecar; stage the database journal above; apply safe exclusive creates/compare-and-swap writes; expose completed files, receipt and index at a turn boundary. Coordinate concurrent writes with the existing file-write boundary, rechecking path identity/hash without following links. A race converts that path to preserved-customized and does not hold other paths or activation. Recovery resumes the same transaction idempotently before serving, without replaying a completed write or duplicating the note. Real storage failure is reported and retried as a technical failure, never converted to permission waiting or a fallback renderer.

The visible notice lists automatic upgrades/additions, preserved paths and the offered version. Its one-click Undo reverses only transaction writes whose current bytes still equal the installed hash: restore prior bytes for replacements, remove only unchanged newly added files. Changed/deleted files remain untouched and are reported. Undo records the owner's customization and stays on the new runtime. It never re-enables the old renderer or extraction. Existing private content used for Undo stays owner-scoped.

For legacy empty `AGENTS.md`, the note must say: "Your instructions file is empty; you were previously running defaults. It was kept unchanged. The new runtime no longer substitutes defaults; starter/hooks.md now supplies the moved guidance alongside your instructions." For linked or unreadable files, replace the first clause with the observed condition and explain that the old runtime supplied defaults because it could not safely read the file; never follow the link to diagnose its contents. List the installed hook/skill paths and edit/delete/Undo controls. If hooks or skills could not be installed, replace any claim that they now supply guidance with their actual preserved/error outcome, unavailable guidance and candidate offer. Persist and show this condition-specific explanation even when no files changed or the owner was dormant; a generic preserved-path list is insufficient.

## Migration Plan

Native CLI launches take the same shared sidecar file lock before spawning.
The descriptor is held by bubblewrap via `--sync-fd` until its sandbox ends,
including direct native writes that do not call the engine. The daemon closes
its descriptor after spawn without unlocking the shared open-file description.
An exclusive migration cannot enter while that descriptor survives. Launch
failure closes all descriptors; the lock file is never mounted inside the box.
Concurrent engine tools retain shared access. This extends the existing boundary
without removing native inference or its file-writing capabilities.

1. Land this single D10 mechanism and verify new-center receipts before consuming it in the starter release; no implicit per-turn seeding. Resuming a versioned release transaction before a dormant center's first turn is distinct from recreating missing files on each turn.
2. Expose the versioned provisioning/upgrade/resume API and per-path outcomes to the starter consumer; test against stock/custom/deleted fixtures with a manifest supplied by the test.
3. Depend on `starter-agent-out-of-plumbing` task 2.3 for publishing/consuming the bundle at the release boundary, renderer wiring and all-center/dormant-center integration proof. This change does not own or repeat that work; its API emits a durable notice even when no path can change.
4. Verify transaction retry/Undo and owner/center isolation here. Later versions use the same rules and receipts; no second D10 rollout implementation.

## Risks / Trade-offs

- Missing legacy receipts obscure deletions: preserve absence of historically seeded paths; add only proven new paths.
- Concurrent editing or crashes could lose data: conditional writes, durable journal and a data-loss mutation matrix cover replacement, create, recovery and Undo.
- Preserved custom guidance may be obsolete: visibly offer current content; owner instructions take precedence and runtime cutover still completes.

## Open Questions

None. Receipt location, keys, journal/Undo storage and owner/center isolation are fixed above; implementation may choose indexes and helper structure without changing that contract. No external service or owner-approval gate is required.
