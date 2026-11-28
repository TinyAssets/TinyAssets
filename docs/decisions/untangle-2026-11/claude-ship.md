# claude-ship as built (2026-11-24)

Research and design only; nothing in the pipeline changes in this PR. Founder
direction, 2026-11-24: TinyAssets will not subscribe to Claude Ship. Ship is the
blueprint for our own PR pipeline on GitHub: "us doing anything they don't do,
or doing it differently, is highly questionable, because they are the experts
and we have had production issues." So each row below either adopts Ship's way
or keeps ours with a concrete reason. Ship work is process work, ahead of
product work: "Claude Ship is process improvement so it is prioritized and
implemented."

Rewritten 2026-11-28, after three reviews of #5856 and the founder's answers. On
approvals he answered: "I don't understand all this about approvals. How we have
been doing it is working for me so I don't know what all this is even all
about." That answer is the stated reason we keep our approval model, so the
Approvals rows below say "Keep". It covers approvals only; everything else Ship
does that he did not reject is adopted (Direction 6: less process).

## Sources

Anthropic's Claude Ship docs, read 2026-11-24 and again by #5856's reviewers on
2026-11-28 (Ship released in beta 2026-11-23; Code Review GA, PR Steward and
Claude Approvals public beta), all under `code.claude.com/docs/en/`:
`claude-ship`, `code-review`, `code-review-customize`,
`code-review-pull-requests`, `pr-steward`, `pr-steward-setup`,
`pr-steward-pull-requests`, `pr-steward-security`, `pr-steward-troubleshooting`,
`approvals`, `approvals-policy`, `approvals-setup`, `approvals-classifiers`,
`approvals-github-rules`, `approvals-pull-requests`.

Ours, read at `origin/main` 42ad8e8b0: `.github/workflows/` `review.yml`,
`review-signal.yml`, `review-queue.yml`, `auto-enroll-merge.yml`,
`queue-handoff.yml`, `pr-scope-guard.yml`, `tests.yml`;
`scripts/review_checks.py`, `scripts/test_hygiene_gate.py`,
`scripts/ruleset_drift.py`, `scripts/validate_skills.py`;
`docs/ops/merge-queue-ruleset.json` and the live ruleset 24065294 (read only;
identical to the file); ADR-038; `docs/reference/executable-gates.md`; the
untangle and peer-agents skills; and the host side, outside the repo:
`land_pr2.sh`, `brief_common.md` and `untangle/brief_lane.md`.

Facts measured for this file:

- Every PR on `main` is opened, pushed and reviewed by one GitHub user, the
  founder's (#5846 to #5854 opened by it; #5854's one verdict review is by it).
  Agents write as that user by several paths: the Claude GitHub App, host `gh`,
  `land_pr2.sh`, the `MERGE_ATTRIBUTION_TOKEN` PAT, and the browser skills.
  Some agent comments carry no App marker (7057154858, 7194785637), so nothing
  GitHub records tells the founder's act from an agent's.
- `review.yml` counts a verdict only from a review by an account that can push
  (`latest_verdict` and `push_reader` in `scripts/review_checks.py`), on the
  commit it read, after the PR's last base change; a permission read that fails
  is no verdict. A verdict stands for a later head only across the same change
  or a prose-only delta (`carries`); any other delta gets, in its own words, "a
  one-round delta review of only that delta". The check is posted only by the
  review App (5634948), the ruleset's pinned source.
- `latest_verdict` takes the writer verdict with the newest `submitted_at`,
  whatever commit it read, and `carries` rule 1 lets a verdict on one commit
  stand for a later head that is the same change. So a late `APPROVE` of an
  earlier, same-change head voids a newer `BLOCK`: a live bug in today's
  `review` (Fact 17), fixed first in its own PR (§ Lanes, `fix/verdict-order`).
- The writer read `push_reader` makes (`collaborators/{login}/permission`)
  works in Actions with the workflow's token, but this session type's GitHub
  proxy refuses it and the collaborator list (HTTP 403); the GitHub MCP's
  `list_repository_collaborators` works from a session (read 2026-11-28,
  `Jonnyton admin`).
- The ruleset's only bypass actor is the admin role, mode `always`, and that
  user is admin. The ruleset has no `pull_request`, `non_fast_forward` or
  `deletion` rule. `main` also has classic branch protection, read 2026-11-28:
  `allow_force_pushes` and `allow_deletions` false, `enforce_admins` false, no
  status checks and nothing else. With `enforce_admins` false a classic rule does not bind an
  admin, so nothing refuses a force-push to `main` or its deletion by any
  agent, and `docs/host-actions.md`'s "it blocks force pushes and deletion"
  overstates it. No workflow or script force-pushes or deletes `main`.
- No `Claude Code Review` check run is on #5854's head, so no Ship agent is on
  this repository.
- On 2026-11-28 a container restart wiped `/home/user/ta-work`: both host
  briefs and `land_pr2.sh` were lost and rebuilt from a coordinator's memory.
  Every rule held there was one restart from gone; Ship keeps every
  instruction on the default branch.

## The gap table

"Adopt" changes ours to Ship's way in the lanes below. "Keep" names why Ship's
way does not apply here. "In effect" marks what the host side already does
since 2026-11-24 (`brief_lane.md`'s "Ship's separation of duties"), which
`process/ship-rules` moves into the repo.

### Code Review

| Ship | Ours today | Verdict |
|---|---|---|
| Several agents find in parallel, a verification step drops false positives, findings are deduplicated and ranked | One reviewer session, whatever engine the coordinator dispatched (peer-agents skill) | **Adopt**: find, verify, rank, post, inside the reviewer session (decision 3) |
| Severities 🔴 Important (fix before merge), 🟡 Nit, 🟣 Pre-existing; `pass` = no open Important | **In effect**: Important/Nit/Pre-existing with `file:line` and the failing case; the first line `BLOCK` while an Important stands, else `APPROVE` | **Adopt**: the untangle skill's reviewer list becomes REVIEW.md's "What Important means here" |
| Inline comments on the lines, a summary, a neutral check run `Claude Code Review` | One `COMMENT` review whose body is the evidence; `review.yml` turns its first line into the required `review` check | **Adopt** inline comments in that one review. **Keep** `review` as the check: it is our Approvals too (below) |
| Reads `REVIEW.md` (root, includes `@file`, `@.claude/skills/<folder>/SKILL.md`, ≤8 includes, ≤150,000 bytes) and `CLAUDE.md`, from the default branch, never the PR | The reviewer reads the PR's checkout of the untangle skill, AGENTS.md and a host brief | **Adopt**: `REVIEW.md` in Ship's format, read at `origin/main` |
| Runs after creation, after every push, or on request; keeps the earlier result only while the PR adds and removes the same lines and nobody has resolved an Important thread since | **In effect**: every new head is reviewed again. `review.yml` still carries a verdict across a prose-only delta, and the untangle skill still says "One review a PR" and "a re-confirm, which reads only its conflicts" | **Adopt**: the prose carry goes; `carries` rule 1 (the same change, so a clean merge of main) is Ship's first condition and stays, narrowed to a commit in the head's history: a rebased or force-pushed head no longer carries and waits for a fresh review (we never rebase). Ship's second condition exists because a resolve can close a finding; ours makes thread state decide nothing (next row), so it has nothing to guard |
| A later review focuses on what changed since the last, but its counts include earlier reviews' findings whose threads are still open | `review_check` asks for "a one-round delta review of only that delta", so a delta review can approve a head whose earlier Important finding is unfixed | **Adopt**, stricter: a later review may focus on the delta, but it lists every earlier Important finding as fixed at this head, withdrawn by this review with its reason, or standing, and its first line is `BLOCK` while one stands. Earlier findings are read from the PR's reviews and their inline comments (outdated ones included), by the fixed `Important:` marker the poster writes, and only from the accounts § Target pipeline's "who counts" names, after the last base change. A resolved thread decides nothing: all agents write as one account (decision 2), so a resolve cannot be told apart |
| A later review closes its own threads it finds fixed; replies are read by the next review | Not done; the coordinator relays | **Adopt** |
| Read-only; never builds or runs the code | **In effect**: the reviewer never fixes. The untangle skill still says it "commits its own fixes of up to 50 lines and approves that head" and reruns builder mutations | **Adopt**: the reviewer reads; a test that cannot fail is an Important finding read from the code; mutations are the builder's done-check |
| Skips drafts | The builder pushes a draft early; CI runs on it; review waits for ready | Matches |

### PR Steward

| Ship | Ours today | Verdict |
|---|---|---|
| Fixes failing checks in code the PR touches, conflicts, review comments, Code Review's Important findings | **In effect**: the builder (or a fresh fixer) is the PR's steward and pushes the fixes | **Adopt**, as a session (decision 3) |
| Merges the base in on a conflict; never rebases | "Merge `origin/main` in; never rebase" (untangle skill) | Matches |
| Never approves or merges; the service refuses approving, changing rulesets, statuses, check runs, deleting branches, pushing tags | Agents act as the founder's admin user, so nothing refuses any of these | **Keep, a known gap** (decision 2): the steward skill forbids them. Force-pushing or deleting `main` becomes refused by GitHub (§ Branch rules), a mistaken push, not a deliberate one: an admin can still disable or delete the ruleset or add a bypass, which drift sees only at the next arm |
| Re-runs a flaky job once (failed before any test ran, or passed earlier on the same commit) | "After a red CI job, rerun only its failures" (gates); `queue-handoff` stops after two counted queue failures | **Adopt** Ship's flaky rule into the steward skill; `queue-handoff` stays (Ship leaves the merge queue to GitHub) |
| Instructions in `.claude/skills/steward/SKILL.md` on the default branch, plus `CLAUDE.md`/`AGENTS.md` | Builder rules live partly in host briefs, lost in the 2026-11-28 restart | **Adopt**, first (§ Where the host rules land) |
| The skill's example frontmatter sets `user-invocable: false` and `disable-model-invocation: true`: a file only the service reads | n/a | **Differs**: our steward is every builder session, so the skill must load for them; it keeps name and description only |
| Watch ends at merge or close, or after days with no person commenting ("about a week" before a session restarts) | No watch; the coordinator decides | **Adopt**: a watch ends at merge, at close, or after 7 days with no new head, review or comment (no comment can be told to be a person's, decision 2) |
| Stop by removing the watching label or commenting `@claude stop`; it stays stopped across later pushes and comments until started again | None | **Adopt**: removing `steward-watching`, or a top-level `/steward stop`, ends the watch, and it stays ended until the label is added back or `/steward` is commented. `/steward`, not `@claude`, because `@claude` is Anthropic's app's trigger. As Ship sends a session only the comments of the author, writers and bots, a command, and any comment the steward acts on, counts only from the accounts § Target pipeline's "who counts" names |
| A spend cap per repository's PR session, and a monthly limit | No cap | **Adopt** the per-PR cap as a ceiling with its reason (Fact 15): a steward that pushes three times and leaves the same check failing the same way stops and hands the PR to the coordinator, as `queue-handoff` stops a queue loop after two. The monthly limit maps to the subscription's own usage limits, the vendor's limit (Fact 15): Ship needs its own because usage credits bill without one; ours stops when the subscription does. Another cap is the founder's setting, none set |
| Opt-out marker in the body, a label while it works | None | **Adopt**: `<!-- steward-disable -->` in the body, and the `steward-watching` label |
| Starts Manual first, then On | n/a | **Keep ours**: the coordinator already starts a steward per PR; there is no service to switch on |

### Claude Approvals

| Ship | Ours today | Verdict |
|---|---|---|
| A policy file `.claude/approvals.yml` decides who approves, with seats, overrides, classifiers, `findings`, `reuse`, observe then enforce | `review.yml`: a writer's verdict, `APPROVE` with no Important open, is the required `review` check | **Keep** (decision 5). In Ship's terms ours is one rule, the starter's override alone: `{ paths: ["**"], if: "claude/code_review = pass", verdict: { claude: approves, seats: 0 } }`, with no person's seat. One rule with no seats needs no policy file; a `.claude/approvals.yml` nothing of ours reads would also switch on Ship's billed Approvals if the Claude App were ever linked to a qualifying org |
| Only accounts with write access count | `latest_verdict` counts only a review by an account that can push; a failed read is no verdict | Matches, and stays: no other GitHub user's review can pass `review` |
| The required check pinned to the app's id as its source | `review` pinned to the review App (5634948), whose key only a `main`-only environment can mint | Matches |
| Merge-queue commits are stamped | `review-queue.yml` signals with no permission; `review.yml` decides the group's PR afresh at its head | **Keep**: it does the stamp's job, and a `merge_group` run cannot reach the `main`-only key, which is why the two-step exists |
| Claude posts an approving review; GitHub requires one approval and dismisses stale ones | A `COMMENT` review; no `pull_request` rule | **Keep** (decision 5): the one writer account cannot approve its own PR (ADR-038), and `review` already holds a verdict to the commit it read |
| Protected files and a person on paths that matter | Every file gets the same review | **Keep** (decision 5): no founder seat; REVIEW.md marks a change that loosens a gate Important |
| A custom classifier read in an override | None | **Keep** without a classifier file: the reviewer answers one question, "does this change need the founder?", in its review (decision 1) |

### Branch rules and identity

| Ship | Ours today | Verdict |
|---|---|---|
| Require ≥1 approval; dismiss stale approvals on push | None | **Keep** (decision 5); `review` does the freshness work |
| Block force pushes where you don't want them (pr-steward-setup step 4) | Classic protection on `main` blocks both, but not for an admin (`enforce_admins` false), and every agent is an admin; no ruleset has either rule | **Adopt**: a second ruleset on the default branch with `non_fast_forward` and `deletion` and no bypass actor, so the admin bypass does not void it. It replaces the classic protection, which holds nothing else and is deleted, so `main`'s protection has one source and drift reads it. Decision 5 covers approvals only; a repo setting is the agent's call (Fact 4) |
| The App off the bypass list; agents act as an App, apart from people | The admin role bypasses the merge-queue ruleset always, and every agent is that admin | **Keep, a known gap** (decision 2) |
| Warning: no person required + auto-merge or a merge queue merges with nobody acting | That is the founder's intent: `auto-enroll` arms every ready PR | **Keep** (decisions 1 and 5); a PR whose review asks the founder is held by its `BLOCK` until he answers (§ Target pipeline) |

### Our own gates, which Ship has no equivalent of

| Ours | Verdict |
|---|---|
| `Diff scope declared`: the body's Untangle check, owed items naming a fix, `Test-Removal:` lines (test hygiene), the release-critical count | **Keep as CI**: mechanical reads of the diff and body, and Ship says keep CI as its own check. What only judgment reads (the check is true; a removal's reason holds; a carried lineage) becomes REVIEW.md Important rules |
| Recorders (`ci/record.ts`, `Recorders-Differ:`), `required-tests`, `invariants` | **Keep as CI**: behaviour is proven by running code, which Code Review never does |
| `auto-enroll-merge` (arms every ready PR; refuses while the live rules differ from the ruleset files) | **Keep**: Ship leaves arming to people; the founder's process has none |
| `land_pr2.sh`: reruns the Untangle and hygiene checks, opens the PR, posts the receipt's verdict as a review, marks it ready; `Drain-Review-*` receipts | **Adopt** Ship's: no landing step. The checks are CI's; the reviewer posts its own review from a repo script; opening and readying are the steward's. It goes in `process/ship-code-review` |

### Other findings

- **Runs in sessions, not a service** (decision 3): with no session running, no
  review or fix happens, a known gap against Direction 8, the founder's choice.
- **The reviewer's verdict is any agent's word**, as today: separation of
  duties holds by the skills, since all agents write as the founder (decision
  2). Outsiders are held off by `latest_verdict`'s writer test.
- **One decision, one place.** What the founder is asked is part of the
  review's verdict, not a second machine-read line: there is no second reader
  to disagree with `review.yml` about which review governs a head.
- `is_prose` reads a root Markdown file as prose, so a root `REVIEW.md` would
  carry a verdict unreviewed today; `process/ship-rules` reads it as
  instructions (`_AGENT_INSTRUCTIONS_RE`, `check_shapes.py` `INSTRUCTION_FILES`).
- The reviewer is told to break the change, "any model family". **Keep** the
  engine as data (Direction 2). The peer-agents skill's
  `VERDICT: APPROVE|ADAPT|REJECT` contract is a second verdict vocabulary;
  `process/ship-code-review` replaces it with the review's `APPROVE`/`BLOCK`.

## Target pipeline

1. **The builder** (the PR's steward) builds and pushes, following the steward
   skill and the done-check, and opens the PR.
2. **The reviewer**, a separate session on each new head, reads REVIEW.md and
   `CLAUDE.md` at `origin/main`, finds in parallel, verifies, ranks, and posts
   one `COMMENT` review on the commit it read with `scripts/review_post.py`.
   Each finding is inline where it has a line, else in the body, with
   `file:line`, the failing case, and one leading severity marker,
   `Important:`, `Nit:` or `Pre-existing:`, defined once in that script. It
   reads the earlier reviews' findings (who counts, after the last base change,
   by that marker) and lists each earlier Important as fixed, withdrawn with
   its reason, or standing. First line `BLOCK` while an Important finding
   stands or the founder's question below is unanswered on the PR; else
   `APPROVE`. A founder question, when there is one, is on its own line
   `Founder question: <one sentence>`. It never fixes. One review per head,
   and one more after a founder answer or after a `BLOCK` posted late on an
   earlier head.
3. **`review.yml`**, unchanged but for the prose carry, its ask text and which
   verdict governs. Counted verdicts are those `latest_verdict` counts today
   (by an account that can push, not voided by a dismissal, after the last
   base change) on a commit in the head's history (`git merge-base
   --is-ancestor` on the objects `review.yml` already fetches); a verdict on a
   commit outside it, force-pushed away, is ignored, so the check waits. A
   `BLOCK` stands until a counted verdict is submitted after it on the same
   commit or a descendant. `review` passes only when no `BLOCK` stands and the
   latest counted verdict on the most descendant reviewed commit is `APPROVE`
   and carries to the head; a merge group follows its PR's decision. Never the
   latest by time alone.

**Who counts.** Everything the reviewer and steward read on a PR (earlier
findings, the founder's answer as the coordinator posts it, `/steward`
commands, comments the steward acts on) counts only from an account with
`admin`, `maintain` or `write` on this repository, or from this repository's
own bots: `github-actions[bot]` (the queue-handoff ledger) and the review App's
bot. Ship counts the author, writers and bots the same way; an outsider is in
neither set, and an author's login cannot be forged in the API's record. In
Actions, `review.yml` keeps `push_reader`. In a session, whose proxy refuses
that read, the writers come from the GitHub MCP's `list_repository_collaborators`
(`role_name` `admin`, `maintain` or `write`), passed to `review_post.py` as a
file; a missing, empty or failed list is refused, never read as "everyone".
4. **The steward** fixes what the review and CI raise, merging the base in on a
   conflict, and the new head is reviewed again.
5. **The founder** is asked before the PR lands. A `Founder question:` line
   means the PR's `review` fails, so it waits. The coordinator asks him in
   chat, posts his answer on the PR as a comment quoting it with the date, and
   asks for a fresh review, which reads the answer only from a comment by an
   account that counts; feedback goes to the steward first.
6. **Merge**: `auto-enroll` arms the PR; the queue merges it once `review`,
   `Diff scope declared`, `required-tests` and `invariants` pass.

The founder question, defined once in REVIEW.md: ask it when the change sets
or changes a design frame or shape (a new module, layer, boundary, contract,
data model, pipeline or ADR, or reverses a decided one), moves away from a line
of AGENTS.md's Direction, or makes a product or process choice the founder has
not made and the repo does not settle; one question a non-engineer can answer.

## Where the host rules land

`process/ship-rules` moves every rule out of the two briefs; what stays on the
host is only session mechanics (where the body file goes, the reply's shape,
the session's own commit trailers).

| Host rule | Lands in |
|---|---|
| Own worktree; `npm ci` in `platform/` | the steward skill (setup); the untangle skill already says worktrees |
| Never rebase, amend or force-push; merge `origin/main`; no empty commits, hook bypass or stash | the steward skill |
| Never read `node_modules`; never search for or print a credential or binding value (names and hashes only) | the steward skill; a printed secret is Important in REVIEW.md |
| Keep your logs and scratch files in your own directory (your worktree or a folder named for your lane), never a shared scratch root | the steward skill |
| If a permission is denied, don't work around it | the steward skill, and the peer-agents skill's dispatch section for the coordinator and reviewer |
| No live action, spelled out: no workflow dispatch, deploy, ruleset or settings change (a lane that names one is the exception, Fact 4), and no touching live E2B sandboxes or stage data; the old stack (`tinyassets/**`, `deploy/**`, `pyproject.toml`, `scripts/mcp_public_canary.py`) untouched | the steward skill (Direction's "fixes only") |
| The repo is public: logs and PR text are public | REVIEW.md, Important |
| Ship is the blueprint: a pipeline mechanism that differs needs a reason | REVIEW.md, Important, citing this file |
| The body: `Recorders-Differ:`, `Test-Removal:`, the Untangle check; PATCH the open PR's body | already the untangle skill and the gates page; the brief's copy goes |
| The cutover manifest guard after merging main; draft early; `--untangle-check` before push | already the gates page and the untangle skill; the copies go |
| An enforcer's values from root-owned sources | already the untangle skill's reviewer list; the copy goes |
| `ci/guards` for `platform/*/src`; owner `guards/` for `platform/owner/src`; `test:app` for the app; a new guard run as CI runs it, reading files relative to the module, never cwd; `Recorders-Differ:` for migrations, doors and routes | the steward skill (the done-check's specifics) |
| The reviewer's first line `APPROVE`/`BLOCK`; its receipt | the reviewer section of the peer-agents skill; the receipt goes with `land_pr2.sh` |
| Ship's separation of duties (review never fixes; severities; every head reviewed; an Important finding stands until fixed in code) | the untangle skill's reviewer lines, rewritten: "commits its own fixes of up to 50 lines and approves that head", "a re-confirm, which reads only its conflicts", "reruns two or three builder mutations" and "One review a PR, a second only after a security block" go; REVIEW.md; the steward skill |
| The coordinator's founder step (pipeline step 5) | the peer-agents skill's dispatch section, in `process/ship-code-review` |
| `land_pr2.sh` | not moved: the reviewer posts with `scripts/review_post.py`, the steward opens and readies the PR; deleted in `process/ship-code-review` |

## Lanes

Four remain, the first a fix. Lanes for an approvals check, its policy and
founder fact, the gate-path rule, and an approval ruleset change are dropped
with decision 5; the separate steward lane folds into `process/ship-rules`,
since the steward's procedure is skill text and the coordinator already starts
one per PR.

- [ ] The live verdict-order bug (Fact 17), its own small PR first: `fix/verdict-order`
- [ ] Rules into the repo, the steward procedure, the prose carry and the delta-only ask gone: `process/ship-rules`
- [ ] The reviewer's procedure and poster, the coordinator's founder step, the land step gone: `process/ship-code-review`
- [ ] No force-push or deletion of `main`, one source for it: `process/ship-main-guard`
- [ ] live: the main-guard ruleset created from its file and read back (`current_user_can_bypass` `never` to the arming token), `main`'s classic protection deleted and read back as 404, open PRs armed again (after `process/ship-main-guard` merges)

0. **`fix/verdict-order`** (now, alone). `review_checks.py`'s decision becomes
   § Target pipeline step 3's: counted verdicts on commits in the head's
   history, a `BLOCK` standing until a counted verdict after it on the same
   commit or a descendant, then the most descendant commit's latest verdict,
   carried by `carries`; `carries`' docstring loses "a rebase … carries". Its
   tests, in `scripts/tests/test_review_check.py`, head H2 unless stated,
   t1 < t2: (1) `BLOCK@H2` t1, `APPROVE@H1` t2, the same change: fail;
   (2) `APPROVE@H2` t1, `BLOCK@H1` t2, a code delta: fail; (3) as 2, the same
   change: fail; (4) `BLOCK@H1` t1, `APPROVE@H2` t2: pass; (5) `APPROVE@H1`
   only, H2 the same change: pass; (6) on H2, `APPROVE` t1 then `BLOCK` t2:
   fail, and `BLOCK` t1 then `APPROVE` t2: pass; (7) a dismissed review at t2
   voids the verdicts at or before it; (8) H1 force-pushed away to H1': its
   verdict is ignored and the check waits; (9) cases 1 to 6 with
   `--group-sha`, the group following its PR. Gates: those tests, the existing
   `test_review_check.py` and `test_review_writers.py`, actionlint and
   `ci_structural_guards.py` if `review.yml` changes, `--untangle-check`.
1. **`process/ship-rules`** (after this PR and the fix). Adds `REVIEW.md` (includes
   `@.claude/skills/untangle/SKILL.md`; what Important means here; an
   Important finding stands until fixed in code, whatever its thread; the
   founder question; Nits capped; what CI proves not reported; evidence by
   `file:line`) and `.agents/skills/steward/SKILL.md` with its
   `.claude/skills/steward/` mirror (the PR conventions and the steward's
   procedure: fix in touched code, merge the base, re-run a flaky job once,
   reply on each thread, the watch's end and stop, the three-push ceiling, the
   opt-out marker and label, and act only on comments and commands from the
   accounts "who counts" names, read as it says), with the rules of § Where
   the host rules land;
   rewrites the untangle skill's reviewer lines named there; drops `carries`'
   prose rule and rewrites `review_check`'s ask text and its "needs a delta
   review" title to "a fresh review of this head", with `review.yml`'s header,
   and the gates page's carry sentence; reads `REVIEW.md` as instructions
   (`_AGENT_INSTRUCTIONS_RE`, `check_shapes.py` `INSTRUCTION_FILES`). The skills
   aggregate pays for the steward skill from the untangle skill's removed lines,
   any rest recorded as a pin raise inside the aggregate total, as #5508, #5593
   and #5605 did. Gates: `validate_skills.py`, `test_skill_mirrors.py`,
   `check_context_budget.py` and `test_rulebook_ratchet.py`,
   `scripts/tests/test_review_check.py` (the prose case flips: a `Test-Removal:`
   line), `check_shapes.py`, actionlint and `ci_structural_guards.py` for
   `review.yml`, `tests/test_live_docs_reference_real_scripts.py`,
   `--untangle-check`. The host briefs shrink to mechanics in the same hour.
2. **`process/ship-code-review`** (after 1). First, before building: from a
   reviewer session, read the collaborator list with the GitHub MCP's
   `list_repository_collaborators` (read only) and record what it returns.
   Adds `scripts/review_post.py`
   (format and post one `COMMENT` review on the commit read: the verdict line,
   each finding with its one severity marker, inline where it has a line, the
   founder question; and list the PR's earlier findings by marker, from the
   reviews and inline comments of the accounts "who counts" names, the writers
   given as a file from that list, after `last_base_change`, outdated ones
   included; it refuses a missing or empty writers file, and a body whose first
   line is not `APPROVE` or `BLOCK`, and a `Founder question:` under `APPROVE`)
   and its tests; rewrites the peer-agents skill's reviewer section to the
   find/verify/rank/post procedure (parallel finders, one verifier, REVIEW.md and
   `CLAUDE.md` at `origin/main`, one review per head and one more after a
   founder answer or a late `BLOCK` on an earlier head, every earlier Important listed as fixed, withdrawn or
   standing, the founder's answer read only from a writer's comment, its own
   fixed threads resolved), replaces its
   `VERDICT: APPROVE|ADAPT|REJECT` contract with the review's, and writes the
   coordinator's founder step and the denied-permission rule into its dispatch
   section. The host's `land_pr2.sh` and receipts are deleted. Gates: pytest of
   the module (a posted review's first line, inline comments on the commit read,
   the two refusals; an outsider's review or comment is not listed, so it can
   neither withdraw a finding nor answer for the founder; a finding before a
   base change is not listed; a body-only finding is; the repo's own bots are
   listed; a missing or empty writers file is refused), the recorded real
   collaborator read, `validate_skills.py`, `test_skill_mirrors.py`,
   `check_context_budget.py`, `--untangle-check`.
3. **`process/ship-main-guard`** (beside 1 and 2). First, before building:
   the drift check runs in `auto-enroll-merge.yml` with
   `MERGE_ATTRIBUTION_TOKEN`, a fine-grained PAT with Contents and Pull
   requests only, and GitHub returns `bypass_actors` only to a caller with
   write access to the ruleset. So the check reads
   `current_user_can_bypass`, which `GET rulesets/{id}` returns to any reader
   for the calling user, the user every agent acts as. Verify, read only, that
   the endpoint returns it (from a session, `GET rulesets/24065294` reads
   `always` for that user) and record what it returns. If it is not returned to
   a fine-grained PAT, the lane stops and records the read that can see it;
   it adds no token with Administration write. Then it adds
   `docs/ops/main-guard-ruleset.json` (named `main guard`; the default branch;
   `non_fast_forward` and `deletion`; no bypass actor). `scripts/ruleset_drift.py`
   compares each ruleset file with the live rules of `main` carrying that
   ruleset's id (`ruleset_id`, found by the file's `name`), and reads
   `GET rulesets/{id}`: the guard's `enforcement` must be `active` and its
   `current_user_can_bypass` `never`; an absent field is drift (exit 1), never
   a pass. So a guard created with the admin bypass, disabled, or holding a rule
   that moved to the other file is drift. `docs/host-actions.md` gains the
   create command and its read-back beside the 24065294 `PUT` row, and its
   classic-protection row is rewritten: once the guard reads back, `main`'s
   classic protection, which holds only the two settings the guard replaces, is
   deleted (`DELETE branches/main/protection`, read back as 404). Between the
   merge and the create, `auto-enroll` arms nothing (drift reports "file
   only"), as after a `PUT`: arm open PRs again after the read-back. Both acts
   are the coordinator's (Fact 4; the `live:` line above). Gates:
   `scripts/tests/test_ruleset_drift.py` (each file against its own ruleset;
   `always` or an absent `current_user_can_bypass`, or `disabled`, on the guard
   is drift; a rule under the wrong ruleset id is drift; the undeclared-rule
   case's example changes from `deletion`, which the guard declares, to
   another type), `ruleset_drift.py` exit 0 after the create,
   `--untangle-check`.

## Founder decisions

1. **Consulted by judgment, not paths** (2026-11-24): "I should be consulted
   when design frame or shape or project drift or feedback is needed." The
   reviewer asks the question in its review, which holds the PR; the
   coordinator asks him in chat before it lands.
2. **Agents keep the founder's account** (2026-11-24); no machine user or App.
   The admin bypass of the merge-queue ruleset and the indistinguishable acts
   are known gaps.
3. **Reviews and fixes stay in Claude sessions on his subscription**
   (2026-11-24); no API key, no CI-hosted review or steward.
4. **Branch rules at enforce** (2026-11-24): superseded by 5; there is no
   enforce step. The force-push and deletion rules were never an approval rule
   and are adopted on their own.
5. **Keep our approval model** (2026-11-28): "How we have been doing it is
   working for me." Agents approve through `review`; no founder seat, no
   `/approve`, no gate-path rule, no branch rule requiring an approval.

## Known

- The live PR body differs from its file at the end: the session's GitHub proxy
  rewrites the attribution footer on every PATCH. CI's Untangle check reads
  above it.

## This PR

Only this file. `REVIEW.md`, the steward skill, the reviewer's poster and the
second ruleset are the three lanes'.
