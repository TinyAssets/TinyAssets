# claude-ship as built (2026-11-24)

Research and design only; nothing in the pipeline changes in this PR. Founder
direction, 2026-11-24: TinyAssets will not subscribe to Claude Ship. Ship is the
blueprint for our own PR pipeline on GitHub: "us doing anything they don't do,
or doing it differently, is highly questionable, because they are the experts
and we have had production issues." So each row below either adopts Ship's way
or keeps ours with a concrete reason Ship's does not apply.

## Sources

Anthropic's Claude Ship docs, read 2026-11-24 (Ship released in beta
2026-11-23; Code Review GA, PR Steward and Claude Approvals public beta), all
under `code.claude.com/docs/en/`: `claude-ship`, `code-review`,
`code-review-customize`, `code-review-pull-requests`, `pr-steward`,
`pr-steward-setup`, `pr-steward-pull-requests`, `pr-steward-security`,
`pr-steward-troubleshooting`, `approvals`, `approvals-policy`,
`approvals-setup`, `approvals-classifiers`, `approvals-github-rules`,
`approvals-pull-requests`.

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
  ADR-038 already records why the verdict is a `COMMENT` review: "the one
  writer account cannot approve its own PR".
- The ruleset's only bypass actor is the admin role, mode `always`, and that
  user is admin, so every agent session can bypass every rule on `main`.
- The ruleset has no `pull_request` rule: no approval is required, and no push
  dismisses one.
- No `Claude Code Review` check run is on #5854's head, so no Ship agent is on
  this repository.
- Of the 535 PRs merged in the 30 days to 2026-11-24, 144 (27%) touch a path
  the policy below gives the founder; 122 of those touch `.github/`. Narrowed to
  the files that define a gate (the review, scope, test, invariants, enroll and
  queue workflows, `CODEOWNERS`, the rule files and the gate scripts), 81 (15%).
  39 of the 59 workflows read a secret or the `production` environment.

## The gap table

"Adopt" changes ours to Ship's way in the build order below. "Keep" names why
Ship's way does not apply here.

### Code Review

| Ship | Ours today | Verdict |
|---|---|---|
| Several agents find in parallel, a verification step drops false positives, findings are deduplicated and ranked | One reviewer agent, host-side, whatever engine the coordinator dispatched (peer-agents skill) | **Adopt**: find, verify, rank, post, as one CI job |
| Severities 🔴 Important (fix before merge), 🟡 Nit, 🟣 Pre-existing; `pass` = no open Important | A first line `APPROVE` or `BLOCK`; blocking findings are the untangle skill's reviewer list | **Adopt**: the reviewer list becomes REVIEW.md's "What Important means here" |
| Inline comments on the lines, a summary, a check run `Claude Code Review` that concludes neutral, never failure | One `COMMENT` review whose body is the evidence; `review.yml` turns it into the required `review` check | **Adopt**: findings are inline comments and a neutral `code-review` check; the gate moves to the approvals check. The name differs from Ship's: a check of Ship's name from another App is the spoof Ship's docs warn of |
| Reads `REVIEW.md` (root, includes `@file`, `@.claude/skills/<folder>/SKILL.md`, ≤8 includes, ≤150,000 bytes) and `CLAUDE.md`, from the default branch, never the PR | The reviewer reads the PR's checkout of the untangle skill, AGENTS.md and the host brief | **Adopt**: `REVIEW.md` in Ship's format, read from the default branch |
| Runs after creation, after every push, or on request (`@claude review`); a push that adds and removes the same lines keeps the earlier result | One review a PR, a second only after a security block (untangle skill); a merge of main touching the PR's code fails `review` until someone asks a re-confirm | **Adopt**: a review on ready and after every push, the delta its focus; `carries` rule 1 (the same change) is Ship's keep-the-result rule and stays |
| A later review closes its own threads it finds fixed; replies are read by the next review | Not done; the coordinator relays | **Adopt** |
| Read-only; never builds or runs the code | The reviewer reruns two or three builder mutations and one per new guard | **Adopt**: the reviewer reads. A test that cannot fail is an Important finding read from the code; mutations are the builder's done-check |
| Skips drafts | The builder pushes a draft early; CI runs on it; review waits for ready | Matches |

### PR Steward

| Ship | Ours today | Verdict |
|---|---|---|
| Fixes failing checks in code the PR touches, conflicts, review comments, Code Review's Important findings | The builder lane, re-dispatched by the coordinator; and the reviewer commits fixes of up to 50 lines and approves that head (gap a) | **Adopt**: a steward that fixes and never approves; the reviewer stops fixing |
| Merges the base in on a conflict; never rebases | "Merge `origin/main` in; never rebase" (untangle skill) | Matches |
| Never approves or merges; the service refuses approving, changing branch protection, rulesets, statuses, check runs, deleting branches, pushing tags | Agents act as the founder's admin user, so nothing refuses any of these | **Adopt**: by identity (below), not by a promise |
| Re-runs a flaky job once (failed before any test ran, or passed earlier on the same commit) | "After a red CI job, rerun only its failures" (gates); `queue-handoff` stops after two counted queue failures | **Adopt** Ship's flaky rule into the steward skill; `queue-handoff` stays until the steward takes queue failures, then is re-examined |
| Instructions in `.claude/skills/steward/SKILL.md` on the default branch, plus `CLAUDE.md`/`AGENTS.md` | Builder rules live partly in host briefs outside the repo (`brief_lane.md`'s "found on #58xx" lines) | **Adopt**: the brief's rules move into the repo; briefs keep host mechanics only |
| Watch ends at merge or close, or after days with no person commenting; spend cap per PR and per month | No watch; the coordinator decides; no cap | **Adopt** both (Fact 15: every limit recorded, every path honours the owner's) |
| Opt-out marker in the body, a label while it works, stop by comment or label | None | **Adopt** |
| Starts Manual first, then On | n/a | **Adopt** |

### Claude Approvals

| Ship | Ours today | Verdict |
|---|---|---|
| A policy file `.claude/approvals.yml` (paths, verdict `claude` + `seats`, `override`, `if`, `reuse`, `findings`, `nested`, `locals`); strictest verdict wins; unknown keys refused | No policy: every PR needs the same one verdict | **Adopt**: our approvals check reads Ship's grammar, the subset our policy uses, and refuses the rest loudly (Fact 8) |
| People lists: `humans`, `bots`, `"!pr/authors"`, logins; only writers count; the author can fill one seat | "The latest verdict by an account that can push" decides; that is every agent, as the founder | **Adopt** |
| Built-in protected files (policy, classifiers, root `REVIEW.md`, `CODEOWNERS`, git files); `.github/`, `CLAUDE.md`, `AGENTS.md`, `.mcp.json`, `.claude/` need your own rules | None; a rule file is reviewed like code | **Adopt**, with the rule below |
| Observe mode first (check neutral), then enforce | n/a | **Adopt**: observe beside `review`, then enforce in the change that retires `review` |
| The required check pinned to the app's id as its source | `review` pinned to the review App (5634948), which only a `main`-only environment can mint | Matches; the approvals check is posted by the same App |
| Claude's approving review only where every applying rule says `approves`; Claude's approval dismissed on a diff-changing push | A `COMMENT` review whose first line approves, posted by the founder's user | **Adopt**: a real approving review by the review App |
| `reuse` keeps nothing by default; `claude/diff` can keep approvals across same-intent pushes | `carries`: same change, or a delta of Markdown prose | **Adopt**: the prose carry goes (gap c); the same-change carry is Code Review's keep-the-result |
| Merge-queue commits are stamped, not re-evaluated | `review-queue.yml` signals, and `review.yml` decides the group's PR afresh | **Adopt** the stamp; `review-queue.yml` goes |
| A condition cannot read CI; keep CI as its own required check | `Diff scope declared`, `required-tests`, `invariants` required beside `review` | Matches |

### Branch rules, identity, and the no-human warning

| Ship | Ours today | Verdict |
|---|---|---|
| Require a PR with ≥1 approval; dismiss stale approvals on push (or require approval of the latest push) | No `pull_request` rule | **Adopt** at enforce (needs a founder settings change) |
| The App off the bypass list | The admin role bypasses always, and every agent is that admin | **Adopt**: agents get write, not admin; the founder keeps the admin bypass |
| Agents act as an App, so a person and an agent are told apart | Every agent acts as the founder's user (gap b) | **Adopt** (founder decision 2) |
| GitHub forbids the opener approving, so PRs `claude[bot]` opens need a second App to approve | Hence ADR-038's `COMMENT` reviews | **Adopt**: agents open PRs under their own identity; the review App approves |
| Warning: no person required + auto-merge or a merge queue merges with nobody acting | That is the founder's intent: `auto-enroll` arms every ready PR | **Keep** for ordinary code, the founder's process (decision 1); the policy's founder seat holds the gate files |

### Our own gates, which Ship has no equivalent of

| Ours | Verdict |
|---|---|
| `Diff scope declared`: the body's Untangle check, owed items naming a fix, `Test-Removal:` lines (test hygiene), the release-critical count | **Keep as CI**: mechanical reads of the diff and body, and Ship says keep CI as its own check. What only judgment reads (the check is true; a removal's reason holds; a carried lineage) becomes REVIEW.md Important rules |
| Recorders (`ci/record.ts`, `Recorders-Differ:`) and `required-tests` | **Keep as CI**: behaviour is proven by running code, which Code Review never does |
| `invariants` (rulebook ratchet, skill mirrors) | **Keep as CI** |
| `auto-enroll-merge` (arms every ready PR; refuses while the live ruleset differs from the file, `ruleset_drift.py`) | **Keep**: Ship leaves arming to people; the founder's process has none, so a workflow arms. The drift refusal stays because the ruleset is kept as a file |
| `land_pr2.sh`: reruns the Untangle and hygiene checks, opens the PR, posts the receipt's verdict as the founder's review, marks it ready; `Drain-Review-*` receipts (gap g) | **Adopt** Ship's: no landing step. The checks are CI's already; the verdict is the review job's; opening and readying are the builder's. Retired with `review` |

### Other findings

- The reviewer runs on a host. Ship runs on the vendor's infrastructure, and
  Direction 8 asks every surface to work with no host on. **Adopt**: review,
  approvals and steward run in GitHub Actions.
- The reviewer is told to break the change, "any model family". Ship is one
  vendor's service. **Keep** the engine as data (Direction 2); Ship's
  find/verify/rank/post shape applies whatever the engine.
- `is_prose` reads a root Markdown file as prose, so a root `REVIEW.md` would
  carry a verdict unreviewed today. It must be read as instructions when it
  lands (it joins AGENTS/CLAUDE in `_AGENT_INSTRUCTIONS_RE`, and
  `check_shapes.py` `INSTRUCTION_FILES`).
- A root `.claude/approvals.yml` also switches on Ship's own Approvals where the
  Claude GitHub App is installed and linked to a Team or Enterprise org with
  usage credits. None is linked today; if one ever is, an Owner's "Block
  globally" on the Approve row keeps our file ours.
- A steward skill adds to the skills aggregate pin (34,462 of 34,467 bytes).
  The reviewer lines the untangle skill and peer-agents drop at enforce pay
  for it; the steward PR moves the brief's lines in and records the bytes.

## Target pipeline

**Identities.** Agents (builders and steward) push and open PRs as one agent
identity with write access and no admin. The review App (5634948, already the
only poster of `review`) posts Code Review's findings and check, the approvals
check, and the approving review; it needs pull requests write. The founder's
user is the only human with write access, and keeps the admin bypass. The agent
identity's approvals never count (it is the author, and its login is excluded
from the founder's seat).

**Files**, each in Ship's format, read from `main`:

- `REVIEW.md`: what Important means here (the untangle skill's reviewer list,
  AGENTS.md's Facts, the brief's reviewer lines), Nits capped, what not to report
  (what CI already proves, `archive/`, the dated records in `docs/decisions/`),
  evidence by `file:line`; includes `@.claude/skills/untangle/SKILL.md` rather
  than copying it.
- `.claude/skills/steward/SKILL.md`, mirrored from `.agents/skills/steward/`
  (`validate_skills.py` holds frontmatter to name and description, so Ship's
  `user-invocable`/`disable-model-invocation` keys are left out): merge the base
  in, never rebase or force-push; the done-check before a push; never skip,
  loosen or delete a test or guard to turn a check green; no live act (Fact 4);
  a path the policy gives the founder gets a reply, never a push; answer each
  thread in it, resolve only what a push fixed; Ship's flaky rule.
- `.claude/approvals.yml`, Ship's "Let Claude approve when Code Review passes"
  starter with its second rule widened to our gates:

```yaml
version: 1
mode: observe
locals:
  # Ship's "ask for a person who isn't an author". Add "!<agent login>" once it exists.
  founder: { count: 1, from: [humans, "!pr/authors"] }
rules:
  - purpose: "A change takes two people, the author one of them. When Code Review passes, the author is enough and Claude approves."
    paths: ["**"]
    verdict: { claude: comments, seats: 2 }
    override:
      - { if: "claude/code_review = pass", verdict: { claude: approves, seats: 1 } }
  - purpose: "No agent loosens its own gate: what reviews, approves, gates or instructs agents takes the founder."
    paths:
      - "**/.claude/**"
      - ".agents/skills/**"
      - "AGENTS.md"
      - "**/CLAUDE.md"
      - "**/REVIEW.md"
      - ".mcp.json"
      - ".github/**"
      - "docs/reference/executable-gates.md"
      - "docs/ops/merge-queue-ruleset.json"
      - "scripts/{review_checks,test_hygiene_gate,python_tests,platform_tests,ci_required_tests,affected_tests,ruleset_drift,queue_attempts,invariants_run,check_context_budget}.py"
      - "scripts/invariants/**"
    nested: inherit
    verdict: { claude: comments, seats: ["@founder"] }
```

With the agent identity as author, rule 1 is the founder's process: Code Review
passes, the author fills the one seat, the review App approves, auto-merge
queues it. A failed or missing review falls back to the rule's own verdict,
the author and one more person, so the steward fixes it or the founder
approves past a false finding. Rule 2 needs the founder whatever the review
says. Not founder paths, and why: `platform/deploy/**` (106 PRs in 30 days) and
`platform/contract/identity.json` act on nothing at merge, because
`platform-deploy.yml` is manual and "the founder's dispatch is the approval";
the workflow that holds their secrets is under `.github/**`. REVIEW.md marks
secret handling there, and a `routes` change without the handles canary
(Fact 11), as Important. The other guards and tests stay agents' to change in
the same PR (the untangle skill), visible through test hygiene.

**Workflows.** `code-review` (pull_request_target on ready and each push, the
default branch's copy, the PR read as data by an engine holding only a read
token, findings posted by a separate step as the review App), `approvals`
(the same triggers plus reviews and `code-review`'s completion; evaluates the
policy; neutral in observe; on enforce passes or waits, approves where allowed,
stamps merge groups), and `steward` (on a failing check, an Important finding,
a conflict or a person's comment; acts as the agent identity; capped per PR;
watch ends at merge, close or days without a person). Kept as they are:
`pr-scope-guard`, `tests`, `invariants`, `auto-enroll-merge`, `queue-handoff`
(re-examined once the steward runs), `main-red`.

**Ruleset at enforce.** A `pull_request` rule (one approval, dismiss stale on
push); required checks `Diff scope declared`, `required-tests`, `invariants`
and `approvals` (the review App as source), `review` gone; the admin bypass
kept, with no agent an admin.

## Migration order

Expand, then contract; nothing retires until its replacement is enforced.
Ship's own order: Code Review, then Steward on Manual, then Approvals in
observe, then enforce.

1. Founder decisions below, and the agent identity made (founder).
2. Rule files: `REVIEW.md`, the steward skill, `.claude/approvals.yml` in
   observe; `REVIEW.md` read as instructions (`is_prose`, `check_shapes.py`);
   the brief's builder and reviewer lines moved in. Inert: nothing reads them yet.
3. `code-review` runs beside the host reviewer, which still decides `review`.
4. `steward` on Manual, then On; the reviewer stops committing fixes.
5. `approvals` in observe beside `review`, until a week of PRs shows its
   decisions match the reviewers'.
6. One change: the policy's `mode: observe` line goes, the ruleset gains the
   `pull_request` rule and `approvals` and loses `review` (founder's PUT), and
   the same PR deletes `review.yml`, `review-signal.yml`, `review-queue.yml` and
   `review_checks.py`'s verdict, `carries` and check-run code (the Untangle check
   stays), supersedes ADR-038 with a new ADR, rewrites the openspec delivery
   contract's "A review verdict is a check run only the review App posts", the
   gates page's landing paragraph, the untangle skill's reviewer mechanics and
   peer-agents' reviewer dispatch; the host deletes `land_pr2.sh`, the receipts
   and the brief's reviewer lines.

- [ ] Rule files and the brief's rules in the repo: `process/ship-rules`
- [ ] Our Code Review job: `process/ship-code-review`
- [ ] Our steward job, Manual then On: `process/ship-steward`
- [ ] Our approvals check in observe: `process/ship-approvals`
- [ ] Enforce, require `approvals`, retire `review` and the land step: `process/ship-enforce`

## Founder decisions

Each changes the founder's current process or is spending or outward-facing.
Recommendations in bold.

1. **Which PRs need a person.** Ship's two starters: a person on every PR, or
   Claude approves when Code Review passes and people only on the review rules.
   **Recommend the second, widened to the gate files above**: ordinary code
   keeps no human; the founder approves 15% (gate-defining files only) to 27%
   (all of `.github/**`, as drafted) of PRs, about 3 to 5.5 a working day at
   November's rate. **Recommend all of `.github/**`**: 39 of 59 workflows hold
   secrets, and Ship's docs name `.github/` for a rule.
2. **An identity for agents** (outward-facing: a new GitHub account or App).
   Without it no policy can tell the founder from an agent, and agents keep the
   admin bypass. **Recommend one machine user** with write, not admin, used by
   every agent session and job: the agents' sessions push as the GitHub user
   their account connects (every PR here is the founder's user today), so a
   machine user fits them where an App's installation token does not; Ship's
   grammar treats it as a person, filling the author's seat. Plus the
   review App's pull-requests-write permission, accepted by the founder.
3. **What pays for review and steward runs in CI** (spending). **Recommend an
   API key with a hard monthly cap** in the `production` environment, per
   ADR-031's lesson that unattended runs belong on API keys and Ship's own
   model (usage credits with per-service caps). The cap's number is the
   founder's.
4. **Review after every push** (spending), Ship's default for an added
   repository, with the same-change carry. **Recommend yes**, and dropping the
   prose carry: Ship carries nothing by rule.
5. **The branch-rule change at enforce** (settings): one required approval,
   dismiss stale on push, `approvals` required from the review App, agents off
   admin. **Recommend yes**, in the one enforce change.

## This PR

Only this file. No `.claude/approvals.yml`, `REVIEW.md` or steward skill was
committed: they are step 2's, once decisions 1 and 2 fix their content, and an
unread `.claude/approvals.yml` on `main` would only invite Ship's own Approvals
to read it.
