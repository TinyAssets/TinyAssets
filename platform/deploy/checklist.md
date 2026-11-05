# The dark deploy: what it changes

The kit is `platform/deploy/deploy.ts`. It runs from the manual workflow **Platform deploy
(manual, founder-approved)** (`.github/workflows/platform-deploy.yml`), on `main` only, in
three jobs:

1. **`plan`** prints what the run would change to the job summary. It holds only the
   Cloudflare token and the repo's variables, and changes nothing.
2. **`pair`** (modes `dark` and `routes`) checks the pinned E2B template with the runner this
   commit ships, unless E2B already holds their pair's tag (`deploy.ts --pair`: three
   throwaway sandboxes, up to about 50 minutes, the E2B key alone).
3. **`apply`** runs the kit with `--yes`, in the main-only `production` environment.

Starting a run with a mode other than `plan` is the approval. Do not add a required reviewer
to `production`: the uptime canary, the DR drill, deploy-prod and the host installs run there
on their own, and a reviewer would hold every one of them for a click.

**The go-ahead deploy** is the first `dark` run. It turns on the platform's storage, secrets,
six Workers and control record (every gate closed), and nothing public: it moves no route,
so `tinyassets.io` keeps serving the old stack. The doors go live only with mode `routes`,
at the cutover.

## Before you run it

Your steps, in order. The rest is a workflow's or a session's.

1. **Cloudflare: done** (founder, 2026-11-05). The account is on Workers Paid, and the
   token behind `CLOUDFLARE_API_TOKEN` has R2 Storage, KV Storage and Queues edit. The
   `plan` job lists all three, so a missing scope stops it. Zone: Workers Routes Edit is
   still owed before mode `routes`.
2. **GitHub: Settings, Environments, `production`, Add secret**, twice:
   - `E2B_API_KEY`, from the E2B dashboard of the team that holds the template. No run has
     shown one at repository scope; if one is there, the setup moves it here and deletes
     that copy.
   - `SETUP_PAT`: a fine-grained token for this repository only, expiring in one day, with
     Actions: read, and Environments, Secrets and Variables: read and write. The setup
     deletes this secret when it ends; the token itself expires the next day.
3. **The review App, on your own computer.** Open the review App page
   (`platform/deploy/review-app.html`) and click its button, then **Create GitHub App** on
   GitHub (you must be an owner of the organization). GitHub lands on the repository with
   `?code=` in the address bar. Give that address to Claude on your computer and nowhere
   else: whoever turns the code in first gets the App's private key. It runs
   `python3 scripts/review_app_setup.py --code '<address>'` with your own `gh` within the
   hour: the main-only `review` environment (made when absent), `REVIEW_APP_PRIVATE_KEY`
   there and `REVIEW_APP_ID`. Then install the App on this repository alone, from the link
   it prints (one click).
4. **Actions, Platform setup, Run workflow** on `main`. It makes or moves, each only where
   absent and never printed:
   - `ROUTING_MAC_KEY`, `GATEWAY_KEYRING` and `FILES_KEYRING` (`deploy.ts keyring-new`);
   - `WORKOS_API_KEY`, read from the production host;
   - `E2B_WEBHOOK_SECRET`, and E2B's lifecycle webhook, registered **disabled**;
   - the variables `OWNER_ISSUER`, `WORKOS_CLIENT_ID` and `OWNER_TOKEN_CLAIMS` (below).

   Its summary lists each name and its status. A rerun needs a new `SETUP_PAT` and changes
   nothing already set.
5. **Platform deploy, mode `plan`.** Read it.
6. **A session pins the migration lock** (below) and merges it. Then **Platform deploy, mode
   `dark`**.

Before the cutover only, not for `dark`: the AuthKit GitHub sign-in app (absent from the
login page today; Google is live), or a decision to drop GitHub sign-in; the optional Slack
app (`INBOUND_SLACK_SIGNING_SECRET`); `tinyassets.io/hooks/e2b/*` added to `ROUTES` in
`deploy.ts`; then E2B's webhook enabled.

What the kit reads, all from the store, never a host:

- `production` secrets: `WORKOS_API_KEY`, `E2B_API_KEY`, `ROUTING_MAC_KEY`,
  `GATEWAY_KEYRING`, `FILES_KEYRING`, `E2B_WEBHOOK_SECRET`, `TINYASSETS_WIKI_CANARY_TOKEN`
  (the handles canary's, there since 2026-10-14) and, for Slack events,
  `INBOUND_SLACK_SIGNING_SECRET`.
- The repository secret `CLOUDFLARE_API_TOKEN`. The account is the `tinyassets.io` zone's,
  read from the zone (`zoneOf`), so no account id is stored.
- The repository variables `OWNER_ISSUER`, `WORKOS_CLIENT_ID` and `OWNER_TOKEN_CLAIMS`.

More on three of them:

- **The migration lock, pinned at the commit you deploy.** The kit refuses to run unless
  `platform/owner/migrations.json` marks the first deploy and pins every migration the code
  ships, in order. To pin it:
  1. **Who and when.** A session does it once, in the same sitting as the first deploy.
  2. **Pin.** On a branch made from the current `main`, run this in `platform/owner`:
     `PIN_MIGRATIONS=$(git rev-parse HEAD) npx vitest run --project guards guards/migrations.test.ts`.
     It writes `platform/owner/migrations.json`: the first-deploy marker (time and commit)
     and a digest for every migration.
  3. **Land it.** Commit only that file and merge it to `main` by pull request. The workflow
     runs only from `main`, and the kit reads the lock there.
  4. **Every later deploy that adds a migration** repeats steps 2 and 3 first. The pin only
     appends, and the kit refuses until it is done.
  5. **Pin only when you deploy.** Pinning freezes the baseline for good: from then on a
     schema change is a new migration, even if no deploy follows.
- **`auth_time`, settled before anything changes.** Every sign-in needs AuthKit's tokens to
  carry `auth_time`, and AuthKit's discovery lists no `claims_supported`. So the uptime
  canary keeps a receipt of its renewed AuthKit token's `iss` and `auth_time` alone (until
  the variable is set), and the setup stores it as `OWNER_TOKEN_CLAIMS`. That is AuthKit's
  access token; the doors check the ID token, so the first real sign-in at the cutover is
  the final test (`oidc.ts` refuses loudly). An absent field counts as unknown, and unknown
  stops the kit. If the token has no `auth_time`, the way on is to have AuthKit send it
  (`max_age=0`).
- **The sandbox's template is built** (`E2B_TEMPLATE` in `platform/sandbox/wrangler.jsonc`,
  pinned in #5428). The kit checks E2B has a ready build, and the `pair` job checks the pair.

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
