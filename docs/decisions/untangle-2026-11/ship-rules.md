# ship-rules as built (2026-11-28)

The first build lane of `claude-ship.md`: every rule the two host briefs held
moves into the repo, as that file's "Where the host rules land" table says, and
the review gate loses its prose carry.

## Research

Read 2026-11-28, under `code.claude.com/docs/en/`: `code-review-customize`
(REVIEW.md at the root, read from the default branch; an include is a line
holding only `@` and a path, a skill as `.claude/skills/<folder>/SKILL.md`, a
regular file of at most 30,000 bytes, eight includes and 150,000 bytes in all;
its example's sections "What Important means here", "Cap the nits", "Do not
report", evidence by `file:line`), `pr-steward-pull-requests` (the
repository's instructions at `.claude/skills/steward/SKILL.md`; the defaults:
fix failing checks in code the PR touches, rerun a flaky job once, merge the
base on a conflict, regenerate lockfiles; stop by label or `@claude stop`,
staying stopped until started again; the `claude-pr-steward-disable` marker),
and `pr-steward-setup`. Ours: the two briefs, the untangle and peer-agents
skills, `review_checks.py`, `check_shapes.py`, `check_context_budget.py`,
`validate_skills.py` and their tests.

## As built

1. `REVIEW.md` at the root: Ship's format, including the untangle skill by
   Ship's include line. It holds the untangle skill's reviewer list as "What
   Important means here", with the briefs' rules the table gives it (the repo
   is public; Ship is the blueprint), a change that loosens a gate, a test
   that cannot fail read from the code, and the gates page's carried-lineage
   check (`claude-ship.md`'s "becomes REVIEW.md Important rules"); Nits capped
   at five; what CI proves not reported; the founder question; the review's
   format, its three markers and the accounting of earlier Important findings;
   "Who counts". It and what it includes are read at `origin/main`. The gates
   page keeps a pointer to it, and to the steward skill for reruns: each rule
   has one home.
2. The steward skill, `.agents/skills/steward/` and its `.claude/skills/`
   mirror (the repo's mirror is a whole-folder copy): setup, the never list,
   the done-check's specifics, and the watch (Ship's defaults, the three-push
   ceiling, the label, `/steward stop`, the 7-day end, the opt-out marker).
   Its frontmatter keeps name and description only: every builder loads it,
   and `validate_skills.py` refuses Ship's invocation keys.
3. The untangle skill: the reviewer bullet points at `REVIEW.md`; "commits its
   own fixes of up to 50 lines", "a re-confirm, which reads only its
   conflicts", "reruns two or three builder mutations" and "One review a PR"
   go; "Merge `origin/main` in; never rebase" moves to the steward skill.
4. `carries` loses its prose rule (and `_modes`, `_PROSE_MODES`, which only it
   read); `review_check`'s ask and title say "a fresh review of this head";
   `review.yml`'s header, the gates page, ADR-038's decision 3 and the
   openspec requirement say the same change only.
5. The root `REVIEW.md` reads as instructions: `is_prose` (so a change to it
   needs an Untangle check; the root file only, as Ship reads and as
   `check_shapes.py` `INSTRUCTION_FILES` lists it, so a `review-*.md` note
   stays prose), and the docs-reference test's `LIVE_FILES`.
6. The `steward-watching` label is a repo setting the agent may make (Fact 4);
   the coordinator created it once, on 2026-11-28, and `process/ship-code-review`
   drops the skill's "until it exists" clause.

## Pins

The skills aggregate pays for the steward skill (4,147 B) with the untangle
skill's removed lines (1,209 B); the rest is a raise from 34,467 to 37,400
(2,933 B), inside the aggregate total, as #5508, #5593 and #5605 did.
`REVIEW.md` gets its own aggregate pin, 4,550 B: unpinned, it would be the
offload hole the aggregates exist to close. The gates page's 156 B that moved
into it lower the `docs/reference` pin from 5,615 to 5,459. Neither total
moves.

The aggregate pins now sum to 47,409 B of the 58,471 B total (81%, from 68.5%
on main). The untangle skill's 20% band is for a measured value that drifts
toward its ceiling under load (a watchdog, a budget, a quota). This total
fails `test_rulebook_ratchet.py` when the pins pass it, but it is a sum of
fixed pins that only a reviewed diff moves, each pin at 100% of its file by
design, and nearing it is the ratchet working, since each later raise must
then displace an old rule. So the band does not apply, and no pin is cut for
it (corrected by `process/ship-code-review`, from #5861's review).

## Departures

- `REVIEW.md` is pinned, and the docs-reference test reads it; the design
  named neither. Both follow from reading it as instructions.
- The opening and readying of the PR stay out of the steward skill: they are
  `process/ship-code-review`'s, with the land step they replace.

## Owed

- [x] The peer-agents skill's dispatch and reviewer sections point at `REVIEW.md` for the review's format and accounting, and carry the coordinator's denied-permission rule: `process/ship-code-review`
- [x] live: the coordinator creates the `steward-watching` label on the repository, once (done 2026-11-28, read back as 200)
