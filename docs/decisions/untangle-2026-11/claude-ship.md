# claude-ship as built (2026-11-24)

Research and design only; nothing in the pipeline changes in this PR. Founder
direction, 2026-11-24: TinyAssets will not subscribe to Claude Ship. Ship is the
blueprint for our own PR pipeline on GitHub: "us doing anything they don't do,
or doing it differently, is highly questionable, because they are the experts
and we have had production issues." So each row below either adopts Ship's way
or keeps ours with a concrete reason. The founder answered the design's
decisions (§ Founder decisions) and made the pipeline process work, ahead of
product work: "Claude Ship is process improvement so it is prioritized and
implemented." Updated 2026-11-28 for those answers and for #5856's review (a
`BLOCK` at a2360fb55).

## Sources

Anthropic's Claude Ship docs, read 2026-11-24 and again by #5856's reviewer on
2026-11-28 (Ship released in beta 2026-11-23; Code Review GA, PR Steward and
Claude Approvals public beta), all under `code.claude.com/docs/en/`:
`claude-ship`, `code-review`, `code-review-customize`,
`code-review-pull-requests`, `pr-steward`, `pr-steward-setup`,
`pr-steward-pull-requests`, `pr-steward-security`, `pr-steward-troubleshooting`,
`approvals`, `approvals-policy`, `approvals-setup`, `approvals-classifiers`,
`approvals-github-rules`, `approvals-pull-requests`. GitHub's own docs were not
reachable from the session (`docs.github.com` is outside its network); what they
would settle is an owed live check below.

Ours, read at `origin/main` 42ad8e8b0: `.github/workflows/` `review.yml`,
`review-signal.yml`, `review-queue.yml`, `auto-enroll-merge.yml`,
`queue-handoff.yml`, `pr-scope-guard.yml`, `tests.yml`, `okf-watch.yml`;
`scripts/review_checks.py`, `scripts/ci_required_tests.py`,
`scripts/test_hygiene_gate.py`, `scripts/ruleset_drift.py`,
`scripts/validate_skills.py`; `docs/ops/merge-queue-ruleset.json` and the live
ruleset 24065294 (read only; identical to the file); ADR-038;
`docs/reference/executable-gates.md`; the untangle skill's "How this project
uses it"; and the host side, outside the repo: `land_pr2.sh`, `brief_common.md`
and `untangle/brief_lane.md`.

Facts measured for this file:

- Every PR on `main` is opened, pushed and reviewed by one GitHub user, the
  founder's (#5846 to #5854 opened by it; #5854's one verdict review is by it).
  ADR-038 records why the verdict is a `COMMENT` review: "the one writer
  account cannot approve its own PR".
- Agents write as that user by several paths. Most of their comments go
  through the Claude GitHub App and carry `performed_via_github_app: claude`,
  but not all: comment 7057154858 (#5696, an agent's review receipt) and
  7194785637 (#5692) carry none. Host-run agents and `land_pr2.sh` use the
  founder's own `gh` login; `MERGE_ATTRIBUTION_TOKEN`, a user-owned
  fine-grained PAT with pull-requests write, sits in the `production`
  environment for `auto-enroll-merge.yml` and the scheduled `okf-watch.yml`;
  the browser skills act with the founder's own sign-ins. So nothing GitHub
  records tells a comment by the founder from one by an agent.
- The ruleset's only bypass actor is the admin role, mode `always`, and that
  user is admin, so every agent session can bypass every rule on `main`. The
  ruleset has no `pull_request`, `non_fast_forward` or `deletion` rule.
- No `Claude Code Review` check run is on #5854's head, so no Ship agent is on
  this repository.
- Workflows act at merge with secrets: `deploy-worker.yml`,
  `deploy-site-react.yml`, `build-image.yml` (which chains `deploy-prod.yml` and
  `install-host-services.yml`) and `android-build.yml` run on push to `main`;
  `okf-watch.yml` runs on a schedule holding the founder's PAT;
  `platform-canary.yml` and `offsite-copy.yml` run the deploy kit on a
  schedule. 39 of the 59 workflows read a secret or the `production`
  environment.
- `.github/known-failing-tests.txt` is the quarantine that lets
  `required-tests` pass a listed failure (`scripts/ci_required_tests.py`
  `QUARANTINE`); the scope guard counts it as release-critical, with
  `.github/heavy-test-files.txt`, `scripts/ci_structural_guards.py`,
  `scripts/check_shapes.py`, `scripts/review_checks.py`,
  `tests/test_shape_registry.py`, `deploy/`, `platform/deploy/`, `Dockerfile`
  and `.dockerignore` (`pr-scope-guard.yml` `SENSITIVE_RE`).
- Of the 535 PRs merged in the 30 days to 2026-11-24, 155 (29%) touch the
  gate rule's paths below without the two deploy directories, and 212 (40%)
  with them.
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
| Inline comments on the lines, a summary, a check run `Claude Code Review` that concludes neutral, never failure | One `COMMENT` review whose body is the evidence; `review.yml` turns it into the required `review` check | **Adopt**: findings as inline comments in the reviewer's review, its result in a machine line of the body; the gate moves to the approvals check. Our names (`code-review`, `approvals`, `repo/code_review`) are not Ship's: a check of Ship's name from another app is the spoof Ship's docs warn of, and `claude/code_review` in a policy would start billed Ship reviews if Ship's Approvals were ever linked |
| Reads `REVIEW.md` (root, includes `@file`, `@.claude/skills/<folder>/SKILL.md`, ≤8 includes, ≤150,000 bytes) and `CLAUDE.md`, from the default branch, never the PR | The reviewer reads the PR's checkout of the untangle skill, AGENTS.md and a host brief | **Adopt**: `REVIEW.md` in Ship's format, read at `origin/main` |
| Runs after creation, after every push, or on request; a push keeps the earlier result only while the PR adds and removes the same lines and nobody has resolved an Important thread since | **In effect**: every new head is reviewed again. `review.yml` still carries a verdict across a prose-only delta | **Adopt**: the prose carry goes in `process/ship-rules`; `carries` rule 1 (the same change) is Ship's first condition, and the reviewer adds the second (no Important thread resolved since its review) |
| A later review closes its own threads it finds fixed; replies are read by the next review | Not done; the coordinator relays | **Adopt** |
| Read-only; never builds or runs the code | **In effect**: the reviewer never fixes. It still reruns two or three builder mutations (untangle skill) | **Adopt**: the reviewer reads; a test that cannot fail is an Important finding read from the code; mutations are the builder's done-check |
| Skips drafts | The builder pushes a draft early; CI runs on it; review waits for ready | Matches |

### PR Steward

| Ship | Ours today | Verdict |
|---|---|---|
| Fixes failing checks in code the PR touches, conflicts, review comments, Code Review's Important findings | **In effect**: the builder (or a fresh fixer) is the PR's steward and pushes the fixes | **Adopt**, as a session (founder decision 3) |
| Merges the base in on a conflict; never rebases | "Merge `origin/main` in; never rebase" (untangle skill) | Matches |
| Never approves or merges; the service refuses approving, changing branch protection, rulesets, statuses, check runs, deleting branches, pushing tags | Agents act as the founder's admin user, so nothing refuses any of these | **Keep, a known gap** (founder decisions 2 and 5): the steward skill forbids them, `/approve` among them; nothing enforces it. GitHub refuses one, an author approving its own PR |
| Re-runs a flaky job once (failed before any test ran, or passed earlier on the same commit) | "After a red CI job, rerun only its failures" (gates); `queue-handoff` stops after two counted queue failures | **Adopt** Ship's flaky rule into the steward skill; `queue-handoff` stays until the steward takes queue failures, then is re-examined |
| Instructions in `.claude/skills/steward/SKILL.md` on the default branch, plus `CLAUDE.md`/`AGENTS.md` | Builder rules live partly in host briefs, lost in the 2026-11-28 restart | **Adopt**, first (`process/ship-rules`, § Where the host rules land) |
| The skill's example frontmatter sets `user-invocable: false` and `disable-model-invocation: true`: a house-rules file only the service reads | n/a | **Differs**: our steward is every builder session, so the skill must load for them; it keeps name and description only |
| Watch ends at merge or close, or after days with no person commenting; spend cap per PR and per month | No watch; the coordinator decides; no cap | **Adopt** both, the cap as sessions a PR may take (Fact 15). No comment can be told to be a person's (decision 5), so the idle watch ends after days with no new head, review or comment at all |
| Opt-out marker in the body, a label while it works, stop by comment or label | None | **Adopt** |
| Starts Manual first, then On | n/a | **Adopt** |

### Claude Approvals

| Ship | Ours today | Verdict |
|---|---|---|
| A policy file `.claude/approvals.yml`: top level `version`, `rules`, `mode`, `nested`, `run`, `locals`; a rule's `purpose`, `paths`, `verdict` (`claude`, `seats`, `max_age`), `if`, `override`, `advisory`, `reuse`, `findings`, `use`, `mode`, `nested`, `run`; strictest verdict wins; unknown keys refused | No policy: every PR needs the same one verdict | **Adopt**: our approvals check reads Ship's grammar, implements the keys our policy uses, and refuses every other key loudly (Fact 8) |
| A custom classifier, `.claude/classifiers/<name>/def.yml` (question, outputs), read in an override's `if`; the rule's own verdict when it gives no answer | None | **Adopt**, for our reviewer's result and the founder's consultation |
| People lists: `humans`, `bots`, `"!pr/authors"`, logins; the author can fill one seat; `@claude approve` counts only from a write-access account, only unedited, only for the latest commit | "The latest verdict by an account that can push" decides; that is every agent, as the founder | **Differs**: no seat can tell the founder from an agent, so seats stay 0 and his consent is a fact, `ta/founder_approved`, with Ship's `@claude approve` tests (his login, write access, unedited, the current head). **Agents can produce it**: the founder's accepted risk (decision 5) |
| Built-in protected files: every policy file, `.claude/classifiers/`, root `REVIEW.md`, `.gitmodules`, `.gitattributes`, symlinks, submodule pointers, any `CODEOWNERS`; never an approval of a `.gitattributes`, symlink or submodule-pointer change; `.github/`, `CLAUDE.md`, `AGENTS.md`, `.mcp.json`, `.claude/` need your own rules; on paths that matter, a rule that reads no classifier | None; a rule file is reviewed like code | **Adopt**: the built-in list in `approvals.py`, and the gate rule below (decision 6) |
| `claude: approves` starts no review; let Claude approve only when Code Review passes | n/a | **Adopt**: no override approves without `repo/code_review = pass`, the founder's consent included |
| Observe mode first (check neutral), then enforce in four steps: merge the deletion of `mode: observe`; rerun open PRs and keep them out of the queue; require the check; change GitHub's required approvals last | n/a | **Adopt**, the steps in that order (§ Migration order, lane 5) |
| The required check pinned to the app's id as its source | `review` pinned to the review App (5634948), whose key only a `main`-only environment can mint | Matches; the approvals check is posted by the same App |
| Claude's approving review only where every applying rule says `approves`; dismissed on a diff-changing push | A `COMMENT` review whose first line approves, posted by the founder's user | **Adopt**: a real approving review by the review App |
| `reuse` keeps nothing by default | `carries`: same change, or a delta of Markdown prose | **Adopt**: the prose carry goes |
| Merge-queue commits are stamped, not re-evaluated | `review-queue.yml` (no permission, the group commit's copy) signals, and `review.yml` (the default branch's copy, which can reach the App key) decides the group | **Adopt** the stamp, through the same two steps: a `merge_group` run takes its workflow from the group commit, which holds the PR's edits and cannot reach the `main`-only key, so `review-queue.yml` stays as the signal and `approvals.yml` stamps on its `workflow_run` |
| A condition cannot read CI; keep CI as its own required check | `Diff scope declared`, `required-tests`, `invariants` required beside `review` | Matches |

### Branch rules, identity, and the no-human warning

| Ship | Ours today | Verdict |
|---|---|---|
| Require a PR with ≥1 approval; dismiss stale approvals on push; block force pushes | No `pull_request`, `non_fast_forward` or `deletion` rule | **Adopt** at enforce (decision 4), the force-push and deletion rules too, though the admin bypass makes them moot for agents |
| The App off the bypass list | The admin role bypasses always, and every agent is that admin | **Keep, a known gap** (decision 2): the admin bypass is the agents' too, and enforce cannot remove it |
| Agents act as an App, so a person and an agent are told apart | Every agent acts as the founder's user, by App and non-App paths alike | **Keep, the founder's accepted risk** (decisions 2 and 5): his consultation is honoured by agents, not enforced. A broken or misled agent could approve in his name |
| GitHub forbids the opener approving, so PRs `claude[bot]` opens need a second App to approve | Hence ADR-038's `COMMENT` reviews | **Adopt**: the review App approves |
| Warning: no person required + auto-merge or a merge queue merges with nobody acting | That is the founder's intent: `auto-enroll` arms every ready PR | **Keep** (decision 1): the founder is asked by judgment, and on the gate files if he confirms decision 6 |

### Our own gates, which Ship has no equivalent of

| Ours | Verdict |
|---|---|
| `Diff scope declared`: the body's Untangle check, owed items naming a fix, `Test-Removal:` lines (test hygiene), the release-critical count | **Keep as CI**: mechanical reads of the diff and body, and Ship says keep CI as its own check. What only judgment reads (the check is true; a removal's reason holds; a carried lineage) becomes REVIEW.md Important rules |
| Recorders (`ci/record.ts`, `Recorders-Differ:`) and `required-tests` | **Keep as CI**: behaviour is proven by running code, which Code Review never does |
| `invariants` (rulebook ratchet, skill mirrors) | **Keep as CI** |
| `auto-enroll-merge` (arms every ready PR; refuses while the live ruleset differs from the file, `ruleset_drift.py`) | **Keep**: Ship leaves arming to people; the founder's process has none, so a workflow arms. The drift refusal stays because the ruleset is kept as a file |
| `land_pr2.sh`: reruns the Untangle and hygiene checks, opens the PR, posts the receipt's verdict as a review, marks it ready; `Drain-Review-*` receipts | **Adopt** Ship's: no landing step. The checks are CI's; the reviewer posts its own review; opening and readying are the steward's. It goes in `process/ship-code-review`, before enforce |

### Other findings

- **No signal by the founder's login can prove the founder.** Every path agents
  write by (the Claude App, his own `gh` login, his PAT in `production`, his
  browser sign-ins) produces a comment by his login, and some carry no App
  marker, as an agent receipt already does. The login, write-access, unedited and
  current-head tests stop outsiders on this public repository and stale or
  edited comments; they cannot stop an agent. The founder accepted that gap
  (decision 5); nothing in this design, and no live check, claims otherwise.
- **Runs in sessions, not a service** (decision 3). Ship's agents run on
  Anthropic's infrastructure on every PR event; ours are Claude sessions on the
  founder's subscription, started by the coordinator. With no session running,
  no review or fix happens: a known gap against Direction 8, the founder's
  choice. Only the approvals evaluation runs in Actions. A later option within
  that choice: an Actions job on each new head fires a Claude routine
  (ADR-031 decision 2 allows storing the routine's `/fire` token).
- **The reviewer's result is any agent's word.** The reviewer is an agent, so
  the approvals check trusts a result line exactly as far as `review.yml` trusts
  a verdict today: separation of duties holds by the skills.
- The reviewer is told to break the change, "any model family". **Keep** the
  engine as data (Direction 2); Ship's find/verify/rank/post shape applies
  whatever the engine.
- `is_prose` reads a root Markdown file as prose, so a root `REVIEW.md` would
  carry a verdict unreviewed today. `process/ship-rules` reads it as
  instructions (`_AGENT_INSTRUCTIONS_RE`, `check_shapes.py` `INSTRUCTION_FILES`).
- The gate rule's paths and the scope guard's `SENSITIVE_RE` would be two
  copies of one list. `process/ship-approvals` makes the scope guard's
  release-critical count read the gate rule's paths from the policy.
- A root `.claude/approvals.yml` also switches on Ship's own Approvals where the
  Claude GitHub App is linked to a Team or Enterprise org with usage credits.
  The App is installed (the agents use it); no qualifying org is linked. If one
  ever is, an Owner's "Block globally" on the Approve row keeps our file ours.
- AGENTS.md Fact 4 makes repo settings the agent's call, so the ruleset `PUT`s
  are the coordinator's. A GitHub App's permissions are not repo settings: its
  owner changes them in GitHub's UI and accepts them on the installation, with
  no API, so the review App's new permission is a founder step (a
  `docs/host-actions.md` row).

## Target pipeline

**Identities** (decisions 2 and 5). Agents and the founder are one GitHub user.
The review App (5634948, already the only poster of `review`) posts the
approvals check and the approving review. Its approval is the only one that can
meet the ruleset's required approval: the founder's user authors every PR and
GitHub refuses an author's approval. Ship's docs say an app's approving review
"can meet a GitHub branch rule that requires one approval"; that it does under
our rules is proven live before enforce relies on it (owed below). The admin
bypass stays the agents' too.

**The founder's consultation** (decision 1) is judgment, in Ship's classifier
form. The reviewer session answers two classifiers on each head it reviews,
each defined once in Ship's format. Ship starts a run of its own; ours is the
reviewer (decision 3), so `model` is kept only because the format requires it:

```yaml
# .claude/classifiers/code_review/def.yml
version: 1
description: "Did the review of this head leave no Important finding open?"
model: opus
prompt: |
  pass: the review of this head found no Important finding, or every one it
    found is fixed at this head. fail: anything else.
outputs:
  result: { type: criteria, values: [pass, fail] }
```

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

When `repo/founder_consult` is not `none`, the approvals job's one comment on
the PR (edited in place, as Ship's is) asks the question and how to answer it,
and the coordinator relays the same question to the founder in its session. He
answers on the PR: a top-level comment whose whole text is `/approve <head
sha>`, or feedback, which the steward acts on.

**`ta/founder_approved`** is the one fact our check adds to Ship's grammar,
under our own prefix. It is true when a top-level comment on the PR whose whole
text is `/approve <sha>` is by the founder's login (`locals.founder_login`),
from an account with write access, never edited (`updated_at` equals
`created_at`), made after the PR's last base change, and names the current
head. Those are Ship's tests for `@claude approve`. They stop outsiders and
stale or edited comments; they do not stop an agent, which writes as the same
login (the founder's accepted risk, decision 5). `/approve`, not `@claude
approve`, because `@claude` is Anthropic's app's trigger.

**Agents never post `/approve`**, nor any comment, review or label that reads
as the founder's approval, on any PR. The rule lands in `process/ship-rules`:
in the steward skill, which every builder and steward session loads, and in
REVIEW.md as an Important finding for any change that would post one (a
script, workflow or skill writing `/approve` as the founder's login).

**The policy**, Ship's grammar, `mode: observe` until enforce:

```yaml
version: 1
mode: observe
locals:
  founder_login: "<founder login>"
  gate_paths: [ ... § The gate rule's paths ... ]
rules:
  - purpose: "Claude approves a change our review passes that needs nothing from the founder; one that sets or changes a design frame or shape, drifts from the Direction or needs his feedback waits for his /approve; nothing passes an open Important finding."
    paths: ["**"]
    verdict: { claude: blocks }
    override:
      - { if: "repo/code_review = pass AND repo/founder_consult = none", verdict: { claude: approves, seats: 0 } }
      - { if: "repo/code_review = pass AND ta/founder_approved = true", verdict: { claude: approves, seats: 0 } }
      - { if: "repo/code_review = pass", verdict: { claude: comments, seats: 0 } }
  - purpose: "No agent loosens its own gate: a change to what decides a merge or acts at merge with secrets waits for the founder's /approve, whatever a classifier answers."
    paths: "@gate_paths"
    nested: inherit
    verdict: { claude: comments, seats: 0 }
    override:
      - { if: "repo/code_review = pass AND ta/founder_approved = true", verdict: { claude: approves, seats: 0 } }
```

`seats` is 0 throughout: the founder's user fills the author's seat on every
PR, so a seat tells nothing apart. The rule's own verdict is the strict one, as
Ship advises: with no review, a failed review, or no classifier answer, rule 1
`blocks`, and the founder's `/approve` never approves past an open Important
finding. Strictest wins, so a gate file needs a passing review and the
founder's `/approve`. `claude: comments` means the review App does not approve,
so the ruleset's one required approval holds the PR: Ship's "Passed; no
approval from Claude". Ship's built-in protected files join the gate paths in
`approvals.py`, and the App never approves a `.gitattributes`, symlink or
submodule-pointer change, whatever the rules say.

**The gate rule's paths** (`locals.gate_paths`), a recommendation the founder
must confirm (decision 6), since answer 1 says "judgment, not paths":
`.github/**`; `**/.claude/**`; `.agents/skills/{untangle,steward}/**`;
`AGENTS.md`; `**/CLAUDE.md`; `**/REVIEW.md`; `.mcp.json`;
`docs/reference/executable-gates.md`; `docs/ops/merge-queue-ruleset.json`;
`scripts/{approvals,review_result,review_checks,test_hygiene_gate,python_tests,platform_tests,ci_required_tests,affected_tests,ruleset_drift,queue_attempts,invariants_run,check_context_budget,ci_structural_guards,check_shapes}.py`;
`scripts/invariants/**`; `tests/test_shape_registry.py`; `deploy/**`;
`platform/deploy/**`; `Dockerfile`; `.dockerignore`. Why a path rule at all: a
classifier reads the author's text and can be steered by it, and Ship's docs
say to ask for a person "in a rule that reads no classifier" on paths that
matter. Why this list: it is what decides a merge (`.github/known-failing-tests.txt`
and the other quarantine and gate files under `.github/` among them), what
instructs the reviewer and steward, and what acts at merge or on a schedule with
secrets (the workflows and both deploy directories). Ship's docs name `.github/`
for a rule; the scope guard already counts the rest as release-critical. Its
cost: 40% of November's PRs, or 29% without the two deploy directories. Under
decision 5 it is honoured by agents, not enforced, like the consultation.

**Files**, each in Ship's format, read at `origin/main`: `REVIEW.md` (includes
`@.claude/skills/untangle/SKILL.md`; what Important means here; Nits capped;
what CI proves is not reported; evidence by `file:line`), the steward skill
(`.agents/skills/steward/SKILL.md` mirrored to `.claude/skills/steward/`), the
two classifiers and the policy above.

**The result line.** The reviewer's review ends with one machine line, written
and read by one module (`scripts/review_result.py`):
`<!-- code-review: {"head":"<sha>","important":0,"nit":2,"pre_existing":1,"founder_consult":"none","question":""} -->`,
after Ship's `bughunter-severity` line. `repo/code_review` reads `pass` from
`important` 0 at the current head.

**Actions.** `approvals.yml`, always the default branch's copy: pull_request_target
on a new head, edit or retarget; issue_comment (`/approve`, and `/approvals
rerun` from a writer, Ship's `@claude approvals rerun`); workflow_run of
`review-signal.yml` (reviews; that file runs the PR's copy with no permission)
and of `review-queue.yml` (merge groups, the same reason). It runs
`scripts/approvals.py`: load and validate the policy, match paths with Ship's
glob rules and the built-in protected list, read the facts and the result line,
combine (strictest wins, first true override), post the `approvals` check and
the one comment as the review App, neutral in observe; in enforce, approve where
the verdict says so, and on a merge group stamp the group when its PR's head
passed under enforce. It checks out no PR code.

**Ruleset at enforce** (decision 4), in Ship's four steps (lane 5): `approvals`
required with the review App as source; then a `pull_request` rule (one
approval, dismiss stale on push) with `non_fast_forward` and `deletion`; then
`review` removed. The admin bypass is unchanged.

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
| Ship's separation of duties (review never fixes; severities; every head reviewed) | the untangle skill's reviewer lines, rewritten ("commits its own fixes of up to 50 lines and approves that head" goes); REVIEW.md; the steward skill |
| New with this design: an agent never posts `/approve` or anything that reads as the founder's approval | the steward skill; REVIEW.md, Important |
| `land_pr2.sh` | not moved: the reviewer posts its own review (`scripts/review_result.py`), the steward opens and readies the PR; deleted in `process/ship-code-review` |

## Migration order

Expand, then contract; nothing retires until its replacement is enforced.
Ship's own order: Code Review, then Steward on Manual, then Approvals in
observe, then enforce. Lanes 2 and 3 share no file and run in parallel; lane 4's
build runs beside them, and its observe period starts once lane 3 is on Manual.

- [ ] Rules into the repo: `process/ship-rules`
- [ ] Session reviewer in Ship's shape, the result line, the land step gone: `process/ship-code-review`
- [ ] Session steward, Manual then On: `process/ship-steward`
- [ ] The approvals check in observe, the scope guard reading the gate paths: `process/ship-approvals`
- [ ] Enforce in Ship's four steps, then retire `review`: `process/ship-enforce`
- [ ] live: on a scratch ruleset over a scratch branch with a `pull_request` rule, the review App's approving review satisfies the required approval, before `process/ship-enforce`'s first `PUT`

1. **`process/ship-rules`** (depends on this PR). Adds `REVIEW.md`,
   `.agents/skills/steward/SKILL.md` and its mirror, the two classifier
   definitions and `.claude/approvals.yml` (observe), with the rules of § Where
   the host rules land; rewrites the untangle skill's reviewer lines to Ship's
   separation; drops `carries`' prose rule and reads `REVIEW.md` as instructions
   (`review_checks.py`, `check_shapes.py`); the gates page's carry sentence. The
   skills aggregate pays for the steward skill from the untangle skill's removed
   lines, any rest recorded as a pin raise inside the aggregate total, as #5508,
   #5593 and #5605 did. Gates: `validate_skills.py`, `test_skill_mirrors.py`,
   `check_context_budget.py` and `test_rulebook_ratchet.py`,
   `scripts/tests/test_review_check.py` (the prose case flips: a `Test-Removal:`
   line), `check_shapes.py`, `tests/test_live_docs_reference_real_scripts.py`,
   a grammar test of the policy and classifier files, `--untangle-check`. The
   host briefs shrink to mechanics in the same hour.
2. **`process/ship-code-review`** (after 1). Adds `scripts/review_result.py`
   (format, parse and post one review: inline comments, body, the result line,
   `COMMENT` event on the commit read) and its tests; rewrites the peer-agents
   skill's reviewer section to the find/verify/rank/post procedure (parallel
   finders, one verifier, REVIEW.md and CLAUDE.md read at `origin/main`, one
   review per head, its own fixed threads resolved, a kept result only while the
   change is the same and no Important thread was resolved since); the verdict's
   first line stays for `review.yml` until enforce. The host's `land_pr2.sh` and
   receipts are deleted. Gates: pytest of the module (a result line
   round-trips; a head mismatch, a missing line and a malformed one fail
   loudly), `validate_skills.py`, `--untangle-check`.
3. **`process/ship-steward`** (after 1). Adds `scripts/steward_watch.py` (what a
   PR needs, read only: failing checks with the PR's touched files, conflicts,
   open Important threads, unanswered review comments, and when the watch ends:
   merge, close, the cap, or days with no new head, review or comment) and its
   tests; the steward skill's watch procedure (Ship's defaults: fix in touched
   code, merge the base, re-run a flaky job once, reply on each thread, the
   opt-out marker and the `steward-watching` label). Manual first: the
   coordinator starts a steward per PR; On once a week of PRs shows its fixes
   are the reviewers'. Gates: pytest of the module against recorded API shapes,
   `validate_skills.py`, `--untangle-check`.
4. **`process/ship-approvals`** (after 1 and 2; observe after 3 is on Manual).
   Adds `scripts/approvals.py` (the grammar subset, refusing every key and fact
   it does not implement; Ship's glob rules and built-in protected list; the
   classifiers from `review_result.py`; the `ta/founder_approved` fact;
   combining; the check, the one comment, the approving review, the stamp), its
   tests and `.github/workflows/approvals.yml`; `review-signal.yml` and
   `review-queue.yml` also wake it; `pr-scope-guard.yml` reads its
   release-critical paths from the policy's gate rule. The founder grants the
   review App pull-requests write in GitHub's UI (a host-actions row). The
   scratch-ruleset proof above runs in its observe period. Gates: pytest
   (refusals, globs, built-in files and the never-approve cases, strictest-wins,
   overrides in order, `blocks` without a passing review, `/approve` with a
   failing review approving nothing, observe neutral; the fact false for
   another login, an account without write access, an edited comment, a stale
   head and a comment before a base change; the stamp), actionlint,
   `ci_structural_guards.py`, the `environment`-guard tests, `--untangle-check`.
   Its own change is a gate file, so the founder's `/approve` on its PR is read
   by hand by its reviewer.
5. **`process/ship-enforce`** (after 4's observe period and the scratch proof),
   in Ship's four steps, each a merged file change and then its `PUT` (Fact 4),
   since `auto-enroll` arms nothing while the live rules differ from the file:
   (1) merge the deletion of `mode: observe`; (2) rerun `approvals` on every open
   PR (`/approvals rerun`), holding each out of the queue until its check is an
   enforced one; (3) require `approvals` from the review App, beside `review`;
   (4) add the `pull_request` rule (one approval, dismiss stale on push) with
   `non_fast_forward` and `deletion`, the approval rule last as Ship says. Then
   the contraction, one change: `review` leaves the ruleset; `review.yml` and
   `review_checks.py`'s verdict, `carries` and check-run code go (the Untangle
   check and `last_base_change` stay); `review-signal.yml` and `review-queue.yml`
   stay as the approvals job's signals; a new ADR supersedes ADR-038; the
   openspec delivery contract's "A review verdict is a check run only the review
   App posts", the gates page's landing paragraph and the peer-agents verdict
   line are rewritten. Gates: the full CI, `ruleset_drift.py` after each `PUT`,
   and one PR merged under each step.

## Founder decisions

Asked 2026-11-24; 1 to 4 answered that day, 5 on 2026-11-28; 6 is put to him
after #5856's review.

1. **Who needs a person: by judgment, not paths.** "I should be consulted when
   design frame or shape or project drift or feedback is needed." Modelled as
   the `founder_consult` classifier, read in an override, its question asked
   once on the PR and relayed by the coordinator.
2. **Keep the founder's account for agents; no machine user or App.**
3. **Reviews and fixes stay in Claude sessions on the founder's subscription;
   no API key and no CI-hosted review or steward.** Only the deterministic
   approvals evaluation and the review App's check and approval run in Actions.
   The Direction 8 gap is recorded above as his choice.
4. **Branch rules at enforce: yes** (one approval, dismiss stale on push,
   `approvals` required from the review App). Taking agents off admin is ruled
   out by decision 2.
5. **"Accept the gap."** Asked: under decision 2 no comment, label or review by
   the founder's login can be told from an agent's, since agents write as him by
   paths with no App marker. He keeps one account. So his consultation, and the
   gate rule, are honoured by agents but not enforced: `/approve` keeps Ship's
   login, write-access, unedited and current-head tests and needs a passing
   review, and any agent can still produce it. A broken or misled agent could
   approve in his name; that is his accepted risk. Agents' own rule is never to
   post one (the steward skill, REVIEW.md).
6. **To confirm: a path rule for the gate files.** Answer 1 says judgment, not
   paths; a classifier can be steered by the PR's own text, so Ship asks for a
   person on paths that matter in a rule that reads no classifier. **Recommend
   the list above** (40% of November's PRs), or without the two deploy
   directories (29%). Without it, the classifier decides those files too.

## This PR

Only this file. The policy, classifiers, `REVIEW.md` and steward skill are
`process/ship-rules`'s.
