## Context

Two June legacy documents preserve a useful cross-artifact mismatch: the TINY
narrative records SQLite-canonical wording, while the research companion records
OpenClaw's markdown-source-of-truth/rebuildable-index model. They are provenance,
not ratified authority. PLAN owns the architectural decision; this active
change owns only the proposed behavioral contract until host-approved foldback.

On 2026-06-12 Google published **OKF v0.1** (`github.com/GoogleCloudPlatform/knowledge-catalog` → `okf/SPEC.md`): markdown + YAML-frontmatter files, one file per concept, cross-links as an UNTYPED relationship graph, reserved `index.md`/`log.md`; only `type` required; consumers MUST tolerate unknown types and SHOULD preserve unknown keys; broken links are valid; versioned `<major>.<minor>` with backward-compatible minor bumps; bundle root MAY declare `okf_version`. There is **no profile registry** — extensibility is free `type` + additional keys + GitHub-governed PRs. Host directive 2026-06-24: OKF is the brain's format foundation and the brain must auto-sync as it evolves.

Current live state: the brain write path is **unbuilt** and gated behind the Codex 6 pre-build gates; the designated first slice is a read-only `assemble(lens)` over the existing wiki. So the canonicality decision can be corrected before code locks it in. Codex review of THIS amendment: **ADAPT** (`2026-06-24-brain-okf-canonical-codex-review.md (archived in git)`) — direction approved, the six adaptations below folded.

## Goals / Non-Goals

**Goals:**
- Make the OKF bundle the proposed canonical source of truth and
  SQLite/FTS/vectors a rebuildable operational index, subject to host-approved
  PLAN foldback.
- Define a real commit protocol so durability is sound under public concurrency (not a naive dual-write).
- Map Tiny's typed entries onto OKF as a conformant producer/consumer with zero special-casing.
- Make durability, federation, and no-lock-in fall out of the bundle (git snapshot = the store; export = the bundle).
- Define an auto-sync obligation to OKF as a composable steward, with conformance validation as substrate.

**Non-Goals:**
- Unblocking or performing the brain build (still gated; this is a design amendment).
- Migrating the 1,183-page wiki (slice-1 reads it through a compatibility shim, no content migration).
- Building the `tinyassets/brain/` package, the commit protocol, the conformance shim, or the steward in this change.
- Inventing an OKF "profile" mechanism (OKF has none; use its native extensibility).

## Decisions

**D1 — OKF bundle canonical; SQLite is the rebuildable operational index.**
Rationale: host directive plus the research provenance; strengthens durability
(the git snapshot becomes the store, not a backup of a DB) and no-lock-in
(wholesale OKF export is native).
Alternatives: *keep SQLite-canonical* — rejected (contradicts the directive + the adopted index-is-disposable invariant; export/federation become secondary). *Dual-canonical* — rejected (ambiguous source of truth).

**D2 — Write-through under an explicit commit protocol; the bundle is the durability boundary.**
A write is applied transactionally in the operational layer (concurrency + candidate gate) and projected to the bundle; durability = "present in the bundle." Write-through **re-houses, and does NOT by itself resolve, research-impl Gap #4** — it requires: idempotency key, pending→durable entry states, atomic temp-file+rename projection, file locking, SQLite-transaction/outbox ordering, crash recovery, and rebuild reconciliation. `log.md` is human-readable update history (generated/appended); the **transactional journal/outbox is separate operational state**, never a single prose markdown file under concurrent writes.
Alternatives: *naive dual-write* — rejected (accepted-in-SQLite-but-not-in-bundle violates the durability rule; partial projection corrupts state). *bundle-only writes* — rejected (file-level concurrency is weak at a public endpoint).

**D3 — Typed fields ride as additional frontmatter keys; no formal profile.**
`type` is the only required field; Tiny's typed/scoped/lifecycled fields are additional keys OKF consumers SHOULD preserve. Conventions contributed upstream by community PR.
Alternatives: *OKF profile/extension registry* — rejected (OKF specifies none).

**D4 — Auto-sync is a composable steward; conformance validation is substrate.**
A forkable `[composable]` steward holds a vigil on the OKF spec repo under the
project's upstream-asset change-control principle, pins `okf_version`, and
PROPOSES migrations on minor bumps. The bundle store, the rebuildable index,
AND OKF-**conformance validation** are `[substrate]`.
Alternatives: *conformance + sync both composable* — rejected (validation is a substrate guarantee, not a forkable default).

**D5 — OKF compatibility shim for the existing wiki (not "already a bundle").**
The current wiki is not OKF-conformant as-is (root `index.md` carries `title/type/updated`; `[[wikilinks]]`). Slice-1 consumes it through a shim — lossless wikilink→Markdown-link projection, root-`index.md`→`okf_version`-only, `log.md` date normalization, and a `drafts/` = bundle-concept-vs-operational-staging rule. No content migration.

**D6 — Keep legacy provenance immutable; fold accepted architecture into PLAN.**
The June legacy documents remain evidence of the earlier conflict. Before this
change gates implementation or syncs behavior, the host-approved source-of-truth,
redaction, build-boundary, and backup decisions SHALL be recorded in PLAN.

## Risks / Trade-offs

- **Partial write-through** → an entry in SQLite but not yet in the bundle would violate "durable only once in bundle" → outbox + pending→durable states + crash recovery (D2); the bundle is authoritative on reconcile.
- **Latency vs durability** → if the API blocks on file write + log append + fsync/snapshot it may break p95<200ms → async projection off the write path; durability acked from the outbox, not the file fsync.
- **`log.md` hot file** under multi-writer load → `log.md` is generated human history, NOT the transactional journal (separate operational outbox).
- **Git-snapshot race** capturing half-projected state → atomic bundle generations / write-lock coordination at snapshot.
- **Redaction staleness** → operational index must stop serving FIRST (tombstone/block) before bundle delete + rebuild; secrets-class tombstones omit recoverable content hashes.
- **OKF is v0.1 Draft** → depend only on its stable core (markdown + frontmatter + non-empty `type` + reserved files); `okf_version` pin + steward absorb minor bumps; a major bump is a deliberate reviewed migration.
- **File-per-entry scale** (1,183+ entries) → implementation remains gated on
  explicit size-pressure caps, consolidation policy, and load proof; "just
  files" is not itself scale evidence.

## Migration Plan

Design proposal only — **no data migration, no runtime change**. The legacy
documents are not edited as authority. After host-approved PLAN foldback,
Slice-1 (gated, future) reads the existing wiki through the D5 compatibility
shim; later slices build the commit protocol, index rebuild, and steward behind
the Codex gates. **Rollback:** retire/revert the active change; nothing runtime
depends on it yet.

## Dependent-lane contract

**Updated 2026-07-25 — this change is archived; the contract moves to
`openspec/changes/build-brain-canonical-store/`, which is now the live owner of
the target.** Cite the successor, not this archived change. Two active lanes
carried `brain-okf-canonical-store` as a dependency; neither should wait on it,
and neither is unblocked by the archive. What a dependent may and may not rely on:

- **Historical, superseded 2026-07-25:** while this change was in flight it was
  the sole owner of the brain's canonical-store *target* — the OKF bundle as
  source of truth, the rebuildable operational index, and the write-through
  commit protocol. The host decision landed on 2026-07-25 and that ownership
  moved with the delta; `openspec/changes/build-brain-canonical-store/` is now
  the sole live owner. Cross-referencing those concepts rather than restating
  them is still the right pattern, but a dependent MUST cite the successor.
  Note the successor's delta is no longer byte-identical to the copy archived
  here: a 2026-07-25 correction scoped the source-of-truth and OKF-conformance
  requirements to the commons and the default brain organization, so the frozen
  text below reads as an all-brains OKF mandate that the live target no longer
  asserts.
- **May rely on:** the *shipped* OKF surface is `tinyassets/wiki/okf_export.py`,
  spec'd as-built in `openspec/specs/knowledge-retrieval-and-memory/spec.md`
  (curated one-way export; deliberately narrow local `conformant` flag). A
  dependent needing OKF behavior **today** cites that capability, not this change.
- **MUST NOT rely on:** any assembled-view / `assemble(lens)` runtime. It is
  unbuilt and ungated — there is no `tinyassets/brain/` package. A lane that
  needs assembled-view content today must either scope around it or own the
  build in its own change; `reconcile-universe-personification-relay` already
  does this correctly (its 2.3 records the mechanism as REVERSED and relocates
  the intent here rather than blocking on it).
- **MUST NOT rely on:** `openspec/specs/brain-canonical-store/` existing. It does
  not. The 2026-07-25 host store decision did **not** create it — that decision
  resolved the PLAN gate, while the spec path stays blocked on the as-built gate
  (`openspec/specs/` describes built behavior, and the brain store is unbuilt).
  It will exist when `build-brain-canonical-store` ships the behavior and syncs.
  A dependent that needs a stable path today cites
  `openspec/changes/build-brain-canonical-store/`.

## Open Questions

- Bundle topology: one physical bundle per universe with the commons as a union *view*, or a physical commons bundle?
- Is `drafts/` a set of bundle concepts (visibility=candidate) or operational staging outside the bundle? (D5 must declare this.) — **shipped code now answers this in one direction:** the OKF exporter treats `drafts/` as operational staging and excludes it from the bundle (`tinyassets/wiki/okf_export.py:15`, as-built in `openspec/specs/knowledge-retrieval-and-memory/spec.md:386`). That settles the *export* direction; D5 must still declare whether the canonical write path adopts the same rule, since exclusion-on-export and non-membership-in-canon are not the same claim.
- Render-time coexistence of Tiny's typed relations (`supersedes`, `evidence_refs`, goal-graph edges) with OKF's untyped body cross-links.
- Exact outbox/commit-protocol mechanism (SQLite outbox table vs WAL hooks) — deferred to the build slice.
- `okf_version` pin cadence + the steward's escalation thresholds (org-chart routed).
