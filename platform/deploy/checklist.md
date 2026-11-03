# The dark deploy: what it changes

The kit is `platform/deploy/deploy.ts`. It runs from the manual workflow **Platform deploy
(manual, founder-approved)** (`.github/workflows/platform-deploy.yml`), on `main` only, in
the repo's main-only `production` environment. Your dispatch of a mode other than `plan` is
the kit's `--yes`. Every run prints its plan to the job summary first.

Run it once with mode `plan` and read the list; it changes nothing. Then run it with mode
`dark`.

## Before you run it

- **The environment.** The `production` environment (`docs/host-actions.md`) holds the
  secrets. Add yourself as its required reviewer if you want GitHub to hold each run for
  your approval as well; whether it has one today is **unverified** here. Store these as
  its secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `WORKOS_API_KEY`,
  `WORKOS_CLIENT_ID`, `OWNER_ISSUER` (AuthKit's production issuer URL), `E2B_API_KEY`,
  `ROUTING_MAC_KEY`, `GATEWAY_KEYRING`, and, if you want Slack events,
  `INBOUND_SLACK_SIGNING_SECRET`. The kit reads nothing from a host.
  - `WORKOS_API_KEY` lives on the production host today. Move it once, the way
    `copy-secrets-to-production.yml` moved the others.
  - `ROUTING_MAC_KEY` is one random 32-byte value you make and store. The kit never makes it.
  - `GATEWAY_KEYRING`: make the first one locally with
    `node --experimental-strip-types platform/deploy/deploy.ts keyring-new <file>`, store the
    file's contents, then delete the file.
- **`auth_time`, settled before anything changes.** Every sign-in needs AuthKit's tokens to
  carry `auth_time`. Store one real AuthKit token's claims as `OWNER_TOKEN_CLAIMS`, a JSON
  object (the `fetch-prod-logs` workflow shows a claim receipt). Without it, the kit uses the
  discovery's `claims_supported`. A field that is absent counts as unknown, and unknown stops
  the kit; it never passes. If the token has no `auth_time`, the kit stops and you choose:
  ask AuthKit for it (`max_age=0`), or accept the binder's clock at the code exchange.
- **The sandbox's template is built.** `E2B_TEMPLATE` in `platform/sandbox/wrangler.jsonc`
  must have a ready build at E2B (`platform/sandbox/template/build.py`). The kit checks this
  through E2B's API and stops if it is missing.
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
   - `GATEWAY_KEYRING` on the gateway;
   - `WORKOS_API_KEY` and, if set, `INBOUND_SLACK_SIGNING_SECRET` on the doors;
   - `E2B_API_KEY` on the sandbox.

   A held secret is never replaced unless `rotate` names it. A held `GATEWAY_KEYRING` is
   never generated or replaced. It is rotated only by adding a key: run `keyring-add` on your
   copy of the stored keyring, store the result, then run with `rotate: GATEWAY_KEYRING`. The
   kit refuses a rotated keyring with fewer than two keys, or whose active key is not among
   them. Cloudflare never shows a held secret, so the kit cannot compare against the old
   keyring; `keyring-add` keeps every old key by construction.
4. **Six Workers deployed with no public route:** files, gateway, hooks, sandbox, owner,
   doors. None has a `workers.dev` or preview URL. A Worker's first deploy is
   `wrangler deploy`. Later deploys upload a version, then deploy it (`wrangler versions
   upload`, then `versions deploy`), which never touches a route. Wrangler refuses a version
   upload with a pending Durable Object migration (read in wrangler 4.154's source). Name that
   Worker in `migrate` to deploy it whole; `migrate` never takes the doors.
5. **The control record, written only when the API answered that it is absent.** It starts
   with everything closed: outside access off, maintenance admitting no owner, inbound events
   off. It also holds your tier numbers: free 5 runs and 25 GB, paid 10 runs and 250 GB. If the
   record is there, or the read fails, the kit leaves it alone.
6. **One WorkOS redirect URI, registered if WorkOS's listing lacks it:**
   `https://tinyassets.io/oauth/upstream/callback`. The kit never deletes one.
7. **The handles canary** (Fact 11) runs against `tinyassets.io/mcp`. No route moves, so it
   should show the live connector exactly as before.

## What it does not change

- No DNS record and no route on `tinyassets.io`. The live connector keeps serving everyone.
- No owner data. No connector re-authorizes.
- No WAF rule. See "Later steps".

## Later steps, each its own approved run

- **Mode `routes`: the cutover's routes.** It attaches the doors' routes (`/mcp*`, `/api/*`,
  `/hooks/inbound/*`, `/app/api/*`, `/authorize*`, `/oauth/*`, `/.well-known/oauth-*`) and the
  `egress.tinyassets.io` custom domain, with `wrangler triggers deploy`.
  - The routes stay with their current Worker until you release them. Wrangler refuses a
    pattern another Worker holds ("Can't deploy routes that are assigned to another worker",
    wrangler 4.154's source). When patterns overlap, Cloudflare's routes page says the most
    specific one wins. So the old proxy's `.well-known/oauth-protected-resource*` would keep
    that path from the doors.
  - Before deploying anything, the kit reads the zone's routes. It stops on any route of
    another Worker that overlaps one of the doors'. Today that is the old proxy
    (`tinyassets-mcp-proxy`): `mcp*`, `app*`, `api*`, `.well-known/oauth-protected-resource*`
    and `.well-known/oauth-authorization-server*`.
  - Release those yourself, as their own approved step, then run `routes`.
  - Every connector then signs in again once. Update the handles canary's tool list for the
    new door first.
- **One WAF rate-limiting rule** on `/authorize` and `/oauth/token`, as a per-IP backstop at
  the edge (research 3a). You make it in the dashboard. Free allows one rule, Pro two. The
  zone's plan is **unverified** here; check it in the dashboard before making the rule.
- **Mode `h2`: H2's probes**, once a probe owner exists. Store `TA_PROBE_KEY` and
  `TA_PROBE_KEY_B` (two owners' API keys), plus `TA_PROBE_APP_TOKEN` and `TA_PROBE_KEY_ID` for
  the revoke probe. It deploys as `dark` does, then prints one row per probe.
- **Open the gates** in the control record when you are ready: canary owners first, then
  `outside: "on"`, and `inbound: true` for `/hooks/inbound`.

## Registrations you make by hand at the providers

The registrations are data in `platform/doors/redirect-registrations.json`, one per source.

| Source | Redirect URI to register | How the provider matches it |
|---|---|---|
| `chatgpt` (parked) | `https://tinyassets.io/oauth/connect/callback/chatgpt` | exactly |
| `openrouter_user_models_v1` | nothing to register | any callback |
