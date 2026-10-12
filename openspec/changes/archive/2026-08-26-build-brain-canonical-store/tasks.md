> **Lane disposition (created 2026-07-25).** This change owns the *build* that
> `brain-okf-canonical-store` (archived 2026-07-25) explicitly excluded by its own
> §5 heading. Every task below is genuinely unbuilt or partially landed in the
> opposite direction; the partial-landing notes were verified against the tree, not
> taken on faith, so a future builder reuses shipped code instead of rebuilding it.
> Nothing here is checked off until the behavior exists.

## 1. Inherited state (no work — provenance)

- [x] 1.1 Host store decision recorded in `PLAN.md` (Brain Module "Canonical store"; Design Decisions; Open Tensions resolved-by-scoping) — host-approved 2026-07-25
- [x] 1.2 Codex `ADAPT` review and its six adaptations carried in the relocated delta (`2026-06-24-brain-okf-canonical-codex-review.md (archived in git)`)
- [x] 1.3 Delta relocated from the archived amendment; this change is the sole live owner of the `brain-canonical-store` target
  > **Not byte-identical to the archived copy.** The relocation was verbatim except for one 2026-07-25 scoping correction (Codex `ADAPT` finding 1): the source-of-truth and OKF-conformance requirements now bind the commons and the default organization rather than every brain. The archived copy predates that correction and still reads as an all-brains OKF mandate — cite this change, not the archive.

## 2. Build

- [ ] 2.1 OKF **read-path** compatibility shim — consume the existing wiki **in place** for slice-1 `assemble(lens)`
  > **Reuse, do not rebuild.** All four projection mechanics ship already, but as a one-way *export* into a fresh bundle: `_convert_wikilinks`, `_write_index` (root `index.md` carries `okf_version` and nothing else, `okf_export.py:199`), `_write_log`, and `_EXCLUDED_ROOTS = {"drafts", "raw", "daemon-wiki"}` (`okf_export.py:15`). The exporter refuses a target inside the source root (`okf_export.py:38`), so it is **not** an in-place reader. The unbuilt part is the D5 read path; the projection rules are settled and should be shared, not duplicated.
  > **Blocked design question:** `_EXCLUDED_ROOTS` answers the `drafts/` question for the *export* direction only. Exclusion-on-export and non-membership-in-canon are different claims; the canonical write path must declare its own rule before 2.2 lands.
- [ ] 2.2 Write commit protocol — idempotency key, pending→durable entry states, atomic temp+rename projection, file locking, transaction/outbox ordering, crash recovery, rebuild reconciliation
  > **Wholly unbuilt — no partial credit.** No bundle write path, outbox, or entry-state machine exists in `tinyassets/`. The shipped exporter is read-only by construction and MUST NOT mutate the source wiki. `log.md` is generated human history and MUST NOT become the transactional journal (Codex adaptation 3.2.2).
- [ ] 2.3 Substrate conformance validation + `okf_version` pin + `[composable]` upstream-watch steward
  > **Split verdict.** *Landed:* the pin (`OKF_VERSION = "0.1"`, `okf_export.py:12`) and a structural validator (`_conformance_report`, `okf_export.py:232`) — but deliberately narrow, validating only the *generated* bundle, with an as-built `conformant` flag that disclaims canonical-store conformance. *Unbuilt:* substrate-wide validation over a canonical bundle, and the steward — a repo-wide search finds no OKF upstream-watch steward in any module or workflow.
- [ ] 2.4 `tinyassets/brain/` package with bundle reader/writer and the one-command index rebuild
  > Prerequisite for 2.1–2.3 to have a home. The package does not exist; the only `assemble_*` functions in the tree are unrelated retrieval helpers.

## 3. Fold-back (do NOT run early)

- [ ] 3.1 `sync-specs`: merge the `brain-canonical-store` delta into `openspec/specs/brain-canonical-store/spec.md` — **only after 2.1–2.4 ship**
  > `openspec/specs/` is as-built truth (AGENTS.md §Spec-driven development). Syncing before the build asserts requirements the system does not satisfy — the exact reason the predecessor could not discharge its own 4.1 and archived with `--skip-specs`. If only part of the build lands, sync only the requirements that part satisfies and leave the rest in the delta.
- [ ] 3.2 Archive this change after 3.1

## 4. Open design questions carried forward

- [ ] 4.1 Bundle topology — one physical bundle per universe with the commons as a union *view*, or a physical commons bundle?
- [ ] 4.2 Render-time coexistence of Tiny's typed relations (`supersedes`, `evidence_refs`, goal-graph edges) with OKF's untyped body cross-links
- [ ] 4.3 Exact outbox mechanism (SQLite outbox table vs WAL hooks) — decide inside 2.2, not before
- [ ] 4.4 `okf_version` pin cadence + the steward's escalation thresholds
- [ ] 4.5 How a **user-designed** brain organization (PLAN Design Decisions: OKF is the default, not a mandate) shares this substrate — what the bundle reader/writer must abstract so a non-OKF organization is expressible without a second engine
