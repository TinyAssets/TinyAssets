# ADR-004: Merge Attribution Gates the Whole Deploy Chain

## Status

Proposed — **the fix is a host action**: it needs a credential only the repo
owner can mint. Everything up to that point is measured and written down here
so the decision takes minutes, not investigation. See "What the host must do".

## Date

2026-08-04

## Context

Two problems were treated as separate for weeks. They are the same problem.

**Problem 1 — merged is not deployed.** `build-image.yml` triggers on
`push: main`. Auto-merged PRs did not produce builds, so merges sat undeployed
until someone noticed and ran a manual `workflow_dispatch`. AGENTS.md hard rule
14 records the symptom and the five-PR incident of 2026-07-21.

**Problem 2 — PRs stall at `BEHIND`.** `main` requires branches to be current
(`strict: true`), and GitHub's auto-merge does **not** update them. With
several providers landing PRs concurrently, a PR goes `BEHIND` faster than its
checks finish and then waits for a human. On 2026-08-04 two PRs needed two
manual re-syncs each.

### The measurement

A controlled pair, an hour apart on the same repository:

| PR | merged by | `build-image` result |
|---|---|---|
| [#2259](https://github.com/Jonnyton/TinyAssets/pull/2259) | `app/github-actions` | **no run**; sha `9879311f` built only by a later manual dispatch |
| [#2260](https://github.com/Jonnyton/TinyAssets/pull/2260) | `Jonnyton` (human) | **`event=push`, automatic**; sha `3b874d65` built and deployed unattended |

Same workflow, same path filters, same branch. The only difference is **who the
merge was attributed to**.

This also resolved the open production gap by accident: `8e93a99b` (PR #2253,
the ledger-attribution fix) is an ancestor of `3b874d65`, so the human-attributed
merge carried it into production at 18:55:53Z — no dispatch required.

### Why the obvious automation does not work

The natural fix for problem 2 is a scheduled workflow calling `update-branch`.
With the default `GITHUB_TOKEN` that makes things **worse**, and the reason is
narrower than the folklore. GitHub's actual behavior for an event caused by the
default token:

* `pull_request` `opened` / `synchronize` / `reopened` **do** create runs — but
  they land in an **approval-required** state.
* `push`, `pull_request_target`, and other `pull_request` activity types create
  **no run at all**.

So such an updater wedges a PR twice over: `required-tests` (`tests.yml`,
`pull_request: synchronize`) parks awaiting a human approval, and
`Diff scope declared` (`pull_request_target`) never runs. Required checks must
pass for the **latest** sha, so nothing carries over. Two manual interventions
where there had been one.

The generalized form of hard rule 14 — "`GITHUB_TOKEN` events never start
workflows" — is inaccurate and should not be relied on for design. The rule is
correct about its operational subject: app-attributed merges do not trigger
`push: main` deploys.

## Decision

**Both problems are one root cause: the default `GITHUB_TOKEN` cannot originate
events that drive this repo's automation.** Fix the credential, not the two
symptoms.

Options, ranked, with what each costs:

1. **Dedicated GitHub App installation token** — best fit. Keep `strict`, keep
   the current workflows. Mint a token from a separately installed App and use
   it for merge enrollment and for `update-branch`. Costs app registration,
   private-key handling, and rotation. Requires the owner to create/install the
   App with Pull Requests: write and Contents: write.
2. **Fine-grained PAT** — fastest correction. Scope to this repo, Pull
   Requests: write, store as an Actions secret. Costs identity coupling and
   expiry/rotation; higher operational credential risk than an App.
3. **Merge queue** — the best long-term model for a high-churn protected
   branch, and what GitHub recommends for exactly this. **Not available here**:
   merge queues need an organization-owned repository and `Jonnyton/TinyAssets`
   is personal-account owned. Adopting it means transferring the repo, changing
   protection, and adapting all three required checks for `merge_group` —
   `policy` and `Diff scope declared` currently read PR-specific payloads.
   *Adopted 2026-09-27:* the repo moved to the `TinyAssets` org and the
   required checks gained `merge_group` triggers. Measured on the first queue
   merge: the queue's push to `main` is attributed to
   `github-merge-queue[bot]` and DOES trigger `build-image` → `deploy-prod`.
   So with the queue on, the deploy gap this ADR describes is closed by the
   queue itself. A user identity (the PAT) is needed only to *enter* the queue,
   because an auto-merge armed by `GITHUB_TOKEN` never enqueues. Evidence is in
   `docs/ops/org-transfer-runbook.md`.
4. **Drop `strict`** — removes the re-sync race with a one-line protection
   change, but lets checks pass against a stale base so incompatible PR
   combinations can merge. On an automated high-frequency merge path that
   weakens a load-bearing guarantee. Last resort.

## What the host must do

Smallest ask, option 2 (option 1 is the same shape with an App):

1. Create a fine-grained PAT scoped to `Jonnyton/TinyAssets` with
   **Pull requests: write** and **Contents: write**.
2. Add it as an Actions secret.
3. Point `auto-enroll-merge.yml` at that secret. It is exactly one line —
   `.github/workflows/auto-enroll-merge.yml:58`:

   ```diff
   -          GH_TOKEN: ${{ github.token }}
   +          GH_TOKEN: ${{ secrets.MERGE_ATTRIBUTION_TOKEN || github.token }}
   ```

   The `||` fallback means this edit is a **no-op until the secret exists**,
   so it can land before step 1 without changing behavior. An unset secret is
   an empty string, which is falsy, so enrollment keeps using the default
   token exactly as it does today.

### Verifying it worked

Do NOT infer success from the merge. Merge one PR through auto-merge, then:

```bash
gh pr view <n> --json mergedBy --jq .mergedBy.login     # expect the PAT/App identity, not app/github-actions
gh run list --workflow build-image.yml --limit 1   --json event,headSha --jq '.[0]'                       # expect event=push on the merge sha
```

Then confirm production actually moved — `get_status` -> `release_state.git_sha`
must contain the commit (hard rule 14).

After step 3, auto-merged PRs raise a real `push` on `main`, `build-image`
fires, and `deploy-prod` chains from it — the whole "merged is not deployed"
class closes, and a future `update-branch` automation becomes viable.

## Consequences

- Until this lands, **every merge needs either a human merger or a manual
  `gh workflow run build-image.yml --ref main`** to reach production. Verify
  with `get_status` → `release_state.git_sha`, never with the merge itself
  (hard rule 14).
- `scripts/pr_sync_behind.py` covers problem 2 in the meantime. It runs on a
  *user's* credentials, which is why it works — verified 2026-08-04 on PR
  #2263: the head moved and `Tests event=pull_request` queued against the new
  sha. It is a stopgap, not the fix.
- The broad restatement of hard rule 14 in workflow comments should be narrowed
  to the accurate mechanism above.

## References

- AGENTS.md hard rule 14 ("Merged is not deployed")
- `2026-04-20-public-mcp-outage-postmortem.md (archived in git)` (the post-change probe rule)
- [`GITHUB_TOKEN` and workflow runs](https://docs.github.com/en/actions/concepts/security/github_token#when-github_token-triggers-workflow-runs)
- [Update a pull request branch](https://docs.github.com/en/rest/pulls/pulls#update-a-pull-request-branch)
- [Required checks and the latest sha](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks#required-check-needs-to-succeed-against-the-latest-commit-sha)
