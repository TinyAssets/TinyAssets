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
- The ruleset's only bypass actor is the admin role, mode `always`, and that
  user is admin. The ruleset has no `pull_request`, `non_fast_forward` or
  `deletion` rule, so nothing refuses a force-push to `main` or its deletion by
  any agent. No workflow or script force-pushes or deletes `main`.
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
| Runs after creation, after every push, or on request; keeps the earlier result only while the PR adds and removes the same lines and nobody has resolved an Important thread since | **In effect**: every new head is reviewed again. `review.yml` still carries a verdict across a prose-only delta, and the untangle skill still says "One review a PR" and "a re-confirm, which reads only its conflicts" | **Adopt**: the prose carry goes; `carries` rule 1 (the same change, so a clean merge of main) is Ship's first condition and stays. Ship's second condition exists because a resolve can close a finding; ours makes thread state decide nothing (next row), so it has nothing to guard |
| A later review focuses on what changed since the last, but its counts include earlier reviews' findings whose threads are still open | `review_check` asks for "a one-round delta review of only that delta", so a delta review can approve a head whose earlier Important finding is unfixed | **Adopt**, stricter: a later review may focus on the delta, but its first line is `BLOCK` while any earlier review's Important finding on the PR is not judged fixed in the code at this head, or withdrawn by a review. A resolved thread decides nothing: all agents write as one account (decision 2), so a resolve cannot be told apart |
| A later review closes its own threads it finds fixed; replies are read by the next review | Not done; the coordinator relays | **Adopt** |
| Read-only; never builds or runs the code | **In effect**: the reviewer never fixes. The untangle skill still says it "commits its own fixes of up to 50 lines and approves that head" and reruns builder mutations | **Adopt**: the reviewer reads; a test that cannot fail is an Important finding read from the code; mutations are the builder's done-check |
| Skips drafts | The builder pushes a draft early; CI runs on it; review waits for ready | Matches |

### PR Steward

| Ship | Ours today | Verdict |
|---|---|---|
| Fixes failing checks in code the PR touches, conflicts, review comments, Code Review's Important findings | **In effect**: the builder (or a fresh fixer) is the PR's steward and pushes the fixes | **Adopt**, as a session (decision 3) |
| Merges the base in on a conflict; never rebases | "Merge `origin/main` in; never rebase" (untangle skill) | Matches |
| Never approves or merges; the service refuses approving, changing rulesets, statuses, check runs, deleting branches, pushing tags | Agents act as the founder's admin user, so nothing refuses any of these | **Keep, a known gap** (decision 2): the steward skill forbids them; force-pushing or deleting `main` becomes refused by GitHub (§ Branch rules) |
| Re-runs a flaky job once (failed before any test ran, or passed earlier on the same commit) | "After a red CI job, rerun only its failures" (gates); `queue-handoff` stops after two counted queue failures | **Adopt** Ship's flaky rule into the steward skill; `queue-handoff` stays (Ship leaves the merge queue to GitHub) |
| Instructions in `.claude/skills/steward/SKILL.md` on the default branch, plus `CLAUDE.md`/`AGENTS.md` | Builder rules live partly in host briefs, lost in the 2026-11-28 restart | **Adopt**, first (§ Where the host rules land) |
| The skill's example frontmatter sets `user-invocable: false` and `disable-model-invocation: true`: a file only the service reads | n/a | **Differs**: our steward is every builder session, so the skill must load for them; it keeps name and description only |
| Watch ends at merge or close, or after days with no person commenting ("about a week" before a session restarts) | No watch; the coordinator decides | **Adopt**: a watch ends at merge, at close, or after 7 days with no new head, review or comment (no comment can be told to be a person's, decision 2) |
| Stop by removing the watching label or commenting `@claude stop`; it stays stopped across later pushes and comments until started again | None | **Adopt**: removing `steward-watching`, or a top-level `/steward stop`, ends the watch, and it stays ended until the label is added back or `/steward` is commented. `/steward`, not `@claude`, because `@claude` is Anthropic's app's trigger |
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
| Block force pushes where you don't want them (pr-steward-setup step 4) | No `non_fast_forward` or `deletion` rule on `main`; every agent is an admin on an `always` bypass | **Adopt**: a second ruleset on the default branch with `non_fast_forward` and `deletion` and no bypass actor, so the admin bypass does not void it. Decision 5 covers approvals only; a repo setting is the agent's call (Fact 4) |
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
   one `COMMENT` review on the commit it read with `scripts/review_post.py`:
   first line `BLOCK` while an Important finding of this or any earlier review
   is not fixed in the code at this head, or while the founder's question below
   is unanswered on the PR; else `APPROVE`. Each finding inline with
   `file:line`, the failing case and its severity; a founder question, when
   there is one, on its own line `Founder question: <one sentence>`. It never
   fixes.
3. **`review.yml`**, unchanged but for the prose carry and its ask text: the
   latest verdict by an account that can push, at the head or the same change,
   decides `review`.
4. **The steward** fixes what the review and CI raise, merging the base in on a
   conflict, and the new head is reviewed again.
5. **The founder** is asked before the PR lands. A `Founder question:` line
   means the PR's `review` fails, so it waits. The coordinator asks him in
   chat, posts his answer on the PR as a comment quoting it with the date, and
   asks for a fresh review, which reads the answer; feedback goes to the
   steward first.
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

Three remain. Lanes for an approvals check, its policy and founder fact, the
gate-path rule, and an approval ruleset change are dropped with decision 5; the
separate steward lane folds into `process/ship-rules`, since the steward's
procedure is skill text and the coordinator already starts one per PR.

- [ ] Rules into the repo, the steward procedure, the prose carry and the delta-only ask gone: `process/ship-rules`
- [ ] The reviewer's procedure and poster, the coordinator's founder step, the land step gone: `process/ship-code-review`
- [ ] No force-push or deletion of `main`: `process/ship-main-guard`

1. **`process/ship-rules`** (after this PR). Adds `REVIEW.md` (includes
   `@.claude/skills/untangle/SKILL.md`; what Important means here; an
   Important finding stands until fixed in code, whatever its thread; the
   founder question; Nits capped; what CI proves not reported; evidence by
   `file:line`) and `.agents/skills/steward/SKILL.md` with its
   `.claude/skills/steward/` mirror (the PR conventions and the steward's
   procedure: fix in touched code, merge the base, re-run a flaky job once,
   reply on each thread, the watch's end and stop, the three-push ceiling, the
   opt-out marker and label), with the rules of § Where the host rules land;
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
2. **`process/ship-code-review`** (after 1). Adds `scripts/review_post.py`
   (format and post one `COMMENT` review on the commit read: the verdict line,
   inline findings, the founder question; it refuses a body whose first line is
   not `APPROVE` or `BLOCK`, and a `Founder question:` under `APPROVE`) and its
   tests; rewrites the peer-agents skill's reviewer section to the
   find/verify/rank/post procedure (parallel finders, one verifier, REVIEW.md and
   `CLAUDE.md` at `origin/main`, one review per head, earlier Important findings
   carried until fixed in code, its own fixed threads resolved), replaces its
   `VERDICT: APPROVE|ADAPT|REJECT` contract with the review's, and writes the
   coordinator's founder step and the denied-permission rule into its dispatch
   section. The host's `land_pr2.sh` and receipts are deleted. Gates: pytest of
   the module (a posted review's first line, inline comments on the commit read,
   the two refusals), `validate_skills.py`, `test_skill_mirrors.py`,
   `check_context_budget.py`, `--untangle-check`.
3. **`process/ship-main-guard`** (beside 1 and 2). Adds
   `docs/ops/main-guard-ruleset.json` (the default branch; `non_fast_forward` and
   `deletion`; no bypass actor), and `scripts/ruleset_drift.py` compares the
   rules GitHub applies to `main` with both ruleset files together, so
   `auto-enroll` keeps arming once the second ruleset exists. After it merges,
   the coordinator creates the ruleset from the file (Fact 4). Gates:
   `scripts/tests/test_ruleset_drift.py` (two files' rules against one live
   list), `ruleset_drift.py` exit 0 after the create, `--untangle-check`.

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
