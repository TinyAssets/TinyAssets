## Context

Split out of `complete-independent-full-platform-targets` on 2026-07-25 to
discharge that change's task 6.3. Provenance is unchanged: the integrated
full-platform architecture (§§13, 14, 26, 30) plus the legacy moderation
execution spec, classified as absent from canonical specs by
`2026-07-22-openspec-full-coverage-audit.md (archived in git)`.

Two facts shaped the split rather than a rewrite:

1. **Implementation already started outside the parent change.** Draft PRs #1662
   and #1667 carry `tinyassets/moderation/models.py`, `policy.py`, `service.py`,
   `__init__.py`, `tests/test_moderation_authority.py`, and
   `tests/test_moderation_service.py`. Neither is merged; neither claims the full
   service surface. `store.py`, the storage migration, the API routing, and
   `tests/test_moderation_concurrency.py` remain unowned.
2. **The parent change could not do the split from its own lane.** Its section-2
   write-set is fenced to those PRs, so a split executed as file edits inside the
   parent would have collided with them. Moving the *spec and tasks* (which the
   PRs do not touch — verified: neither PR modifies
   `openspec/changes/complete-independent-full-platform-targets/`) is
   collision-free, which is why the split is a spec/governance move and not a
   code move.

## Goals / Non-Goals

**Goals:**

- Give the moderation capability an independently completable change: delta spec,
  tasks, acceptance evidence, sync, archive — all in one place.
- Preserve the requirements verbatim across the split so no reviewer has to
  re-derive what was already reviewed (parent change task 1.4: Claude Sonnet
  approved the corrected ownership model 2026-07-22).
- Keep the in-flight fence explicit so the split does not authorize a second lane
  to write the same files.

**Non-Goals:**

- Change any moderation requirement, threshold, or authority model.
- Build moderation runtime from this change's lane. This change owns the spec and
  the task ledger; the code lands in the moderation PR lane.
- Sync into `openspec/specs/` before implementation plus acceptance evidence
  lands. The delta stays target-only until then.
- Re-specify the handoff/outcome dispute surface, which stays with
  `complete-independent-full-platform-targets` task 5.5 and merely depends on
  this change's service owner.

## Decisions

### The split moves ownership, not requirements

`specs/moderation-and-abuse-response/spec.md` is the file that was held in the
parent change, moved with `git mv`. Its requirement text, scenarios, and SHALL
language are unchanged. A diff of the split commit shows a rename, so the
already-completed cross-family requirement review carries over.

### Ownership is named, not implied

Task 6.3 of the parent change specifically rejects "a naming note" as
discharge. The ownership assignment is therefore concrete and lives in
`tasks.md` §0: which lane implements, what counts as acceptance, who syncs, who
archives, and what the fence forbids.

### The fence is part of the split

Because implementation is mid-flight on unmerged drafts, this change must not
become a second writer of `tinyassets/moderation/`. Section 1 records exactly
which files each draft PR holds and which remain unowned, so the next provider
can pick up an unowned file without racing a draft.

### Moderation stays behind the canonical handle routers

Unchanged from the parent change: moderation actions route through existing
`tinyassets/api/` handles and web/tray surfaces are alternate presentations of
the same authorization boundary. No standalone advertised MCP handle. The
`--assert-handles` canary remains the guard.

## Risks / Trade-offs

- **Split-then-drift**: the parent change's task 5.5 depends on this change's
  service module, so a rename here breaks a dependent task → the dependency edge
  is recorded in both changes' task ledgers, not just here.
- **Two changes, one capability history**: someone reading the parent change's
  git history sees moderation disappear → the parent change's 6.3 note names this
  change explicitly as the successor.
- **The drafts could be abandoned**: if #1662/#1667 close unmerged, the fence in
  section 1 becomes stale and the files become claimable → section 1 states that
  the fence is only valid while the named PRs are open, and how to verify.

## Migration Plan

1. Implement the fenced files in the moderation PR lane (#1662/#1667 or their
   successors); implement the unowned files (`store.py`, the storage migration,
   the API routing, `tests/test_moderation_concurrency.py`) in a lane that
   declares them in `STATUS.md`.
2. Run the §14 moderation proof (concurrent flag/decision/appeal traffic,
   queue-latency and write-contention bounds, anomaly-volume failure injection,
   no lost or duplicated terminal decision).
3. Where a user-visible surface changes, add the rendered-chatbot proof per
   AGENTS.md, plus the `--assert-handles` canary to prove the handle set did not
   drift.
4. Obtain independent cross-family code-to-requirement review.
5. Sync this delta into `openspec/specs/moderation-and-abuse-response/` and
   archive this change in the same landing lane.

Rollback before implementation is deletion of this change plus restoring the
delta and section-2 tasks to `complete-independent-full-platform-targets`.
