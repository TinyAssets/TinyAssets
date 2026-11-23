# deploy-version-visible as built (2026-11-23)

`fix/deploy-version-visible` fixes the stage `routes` runs 45184403474 and 45184872714
(2026-11-23), which stopped before any change: the kit uploaded a version of
`tinyassets-codemode`, then `wrangler versions deploy <it>@0 <before>@100` failed with "The
requested Worker version could not be found ... [code: 100146]". The research came first
(Fact 14).

## Research

Sources: the two runs' logs, `platform/deploy/deploy.ts` and `cloudflare.ts` on `main` at
351ba2fd6, #5800 (`deploy-readback.md`), wrangler's source at its pinned tag
(`cloudflare/workers-sdk` `wrangler@4.154.0`, `packages/wrangler/src/versions/deploy.ts`),
Cloudflare's API docs, and Cloudflare's status page as the coordinator read it during the
second run. `[I]` marks inference.

1. **The kit's sequence.** For a held Worker, `workers` runs `wrangler versions upload`, reads
   the new id from its output (`versionOf`), then runs `wrangler versions deploy` once a stage
   (`platform.json` `stages`: 0, then 100), each deployment read back (`readBack`). Nothing
   read the version between its upload and its first deploy. A first or whole deploy is one
   `wrangler deploy`, which uploads and deploys in one call, so it has no such gap. A
   rollback's deployment (`Cloudflare.deploy`) names versions deployed before.
2. **Where the failure came from.** The log names the failed request:
   `GET /accounts/***/workers/scripts/tinyassets-codemode/versions/<id>`, not the deployments
   POST. Wrangler's `versionsDeployHandler` fetches each version (`fetchVersion`, outside any
   try) before `createDeployment`, so a failed read stops it before the POST: nothing was
   deployed, and the run stopped at codemode, the first Worker, before any change.
3. **What Cloudflare documents.** Neither code 100146, nor its HTTP status, nor any
   read-after-write for the versions API after an upload. The upload had answered with the id.
   During the second run Cloudflare's status page showed Workers partially degraded ("changes
   to Cloudflare Workers are not producing Event Subscription events"). [I] The versions API
   lags an upload for some interval, longer during an incident.
4. **#5800's bounded re-read.** `readBack` read the deployments listing every 2 s for up to
   60 s until it matched, then threw naming the last read; it never deploys again. The loop
   was written into `readBack`. `answering` (the egress door's refusal after the custom
   domain's attach) held a second copy of the same loop with its own bound (10 min, 15 s).
5. **The versions endpoint in the client.** `annotations` (and `tag`) read
   `GET .../versions/<id>` through `#ok`, every failure alike. The test fake answered an unknown
   version 404 with code 10007, R2's "no such key", which Cloudflare does not answer there.

## The shape

After an upload, read the version through Cloudflare's versions API with the one bounded
re-read until it is listed, then deploy it. Never retry `versions deploy`: a deploy that
failed past its read might have half-applied, so a retry acts on a state not read.

1. **One bounded re-read, `reread(run, bound, read, holds, late)`.** Its bound is data
   (`Bound`: `READ_BACK`, 60 s every 2 s; `ANSWER`, 10 min every 15 s). `readBack`, the new
   `uploaded`, and `answering` all use it, so the loop is written once.
2. **`Cloudflare.listed(account, script, version)`.** True when the versions API reads the
   version, false only when it answers code 100146 (`NO_VERSION`); any other answer throws.
   The code alone is read, since nothing documents the status it comes with. `annotations`
   reads through the same private `#version`, so the path is written once, and a missing
   version now says so.
3. **`uploaded`.** It runs after each `versions upload` and before the first `versions deploy`,
   on `READ_BACK`. Past the bound it throws: "Cloudflare's versions API did not list <worker>'s
   version <id> 60 s after its upload, so nothing deployed it (a Cloudflare incident? check
   cloudflarestatus.com, then run again)", then the run's stop (the rollback hint, as for a
   read-back). For the first Worker, as live, nothing has changed. For a later one, the
   Workers before it have deployed, and the rollback the stop names takes them back.
4. **The plan's text does not change.** "upload a version ... and deploy it" still says what
   happens. The new reads appear only in the apply's timeline.

## Tests

- **Listed after N reads.** `unlisted: 3`: each upload is read three more times than with
  `unlisted: 0`, before its first `versions deploy`. The stages, the plan and what each Worker
  serves are the same as a run whose uploads were listed at once.
- **Never listed.** It stops at codemode with the message above, after 31 reads (60 s every 2
  s). No `versions deploy` runs, and every Worker serves its version before.
- **The client.** `listed` is true on success and false on 100146 alone; 500, 403, R2's 10007
  and a 200 without success throw. `tag` on 100146 names the missing version.
- **The fake** answers a missing version 404 with 100146, as live. Its `versions deploy` of a
  version still unlisted fails as wrangler's did, with nothing deployed.
- **Mutations**, each caught:
  - no `uploaded` call;
  - an unlisted answer throwing at once;
  - `uploaded` holding on any answer;
  - any 404 read as unlisted;
  - `answering` on `READ_BACK`;
  - `answering` taking its first answer;
  - `reread` sleeping its whole bound.

## Left

- Nothing is owed. If the versions API stays behind for over 60 s outside an incident, that
  bound is the one to raise, in `READ_BACK`, for every read-back alike.
