## Context

The supplied muse-pi-gap audit (2026-10-04, baseline `9e96ff9595`, B3/L9) identifies `universe_bundle.BASELINE_FILES`, the first-contact `soul.md` sentinel, Loop-branch parsing and soul-edit governance as coupled to the old baseline. The sentinel must change because owners can delete Soul without deleting their center. `MEMORY.md` already has item IDs and history. D10's existing proposal defines receipt storage and recovery; it remains the sole writer of seed transactions.

Founder direction supersedes the audit's suggestion to seed a fourth empty `AGENTS.md`. The personal baseline is exactly three files. Harness instructions, hooks and skills are editable content supplied by the separate starter change; this is not a claim that a running center contains only three filesystem entries. The platform supplies no LLM; all model calls use that owner's connections.

## Goals / Non-Goals

Give new owners a readable personal baseline and preserve existing bytes. Keep all three files inspectable/editable through D7 and the owner-permitted harness bridge. Do not build the editor, a seed database, renderer cutover, learning retirement or a new identity/authority store here.

## Decisions

### Three personal files, stable paths

Keep on-disk case and names: `soul.md` (Soul), `MEMORY.md` (Memory), `identity.md` (Identity). Renaming to title case would break callers and behave differently on Windows and Linux. Soul contains a short plain-markdown default describing persona, voice and owner-editable boundaries; Identity has empty Name and Vibe fields; Memory is empty, with no invented facts or synthetic memory IDs. Neither Soul nor Identity contains OKF frontmatter, remote tracking links, founder oaths or a catalog of absent documents. A supplied purpose/name remains verbatim user content, never a stock hash candidate.

`AGENTS.md` is absent until the owner or the separately versioned starter supplies it; no per-turn recreation or fallback. Other thirteen-file baseline paths (`index`, `log`, `soul.edit`, `founder`, `orgchart`, `projects`, `goals`, `body`, `origin`, `soul_versions`) are not eagerly materialized. Meaningful learning or an actual governed edit creates only its needed companions. Empty scaffolding is not learning.

### Extend D10 without competing ownership

Add a versioned clean profile to the existing starter bundle manifest and per-path receipts, using the same bundle/path ownership. This is a content revision of the existing bundle, not a second bundle claiming `soul.md`. The seed-lifecycle change still owns locks, journal, blobs, notices, recovery and conditional Undo. The starter consumer still owns all-center renderer integration. This change owns the three templates, legacy classification inputs and reader adaptation only.

Publish no predecessor seed hashes for these three personal paths in this profile. D10's predecessor-hash upgrade rule therefore cannot select unreceipted personal files; only its installed-receipt branch applies. Preserve the original AGENTS/hooks predecessor lists. This reconciles the additive delta with D10's existing normative automatic-upgrade rule.

The delta adds uniquely named requirements under `starter-seed-lifecycle`; it does not copy or replace the parent requirements. Merge/sync D10 first. D7's existing editor work consumes the three labels and paths. D8/D9 agent templates retain their separate installation/main-selection responsibilities.

### Never clean up legacy content automatically

| Observed path | Clean-profile action |
|---|---|
| Fresh center | Install the three personal files with D10 receipts. |
| Legacy core file with matching installed receipt hash and no recorded owner choice | Offer/perform ordinary D10 conditional template upgrade, retaining exact prior bytes for Undo. |
| Legacy core file without a receipt, even if it matches historical stock text | Preserve; a template hash alone cannot prove the owner never wrote it. Offer a candidate. |
| Customized, empty, linked, unreadable or deleted core file | Preserve bytes or absence, without following links; use D10 notice/candidate/tombstone behavior. |
| Any old companion file or directory | Preserve in place, including stock-looking content and every soul version. Exclusion from the new manifest is never permission to delete. |

This stricter classification is specific to the clean personal profile: it does not change D10's existing AGENTS/hooks migration. Migration performs no legacy path deletion, renaming, reformatting or content merging. Owners can remove their own files later. Undo reverses only still-matching migration writes; concurrent edits, explicit deletions and owner choices survive retry, recovery and future seed versions. A case-colliding `memory.md` and `MEMORY.md` pair is reported for owner resolution without choosing, merging or overwriting either.

### Readers accept sparse homes

Use the authenticated platform center/home record, with D10 provisioning receipt where available, as the existence/first-contact authority. `soul.md` remains a stable personal path, not an existence sentinel. Legacy registered homes without receipts remain the same homes even if Soul is absent; absence never authorizes rebind or replacement provisioning. Update first_contact, home rebinding and work-queue consumers together. Update Soul/Identity readers to accept plain markdown and missing optional files while continuing to read existing historical formats without rewriting them. Keep purpose and existing `Loop branch:` associations readable by `branches.py` and `api/universe.py`; a blank/deleted Soul yields an empty soul, not a newly allocated loop, home or an error. Identity absence/blank name never supplies a fabricated founder identity.

Existing `soul.edit` policy remains effective and owner-controlled; no missing file disables its write boundary. For a fresh never-configured home, the versioned starter manifest supplies an inert `soul.edit` template candidate in D10's scoped candidate store, outside the visible personal baseline. Its explicit default permits edits by the authenticated owner and agents acting within that owner's current delegated rules; it requires no extra confirmation for already-permitted edits and records exact prior bytes/history. On the first governed edit, D10 materializes that editable template and needed history through its exclusive-create API. A recorded owner deletion/custom policy takes precedence and is never silently restored; owner settings/rules then resolve the permitted action, or the operation reports the unresolved policy rather than guessing. Reuse the current governed file/history operation, including D7's replacement after learning-handle retirement; do not recreate retired soul_edit/write_brain handles. Default policy is editable starter content, not immutable platform behavior. No seed or file text grants another user's authority.

Coordinate the command-center-cutover classification of `MEMORY.md` (audit L0), `settings.yaml` and starter content with their owning lanes before rollout; do not slip those product fixes into this docs change. Test sparse-home first contact, branch status and governed edits before changing provisioning.

## Migration Plan

1. Land D10's receipt API and coordinate sparse readers with starter renderer cutover; register the content profile in that single manifest.
2. Use it for fresh centers and resume legacy upgrades through D10, including dormant centers. Preserve all unknown and companion paths; report candidates through its notice.
3. Prove byte preservation, path races, crash recovery and conditional Undo on Windows and Linux 3.11. Roll back content through D10 conditional Undo, never by reinstalling the thirteen-file baseline or restoring the old renderer.

## Risks / Trade-offs

- Existing centers remain cluttered on disk: preserving user data is preferable to automatic cleanup; the editor can present the three primary files without hiding access to others.
- Legacy parser assumptions can break empty homes: first-contact, branch and governed-write fixtures are release prerequisites.
- Byte hashes cannot establish authorship without history: unreceipted files remain untouched, at the cost of fewer automatic upgrades.

## Open Questions

None for this proposal. Final friendly starter wording is implementation content, subject to the three-file/no-jargon acceptance above.
