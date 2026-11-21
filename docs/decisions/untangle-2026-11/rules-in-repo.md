# rules-in-repo as built (2026-11-21)

`process/rules-in-repo` moves into the repo every rule in the coordinator's host briefs
(`brief_common.md`, `brief_lane.md`, `brief_rvw2.md`, `brief_codex_review.md`) and its
process lessons from 2026-11-15 on that a builder or reviewer on any engine must follow, so
Codex, a fresh session or any other engine gets them from the files every engine reads: the
untangle skill's "How this project uses it" (Fact 16), `docs/reference/executable-gates.md`
and `AGENTS.md`. The founder asked that the day's process fixes be permanent for all agents,
and (2026-11-21) that PR time be measured and each optimization made permanent when found.
The host briefs can now be cut to the host's own mechanics and pointers.

## 1. Where each host rule lives now

"Skill" is `.agents/skills/untangle/SKILL.md`, "How this project uses it" (mirrored
byte-identically to `.claude/skills/`); "gates" is `docs/reference/executable-gates.md`.

| Host rule (file) | Where it lives |
|---|---|
| Parallel by default: a PR sharing no files with open work starts now, from main, never stacked; one blocked on some files starts now without them, with a named part 2; a lane is no two PRs on one file (founder_prefs 2026-11-21) | Skill, new "Parallel by default" bullet. The old "One PR in flight per lane, never stacked" is replaced by it; peer-agents' "Lanes are not capped, but RECONCILED ... Merges stay serialized" is cut to "shares files with another open PR: say so and stop" |
| Push a draft early; CI is the one full run; builders run typecheck, the touched files' tests, the guards the change can break and their mutations, never whole-workspace suites (brief_lane) | Skill, CI bullet ("Push a draft once it compiles ... never a whole workspace's suite"); gates, the done-check (touched files' tests and the `ci/guards/` they can break, in place of `npm test -w <ws>` and the whole `guards/` run) |
| The recorders are CI's (brief_lane) | Not moved: owed to the CI recorder job (below) |
| Reviewer reruns two or three of the builder's mutations and one per new guard, gates only for its own fixes, recorders only if its fixes touch `platform/*/src` (brief_rvw2 Runs) | Already in the repo: skill, CI bullet ("A reviewer reruns two or three ...") |
| A prompt asking for more loses to the Runs line (brief_rvw2); a prompt that drops a gate is the same drift (founder_prefs 2026-11-15, 2026-11-16) | Skill, CI bullet: "a coordinator's prompt adds or drops no check this section names" (was "asks for no check") |
| Reviewer fixes of 50 lines or fewer itself (brief_rvw2) | Already in the repo: skill, "Every reviewer" |
| Reviewer works in its own detached worktree, never the builder's; each agent runs git only in worktrees it made (brief_rvw2; the coordinator, 2026-11-21: a builder's HEAD moved to a reviewer's commit with 55 staged changes undoing a merge) | Skill, the shared-host bullet: "An agent runs git only in worktrees it made; a reviewer checks the PR out in its own detached one." Fact 13 already forbids switching a dirty worktree to `main` |
| Remove only the worktrees and scratch files you created (brief_rvw2, brief_common) | Skill, the shared-host bullet: "delete only what you wrote outside the repo" (was "delete what you wrote") |
| After merging main, a PR touching a file `platform/cutover.json` names runs the manifest guard and updates entries naming text it changed (brief_lane, brief_rvw2; #5704) | Gates, the done-check's last sentence |
| An enforcer (hook, gate, sandbox check) reads every run-time value from a source the agent cannot write, never env vars or files the agent or its settings can set, and its start check runs where it enforces (brief_lane; #5707) | Skill, "Every reviewer"'s blocking list, which builders build against |
| A rerun `pull_request_target` run replays the old event body (founder_prefs 2026-11-21) | Already in the repo: gates, second paragraph (#5697), reworded shorter |
| Owed items | Left to `process/owed-in-decisions`, which puts them in the skill's "The PR body"; not restated here |
| A PR another engine built gets the same review: an Untangle check, `Test-Removal:` lines, a decision file if it decides something (brief_codex_review) | Skill, "Every reviewer": "whatever engine built the PR ... a missing one, `Test-Removal:` line or decision file is a finding"; the reviewer's own fixes (50 lines or fewer) supply them |
| CI on the PR's head is the builder's run; don't rerun suites CI passed (brief_codex_review) | Already in the repo: skill, CI bullet ("CI is the one full run", the reviewer's runs) |
| Merge `origin/main` in; never rebase or force-push (brief_codex_review) | Already in the repo: skill, CI bullet |
| Body: Untangle check, `Test-Removal:` lines, test-hygiene gate (brief_lane, brief_codex_review) | Already in the repo: skill, "The PR body"; gates, the done-check |
| Heavy runs take the host's run lock; scratch and `node_modules` deleted after (brief_common) | Already in the repo: skill, the shared-host bullet. The lock's script and paths stay host-side |
| No live actions or dispatches outside the agreed work (brief_common) | Already in the repo: `AGENTS.md` Fact 4 |
| Untangle work first; CI and gate fixes never deferred under the cutover rule (founder_prefs 2026-11-15) | Already in the repo: skill, "Process first" |
| PR time is measured; a step that costs every PR time, or rework that repeats, gets its fix landed in the repo as a process PR when found, never left in a host note (the founder, 2026-11-21) | Skill, "Process first": such a cost is a process fix, timed by `scripts/pr_times.py`; `AGENTS.md` Fact 17 already says a known fix lands on discovery, never as a note |
| Estimates report PRs merged, items found, net remaining (founder_prefs 2026-11-21) | Host-side: the coordinator's report to the founder, not a builder's or reviewer's rule |
| Worktree and scratch paths, `link-deps.sh`, `heavy.sh`, commit trailers, body and receipt paths, reply shapes, the coordinator posting the review, `land_pr2.sh`, "push, don't open the PR", `/proc/loadavg` before holding work | Host-side: one host's or one coordinator's mechanics |

## 2. `scripts/pr_times.py`

For named PRs or `--last N` merged, from GitHub's API (reads only, through
`review_checks.py`'s `_gh_read` and its 5xx retry): opened to the last ready-for-review,
ready to the first queue entry after it, the last queue entry to merged, opened to merged,
the number of queue entries, and the head's latest completed `Tests` run in minutes; then
the median and max of each. It starts from the coordinator's `pr_timeline.py`, with every
page of a list read (`--paginate`, parsed page by page) and the repository taken from the
checkout (`{owner}/{repo}`) unless `--repo` names one. `scripts/tests/test_pr_times.py`
runs it against a faked `gh` that answers in two pages. Mutation: the first
ready-for-review instead of the last fails it.

## 3. Paid for inside the pins

The skills' aggregate is 34,417 of 34,467 B (from 34,420), so `process/owed-in-decisions`'s
+45 B still fits. The new lines are paid for by: the builder bullet's `Test-Removal:` clause
(the gates page's done-check says it), the reviewer's verdict-posting clause (the gates
page says how to post it), "Process first" and the measured-size bullet reworded shorter,
peer-agents' lane paragraph (superseded by "Parallel by default"; "Merges stay serialized"
is the merge queue's job), and ui-test's crashed-pin history cut to its rule. The gates
page is 1,380 of 1,387 B: `review.yml` (the table names it), "any other delta is reviewed"
(implied), and the rerun line shortened. `docs/reference/*.md` is 5,608 of 5,615 B;
`AGENTS.md` is unchanged.

## 4. Retro-check (Fact 17)

- The enforcer rule: on main, the enforcers are workflows (`review.yml`,
  `pr-scope-guard.yml`) that read Actions secrets and run the base's copy of their
  scripts, neither of which an agent can write. The one hook in flight is
  `untangle/r5-hooks`, already sent back to its builder for this fix (#5707).
- The done-check change and the draft push: the open lanes' builders follow it from their
  next run; nothing built needs changing.
- Parallel by default and the worktree rule: coordinator scheduling; no repo file but
  peer-agents' lane paragraph (cut above) said otherwise.

## 5. Owed

- [ ] The skill's CI bullet still says the recorders run "whenever it touches
  `platform/*/src`"; once the CI recorder job lands, that sentence says they are CI's
  (`process/ci-recorders`).

No `tasks.md` line: this PR has none of its own, and adds none.
