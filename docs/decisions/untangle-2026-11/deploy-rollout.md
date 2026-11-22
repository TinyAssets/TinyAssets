# deploy-rollout (2026-11-22)

`untangle/deploy-rollout` takes slice A2 of the deploy-safety research (§1.2, §1.3), the
"Owed" A2 line of `deploy-versions.md` (#5751). The research below came first (Fact 14), and
it shows the whole of A2 is too large and partly unbuildable in one PR, so it is a series:
this PR builds its first slice, A2a, and names the rest.

## Research

Sources: Cloudflare's docs from their source repo (`cloudflare/cloudflare-docs` at `180f559`,
2026-11-21 20:03 UTC), Cloudflare's API schema (`cloudflare/api-schemas` `openapi.json`,
fetched 2026-11-22), wrangler's source at its pinned tag (`cloudflare/workers-sdk`
`wrangler@4.154.0`, `71d0903`) and wrangler 4.154.0's `--help`. `[I]` marks inference.

1. **A split is two versions, set by one deployment.** `versions upload` makes a version and
   deploys nothing; `versions deploy <id>@<pct>..` sets the deployment; "Workers currently
   only supports serving **two** different versions in one deployment"
   (`version-overrides.mdx`). The REST call is `POST /accounts/{a}/workers/scripts/{s}/deployments`
   `{strategy: "percentage", versions: [{version_id, percentage}]}`; its schema gives each
   percentage a **minimum of 0.01**, while the docs and wrangler (`versions/deploy.ts`
   `parseOptionalPercentage`, 0 to 100) take 0%. So a 0% stage is wrangler's, or needs one
   live check of the REST call.
2. **The 0% smoke reaches only `fetch`.** An override (`Cloudflare-Workers-Version-Overrides:
   <worker>="<id>"`) selects a version in the deployment even at 0%; one outside it is ignored
   and "an ignored override can still return a successful response", so the served version
   must be read back (the doors' `x-tinyassets-version`, A1). Overrides do not reach RPC
   service bindings (`version-overrides.mdx`, "Service bindings"), and a deployment change
   "takes up to a few seconds to reach every location": a location without the new deployment
   ignores the override (`version-skew.mdx`). [I] So a 0% stage smokes the doors alone; the
   owner, gateway, files, codemode and sandbox Workers, reached over RPC, meet traffic first at
   the first split.
3. **Durable Objects run one version each.** Each object is assigned a version by the
   deployment's percentages, and keeps it "until you create a new deployment"; listed "in the
   same order as the previous deployment", a raised percentage never moves an object back
   (`gradual-deployments/with-durable-objects.mdx`). An active object keeps its old code up to
   `max_delay` (300 s by default) after it is assigned a new version
   (`durable-objects/deployments/durable-objects-code-updates.mdx`); wrangler 4.154's
   `versions deploy` and `rollback` take `--durable-objects-code-update-mode immediate |
   deferred <duration>` (its `--help`). A class lifecycle change (`migrations`, `exports`)
   "cannot be uploaded" as a version and "rollbacks cannot take place to any version prior"
   (same page): `--migrate` deploys whole, as the kit does today, and a split never holds one.
   App-level schema (`contract/src/migrate.ts`) migrates each object on wake by the version
   assigned to it, and refuses data newer than its code: a version that adds a migration is
   forward-only, during a split as at 100%.
4. **A version carries its own secrets.** `--secrets-file` sets them on the version uploaded,
   keeping the rest from the version before (`configuration/secrets.mdx`). Under a split each
   version serves with its own values. A deployment of an older version whose secrets have
   changed since is refused with code 10220 unless `force=true` (the schema's `force` query:
   "rolling back to an older version when a secret has changed"; wrangler's
   `versions/rollback/index.ts` `CANNOT_ROLLBACK_WITH_MODIFIED_SECERT_CODE`, which asks to
   confirm and then forces). So a run that sets a Worker's secrets is, for that Worker,
   forward-only too: its version before holds the old values.
5. **The script-level secret calls deploy at once, and refuse when the latest version is not
   deployed.** `PUT .../secrets` and `DELETE .../secrets/{name}` each create a version and
   deploy it (the schema; `wrangler secret put` "creates a new version of the Worker and
   deploys it immediately"); the API refuses with 10214 or 10215 when "the latest version of
   your Worker isn't currently deployed" (wrangler `secret/index.ts`
   `throwIfLatestVersionNotDeployed`). What it does during a split whose latest version is in
   the deployment at a part of the traffic is not documented [I: it may refuse, or deploy the
   latest version's code with the change at 100%]. So mode `retire`'s `wrangler secret put` and
   the import window's close (`window.ts` `unkey`, the same `DELETE`) are held by the kit
   itself to a Worker serving one version.
6. **Rollback is a 100% deployment of one version** (`rollbacks.mdx`), from a split too; only
   the 100 most recent versions are targets, and versions older than 30 days may be deleted.
   "Resources connected to your Worker will not be changed during a rollback."
7. **What the health check can read with the deploy's token.** The deployments and versions
   reads (`GET .../deployments`, the first "the latest deployment actively serving traffic";
   `GET .../versions/{id}`) take Workers Scripts Read or Write, which the token holds
   (`checklist.md`, "Before you run it"). wrangler reads a version's tag from its
   `annotations["workers/tag"]` (`versions/deployments/status.ts`). Per-version error rates are
   the GraphQL Analytics API's, which needs Account Analytics Read, not recorded on the token,
   and the docs still name no version field in `workersInvocationsAdaptive`
   (`analytics/graphql-api/tutorials/querying-workers-metrics.mdx`). Cloudflare offers no
   health-gated promotion. [I] Before the cutover's `routes` nothing public reaches the doors,
   so no stage before it has traffic or a smoke to read.
8. **`wrangler versions deploy` does more than the REST call.** After the deployment it syncs
   the config's non-versioned settings (`maybePatchSettings`: `logpush`, `tail_consumers`,
   `observability`, which every platform config sets). So the run's own versions stay
   wrangler's to deploy; a rollback, which must change nothing but the version, is the REST
   call, never forced.
9. **The routing key outlives any deploy.** `ROUTING_MAC_KEY` MACs a sandbox lease's egress and
   sync tokens, which have no expiry of their own (`LEASE_LIVED`, `contract/src/routing-mac.ts`),
   and seals each sandbox's route into E2B's metadata (`sealRoute`, `owner/src/launch.ts`). One
   key rotated, at 100% or under a split, refuses every running sandbox's egress, sync and
   lifecycle hook, and the doors refuse what the owner seals between the owner's deploy and
   theirs (A1's "Behaviour changed on purpose"). Rotation "only at 100%" fixes neither. A
   keyring (mint and seal under the active kid, verify and open under any kid the ring holds),
   as `OPERATOR_KEYRING` already is for the operator channel's MAC, fixes both. **Decided: a
   keyring**, its own slice.

## The shape, checked against the intent

- **One home.** The kit stays the one writer of deployments; the rollout record lives beside the
  route record in the control namespace; the split check is one client call (`Cloudflare.serving`)
  that the deploy, `retire` and the window's close all use; forward-only is one function
  (`forwardOnly`) the deploy's plan and `rollback` both read.
- **Data, not code.** The record holds facts only (each Worker's version before and its tag, the
  run's lock counts, whether it went whole, the secret names it set); every rule is computed from
  them. The stages and soak (A2b) go in `platform.json` with their reason.
- **No provider code beyond Cloudflare's own deploy API.** Reads and the rollback's deployment
  are Cloudflare's REST API through `deploy/cloudflare.ts`; uploads stay wrangler's.
- **A migration forbids a rollback, never a deploy.** `--migrate` deploys whole (fact 3); a
  version adding an app-level migration, setting secrets, or deployed first is forward-only, and
  the rollback stops at it (below).

## The series

1. **A2a, this PR:** the rollout record, mode `rollback` with the forward-only refusal, and the
   split refusals, all at 100%.
2. **`untangle/routing-keyring`:** `ROUTING_MAC_KEY` a keyring (fact 9), before any split.
3. **`untangle/deploy-stages` (A2b):** the stages (0%, splits, 100%) and soak in `platform.json`,
   the health check (deployments read back; per-version errors once the GraphQL field is read
   live and the token holds Account Analytics Read, the founder's scope), the doors' 0% smoke by
   the canary with the override header held to `x-tinyassets-version` (after `routes`, fact 7),
   the new version listed first in every stage, a Worker that goes whole (`--migrate`, a first
   deploy) at 100% at once, a run setting `E2B_WEBHOOK_SECRET` (one value at E2B) taking the
   doors to 100% at once, the workflow's timeout, and the 0.01 minimum (fact 1) settled.

## A2a as built

(Filled in below as it is built.)
