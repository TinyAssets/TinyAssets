# deploy-stages as built (2026-11-22)

`untangle/deploy-stages` takes A2b of the deploy-safety research, as `deploy-rollout.md`
(#5782) names it in "The series" item 3 and owes it: the stages, the soak and health check,
the doors' 0% smoke, and, before any split, the rule and the mark that make a version
forward-only for a shape or protocol its version before cannot read (its "Left", review),
with the record's trust (a planted record naming another real, older version). The research
below came first (Fact 14). It shows A2b is three PRs, ordered by what each needs: this PR
builds the first, the one every split needs and that works today at 100%, and names the rest.

## Research

Sources: Cloudflare's docs from their source repo (`cloudflare/cloudflare-docs` at `1c6e632`,
2026-11-22 09:43 UTC), Cloudflare's API schema (`cloudflare/api-schemas` `openapi.json`,
fetched 2026-11-22), the kit at `475c51e5a`. `[I]` marks inference.

1. **Overrides, rechecked.** `Cloudflare-Workers-Version-Overrides: <worker>="<id>"` selects a
   version only when "the deployment at the location handling the request" includes it, at
   0% included; otherwise the percentages decide, and "an ignored override can still return a
   successful response", so "check the version as well as the status code"
   (`version-overrides.mdx`, its table "Versions in the deployment"). They reach `fetch` and
   `fetch`-based service binding calls only, never RPC (same page). Unchanged since #5782.
2. **What the doors' `x-tinyassets-version` lets a 0% smoke prove.** Every doors answer names
   the version that served it (`CF_VERSION_METADATA`, A1). Held to the new version's id, a
   smoke answer proves the override was taken at that location and the doors' new code
   answered, and so turns an ignored override from a false pass into a failure. It proves
   nothing of the owner, gateway, files, codemode or sandbox's new versions: the doors reach
   them over RPC, which no override reaches, so at 0% the new doors meet the callees' versions
   at 100%, the old [I]. That is a real check of one direction of skew (new caller, old
   callee), the one a split makes per request; the callees' new versions first meet traffic
   at the first split. Before the cutover's `routes` no public request reaches the doors, so
   no smoke can run (#5782 research 7).
3. **Durable Objects under a split, rechecked** (`gradual-deployments/with-durable-objects.mdx`):
   one version per object, assigned by the deployment and kept "until you create a new
   deployment"; listed "in the same order as the previous deployment", a raised percentage
   never moves an object back; an object is reset only when assigned another version. A
   class lifecycle change "cannot be uploaded" as a version (so `--migrate` deploys whole and
   no split holds one). An app-level migration (`contract/src/migrate.ts`) runs on each
   object's wake under the version assigned to it: under a split only the new version's share
   of objects migrates, and a later stage, listed in the same order, never moves one back.
   The rollback is what crosses it: forward-only, as #5782 built.
4. **A split mixes the two versions both ways, where a rollback mixes them one way.** During
   a split each request picks its version (`gradual-deployments/index.mdx`, "Version skew
   within a Worker"; `version-skew.mdx`, "Traffic split between versions"), so whatever the
   new version writes outside a Durable Object (a KV value, an R2 object or layout, a queue
   message, JSON in a column of another Worker's object) is read by the old version *while the
   split lasts*, and whoever the new version's answers reach (an open page of the doors' app,
   a sandbox started from its template, a queued message) calls the old version on the next
   request. A rollback does the same once, after. So a version whose writes or callers its
   version before cannot read is forward-only *and* must not be split: it goes to 100% at
   once. The fix in both cases is the same expand-then-contract the runner's format and
   protocol already follow (`sandbox/runner/state-format.ts`): ship the reader first, which
   the version before it can follow, then the writer, whose version before reads it. A
   version shipped that way needs no mark.
5. **What the tree can see of these, and what only the author can.** The kit reads the tree
   and Cloudflare; neither tells a new shape from an old one. The runner's disk format and
   its protocol with the sandbox Worker are already held one deploy deep both ways
   (`state-format.ts`, `protocol.ts` `SPEAKS`), so the runner the sandbox Worker installs is
   not one of them. The template is: the sandbox Worker starts sandboxes from its version's
   `E2B_TEMPLATE` (`sandbox/src/control.ts`), they persist between runs, and a rolled-back
   Worker resumes a sandbox made from the newer image and installs its older runner there, a
   pair no check passed (`template/pair.ts`). That one change is visible in the tree exactly:
   the pinned template differs from the base's. Every other kind (a KV value's shape, an R2
   layout, a queue message, Durable Object storage outside `migrate.ts`, a column's JSON, the
   doors' app API, what the runner sends the doors) is visible only to the author.
6. **Where a mark can live so a rollback reads it from Cloudflare, not from KV.** A version's
   tag (`workers/tag`, at most 100 bytes, written only with the deploy's token) already
   carries the run's commit and its migration counts (`versionFlags`, A1); a rollback holds the
   record's counts to the tags (#5782). A count of marks rides the same tag and is held the
   same way: a mark added since the version before makes the run's count higher than the
   before's tag names.
7. **The deployments listing is Cloudflare's history, and only the token writes it.**
   `GET .../workers/scripts/{s}/deployments` lists deployments "sorted from the latest
   deployment to the oldest", paged (`page`, `per_page` at most 100, `result_info`), each with
   its versions and their percentages (the schema). A deployment is made or deleted only with
   Workers Scripts Write, the deploy's token's; the Workers that can write the control
   namespace hold no token. So whether a Worker served a version just before a run of the
   commit is Cloudflare's to say: a deployment of that version alone, followed by one
   serving a version tagged with the commit. A record naming another version as "before" (a
   real, older one with its real tag, which #5782's check passes) names one no such pair
   holds, unless a run of the same commit followed it too, which makes it a version before
   that commit as well. Holding it to the deployment just before the latest unbroken run of
   the commit's deployments (the first shape built) is stricter and wrong: after a later
   run and its rollback, the listing shows the commit's version again over the later one,
   and the rollback after that, to the earlier run's version before, is one the founder asks
   for.
8. **The health check's error rates, rechecked.** The docs still name no version field in
   `workersInvocationsAdaptive` (`analytics/graphql-api/tutorials/querying-workers-metrics.mdx`
   shows `scriptName`, `status`, `datetime`; `features/data-sets.mdx` lists the dataset only);
   per-version attribution is documented only for Logpush's `ScriptVersion`
   (`gradual-deployments/index.mdx`, "Observability"). The dashboard's per-version rates are
   "calculated from the same GraphQL Analytics API data" (#5782 research 7), so the field
   exists, unnamed. One introspection query with a token holding **Account Analytics Read**
   names it; the token's record holds Zone DNS Edit and Account Workers Scripts Edit
   (`checklist.md`, "Before you run it"), not that (founder_batch item 14).
9. **0% is wrangler's.** The REST deployment's schema still sets each percentage's minimum at
   0.01 (fetched 2026-11-22); wrangler 4.154's `versions deploy` takes 0 (#5782 research 1),
   and syncs the config's non-versioned settings after (research 8 there), which the run's
   own stages want. So the run's stages stay wrangler's; a rollback stays the REST call.

## The shape, checked against the intent

- **One mark, one count, one rule.** What only the author knows is data the author writes in
  the commit: `platform.json` `forward`, each change naming the Workers whose version before
  cannot read what it writes or who calls it, with its reason. Each Worker's count of changes
  rides its version's tag (`-f<n>`), beside its `-m` counts, and `forwardOnly` stops a
  rollback at a Worker whose count is above its version before's, as it does for `-m`. The
  record carries the count as it carries `counts`, held to the tags. The stages slice reads
  the same count to take a marked Worker to 100% at once (research 4).
- **A guard where the tree can see it, the reviewer where it cannot.** The guard holds the
  list to only growing against the base, as the migration locks are, and holds a change of the
  pinned template to adding a change naming the sandbox (research 5). The rest is the
  author's to mark and the reviewer's to find unmarked, which the untangle skill's reviewer
  list now says; the checklist's "read the run's diff before you approve" goes.
- **Every fact a rollback acts on is Cloudflare's.** The record's version before is held to
  the deployments listing (research 7), its Workers and their order to the tree's deploy
  order, its counts and marks to the tags. `whole` and `secrets` stay the record's word: a
  false one only lets the kit ask for a deployment Cloudflare refuses (a class change or
  changed secrets since, 10220, never forced), a loud stop after the callers before it went
  back, the direction every deploy takes. The founder's hold of each version to the job
  summary goes.
- **No new reader of KV, no provider code beyond Cloudflare's own deploy API.**

## The series

1. **A2b-1, this PR (`untangle/deploy-stages`):** the forward mark and its guard, and the
   rollback's record held whole to Cloudflare. Changes nothing at 100% but what a rollback
   refuses; every later slice reads the mark.
2. **A2b-2, `untangle/deploy-zero`:** the stages in `platform.json` (`0`, then `100`, with their
   reason), the soak (the run's `Io.sleep`, the tests' clock), and its health check without
   analytics: each Worker's deployment read back, and after `routes` the doors' 0% smoke by
   the canary with the override header, each answer held to `x-tinyassets-version` (research
   1, 2). Each Worker's new version listed first in every stage; a Worker the run deploys
   first or whole, sets a secret on, or marks (research 4) goes to 100% at once; before
   `routes` (the doors serve no one) every Worker goes to 100% at once and the plan says so.
   The workflow's apply job's timeout; 0% by wrangler alone (research 9).
3. **A2b-3, `untangle/deploy-splits`:** the split stages (10%, 50%), after the routing keyring
   (`untangle/routing-keyring`) and after the GraphQL field is read live (research 8): each
   Worker's error rate on its new version against its old one over the soak, with a request
   floor under which the smoke alone decides. A health read that fails, Account Analytics
   Read missing included, stops the run before the split it would gate, naming the
   permission, never a pass (Fact 8); a failed check rolls the run back in reverse order
   (`rollbackAsk`'s path).

## A2b-1 as built

### The problems found

- The forward-only rule read only `-m` counts, `--migrate`, secrets and first deploys; a
  version writing a shape, or answering a caller outside the binding graph, that its version
  before cannot follow had no mark, so the checklist asked the founder to read the run's diff
  before approving a rollback (#5782 "Left", review).
- The rollout record's version before was the record's word: tags and counts were held to
  Cloudflare's, but a record naming another real, older version with its real tag passed,
  and the checklist asked the founder to hold the plan to the job summary's copy. So did its
  Workers and their order: a record leaving the doors out, or turning the order, would take
  a callee back while its caller stayed on the run's version, the direction no deploy makes.
- The base a growth guard compares against was written in the migration locks' guard alone
  (`lockBase`, `baseLock`, under `MIGRATION_LOCK_BASE`), so a second growth guard would copy it.

### The fix

1. **The forward list** (`platform.json` `forward`, `facts.ts` `forwardCounts`, `FORWARD`):
   each change names Workers of this tree, by folder, each once, with why; each Worker's
   count of the changes naming it. It starts empty: no version deployed so far has a change
   its version before cannot follow that the list could name, and every tag written before
   it reads as count 0, which is exact.
2. **The count rides the tag** (`versionFlags`: `-f<n>` after the `-m` counts, left out at 0;
   `taggedAs` reads it), the record (`Rolled.forward`, `rolloutOf` holds it in shape), the
   plan's rollout line and `forwardOnly`, which stops at a count above the version before's
   tag's, or a tag that names none: unknown.
3. **The record held whole to Cloudflare** (`rollbackAsk`): its Workers are this tree's, in its
   deploy order; the serving versions' tags hold its `-f` count as they hold its counts; and
   each Worker it takes back is one whose deployments listing (`Cloudflare.deployments`, all
   pages, newest first) holds a deployment of its version before alone followed by one of the
   run's commit (`servedBefore`, research 7). Each stop is before any change.
4. **The guard** (`deploy/guards/forward.test.ts`): the list only grows against the base, and a
   moved pinned template comes with a change naming the sandbox. The base and a file at it are
   `ci/history.ts`'s (`baseOf`, `fileAt`, under `GUARD_BASE`), which the locks' guard uses too.
5. The untangle skill's reviewer list blocks a change its version before cannot follow with
   no forward change; the checklist's rollback says the `-f` stop and the holds, the
   founder's two reads go, and a rollback by hand follows `-f` as it follows `-m`.

### Behaviour changed on purpose

- A rollback stops, with nothing changed, on a record naming Workers other than the tree's in
  its order, and on a version before no deployment pair in the Worker's listing holds; it
  reads each taken-back Worker's whole deployments listing first.
- A rollout record written before this PR lacks `forward`, so `rollbackAsk` refuses it as out
  of shape: the only ones are `dark` runs with no routes and no users, which a new `dark` run
  replaces.
- `MIGRATION_LOCK_BASE` is `GUARD_BASE` (no workflow sets either).

### Left, each with its reason

- **`whole` and `secrets` stay the record's word.** A false `whole: false` or `secrets: []`
  only lets the kit ask for a deployment Cloudflare refuses (a class lifecycle change since,
  or changed secrets, 10220; never forced): a loud stop with the callers before it already
  back, the direction every deploy takes. Cloudflare's own refusal is the hold.
- **Only the template is found by the guard.** Every other kind of change the list names is
  invisible in the tree (research 5): the author marks it, the reviewer finds it missing.
  A recorder's `Recorders-Differ:` line is no tie (review): the recorders hold answers, which
  go back with the version, never a shape written; a forced mark there would stop every
  rollback past an additive answer.
- **A Worker a forward change names cannot retire under the append-only rule** (review):
  `forwardCounts` throws on a folder the tree lacks. Loud; the retiring PR changes the rule.
- **A rollback across a tree that added or retired a Worker** stops at the order check: the
  workflow runs from `main`, so such a rollback is by hand (the checklist's rules).
- **The stages, the soak, the health check and the 0% smoke:** A2b-2 and A2b-3, below.

## Owed

- [ ] `untangle/deploy-zero`: A2b-2 as "The series" item 2 says, the stages `0` then `100`
  in `platform.json`, the soak, the deployments read back, the doors' 0% smoke by the canary
  with the override header held to `x-tinyassets-version` once the doors serve, and a Worker
  the run deploys first or whole, sets a secret on, or marks with a forward change taken to
  100% at once.
- [ ] `untangle/deploy-splits`: A2b-3 as "The series" item 3 says, after
  `untangle/routing-keyring` and the GraphQL field read live: the split stages, each
  Worker's per-version error rate with a request floor, a failed health read (Account
  Analytics Read missing included) stopping the run before the split it gates, naming the
  permission, and a failed check rolling the run back.
- [ ] live: the next `dark` run's versions are tagged as before (no `-f` while the list is
  empty) and its rollout record holds each Worker's `forward: 0`; a `rollback` dispatch of
  that run's id, its plan job alone, reads each Worker's deployments listing and names each
  Worker's way back as the record's lines said, with no stop on the record's holds.
