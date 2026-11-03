# The dark deploy: what it changes

One command, run by you: `node --experimental-strip-types platform/deploy/deploy.ts`.

Run it once without `--yes` first. That run changes nothing. It prints every change, and you
read the list. Then run it again with `--yes`.

## Before you run it

- Slice S (the sandbox Worker) has landed on `main`. The script stops without it.
- You are on the production host, or have its values in your shell: `CLOUDFLARE_API_TOKEN`,
  `CLOUDFLARE_ACCOUNT_ID`, `WORKOS_API_KEY`, `WORKOS_CLIENT_ID`, `E2B_API_KEY`, and
  `OWNER_ISSUER`, AuthKit's production issuer URL. The script reads `/etc/tinyassets/env`
  when it is there and never prints a value.
- The Cloudflare plan is Workers Paid. The owner Worker needs it for its one million
  subrequests and for Durable Objects on SQLite.
- AuthKit's production Google and GitHub apps exist (WorkOS dashboard).

## What `--yes` changes

1. **AuthKit's issuer, checked.** It reads the issuer's discovery document. If its issuer is
   not exactly `OWNER_ISSUER`, or it lists no `auth_time` claim, the script stops before it
   changes anything.
2. **Storage, created when missing:** three KV namespaces (`tinyassets-oauth`,
   `tinyassets-directory`, `tinyassets-control`), one R2 bucket (`tinyassets-files`), and
   the sandbox's queue (`tinyassets-sandbox-ring`).
3. **Secrets, set when missing:**
   - a new routing key on the doors and the owner Workers, the same key on both;
   - a new keyring on the gateway;
   - `WORKOS_API_KEY` on the doors;
   - `E2B_API_KEY` on the sandbox;
   - the Slack signing secret on the doors, if you set one.
   Nothing is rotated unless you pass `--rotate NAME`.
4. **Six Workers deployed with no public route:** files, gateway, hooks, sandbox, owner,
   doors. No `workers.dev` or preview URL. Nobody outside can reach them yet.
5. **The control record, written only if there is none.** Everything is closed: outside
   access off, maintenance admitting no owner, inbound events off. It also holds your tier
   numbers: free 5 runs and 25 GB, paid 10 runs and 250 GB.
6. **One WorkOS redirect URI registered, if missing:**
   `https://tinyassets.io/oauth/upstream/callback`. The script never deletes one.
7. **The handles canary** (Fact 11) runs against `tinyassets.io/mcp`. The dark deploy moves
   no route, so it should show the live connector exactly as before.

## What it does not change

- No DNS record. No route on `tinyassets.io`. The live connector keeps serving everyone.
- No owner data. No connector re-authorizes.

## Later steps, each its own OK from you

- **`--yes --routes`: the cutover's routes.** It attaches the doors' routes (`/mcp`, `/api/*`,
  `/hooks/inbound/*`, `/app/api/*`, `/authorize`, `/oauth/*`, `/.well-known/oauth-*`) and the
  `egress.tinyassets.io` custom domain. This takes those paths from the old Worker
  (`tinyassets-mcp-proxy`). Every connector then signs in again once. The handles canary's
  tool list must be updated for the new door first.
- **`--yes --h2`: H2's probes**, once a probe owner exists. Set `TA_PROBE_KEY` and
  `TA_PROBE_KEY_B` (two owners' API keys), plus `TA_PROBE_APP_TOKEN` and `TA_PROBE_KEY_ID`
  for the revoke probe. It prints one row per probe.
- **Open the gates** in the control record when you are ready: canary owners first, then
  `outside: "on"`, and `inbound: true` for `/hooks/inbound`.

## Registrations you make by hand at the providers

The registrations are data in each source's record, as `oauth.redirect_registration`.

| Source | Redirect URI to register | How the provider matches it |
|---|---|---|
| `chatgpt` (parked) | `https://tinyassets.io/oauth/connect/callback/chatgpt` | exactly |
| `openrouter_user_models_v1` | nothing to register | any callback |
