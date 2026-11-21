# owed-in-decisions as built (2026-11-21)

A process fix (`process/owed-in-decisions`). On 2026-11-21 every comments and tests pass
(#5677, #5679, #5698, #5699, #5701, #5704, #5705) added its own `[x]` line at the end of `tasks.md`'s passes
list and its new owed items at the end of the re-check loop's list. Each merge made the next
open PR conflict on those same lines: a manual merge of `origin/main` and a 10 to 15 minute CI
rerun, six times that day. The two lists were a hidden link between PRs that otherwise share
nothing: every PR had to change one shared place to record something only it knew.

## 1. The research

What the record has to keep (the untangle skill, "The PR body"; `scripts/review_checks.py`,
the Untangle check): every owed item names the PR, branch or `live:` wait that fixes it, and
the re-check loop (`untangle-2026-11-wave-2.md` §E step 6) finds every open one. The body's
check holds the first only for the PR's own life: the body is not in the repository, and once
merged nothing reads it again. `tasks.md` held the second, but only by convention: a sub-line
deleted without being done failed nothing.

The candidates:

- **(b) One pre-allocated `tasks.md` line per area, edited in place.** It moves the conflict
  from "the last line of the list" to "the area's line": two PRs in one area (owner part 1 and
  part 2, a pass and a fix that follows it) still collide, and owed items are not per area (a
  pass in `gateway/` owes a change to `sources/`). Someone must also allocate the lines before
  each PR, which is the same shared edit made earlier. Rejected.
- **(c) One file per owed item** (`docs/owed/<slug>.md`). No two PRs ever touch one file, but
  the item is split from the decision that explains it, and closing it deletes a file, which a
  guard cannot tell from dropping it without a second record. Rejected: (a) gives the same
  isolation with the reason beside the item.
- **(a) A PR's owed items live in its own decision file**, as checkbox lines, and the shared
  list is computed, not written. Every PR already writes a file of its own
  (`docs/decisions/<log>/<pr>.md`), so the record lands in one place, the place no other PR
  edits. Chosen.

## 2. The rule (both copies of the untangle skill, "The PR body")

- Anything a PR finds and leaves is fixed in place or owed in its decision file: a `- [ ]`
  line naming the PR, branch or `live:` wait that fixes it, wrapped lines indented under it.
- The PR that does an owed item ticks it (`- [x]`) where it stands, and may add after its
  text what did it (`Done (#5680).`, as `tasks.md`'s ticks do); an open item is never
  deleted or reworded. Ticking a line of another PR's decision file is the one edit to it
  besides a correction.
- A PR ticks its own `tasks.md` line and adds none. The passes list's remaining area
  (`platform/owner/`, part 2) already has its line.
- `python scripts/review_checks.py --owed`, from the repository's root, lists every open item
  under `docs/decisions/` as `path:line: text`. The re-check loop's line in `tasks.md` points
  at it.

The skill's byte cap (`scripts/check_context_budget.py`, the skills aggregate, 47 bytes free)
is held by rewording the bullet: 34465 of 34467 bytes.

## 3. The guard (`scripts/review_checks.py` `owed_problems`)

It runs in the Untangle check step of `pr-scope-guard.yml` (required), on every PR, prose-only
ones included, since decision files are prose. From the change's `git diff --raw` it reads
every changed `.md` under `docs/decisions/` at the merge base and at the head, as blobs (no PR
code runs), and refuses the change when:

- an item open at the merge base is at the head neither open nor ticked (a tick may add a
  note after the text), in any changed decision file (so an item moved between files in one
  change is kept); or
- an open item at the head names no PR, branch or `live:` wait (the body check's own pattern,
  `_NAMES_FIX_RE`).

An item is its text with its wrapped lines joined and spaces collapsed. That is why an open
item is never reworded: the guard cannot tell a reworded item from a dropped one without an id,
and an id per item is a second thing to keep. An item stands as written; what its fix finds
goes in the fix's own decision file.

The guard runs from the base commit's copy of the script (`pull_request_target`), so it holds
from the PR after this one merges.

## 4. Not migrated

The open sub-lines under the re-check loop in `tasks.md` stay where they are, and are ticked
there. Other PRs in flight tick them now; moving them would turn each of those ticks into a
conflict, the hot spot this fix removes. The loop's line names both: each sub-line below it and
each item `--owed` lists. No new owed sub-line is added there.

Nothing is owed by this change.
