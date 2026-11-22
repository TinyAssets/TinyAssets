# deploy-zero as built (2026-11-22)

`untangle/deploy-zero` takes A2b-2 of the deploy-safety work, as `deploy-stages.md` (#5788)
owes it in "The series" item 2: the stages `0` then `100` for each Worker, the soak, each
deployment read back, and the doors' 0% smoke once they serve routes, under the rollout
record's rules (`deploy-rollout.md`, #5782) and the apply job's line-by-line hold
(`plan-apply-secrets.md`, #5786). The splits and the analytics health check stay
`untangle/deploy-splits`'s. The research below came first (Fact 14).

## Research

Sources: wrangler's source at its pinned tag (`cloudflare/workers-sdk` `wrangler@4.154.0`:
`packages/wrangler/src/versions/deploy.ts`, `versions/api.ts`, `packages/cli/interactive.ts`,
and `packages/deploy-helpers` 0.24.1's `deploy/helpers/versions-api.ts`, `deploy/deploy.ts`,
`deploy/versions-upload.ts`), Cloudflare's docs (`cloudflare/cloudflare-docs`, `production`,
fetched 2026-11-22: `workers/versions-and-deployments/version-overrides.mdx`,
`gradual-deployments/index.mdx`, `gradual-deployments/with-durable-objects.mdx`), the kit at
`d83cf7c83`, and the deploy workflow's recent runs (durations only). `[I]` marks inference.

1. **Wrangler's 0% is the REST call with a 0 in it.** `versions deploy <id>@<share>..` parses
   each share from 0 to 100 (`parseOptionalPercentage`), requires them to total 100
   (`validateTrafficSubtotal`) and at most two versions (`max-versions`, 2). With the specs
   given, `--yes` accepts them as given: the versions in the order named
   (`promptVersionsToDeploy`'s multiselect returns its default, `interactive.ts`
   `acceptDefault`), each share set in that order (`promptPercentages`). It then sends
   `createDeployment`: `POST .../workers/scripts/{s}/deployments` with `{strategy:
   "percentage", versions: [{version_id, percentage}..]}` in that order, the same call the
   kit's rollback makes, then syncs the config's non-versioned settings
   (`maybePatchSettings`). So the schema's 0.01 minimum (#5788 research 9) is not what the
   server takes from wrangler [I: the docs' own smoke-test example deploys a version at 0%
   with `wrangler versions deploy`]. The run's stages stay wrangler's, for the settings sync
   (#5782 research 8); the first `dark` run's 0% stage shows the server's answer live.
2. **What each command prints.** `versions upload` logs `Worker Version ID: <id>`
   (`versions-upload.ts`), `deploy` logs `Current Version ID: <id>` (`deploy.ts`). The kit
   read only the first, and never read a whole deploy's version.
3. **Overrides, rechecked.** The header is a Dictionary Structured Header,
   `Cloudflare-Workers-Version-Overrides: <worker>="<id>"`, applied only where "the deployment
   at the location handling the request" holds the version, 0% included; otherwise the shares
   decide, and "an ignored override can still return a successful response", so the answer's
   version is read. "Deployment changes take up to a few seconds to propagate ... A fixed wait
   or a check at one location does not prove that every location includes B." A wait before
   the smoke makes a false failure unlikely; holding each answer's version makes a false pass
   impossible. The doors serve their app's files with `run_worker_first`, so every answer,
   assets included, passes `versioned` and names its version.
4. **Durable Objects at 0%.** Each object is assigned a version "based on the percentages" and
   keeps it until a new deployment; listed in the same order, a raised share never moves one
   back. [I] At 0% no object is assigned the new version, so none runs its migrations: a 0%
   split of a Worker with a new `-m` count can be ended by a rollback, which `forwardOnly`
   would refuse at 100%.
5. **Who reaches a 0% version.** Overrides reach `fetch` and no RPC, so only the doors' new
   version is reachable at 0%, and only once the doors hold their routes. A `routes` run
   deploys the Workers before its handover (`ROUTED`'s order), so the cutover's own run, like
   every `dark` run before it, reaches no doors: the first smoke is the first deploy after
   the cutover.
6. **Bindings and the order.** A Worker is uploaded after every Worker it binds is deployed
   (`WORKERS`): a binding to a new entrypoint or class needs its callee's new version first
   [I: Cloudflare checks a Durable Object binding's class against the script it names]. So the
   stages go Worker by Worker, each to 100% before the next is uploaded, not stage by stage
   across Workers.
7. **Time.** Recent `dark` apply jobs ran about a minute (run 45031987348: 64 s). The stages
   add one deployment and two reads a Worker, and one 30 s soak once the doors hold routes:
   well inside the apply job's 30 minutes. No timeout change.

## The shape, checked against the intent

- **Data, one rule, one home.** `platform.json` `stages` (`shares` `[0, 100]`, `soak_s` 30,
  with their reason; `facts.ts` `stagesOf`) and the workers step loop over them, so
  `untangle/deploy-splits` adds its shares and its health check, not a second path. Which
  Worker skips the stages is one function, `atOnce`: a first or whole deploy, secrets the run
  sets, a forward mark above its version before's. `forwardOnly` is `atOnce` and the
  migration counts, so the two cannot drift. A migration alone takes the stages (research 4).
- **Every fact read back.** Each deployment the run makes, whole deploys included, is read
  back before anything follows it, against the exact versions and shares in order.
- **The smoke is the canary's, held to the version.** `canary.ts --smoke <id>` runs the doors'
  own checks alone, each request to the doors' host or the egress host carrying the override
  and each answer held to `VERSION_HEADER` (exit 16 otherwise). The header's name moves to the
  contract (`identity.ts`), which the doors write and the canary reads.
- **The soak is the smoke's wait.** At 0% nothing but the smoke reads the new version, so the
  soak is held only before a smoke (research 3); a stage with no smoke goes on after its
  read-back. `untangle/deploy-splits` gives its split stages their own window for the error
  rates.
- **Dark takes the stages too (a departure, better).** `deploy-stages.md` planned every Worker
  at 100% at once before `routes`. Built: every `dark` run takes each held Worker through 0%
  and 100%, each read back, the doors' line saying they are not smoked because they hold no
  routes yet. So the 0% call, its order and its read-back are proven live before the cutover,
  not first at the first deploy after it, at the cost of one deployment and two reads a Worker.
- **Every new line is held.** Each Worker's deploy line is said before its first change and is
  the same in both jobs (`decided`, which `rolledLine` now shares, leaves the doors' stages to
  the apply job while only optional secrets it cannot see decide them); the read-backs and the
  smoke's pass name versions only the change makes, so they are `did`, reported, never held.

## The problems found

- `workers` recomputed a first and a whole deploy from `deployed` and `--migrate`, a copy of
  what `rollout` had just read (`rolled.before`, `rolled.whole`).
- `rolledLine` held the one logic for a line the plan job cannot finish (optional secrets);
  the stage line needed the same.
- `VERSION_HEADER` was the doors' (`doors/src/doors.ts`) while its reader is the deploy kit.
- `versionOf` read only an upload's words; a whole deploy's version was never read.
- The rollback's read-back compared one version by hand; the stages need the same compare.
- The canary's spawn was written inside one step.
- The deploy harness's fake printed an upload's words for `wrangler deploy` and took one spec.
- No stage existed: every later deploy went to 100% at once, its deployment never read back.

## The fix

1. `platform.json` `stages`, `facts.ts` `Stages`, `stagesOf`, `STAGES`.
2. `atOnce` (and `forwardOnly` over it); `decided` (shared by `rolledLine` and `deployLine`).
3. `rollout` makes `rolled`; `workers` reads it, says each Worker's `deployLine`, and deploys it
   through the stages: upload, `versions deploy <new>@<share> <before>@<100-share>`, read
   back, and for the doors once `routed` the soak and `canary.ts --smoke`, then `<new>@100`,
   read back; a whole or first deploy read back by the id `wrangler deploy` prints. A stop
   names `--mode rollback --record <run id>`.
4. `routed`: the route plan's (routes) or `routedNow`'s (dark), one read of the zone's routes
   a run.
5. `rollbackAsk` takes back a Worker serving its run's version at 0% beside its version
   before whatever `forwardOnly` says (research 4); `servesAs`, `sharesLine` for every
   read-back.
6. `canary.ts --smoke` (`smoked`, exit 16); `canaryExit`, the kit's one spawn of it.
7. `VERSION_HEADER` in `contract/src/identity.ts`; `versionOf` by command (`PRINTS`).
8. The checklist's step 4 and rollback say the stages, the smoke and the 0% way back.

### Behaviour changed on purpose

- A held Worker's deploy is two deployments (0% beside its version before, then 100%), each
  read back; its plan line says so, and the summary gains a read-back line a deployment.
- Once the doors hold their routes, their 0% is held 30 s and smoked; a failed smoke stops the
  run with the doors split, which a rollback of the run ends.
- A deployment that reads back otherwise stops the run there.
- A rollback takes back a 0% split of a Worker whose run's version has a higher `-m` count.
- Plan lines reworded: each Worker's deploy line.

### Left, each with its reason

- **The soak is a fixed wait,** as Cloudflare's own flowchart has it, and proves nothing alone:
  the smoke holds every answer, so a location still without the deployment fails loudly
  (exit 16), never a false pass.
- **Only the doors are smoked.** No override reaches RPC (research 5): the other Workers' new
  versions meet traffic first at 100% until `untangle/deploy-splits`' splits.
- **A run stopped between stages is not resumed.** A re-run attempt stops at the split check;
  `rollback` of the run ends the split, then a dispatch goes again.
- **The read-back right after a deployment** trusts the API to answer what it just set [I];
  the next `dark` run shows it live.

## Owed

- [ ] live: the next `dark` run's plan and summary show each held Worker's line "upload a
  version of ... and deploy it at 0%, listed first beside its version before, not smoked: ...,
  then at 100%, each read back, touching no route", then "... serves <new> at 0% and <old> at
  100%, read back" and "... serves <new> at 100%, read back"; no smoke, no soak; each Worker's
  Deployments in the dashboard list the run's two deployments, 0% then 100%.
- [ ] live: the first deploy after the cutover's `routes` (the doors hold their routes) says
  the doors are "held 30 s and smoked there", and the smoke passes with every doors answer
  naming the new version in `x-tinyassets-version`.
