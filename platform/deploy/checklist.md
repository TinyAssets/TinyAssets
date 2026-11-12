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

The setup is done: Workers Paid, the Cloudflare token's scopes, the `production`
environment limited to `main` with no required reviewer, its secrets (made by
`scripts/platform_setup.py`, every value set only where it is absent), the repository
variables, and both migration locks pinned. Later runs need only what follows.

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

- **The migration locks.** The kit refuses to run unless each lock
  (`platform/owner/migrations.json`, and the sandbox Worker's
  `platform/sandbox/migrations/<class>.json`) marks the first deploy and pins every migration
  the code ships, in order. **A deploy that adds a migration** pins it first:
  1. **Pin.** On a branch made from the current `main`, run this in `platform/owner` (or
     `platform/sandbox`):
     `PIN_MIGRATIONS=$(git rev-parse HEAD) npx vitest run --project guards guards/migrations.test.ts`.
     It appends a digest for each new migration to that Worker's locks.
  2. **Land it.** Commit only those files and merge them to `main` by pull request. The workflow
     runs only from `main`, and the kit reads the lock there.
  3. **Pin only when you deploy.** A pinned migration is frozen for good: from then on a
     change to it is a new migration, even if no deploy follows.
- **The sandbox's template is built** (`E2B_TEMPLATE` in `platform/sandbox/wrangler.jsonc`).
  The kit checks E2B has a ready build, and the `pair` job checks the pair.

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
   off. It also holds your tier numbers (`DARK_CONTROL` in `deploy.ts`). If the
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

  **Open the link and sign in once.** Read where the browser ends against the pass and
  fail signs the apply job prints under the link (`PROBE_SIGNS` in `deploy.ts`), and report
  it.

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
tasks.md §9 item 7), runs each mode on the run's own **import host**: a Droplet,
`tinyassets-import-<run>` in `sfo3` (`s-4vcpu-8gb`, about $0.07 an hour, $1.71 a day), that
`census` makes and `clean` destroys, one at a time. It never reaches the production droplet.
The host reads the backup from the primary store with a read-only key, and leaves for
Cloudflare through one **reserved IP**, which both Cloudflare tokens are pinned to. Before any
copy, and before any mode hands it a credential, the workflow checks from a container on the
host that Cloudflare sees that address, and that no container on the host reaches the
metadata service (it serves the host's `user_data`, which holds its private SSH key; the
host's boot script drops it for Docker's containers).

It reads these `production` secrets. Every mode needs `DO_SSH_KEY` (already set),
`IMPORT_DO_TOKEN` and `IMPORT_RESERVED_IP`. `census` needs the Spaces pair too, `bundle` the
exclusion list, `stage`, `verify`, `wipe`, `admit` and `dlq` the R2 pair, the API token and
`IMPORT_KEY`, and `offsite` the Spaces pair and the R2 pair. A mode that lacks one stops
before any step and names it. The account id is no secret: the six Cloudflare modes read it
from the repository **variable** `CLOUDFLARE_ACCOUNT_ID` (step 0), held to 32 hex digits, and
no token of the deploy kit's is read by this workflow. **Make them before the rehearsal, keep
them through the window, and revoke them after it.**

| `production` secret | What it is | Scope |
|---|---|---|
| `IMPORT_RESERVED_IP` | One reserved IPv4 in `sfo3` (step 1). | Every run's import host is given it in turn. Free while assigned, $0.01 an hour while not. |
| `IMPORT_DO_TOKEN` | A custom-scope DigitalOcean token (step 2). | Droplet create, read and delete; image read; SSH key read; tag create and read (the `tag_name` filter and a Droplet's `tags` need `tag:read`); reserved IP read and update. An expiry just after the window. It is not the DR drill's `DIGITALOCEAN_TOKEN`. |
| `IMPORT_SPACES_ACCESS_KEY_ID` and `IMPORT_SPACES_SECRET_ACCESS_KEY` | A limited-access Spaces key (step 3). | **Read** on `workflow-backups-jonnyton-sfo3` alone, the bucket the nightly backup writes. |
| `IMPORT_EXCLUSION_LIST` | Your principal and every canary owner's, one a line (`#` starts a comment). They start fresh: backed up, never imported. | none |
| `IMPORT_R2_ACCESS_KEY_ID` and `IMPORT_R2_SECRET_ACCESS_KEY` | The S3 key pair of one Cloudflare API token, made through the API (step 4). | Workers R2 Storage Bucket Item Write on `tinyassets-import-staging` and on `tinyassets-final-backup` (the off-site copy), Workers R2 Storage Bucket Item Read on `tinyassets-files`: it can never write the live files bucket. The reserved IP only; an end date. |
| `IMPORT_CLOUDFLARE_API_TOKEN` | One account API token (step 5). | Account · Queues · Edit (send to `tinyassets-import`, add and use the HTTP pull consumer of `tinyassets-import-dlq`), Account · Workers KV Storage · Edit (list `tinyassets-directory` and delete a rehearsal's records). The reserved IP only; an end date. |
| `IMPORT_KEY` | 32 random bytes, base64url (step 6). | The same value as the doors' Worker secret `IMPORT_KEY` |

**Never give one of them a name another job reads.** An environment secret hides the
repository secret of the same name in every `production` job, so a `production` secret named
`CLOUDFLARE_API_TOKEN` would hand the import token to the platform deploy's apply job.

The steps, on your own computer, with your own `gh` login. `gh secret set <name> --env
production` asks for the value and does not show it, so no value enters the chat or a
command line. **Reserve the IP first:** both Cloudflare tokens are minted against it.

0. **The account id, a repository variable (not a secret).** Cloudflare dashboard → Workers &
   Pages → Account details → Account ID, then `gh variable set CLOUDFLARE_ACCOUNT_ID` with its
   32 hex digits. A variable is shown in logs; the id is not a secret (the deploy logs print
   it). It stays after the window, for `cloud-only-preflight.yml` reads it too.
1. **The reserved IP.** DigitalOcean → Networking → Reserved IPs → Reserve an IPv4 in
   `sfo3`, assigned to nothing. Then `gh secret set IMPORT_RESERVED_IP --env production`
   with its address.
2. **`IMPORT_DO_TOKEN`.** DigitalOcean → API → Tokens → Generate New Token → Custom Scopes,
   named `tinyassets-import-window`, with the scopes above and an expiry just after the
   window. Then `gh secret set IMPORT_DO_TOKEN --env production`. The deploy key
   (`DO_SSH_KEY`'s public half) must be registered under Settings → Security → SSH Keys;
   `census` refuses, naming it, when it is not.
   - *Optional, not built:* `DO_SSH_KEY` is also the production droplet's login key. The
     workflow cannot reach that droplet (it holds no address for it, the pin would not
     match, and the host guard refuses), but a separate `IMPORT_SSH_KEY`, registered in
     DigitalOcean for the import hosts alone, would keep that key off this job. It costs one
     more step here and a one-line change in the workflow.
3. **The Spaces read key.** Spaces Object Storage → Access Keys → Create Access Key →
   Limited Access → `workflow-backups-jonnyton-sfo3` → **Read**. Spaces keys take no IP
   condition and no end date, so it is revoked by hand after the window. Then
   `gh secret set IMPORT_SPACES_ACCESS_KEY_ID --env production` and
   `gh secret set IMPORT_SPACES_SECRET_ACCESS_KEY --env production`.
4. **The exclusion list.** Read the principals from the old stack's records and
   `docs/host-actions.md`, then `gh secret set IMPORT_EXCLUSION_LIST --env production`. An
   empty list stops `bundle`.
5. **The R2 key, through Cloudflare's API.** The dashboard's R2 token form gives one
   permission to every bucket it names, which would let the key write `tinyassets-files`, every
   user's file chunks. One API token with bucket-level policies cannot. You need:
   - **A short-lived token that may make tokens.** My Profile → API Tokens → Create Token →
     the "Create Additional Tokens" template (User · API Tokens · Edit), with a TTL ending
     today. Delete it after this step.
   - **Three values:**
     - the account id (Cloudflare dashboard → Workers & Pages → Account details);
     - the reserved IP from step 1. A job's container leaves through Docker's default bridge,
       which has no IPv6, and the import host routes through the reserved IP's anchor
       gateway, so Cloudflare sees that address. Never the droplet's: that puts the window
       back on the live host;
     - an end date just after the window, for example `2026-12-31T00:00:00Z`.

   In Git Bash, `read -rs` takes the bootstrap token without showing it, and curl reads its
   header from a file descriptor, so the token is in no command line:
   ```sh
   read -rs BOOT
   acct='<account id>'; ip='<reserved IPv4>'; until='<end date>'
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
                        "resources": {b + "tinyassets-import-staging": "*",
                                      b + "tinyassets-final-backup": "*"}},
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
   deletes as well as writes: `stage` puts, `verify` reads answers, `wipe` lists and deletes
   `<run>/`, and `offsite` puts and reads back. If either is not so, stop and say so before
   the rehearsal. A wrong key fails loudly at its first request, with nothing written.
6. **The API token.** My Profile → API Tokens → Create Token → Custom token, named
   `tinyassets-import-window`. Account resources: this account only; the two permissions
   above. Cloudflare scopes them to the account, not to one queue or namespace, so the job's
   own filters are the guard (`wipe` deletes only the run's `o:rehearsal-<run>-` records). Narrow
   it where the form allows:
   - **Client IP Address Filtering:** Is in, the reserved IP from step 1;
   - **TTL:** an end date just after the window.

   Then `gh secret set IMPORT_CLOUDFLARE_API_TOKEN --env production`.
7. **`IMPORT_KEY`, in both places from one value,** in Git Bash from `platform/`, with
   wrangler's own Cloudflare login:
   ```sh
   k="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
   printf %s "$k" | gh secret set IMPORT_KEY --env production
   printf %s "$k" | npx wrangler secret put IMPORT_KEY --name tinyassets-doors
   unset k
   ```
   Without the doors' copy, the consumer drops every import message.

The R2 bucket `tinyassets-final-backup` is made by the deploy kit's `dark`, with the other
buckets. **In the window, after its census,** run `offsite`: it copies the final backup the
window's copy was made from (its manifest, archive and every pinned snapshot) there, and reads
each back against the manifest. Keep that bucket through the 14-day hold. **Run it before the
next nightly backup (03:00 UTC), or with the backup timer stopped:** `offsite` refuses once a
newer manifest than the copy's is on the store, and that run's final backup is then copied by
no one (census a new run first).

**A host that fails is still cleaned.** `clean` destroys the run's Droplet once it is held to
its name, tags and kind, even when its set-up never finished, its key tag is not one, or it lost
the reserved IP: that host is never reached, and its disk (the copy) goes with it. It destroys
nothing when the host's own clean refuses, nor any Droplet not so held: delete such a one by
hand (DigitalOcean → Droplets → the `import-window` tag), as the summary names it. The kind is
census's tag `ta-kind-rehearsal` or `ta-kind-window`: clean the window's run with `rehearsal`
false and its confirm, as it was made; a rehearsal-kind dispatch is refused before the host is
reached or destroyed.

**After the admit, before `routes`,** delete the doors' copy
(`npx wrangler secret delete IMPORT_KEY --name tinyassets-doors`), since a replayed admit is
accepted by design. **After the window,** revoke everything and leave nothing billing:
- run `clean` for every run, which destroys its import host, and confirm in DigitalOcean that
  no Droplet tagged `import-window` remains (every dispatch's summary names each one and its
  age, and warns past 7 days);
- release the reserved IP (Networking → Reserved IPs → Release);
- revoke `IMPORT_DO_TOKEN` (API → Tokens), the Spaces read key (Spaces → Access Keys), and
  both Cloudflare tokens (My Profile → API Tokens), even those that also end on their own date;
- delete the `production` secrets above (`gh secret delete <name> --env production`). The
  `CLOUDFLARE_ACCOUNT_ID` variable stays: it is no credential, and other workflows read it.

## Registrations you make by hand at the providers

Each source's redirect URI is its own callback, as the doors derive it (`connect.ts`,
`redirectFor`); a key exchange's provider takes any callback.

| Source | Redirect URI to register | How the provider matches it |
|---|---|---|
| `chatgpt` (parked) | `https://tinyassets.io/oauth/connect/callback/chatgpt` | exactly |
| `openrouter_user_models_v1` | nothing to register | any callback |
