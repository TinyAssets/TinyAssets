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

Done already (2026-11-05): Workers Paid; the Cloudflare token's R2, KV, Queues and Workers
Routes edit (the `plan` job lists R2, KV and queues, so a missing scope stops it);
`E2B_API_KEY` in `production` and nowhere else; `production` limited to `main`, with no
required reviewer.

The rest runs on your own computer, by your Claude session with your own `gh` login. It
runs `scripts/platform_setup.py` (Windows: `python`); every value is set only where it is
absent, goes to GitHub on stdin, and is never shown. Your part is in bold.

1. `python scripts/platform_setup.py secrets`: `ROUTING_MAC_KEY`, `E2B_WEBHOOK_SECRET`,
   `GATEWAY_KEYRING` and `FILES_KEYRING` (each keyring made in memory, in the shape
   `deploy.ts keyring-new` writes).
2. **Make a production WorkOS API key** in the WorkOS dashboard (API Keys, the production
   environment; name it `github-production`) and **copy it**: it goes from your clipboard
   straight to the script, never into the chat or a command line. Two steps:
   - `Get-Clipboard | python scripts/platform_setup.py workos` in PowerShell, or from Git
     Bash (the shell a Windows Claude session usually runs)
     `powershell.exe -NoProfile -Command Get-Clipboard | python scripts/platform_setup.py workos`,
     stores it in `production` if `production` holds none, after checking it starts
     `sk_live_`, and prints when `production`'s key was last updated. A held key is never
     replaced.
   - Once that time is the key you just made, `python scripts/platform_setup.py workos
     --delete-repo-copies` deletes the repository-level `WORKOS_API_KEY` and
     `WORKOS_AUTHKIT_DOMAIN` (only `production` jobs read them). The host keeps its own key.
3. **Make the review App.** Open the review App page (`platform/deploy/review-app.html`),
   click its button, then **Create GitHub App** on GitHub (you must be an owner of the
   organization). GitHub lands on the repository with `?code=` in the address bar: give
   that address to your Claude session and nowhere else, since whoever turns the code in
   first gets the App's private key. Within the hour it runs
   `python scripts/platform_setup.py review-app --code "<address>"`: the main-only `review`
   environment (made when absent), `REVIEW_APP_PRIVATE_KEY` there and the variable
   `REVIEW_APP_ID`. **Install the App** on this repository alone, from the link it prints.
4. After this change is on `main` and one uptime canary run has passed there (every five
   minutes): `python scripts/platform_setup.py variables` sets `OWNER_ISSUER`,
   `WORKOS_CLIENT_ID` and `OWNER_TOKEN_CLAIMS` (below).
5. **Approve** the session's `gh workflow run platform-deploy.yml -f mode=plan`, and read
   the plan.
6. A session pins the migration lock (below) and merges it. Then **approve**
   `gh workflow run platform-deploy.yml -f mode=dark`.

Before the cutover only, not for `dark`: the AuthKit GitHub sign-in app (absent from the
login page today; Google is live), or a decision to drop GitHub sign-in; and the optional
Slack app (`INBOUND_SLACK_SIGNING_SECRET`). Mode `routes` enables E2B's lifecycle webhook
itself, after the routes.

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
  the variable is set), and `platform_setup.py variables` stores it as `OWNER_TOKEN_CLAIMS`. That is AuthKit's
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
7. **E2B's lifecycle webhook** (`https://tinyassets.io/hooks/e2b/lifecycle`, matched by
   its URL, listed with the checks before any change): registered **disabled** if E2B's
   listing lacks it, and the run that registers it sets the doors' `E2B_WEBHOOK_SECRET` from
   `production` too, so the two start equal. After that its secret changes only with the
   doors' (`rotate` naming `E2B_WEBHOOK_SECRET`): E2B never shows a hook's secret, so the kit
   cannot compare them. It is never disabled or deleted.
8. **The handles canary** (Fact 11) runs against `tinyassets.io/mcp`, with
   `TINYASSETS_WIKI_CANARY_TOKEN`. No route moves, so it should show the live connector
   exactly as before.

## What it does not change

- No DNS record and no route on `tinyassets.io`. The live connector keeps serving everyone.
- No owner data. No connector re-authorizes.
- No rate limit and no WAF rule. The platform sets no number on any caller. Cloudflare's
  own DDoS protection is in front of every Worker.

## Later steps, each its own approved run

- **Mode `routes`: the cutover's routes.** It does everything `dark` does. Then it hands
  the doors' routes over (`/mcp*`, `/api/*`, `/hooks/inbound/*`, `/hooks/e2b/*`,
  `/app/api/*`, `/authorize*`, `/oauth/*`, `/.well-known/oauth-*`) one route at a time, through
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

  It then adds the `egress.tinyassets.io` custom domain (`wrangler triggers deploy`),
  enables E2B's lifecycle webhook now that the doors serve its path, and runs the handles
  canary. Every connector then signs in again once. Update the handles
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
