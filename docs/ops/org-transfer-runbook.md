# Org transfer and merge queue cutover

Moves `Jonnyton/TinyAssets` to `TinyAssets/TinyAssets` so `main` can use
GitHub's merge queue, which personal-account repositories don't get. Founder
decision, 2026-09-27. Run the steps in order. Each step says who does it: **F**
is the founder (account or dashboard access), **L** is the lead (a `gh` CLI
session with admin on the repo).

The code side is three PRs, each with its own `Lane:`:

| PR | Contents | When it merges |
|---|---|---|
| **A** `merge_group` triggers | The four required checks, this runbook, and the ruleset | **Before the transfer.** The triggers do nothing until a queue exists. |
| **B** cutover (#4057) | Every live `Jonnyton/TinyAssets` reference and the `ghcr.io/tinyassets` image path, including `dr-drill.yml` and `deploy/` config | **Step 6**, after the transfer. The hard-coded image consumers would otherwise name a package that doesn't exist yet. |
| **C** unit links + ratchet | `Documentation=` URLs in 7 systemd units, plus the test that keeps the old slug out | **After B.** Optional timing, since the URLs redirect. Changing a unit makes the next host install rewrite it. |

## What moves and what doesn't

Checked 2026-09-26 against GitHub docs and the live repo (`gh api`):

| Thing | After the transfer |
|---|---|
| Issues, PRs, stars, wiki, 21 repo secrets, 4 variables, the environments `app-store` / `github-pages` / `react-preview` and their secrets | Move with the repo ("secrets ... will remain associated", *Transferring a repository*). No webhooks or deploy keys exist. |
| Git and web URLs, including `raw.githubusercontent.com` and release downloads | Redirect permanently. The old name is also retired, so nobody can squat it and break the redirect: GitHub retires `OWNER/REPO` for repos with over 100 clones or Actions uses in the prior week. |
| Branch protection on `main` | Moves (repo setting). |
| `ghcr.io/jonnyton/tinyassets-daemon` | **Does not move.** Container packages stay with the account, and "GitHub Actions workflows associated with the repository will lose access to the package" (*About permissions for GitHub Packages*). It's public, so the droplet can still pull every old digest, which keeps rollback targets valid. |
| New images | `build-image.yml` and `deploy-prod.yml` derive the owner from `github.repository_owner`, so they switch to `ghcr.io/tinyassets/tinyassets-daemon` **at the moment of transfer, without this PR**. A new package is private by default ("When you first publish a package, the default visibility is private"). The droplet pulls anonymously, so a deploy fails at `docker pull` until the package is public. `deploy_fail_safe.sh` refuses before it touches prod ("failed to pull ...; prod untouched"). |
| `MERGE_ATTRIBUTION_TOKEN` | **Stops working.** It's a fine-grained PAT whose resource owner is the `Jonnyton` account, so it can't reach an org repo. Auto-enrollment then fails with "Could not enrol" (non-fatal, so it's quiet) and nothing merges itself. It also expires 2026-11-02 anyway. |
| `gh` CLI and other OAuth-app tokens (`gho_...`), including every agent session and the droplet's `GH_TOKEN` | **Lose write access** unless the org allows them. "When you create a new organization, OAuth app access restrictions are enabled by default." Unapproved apps get "no privileged create, update, or delete actions on public organization resources". That blocks agent pushes and PRs, and the droplet watchdog's issue alerts. |
| GitHub App installations on the account (at least `cursor`, from check suites; also check Claude and ChatGPT/Codex connectors) | Cover the account's repos only. Reinstall on the org. |
| GitHub Pages site `tinyassets.io` (build type `workflow`, CNAME `tinyassets.io`) | Custom domain is a repo setting and moves. The domain is **not** verified for any account (no `_github-pages-challenge-*` TXT record), so it isn't locked to `Jonnyton`. `www` CNAMEs to `jonnyton.github.io` through Cloudflare; retarget it to `tinyassets.github.io`. |
| MCP registry entry `io.github.Jonnyton/tinyassets-universe-server` | Unchanged. The name is the publisher's namespace, not the repo path. Republishing needs `mcp-publisher login github` as `Jonnyton`; a GitHub OIDC login from Actions would get the `io.github.TinyAssets` namespace instead. |
| claude.ai connector, WorkOS, Play / App Store listings | Point at `tinyassets.io`, not GitHub, so they're unaffected. Any store-listing field that links `github.com/Jonnyton/TinyAssets` keeps working through the redirect. |
| Stored universe grants naming `github_pr:Jonnyton/TinyAssets` | Keep working for writes to the old name through the redirect. A grant check compares strings, so a graph that names the new slug needs its own grant. |

## Does a merge-queue merge trigger `push` workflows?

**Yes. Measured 2026-09-27 on the first queue merge (step 11).** The deploy
chain is `push: main` → `build-image` → `deploy-prod` (`workflow_run`).

- The queue's push to `main` is attributed to `github-merge-queue[bot]`, and it
  starts push workflows. That is unlike `GITHUB_TOKEN`, whose events "do not
  create workflow runs at all" (*Events that trigger workflows*).
- #4062, #4059 and #4067 merged as one batch. The queue fast-forwarded `main`
  to the merge-group commit (#4062's merge sha, `86dd375b`, was its group
  commit) and raised two push events. On `5364c2f8`:
  - Build and publish image (run 36302492671, `event=push`) decided `build`.
    Its served..head range covered the whole batch.
  - `deploy-prod` (run 36302831420, triggered by `github-merge-queue[bot]`)
    shipped. The receipt check printed "production reports 5364c2f8a901,
    which contains 5364c2f8a901d57757f2561ec2244430a8a9df58".
- **A user identity is needed only to ENTER the queue.** An auto-merge armed
  by `GITHUB_TOKEN` never enqueues. On 2026-09-27, #4059 and #4062 sat CLEAN,
  with every check green, and the queue stayed empty until a user enqueued
  them. `auto-enroll-merge.yml` therefore enrolls with `MERGE_ATTRIBUTION_TOKEN`
  only. It fails loudly if that is empty or if the enroller isn't a `User`
  (#4069).
- If a queue merge ever shows no `event=push` run, `release-reconcile.yml`
  redeploys undeployed `main` on its 15-minute schedule. The immediate fix is
  `gh workflow run build-image.yml --repo TinyAssets/TinyAssets --ref main`.

## Why `Diff scope declared` passes through in the queue

It reads the PR's file list, labels, body and receipt comment, and none of
those exist in a `merge_group` payload. On `merge_group` it reports a pass
without re-running the gate:

1. "Once a pull request has passed all required branch protection checks, a
   user with write access to the repository can add the pull request to the
   queue" (*Managing a merge queue*). The gate already passed at the PR's head.
2. Grouping changes the code under test, not the PR's metadata.
   `required-tests` and `invariants` re-run on the group commit.
3. A `merge_group` run takes the workflow file from the group commit, which
   includes the PR's own edits. Re-running the gate there would let a PR grade
   itself. A PR that edits the guard is caught at PR time, by its review.

`tests/test_merge_queue_triggers.py` pins all four required contexts to a
`merge_group` trigger. It also pins that the gate steps run only on
`pull_request_target`, and that queue entries don't share a concurrency group
(a cancelled required check ejects a PR from the queue).

## 0. Founder preparation (F, before step 2 unless marked)

1. **Create the org.** <https://github.com/organizations/plan>, Free, named
   `TinyAssets`, owned by your personal account (host-actions row).
2. **Org settings → Third-party access → OAuth app policy:** remove
   restrictions. With a one-person org and a public repo, restrictions only
   block your own tools. If you'd rather keep them, approve **GitHub CLI** at
   minimum, plus any other OAuth app that writes to the repo.
3. **Org settings → People → your membership → Public.** The review gate
   trusts `author_association` in {OWNER, MEMBER, COLLABORATOR}. GitHub may
   report a private member as `CONTRIBUTOR` to tokens that can't see the
   membership, and that would silently fail your receipts.
4. **Mint the new merge token.** Settings → Developer settings → Fine-grained
   tokens → Generate: **Resource owner `TinyAssets`**, repository access "All
   repositories" (the org is empty until the transfer, and a token an org
   owner creates needs no approval). Permissions: **Contents: read and write,
   Pull requests: read and write** (the same as today). Keep the value for
   step 4. Don't set it yet: the current token still serves the old owner
   until the transfer.
5. **(After step 2.)** Reinstall GitHub Apps on the org: `github.com/settings/installations`
   → each app that listed `TinyAssets` → install on org `TinyAssets`.

## 1. Pre-transfer checks (L)

```bash
gh api orgs/TinyAssets -q .login                              # org exists
gh api orgs/TinyAssets -q .members_can_create_public_repositories
gh run list --repo Jonnyton/TinyAssets --workflow deploy-prod.yml --status in_progress   # must be empty
gh run list --repo Jonnyton/TinyAssets --workflow build-image.yml --status in_progress   # must be empty
python scripts/deployed_sha.py --json > /tmp/pre-transfer-receipt.json                  # baseline
git ls-remote https://github.com/Jonnyton/TinyAssets.git refs/heads/main                 # baseline main sha
gh api repos/Jonnyton/TinyAssets/pages -q .cname                                        # tinyassets.io
```

Note the open PRs with auto-merge armed. During the window between steps 2
and 5, they can still merge, and each merge builds into the new private
package, where its deploy refuses safely. To keep the window quiet, hold
merges until step 7.

## 2. Transfer (L)

```bash
gh api repos/Jonnyton/TinyAssets/transfer -f new_owner=TinyAssets
gh api repos/TinyAssets/TinyAssets -q '.full_name + " " + .visibility'   # TinyAssets/TinyAssets public
```

## 3. Local remotes (L, and every agent checkout)

```bash
git -C C:/Users/Jonathan/Projects/TinyAssets remote set-url origin https://github.com/TinyAssets/TinyAssets.git
git -C C:/Users/Jonathan/Projects/TinyAssets fetch origin
```

Worktrees share the primary checkout's config, so one command covers them.
Redirects keep old remotes working in the meantime.

## 4. Merge token (F pastes, or L with the value)

```bash
gh secret set MERGE_ATTRIBUTION_TOKEN --repo TinyAssets/TinyAssets   # paste the org-owned PAT from 0.4
```

## 5. Bootstrap the org image package (L, then F)

Build once from a **non-main** ref. `deploy-prod.yml` chains only from
`build-image` runs on `main`, so this creates the package without deploying:

```bash
gh workflow run build-image.yml --repo TinyAssets/TinyAssets --ref claude/org-cutover
gh run watch --repo TinyAssets/TinyAssets "$(gh run list --repo TinyAssets/TinyAssets --workflow build-image.yml --limit 1 --json databaseId -q '.[0].databaseId')"
```

**F:** `https://github.com/orgs/TinyAssets/packages/container/tinyassets-daemon/settings`
→ Danger Zone → Change visibility → **Public**. On the same page, under
"Manage Actions access", confirm `TinyAssets` is listed with **Write**. It
should be inherited through the image's `org.opencontainers.image.source`
label.

**L:** prove an anonymous pull works, as the droplet does it:

```bash
tok=$(curl -s "https://ghcr.io/token?scope=repository:tinyassets/tinyassets-daemon:pull" | python -c "import json,sys;print(json.load(sys.stdin)['token'])")
curl -s -H "Authorization: Bearer $tok" https://ghcr.io/v2/tinyassets/tinyassets-daemon/tags/list   # lists the bootstrap tag, not an auth error
```

## 6. Merge the cutover PR (L)

Rebase it on `main` if needed, mark it ready, and merge it by hand. The merge
queue isn't on yet, so this is a normal merge by your identity.

B needs only its `Lane:` and its review, like any PR. Then:

```bash
gh pr ready 4057 --repo TinyAssets/TinyAssets
gh pr merge 4057 --repo TinyAssets/TinyAssets --squash
```

The PR changes runtime paths (`tinyassets/universe_server.py`, `deploy/`), so
`build-image` decides `build`, and `deploy-prod` deploys the first
`ghcr.io/tinyassets` digest. After that deploy,
`install-host-services.yml` chains and installs the updated host scripts,
including `daemon_image_retention.py` with the new repository prefix.

## 7. Confirm the deploy (L)

```bash
MERGE_SHA=$(gh pr view 4057 --repo TinyAssets/TinyAssets --json mergeCommit -q .mergeCommit.oid)
gh run list --repo TinyAssets/TinyAssets --commit "$MERGE_SHA" --json workflowName,event,conclusion
python scripts/deployed_sha.py --assert-contains "$MERGE_SHA"
python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp --assert-handles
```

`deployed_sha.py` must exit 0 (Hard Rule 14). If the deploy refused at pull,
step 5's visibility change didn't take. Fix it, then
`gh workflow run deploy-prod.yml --repo TinyAssets/TinyAssets`.

## 8. Enable the merge queue on `main` (L)

Precondition: #4058 is on `main`. It limits `heavy-tests` to schedule and
workflow_dispatch. Without it, the non-required, hour-long `heavy-tests` job
also runs on every queue entry.

```bash
gh api -X POST repos/TinyAssets/TinyAssets/rulesets --input docs/ops/merge-queue-ruleset.json -q '.id'
```

That ruleset (`docs/ops/merge-queue-ruleset.json`) sets squash, max batch 5,
min 1, a 5-minute wait, a 60-minute check timeout (`required-tests` may take
up to 50), ALLGREEN (each PR's group commit must pass), and the required
contexts. It also sets an admin bypass, which matches today's
`enforce_admins: false`. Classic branch protection stays as it is. Rules and
protection layer, and the queue waits on the union.

UI alternative: Settings → Branches → edit the `main` rule → **Require merge
queue**, with the same values.

## 9. Pages and DNS (L, then F if the site is down)

```bash
gh api repos/TinyAssets/TinyAssets/pages -q '.cname + " " + .build_type'   # tinyassets.io workflow
curl -sI https://tinyassets.io | head -1                                    # 200
gh workflow run site-dns-cutover.yml --repo TinyAssets/TinyAssets -f apply=false   # dry run: expect "www CNAME -> tinyassets.github.io ... will upsert"
gh workflow run site-dns-cutover.yml --repo TinyAssets/TinyAssets -f apply=true
```

**Done 2026-09-27** (run 36301175692; the next uptime canary, 36301317603,
was green). `site-dns-cutover.yml` and `scripts/site_apex_cutover.py` were
then deleted as one-shot tooling (#4059); both are in git history. For a
DNS emergency, use `emergency-dns.yml`.

If the site 404s, re-publish with
`gh workflow run deploy-site-react.yml --repo TinyAssets/TinyAssets -f confirm=deploy`.
If the custom domain was dropped, run
`gh api -X PUT repos/TinyAssets/TinyAssets/pages -f cname=tinyassets.io` first.

**F, recommended:** verify `tinyassets.io` for the org (Org settings → Pages →
Add a domain → TXT record in Cloudflare). It's unverified today, which is a
standing takeover risk if Pages is ever disabled.

## 10. Droplet (L, if you have SSH; not urgent, since redirects cover it)

```bash
ssh <droplet> 'sudo -u tinyassets git -C /opt/tinyassets remote set-url origin https://github.com/TinyAssets/TinyAssets.git'
ssh <droplet> 'grep -n GITHUB_REPOSITORY /etc/tinyassets/env || echo "unset: uses the code default"'
```

If `/etc/tinyassets/env` pins `GITHUB_REPOSITORY=Jonnyton/TinyAssets`, change
it. Python's `urllib` won't follow a 307 on POST, so issue alerts sent to the
old slug fail. After the first new-path deploy, old `ghcr.io/jonnyton` images
on the host no longer match the retention prefix, so retention ignores them.
Once the new deploy is healthy, remove all but the current rollback target by
hand (`docker image ls ghcr.io/jonnyton/tinyassets-daemon`).

## 10b. Land PR C (L, any time after step 7)

Merge it through the queue, which also serves as the step 11 test PR if you
like. It touches 7 systemd units, so expect `install-host-services` to rewrite
them on its next run.

## 11. Verify the queue end-to-end (L)

Open a trivial docs PR, let auto-enroll pick it up, then:

```bash
gh pr view <n> --repo TinyAssets/TinyAssets --json autoMergeRequest,mergeStateStatus
gh run list --repo TinyAssets/TinyAssets --event merge_group --limit 8 --json workflowName,conclusion,headBranch
```

Expect `Tests`, `invariants` and `PR scope guard` runs on a
`gh-readonly-queue/main/pr-<n>-...` branch, then the PR merged. Then check the
push on the merge sha:

```bash
MERGE_SHA=$(gh pr view <n> --repo TinyAssets/TinyAssets --json mergeCommit -q .mergeCommit.oid)
gh run list --repo TinyAssets/TinyAssets --commit "$MERGE_SHA" --event push --json workflowName
```

At least `Tests` must appear with `event=push`. A docs-only PR doesn't build an
image, so prove the deploy chain on the next runtime PR:
`python scripts/deployed_sha.py --assert-contains <its merge sha>`. If there
are no push runs, see "Does a merge-queue merge trigger push workflows?" above.

## Behaviour changes to know

- `gh pr merge --auto` on `main` now arms "merge when ready", which enqueues.
  `gh pr merge --disable-auto` does not remove an already-queued PR, and the
  CLI can't dequeue ("You cannot use GitHub CLI to remove a pull request from
  a merge queue"). To hold a PR whose review came back BLOCK after it
  enqueued, remove it in the UI: PR page → **Remove from queue**.
- With the queue on, `gh pr merge --auto --squash` prints "merge strategy is
  set by the merge queue". That's a notice, not an error. Once GitHub has
  queued a PR, `autoMergeRequest` reads **null**, so a null there does not mean
  "not armed". Check `mergeQueueEntry` (GraphQL) or `isInMergeQueue` instead.
  Its `enqueuer` must be a user: a PR armed by `github-actions` never enters
  the queue (seen 2026-09-27 with an empty `MERGE_ATTRIBUTION_TOKEN`), and
  auto-enroll now fails loudly on both.
- Merges land in batches of up to 5. `build-image`'s `decide` job already
  judges the whole served..head range, so a batch deploys once.
- `strict` stays off. The queue now provides the "tested against current main"
  guarantee that `strict` would have, without the BEHIND churn.

## Rollback

- **Before step 2:** nothing to undo. Close or park the cutover PR.
- **After step 2, before step 6:** prod is untouched, since the running image
  stays cached and pulled. The only failures are refused deploys and a quiet
  auto-enroll. To stay in the org, finish steps 4-6. The code on `main`
  already builds to the org path.
- **Queue problems after step 8:** delete the ruleset. Merging reverts to
  auto-merge under classic protection:
  `gh api -X DELETE repos/TinyAssets/TinyAssets/rulesets/<id>`.
- **Bad deploy after step 6:** `deploy_fail_safe.sh` already rolls back to the
  recorded previous image, which is a public `ghcr.io/jonnyton` digest. For a
  manual pin, write that digest to `TINYASSETS_IMAGE` in
  `/etc/tinyassets/env` and restart (`deploy/DEPLOY.md`, "Short-SHA image pin
  not pullable").
- **Transferring back** (`gh api repos/TinyAssets/TinyAssets/transfer -f
  new_owner=Jonnyton`) is the last resort. The transfer retires the
  `Jonnyton/TinyAssets` name for new repos, and GitHub doesn't document
  whether a transfer back into a retired name is allowed. Revert the cutover
  PR in the same change, because the build and deploy workflows follow the
  owner either way.
