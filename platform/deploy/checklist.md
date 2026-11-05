# The dark deploy: what it changes

The kit is `platform/deploy/deploy.ts`. It runs from the manual workflow **Platform deploy
(manual, founder-approved)** (`.github/workflows/platform-deploy.yml`), on `main` only, in
two jobs:

1. **`plan`** prints what the run would change to the job summary. It holds only the
   Cloudflare token and the repo's variables, and changes nothing.
2. **`apply`** waits for the plan job, then for the main-only `production` environment. With
   you as that environment's required reviewer, GitHub holds it until you approve it, with
   the plan already on the page. It then runs the kit with `--yes`.

Run mode `plan` once and read the list. Then run mode `dark`, read its plan, and approve.

**The go-ahead deploy** is that first approved `dark` run. It turns on the platform's storage,
secrets, six Workers and control record (every gate closed), and nothing public: it moves no
route, so `tinyassets.io` keeps serving the old stack. The doors go live only with mode
`routes`, at the cutover.

## Before you run it

- **Your approval.** Add yourself as the `production` environment's required reviewer
  (`docs/host-actions.md`), so the apply job waits for you after its plan. Whether it has
  one today is **unverified** here. Without one, starting a run with a mode other than
  `plan` is the approval.
- **The secrets and variables.** The kit reads nothing from a host.
  - The `production` environment's secrets: `WORKOS_API_KEY`, `E2B_API_KEY`,
    `ROUTING_MAC_KEY`, `GATEWAY_KEYRING`, `FILES_KEYRING`, `E2B_WEBHOOK_SECRET`,
    `TINYASSETS_WIKI_CANARY_TOKEN` (the handles canary's), and, if you want Slack events,
    `INBOUND_SLACK_SIGNING_SECRET`.
  - `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` as repository secrets, where
    `deploy-worker.yml` already reads them. The plan job reads them too; the repo's
    workflow guard counts neither as a production secret.
  - Repository variables, which are not secrets: `OWNER_ISSUER` (AuthKit's production issuer
    URL), `WORKOS_CLIENT_ID`, and `OWNER_TOKEN_CLAIMS` (below).
  - `WORKOS_API_KEY` lives on the production host today. Move it once, the way
    `copy-secrets-to-production.yml` moved the others.
  - `ROUTING_MAC_KEY` is one random 32-byte value you make and store. The kit never makes it.
  - `GATEWAY_KEYRING` and `FILES_KEYRING` (the files Worker's data keys' ring), each its own:
    make the first one locally with
    `node --experimental-strip-types platform/deploy/deploy.ts keyring-new <file>`, store the
    file's contents, then delete the file.
  - `E2B_WEBHOOK_SECRET`: the signature secret you register with E2B's sandbox lifecycle
    webhook (`/hooks/e2b/lifecycle`). Its registration at E2B is owed (tasks.md).
- **The migration lock, pinned at the commit you deploy.** The kit refuses to run unless
  `platform/owner/migrations.json` marks the first deploy and pins every migration the code
  ships, in order. To pin it:
  1. **Who and when.** You, or a session you ask, do it once, in the same sitting as the
     first deploy. The deploy itself is yours to approve.
  2. **Pin.** On a branch made from the current `main`, run this in `platform/owner`:
     `PIN_MIGRATIONS=$(git rev-parse HEAD) npx vitest run --project guards guards/migrations.test.ts`.
     It writes `platform/owner/migrations.json`: the first-deploy marker (time and commit)
     and a digest for every migration.
  3. **Land it.** Commit only that file and merge it to `main` by pull request. The workflow
     runs only from `main`, and the kit reads the lock there.
  4. **Deploy.** Run the Platform deploy workflow from `main`: `plan`, then `dark`.
  5. **Every later deploy that adds a migration** repeats steps 2 and 3 first. The pin only
     appends, and the kit refuses until it is done.
  6. **Pin only when you deploy.** Pinning freezes the baseline for good: from then on a
     schema change is a new migration, even if no deploy follows.
- **`auth_time`, settled before anything changes.** Every sign-in needs AuthKit's tokens to
  carry `auth_time`. Store one real AuthKit token's `iss` and `auth_time`, copied from it, as
  the variable `OWNER_TOKEN_CLAIMS`, a JSON object (the `fetch-prod-logs` workflow shows a
  claim receipt). The kit reads only those two, so leave out `sub` and the rest. Without it, the kit uses the
  discovery's `claims_supported`. A field that is absent counts as unknown, and unknown stops
  the kit; it never passes. If the token has no `auth_time`, the kit stops. The way on is
  to have AuthKit send it (`max_age=0`). Taking the binder's clock at the code exchange
  instead is not something the code can do: it would need its own change and your approval.
- **The sandbox's template is built.** `E2B_TEMPLATE` in `platform/sandbox/wrangler.jsonc`
  must have a ready build at E2B (`platform/sandbox/template/build.py`, or the manual
  `sandbox-template.yml` workflow in the production team). The kit checks this through E2B's
  API and stops if it is missing. The runner is the sandbox Worker's own: the workflow's
  `pair` job, before the apply job, looks for the pair's tag (the template and the runner it
  deploys) and, when absent, runs the pair check (`deploy.ts --pair`, `build.py
  --check-pair`, three throwaway sandboxes, up to about 50 minutes) with the E2B key alone; a
  failure stops the run, and the apply job stops without the tag.
- **The Cloudflare plan is Workers Paid.** The owner Worker needs it for its subrequests and
  for Durable Objects on SQLite.
- **AuthKit's production Google and GitHub apps exist** (WorkOS dashboard).

## What mode `dark` changes

Each step reads before it writes. A failed listing or read stops the kit with its error.
It never counts as "absent".

1. **Checks, before any change.** The issuer's discovery must name `OWNER_ISSUER` exactly,
   declare `client_secret_post`, and name https endpoints. `auth_time` must be present, as
   above. The E2B template must be ready. Any miss stops the kit.
2. **Storage, created only when its listing answered and lacks it:** three KV namespaces
   (`tinyassets-oauth`, `tinyassets-directory`, `tinyassets-control`), one R2 bucket
   (`tinyassets-files`), and the queues the Workers send to (`tinyassets-sandbox-ring`,
   `tinyassets-erasure`).
3. **Secrets, set only when the Worker's secret listing answered and lacks them** (or the API
   says the Worker does not exist yet). Values come from the environment's secrets:
   - `ROUTING_MAC_KEY` on the doors and the owner, both or neither;
   - `GATEWAY_KEYRING` on the gateway, `FILES_KEYRING` on the files Worker;
   - `WORKOS_API_KEY`, `E2B_WEBHOOK_SECRET` and, if set, `INBOUND_SLACK_SIGNING_SECRET` on
     the doors;
   - `E2B_API_KEY` on the sandbox.

   A held secret is never replaced unless `rotate` names it. A held keyring is never
   generated or replaced. It is rotated only by adding a key: run `keyring-add` on your copy
   of the stored keyring, store the result, then run with `rotate` naming it. The
   kit refuses a rotated keyring with fewer than two keys, or whose active key is not among
   them. **Residual:** Cloudflare never shows a held secret, so the kit cannot read the
   deployed keyring back. A two-key ring that dropped the deployed key would pass its check
   and make every sealed credential (or, for `FILES_KEYRING`, every chunk) unreadable. `keyring-add` keeps every old key by
   construction, so rotate only with it, on your copy of the stored keyring.
4. **Six Workers deployed with no public route:** files, gateway, hooks, sandbox, owner,
   doors. None has a `workers.dev` or preview URL. A Worker's first deploy is
   `wrangler deploy`. Later deploys upload a version, then deploy it (`wrangler versions
   upload`, then `versions deploy`), which never touches a route. Wrangler refuses a version
   upload with a pending Durable Object migration (read in wrangler 4.154's source). Name that
   Worker in `migrate` to deploy it whole; `migrate` never takes the doors.
5. **The control record, written only when the API answered that it is absent.** It starts
   with everything closed: outside access off, maintenance admitting no owner, inbound events
   off. It also holds your tier numbers: free 5 runs and 25 GB, paid 10 runs and 250 GB. If the
   record is there, the kit leaves it alone. If the read fails, the whole run stops.
6. **One WorkOS redirect URI, registered if WorkOS's listing lacks it:**
   `https://tinyassets.io/oauth/upstream/callback`. The kit never deletes one.
7. **The handles canary** (Fact 11) runs against `tinyassets.io/mcp`, with
   `TINYASSETS_WIKI_CANARY_TOKEN`. No route moves, so it should show the live connector
   exactly as before.

## What it does not change

- No DNS record and no route on `tinyassets.io`. The live connector keeps serving everyone.
- No owner data. No connector re-authorizes.
- No rate limit and no WAF rule. The platform sets no number on any caller. Cloudflare's
  own DDoS protection is in front of every Worker.

## Later steps, each its own approved run

- **Mode `routes`: the cutover's routes.** It does everything `dark` does. Then it hands
  the doors' routes over (`/mcp*`, `/api/*`, `/hooks/inbound/*`, `/app/api/*`,
  `/authorize*`, `/oauth/*`, `/.well-known/oauth-*`) one route at a time, through
  Cloudflare's routes API, so no path is ever served by nothing:
  - A pattern another Worker holds is moved to the doors in place (`PUT`, one change). Today
    that is the old proxy's (`tinyassets-mcp-proxy`) `mcp*`.
  - A pattern nobody holds is added (`POST`).
  - Cloudflare sends each URL to the most specific pattern that matches (its routes page).
    So the old proxy's narrower `.well-known/oauth-protected-resource*` and
    `.well-known/oauth-authorization-server*` would keep their paths from the doors'
    `.well-known/oauth-*`. Each is deleted only after the doors' wider pattern exists, so
    its paths pass straight to the doors.
  - The old proxy's wider `app*` and `api*` stay. The doors' narrower `app/api/*` and
    `api/*` already take their own paths, and `/app` itself stays with the proxy.
  - A refused change stops the run and says which steps are done.

  It then adds the `egress.tinyassets.io` custom domain (`wrangler triggers deploy`) and
  runs the handles canary. Every connector then signs in again once. Update the handles
  canary's tool list for the new door first.
- **Optional: a WAF rate-limiting rule of your own.** The platform sets no number on any
  caller. If you ever want a per-IP backstop at the edge, make one zone rule in the
  dashboard (Free allows one, Pro two), with your own number. It is your setting, recorded
  as such. The zone's plan is **unverified** here.
- **Mode `h2`: H2's probes alone**, once a probe owner exists. They deploy nothing. Store
  `TA_PROBE_KEY` and `TA_PROBE_KEY_B` (two owners' API keys), plus `TA_PROBE_APP_TOKEN` and
  `TA_PROBE_KEY_ID` for the revoke probe, as `production` secrets. It prints one row per
  probe.
- **Open the gates** in the control record when you are ready: canary owners first, then
  `outside: "on"`, and `inbound: true` for `/hooks/inbound`.
- **Later deploys change the Workers one after another, never at once.** The kit deploys
  `files, gateway, hooks, sandbox, owner, doors` in turn (`WORKERS` in `deploy.ts`), so
  between two of them old and new versions serve together. A change to a contract between
  them needs either an order in which each step is compatible with both sides, or a
  maintenance window (the control record's maintenance gate closed for the run). Egress PR
  3a (admit, then reserve) is such a change: the open request lost its `model` and `tier`, and
  `EgressGateway.forward` takes a `reserve` callback in place of its metering, so a new
  gateway behind old doors answers `internal` (settled `failed`, nothing sent) and a new
  owner refuses an old door's open. It ships in the first deploy, where nothing serves yet; a
  later change of that kind takes the window.

## Registrations you make by hand at the providers

The registrations are data in `platform/doors/redirect-registrations.json`, one per source.

| Source | Redirect URI to register | How the provider matches it |
|---|---|---|
| `chatgpt` (parked) | `https://tinyassets.io/oauth/connect/callback/chatgpt` | exactly |
| `openrouter_user_models_v1` | nothing to register | any callback |
