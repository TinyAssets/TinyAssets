# claude-ship as built (2026-11-24)

Research and design only; nothing in the pipeline changes in this PR. Founder
direction, 2026-11-24: TinyAssets will not subscribe to Claude Ship. Ship is the
blueprint for our own PR pipeline on GitHub: "us doing anything they don't do,
or doing it differently, is highly questionable, because they are the experts
and we have had production issues." So each row below either adopts Ship's way
or keeps ours with a concrete reason. The founder answered the design's
decisions the same day (§ Founder decisions) and made the pipeline process
work, ahead of product work: "Claude Ship is process improvement so it is
prioritized and implemented." Updated 2026-11-28 for those answers.

## Sources

Anthropic's Claude Ship docs, read 2026-11-24 (Ship released in beta
2026-11-23; Code Review GA, PR Steward and Claude Approvals public beta), all
under `code.claude.com/docs/en/`: `claude-ship`, `code-review`,
`code-review-customize`, `code-review-pull-requests`, `pr-steward`,
`pr-steward-setup`, `pr-steward-pull-requests`, `pr-steward-security`,
`pr-steward-troubleshooting`, `approvals`, `approvals-policy`,
`approvals-setup`, `approvals-classifiers`, `approvals-github-rules`,
`approvals-pull-requests`. GitHub's own docs were not reachable from the
session (`docs.github.com` is outside its network); what they would settle is
an owed live check below.

Ours, read the same day at `origin/main` 42ad8e8b0: `.github/workflows/`
`review.yml`, `review-signal.yml`, `review-queue.yml`, `auto-enroll-merge.yml`,
`queue-handoff.yml`, `pr-scope-guard.yml`, `tests.yml`; `scripts/review_checks.py`,
`scripts/test_hygiene_gate.py`, `scripts/ruleset_drift.py`;
`docs/ops/merge-queue-ruleset.json` and the live ruleset 24065294 (read only;
identical to the file); ADR-038; `docs/reference/executable-gates.md`; the
untangle skill's "How this project uses it"; and the host side, outside the
repo: `land_pr2.sh`, `brief_common.md` and `untangle/brief_lane.md`.

Facts measured for this file:

- Every PR on `main` is opened, pushed and reviewed by one GitHub user, the
  founder's (#5846 to #5854 opened by it; #5854's one verdict review is by it).
  ADR-038 records why the verdict is a `COMMENT` review: "the one writer
  account cannot approve its own PR".
- The agents reach GitHub through the Claude GitHub App on that user's behalf:
  each of the 14 agent receipt comments among the newest 60 issue comments
  carries `performed_via_github_app: claude`.
- The ruleset's only bypass actor is the admin role, mode `always`, and that
  user is admin, so every agent session can bypass every rule on `main`.
- The ruleset has no `pull_request` rule: no approval is required, and no push
  dismisses one.
- No `Claude Code Review` check run is on #5854's head, so no Ship agent is on
  this repository.
- Of the 537 PRs merged in the 30 days to 2026-11-24, 81 (15%) touch a file
  that defines the gate (the list under § Target pipeline); 144 (27%) would with
  all of `.github/**`. 39 of the 59 workflows read a secret or the `production`
  environment.
- On 2026-11-28 a container restart wiped `/home/user/ta-work`: both host
  briefs and `land_pr2.sh` were lost and rebuilt from a coordinator's memory.
  Every rule held there was one restart from gone; Ship keeps every
  instruction on the default branch.

## The gap table

"Adopt" changes ours to Ship's way in the build order below. "Keep" names why
Ship's way does not apply here. "In effect" marks what the host side already
does since 2026-11-24 (`brief_lane.md`'s "Ship's separation of duties"), which
`process/ship-rules` moves into the repo.

### Code Review

| Ship | Ours today | Verdict |
|---|---|---|
| Several agents find in parallel, a verification step drops false positives, findings are deduplicated and ranked | One reviewer session, whatever engine the coordinator dispatched (peer-agents skill) | **Adopt**: find, verify, rank, post, inside the reviewer session (founder decision 3) |
| Severities 🔴 Important (fix before merge), 🟡 Nit, 🟣 Pre-existing; `pass` = no open Important | **In effect**: Important/Nit/Pre-existing with `file:line` and the failing case; the first line `BLOCK` while an Important stands, else `APPROVE` (the line `review.yml` reads until enforce) | **Adopt**: the untangle skill's reviewer list becomes REVIEW.md's "What Important means here" |
| Inline comments on the lines, a summary, a check run `Claude Code Review` that concludes neutral, never failure | One `COMMENT` review whose body is the evidence; `review.yml` turns it into the required `review` check | **Adopt**: findings as inline comments in the reviewer's review, its result in a machine line of the body; the gate moves to the approvals check. Our names (`code-review`, `approvals`) differ from Ship's: a check of Ship's name from another app is the spoof Ship's docs warn of |
| Reads `REVIEW.md` (root, includes `@file`, `@.claude/skills/<folder>/SKILL.md`, ≤8 includes, ≤150,000 bytes) and `CLAUDE.md`, from the default branch, never the PR | The reviewer reads the PR's checkout of the untangle skill, AGENTS.md and a host brief | **Adopt**: `REVIEW.md` in Ship's format, read at `origin/main` |
| Runs after creation, after every push, or on request; a push that adds and removes the same lines keeps the earlier result | **In effect**: every new head is reviewed again. `review.yml` still carries a verdict across a prose-only delta | **Adopt**: the prose carry goes in `process/ship-rules`; `carries` rule 1 (the same change) is Ship's keep-the-result rule and stays |
| A later review closes its own threads it finds fixed; replies are read by the next review | Not done; the coordinator relays | **Adopt** |
| Read-only; never builds or runs the code | **In effect**: the reviewer never fixes. It still reruns two or three builder mutations (untangle skill) | **Adopt**: the reviewer reads; a test that cannot fail is an Important finding read from the code; mutations are the builder's done-check |
| Skips drafts | The builder pushes a draft early; CI runs on it; review waits for ready | Matches |

### PR Steward

| Ship | Ours today | Verdict |
|---|---|---|
| Fixes failing checks in code the PR touches, conflicts, review comments, Code Review's Important findings | **In effect**: the builder (or a fresh fixer) is the PR's steward and pushes the fixes | **Adopt**, as a session (founder decision 3) |
| Merges the base in on a conflict; never rebases | "Merge `origin/main` in; never rebase" (untangle skill) | Matches |
| Never approves or merges; the service refuses approving, changing branch protection, rulesets, statuses, check runs, deleting branches, pushing tags | Agents act as the founder's admin user, so nothing refuses any of these | **Keep, a known gap** (founder decision 2): the steward skill forbids them; nothing enforces it. GitHub itself refuses the one that matters, an author approving its own PR |
| Re-runs a flaky job once (failed before any test ran, or passed earlier on the same commit) | "After a red CI job, rerun only its failures" (gates); `queue-handoff` stops after two counted queue failures | **Adopt** Ship's flaky rule into the steward skill; `queue-handoff` stays until the steward takes queue failures, then is re-examined |
| Instructions in `.claude/skills/steward/SKILL.md` on the default branch, plus `CLAUDE.md`/`AGENTS.md` | Builder rules live partly in host briefs, lost in the 2026-11-28 restart | **Adopt**, first (`process/ship-rules`, § Where the host rules land) |
| Watch ends at merge or close, or after days with no person commenting; spend cap per PR and per month | No watch; the coordinator decides; no cap | **Adopt** both, the cap as sessions a PR may take (Fact 15) |
| Opt-out marker in the body, a label while it works, stop by comment or label | None | **Adopt** |
| Starts Manual first, then On | n/a | **Adopt** |

### Claude Approvals

| Ship | Ours today | Verdict |
|---|---|---|
| A policy file `.claude/approvals.yml` (paths, verdict `claude` + `seats`, `override`, `if`, `reuse`, `findings`, `nested`, `locals`); strictest verdict wins; unknown keys refused | No policy: every PR needs the same one verdict | **Adopt**: our approvals check reads Ship's grammar, the subset our policy uses, and refuses the rest loudly (Fact 8) |
| A custom classifier, `.claude/classifiers/<name>/def.yml` (question, outputs), read in an override's `if`; the rule's own verdict when it gives no answer | None | **Adopt** for the founder's consultation (decision 1) |
| People lists: `humans`, `bots`, `"!pr/authors"`, logins; the author can fill one seat; `@claude approve` lets the author's own comment count | "The latest verdict by an account that can push" decides; that is every agent, as the founder | **Differs** (decision 2): no seat can tell the founder from an agent, so his consent is a fact the check reads (`pr/founder_approved`, below) |
| Built-in protected files; `.github/`, `CLAUDE.md`, `AGENTS.md`, `.mcp.json`, `.claude/` need your own rules; on paths that matter, a rule that reads no classifier | None; a rule file is reviewed like code | **Adopt**: the gate files get a rule that reads no classifier |
| Observe mode first (check neutral), then enforce | n/a | **Adopt** |
| The required check pinned to the app's id as its source | `review` pinned to the review App (5634948), which only a `main`-only environment can mint | Matches; the approvals check is posted by the same App |
| Claude's approving review only where every applying rule says `approves`; dismissed on a diff-changing push | A `COMMENT` review whose first line approves, posted by the founder's user | **Adopt**: a real approving review by the review App |
| `reuse` keeps nothing by default | `carries`: same change, or a delta of Markdown prose | **Adopt**: the prose carry goes |
| Merge-queue commits are stamped, not re-evaluated | `review-queue.yml` signals, and `review.yml` decides the group's PR afresh | **Adopt** the stamp; `review-queue.yml` goes |
| A condition cannot read CI; keep CI as its own required check | `Diff scope declared`, `required-tests`, `invariants` required beside `review` | Matches |

### Branch rules, identity, and the no-human warning

| Ship | Ours today | Verdict |
|---|---|---|
| Require a PR with ≥1 approval; dismiss stale approvals on push | No `pull_request` rule | **Adopt** at enforce (decision 4) |
| The App off the bypass list | The admin role bypasses always, and every agent is that admin | **Keep, a known gap** (decision 2): agents are the founder's account, so the admin bypass is theirs too. Enforce cannot remove it |
| Agents act as an App, so a person and an agent are told apart | Every agent acts as the founder's user | **Keep** (decision 2); the founder's consent is told apart by how it is made (below) |
| GitHub forbids the opener approving, so PRs `claude[bot]` opens need a second App to approve | Hence ADR-038's `COMMENT` reviews | **Adopt**: the review App approves; the founder's user, the author of every PR, never can |
| Warning: no person required + auto-merge or a merge queue merges with nobody acting | That is the founder's intent: `auto-enroll` arms every ready PR | **Keep** (decision 1): the founder is asked by judgment, and on the gate files always |

### Our own gates, which Ship has no equivalent of

| Ours | Verdict |
|---|---|
| `Diff scope declared`: the body's Untangle check, owed items naming a fix, `Test-Removal:` lines (test hygiene), the release-critical count | **Keep as CI**: mechanical reads of the diff and body, and Ship says keep CI as its own check. What only judgment reads (the check is true; a removal's reason holds; a carried lineage) becomes REVIEW.md Important rules |
| Recorders (`ci/record.ts`, `Recorders-Differ:`) and `required-tests` | **Keep as CI**: behaviour is proven by running code, which Code Review never does |
| `invariants` (rulebook ratchet, skill mirrors) | **Keep as CI** |
| `auto-enroll-merge` (arms every ready PR; refuses while the live ruleset differs from the file, `ruleset_drift.py`) | **Keep**: Ship leaves arming to people; the founder's process has none, so a workflow arms. The drift refusal stays because the ruleset is kept as a file |
| `land_pr2.sh`: reruns the Untangle and hygiene checks, opens the PR, posts the receipt's verdict as a review, marks it ready; `Drain-Review-*` receipts | **Adopt** Ship's: no landing step. The checks are CI's; the reviewer posts its own review; opening and readying are the steward's. It goes in `process/ship-code-review`, before enforce |

### Other findings

- **Runs in sessions, not a service** (decision 3). Ship's agents run on
  Anthropic's infrastructure on every PR event; ours are Claude sessions on the
  founder's subscription, started by the coordinator. With no session running,
  no review or fix happens: a known gap against Direction 8, the founder's
  choice. Only the approvals evaluation runs in Actions. A later option within
  that choice: an Actions job on each new head fires a Claude routine
  (ADR-031 decision 2 allows storing the routine's `/fire` token).
- **The reviewer's result is any agent's word.** The reviewer posts as the
  founder's user, like every agent, so the approvals check trusts a result
  line exactly as far as `review.yml` trusts a verdict today: separation of
  duties holds by the skills, not by identity (decision 2).
- The reviewer is told to break the change, "any model family". **Keep** the
  engine as data (Direction 2); Ship's find/verify/rank/post shape applies
  whatever the engine.
- `is_prose` reads a root Markdown file as prose, so a root `REVIEW.md` would
  carry a verdict unreviewed today. `process/ship-rules` reads it as
  instructions (`_AGENT_INSTRUCTIONS_RE`, `check_shapes.py` `INSTRUCTION_FILES`).
- A root `.claude/approvals.yml` also switches on Ship's own Approvals where the
  Claude GitHub App is linked to a Team or Enterprise org with usage credits.
  The App is installed (the agents use it); no qualifying org is linked. If one
  ever is, an Owner's "Block globally" on the Approve row keeps our file ours.
- AGENTS.md Fact 4 now makes repo settings the agent's call, so the enforce
  step's ruleset `PUT` is the coordinator's, not a founder ask.

## Target pipeline

**Identities** (decision 2). Agents and the founder are one GitHub user. Agents
write through the Claude GitHub App; the founder writes in GitHub's own UI or
app. The review App (5634948, already the only poster of `review`) posts the
approvals check and the approving review. Its approval is the only one that can
meet the required approval: the founder's user authors every PR and GitHub
refuses an author's approval. Ship's docs say an app's approving review "can
meet a GitHub branch rule that requires one approval"; that it does under our
ruleset is a live check before enforce relies on it (owed below). The admin
bypass stays: a known gap, the founder's choice.

**The founder's consultation** (decision 1) is judgment, in Ship's classifier
form. The reviewer session answers one question on each head it reviews,
defined once in Ship's format:

```yaml
# .claude/classifiers/founder_consult/def.yml
version: 1
description: "Does this change need the founder?"
model: opus
prompt: |
  Answer from the diff, the PR body and AGENTS.md's Direction, at main.
  design_frame: it sets or changes a design frame or shape: a new module, layer,
    boundary, contract, data model, pipeline or ADR; or it reverses a decided one.
  direction_drift: it moves away from a line of AGENTS.md's Direction, or builds
    something the Direction says is no longer the direction.
  feedback: it makes a product or process choice the founder has not made and
    the repo does not settle, or its author asks for his view.
  none: anything else.
  When two fit, pick the first. For any answer but none, write the one question
  the founder must answer, in one sentence a non-engineer can answer.
outputs:
  consult: { type: criteria, values: [none, design_frame, direction_drift, feedback] }
```

`model` is kept because Ship's format requires it; the reviewer session is the
model that answers (decision 3), where Ship starts a run of its own. The answer
goes in the reviewer's result line. When it is not `none`, the approvals job's
one comment on the PR (edited in place, as Ship's is) asks the question and how
to answer it, and the coordinator relays the same question to the founder in
its session. The founder answers on the PR: a top-level comment whose whole
text is `/approve <head sha>`, or feedback, which the steward acts on.

**`pr/founder_approved`**, the one fact our check adds to Ship's grammar, is
true when such a `/approve` comment names the head, was made after the PR's
last base change, and carries no `performed_via_github_app`: the founder's own
hand, not an agent's App. Ship counts an author's `@claude approve` toward a
seat; we cannot, since no seat can be told apart. `/approve` because `@claude`
is Anthropic's app's trigger. It holds only while every agent writes through an
App: a raw `gh` token in an agent's hands would read as the founder. So no
agent holds a GitHub token outside the Claude App (a steward-skill rule), and
the first founder `/approve` proves the null live (owed below).

**The policy**, Ship's grammar, `mode: observe` until enforce:

```yaml
version: 1
mode: observe
rules:
  - purpose: "Claude approves a change Code Review passes that needs nothing from the founder; one that sets or changes a design frame or shape, drifts from the Direction or needs his feedback waits for his /approve."
    paths: ["**"]
    verdict: { claude: comments, seats: 0 }
    override:
      - { if: "pr/founder_approved = true", verdict: { claude: approves, seats: 0 } }
      - { if: "claude/code_review = pass AND repo/founder_consult = none", verdict: { claude: approves, seats: 0 } }
  - purpose: "No agent loosens its own gate: a change to what decides a merge waits for the founder's /approve, whatever a classifier answers."
    paths:
      - "**/.claude/**"
      - ".agents/skills/{untangle,steward}/**"
      - "AGENTS.md"
      - "**/CLAUDE.md"
      - "**/REVIEW.md"
      - ".mcp.json"
      - ".github/CODEOWNERS"
      - ".github/workflows/{approvals,review,review-signal,review-queue,pr-scope-guard,auto-enroll-merge,tests,invariants,queue-handoff}.yml"
      - "docs/reference/executable-gates.md"
      - "docs/ops/merge-queue-ruleset.json"
      - "scripts/{approvals,review_result,review_checks,test_hygiene_gate,python_tests,platform_tests,ci_required_tests,affected_tests,ruleset_drift,queue_attempts,invariants_run,check_context_budget}.py"
      - "scripts/invariants/**"
    nested: inherit
    verdict: { claude: comments, seats: 0 }
    override:
      - { if: "pr/founder_approved = true", verdict: { claude: approves, seats: 0 } }
```

`seats` is 0 throughout because no seat can be told apart (decision 2): the
founder's user fills the author's seat on every PR. `claude: comments` means
the review App does not approve, so the ruleset's one required approval holds
the PR: Ship's "Passed; no approval from Claude". A failed or missing review
falls to that verdict, for the steward to fix or the founder to `/approve`.

The gate rule stays a path rule, not the classifier's call: a classifier reads
the author's text and can be steered by it, and Ship's docs say to ask for a
person "in a rule that reads no classifier" on paths that matter. It covers
only the files that decide whether a PR merges (15% of November's PRs); the
other workflows, `platform/deploy/**` and `platform/contract/identity.json` are
the classifier's and REVIEW.md's (secret handling there is Important; a
`routes` change without the handles canary of Fact 11 is Important). Nothing
they hold acts at merge: `platform-deploy.yml` is manual, and "the founder's
dispatch is the approval".

**Files**, each in Ship's format, read at `origin/main`: `REVIEW.md` (includes
`@.claude/skills/untangle/SKILL.md`; what Important means here; Nits capped;
what CI proves is not reported; evidence by `file:line`), the steward skill
(`.agents/skills/steward/SKILL.md` mirrored to `.claude/skills/steward/`; its
frontmatter is name and description only, as `validate_skills.py` requires),
the classifier above and the policy above.

**The result line.** The reviewer's review ends with one machine line, written
and read by one module (`scripts/review_result.py`):
`<!-- code-review: {"head":"<sha>","important":0,"nit":2,"pre_existing":1,"founder_consult":"none","question":""} -->`,
after Ship's `bughunter-severity` line.

**Actions.** `approvals.yml` (default branch's copy; pull_request_target on a new
head, edit or retarget; issue_comment for `/approve`; workflow_run of
`review-signal.yml` for reviews; merge_group) runs `scripts/approvals.py`: load
and validate the policy, match paths, read the facts and the result line,
combine (strictest wins, first true override), post the `approvals` check and
the one comment as the review App, neutral in observe; in enforce, approve where
the verdict says so and stamp merge groups. It checks out no PR code.

**Ruleset at enforce** (decision 4): a `pull_request` rule (one approval,
dismiss stale on push); required checks `Diff scope declared`,
`required-tests`, `invariants` and `approvals` (the review App as source);
`review` gone; the admin bypass unchanged (decision 2).

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
| The reviewer's first line `APPROVE`/`BLOCK`, its receipt | the reviewer section of the peer-agents skill until enforce; the receipt goes with `land_pr2.sh` |
| Ship's separation of duties (review never fixes; severities; every head reviewed) | the untangle skill's reviewer lines, rewritten; REVIEW.md; the steward skill |
| `land_pr2.sh` | not moved: the reviewer posts its own review (`scripts/review_result.py`), the steward opens and readies the PR; deleted in `process/ship-code-review` |

## Migration order

Expand, then contract; nothing retires until its replacement is enforced.
Ship's own order: Code Review, then Steward on Manual, then Approvals in
observe, then enforce. Lanes 2 and 3 share no file and run in parallel.

- [ ] Rules into the repo: `process/ship-rules`
- [ ] Session reviewer in Ship's shape, the result line, the land step gone: `process/ship-code-review`
- [ ] Session steward, Manual then On: `process/ship-steward`
- [ ] The approvals check in observe: `process/ship-approvals`
- [ ] Enforce, require `approvals`, retire `review`: `process/ship-enforce`
- [ ] live: the founder's first `/approve` comment reads `performed_via_github_app` null in the approvals job's log (observe period of `process/ship-approvals`)
- [ ] live: the review App's approving review meets the ruleset's required approval, on the first PR after `process/ship-enforce`

1. **`process/ship-rules`** (depends on this PR). Adds `REVIEW.md`,
   `.agents/skills/steward/SKILL.md` and its mirror,
   `.claude/classifiers/founder_consult/def.yml` and `.claude/approvals.yml`
   (observe), with the rules of § Where the host rules land; rewrites the
   untangle skill's reviewer lines to Ship's separation (no fixes, no
   mutation reruns, every head reviewed); drops `carries`' prose rule and reads
   `REVIEW.md` as instructions (`review_checks.py`, `check_shapes.py`); the
   gates page's carry sentence. The skills aggregate pays for the steward skill
   from the untangle skill's removed lines, any rest recorded as a pin raise
   inside the aggregate total, as #5508, #5593 and #5605 did. Gates:
   `validate_skills.py`, `test_skill_mirrors.py`, `check_context_budget.py` and
   `test_rulebook_ratchet.py`, `scripts/tests/test_review_check.py` (the prose
   case flips: a `Test-Removal:` line), `check_shapes.py`,
   `tests/test_live_docs_reference_real_scripts.py`, a YAML-grammar test of the
   policy and classifier files against this file's rules, `--untangle-check`.
   The host briefs shrink to mechanics in the same hour.
2. **`process/ship-code-review`** (after 1). Adds `scripts/review_result.py`
   (format, parse and post one review: inline comments, body, the result line,
   `COMMENT` event on the commit read, via `gh api`) and its tests; rewrites the
   peer-agents skill's reviewer section to the find/verify/rank/post procedure
   (parallel finders, one verifier, REVIEW.md and CLAUDE.md read at
   `origin/main`, one review per head, its own fixed threads resolved); the
   verdict's first line stays for `review.yml` until enforce. The host's
   `land_pr2.sh` and receipts are deleted. Gates: pytest of the module (a
   result line round-trips; a head mismatch, a missing line and a malformed
   one fail loudly), `validate_skills.py`, `--untangle-check`.
3. **`process/ship-steward`** (after 1). Adds `scripts/steward_watch.py` (what a
   PR needs, read only: failing checks with the PR's touched files, conflicts,
   open Important threads, a person's unanswered comments, and when the watch
   ends: merge, close, days without a person, the cap) and its tests; the steward
   skill's watch procedure (Ship's defaults: fix in touched code, merge the base,
   re-run a flaky job once, reply on each thread, the opt-out marker and the
   `steward-watching` label). Manual first: the coordinator starts a steward per
   PR; On once a week of PRs shows its fixes are the reviewers'. Gates: pytest of
   the module against recorded API shapes, `validate_skills.py`,
   `--untangle-check`.
4. **`process/ship-approvals`** (after 1 and 2). Adds `scripts/approvals.py` (the
   grammar subset, refusing every key and fact it does not implement; Ship's
   glob rules; facts `pr/founder_approved`; classifiers `claude/code_review` and
   `repo/founder_consult` from `review_result.py`; combining; the check, the one
   comment, the approving review, the merge-group stamp), its tests and
   `.github/workflows/approvals.yml`; `review-signal.yml` also wakes it; the
   review App is granted pull-requests write (a settings act, Fact 4). Runs in
   observe beside `review` until a week of PRs shows its decisions match the
   reviewers'. Gates: pytest (refusals, globs, strictest-wins, overrides in order,
   observe neutral, the `/approve` fact on SHA, App marker and base change, the
   stamp), actionlint, `ci_structural_guards.py`, the `environment`-guard tests,
   `--untangle-check`. Its own change is a gate file, so it needs the founder's
   `/approve`, which it cannot read yet: the founder's comment on its PR is read
   by hand by its reviewer.
5. **`process/ship-enforce`** (after 4's observe period). Deletes the policy's
   `mode: observe` line; the ruleset file gains the `pull_request` rule and
   `approvals` and loses `review`, and the coordinator `PUT`s it (Fact 4);
   deletes `review.yml`, `review-queue.yml` and `review_checks.py`'s verdict,
   `carries` and check-run code (the Untangle check and `last_base_change`
   stay); supersedes ADR-038 with a new ADR; rewrites the openspec delivery
   contract's "A review verdict is a check run only the review App posts", the
   gates page's landing paragraph, and the verdict line in the peer-agents
   skill. Gates: the full CI, `ruleset_drift.py` after the `PUT`, the two live
   checks above.

## Founder decisions

Asked 2026-11-24, answered the same day.

1. **Who needs a person: by judgment, not paths.** "I should be consulted when
   design frame or shape or project drift or feedback is needed." Modelled as
   the `founder_consult` classifier above, read in an override, its question
   asked once on the PR and relayed by the coordinator. Recommended and
   designed: the gate files keep a path rule that reads no classifier, as
   Ship's docs advise; the classifier decides everything else.
2. **Keep the founder's account for agents; no machine user or App.** So no
   seat can tell the founder from an agent (his consent is `/approve`, told
   apart by the App marker); the approving review must be the review App's,
   since GitHub refuses an author's; the admin bypass stays as a known gap;
   and the reviewer's result is trusted as far as any agent's comment.
3. **Reviews and fixes stay in Claude sessions on the founder's subscription;
   no API key and no CI-hosted review or steward.** Only the deterministic
   approvals evaluation and the review App's check and approval run in Actions.
   The Direction 8 gap is recorded above as his choice.
4. **Branch rules at enforce: yes** (one approval, dismiss stale on push,
   `approvals` required from the review App). Taking agents off admin is ruled
   out by decision 2.

## This PR

Only this file. The policy, classifier, `REVIEW.md` and steward skill are
`process/ship-rules`'s.
