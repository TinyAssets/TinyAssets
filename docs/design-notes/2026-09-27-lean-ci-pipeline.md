# Lean CI pipeline: measured baseline and target (2026-09-27)

Founder, 2026-09-27: "required tests seem long still"; the review should "cover
end to end the development iteration cycle", and a "complete overhaul clean lean
pipeline" is acceptable. Research input: industry practice gathered the same
night, summarised under Evidence below.

## The finding that orders everything: runners, not tests

The repo is **user-owned** (`owner.type = User`) and public. GitHub-hosted jobs
are capped at about 20 concurrent. At 01:52 UTC, 17 jobs were in progress and
30 runs were queued. Tonight's estimated runner-minutes, 21:56–01:52 UTC,
sampled from 12 runs per workflow and event (method:
`gh api .../runs/<id>/jobs`, sum of job durations):

| Workflow / event | runs | min/run (median) | est. total | note |
|---|---|---|---|---|
| Desktop release / pull_request | 60 | 31.8 | **1,910** | 6-platform installer matrix on every PR that touches `tinyassets/**`; not required |
| Tests / push (main) | 18 | 41.4 | **745** | runs the red-at-baseline `heavy-tests` on every merge; 15/15 failed |
| Tests / pull_request | 86 | 8.4 | 727 | 37 of 86 cancelled as superseded, so cancellation already works |
| Docker build smoke / push | 68 | 3.8 | 257 | `push:` has **no branch filter**, so every agent-branch push builds, on top of the PR run |
| Docker build smoke / pull_request | 61 | 4.4 | 270 | |
| Desktop release / push | 10 | 14.7 | 147 | |
| everything else | | | ~250 | |

That is about 4,300 of the roughly 4,800 runner-minutes a 20-slot cap allows in
4 hours. **Every stage below waits in this queue.** The deploy is the clearest
case. `967b7c29` merged at 01:23 and its image was built by 01:34. The
`deploy` job then sat *queued for a runner* until 01:56, **33 min from merge to
live**, 22 of them waiting behind PR CI.

## Stage table (tonight: 22 PRs merged 20:23–01:55 UTC)

| Stage | median / p90 | Root cause | Fix (practice) | Expected saving |
|---|---|---|---|---|
| 1. dispatch → PR opened | not measurable from GitHub (dispatch time is not recorded) | PRs are large: median **1,003 changed lines**, 13 files; p90 3,597 | small PRs, opened early (research 7: conflict odds roughly triple above 25 lines) | process, not CI |
| 2. PR open → merge | **55 / 210 min** | serial `required-tests` 19–22 min, plus 5 min median runner queue (18+ at peak) | cut runner waste (below), then shard (research 3) | queue ~0; gate 22 → ~6 min |
| 3. review / receipts | 74 commits over 22 PRs (3 median per PR); 13 scope-guard failures | every push voids an exact-head receipt | diff-keyed receipts via `git patch-id` (research 6) | a rebase or merge-main no longer forces a restamp |
| 4. conflicts | **30 merge-main commits across 15 of 22 PRs** | large PRs; shared hand-edited index files | generated `docs/concerns/README.md` (research 2); small PRs | each avoided merge-main saves one full CI round of ~50 runner-min |
| 5. merge → live | ~16 min typical, **33 max** tonight | the deploy job waits for a runner. Back-to-back merges cancel in-flight *builds*, but nothing is lost: `decide` judges the whole served..head range | **deploy coalescing is already in place** (`production-host-mutation`, `cancel-in-progress: false`). The fix is runner load | 22 → ~0 min of queue |
| 6. deploy → verified | < 1 min | canary on `workflow_run`; `deployed_sha.py` | keep | none |

On research item 1: it is already implemented. Setting `build-image` to
`cancel-in-progress: false` would *add* a deploy, meaning two container
recreates and two rounds of killed turns per burst, and it would not shorten
anything. Leave it as it is.

## Target pipeline

| Trigger | Runs | Budget |
|---|---|---|
| **pull_request** | scope guard + auto-enroll, `invariants`, `actionlint` (path-filtered), preview-security, and `required-tests` sharded 6 ways. Path-filtered smokes stay only where the PR changes their inputs: docker, bundle, preview, reconcile regression, layer-2 regression, linux-jail-proof | < 8 min wall, about 25 runner-min |
| **merge to main** (`push`) | build-image → deploy-prod → install-host-services → canary / `deployed_sha` (unchanged, Hard Rules 11/14); `required-tests` as tripwire; desktop, android and bundle artifacts | coalesced |
| **schedule** | `heavy-tests` (hourly), uptime / dns canaries, release-reconcile, tier-3 clone nightly, secrets expiry, branch janitor | off the PR path |
| **manual** | release / ops / recovery workflows (no runner cost at rest) | – |

**With a merge queue** (research 5): this needs an organisation-owned repo, so
it is a founder action. The PR runs a fast, impact-selected gate
(research 4). The full sharded suite runs once per batch on `merge_group`, with
at most 5 per batch. Without an organisation, the sharded full suite stays on
the PR. That is the correct fallback, and nothing else in this design depends
on the move.

## Workflow inventory (44)

**Keep, PR path:** `pr-scope-guard`, `auto-enroll-merge`, `invariants`,
`tests` (sharded), `preview-security`, `preview-worker` + `preview-worker-deploy`,
`actionlint`, `linux-jail-proof`, `release-reconcile-regression`,
`uptime-layer2-regression`, `build-bundle`.
**Change:** `desktop-release` drops `pull_request` (keep main push, tag and
dispatch). `docker-build` limits `push` to `main`, or removes it, since
`build-image` already builds on main. `tests` / `heavy-tests` becomes
schedule + dispatch only. `android-build` / `ios-build` drop `pull_request`
too. The saving is small (they are path-filtered to `mobile/**` and ran 0
times tonight); the rule is "no platform builds on PRs", and a native-shell PR
can dispatch them. `android-release` keeps its PR run: it is the fail-closed
release-gate check (`tests/test_android_release_pipeline.py`).
**Keep, deploy chain (Hard Rules 11/14):** `build-image`, `deploy-prod`,
`install-host-services`, `release-reconcile`, `uptime-canary`, `deploy-worker`,
`dns-canary`, `p0-outage-triage`.
**Keep, scheduled:** `tier3-oss-clone-nightly`, `secrets-expiry-check`,
`branch-janitor`.
**Investigate → merge or delete:** `community-loop-watch` failed 21 of 23 runs
tonight, and its signals overlap `uptime-canary` + `release-reconcile`.
**Deleted (one-shot, done):** `site-dns-cutover` and its only caller's script `scripts/site_apex_cutover.py`; last run 2026-04-29.
**Keep, manual (zero cost at rest):** `android-release`, `ios-release`,
`app-store-review-account`, `apply-daemon-env`, `restart-daemon`,
`diagnose-prod-startup`, `droplet-resize`, `dr-drill`, `emergency-dns`,
`recovery-retag-image`, `deploy-site-react`, `cloud-only-preflight`,
`cloud-prepush-oracle`, `quarantine-oracle`, `pushover-test`.

"Last caught something real" was not measured per workflow. The deletions
above rest only on trigger shape and tonight's results.

## Required checks

Now: `Diff scope declared`, `required-tests`, `invariants`, `slow-tests`;
`strict` off. **After steps M1–M4: unchanged.** `required-tests` keeps its
name as the shard aggregate. **With a merge queue (M5):** enable "Require
merge queue" (merge method squash, max batch 5, build concurrency 5). Every
required workflow adds a `merge_group:` trigger so the same four contexts
report on the batch. `required-tests` on `pull_request` then becomes the
impact-selected run.

## Migration (each step leaves the gates intact)

- **M1** Shard `required-tests` (PR #4046: aggregate fails closed on a
  missing, duplicate, truncated or failed shard). No protection change.
- **M2a** (this note's PR) `desktop-release`, `android-build` and
  `ios-build` drop `pull_request`, and `docker-build` `push` is limited to
  `main`. Pinned by
  `tests/test_ci_runner_budget.py`.
- **M2b** (after M1, same file) `heavy-tests` becomes schedule-only. No required check involved, so this
  deletes nothing a gate depends on. Saving: about 2,300 of 4,300
  runner-min per 4 h.
- **M3** Generated concerns README + drift check.
- **M4** Diff-keyed receipts in `drain_review_gate.py`.
- **M5** (founder) Move the repo to an organisation → merge queue, then
  impact-selected PR tests with an always-run safety set and a full-suite
  fallback for conftest, config or unknown files.
- **M6** Fold `slow-tests` into the aggregate (a protection change),
  delete `site-dns-cutover` (done), and resolve `community-loop-watch` (done:
  it was correctly reporting stale P0 #2824, which could never auto-close;
  #2824 closed and the cause filed as a concern).

## Evidence

AgenticFlict (arXiv 2604.03551): 27.7% of agent PRs conflict, about 10% at
~2 lines against 30–33% at 25+. arXiv 2607.04697: concurrent agent PRs
conflict 41.7% of the time. DORA 2025 / Faros: AI raises throughput and
instability together. Stripe Minions: 1,300+ PRs a week with a 2-CI-round cap.
GitHub documentation covers `merge_group`, concurrency and dismissing stale
approvals.

## 2026-10-01: merge-queue baseline, before PR-time affected tests

Founder, 2026-10-01: "an hour of tests for each pr seems like a process that
has been allowed to bloat". The lean-CI follow-ups are #4201 (PR-time affected
tests), #4203 (jail-proof marker), #4205 (brand badge check) and #4206 (shard
packing). The numbers below are the **before** line. Re-run the same queries
about a week after #4201 lands and compare.

**Window:** 2026-09-27 20:40Z to 2026-10-01 20:40Z, measured on 2026-10-01.

| Measure | Before |
|---|---|
| PRs enqueued at least once | 122 |
| PRs dropped from the queue at least once | 32 |
| Queue drops (not counting `merged`) | 65: **39 `failed_checks`**, 20 `merge_conflict`, 6 `manual` |
| First enqueue to merged (n=110) | median **10 min**, p90 91, max 895 |
| `merge_group` Tests runs | 212, of which **87 failed** (41%) |
| Shard wall time, max/median within a run (57 runs) | median 1.23x, p90 2.14x |

**Where `failed_checks` comes from.** These are the test files named in the
failed merge-group runs' new-failure lists:

- **Repo-wide ratchets and boundary scans dominate:**
  - `test_channel_agnostic_ratchet` 34, half of it from
    `test_the_baseline_matches_what_is_actually_there`, an exact-match committed
    count;
  - `test_served_tool_guidance` 29;
  - `test_source_channel_policy_is_gone` 16;
  - `test_owner_door_import_boundary` 14;
  - `test_storage_registry_complete` 14.
- **Flakes:** `test_execution_evidence_store` 11 (the fd-leak test).
- **Batch conflicts.** An exact-match baseline fails whenever two PRs in one
  batch each move the count. No per-PR test can see that conflict, which is
  the duplicated-truth shape again.

**Will PR-time selection cover them?** Over the last 60 main commits,
`scripts/affected_tests.py` selected each of those files (or ALL) in 25 to 52
of the 60. Most single-PR failures should therefore show up on the PR. Batch
conflicts will not.

**Queries.**
- Drop reasons and times: GraphQL `pullRequests { timelineItems(itemTypes:
  [ADDED_TO_MERGE_QUEUE_EVENT, REMOVED_FROM_MERGE_QUEUE_EVENT]) }`, reading
  `reason` and `createdAt`. The proxy for "stamp" is the first enqueue.
- Failing tests: `gh run list --workflow tests.yml --event merge_group`, then
  `gh run view <id> --log-failed`, then the ``- `tests/...` `` lines.

## M2c, 2026-10-03: heavy `pull_request` workflows skip drafts

Prompted by a merge-group `Tests` run queued 20+ minutes behind 30 PR runs.
Five heavy, non-required `pull_request` workflows now skip while the PR is a
draft, and re-run on `ready_for_review`:

| Workflow | Job | Median / run | Total of last 100 completed PR runs |
|---|---|---|---|
| Docker build smoke | `build-smoke` | 11 min | 81 min |
| preview-security | `contract` | 6 min | 77 min |
| Build packaging artifacts | `stage-and-probe` | 11 min | 72 min |
| linux-jail-proof | `linux-jail-proof` | 10 min | 66 min |
| real-browser-proof | `real-browser-proof` | 10 min | 15 min |

Those five are 311 of the 436 `pull_request` runner-minutes in that window
(71%). Pinned by `tests/test_ci_runner_budget.py`, both halves: the condition
and the `ready_for_review` type that makes the skip recoverable instead of
sticky (it is not a default type, so without it a PR marked ready keeps the
draft run's skip until its next push).

**Measure this before expecting it to fix the queue.** Of those 436 minutes,
only **62 (14%)** were on branches whose PR is currently a draft — 12 of 40
open PRs. The draft cut is cheap and correct, but the dominant cost is 40 open
PRs each running five heavy workflows on every push, not drafts specifically.
The lower bound caveat: "currently a draft" misses runs that were drafts then
and are ready now, so 14% understates it by an unmeasured amount. The next cut
should target ready PRs — path-filter width, or per-PR concurrency on
`preview-security` and `build-bundle`, which have no `concurrency:` block at
all and so do not cancel superseded runs.

**Why the required checks were left alone, and must stay that way.** A job
skipped by an `if:` reports `conclusion=skipped`, and branch protection accepts
a skipped required check as SATISFIED — verified on this repo on PR #2197 and
written up at length in `tests.yml`. So a draft condition on `required-tests`,
`slow-tests`, `invariants` or `Diff scope declared` fails OPEN: any edge case
where the condition is wrong merges untested code silently, and auto-enrol arms
auto-merge on every non-draft PR. `tests/test_ci_runner_budget.py` pins that
boundary from both sides — the five above carry the condition, the required
four do not. There was also nothing to win there: `tests.yml` already gates its
heavy jobs on `github.event_name != 'pull_request'`, so a PR run is only
`affected-tests` plus the aggregate, median 3 minutes.
