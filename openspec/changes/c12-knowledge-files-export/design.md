## Context

ADR-017 makes editable files the default knowledge authority; ADR-018 requires one portable export. Today the OKF bundle is seed-only, `memory/`, `retrieval/`, `knowledge/` and `daemon_brain.py` own later state, and `wiki/okf_export.py` has no owner action. This change follows `owner-daemon-in-box/research.md` and its binding T1–T10 table: owner logic and files live in the box; the router forwards, the broker holds credentials and egress policy, and the waker holds only wake time. No shared service obtains a host path into an owner volume.

## Goals / Non-Goals

**Goals:** One canonical file tree per owner, default OKF layout, disposable in-box indexes, one complete export action, and deletion of second canonical stores.

**Non-Goals:** A new brain editor, provider-specific runtime, shared knowledge service, or extension of the C6 one-shot migration. Users may replace the default file organization while retaining the source/index split.

## Decisions

1. **Files win.** The owner's box owns reads, edits, conflict handling and indexing. Markdown with frontmatter, links and root `okf_version` is the seeded form. Index rows carry source identity and revision; a missing, corrupt or stale index is rebuilt from files. A failed file write does not publish a newer index. Existing SQLite/vector APIs may remain as in-box derived readers only until their consumers move. Alternative rejected: treating the current database and files as coequal authorities, which preserves S17's drift.
2. **Convert after C6, one owner at a time.** For each owner, take a verified owner-volume snapshot, stop that box's knowledge writes, render existing authoritative records to files inside that box, compare counts/content and a representative retrieval set, then switch its readers and writers to files. Resume only after successful verification. On failure, keep the owner on its pre-slice version and restore that owner's snapshot if needed; never run a shared host-side data walk or repeat the C6 fleet migration. Each slice removes its old write path after cutover. Alternative rejected: the closed PR #4903's broad pre-C6 migration.
3. **One export door.** An authenticated owner request reaches an in-box export assembler through the router. The same action serves UI and agent invocation and returns a downloadable folder/archive. It takes a consistent file snapshot, includes harness, roster, rules, workspace, wiki/brain, workflows, schedules, selected memory and UI layouts, plus a format manifest, README, local runner, license placeholder, `.gitignore` and `.env.example`. The runner uses the four tools plus `ta` and a user-supplied model connection; it needs no platform account. The box excludes credentials and private broker custody; the broker never packages owner files. Sharing/import consume this bundle format. Alternative rejected: wiring `wiki/okf_export.py` as a second, wiki-only export action.

### Delivery slices and deletions

| Slice | Move and delete | Exit proof |
|---|---|---|
| K1 — file authority | Seed and convert owner knowledge into editable files inside each box; move all knowledge mutations to those files. Delete database-canonical writes in `knowledge/` and the seed-only assumption in `universe_bundle.py`. | Edit a file, restart the box, and see the edit in reads; a database mismatch cannot overwrite it. Existing owner content survives per-owner conversion. |
| K2 — derived retrieval | Rebuild graph, full-text and LanceDB indexes from the file tree inside the box; retain one LanceDB connection per path. Delete independent corpus writes and shared/second-authority paths in `retrieval/` and `knowledge/`. | Remove indexes, rebuild, and recover the same results; shared router/broker/waker cannot open an owner-content path. |
| K3 — memory consolidation | Put durable selected memory and mini-brain knowledge into owner files; route `memory/` and `daemon_brain.py` consumers through them. Delete their second canonical tables, files and promotion/write paths as each consumer moves. Keep operational history and transactional data in their proper owner stores. | Restart and restore prove the same selected memory; no live knowledge writer bypasses files. |
| K4 — one export | Assemble the full bundle in-box and expose one owner action. Fold useful wiki conversion into the assembler; delete the unwired `wiki/okf_export.py` entry point and any duplicate export door. | Owner downloads, runs offline with a supplied local model, and imports the same bundle; credentials are absent. |

K1–K3 are independent deployment slices only where their consumer inventory proves no dual authority. Each slice deletes its replaced path before it is called done; no compatibility bridge becomes a new permanent store.

## Risks / Trade-offs

- **Incomplete conversion or concurrent edit** → Serialize per-owner conversion with box writes, verify content, and retain that owner's pre-slice snapshot until acceptance.
- **Index drift or cost of rebuild** → Version indexes by file revision, rebuild in-box, and treat index failure as a visible degraded read rather than silently preferring stale data.
- **Export leak or incomplete folder** → Allowlist bundle entries, exclude credential material, validate the manifest, and test local run and import from the exact produced archive.

## Migration Plan

Ship K1 through K4 after C6's owner-volume cutover. Roll back a failing slice by restoring only the affected owner's verified pre-slice snapshot and its previous serving version. Do not restore shared platform state. Close S17 only when its registry guard proves all four exit checks together and the obsolete canonical paths are absent.

## Open Questions

None blocking this spec. The implementation inventory determines the exact consumer order within K1–K3.
