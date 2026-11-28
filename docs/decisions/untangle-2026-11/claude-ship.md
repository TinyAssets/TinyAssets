# claude-ship as built (2026-11-24)

Research and design only; nothing in the pipeline changes in this PR. Founder
direction, 2026-11-24: TinyAssets will not subscribe to Claude Ship. Ship is the
blueprint for our own PR pipeline on GitHub: "us doing anything they don't do,
or doing it differently, is highly questionable, because they are the experts
and we have had production issues." So each row below either adopts Ship's way
or keeps ours with a concrete reason. Ship work is process work, ahead of
product work: "Claude Ship is process improvement so it is prioritized and
implemented."

Rewritten 2026-11-28, after two reviews of #5856 and the founder's answers. On
approvals he answered: "I don't understand all this about approvals. How we have
been doing it is working for me so I don't know what all this is even all
about." That answer is the stated reason we keep our approval model, so the
Approvals rows below say "Keep". Everything else Ship does that he did not
reject is adopted (Direction 6: less process).

## Sources

Anthropic's Claude Ship docs, read 2026-11-24 and again by #5856's reviewer on
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
untangle skill's "How this project uses it"; and the host side, outside the
repo: `land_pr2.sh`, `brief_common.md` and `untangle/brief_lane.md`.

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
  is no verdict. The check is posted only by the review App (5634948), the
  ruleset's pinned source.
- The ruleset's only bypass actor is the admin role, mode `always`, and that
  user is admin. The ruleset has no `pull_request`, `non_fast_forward` or
  `deletion` rule.
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
| Runs after creation, after every push, or on request; a push keeps the earlier result only while the PR adds and removes the same lines and nobody has resolved an Important thread since | **In effect**: every new head is reviewed again. `review.yml` still carries a verdict across a prose-only delta, and the untangle skill still says "One review a PR" and "a re-confirm, which reads only its conflicts" | **Adopt**: the prose carry goes; `carries` rule 1 (the same change, so a clean merge of main) is Ship's first condition and stays; the reviewer applies the second |
| A later review closes its own threads it finds fixed; replies are read by the next review | Not done; the coordinator relays | **Adopt** |
| Read-only; never builds or runs the code | **In effect**: the reviewer never fixes. The untangle skill still says it "commits its own fixes of up to 50 lines and approves that head" and reruns builder mutations | **Adopt**: the reviewer reads; a test that cannot fail is an Important finding read from the code; mutations are the builder's done-check |
| Skips drafts | The builder pushes a draft early; CI runs on it; review waits for ready | Matches |

### PR Steward

| Ship | Ours today | Verdict |
|---|---|---|
| Fixes failing checks in code the PR touches, conflicts, review comments, Code Review's Important findings | **In effect**: the builder (or a fresh fixer) is the PR's steward and pushes the fixes | **Adopt**, as a session (decision 3) |
| Merges the base in on a conflict; never rebases | "Merge `origin/main` in; never rebase" (untangle skill) | Matches |
| Never approves or merges; the service refuses approving, changing rulesets, statuses, check runs, deleting branches, pushing tags | Agents act as the founder's admin user, so nothing refuses any of these | **Keep, a known gap** (decision 2): the steward skill forbids them; nothing enforces it |
| Re-runs a flaky job once (failed before any test ran, or passed earlier on the same commit) | "After a red CI job, rerun only its failures" (gates); `queue-handoff` stops after two counted queue failures | **Adopt** Ship's flaky rule into the steward skill; `queue-handoff` stays (Ship leaves the merge queue to GitHub) |
| Instructions in `.claude/skills/steward/SKILL.md` on the default branch, plus `CLAUDE.md`/`AGENTS.md` | Builder rules live partly in host briefs, lost in the 2026-11-28 restart | **Adopt**, first (§ Where the host rules land) |
| The skill's example frontmatter sets `user-invocable: false` and `disable-model-invocation: true`: a file only the service reads | n/a | **Differs**: our steward is every builder session, so the skill must load for them; it keeps name and description only |
| Watch ends at merge or close, or after days with no person commenting; a spend cap per PR | No watch; the coordinator decides; no cap | **Adopt** both as the steward skill's procedure: a watch ends at merge, close, a cap of sessions per PR (Fact 15), or days with no new head, review or comment (no comment can be told to be a person's) |
| Opt-out marker in the body, a label while it works | None | **Adopt** |
| Starts Manual first, then On | n/a | **Keep ours**: the coordinator already starts a steward per PR; there is no service to switch on |

### Claude Approvals

| Ship | Ours today | Verdict |
|---|---|---|
| A policy file `.claude/approvals.yml` decides who approves, with seats, overrides, classifiers, `findings`, `reuse`, observe then enforce | `review.yml`: a writer's verdict on the head, `APPROVE` with no Important open, is the required `review` check | **Keep** (decision 5). In Ship's terms ours is one rule, the starter's override alone: `{ paths: ["**"], if: "claude/code_review = pass", verdict: { claude: approves, seats: 0 } }`, with no person's seat. One rule with no seats needs no policy file; a `.claude/approvals.yml` nothing of ours reads would also switch on Ship's billed Approvals if the Claude App were ever linked to a qualifying org |
| Only accounts with write access count | `latest_verdict` counts only a review by an account that can push; a failed read is no verdict | Matches, and stays: no other GitHub user's review can pass `review` |
| The required check pinned to the app's id as its source | `review` pinned to the review App (5634948), whose key only a `main`-only environment can mint | Matches |
| Merge-queue commits are stamped | `review-queue.yml` signals with no permission; `review.yml` decides the group's PR afresh at its head | **Keep**: it does the stamp's job, and a `merge_group` run cannot reach the `main`-only key, which is why the two-step exists |
| Claude posts an approving review; GitHub requires one approval and dismisses stale ones | A `COMMENT` review; no `pull_request` rule | **Keep** (decision 5): the one writer account cannot approve its own PR (ADR-038), and `review` already holds a verdict to the commit it read |
| Protected files and a person on paths that matter | Every file gets the same review | **Keep** (decision 5): no founder seat; REVIEW.md marks a change that loosens a gate Important |
| A custom classifier read in an override | None | **Keep** without a classifier file: the reviewer answers one question, "does this change need the founder?", and the coordinator asks him in chat (decision 1). It gates nothing on GitHub |

### Branch rules and identity

| Ship | Ours today | Verdict |
|---|---|---|
| Require ≥1 approval; dismiss stale approvals on push; block force pushes | None of these rules | **Keep** (decision 5); `review` does the freshness work |
| The App off the bypass list; agents act as an App, apart from people | The admin role bypasses always, and every agent is that admin | **Keep, a known gap** (decision 2) |
| Warning: no person required + auto-merge or a merge queue merges with nobody acting | That is the founder's intent: `auto-enroll` arms every ready PR | **Keep** (decisions 1 and 5) |

### Our own gates, which Ship has no equivalent of

| Ours | Verdict |
|---|---|
| `Diff scope declared`: the body's Untangle check, owed items naming a fix, `Test-Removal:` lines (test hygiene), the release-critical count | **Keep as CI**: mechanical reads of the diff and body, and Ship says keep CI as its own check. What only judgment reads (the check is true; a removal's reason holds; a carried lineage) becomes REVIEW.md Important rules |
| Recorders (`ci/record.ts`, `Recorders-Differ:`), `required-tests`, `invariants` | **Keep as CI**: behaviour is proven by running code, which Code Review never does |
| `auto-enroll-merge` (arms every ready PR; refuses while the live ruleset differs from the file) | **Keep**: Ship leaves arming to people; the founder's process has none |
| `land_pr2.sh`: reruns the Untangle and hygiene checks, opens the PR, posts the receipt's verdict as a review, marks it ready; `Drain-Review-*` receipts | **Adopt** Ship's: no landing step. The checks are CI's; the reviewer posts its own review from a repo script; opening and readying are the steward's. It goes in `process/ship-code-review` |

### Other findings

- **Runs in sessions, not a service** (decision 3): with no session running, no
  review or fix happens, a known gap against Direction 8, the founder's choice.
- **The reviewer's verdict is any agent's word**, as today: separation of
  duties holds by the skills, since all agents write as the founder (decision
  2). Outsiders are held off by `latest_verdict`'s writer test.
- `is_prose` reads a root Markdown file as prose, so a root `REVIEW.md` would
  carry a verdict unreviewed today; `process/ship-rules` reads it as
  instructions (`_AGENT_INSTRUCTIONS_RE`, `check_shapes.py` `INSTRUCTION_FILES`).
- The reviewer is told to break the change, "any model family". **Keep** the
  engine as data (Direction 2).

## Target pipeline

1. **The builder** (the PR's steward) builds and pushes, following the steward
   skill and the done-check, and opens the PR.
2. **The reviewer**, a separate session on each new head, reads REVIEW.md and
   `CLAUDE.md` at `origin/main`, finds in parallel, verifies, ranks, and posts
   one `COMMENT` review on the commit it read with `scripts/review_post.py`:
   first line `BLOCK` while an Important finding stands, else `APPROVE`; each
   finding inline with `file:line`, the failing case and its severity; and one
   last line, `<!-- review: {"head":"<sha>","important":0,"nit":2,"pre_existing":1,"founder":"none","question":""} -->`.
   It never fixes.
3. **`review.yml`**, unchanged but for the prose carry: the latest verdict by an
   account that can push, at the head or the same change, decides `review`.
4. **The steward** fixes what the review and CI raise, merging the base in on a
   conflict, and the new head is reviewed again.
5. **The founder** is asked by the coordinator in chat when the reviewer's
   `founder` answer is not `none`. The coordinator reads that answer only from a
   writer's review on the current head (`scripts/review_post.py` reads it as
   `latest_verdict` does). Nothing on GitHub waits on him.
6. **Merge**: `auto-enroll` arms the PR; the queue merges it once `review`,
   `Diff scope declared`, `required-tests` and `invariants` pass.

The founder question, defined once in REVIEW.md: `design_frame` (it sets or
changes a design frame or shape: a new module, layer, boundary, contract, data
model, pipeline or ADR, or reverses a decided one), `direction_drift` (it moves
away from a line of AGENTS.md's Direction), `feedback` (a product or process
choice the founder has not made and the repo does not settle), or `none`; for
any answer but `none`, one question a non-engineer can answer.

## Where the host rules land

`process/ship-rules` moves every rule out of the two briefs; what stays on the
host is only session mechanics (where the body file goes, the reply's shape,
the session's own commit trailers).

| Host rule | Lands in |
|---|---|
| Own worktree; `npm ci` in `platform/` | the steward skill (setup); the untangle skill already says worktrees |
| Never rebase, amend or force-push; merge `origin/main`; no empty commits, hook bypass or stash | the steward skill |
| Never read `node_modules`; never print a credential or binding value | the steward skill; a printed secret is Important in REVIEW.md |
| No live action; the old stack (`tinyassets/**`, `deploy/**`, `pyproject.toml`, `scripts/mcp_public_canary.py`) untouched | the steward skill (Fact 4, Direction's "fixes only") |
| The repo is public: logs and PR text are public | REVIEW.md, Important |
| Ship is the blueprint: a pipeline mechanism that differs needs a reason | REVIEW.md, Important, citing this file |
| The body: `Recorders-Differ:`, `Test-Removal:`, the Untangle check; PATCH the open PR's body | already the untangle skill and the gates page; the brief's copy goes |
| The cutover manifest guard after merging main; draft early; `--untangle-check` before push | already the gates page and the untangle skill; the copies go |
| An enforcer's values from root-owned sources | already the untangle skill's reviewer list; the copy goes |
| `ci/guards` for `platform/*/src`; owner `guards/` for `platform/owner/src`; `test:app` for the app; a new guard run as CI runs it; `Recorders-Differ:` for migrations, doors and routes | the steward skill (the done-check's specifics) |
| The reviewer's first line `APPROVE`/`BLOCK`; its receipt | the reviewer section of the peer-agents skill; the receipt goes with `land_pr2.sh` |
| Ship's separation of duties (review never fixes; severities; every head reviewed) | the untangle skill's reviewer lines, rewritten: "commits its own fixes of up to 50 lines and approves that head", "a re-confirm, which reads only its conflicts", "reruns two or three builder mutations" and "One review a PR, a second only after a security block" go; REVIEW.md; the steward skill |
| `land_pr2.sh` | not moved: the reviewer posts with `scripts/review_post.py`, the steward opens and readies the PR; deleted in `process/ship-code-review` |

## Lanes

Two remain. Lanes for an approvals check, its policy and founder fact, the
gate-path rule, and the enforce ruleset change are dropped with decision 5;
the separate steward lane folds into `process/ship-rules`, since the steward's
procedure is skill text and the coordinator already starts one per PR.

- [ ] Rules into the repo, the steward procedure, the prose carry gone: `process/ship-rules`
- [ ] The reviewer's procedure and poster, the land step gone: `process/ship-code-review`

1. **`process/ship-rules`** (after this PR). Adds `REVIEW.md` (includes
   `@.claude/skills/untangle/SKILL.md`; what Important means here, the founder
   question, Nits capped, what CI proves not reported, evidence by `file:line`)
   and `.agents/skills/steward/SKILL.md` with its `.claude/skills/steward/`
   mirror (the PR conventions and Ship's steward procedure), with the rules of
   § Where the host rules land; rewrites the untangle skill's reviewer lines
   named there; drops `carries`' prose rule (`review_checks.py`, the gates
   page's carry sentence) and reads `REVIEW.md` as instructions
   (`_AGENT_INSTRUCTIONS_RE`, `check_shapes.py` `INSTRUCTION_FILES`). The skills
   aggregate pays for the steward skill from the untangle skill's removed lines,
   any rest recorded as a pin raise inside the aggregate total, as #5508, #5593
   and #5605 did. Gates: `validate_skills.py`, `test_skill_mirrors.py`,
   `check_context_budget.py` and `test_rulebook_ratchet.py`,
   `scripts/tests/test_review_check.py` (the prose case flips: a `Test-Removal:`
   line), `check_shapes.py`, `tests/test_live_docs_reference_real_scripts.py`,
   `--untangle-check`. The host briefs shrink to mechanics in the same hour.
2. **`process/ship-code-review`** (after 1). Adds `scripts/review_post.py`
   (format and post one `COMMENT` review on the commit read: the verdict line,
   inline findings, the last line; and read the last line back from the latest
   review by an account that can push, at the current head, after the last base
   change, reusing `review_checks.py`'s `latest_verdict` and `push_reader`) and
   its tests; rewrites the peer-agents skill's reviewer section to the
   find/verify/rank/post procedure (parallel finders, one verifier, REVIEW.md and
   `CLAUDE.md` at `origin/main`, one review per head, its own fixed threads
   resolved). The host's `land_pr2.sh` and receipts are deleted. Gates: pytest of
   the module (the line round-trips; a head mismatch, a missing line and a
   malformed one fail loudly; a non-writer's line is ignored, and a non-writer's
   newer line does not displace a writer's; a line before a base change is
   ignored), `validate_skills.py`, `--untangle-check`.

## Founder decisions

1. **Consulted by judgment, not paths** (2026-11-24): "I should be consulted
   when design frame or shape or project drift or feedback is needed." The
   reviewer's `founder` answer is the coordinator's trigger to ask him in chat.
2. **Agents keep the founder's account** (2026-11-24); no machine user or App.
   The admin bypass and the indistinguishable acts are known gaps.
3. **Reviews and fixes stay in Claude sessions on his subscription**
   (2026-11-24); no API key, no CI-hosted review or steward.
4. **Branch rules at enforce** (2026-11-24): superseded by 5; there is no
   enforce step.
5. **Keep our approval model** (2026-11-28): "How we have been doing it is
   working for me." Agents approve through `review`; no founder seat, no
   `/approve`, no gate-path rule, no branch rule requiring an approval.

## This PR

Only this file. `REVIEW.md`, the steward skill and the reviewer's poster are the
two lanes'.
