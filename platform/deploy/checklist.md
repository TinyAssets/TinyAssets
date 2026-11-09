# The dark deploy: what it changes

The kit is `platform/deploy/deploy.ts`. It runs from the manual workflow **Platform deploy
(manual, founder-approved)** (`.github/workflows/platform-deploy.yml`), on `main` only, in
three jobs:

1. **`plan`** prints what the run would change to the job summary. It holds only the
   Cloudflare token and the repo's variables, and changes nothing.
2. **`pair`** (modes `dark` and `routes`) checks the pinned E2B template with the runner this
   commit ships, unless E2B already holds their pair's tag (`deploy.ts --mode pair`: three
   throwaway sandboxes, up to about 50 minutes, the E2B key alone).
3. **`apply`** runs the mode with `--yes`, in the main-only `production` environment.

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

**The Cloudflare token's scopes for `routes` and `handback`: nothing new is owed, if its
record holds.**
- **What the kit needs.** `routes` lists the egress host's DNS records before any change.
  Cloudflare's API reference accepts either DNS Read or DNS Write (Edit) for that listing.
  `handback` deletes the DNS record the custom domain made, if the detach leaves it, which
  needs DNS Write. So **DNS Edit covers both**. Attaching and detaching the custom domain
  need **Workers Scripts edit** on the account, which the kit's deploys need too.
- **What the token holds.** The infra-ops reference (`.claude/skills/infra-ops/references/
  cloudflare.md`) records this secret with Zone DNS Edit and Account Workers Scripts edit,
  and `emergency-dns.yml` writes DNS records with it. A run has shown it reading DNS:
  `cloud-only-preflight.yml` (run 35704397255, 2026-09-22) read the zone's records with this
  secret, and `internal_origin_dns` passed. No run has written DNS with it yet.
- **How to check without reading the token.**
  - In the Cloudflare dashboard, My Profile → API Tokens lists each token's permissions and
    never shows its value.
  - The `routes` plan job's DNS listing proves the read: without it, the plan job stops with
    nothing changed.
  - If DNS Edit were missing, only the handback's last step (that one DNS delete) would fail,
    loudly, with every route already back.

The rest runs on your own computer, by your Claude session with your own `gh` login. It
runs `scripts/platform_setup.py` (Windows: `python`); every value is set only where it is
absent, goes to GitHub on stdin, and is never shown. Your part is in bold.

1. `python scripts/platform_setup.py secrets`: the secrets `platform/deploy/platform.json`
   says the setup makes (`made`; each keyring made in memory, in the shape
   `deploy.ts keyring-new` writes).
2. **Make a production WorkOS API key** in the WorkOS dashboard (API Keys, the production
   environment; name it `github-production`) and **copy it**: it goes from your clipboard
   straight to the script, never into the chat or a command line. Two steps:
   - `Get-Clipboard | python scripts/platform_setup.py workos` in PowerShell, or from Git
     Bash (the shell a Windows Claude session usually runs)
     `powershell.exe -NoProfile -Command Get-Clipboard | python scripts/platform_setup.py workos`,
     stores it in `production` if `production` holds none, after checking it starts
     `sk_` (not `sk_test_`), and prints when `production`'s key was last updated. A held key is never
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
4. `python scripts/platform_setup.py variables` sets `OWNER_ISSUER` and `WORKOS_CLIENT_ID`.
5. **Approve** the session's `gh workflow run platform-deploy.yml -f mode=plan`, and read
   the plan.
6. A session pins the migration lock (below) and merges it. Then **approve**
   `gh workflow run platform-deploy.yml -f mode=dark`.

Before the cutover only, not for `dark`: the AuthKit GitHub sign-in app (absent from the
login page today; Google is live), or a decision to drop GitHub sign-in; and the optional
Slack app (`INBOUND_SLACK_SIGNING_SECRET`). Mode `routes` enables E2B's lifecycle webhook
itself, after the routes.

What the kit reads, all from the store, never a host:

- `production` secrets: every secret a Worker's config requires (`secrets.required` in its
  `wrangler.jsonc`), the shared apps' signing secrets (`platform/doors/shared-apps.json`),
  `TINYASSETS_WIKI_CANARY_TOKEN` (the handles canary's old side, there since 2026-10-14)
  and H2's probe keys (`deploy/h2-probes.ts`). `TA_PROBE_KEY` is also the handles canary's
  new side. The workflow's guard holds its apply job to that list.
- The repository secret `CLOUDFLARE_API_TOKEN`. The account is the doors' zone's, read from
  the zone (`Cloudflare.zone`), so no account id is stored.
- The repository variables `OWNER_ISSUER` and `WORKOS_CLIENT_ID`.

More on two of them:

- **The migration locks, pinned at the commit you deploy.** The kit refuses to run unless
  each lock (`platform/owner/migrations.json`, and the sandbox Worker's
  `platform/sandbox/migrations/<class>.json`, both pinned) marks the first deploy and pins
  every migration the code ships, in order. To pin one:
  1. **Who and when.** A session does it once, in the same sitting as the first deploy.
  2. **Pin.** On a branch made from the current `main`, run this in `platform/owner` (or
     `platform/sandbox`):
     `PIN_MIGRATIONS=$(git rev-parse HEAD) npx vitest run --project guards guards/migrations.test.ts`.
     It writes that Worker's locks: the first-deploy marker (time and commit) and a digest
     for every migration.
  3. **Land it.** Commit only those files and merge them to `main` by pull request. The workflow
     runs only from `main`, and the kit reads the lock there.
  4. **Every later deploy that adds a migration** repeats steps 2 and 3 first. The pin only
     appends, and the kit refuses until it is done.
  5. **Pin only when you deploy.** Pinning freezes the baseline for good: from then on a
     schema change is a new migration, even if no deploy follows.
- **The sandbox's template is built** (`E2B_TEMPLATE` in `platform/sandbox/wrangler.jsonc`,
  pinned in #5428). The kit checks E2B has a ready build, and the `pair` job checks the pair.

## What mode `dark` changes

Each step reads before it writes. A failed listing or read stops the kit with its error.
It never counts as "absent".

1. **Checks, before any change.** The issuer's discovery must name `OWNER_ISSUER` exactly
   and an https JWKS, the key set the doors check the sign-in's token against. The E2B
   template must be ready. Then the callback is listed at WorkOS, and registered if missing
   (step 6, still before any Cloudflare change), and only then the early sign-in check:
   the WorkOS authorize URL the doors build (User Management's, for `WORKOS_CLIENT_ID` and
   the callback, with a throwaway challenge; not the doors' own `/authorize`) must answer a
   302 to AuthKit's sign-in on `OWNER_ISSUER`'s host. A `client-id-invalid` or
   `redirect-uri-invalid` page stops the kit, naming it. Mode `plan` cannot list the
   callback, so there a `redirect-uri-invalid` answer reads "dark registers it"; every
   other refusal still stops. Any miss stops the kit. The access token's exact `iss` and its
   `auth_time` only a real sign-in shows: mode `probe` settles them, after `dark` and
   before `routes`.
2. **Storage, created only when its listing answered and lacks it:** the KV namespaces
   `platform/deploy/platform.json` titles, one for each binding the configs make, and the
   R2 buckets and the queues the Workers' configs name (a queue its producer's).
3. **Secrets, set only when the Worker's secret listing answered and lacks them** (or the API
   says the Worker does not exist yet). Values come from the environment's secrets: each
   Worker's `secrets.required` (its `wrangler.jsonc`), and on the doors, if set, each shared
   app's signing secret (`platform/doors/shared-apps.json`). A secret two Workers require
   (the routing key, the doors' and the owner's) is set on both or neither.

   A held secret is never replaced unless `rotate` names it. A held keyring is never
   generated or replaced. It is rotated only by adding a key: run `keyring-add` on your copy
   of the stored keyring, store the result, then run with `rotate` naming it. The
   kit refuses a rotated keyring with fewer than two keys, or whose active key is not among
   them. **Residual:** Cloudflare never shows a held secret, so the kit cannot read the
   deployed keyring back. A two-key ring that dropped the deployed key would pass its check
   and make every sealed credential (or, for `FILES_KEYRING`, every chunk) unreadable. `keyring-add` keeps every old key by
   construction, so rotate only with it, on your copy of the stored keyring.
4. **Every Worker deployed with no public route,** each after the Workers it binds (`WORKERS`,
   `deploy/facts.ts`). None has a `workers.dev` or preview URL. A Worker's first deploy is
   `wrangler deploy`. Later deploys upload a version, then deploy it (`wrangler versions
   upload`, then `versions deploy`), which never touches a route. Wrangler refuses a version
   upload with a pending Durable Object migration (read in wrangler 4.154's source). Name that
   Worker in `migrate` to deploy it whole; `migrate` never takes the doors.
5. **The control record, written only when the API answered that it is absent.** It starts
   with everything closed: outside access off, maintenance admitting no owner, inbound events
   off. It also holds your tier numbers: free 5 runs and 25 GB, paid 10 runs and 250 GB. If the
   record is there, the kit leaves it alone. If the read fails, the whole run stops.
6. **One WorkOS redirect URI, registered if WorkOS's listing lacks it:** the doors'
   callback (`signin.callback` in `platform/contract/identity.json`), with the checks in
   step 1, before any Cloudflare change, since the early sign-in check needs it. The kit
   never deletes one.
7. **E2B's lifecycle webhook** (the doors' `hook` in `identity.json`, matched by
   its URL, listed with the checks before any change): registered **disabled** if E2B's
   listing lacks it, and the run that registers it sets the doors' `E2B_WEBHOOK_SECRET` from
   `production` too, so the two start equal. After that its secret changes only with the
   doors' (`rotate` naming `E2B_WEBHOOK_SECRET`): E2B never shows a hook's secret, so the kit
   cannot compare them. It is never disabled or deleted.
8. **The handles canary** (Fact 11, `scripts/cutover_handles_canary.py --side auto`) runs
   against `tinyassets.io/mcp`. No route moves, so the old stack still answers: it runs the
   old stack's own check (`mcp_public_canary.py --assert-handles`, with
   `TINYASSETS_WIKI_CANARY_TOKEN`) and should show the live connector exactly as before.

## What it does not change

- No DNS record and no route on `tinyassets.io`. The live connector keeps serving everyone.
- No owner data. No connector re-authorizes.
- No rate limit and no WAF rule. The platform sets no number on any caller. Cloudflare's
  own DDoS protection is in front of every Worker.

## Later steps, each its own approved run

- **Mode `probe`: one real sign-in through the doors, between `dark` and `routes`.** Every
  sign-in at `routes` rests on three things only a real sign-in through the doors' own code
  shows: User Management's `authenticate` takes the API key as the client's secret, the
  access token's `iss` is the one the kit expects (`upstreamFrom`'s `token_issuer`,
  `https://api.workos.com/user_management/<client id>` by WorkOS's docs), and it carries a
  fresh `auth_time`. The probe:
  - runs only after `dark`: the doors deployed, the control record admitting nobody, and
    WorkOS listing the callback. It reads each live and stops otherwise. Before a route
    moves, it runs `dark`'s early sign-in check again;
  - hands the doors only the routes that take the sign-in's own two paths (`PROBE_ROUTES`,
    `deploy/facts.ts`), by the same handover. No Worker serves those paths today (they
    answer `404`), and the probe
    stops if one does. It deploys nothing and changes no gate. Then the handles canary;
  - prints one sign-in link, the doors' own app's (`/authorize`, its client, its
    `/app/signed-in` redirect, the `/app` resource and a fresh S256 challenge).

  **Open the link and sign in once.** Read where the browser ends:
  - **Pass:** the address begins
    `https://tinyassets.io/app/signed-in?error=temporarily_unavailable` (more follows
    it). The page is still the old app's and reads "Sign in to TinyAssets to continue":
    that is expected. Every check passed and the closed gate refused the sign-in, so no
    owner is made.
  - **Fail:** a `400` at `/authorize` (the doors could not read their app's client
    document); an error page at `error.workos.com` or AuthKit (the client or the callback
    refused); a `502` at `/oauth/upstream/callback` naming the failed check (the token
    endpoint's status and error code, `auth_time`, `client_id`...), the same line in the
    doors' log; or `/app/signed-in?error=access_denied` ("sign in again": `auth_time` over
    600 s old, so `max_age=0` did not force a fresh sign-in).

  If the `502` names the issuer, it quotes the `iss` the token carries. Only a WorkOS value
  (`https://api.workos.com/...` or `OWNER_ISSUER`) becomes `token_issuer` in `upstreamFrom`
  (`deploy.ts`, one line), a change of its own, then
  `dark` and the probe again. `routes`
  later finds both paths already the doors'. Until `routes`, never delete the control
  record (`CONTROL_KEY` in `contract/src/control.ts`): the doors read a missing record as open and would admit owners on
  these two paths.
- **Mode `routes`: the cutover's routes.** It does everything `dark` does. Then it hands
  the doors' routes over (`routes` in `platform/contract/identity.json`) one route at a
  time, through Cloudflare's routes API, so no path is ever served by nothing:
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

  It then adds the egress custom domain (`egress` in `identity.json`; `wrangler triggers
  deploy`),
  enables E2B's lifecycle webhook now that the doors serve its path, and runs the handles
  canary's unkeyed checks for the new side (`--side new --unkeyed`, below). Every
  connector then signs in again once.

  Before any change, `routes` keeps a **route record** in `tinyassets-control`. It holds
  the before-state of everything `routes` changes that `dark` does not:
  - every route of the zone as it read them, and the handover's changes;
  - the egress custom domain's Worker, or none;
  - whether E2B's lifecycle webhook was held, and enabled.

  It also holds the zone, the run id and the time, and no secret. The job summary names its
  id (the run's) and holds a copy. If the record cannot be written, the run stops with
  nothing changed. A re-run attempt of the same run keeps its first attempt's record.

  `routes` also stops before any change while `egress.tinyassets.io` is another Worker's
  custom domain or holds an address record (A, AAAA or CNAME). Run from the workflow,
  wrangler's custom-domain deploy replaces either, and no handback could give it back. The
  plan job shows this stop too.
- **Mode `handback`: the routes' rollback.** Dispatch it with `record` set to the id
  `routes`' summary names. Its plan job reads the record and the zone and changes nothing,
  so run it once right after `routes`, to see what a handback would do. The apply job puts
  back what `routes` changed, in reverse order:
  1. E2B's lifecycle webhook is disabled, unless the record has it enabled before. A hook
     `routes` registered is disabled, never deleted: a disabled hook sends nothing.
  2. The doors' egress custom domain is detached, unless the record has it attached before.
  3. The zone's routes go back as the record has them (the doors' patterns back to
     `tinyassets-mcp-proxy`), one change at a time, so no path the record served is ever
     served by nothing: the old proxy's deleted narrower routes are added back, `mcp*` is
     moved back in place, then the doors' added routes are deleted, each only while it is
     still the doors'.
  4. If the detach left the DNS record the attach made, that record is deleted: only it,
     matched exactly as the record holds it. A second `routes` after the rollback is then
     not stopped by it. This comes last, since the record serves no one, so a refused
     delete never holds the routes back. If `routes` stopped before it recorded the
     attach's DNS record, none is deleted, and the read-back holds the host to no address
     record, as it was before `routes`. A record left there is named for you to delete by
     hand.

  It changes no other route. Before any change, it holds the zone to the record. If a
  route the record or the handover holds was changed by someone after `routes`, or the egress
  host is another Worker's custom domain, it stops, naming each difference, with nothing
  changed. Put each back by hand as the record has it, then run it again. Its plan job
  shows this stop too.

  After its changes it reads each back, and stops, naming each difference,
  unless they are the record's. A rerun changes only what is not back yet, and a killed run
  is simply run again.

  It doesn't run the handles canary. Until the old stack is up, the routes it gave back
  serve nothing, so the canary would fail a correct handback. The order is:
  1. the handback;
  2. restart the old stack on its untouched volumes;
  3. the handles canary (Fact 11) for the old side: dispatch **Handles canary**
     (`handles-canary.yml`) with `side` `old`. Its token is a `production` secret no machine
     holds, so it runs there. It is the old stack's own `--assert-handles` check, and changes
     nothing.

  The run's last line says so.

  It leaves what `dark` made: storage, secrets, the Workers, the control record and the
  WorkOS redirect URI. They serve no one without the routes, the domain and the hook.
- **The handles canary across the cutover** (Fact 11): the workflow **Handles canary
  (manual, read-only)** (`handles-canary.yml`), dispatched with `side`. It changes nothing.
  Each side's handle set is data (`scripts/canary_handles.json`): the doors' is held to
  their tool table by a guard, and the old stack's to its own canary's list.
  - **Which side answers.** It reads the side twice: `/mcp`'s own anonymous answer (the old
    daemon's 401 carries a body, the doors' none) and the public resource document's
    authorization server (AuthKit, or `https://tinyassets.io`). If they disagree, a
    `routes` or `handback` stopped partway: it says so (exit 10). Run that again to the end
    first. `auto` holds the side both name. `old` or `new` holds the side asked for, and
    fails if the other answers.
  - **The old side** is the old stack's own check, `mcp_public_canary.py
    --assert-handles`, with `TINYASSETS_WIKI_CANARY_TOKEN`.
  - **The new side** checks three things:
    - an anonymous `initialize` is challenged, naming the doors' resource document;
    - that document names the doors' issuer;
    - the doors' handle set, one read-only `GET /api/tools/<name>` a name with
      `TA_PROBE_KEY`, the probe owner's API key: each doors tool is served (`405`), and each
      old-only handle is not (`404`).

    Without `TA_PROBE_KEY` it fails, since an unchecked set is not a pass. A closed gate
    (`503`) is named too. The set assumes the probe owner keeps the default policy (every
    tool, and code mode on): a narrowed one reads as drift.
  - **After `routes`, side `new`, in this order:**
    1. **The `routes` run's own canary.** It runs the new side's unkeyed checks alone
       (`--side new --unkeyed`): the doors answer `/mcp` and its document, and the old
       stack doesn't. It says the handle set is owed. It can't check the set itself: no
       probe owner's key exists until the doors serve `/app/api`, and the dark control
       record admits no one.
    2. **Open the control record for the probe owner, both gates:** `outside: "on"`,
       `ordinary: "canary"` and `canary: ["<the probe owner's WorkOS user id>"]`. The keyed
       `/api` passes only with `outside` on (the doors' `outsideOpen`), so with it off,
       step 4 gets a `503`. With `ordinary` still `canary`, this admits no one but the probe
       owner, inside or out.
       - Read the probe owner's WorkOS user id (`user_…`) from WorkOS before its first
         sign-in. The doors name an owner by that id as it stands.
       - Its first sign-in is then admitted automatically: the gate admits the listed id,
         and the doors admit a new owner at sign-up.
    3. **Create the probe owner** (tester B, signing in through the doors), then mint its
       API key in the owner app and store it as the `production` secret `TA_PROBE_KEY`.
       H2's probes use it too.
    4. **Dispatch Handles canary with `side` `new`.** That is verify's handle-set check.
  - **After `handback` and the old stack's restart, side `old`.**
- **Optional: a WAF rate-limiting rule of your own.** The platform sets no number on any
  caller. If you ever want a per-IP backstop at the edge, make one zone rule in the
  dashboard (Free allows one, Pro two), with your own number. It is your setting, recorded
  as such. The zone's plan is **unverified** here.
- **Mode `h2`: H2's probes alone**, once a probe owner exists. They deploy nothing. Store
  the probe keys `deploy/h2-probes.ts` names (`INPUTS`: two owners' API keys, plus an app
  token and a key id for the revoke probe) as `production` secrets. It prints one row per
  probe.
- **Open the gates** in the control record when you are ready: canary owners first, then
  `outside: "on"`, and `inbound: true` for `/hooks/inbound`.
- **Later deploys change the Workers one after another, never at once.** The kit deploys
  them in turn, each after the Workers it binds (`WORKERS` in `deploy/facts.ts`), so
  between two of them old and new versions serve together. A change to a contract between
  them needs either an order in which each step is compatible with both sides, or a
  maintenance window (the control record's maintenance gate closed for the run). Egress PR
  3a (admit, then reserve) is such a change: the open request lost its `model` and `tier`, and
  `EgressGateway.forward` takes a `reserve` callback in place of its metering, so a new
  gateway behind old doors answers `internal` (settled `failed`, nothing sent) and a new
  owner refuses an old door's open. It ships in the first deploy, where nothing serves yet; a
  later change of that kind takes the window.

## The import window's credentials

The importer's runner, **Import window (manual)** (`.github/workflows/import-window.yml`,
tasks.md §9 item 7), reads these `production` secrets. `census` and `clean` need none,
`bundle` needs the exclusion list alone, and `stage`, `verify`, `wipe`, `admit` and `dlq`
need the other four and the repository secret `CLOUDFLARE_ACCOUNT_ID`. A mode that lacks one
stops before any step and names it. **Mint them before the rehearsal, keep them through the
window, and revoke them after it.**

| `production` secret | What it is | Scope |
|---|---|---|
| `IMPORT_EXCLUSION_LIST` | Your principal and every canary owner's, one a line (`#` starts a comment). They start fresh: backed up, never imported. | none |
| `IMPORT_R2_ACCESS_KEY_ID` and `IMPORT_R2_SECRET_ACCESS_KEY` | The S3 key pair of one Cloudflare API token, made through the API (step 2). | Workers R2 Storage Bucket Item Write on `tinyassets-import-staging`, Workers R2 Storage Bucket Item Read on `tinyassets-files`: it can never write the live files bucket. The droplet's address only; an end date. |
| `IMPORT_CLOUDFLARE_API_TOKEN` | One account API token. | Account · Queues · Edit (send to `tinyassets-import`, add and use the HTTP pull consumer of `tinyassets-import-dlq`), Account · Workers KV Storage · Edit (list `tinyassets-directory` and delete a rehearsal's records). The droplet's address only; an end date. |
| `IMPORT_KEY` | 32 random bytes, base64url. | The same value as the doors' Worker secret `IMPORT_KEY` |

**Never give one of them a name another job reads.** An environment secret hides the
repository secret of the same name in every `production` job, so a `production` secret named
`CLOUDFLARE_API_TOKEN` would hand the import token to the platform deploy's apply job.

The steps, on your own computer, with your own `gh` login. `gh secret set <name> --env
production` asks for the value and does not show it, so no value enters the chat or a
command line:

1. **The exclusion list.** Read the principals from the old stack's records and
   `docs/host-actions.md`, then `gh secret set IMPORT_EXCLUSION_LIST --env production`. An
   empty list stops `bundle`.
2. **The R2 key, through Cloudflare's API.** The dashboard's R2 token form gives one
   permission to every bucket it names, which would let the key write `tinyassets-files`, every
   user's file chunks. One API token with two bucket-level policies cannot. You need:
   - **A short-lived token that may make tokens.** My Profile → API Tokens → Create Token →
     the "Create Additional Tokens" template (User · API Tokens · Edit), with a TTL ending
     today. Delete it after this step.
   - **Three values:**
     - the account id (`CLOUDFLARE_ACCOUNT_ID`);
     - the droplet's public IPv4. A job's container leaves through Docker's default bridge,
       which has no IPv6, so Cloudflare sees that address;
     - an end date just after the window, for example `2026-12-31T00:00:00Z`.

   In Git Bash, `read -rs` takes the bootstrap token without showing it, and curl reads its
   header from a file descriptor, so the token is in no command line:
   ```sh
   read -rs BOOT
   acct='<account id>'; ip='<droplet IPv4>'; until='<end date>'
   api=https://api.cloudflare.com/client/v4
   auth() { printf 'Authorization: Bearer %s\n' "$BOOT"; }
   groups="$(curl -sS -H @<(auth) "$api/user/tokens/permission_groups")"
   group() { printf %s "$groups" | python -c "import json,sys; print(next(g['id'] for g in json.load(sys.stdin)['result'] if g['name'] == sys.argv[1]))" "$1"; }
   write="$(group 'Workers R2 Storage Bucket Item Write')"
   rd="$(group 'Workers R2 Storage Bucket Item Read')"
   resp="$(python -c 'import json,sys
   w, r, a, ip, t = sys.argv[1:]
   b = "com.cloudflare.edge.r2.bucket." + a + "_default_"
   print(json.dumps({"name": "tinyassets-import-window-r2", "expires_on": t,
                     "condition": {"request_ip": {"in": [ip + "/32"]}},
                     "policies": [
                       {"effect": "allow", "permission_groups": [{"id": w}],
                        "resources": {b + "tinyassets-import-staging": "*"}},
                       {"effect": "allow", "permission_groups": [{"id": r}],
                        "resources": {b + "tinyassets-files": "*"}}]}))' \
     "$write" "$rd" "$acct" "$ip" "$until" \
     | curl -sS -X POST -H @<(auth) -H 'Content-Type: application/json' --data @- \
         "$api/user/tokens")"
   printf %s "$resp" | python -c "import json,sys; d=json.load(sys.stdin); sys.stdout.write(d['result']['id'] if d.get('success') else sys.exit('refused: ' + str(d.get('errors'))))" \
     | gh secret set IMPORT_R2_ACCESS_KEY_ID --env production
   printf %s "$resp" | python -c "import hashlib,json,sys; sys.stdout.write(hashlib.sha256(json.load(sys.stdin)['result']['value'].encode()).hexdigest())" \
     | gh secret set IMPORT_R2_SECRET_ACCESS_KEY --env production
   unset BOOT resp
   ```
   **Confirm, as you mint it, how R2 derives the S3 pair from a token.** This step takes the
   Access Key ID to be the token's `id`, and the Secret Access Key the SHA-256 (hex) of its
   `value`, from R2's authentication docs as recalled. No session here could reach
   developers.cloudflare.com to read them again. Check also that Bucket Item Write lists and
   deletes as well as writes: `stage` puts, `verify` reads answers, and `wipe` lists and
   deletes `<run>/`. If either is not so, stop and say so before the rehearsal. A wrong key
   fails loudly at its first request, with nothing written.
3. **The API token.** My Profile → API Tokens → Create Token → Custom token, named
   `tinyassets-import-window`. Account resources: this account only; the two permissions
   above. Cloudflare scopes them to the account, not to one queue or namespace, so the job's
   own filters are the guard (`wipe` deletes only the run's `o:rehearsal-<run>-` records). Narrow
   it where the form allows:
   - **Client IP Address Filtering:** Is in, the droplet's public IPv4;
   - **TTL:** an end date just after the window.

   Then `gh secret set IMPORT_CLOUDFLARE_API_TOKEN --env production`.
4. **`IMPORT_KEY`, in both places from one value,** in Git Bash from `platform/`, with
   wrangler's own Cloudflare login:
   ```sh
   k="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
   printf %s "$k" | gh secret set IMPORT_KEY --env production
   printf %s "$k" | npx wrangler secret put IMPORT_KEY --name tinyassets-doors
   unset k
   ```
   Without the doors' copy, the consumer drops every import message.

**After the admit, before `routes`,** delete the doors' copy
(`npx wrangler secret delete IMPORT_KEY --name tinyassets-doors`), since a replayed admit is
accepted by design. **After the window,** delete the five `production` secrets
(`gh secret delete <name> --env production`) and revoke both tokens in the dashboard (My
Profile → API Tokens), even though each also ends on its own date.

## Registrations you make by hand at the providers

The registrations are data in `platform/doors/redirect-registrations.json`, one per source.

| Source | Redirect URI to register | How the provider matches it |
|---|---|---|
| `chatgpt` (parked) | `https://tinyassets.io/oauth/connect/callback/chatgpt` | exactly |
| `openrouter_user_models_v1` | nothing to register | any callback |
