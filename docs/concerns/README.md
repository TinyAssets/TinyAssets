# Concerns

One file per unresolved concern. **This directory replaced the `STATUS.md` Concerns section on
2026-08-25**, when the board was retired in the harness reset.

## Why files, not GitHub issues

Codex's review of the reset plan (`2026-08-25-harness-reset-codex-review.md (archived in git)`) argued
this and was right: GitHub issues are externally mutable, network-dependent, and **absent from a
clone**. A security finding that only exists in a web UI is not evidence a fresh checkout can act
on. A tracked file is. Link an issue from a concern file when one exists — but the file is canonical.

## Conventions

- **Filename:** `YYYY-MM-DD-short-slug.md`, dated by when the concern was **filed**.
- **Header:** `**Filed:**` / `**Verified:**` / `**Re-verified:**` / `**Severity:**` (P0/P1/P2, or
  omitted when severity is not the point).
- **Source (verbatim)** — the original text, unedited. Never paraphrase a security finding forward;
  paraphrase drifts, and the drift is what let `#1489` end up pointing at an unrelated merged PR.
- **Re-verification** — when a premise is re-checked, record the date and what changed. Line numbers
  and paths rot; the premise usually doesn't. Correct the citation, keep the finding.
- **Front-matter** (what the index is built from):

      ---
      severity: P1        # P0 / P1 / P2 / P3 / Watch / note, or null
      title: One-line name of the finding
      filed: '2026-09-27'
      summary: what is wrong, in one or two sentences
      ---

- Resolve a concern by **deleting the file**. Git holds the history.

## Open concerns

**The list is generated, not kept here.** Run:

    python scripts/concerns_index.py

It prints every concern as a table, most severe first, built from each file's
front-matter. Until 2026-09-27 this section was a hand-edited table. Every PR
that filed or resolved a concern edited it, so parallel PRs conflicted on it
(30 merge-main commits across 15 of 22 PRs in one night). Now filing a concern
is adding one file, and resolving it is deleting that file. Neither touches
this README. `tests/test_concerns_index_matches_the_directory.py` rejects a
file without valid front-matter, and rejects a table added back here.

## Not migrated, and why

Triage found three of twelve board rows did not survive contact with the code. Recorded here so
nobody re-derives them.

**Resolved — `EPOCH2_QUEUE_CONSUMER_READY` (P1, filed 2026-08-03).** The row read: *"3 tests still
assert the closed gate — now the ONLY blockers of main's `full-tests` tripwire."* Inverted.
`tinyassets/branch_tasks_v2.py:113` is `EPOCH2_QUEUE_CONSUMER_READY = True`, and the tests now
assert `is True` and pass (`tests/test_cloud_worker.py:601`,
`tests/test_cloud_automation_continuation.py:1814`; 5 passed, 2026-08-25). Nothing to migrate.

**Expired by design — two watches filed 2026-08-25.** Plug-and-play prod verification (waiting on a
founder X deposit; the first organic post is the proof) and prod disk at 78.6% (already guarded by a
`disk_watch` GitHub issue at 80% and hourly `disk_autoprune` at 85%). Both name their own automated
guard or closing event. A watch whose guard already exists does not need a second home.

## A caution the migration earned

The board's row for the LAN/CSRF finding cited **`#1489`**. PR #1489 is *"feat(command-center):
recover the Agent Village"*, merged — unrelated. Two other concerns cited paths that no longer exist
(`engine_helpers.py:192`, `router.py:89-92`); both premises held at their new locations. Verify a
citation against the code before acting on it, and re-stamp it when you do.

## Four concerns the migration dropped (added 2026-08-26)

The rows above marked 2026-07-02 / 2026-07-21 / 2026-08-05 / 2026-08-23 were **not** migrated on
2026-08-25. They were found the next day by diffing the retired board against this directory, and
every premise still held when re-checked against the code.

Three were security findings, one of them the **P0** LAN/CSRF exposure — for which this README
recorded only a caution about its bad citation, never the finding itself. The
`resolve_interlocutor_tier` item sat in the board's *Work* table rather than its Concerns list,
which is likely how it was passed over.

The lesson is the migration's own: a security finding whose only record is a caution about its
citation is not recorded. **Diff the source against the destination before deleting the source** —
the board was still readable in a stale checkout, which is the only reason these were recoverable.
