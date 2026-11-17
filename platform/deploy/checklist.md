# The dark deploy: what it changes

The kit is `platform/deploy/deploy.ts`. It runs from the manual workflow **Platform deploy
(manual, founder-approved)** (`.github/workflows/platform-deploy.yml`), on `main` only, in
three jobs:

1. **`plan`** prints what the run would change to the job summary, under the run's id, and
   keeps the same plan as the run's artifact `deploy-plan`. It holds only the Cloudflare
   token and the repo's variables, and changes nothing.
2. **`pair`** (modes `dark` and `routes`) checks the pinned E2B template with the runner this
   commit ships, unless E2B already holds their pair's tag (`deploy.ts --mode pair`: three
   throwaway sandboxes, up to about 50 minutes, the E2B key alone).
3. **`apply`** runs the mode with `--yes`, in the main-only `production` environment, held
   to that plan: before any change it makes the plan job's plan again from the live state,
   and stops, naming each difference, if anything changed between the jobs (the dispatch,
   a repository variable, or the account's state). It applies only its own run's plan.

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
  the canary clock's three (`platform/contract/canary.json`: `PLATFORM_CANARY_TOKEN` and
  the pager's `PUSHOVER_USER_KEY` and `PUSHOVER_APP_TOKEN`, each set on the doors only when
  given) and H2's probe keys (`deploy/h2-probes.ts`). `TA_PROBE_KEY` is also the platform
  canary's key (`deploy/canary.ts`), and the apply job's own `GITHUB_TOKEN` (`actions:
  read`) is what the canary reads the off-account copy's nights with. The workflow's guard
  holds its apply job to that list.
- The `production` secret `CLOUDFLARE_API_TOKEN`. The account is the doors' zone's, read from
  the zone (`Cloudflare.zone`), so no account id is stored.
- The repository variables `OWNER_ISSUER` and `WORKOS_CLIENT_ID`.

More on two of them:

- **The migration locks.** The kit refuses to run unless each lock
  (`platform/owner/migrations.json`, and the sandbox Worker's
  `platform/sandbox/migrations/<class>.json`) marks the first deploy and pins every migration
  the code ships, in order. **A deploy that adds a migration** pins it first:
  1. **Pin.** On a branch made from the current `main`, run this in `platform/`, naming the
     Worker (`owner` or `sandbox`):
     `node ci/kit/locks.ts pin owner $(git rev-parse HEAD)`.
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
   R2 buckets and the queues the Workers' configs name (a queue its producer's), each listed
   to its last page and made over Cloudflare's API (`deploy/cloudflare.ts`).
3. **Secrets, set only when the Worker's secret listing answered and lacks them** (or the
   account's Workers listing lacks the Worker). Values come from the environment's secrets: each
   Worker's `secrets.required` (its `wrangler.jsonc`), and on the doors, if set, each shared
   app's signing secret (`platform/doors/shared-apps.json`). A secret two Workers require
   (the routing key, the doors' and the owner's) is set on both or neither.

   A held secret is never replaced unless `rotate` names it. A held keyring is never
   generated, and its old kids leave it only by mode `retire` (below). It is rotated only by
   adding a key: run `keyring-add` on your copy
   of the stored keyring, store the result, then run with `rotate` naming it. The
   kit refuses a rotated keyring with fewer than two keys, or whose active key is not among
   them. **Residual:** Cloudflare never shows a held secret, so the kit cannot read the
   deployed keyring back. A two-key ring that dropped the deployed key would pass its check
   and make every sealed credential (or, for `FILES_KEYRING`, every chunk) unreadable. `keyring-add` keeps every old key by
   construction, so rotate only with it, on your copy of the stored keyring. Mode `rotation`
   then reads the deployed active kid back: every owner's answer names it, and a kid other
   than the stored ring's stops the run.
4. **Every Worker deployed with no public route,** each after the Workers it binds (`WORKERS`,
   `deploy/facts.ts`). None has a `workers.dev` or preview URL. A Worker's first deploy is
   `wrangler deploy`. Later deploys upload a version, then deploy it (`wrangler versions
   upload`, then `versions deploy`), which never touches a route. Wrangler refuses a version
   upload with a pending Durable Object migration (read in wrangler 4.154's source). Name that
   Worker in `migrate` to deploy it whole; `migrate` takes only a Worker whose config declares
   Durable Object migrations.
5. **The control record, written only when the API answered that it is absent.** It starts
   with everything closed: outside access off, maintenance admitting no owner, inbound events
   off. It also holds your tier numbers. It is `platform/deploy/control.json`, as is. If the
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
8. **The platform's canary** (Fact 11, `deploy/canary.ts`) runs against
   `tinyassets.io/mcp`. No route moves, so the old stack still answers: it reads the platform
   dark, checks that the old stack still answers `/mcp` and that E2B holds the pinned
   template and the doors' hook, disabled, and says the doors' checks wait for `routes`.

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
    stops if one does. It deploys nothing and changes no gate. Then the platform's canary;
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
    those are the old proxy's (`tinyassets-mcp-proxy`) `mcp*`, `api*` and `app*`, so the old
    proxy is left with no route.
  - A pattern nobody holds is added (`POST`).
  - Cloudflare sends each URL to the most specific pattern that matches (its routes page).
    So the old proxy's narrower `.well-known/oauth-protected-resource*` and
    `.well-known/oauth-authorization-server*` would keep their paths from the doors'
    `.well-known/oauth-*`. Each is deleted only after the doors' wider pattern exists, so
    its paths pass straight to the doors.
  - A narrower doors pattern under another Worker's wider one would not take its paths:
    Cloudflare's known issue "Route specificity" sends `x/*` URLs to another Worker's `x*`.
    So the doors take `app*` and `api*` whole, which a route matches against the query too
    (`/app?upgrade=1`), and the website serves no path under a doors pattern
    (`doors/guards/identity.test.ts`).
  - A refused change stops the run and says which steps are done.

  It then adds the egress custom domain (`egress` in `identity.json`; `wrangler triggers
  deploy`),
  enables E2B's lifecycle webhook now that the doors serve its path, and runs the
  platform's canary's unkeyed checks (`--side doors --unkeyed`, below). The same
  `triggers deploy` sets the doors' Cron Trigger, the canary's clock. Every connector then
  signs in again once.

  Before any change, `routes` keeps a **route record** in `tinyassets-control`. It holds
  the before-state of everything `routes` changes that `dark` does not:
  - every route of the zone as it read them, and the handover's changes;
  - the egress custom domain's Worker, or none;
  - whether E2B's lifecycle webhook was held, and enabled.

  It also holds the zone, the run id and the time, and no secret. The job summary names its
  id (the run's, which heads the plan) and holds a copy. If the record cannot be written, the
  run stops with nothing changed. A re-run attempt of the same run keeps its first attempt's
  record. Re-run all its jobs, not the apply job alone: once an attempt has changed
  anything, the live state no longer plans what the first plan job showed, and the apply job
  stops before any change.

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
     (`handles-canary.yml`). Its token is a `production` secret no machine
     holds, so it runs there. It is the old stack's own `--assert-handles` check, and changes
     nothing.

  The run's last line says so.

  It leaves what `dark` made: storage, secrets, the Workers, the control record and the
  WorkOS redirect URI. They serve no one without the routes, the domain and the hook.
- **The platform's canary** (Fact 11, d3 §J2): `deploy/canary.ts`, run by **Platform canary**
  (`platform-canary.yml`) and by the kit after each mode. It checks the platform from
  outside, through the public routes, and changes nothing but one file in the canary owner's
  workspace. Each side's record is data (`scripts/canary_handles.json`): the doors' handle
  set is held to their tool table by a guard, the old stack's to its own canary's list.
  - **Which side serves.** The public resource document's authorization server (AuthKit, or
    `https://tinyassets.io`). While it is the old stack's, the platform is **dark**: the
    canary checks that the old stack still answers `/mcp` (its anonymous `401` carries a
    body, the doors' none, so a `routes` or `handback` stopped partway is exit 10) and that
    E2B holds the pinned template and the hook, disabled. The doors' checks wait, named.
  - **The doors' checks:** the anonymous challenge and resource document; `/app` and
    `/app?upgrade=1` with their CSP; the first-party client document; the egress host's
    refusal; the sign-in's start (the doors' `/authorize` to WorkOS, WorkOS to AuthKit, the
    issuer's JWKS); E2B's template and the hook, enabled; **the off-account copy**: read
    with the job's own token, a night of `offsite-copy.yml` must have passed inside two and
    a half days once the copy is configured (exit 15, enough alone to page). While the
    newest night found no setting stored, it waits and says "not configured". Then, with
    `TA_PROBE_KEY`, the canary owner's API key: the handle set (one read-only `GET
    /api/tools/<name>` a name: each doors tool `405`, each old-only handle `404`), a file
    written, read and removed in the owner's workspace, and **the full run**: one short turn
    of the owner's main agent on the model source connected to it, once a day (its record is
    `/workspace/canary-full-run.json`; a failed day stays red until a full run passes;
    dispatch with `full` to run it now). It boots one sandbox a day.

    Without `TA_PROBE_KEY` it fails (exit 7), since an unchecked check is not a pass. A
    closed gate (`503`) is named too. The handle set assumes the canary owner keeps the
    default policy (every tool, and code mode on): a narrowed one reads as drift.
  - **Its clock and alarm.** The doors' Cron Trigger dispatches the workflow every ten
    minutes with `PLATFORM_CANARY_TOKEN`; without the token it dispatches nothing. A failed
    dispatch pages Pushover directly at `:00` and `:30`. The workflow's own hourly schedule
    is the watchdog: it also fails when no run was dispatched in 30 minutes. Until
    `production` holds the token, the schedule runs nothing. The alarm
    (`deploy/alarm.ts`) acts only once `production` holds `PLATFORM_CANARY_TOKEN` and the
    doors serve `/mcp`: two reds in a row (or the clock stopped, or the off-account copy
    overdue) open the `platform-down` issue and page, re-paging at 1 h, 4 h and 24 h until
    someone comments; green closes it.
  - **After `routes`, in this order:**
    1. **The `routes` run's own canary.** It runs the unkeyed checks alone (`--side doors
       --unkeyed`): the doors answer `/mcp` and its document, and the old stack doesn't. It
       says the keyed checks wait. It can't run them itself: no canary owner's key exists
       until the doors serve `/app/api`, and the dark control record admits no one.
    2. **Disable the old pager:** `gh workflow disable uptime-canary.yml` (the manifest's
       `routes` phase). Its old-stack probes would page on every run against the doors.
    3. **Open the control record for the canary owner, both gates:** `outside: "on"`,
       `ordinary: "canary"` and `canary: ["<the canary owner's WorkOS user id>"]`. The keyed
       `/api` passes only with `outside` on (the doors' `outsideOpen`), so with it off,
       the keyed checks get a `503`. With `ordinary` still `canary`, this admits no one but
       the canary owner, inside or out.
       - Read the canary owner's WorkOS user id (`user_…`) from WorkOS before its first
         sign-in. The doors name an owner by that id as it stands.
       - Its first sign-in is then admitted automatically: the gate admits the listed id,
         and the doors admit a new owner at sign-up.
    4. **Create the canary owner** (tester B, signing in through the doors), then mint its
       API key in the owner app and store it as the `production` secret `TA_PROBE_KEY`.
       H2's probes use it too. In the same owner app, connect the free-tier model key
       (for example a Google AI Studio key) as the owner's model source: the full run runs
       on it. The key goes into the app's connect form alone, never a GitHub secret: only
       the owner's own session can connect a source (`doors/src/connect.ts`).
    5. **Dispatch Platform canary with `full`.** Every check passes, the full run among them.
    6. **Arm it:** store the fine-grained GitHub token (this repository, Actions read and
       write) as the `production` secret `PLATFORM_CANARY_TOKEN`, then run `dark`, which
       sets the doors' copy. From then the clock runs and the alarm pages.
  - **After `handback` and the old stack's restart:** dispatch **Handles canary**. The
    platform's canary reads the platform dark again, and its alarm stands down.
- **Optional: a WAF rate-limiting rule of your own.** The platform sets no number on any
  caller. If you ever want a per-IP backstop at the edge, make one zone rule in the
  dashboard (Free allows one, Pro two), with your own number. It is your setting, recorded
  as such. The zone's plan is **unverified** here.
- **Mode `h2`: H2's probes alone**, once a probe owner exists. They deploy nothing. Store
  the probe keys `deploy/h2-probes.ts` names (`INPUTS`: two owners' API keys, plus an app
  token and a key id for the revoke probe) as `production` secrets. It prints one row per
  probe.
- **Open the gates with mode `control`** when you are ready (the cutover's Step 14): canary
  owners first (`set`: `outside=on canary+=<owner>`), then everyone (`ordinary=all`), and
  `inbound=true` for `/hooks/inbound`. The plan job shows each field's old and new value; the
  apply job writes only a record `parseControl` takes, keeps the one it replaces as
  `deploy:control:<run id>` (put it back with `replace`), and reads the write back. It edits
  KV itself, never through the doors, so `outside=off`, the kill switch, works when they
  don't. A missing or malformed record is shown as it is; `replace` gives the whole record.
- **Mode `owner`** asks the doors one op for one owner through the operator channel (`owner`
  and `act`: `state`, `tier=<tier>`, `suspend=<reason>`, `lift=<reason>`, `admit`, `rewrap`
  or `reseal`) and prints their answer between the owner's state before and after it, or
  stops on their refusal. `state` alone only reads. `rewrap` and `reseal` are one owner's
  key rotation sweep (`FILES_KEYRING`, `GATEWAY_KEYRING`): they change no meaning, only the
  kid a blob is under, and answer `left`, what is still under another kid (a restore's hold
  counts), so run one again until `left` is 0. It needs `OPERATOR_KEYRING` in `production`.
- **Rotating `FILES_KEYRING` or `GATEWAY_KEYRING`, and retiring the old kid,** your steps in
  order. Only steps 5 and 6 are irreversible, and only if the old ring file is lost: keep it
  offline, and the retired key can always go back under its own name.
  1. `keyring-add` on your offline copy of the stored ring, store the result as that
     `production` secret, then dispatch `dark` with `rotate` naming it. The new kid is active;
     every old one still opens.
  2. Dispatch **`rotation`** with `keyring` naming it, again until a run says the pass swept
     every owner to `left` 0. Each run asks every owner the directory lists, a page of 100 at
     a time, and stops by itself after 20 minutes. Its progress is kept in the control
     namespace (`deploy:rotation:<keyring>:<active kid>`), so a run again goes on where it
     stopped. An owner held by a restore, or with a blob that no longer opens, stays owing,
     named with its restore step; the next run asks it first. Every answer must name the
     stored ring's active kid, or the run stops: then the deployed ring is not the one you
     stored. It refuses while the doors hold `IMPORT_KEY` (the import window).
  3. **`GATEWAY_KEYRING`:** go on once one pass has swept. **`FILES_KEYRING`:** the off-account
     copy keeps versions under the old kid for 22 days after the first night that copies the
     rewrapped roots: wait 23 days after that night. Mode `retire` reads that night from the
     copy's own runs and says the date. A restore from the copy may put roots under the old
     kid back, which the next night copies again for another 22 days, so it is recorded in the
     control namespace (`deploy:offsite:restore`) and restarts this step: dispatch `rotation`
     for a pass begun after it, then wait 23 days after the night that follows that pass.
     Don't retire `FILES_KEYRING` before the copy's lifecycle rule is seen deleting a hidden
     version live (the copy's first nights, `tasks.md`).
  4. `keyring-retire` on your copy: it writes `<file>.retire`, the active key alone, and leaves
     your file as it is. Keep the old file offline.
  5. Store `<file>.retire` as the `production` secret.
  6. Dispatch **`retire`** with `keyring`. Its apply job checks the record and the copy, then
     names the words, `retire-<keyring>-<kids>`, and changes nothing. Dispatch it again with
     `confirm` those words: it sets the stored ring on the keyring's Workers, then runs the
     canary.
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
Cloudflare through one **reserved IP**, which the job's two Cloudflare tokens are pinned to. Before any
copy, and before any mode hands it a credential, the workflow checks from a container on the
host that Cloudflare sees that address, and that no container on the host reaches the
metadata service (it serves the host's `user_data`, which holds its private SSH key; the
host's boot script drops it for Docker's containers).

It reads these `production` secrets. Every mode needs `DO_SSH_KEY` (already set),
`IMPORT_DO_TOKEN` and `IMPORT_RESERVED_IP`. `census` needs the Spaces pair too, `bundle` the
exclusion list, `stage`, `verify`, `wipe`, `admit`, `dlq` and `close` the R2 pair, the API
token and `IMPORT_KEY`, and `offsite` the Spaces pair and the R2 pair. A mode that lacks one
stops before any step and names it. The account id is no secret: the seven Cloudflare modes
read it from the repository **variable** `CLOUDFLARE_ACCOUNT_ID` (step 0), held to 32 hex
digits. The real window's `close` alone also reads `IMPORT_DOORS_API_TOKEN`, on the runner,
never on the host, to delete the doors' `IMPORT_KEY`; no token of the deploy kit's is read.
**Make them before the rehearsal, keep them through the window, and revoke them after it.**

| `production` secret | What it is | Scope |
|---|---|---|
| `IMPORT_RESERVED_IP` | One reserved IPv4 in `sfo3` (step 1). | Every run's import host is given it in turn. Free while assigned, $0.01 an hour while not. |
| `IMPORT_DO_TOKEN` | A custom-scope DigitalOcean token (step 2). | Droplet create, read and delete; image read; SSH key read; tag create and read (the `tag_name` filter and a Droplet's `tags` need `tag:read`); reserved IP read and update. An expiry just after the window. It is not the DR drill's `DIGITALOCEAN_TOKEN`. |
| `IMPORT_SPACES_ACCESS_KEY_ID` and `IMPORT_SPACES_SECRET_ACCESS_KEY` | A limited-access Spaces key (step 3). | **Read** on `workflow-backups-jonnyton-sfo3` alone, the bucket the nightly backup writes. |
| `IMPORT_EXCLUSION_LIST` | Your principal and every canary owner's, one a line (`#` starts a comment). They start fresh: backed up, never imported. | none |
| `IMPORT_R2_ACCESS_KEY_ID` and `IMPORT_R2_SECRET_ACCESS_KEY` | The S3 key pair of one Cloudflare API token, made through the API (step 4). | Workers R2 Storage Bucket Item Write on `tinyassets-import-staging` and on `tinyassets-final-backup` (the off-site copy), Workers R2 Storage Bucket Item Read on `tinyassets-files`: it can never write the live files bucket. The reserved IP only; an end date. |
| `IMPORT_CLOUDFLARE_API_TOKEN` | One account API token (step 5). | Account · Queues · Edit (send to `tinyassets-import`, add and use the HTTP pull consumer of `tinyassets-import-dlq`), Account · Workers KV Storage · Edit (list `tinyassets-directory` and delete a rehearsal's records). The reserved IP only; an end date. |
| `IMPORT_KEY` | 32 random bytes, base64url (step 6). | The same value as the doors' Worker secret `IMPORT_KEY` |
| `IMPORT_DOORS_API_TOKEN` | One account API token (step 8). | Workers · Editor on the doors' Worker `tinyassets-doors` alone (Specified Workers): deleting and listing its secrets. No IP filter (GitHub's runner uses it); an end date. |

**Never give one of them a name another job reads.** Every `production` job reads its
secrets by name from the one environment, so an import token stored as
`CLOUDFLARE_API_TOKEN` would replace the deploy kit's own token in the platform deploy's
apply job.

The steps, on your own computer, with your own `gh` login. `gh secret set <name> --env
production` asks for the value and does not show it, so no value enters the chat or a
command line. **Reserve the IP first:** the job's two Cloudflare tokens are minted against it.

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
8. **`IMPORT_DOORS_API_TOKEN`.** Manage Account → Account API Tokens → Create Token → Custom
   token, named `tinyassets-import-window-doors`: Workers · Editor, resources Specified Workers
   → `tinyassets-doors` alone (Cloudflare's per-Worker roles, 2026-09-15; Editor manages a
   Worker's secrets), with a TTL just after the window. Then
   `gh secret set IMPORT_DOORS_API_TOKEN --env production`. Confirm, as you mint it, that the
   form scopes the token to that one Worker; if it cannot, stop and say so, rather than hand
   the window a token that edits every Worker.

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

**After the admit, before `routes`,** run `close` with rehearsal false and its confirm. It
passes only when every owner the staging bucket holds an index of, in any run, has its
directory record (admitted) or no index left (discarded by its run's `wipe`). Then it deletes the
doors' copy of `IMPORT_KEY` itself, since a replayed admit is accepted by design, and from then
on the kit's `rotation` runs. An owner left behind is named by run and bundle ordinal, and
nothing is changed. The window stays open, and `rotation` keeps refusing, until it is admitted:
in this run once it verifies, or imported again in a later run and admitted there. A
rehearsal's `close` only checks, after its `wipe`, and keeps the key for the window. Nothing
else deletes the key; if it is ever needed again, step 7's second line sets it again.
**After the window,** revoke everything and leave nothing billing:
- run `clean` for every run, which destroys its import host, and confirm in DigitalOcean that
  no Droplet tagged `import-window` remains (every dispatch's summary names each one and its
  age, and warns past 7 days);
- release the reserved IP (Networking → Reserved IPs → Release);
- revoke `IMPORT_DO_TOKEN` (API → Tokens), the Spaces read key (Spaces → Access Keys), and
  the three Cloudflare tokens (API Tokens), even those that also end on their own date;
- delete the `production` secrets above (`gh secret delete <name> --env production`). The
  `CLOUDFLARE_ACCOUNT_ID` variable stays: it is no credential, and other workflows read it.

## The off-account copy

**Off-account copy (nightly)** (`.github/workflows/offsite-copy.yml`, `deploy/offsite.ts`; d3
§G) copies the files bucket every night at 05:37 UTC to a Backblaze B2 bucket outside the
Cloudflare account, so the loss of that account loses no owner. The bucket holds only
ciphertext and wrapped keys: every chunk and generation is sealed under its owner's data key,
and each generation's root carries that key wrapped under `FILES_KEYRING`, which stays in the
Worker and in `production`. The copy keeps every object at its own key, so a restore is a byte
copy back.

**It is dark until all seven settings below are stored.** Until then each night notes "not
configured" and copies nothing. A night with only some of them fails, naming what is missing,
so store all seven at one sitting. From then on, any error fails the night: a key with more or
less than its capabilities, a bucket without its lock or lifecycle rule, rclone's own failure,
or a check that finds an object missing or different.

| Setting | What it is | Scope |
|---|---|---|
| `OFFSITE_BUCKET` (repository variable) | The B2 bucket's name (step 2) | none |
| `OFFSITE_B2_KEY_ID`, `OFFSITE_B2_APPLICATION_KEY` | The write key (step 4) | `listBuckets,listFiles,writeFiles` on the bucket alone: it adds and hides, and can't delete or read |
| `OFFSITE_B2_READ_KEY_ID`, `OFFSITE_B2_READ_KEY` | The read key (step 4) | `listBuckets,listFiles,readBucketRetentions,readFiles` on the bucket alone: the nightly byte check, the bucket's lock, and a restore |
| `OFFSITE_R2_ACCESS_KEY_ID`, `OFFSITE_R2_SECRET_ACCESS_KEY` | The S3 pair of one Cloudflare API token (step 5) | Workers R2 Storage Bucket Item Read on `tinyassets-files` alone. No IP condition: GitHub's runners have no fixed address |

The account id is the repository variable `CLOUDFLARE_ACCOUNT_ID` (the import window's step 0).

**The 22 days, and the erasure.** The bucket keeps each version 22 days, locked in compliance
mode from its upload, and its lifecycle rule deletes a version 22 days after it is hidden or
replaced. An erased owner's objects leave R2 at the wipe, and the first night that passes hides
them. So they are gone from the copy by the wipe plus at most a week for the hide, 22 days and
the lifecycle's daily pass: inside the 30 days the erase answer names (`complete_at`), as long
as a night passes at least once a week. Once no night has passed in two (and half a day for a
late schedule), the platform's canary pages (`deploy/offsite.ts` `watch`), with four days left
to fix it: a red night is an alarm, not a breach.

**A mass deletion fails the night, after it is hidden.** A night that hides more than a tenth of
the copy (at least 100 objects) hides them all and then fails, naming the count. Nothing is lost:
each hidden version stays 22 days, and rclone's `--b2-version-at` reads it back. The copy never
holds a deletion back, because an erasure must reach it within the week.

The steps, on your own computer (about $0.50 a month at today's size):

1. **The account.** backblaze.com → Sign Up → B2 Cloud Storage, then Account → Billing → add a
   payment method.
2. **The bucket.** Buckets → Create a Bucket: name `tinyassets-offsite-` and 8 random lowercase
   letters and digits; Files **Private**; Default Encryption **Disable** (the objects arrive
   encrypted); Object Lock **Enable**.
3. **Its two rules.** On the bucket, Object Lock → Default Retention → **Compliance, 22 days**.
   Lifecycle Settings → **Keep prior versions for this number of days: 22**. A compliance lock
   can't be shortened, and nothing under it can be deleted before its time.
4. **The two keys,** with Backblaze's `b2` command line (`pip install b2`) signed in with the
   master key (`b2 account authorize`). The web form's "Write Only" doesn't say whether it
   leaves out `deleteFiles`, so make each with its exact list:
   ```sh
   b2 key create --bucket <bucket> tinyassets-offsite-write listBuckets,listFiles,writeFiles
   b2 key create --bucket <bucket> tinyassets-offsite-read listBuckets,listFiles,readBucketRetentions,readFiles
   ```
   Each prints a key id and a key. Store them with `gh secret set OFFSITE_B2_KEY_ID --env
   production` and `OFFSITE_B2_APPLICATION_KEY` (the write key), and `OFFSITE_B2_READ_KEY_ID`
   and `OFFSITE_B2_READ_KEY` (the read key), pasting each at the hidden prompt. Then sign the
   command line out (`b2 account clear`).
5. **The R2 read key,** through Cloudflare's API, as the import window's step 5 makes its key,
   with one policy and no condition or end date:
   ```sh
   read -rs BOOT
   acct='<account id>'
   api=https://api.cloudflare.com/client/v4
   auth() { printf 'Authorization: Bearer %s\n' "$BOOT"; }
   rd="$(curl -sS -H @<(auth) "$api/user/tokens/permission_groups" | python -c "import json,sys; print(next(g['id'] for g in json.load(sys.stdin)['result'] if g['name'] == 'Workers R2 Storage Bucket Item Read'))")"
   resp="$(python -c 'import json,sys
   r, a = sys.argv[1:]
   print(json.dumps({"name": "tinyassets-offsite-read",
                     "policies": [{"effect": "allow", "permission_groups": [{"id": r}],
                                   "resources": {"com.cloudflare.edge.r2.bucket." + a
                                                 + "_default_tinyassets-files": "*"}}]}))' \
     "$rd" "$acct" \
     | curl -sS -X POST -H @<(auth) -H 'Content-Type: application/json' --data @- \
         "$api/user/tokens")"
   printf %s "$resp" | python -c "import json,sys; d=json.load(sys.stdin); sys.stdout.write(d['result']['id'] if d.get('success') else sys.exit('refused: ' + str(d.get('errors'))))" \
     | gh secret set OFFSITE_R2_ACCESS_KEY_ID --env production
   printf %s "$resp" | python -c "import hashlib,json,sys; sys.stdout.write(hashlib.sha256(json.load(sys.stdin)['result']['value'].encode()).hexdigest())" \
     | gh secret set OFFSITE_R2_SECRET_ACCESS_KEY --env production
   unset BOOT resp
   ```
   Delete the bootstrap token after this step.
6. **The bucket's name, last:** `gh variable set OFFSITE_BUCKET` with its name. The next night
   copies.

**The first copied nights** show, in the run's summary, both sides' counts and bytes and how
many objects were compared byte for byte.

**The monthly drill.** On the 1st of each month a second night runs at 06:37 UTC and then
drills a restore from the copy (`deploy/offsite.ts` `drill`): every object R2 held when that
night began is downloaded from both sides and compared byte for byte, and the copy is read as a
copy back reads it at the oldest time it takes (21 days back), naming how many objects it would
write, every namespace marked erased left out and the marks read whole. It writes nothing. A
failed drill fails that night's copy job, as a red night does. Dispatch **Off-account copy (nightly)** with `drill` ticked for one by hand.

## A restore from the copy

Mode `copyback` of **Platform deploy** writes back into `tinyassets-files` the objects of one
owner's namespace that R2 lacks, as the copy holds them now or held them at `at`, a time in the
last 21 days, and never replaces one R2 holds (`deploy/offsite.ts` `copyBack`). Use it when R2
lost an owner's objects: a mistaken deletion, or a restore whose chunks step names chunks
missing.

- **Erasures first.** The namespace is the one the owner's own object names (`restore.source`
  through the operator channel). The doors refuse that for an erased owner, or one being
  erased, so nothing of it is read or written. Then the run reads the erasure marks
  (`e/<ns>`, one per namespace the files Worker deleted on purpose, made before its delete and
  never deleted) from R2 and from the copy, and refuses a marked namespace. It stops, writing
  nothing, when they can't be read, or when the copy at `at` held a mark it no longer holds (a
  night copied a bucket that lost them).
- **Only what the owner's object names.** A chunk or generation no row of it names (one its
  own collection deleted, a redaction's bytes, a pruned generation) is never written back:
  nothing would delete it again. Should a row be collected during the run, the run stops
  after the write, naming how many; then restore the owner (mode `restore`), whose sweep
  deletes them.
- **The restore record first.** Before the first byte the run writes `deploy:offsite:restore`.
  That restarts a `FILES_KEYRING` retirement's gate: dispatch `rotation` for a pass begun after
  it.
- **Not here: a lost object, or any owner after an account loss.** Then no object names a
  namespace, and the run refuses. After an account's loss, use mode `recover` (below). A lost
  object with the account intact is not built yet (`untangle/r8-lost-object`, `tasks.md`).

The steps:

1. **The R2 write key, for this restore alone,** as step 5 above makes the read key, with
   **Workers R2 Storage Bucket Item Write** in place of Item Read, the name
   `tinyassets-offsite-restore`, and an end date: add `"expires_on": "<tomorrow>T00:00:00Z"`
   beside `"name"`. Store its pair as `OFFSITE_R2_WRITE_ACCESS_KEY_ID` and
   `OFFSITE_R2_WRITE_SECRET_ACCESS_KEY`. The run also reads `OFFSITE_BUCKET` and the read key
   the nights use.
2. **Dispatch `copyback`** with `owner`, and `at` (`2026-11-01T00:00:00Z`) for the copy as it
   stood then; none reads it as it stands. A deletion more than a night old is hidden in the
   copy as it stands, so give a time before it. The apply job names the namespace, how many
   objects it wrote, and that each read back byte for byte. Nothing lacking, nothing is written
   or recorded.
3. **Should the owner's database go back too,** dispatch `restore` with a generation of that
   namespace (above).
4. **Revoke the key** (My Profile → API Tokens), and `gh secret delete` both halves
   `--env production`.

Never copy back by hand. A copy back that records nothing lets mode `retire` pass while R2 and
the copy still hold roots under a retiring kid of `FILES_KEYRING`.

## A restore after the account's loss

Mode `recover` of **Platform deploy** restores every owner into a new Cloudflare account from
the copy (`deploy/offsite.ts` `recover`; `docs/decisions/untangle-2026-11/r8-locate.md`). No
object names a namespace there, and every erasure record but the marks went with the account.

- **Erasures first.** The run reads the marks as a copy back does, and stops on the same
  refusals. Then, after the restore record, it writes every mark R2 lacks back first, so the
  new bucket holds every erasure again. A marked namespace is never written.
- **Each namespace with a root.** Every other namespace the copy held at `at` and of which it
  held a generation's root is written back whole and read back byte for byte. One with no root
  is left out: nothing could open its chunks.
- **Its owner, from its own generation.** Each namespace's owner is read from its newest
  generation's header in the files Worker (`restore.locate`, addressed to the platform, a page
  of namespaces a message). Two namespaces naming one owner stop the run before any restore
  begins: find which is the owner's before you go on.
- **Each owner's restore begun.** `restore.begin` from that generation, so every written
  namespace is named by an object, whose erasure deletes it. The run names each one begun, and
  the `restore` dispatch that carries it on. One the doors refuse or don't answer stops the run
  once the rest are begun, named: an owner who signed up in the new account before the run is
  refused (it is not empty), and `restore` with its generation takes it (its pin and wipe).
- **Before it, no night.** A night refuses to copy a bucket that lacks a mark the copy holds,
  copying nothing (`copy`): the sync would hide every mark. So the nights stay red in the new
  account until `recover` has run. Run it at once.
- An erasure begun in the day before the loss is not yet in the copy
  (`r8-account-loss.md` item 6).

The steps:

1. **A dark deploy of the new account,** then the copy's settings and keys for it, as above,
   and the R2 write pair of step 1 of "A restore from the copy".
2. **Dispatch `recover`,** with `at` before the loss (none reads the copy as it stands). It is
   cut short past 20 minutes of asks, or by the job's 30, or stopped: dispatch it again with
   the same `at` (with none, the time its restore record names). Once the marks are back a
   night copies again and hides in the copy what R2 does not hold yet, so the copy as it
   stands then lacks it. What R2 holds is not written again, and a begin asked again answers
   as it stands.
3. **Dispatch `restore`** for each owner the run names, with the generation it names.
4. **Revoke the write key,** as step 4 above.

## Registrations you make by hand at the providers

Each source's redirect URI is its own callback, as the doors derive it (`connect.ts`,
`redirectFor`); a key exchange's provider takes any callback.

| Source | Redirect URI to register | How the provider matches it |
|---|---|---|
| `openrouter_user_models_v1` | nothing to register | any callback |
