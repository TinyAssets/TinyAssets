# CI secrets as built (2026-11-14)

This fixes the CI part of the P0 concern `docs/concerns/2026-07-27-public-site-privacy-deps-ci.md`: any same-repo branch pull request could read 13 repository-level secrets, `CLOUDFLARE_API_TOKEN` among them, and two workflows handed secrets to the pull request's own code.

## Research (Fact 14)

`docs.github.com` was not reachable from this session, so the pages were read from GitHub's docs source, `github/docs` at `d59b2631` (2026-11-13):

- `content/actions/reference/workflows-and-actions/deployments-and-environments.md`
- `content/actions/reference/workflows-and-actions/events-that-trigger-workflows.md`
- `content/actions/reference/security/secrets.md` and `securely-using-pull_request_target.md`
- `content/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments.md`

What they say:

1. **Repository secrets reach any same-repo run.**
   - A `pull_request` run executes the workflow files of the PR's merge commit, with `GITHUB_REF` = `refs/pull/N/merge`.
   - Only a fork's run is denied secrets: "With the exception of `GITHUB_TOKEN`, secrets are not passed to the runner when a workflow is triggered from a forked repository." A fork's `GITHUB_TOKEN` is read-only.
   - "Any user with write access to your repository has read access to all secrets configured in your repository" (`secure-use`).
   - So a branch can add or edit a workflow, and an `if:` in it guards nothing.
2. **Environment secrets reach only a job that names the environment, and only if the environment's rules let that job run.**
   - "Secrets stored in an environment are only available to workflow jobs that reference the environment. If the environment requires approval, a job cannot access environment secrets until one of the required reviewers approves it."
   - A deployment-branch rule "is matched against the `GITHUB_REF` of the workflow run". Only "Adding another branch rule for `refs/pull/*/merge`" would admit `pull_request` runs.
   - So a PR that names a main-only environment is refused, and so is a dispatch on a branch.
3. **Precedence.** When a repository secret and an environment secret share a name, "the environment-level secret takes precedence". A job that names the environment also still sees repository secrets the environment lacks. So the workflows can name the environment now, and the founder's move needs no workflow change.
4. **A missing environment.** Running a workflow that names an environment that does not exist "will create an environment with the referenced name", with no protection rules or secrets. So this change names only the three existing environments (`production`, `review`, `app-store`), and no new `edge` environment.
5. **`pull_request_target`.**
   - On github.com it runs "in the context of the default branch of the base repository": the YAML is main's, `GITHUB_REF` is the default branch, and it gets the repository's secrets and a write-capable token, for forks too. So it can name a main-only environment (`review.yml` already does).
   - The risk is running the PR's code. The docs name these shapes: a checkout of `github.event.pull_request.head.sha`, of `refs/pull/N/merge` or of the head repository; `gh pr checkout`; or `git fetch` and then running what was fetched.
6. **`workflow_run`** also runs the default branch with secrets. Its risk is the same as 5, through artifacts downloaded from a PR's run. No workflow here runs code from those artifacts (`review.yml` reads only data from them), and it is out of this guard's scope.

The repo-side fix follows. A branch can always write a new job, so the only real wall is GitHub refusing it. So no secret that can act outside GitHub may stay at repository scope (the founder's step). Every workflow reads such a secret only in a job that names a main-only environment. And the pull-request-time jobs read none.

## Decisions

1. **One allow-list, the harmless secrets.** Five names stay readable without an environment:
   - `GITHUB_TOKEN`: it can do in the repository no more than the pusher whose branch ran it, and a fork's run gets it read-only;
   - `TINYASSETS_WIKI_CANARY_TOKEN`: the read-only canary principal;
   - `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_ZONE_ID`: identifiers;
   - `TOKEN_HEX`: Python's `secrets.token_hex`.

   The guard's old `NON_PRODUCTION_SECRETS` also exempted every token that "cannot reach the host". The host is not the only production. That exemption is gone: the Cloudflare token, the merge PAT, Pushover and the Android keys now need `production`, and the Apple ones need `app-store`.
2. **The pull-request guard is the production guard plus one rule.** `platform/ci/guards/environment.test.ts` already failed any job that reads a non-allowed secret outside its environment, whatever its trigger. With the allow-list cut to harmless names, that test is the `pull_request` guard. A job that names a main-only environment is refused on the PR's ref, and no environment lets a PR run in. The one new rule is for `pull_request_target`: a job there that holds a secret checks out none of the PR's code. A second allow-list for pull requests would copy the first.
3. **`deploy-worker.yml`** is split in two. `check` runs on every event with no secret. `deploy` names `production` and needs `check`. `wrangler deploy --dry-run` needs no credential: run with wrangler 4.154, no token, no login and an empty home directory, it bundled the Worker and exited 0. The workflow is deleted at the cutover (d3 §J1).
4. **`android-release.yml`** gets one build, `.github/actions/android-release-bundle`, called by two jobs. `check` runs on pull requests with no Firebase config: the materialise script builds with push off and says so. `release` runs on a dispatch on main, names `production`, passes the config in, then signs.
   - The `mobile-v*` tag trigger goes. A main-only environment refuses tag refs, no such tag exists, and the documented route is a dispatch on main (`docs/host-actions.md`). `--expected-version`, its only consumer, goes with it.
   - The "refuse to sign outside main history" step goes too: the environment's branch rule now enforces it, and a branch's copy of the step could be deleted anyway.
5. **`android-build.yml`** names `production` for the debug keystore. The `feat/android-app` trigger goes (no such branch), and so does the step's main-only `if:`. A branch dispatch is now refused; a PR gets the release check's unsigned build.
6. **The other readers name `production`:**
   - `emergency-dns`, `store-probe`, `secrets-expiry-check` and the `platform-deploy` plan job, for the Cloudflare token. The deploy kit still holds the plan job to that token alone.
   - `auto-enroll-merge`, `enqueue-merge` and `okf-watch`, for the merge PAT. They run from main through `pull_request_target`, `workflow_run` and `schedule`.
   - `okf-watch`, `pushover-test` and `uptime-canary`'s `alarm-sink`, for Pushover.

   Cost: each `auto-enroll-merge` run now records a `production` deployment, as `review.yml` does for `review`. Nothing reads deployment records.
