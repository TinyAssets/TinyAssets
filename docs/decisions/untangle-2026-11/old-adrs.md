# old-adrs as built (2026-11-21)

Founder call 18 (`founder-answers.md`): ADR-001 to ADR-031 are superseded by ADR-032, the
principles it says stand are kept, `INDEX.md` lists the platform's set, and the answers that
amend an ADR go into an ADR, so they outlive the untangle log the cutover deletes. Doc only,
plus the platform's comments that cited a superseded ADR.

1. **ADR-039 is the new ADR** (ADR-038 was taken by the review check). It holds call 18 and the
   answers that change an ADR decision: calls 1, 2, 3 and 9 (one agent definition, amending
   ADR-034 decisions 1.2, 2 and 11), 10 (code mode off, ADR-034 decision 12), 11 (limits,
   ADR-032 decision 5 and ADR-035 decision 5), 12 (runs past the ceiling wait, ADR-034
   decisions 4.1 and 8) and 13 (the control record's last good copy, ADR-032 decision 2).
   Calls 6 and 7 extend ADR-028 and ADR-029, so they sit in those rules' items. The other
   calls schedule work and do not change an ADR decision, so they stay in `tasks.md`.
2. **What stands is restated once, in ADR-039 decision 2,** each item naming where the
   platform holds it. ADR-032's and ADR-034's "What stands" kept old ADRs by number only, so a
   reader had to open a superseded ADR to learn a live rule; now the platform's set is
   self-contained. An item already held by a platform ADR, the Direction or a guard points
   there and is not restated (2.3, 2.4, 2.8, 2.16, 2.21, 2.22). An item held only by an old ADR
   is stated in full (2.2, 2.5 to 2.7, 2.9, 2.14, 2.17 to 2.20).
3. **Each of ADR-001 to ADR-031 gains one Status line,** "Superseded by ADR-032 (founder,
   2026-11-21)", and where something stands, "except:" naming it and its item of ADR-039
   decision 2. Nothing stands of ADR-001, ADR-002 (both already superseded by ADR-005) or
   ADR-014 (an agent is files now, ADR-034 decision 2). ADR-022 already read "Superseded by
   ADR-032 (2026-10-27)" and nothing of it stands, so it gains no second line. No body is edited.
4. **The platform ADRs' Status lines:** ADR-032's and ADR-034's "amended by the founder's
   answers" pointers (#5730) now name ADR-039's decisions, and each says its "What stands" is
   held by ADR-039 decision 2. ADR-035's names ADR-039 decision 5. ADR-036's says ADR-039
   decision 5 answers its decision 1's open retention of collection history. ADR-033's says
   ADR-031's decisions it left standing are ADR-039 decision 2.20.
5. **`INDEX.md`:** the platform's set (ADR-032 to ADR-039) first, then the superseded ADRs,
   one line each with the ADR-039 item that holds what stands.
6. **Live citations moved to ADR-039:** the platform's code and specs that cited ADR-006,
   ADR-018, ADR-025, ADR-026, ADR-028 and ADR-031 decision 2 as their authority:
   `sandbox/runner/login.ts`, `contract/src/sandbox.ts`, `doors/src/doors.ts`,
   `doors/test/mcp.test.ts`, `owner/src/{file-search,wakes,signins,ledger,export}.ts`,
   `owner/test/runs-authority.test.ts`, `sources/src/{check,schema}.ts`, and the doors and
   owner-object specs. `check.ts`'s refusal text changes only in its citation; no test reads
   it. The cutover manifest's `docs/decisions` keep no longer says the ADRs' call is pending.

**Left, with reasons:**
- Old-stack docs that cite old ADRs for the old stack (`docs/architecture.md`'s registry,
  `docs/concerns/`, `openspec/specs/`, the old OpenSpec changes, `docs/reference/` and
  `docs/getting-started/`): the cutover deletes or edits each (`platform/cutover.json`), and the
  registry's founder rows are `tasks.md` Step 20's.
- The plan's research (`engines-replan.md`, `main-agents.md`, `subscription-engines.md`) and the
  decision logs: history, which describes what was true then.
- `scripts/check_shapes.py`, `scripts/deployed_sha.py` and `docs/host-actions.md` cite ADR-005
  and ADR-004 as background; the cutover deletes the scripts and edits host-actions, and each
  ADR's Status line now says where its rule lives.
- `tasks.md` call 18 is not ticked: its item also holds `untangle/import-export-download` and
  `untangle/shells-cutover`.
